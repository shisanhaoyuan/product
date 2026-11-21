(() => {
  // Observe the page only. All CAPTCHA interaction remains with the user.
  function initialGallery(script) {
    const match = /["']colorImages["']\s*:\s*\{\s*["']initial["']\s*:\s*/.exec(script);
    if (!match) return '';
    const payload = script.slice(match.index + match[0].length);
    const wrapped = /^A\.\$\.parseJSON\(\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")\s*\)/.exec(payload);
    if (wrapped) return wrapped[0];
    if (payload[0] !== '[') return '';
    let depth = 0, quoted = false, escaped = false;
    for (let i = 0; i < payload.length; i++) {
      const char = payload[i];
      if (quoted) {
        if (escaped) escaped = false;
        else if (char === '\\') escaped = true;
        else if (char === '"') quoted = false;
      } else if (char === '"') quoted = true;
      else if (char === '[') depth++;
      else if (char === ']' && --depth === 0) return payload.slice(0, i + 1);
    }
    return '';
  }
  let running = false, misses = 0, openedOffers = false, selectedRegular = false;
  let lastSource = '', sourceStable = 0;
  const visible = node => node && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
  function sourceSnippet() {
    const regular = [...document.querySelectorAll('label, button, a, span')]
      .filter(n => visible(n) && /^Regular\s+Price$/i.test(n.textContent.trim()));
    if (!selectedRegular && regular.length) {
      selectedRegular = true;
      regular[regular.length - 1].click();
      return '';
    }
    const entries = [...new Set([
      ...document.querySelectorAll('a[href*="aod"], [data-action="show-all-offers-display"], #buybox-see-all-buying-choices a, #aod-ingress-link'),
      ...[...document.querySelectorAll('a, button')].filter(n => /Other sellers on Amazon|See All Buying Options/i.test(n.textContent)),
    ])].filter(visible);
    const panel = [...document.querySelectorAll('#aod-offer-list')]
      .find(visible);
    if (entries.length && !panel) {
      if (!openedOffers) { openedOffers = true; entries[0].click(); }
      return '';
    }
    if (panel && [...panel.parentElement.querySelectorAll('[aria-busy="true"], .a-spinner-wrapper')].some(visible)) return '';

    function excludedOfferNode(node) {
      for (let current = node; current; current = current.parentElement) {
        if (/primeSavings|recommend|sponsored/i.test(current.id || '')) return true;
        const style = getComputedStyle(current);
        if (current.hidden || style.display === 'none' || style.visibility === 'hidden') return true;
      }
      return false;
    }
    function offerClone(node) {
      const clone = node.cloneNode(true);
      const originals = [...node.querySelectorAll('*')];
      const copies = [...clone.querySelectorAll('*')];
      originals.forEach((child, index) => {
        if (excludedOfferNode(child)) copies[index].remove();
      });
      return clone;
    }

    const wrapper = document.createElement('section');
    wrapper.id = 'catalog-main-offer';
    const selectors = ['#corePriceDisplay_desktop_feature_div', '#corePrice_feature_div',
      '#desktop_buybox', '#buybox', '#merchant-info', '#tabular-buybox', '#deliveryBlockMessage',
      '[id^="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE"]'];
    const nodes = [...new Set(selectors.flatMap(s => [...document.querySelectorAll(s)]))].filter(node => visible(node) && !excludedOfferNode(node));
    for (const node of nodes) {
      if (nodes.some(other => other !== node && other.contains(node))) continue;
      wrapper.append(offerClone(node));
    }
    if (!wrapper.textContent.trim() && !panel) return '';
    for (const node of wrapper.querySelectorAll('script, style, [id*="recommend"], [id*="sponsored"], #aod-container, #all-offers-display')) node.remove();
    const locationNode = document.querySelector('#glow-ingress-line2, #contextualIngressPtLabel_deliveryShortLine');
    return wrapper.outerHTML + (locationNode?.outerHTML || '') + (panel?.outerHTML || '');
  }
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
      const asins = [...document.querySelectorAll('input#ASIN')].map(el => el.value);
      const asin = asins[0];
      if (asin !== task.asin || asins.some(value => value !== asin) ||
          !new RegExp(`/(?:[^/]+/)?dp/${asin}/?$`).test(location.pathname)) return;
      if (document.querySelector('form[action*="validateCaptcha"], input#captchacharacters')) return;
      const asinInput = document.createElement('input');
      asinInput.id = 'ASIN'; asinInput.setAttribute('value', asin);
      if (task.mode === 'source') {
        const delivery = [...document.querySelectorAll('#glow-ingress-line2, #contextualIngressPtLabel_deliveryShortLine')]
          .filter(visible).map(node => node.textContent).join(' ');
        if (!/\b10001\b/.test(delivery)) {
          lastSource = ''; sourceStable = 0; openedOffers = false;
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
        const gallery = [...document.scripts].map(script => initialGallery(script.textContent)).find(Boolean);
        const landing = document.querySelector('img#landingImage');
        const image = document.createElement('img'); image.id = 'landingImage';
        for (const name of ['data-old-hires', 'data-a-dynamic-image']) {
          if (landing?.hasAttribute(name)) image.setAttribute(name, landing.getAttribute(name));
        }
        const html = asinInput.outerHTML + snippet + image.outerHTML + (gallery ? `'colorImages': {'initial': ${gallery}}` : '');
        if (html.length > 6 * 1024 * 1024) return;
        const result = await chrome.runtime.sendMessage({ channel: 'catalog-capture-v1', action: 'page', asin, html });
        if (result?.accepted) stop();
        return;
      }
      const gallery = [...document.scripts].map(script => initialGallery(script.textContent)).find(Boolean);
      const landing = document.querySelector('img#landingImage');
      if (!gallery && !landing) return;
      const image = document.createElement('img');
      image.id = 'landingImage';
      for (const name of ['data-old-hires', 'data-a-dynamic-image']) {
        if (landing?.hasAttribute(name)) image.setAttribute(name, landing.getAttribute(name));
      }
      const html = asinInput.outerHTML + image.outerHTML + (gallery ? `'colorImages': {'initial': ${gallery}}` : '');
      if (html.length > 6 * 1024 * 1024) return;
      const result = await chrome.runtime.sendMessage({ channel: 'catalog-capture-v1', action: 'page', asin, html });
      if (result?.accepted) stop();
    } catch { stop(); }
    finally { running = false; }
  }, 1500);
  setTimeout(() => stop(), 600000);
})();
