const channel = 'catalog-capture-v1';
const jobKey = (id) => 'capture:' + id;
// Persist jobs across service-worker suspension; never transfer Amazon cookies.
async function finish(key, job, result) {
  await chrome.storage.session.remove(key);
  await chrome.tabs.sendMessage(job.owner, { channel, requestId: job.requestId, ...result }).catch(() => {});
}
chrome.runtime.onMessage.addListener((data, sender, reply) => {
  (async () => {
    if (data?.channel !== channel || !sender.tab || sender.frameId !== 0) return {};
    const origin = new URL(sender.url).origin;
    if (origin !== 'https://www.amazon.com') {
      const permitted = await chrome.permissions.contains({ origins: [origin + '/*'] });
      if (!permitted) throw new Error('请先通过助手连接商品库网站。');
      if (data.action === 'probe') return { ready: true, version: chrome.runtime.getManifest().version,
        modes: ['images', 'source', 'import'], backgroundSource: true, currentProductImages: true };
      const key = jobKey(sender.tab.id);
      const old = (await chrome.storage.session.get(key))[key];
      if (data.action === 'reveal' && old?.requestId === data.requestId) {
        await chrome.tabs.update(old.amazon, { active: true });
        await chrome.storage.session.set({ [key]: { ...old, revealed: true } });
        return {};
      }
      if (data.action === 'cancel') {
        if (old?.requestId === data.requestId) {
          await chrome.storage.session.remove(key);
          if (!old.revealed) await chrome.tabs.remove(old.amazon).catch(() => {});
        }
        return {};
      }
      if (data.action !== 'start' || !/^[A-Z0-9]{10}$/.test(data.asin) ||
          typeof data.requestId !== 'string' || data.requestId.length > 100) throw new Error('无效抓取请求');
      if (old) await finish(key, old, { error: '上一次抓取已被新的请求取消。' });
      const tab = await chrome.tabs.create({ url: `https://www.amazon.com/dp/${data.asin}?th=1&psc=1`, active: !['source', 'import'].includes(data.mode) || data.foreground === true });
      await chrome.storage.session.set({ [key]: { owner: sender.tab.id, amazon: tab.id,
        asin: data.asin, revealed: data.foreground === true, mode: ['source', 'import'].includes(data.mode) ? data.mode : 'images',
        requestId: data.requestId, expires: Date.now() + 600000 } });
      return { started: true };
    }
    if (!['page', 'task', 'attention'].includes(data.action)) return {};
    const jobs = await chrome.storage.session.get(null);
    for (const [key, job] of Object.entries(jobs)) {
      if (!key.startsWith('capture:') || job.amazon !== sender.tab.id) continue;
      if (job.expires < Date.now()) { await finish(key, job, { error: '验证等待已超时，请重新抓取。' }); break; }
      if (data.action === 'task') return { asin: job.asin, mode: job.mode };
      if (data.action === 'attention') {
        if (job.mode === 'import') {
          await chrome.tabs.sendMessage(job.owner, { channel, requestId: job.requestId, attention: data.reason || 'verification' }).catch(() => {});
          // Only an invalid/missing ZIP may automatically reveal an import tab.
          if (data.reason !== 'location') return { accepted: true };
        }
        if (!job.revealed) {
          await chrome.storage.session.set({ [key]: { ...job, revealed: true } });
          await chrome.tabs.update(job.amazon, { active: true }).catch(() => {});
        }
        return { accepted: true };
      }
      if (data.asin !== job.asin || typeof data.html !== 'string' || data.html.length > 6 * 1024 * 1024) break;
      const url = new URL(sender.url);
      if (!new RegExp(`/(?:[^/]+/)?dp/${job.asin}/?$`).test(url.pathname)) break;
      await finish(key, job, { html: data.html, source_url: sender.url, asin: job.asin });
      if (job.revealed || !['source', 'import'].includes(job.mode))
        await chrome.tabs.update(job.owner, { active: true }).catch(() => {});
      if (['source', 'import'].includes(job.mode) && !job.revealed)
        await chrome.tabs.remove(job.amazon).catch(() => {});
      return { accepted: true };
    }
    return {};
  })().then(reply, (e) => reply({ error: e.message }));
  return true;
});
chrome.tabs.onRemoved.addListener(async (id) => {
  const jobs = await chrome.storage.session.get(null);
  for (const [key, job] of Object.entries(jobs)) {
    if (!key.startsWith('capture:')) continue;
    if (job.owner === id) await chrome.storage.session.remove(key);
    else if (job.amazon === id) await finish(key, job, { error: '验证页面已关闭，本次抓取已取消。' });
  }
});
chrome.alarms.create('capture-expiry', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(async ({ name }) => {
  if (name !== 'capture-expiry') return;
  for (const [key, job] of Object.entries(await chrome.storage.session.get(null))) {
    if (key.startsWith('capture:') && job.expires < Date.now())
      await finish(key, job, { error: '验证等待已超时，请重新抓取。' });
  }
});
