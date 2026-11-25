(() => {
  // Observe the page only. All CAPTCHA interaction remains with the user.
  let running = false, misses = 0, imageWait = 0;
  let lastSource = '', sourceStable = 0;
  const locationState = globalThis.jiwuLocationState();
  const visible = node => node && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
  const sourceSnippet = globalThis.jiwuOfferReader();
  function stop() {
    clearInterval(timer);
    document.getElementById('jiwu-location-help')?.remove();
  }
  const timer = setInterval(async () => {
    if (running) return;
    running = true;
    try {
      const task = await chrome.runtime.sendMessage({ channel: 'catalog-capture-v1', action: 'task' });
      if (!task?.asin) { if (++misses >= 4) stop(); return; }
      if (document.querySelector('form[action*="validateCaptcha"], input#captchacharacters') ||
          /sorry, we just need to make sure|click the button below to continue shopping|verify you are human|robot check|press and hold/i.test(document.body?.innerText || '')) {
        await chrome.runtime.sendMessage({ channel: 'catalog-capture-v1', action: 'attention', reason: 'verification' });
        return;
      }
      const asins = [...document.querySelectorAll('input#ASIN')].map(el => el.value);
      const asin = asins[0];
      if (asin !== task.asin || asins.some(value => value !== asin) ||
          !new RegExp(`/(?:[^/]+/)?dp/${asin}/?$`).test(location.pathname)) return;
      if (document.querySelector('form[action*="validateCaptcha"], input#captchacharacters')) return;
      const asinInput = document.createElement('input');
      asinInput.id = 'ASIN'; asinInput.setAttribute('value', asin);
      if (task.mode === 'source' || task.mode === 'import') {
        const delivery = [...document.querySelectorAll('#glow-ingress-line2, #contextualIngressPtLabel_deliveryShortLine')]
          .filter(visible).map(node => node.textContent).join(' ');
        const locationStatus = locationState(delivery, document.readyState === 'complete', Date.now());
        if (locationStatus === 'pending') return;
        if (locationStatus === 'invalid') {
          await chrome.runtime.sendMessage({ channel: 'catalog-capture-v1', action: 'attention', reason: 'location' });
          lastSource = ''; sourceStable = 0;
          if (!document.getElementById('jiwu-location-help')) {
            const banner = document.createElement('div');
            banner.id = 'jiwu-location-help';
            banner.textContent = '集物采集已暂停：请点击 Amazon 左上角配送地址，设置美国邮编 10001 并确认。完成后自动继续；本浏览器会保留地区设置，请勿使用无痕模式。';
            banner.style.cssText = 'position:fixed;bottom:20px;left:20px;right:20px;z-index:2147483647;padding:18px;background:#e8f1fc;color:#172d48;border:2px solid #709ac7;border-radius:12px;font:16px/1.6 sans-serif;box-shadow:0 4px 20px #0003';
            document.body.append(banner);
          }
          return;
        }
        document.getElementById('jiwu-location-help')?.remove();
        const snippet = sourceSnippet();
        sourceStable = snippet && snippet === lastSource ? sourceStable + 1 : 0;
        lastSource = snippet;
        if (!snippet || sourceStable < 2) return;
        const gallery = globalThis.jiwuImageGallery();
        if (!gallery.available && ++imageWait < 8) return;
        const html = asinInput.outerHTML + snippet + globalThis.jiwuProductDetails() + gallery.html;
        if (html.length > 6 * 1024 * 1024) return;
        const result = await chrome.runtime.sendMessage({ channel: 'catalog-capture-v1', action: 'page', asin, html });
        if (result?.accepted) stop();
        return;
      }
      const gallery = globalThis.jiwuImageGallery();
      if (!gallery.available && ++imageWait < 8) return;
      const html = asinInput.outerHTML + gallery.html;
      if (html.length > 6 * 1024 * 1024) return;
      const result = await chrome.runtime.sendMessage({ channel: 'catalog-capture-v1', action: 'page', asin, html });
      if (result?.accepted) stop();
    } catch { stop(); }
    finally { running = false; }
  }, 1500);
  setTimeout(() => stop(), 600000);
})();
