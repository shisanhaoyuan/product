import { collectorApi, type CollectorJob, type CollectorTask } from '../api/amazonCollector';
import { ApiError } from '../api/client';
import { captureInBrowser } from './browserCapture';
import { errorMessage } from './display';

export function delay(signal: AbortSignal, ms = 2000): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new Error('采集已暂停'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, ms);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}

export async function collectTask(
  id: string,
  task: CollectorTask,
  signal: AbortSignal,
  wraith: boolean,
  update: (job: CollectorJob) => void,
  notice: (message: string, reveal?: () => void) => void,
) {
  const browser = new AbortController();
  const cancel = () => browser.abort();
  signal.addEventListener('abort', cancel, { once: true });
  if (signal.aborted) browser.abort();
  let fallbackStarted = false;
  let resolveFallback: (job: CollectorJob) => void = () => {};
  const fallbackDone = new Promise<CollectorJob>((resolve) => {
    resolveFallback = resolve;
  });
  async function fallback(reveal: () => void) {
    try {
      const queued = await collectorApi.action(id, 'fallback', task);
      if (browser.signal.aborted) return;
      update(queued);
      while (!browser.signal.aborted) {
        await delay(browser.signal);
        const job = await collectorApi.get(id);
        if (browser.signal.aborted) return;
        update(job);
        const row = job.items.find((r) => r.asin === task.asin && r.root_asin === task.root_asin);
        if (row?.state === 'done') {
          resolveFallback(job);
          return;
        }
        if (!row || !['fallback_queued', 'fallback_running'].includes(row.state)) {
          notice(row?.message || '请在员工浏览器处理后继续', reveal);
          return;
        }
        notice(row.message, reveal);
      }
    } catch (e) {
      if (!browser.signal.aborted) notice(errorMessage(e) + '；可打开网页人工处理', reveal);
    }
  }
  try {
    const page = captureInBrowser(
      task.asin,
      browser.signal,
      'import',
      false,
      (reason, reveal) => {
        if (reason === 'location') {
          notice(
            `请在已打开的网页设置邮编 ${task.marketplace === 'CA' ? 'M5V 3L9' : '10001'}，完成后自动继续`,
            reveal,
          );
          return;
        }
        if (!fallbackStarted) {
          fallbackStarted = true;
          notice(
            wraith
              ? '遇到验证，正在调用服务器 Wraith；员工网页保持后台'
              : '需要验证，请点击打开网页处理',
            reveal,
          );
          if (wraith) void fallback(reveal);
        }
      },
      task.marketplace || 'US',
    );
    // A returned browser page is saved once. Ambiguous network failures are not replayed.
    const browserDone = page.then((result) =>
      collectorApi.action<CollectorJob>(id, 'page', { ...task, ...result }),
    );
    const result = await Promise.race([browserDone, fallbackDone]);
    update(result);
  } catch (e) {
    if (signal.aborted) throw e;
    if (e instanceof ApiError) throw e; // May already have been saved; pause and inspect durable state.
    if (/安装|连接|更新|重新加载/.test(errorMessage(e))) throw e;
    update(await collectorApi.action(id, 'fail', { ...task, message: errorMessage(e) }));
  } finally {
    browser.abort();
    signal.removeEventListener('abort', cancel);
  }
}
