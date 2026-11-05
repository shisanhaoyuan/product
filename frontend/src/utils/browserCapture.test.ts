import { afterEach, expect, it, vi } from 'vitest';
import { captureInBrowser } from './browserCapture';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function browser() {
  vi.useFakeTimers();
  const listeners = new Set<(event: unknown) => void>();
  const origin = 'https://catalog.test';
  const window = {
    setTimeout,
    postMessage: vi.fn(),
    addEventListener: (_: string, callback: (event: unknown) => void) => listeners.add(callback),
    removeEventListener: (_: string, callback: (event: unknown) => void) =>
      listeners.delete(callback),
  };
  vi.stubGlobal('window', window);
  vi.stubGlobal('location', { origin });
  function deliver(data: object, eventOrigin = origin) {
    const request = window.postMessage.mock.calls[0][0];
    for (const listener of listeners)
      listener({
        source: window,
        origin: eventOrigin,
        data: { channel: 'catalog-capture-v1', requestId: request.requestId, ...data },
      });
  }
  return { window, listeners, deliver };
}

it('waits for manual-page result, ignores other origins, and cleans up', async () => {
  const b = browser();
  const promise = captureInBrowser('B012345678', new AbortController().signal);
  b.deliver({ action: 'reply', ready: true }, 'https://evil.test');
  expect(b.window.postMessage).toHaveBeenCalledTimes(1);
  b.deliver({ action: 'reply', ready: true });
  expect(b.window.postMessage.mock.calls[1][0].action).toBe('start');
  b.deliver({ action: 'result', asin: 'B999999999', html: 'wrong', source_url: 'wrong' });
  expect(b.listeners.size).toBe(1);
  b.deliver({
    action: 'result',
    asin: 'B012345678',
    html: 'gallery',
    source_url: 'https://www.amazon.com/dp/B012345678',
  });
  expect(await promise).toMatchObject({ asin: 'B012345678', html: 'gallery' });
  expect(b.listeners.size).toBe(0);
  expect(vi.getTimerCount()).toBe(0);
});

it('shows installation error when no helper replies', async () => {
  browser();
  const check = expect(
    captureInBrowser('B012345678', new AbortController().signal),
  ).rejects.toThrow('安装');
  await vi.advanceTimersByTimeAsync(1800);
  await check;
});

it('requires source support and passes the requested capture mode', async () => {
  const b = browser();
  const old = expect(
    captureInBrowser('B012345678', new AbortController().signal, 'source'),
  ).rejects.toThrow('新版');
  b.deliver({ action: 'reply', ready: true });
  await old;
  const promise = captureInBrowser('B012345678', new AbortController().signal, 'source');
  const probe = b.window.postMessage.mock.calls.at(-1)![0];
  for (const listener of b.listeners)
    listener({
      source: b.window,
      origin: 'https://catalog.test',
      data: { ...probe, action: 'reply', ready: true, modes: ['images', 'source'] },
    });
  expect(b.window.postMessage.mock.calls.at(-1)![0]).toMatchObject({
    action: 'start',
    mode: 'source',
  });
  for (const listener of b.listeners)
    listener({
      source: b.window,
      origin: 'https://catalog.test',
      data: {
        ...probe,
        action: 'result',
        html: 'source facts',
        source_url: 'https://www.amazon.com/dp/B012345678',
      },
    });
  expect((await promise).html).toBe('source facts');
});

it('cancels pending verification and releases listeners', async () => {
  const b = browser(),
    controller = new AbortController();
  const check = expect(captureInBrowser('B012345678', controller.signal)).rejects.toThrow('取消');
  b.deliver({ action: 'reply', ready: true });
  controller.abort();
  await check;
  expect(b.listeners.size).toBe(0);
  expect(b.window.postMessage.mock.calls.at(-1)?.[0].action).toBe('cancel');
});

it('does not treat closing a verification page or a timeout as success', async () => {
  const b = browser();
  const check = expect(
    captureInBrowser('B012345678', new AbortController().signal),
  ).rejects.toThrow('关闭');
  b.deliver({ action: 'reply', ready: true });
  b.deliver({ action: 'result', error: '验证页面已关闭' });
  await check;
  const timeout = expect(
    captureInBrowser('B012345678', new AbortController().signal),
  ).rejects.toThrow('超时');
  const lastProbe = b.window.postMessage.mock.calls.at(-1)![0];
  for (const listener of b.listeners)
    listener({
      source: b.window,
      origin: 'https://catalog.test',
      data: { ...lastProbe, action: 'reply', ready: true },
    });
  await vi.advanceTimersByTimeAsync(600000);
  await timeout;
});
