import { afterEach, expect, it, vi } from 'vitest';
import { captureInBrowser } from './browserCapture';
import { collectorPluginVersion } from './collectorPlugin';
const capabilities = {
  version: collectorPluginVersion,
  modes: ['images', 'source', 'import'],
  backgroundSource: true,
  currentProductImages: true,
};

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
        data: {
          channel: 'catalog-capture-v1',
          requestId: request.requestId,
          ...capabilities,
          ...data,
        },
      });
  }
  return { window, listeners, deliver };
}

it('waits for manual-page result, ignores other origins, and cleans up', async () => {
  const b = browser();
  const promise = captureInBrowser('B012345678', new AbortController().signal);
  b.deliver({ action: 'reply', ready: true, ...capabilities }, 'https://evil.test');
  expect(b.window.postMessage).toHaveBeenCalledTimes(1);
  b.deliver({ action: 'reply', ready: true, ...capabilities });
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
  ).rejects.toThrow(collectorPluginVersion);
  b.deliver({ action: 'reply', ready: true, modes: ['images'] });
  await old;
  const promise = captureInBrowser('B012345678', new AbortController().signal, 'source');
  const probe = b.window.postMessage.mock.calls.at(-1)![0];
  for (const listener of b.listeners)
    listener({
      source: b.window,
      origin: 'https://catalog.test',
      data: {
        ...capabilities,
        ...probe,
        action: 'reply',
        ready: true,
        modes: ['images', 'source'],
        backgroundSource: true,
      },
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
        ...capabilities,
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
  b.deliver({ action: 'reply', ready: true, ...capabilities });
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
  b.deliver({ action: 'reply', ready: true, ...capabilities });
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
      data: { ...capabilities, ...lastProbe, action: 'reply', ready: true },
    });
  await vi.advanceTimersByTimeAsync(600000);
  await timeout;
});

it('rejects source-capable old helpers that cannot guarantee background collection', async () => {
  const b = browser();
  const failure = expect(
    captureInBrowser('B012345678', new AbortController().signal, 'source'),
  ).rejects.toThrow(collectorPluginVersion);
  b.deliver({ action: 'reply', ready: true, modes: ['source'], backgroundSource: false });
  await failure;
  expect(b.window.postMessage.mock.calls.some(([data]) => data.action === 'start')).toBe(false);
});

it.each(['images', 'source', 'import'] as const)(
  'blocks an old plugin and a plugin missing gallery support in %s mode',
  async (mode) => {
    for (const incompatible of [
      { version: '2.0.0' },
      { version: undefined },
      { version: 'invalid' },
      { currentProductImages: undefined },
    ]) {
      const b = browser();
      const failure = expect(
        captureInBrowser('B012345678', new AbortController().signal, mode),
      ).rejects.toThrow(collectorPluginVersion);
      b.deliver({ action: 'reply', ready: true, ...incompatible });
      await failure;
      expect(b.window.postMessage.mock.calls.some(([data]) => data.action === 'start')).toBe(false);
      expect(b.listeners.size).toBe(0);
    }
  },
);
