document.querySelector('#enable').addEventListener('click', async () => {
  const status = document.querySelector('#status');
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = new URL(tab.url);
    if (url.hostname === 'www.amazon.com' || !(url.protocol === 'https:' ||
        (url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname)))) {
      throw new Error('请先打开商品库网站，再连接。');
    }
    const matches = [url.origin + '/*'];
    if (!await chrome.permissions.request({ origins: matches })) throw new Error('未授权此网站。');
    const id = 'catalog-' + url.origin.replace(/[^a-z0-9]/gi, '-');
    await chrome.scripting.unregisterContentScripts({ ids: [id] }).catch(() => {});
    await chrome.scripting.registerContentScripts([{ id, matches, js: ['bridge.js'], persistAcrossSessions: true }]);
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['bridge.js'] });
    status.textContent = '已连接，可返回商品库继续抓取。';
  } catch (e) { status.textContent = e.message; }
});
