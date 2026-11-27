// Stateful, read-only offer expansion. Shared with the server browser.
globalThis.jiwuOfferReader = function () {
  // display:contents / zero-height wrappers can have visible descendants.
  const visible = node => node && !node.hidden && getComputedStyle(node).display !== 'none' &&
    getComputedStyle(node).visibility !== 'hidden' && (node.getClientRects().length ||
      [...node.querySelectorAll('*')].some(child => child.getClientRects().length && getComputedStyle(child).visibility !== 'hidden'));
  const started = Date.now();
  let quoteSignature = '', quoteStable = 0;

  let regularSelected = false, opened = false, ticks = 0, last = '', stable = 0;
  return function () {
    const regular = [...document.querySelectorAll('#rightCol label, #rightCol button, #rightCol a, #rightCol span')]
      .find(node => visible(node) && /^Regular\s+Price$/i.test(node.textContent.trim()));
    if (!regularSelected && regular) { regularSelected = true; regular.click(); return ''; }
    const entries = [...new Set([...document.querySelectorAll('#aod-ingress-link, #olpLinkWidget_feature_div a, #buybox-see-all-buying-choices a, [data-action="show-all-offers-display"]'),
      ...[...document.querySelectorAll('a,button')].filter(node => /^(?:Other sellers on Amazon|See All Buying Options)$/i.test(node.textContent.trim()))])].filter(visible);
    const panel = document.querySelector('#aod-offer-list');
    const panelVisible = panel && (visible(panel) || visible(panel.closest('#aod-container')));
    let incomplete = false;
    if (entries.length || panelVisible) {
      ticks++;
      if (!panelVisible) {
        if (!opened) { opened = true; entries[0]?.click(); }
        if (ticks < 24) return '';
        incomplete = true;
      } else {
        const more = document.querySelector('#aod-show-more-offers');
        const loading = [...(panel.closest('#aod-container') || panel.parentElement).querySelectorAll('[aria-busy="true"], .a-spinner-wrapper')].some(visible);
        let scroll = panel;
        while (scroll && !(scroll.scrollHeight > scroll.clientHeight + 2 && /auto|scroll/.test(getComputedStyle(scroll).overflowY))) scroll = scroll.parentElement;
        const moved = scroll && scroll.scrollTop + scroll.clientHeight < scroll.scrollHeight - 2;
        if (scroll) scroll.scrollTop = scroll.scrollHeight;
        const hasMore = visible(more);
        if (hasMore) more.click();
        const signature = panel.textContent;
        stable = !loading && !moved && !hasMore && signature === last ? stable + 1 : 0;
        last = signature;
        if (stable < 2) { if (ticks < 24) return ''; incomplete = true; }
      }
    }
    function excluded(node) {
      for (let p = node; p; p = p.parentElement) {
        if (/primeSavings|recommend|sponsored/i.test(p.id || '')) return true;
        if (p.matches('.a-accordion-row') && !p.matches('.a-accordion-active')) return true;
        const style = getComputedStyle(p);
        // a-offscreen is an accessible price, not an inactive offer.
        if (p.hidden || style.display === 'none' || style.visibility === 'hidden') return true;
      }
      return false;
    }
    function clone(node) {
      const result = node.cloneNode(true), originals = [...node.querySelectorAll('*')], copies = [...result.querySelectorAll('*')];
      originals.forEach((child, i) => { if (excluded(child)) copies[i].remove(); });
      // Keep only identity inputs. No cart/session tokens leave the browser.
      result.querySelectorAll('input').forEach(input => {
        if (!/^(?:asin|items\[0\.base\]\[asin\])$/i.test(input.name || '')) input.remove();
      });
      // Seller and delivery text can live inside buttons. Keep their text, strip actions.
      result.querySelectorAll('script,style,iframe').forEach(node => node.remove());
      for (const el of [result, ...result.querySelectorAll('*')]) {
        for (const attr of [...el.attributes]) {
          if (!['id','class','style','hidden','aria-hidden','data-a-strike','tabular-attribute-name',
                'data-csa-c-delivery-price','name','value','data-asin'].includes(attr.name)) el.removeAttribute(attr.name);
        }
      }
      return result;
    }
    const wrapper = document.createElement('section'); wrapper.id = 'catalog-main-offer';
    const selectors = ['#corePriceDisplay_desktop_feature_div', '#corePrice_feature_div', '#apex_desktop', '#apex_mobile', '#corePrice_desktop', '#corePrice_mobile_feature_div', '#desktop_buybox', '#buybox', '#buybox_feature_div', '#mobile_buybox',
      '#merchant-info', '#merchantInfoFeature_feature_div', '#tabular-buybox', '#sellerProfileTriggerId', '[tabular-attribute-name="Sold by"]', '#deliveryBlockMessage', '#deliveryMessageMirId',
      '#price_inside_buybox', '#newBuyBoxPrice', '#priceblock_ourprice', '#priceblock_dealprice',
      '[id^="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE"]'];
    const nodes = [...new Set(selectors.flatMap(s => [...document.querySelectorAll(s)]))].filter(node => visible(node) && !excluded(node));
    for (const node of nodes) if (!nodes.some(other => other !== node && other.contains(node))) wrapper.append(clone(node));
    const priceNodes = wrapper.querySelectorAll('.a-price, .a-offscreen, .apex-pricetopay-accessibility-label, .apex-basisprice-offscreen-label, #price_inside_buybox, #priceblock_ourprice, #priceblock_dealprice');
    const hasPrice = [...priceNodes].some(node => /(?:\$|CAD|USD)\s*[0-9]/.test(node.textContent));
    const unavailable = /currently unavailable|out of stock/i.test(document.querySelector('#availability')?.textContent || '');
    const signature = wrapper.textContent;
    quoteStable = signature === quoteSignature ? quoteStable + 1 : 0;
    quoteSignature = signature;
    // Stable empty HTML is not a completed offer. Wait for hydration (bounded),
    // then preserve partial data and diagnostics instead of dropping the product.
    const elapsed = Date.now() - started;
    if (elapsed < 30000 && (!hasPrice && !unavailable || document.readyState !== 'complete' || quoteStable < 2)) return '';
    wrapper.setAttribute('data-collector-offer-state', hasPrice ? 'price_present' : unavailable ? 'unavailable' : 'price_missing_after_wait');
    wrapper.setAttribute('data-collector-offer-nodes', String(nodes.length));
    const location = document.querySelector('#glow-ingress-line2, #contextualIngressPtLabel_deliveryShortLine');
    if (incomplete) wrapper.setAttribute('data-offers-incomplete', 'true');
    return wrapper.outerHTML + (location?.outerHTML || '') + (panelVisible ? clone(panel).outerHTML : '');
  };
};
