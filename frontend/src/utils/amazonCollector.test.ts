import { afterEach, expect, it, vi } from 'vitest';
import { collectTask } from './amazonCollector';
import { captureInBrowser } from './browserCapture';
import { collectorApi, type CollectorJob } from '../api/amazonCollector';
import { ApiError } from '../api/client';
vi.mock('./browserCapture', () => ({ captureInBrowser: vi.fn() }));
vi.mock('../api/amazonCollector', () => ({ collectorApi: { action: vi.fn(), get: vi.fn() } }));
const task = { root_asin: 'B000000000', asin: 'B000000001', token: 'token' };
const job: CollectorJob = {
  id: 'job',
  created_at: '',
  state: 'collecting',
  result: {},
  items: [{ ...task, state: 'done', message: '' }],
};
afterEach(() => {
  vi.resetAllMocks();
  vi.useRealTimers();
});
it('saves an ASIN-bound browser result once', async () => {
  vi.mocked(captureInBrowser).mockResolvedValue({
    asin: task.asin,
    html: 'own page',
    source_url: 'https://www.amazon.com/dp/' + task.asin,
  });
  vi.mocked(collectorApi.action).mockResolvedValue(job);
  const update = vi.fn();
  await collectTask('job', task, new AbortController().signal, true, update, vi.fn());
  expect(collectorApi.action).toHaveBeenCalledWith(
    'job',
    'page',
    expect.objectContaining({ ...task, html: 'own page' }),
  );
  expect(update).toHaveBeenCalledWith(job);
});
it('queues Wraith once, polls until saved and cancels browser wait', async () => {
  vi.useFakeTimers();
  let cancelled = false;
  vi.mocked(captureInBrowser).mockImplementation((_asin, signal, _mode, _foreground, attention) => {
    attention?.('verification', vi.fn());
    attention?.('verification', vi.fn());
    return new Promise((_resolve, reject) =>
      signal.addEventListener('abort', () => {
        cancelled = true;
        reject(new Error('cancel'));
      }),
    );
  });
  vi.mocked(collectorApi.action).mockResolvedValue(job);
  vi.mocked(collectorApi.get).mockResolvedValue(job);
  const promise = collectTask('job', task, new AbortController().signal, true, vi.fn(), vi.fn());
  await vi.advanceTimersByTimeAsync(2100);
  await promise;
  expect(collectorApi.action).toHaveBeenCalledWith('job', 'fallback', task);
  expect(
    vi.mocked(collectorApi.action).mock.calls.filter((call) => call[1] === 'fallback'),
  ).toHaveLength(1);
  expect(cancelled).toBe(true);
});
it('does not replay a possibly saved HTTP failure', async () => {
  vi.mocked(captureInBrowser).mockResolvedValue({
    asin: task.asin,
    html: 'page',
    source_url: 'url',
  });
  vi.mocked(collectorApi.action).mockRejectedValue(new ApiError('gateway', 504));
  await expect(
    collectTask('job', task, new AbortController().signal, true, vi.fn(), vi.fn()),
  ).rejects.toThrow();
  expect(
    vi.mocked(collectorApi.action).mock.calls.filter((call) => call[1] === 'page'),
  ).toHaveLength(1);
  expect(
    vi.mocked(collectorApi.action).mock.calls.some((call) => ['fail', 'pause'].includes(call[1])),
  ).toBe(false);
});
it('abort never marks a still-running browser task as failed', async () => {
  const controller = new AbortController();
  vi.mocked(captureInBrowser).mockImplementation(
    (_asin, signal) =>
      new Promise((_resolve, reject) =>
        signal.addEventListener('abort', () => reject(new Error('paused'))),
      ),
  );
  const check = expect(
    collectTask('job', task, controller.signal, true, vi.fn(), vi.fn()),
  ).rejects.toThrow('paused');
  controller.abort();
  await check;
  expect(collectorApi.action).not.toHaveBeenCalled();
});
