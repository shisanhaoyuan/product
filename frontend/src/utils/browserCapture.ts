export type BrowserPage = { asin: string; source_url: string; html: string };
const channel = 'catalog-capture-v1';

export function captureInBrowser(
  asin: string,
  signal: AbortSignal,
  mode: 'images' | 'source' | 'import' = 'images',
  foreground = false,
  onAttention?: (reason: string, reveal: () => void) => void,
): Promise<BrowserPage> {
  return new Promise((resolve, reject) => {
    const requestId = crypto.randomUUID();
    let started = false;
    const send = (action: string) =>
      window.postMessage({ channel, action, requestId, asin, mode, foreground }, location.origin);
    const probeTimer = window.setTimeout(
      () => finish(new Error('请先安装并连接浏览器验证助手。')),
      1800,
    );
    const expiry = window.setTimeout(
      () => finish(new Error('验证等待已超时，请重新抓取。')),
      600000,
    );
    function finish(error?: Error, page?: BrowserPage) {
      clearTimeout(probeTimer);
      clearTimeout(expiry);
      window.removeEventListener('message', receive);
      signal.removeEventListener('abort', cancel);
      send('cancel');
      if (error) reject(error);
      else resolve(page!);
    }
    function cancel() {
      finish(new Error('已取消本次抓取。'));
    }
    function receive(event: MessageEvent) {
      const data = event.data;
      if (
        event.source !== window ||
        event.origin !== location.origin ||
        data?.channel !== channel ||
        data.requestId !== requestId ||
        !['reply', 'result'].includes(data.action)
      )
        return;
      if (data.attention) {
        onAttention?.(String(data.attention), () => send('reveal'));
        return;
      }
      if (data.error) {
        finish(new Error(String(data.error)));
        return;
      }
      if (data.ready && !started) {
        if (mode === 'import' && !data.modes?.includes('import')) {
          finish(new Error('请下载并重新加载集物商品采集助手 2.0.0，再刷新页面。'));
          return;
        }
        if (mode === 'source' && (!data.modes?.includes('source') || !data.backgroundSource)) {
          finish(
            new Error(
              '请更新验证助手至 1.2.3 并在扩展管理中重新加载，再刷新商品库，以支持后台采集和按需打开网页。',
            ),
          );
          return;
        }
        started = true;
        clearTimeout(probeTimer);
        send('start');
      } else if (
        data.action === 'result' &&
        data.asin === asin &&
        typeof data.html === 'string' &&
        typeof data.source_url === 'string'
      ) {
        finish(undefined, { asin, source_url: data.source_url, html: data.html });
      }
    }
    window.addEventListener('message', receive);
    signal.addEventListener('abort', cancel, { once: true });
    if (signal.aborted) cancel();
    else send('probe');
  });
}
