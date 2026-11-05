(() => {
  if (globalThis.catalogCaptureBridge) return;
  globalThis.catalogCaptureBridge = true;
  const channel = 'catalog-capture-v1';
  window.addEventListener('message', async (event) => {
    if (event.source !== window || event.origin !== location.origin || event.data?.channel !== channel) return;
    const data = event.data;
    if (!['probe', 'start', 'cancel'].includes(data.action)) return;
    try {
      const result = await chrome.runtime.sendMessage({ ...data, channel });
      window.postMessage({ ...result, channel, action: 'reply', requestId: data.requestId }, location.origin);
    } catch {
      window.postMessage({ channel, action: 'reply', requestId: data.requestId,
        error: '助手连接已失效，请刷新商品库页面。' }, location.origin);
    }
  });
  chrome.runtime.onMessage.addListener((data) => {
    if (data.channel === channel) window.postMessage({ ...data, action: 'result' }, location.origin);
  });
})();
