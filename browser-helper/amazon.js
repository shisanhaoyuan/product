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
    const wrapper = document.createElement('section');
    wrapper.id = 'catalog-main-offer';
    const selectors = ['#corePriceDisplay_desktop_feature_div', '#corePrice_feature_div',
      '#desktop_buybox', '#buybox', '#merchant-info', '#tabular-buybox', '#deliveryBlockMessage',
      '[id^="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE"]'];
    const nodes = [...new Set(selectors.flatMap(s => [...document.querySelectorAll(s)]))].filter(visible);
    for (const node of nodes) {
      if (nodes.some(other => other !== node && other.contains(node))) continue;
      wrapper.append(node.cloneNode(true));
    }
    if (!wrapper.textContent.trim() && !panel) return '';
    for (const node of wrapper.querySelectorAll('script, style, [id*="recommend"], [id*="sponsored"], #aod-container, #all-offers-display')) node.remove();
    const locationNode = document.querySelector('#glow-ingress-line2, #contextualIngressPtLabel_deliveryShortLine');
    return wrapper.outerHTML + (locationNode?.outerHTML || '') + (panel?.outerHTML || '');
  }
  const timer = setInterval(async () => {
    if (running) return;
    running = true;
    try {
      const task = await chrome.runtime.sendMessage({ channel: 'catalog-capture-v1', action: 'task' });
      if (!task?.asin) { if (++misses >= 4) clearInterval(timer); return; }
      const asins = [...document.querySelectorAll('input#ASIN')].map(el => el.value);
      const asin = asins[0];
      if (asin !== task.asin || asins.some(value => value !== asin) ||
          !new RegExp(`/(?:[^/]+/)?dp/${asin}/?$`).test(location.pathname)) return;
      if (document.querySelector('form[action*="validateCaptcha"], input#captchacharacters')) return;
      const asinInput = document.createElement('input');
      asinInput.id = 'ASIN'; asinInput.setAttribute('value', asin);
      if (task.mode === 'source') {
        const snippet = sourceSnippet();
        sourceStable = snippet && snippet === lastSource ? sourceStable + 1 : 0;
        lastSource = snippet;
        if (!snippet || sourceStable < 2) return;
        const html = asinInput.outerHTML + snippet;
        if (html.length > 6 * 1024 * 1024) return;
        const result = await chrome.runtime.sendMessage({ channel: 'catalog-capture-v1', action: 'page', asin, html });
        if (result?.accepted) clearInterval(timer);
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
      if (result?.accepted) clearInterval(timer);
    } catch { clearInterval(timer); }
    finally { running = false; }
  }, 1500);
  setTimeout(() => clearInterval(timer), 600000);
})();
