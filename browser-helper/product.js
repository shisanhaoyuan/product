// Shared by our extension and Wraith. Parse data only; never execute page scripts.
globalThis.jiwuProductDetails = function () {
  function objects(text, key) {
    const values = [];
    const pattern = new RegExp('["\x27]' + key + '["\x27]\\s*:\\s*', 'g');
    for (const match of text.matchAll(pattern)) {
      const rest = text.slice(match.index + match[0].length);
      if (!['{', '['].includes(rest[0])) continue;
      let depth = 0, quoted = false, escaped = false;
      for (let i = 0; i < Math.min(rest.length, 1000000); i++) {
        const c = rest[i];
        if (quoted) {
          if (escaped) escaped = false;
          else if (c === '\\') escaped = true;
          else if (c === '"') quoted = false;
        } else if (c === '"') quoted = true;
        else if (c === '[' || c === '{') depth++;
        else if ((c === ']' || c === '}') && --depth === 0) {
          try { values.push(JSON.parse(rest.slice(0, i + 1))); } catch { /* Not JSON. */ }
          break;
        }
      }
    }
    return values;
  }
  const current = document.querySelector('input#ASIN')?.value;
  const valid = value => typeof value === 'string' && /^[A-Z0-9]{10}$/.test(value);
  const variants = new Set(), parents = new Set(), sources = new Set();
  let mapped = false, truncated = false;
  const add = value => {
    if (!valid(value)) return;
    if (variants.size >= 500 && !variants.has(value)) { truncated = true; return; }
    variants.add(value);
  };
  const controls = document.querySelectorAll('[id^="variation_"], #twister, #twister_feature_div');
  for (const section of controls) {
    section.querySelectorAll('[data-asin], [data-defaultasin], [data-dp-url], option').forEach(node => {
      add(node.getAttribute('data-asin')); add(node.getAttribute('data-defaultasin'));
      const url = /\/dp\/([A-Z0-9]{10})(?:[/?]|$)/.exec(node.getAttribute('data-dp-url') || '');
      const option = /(?:^|,)([A-Z0-9]{10})$/.exec(node.value || '');
      if (url) add(url[1]); if (option) add(option[1]);
    });
  }
  if (variants.size) sources.add('variation_controls');
  for (const script of document.scripts) {
    const text = script.textContent || '';
    for (const key of ['dimensionValuesDisplayData', 'asinVariationValues', 'asinToDimensionIndexMap', 'dimensionToAsinMap']) {
      for (const value of objects(text, key)) {
        if (!value || Array.isArray(value)) continue;
        const ids = (key === 'dimensionToAsinMap' ? Object.values(value) : Object.keys(value)).filter(valid);
        // Recommended products can have their own maps. Only use maps containing this ASIN.
        if (!ids.includes(current)) continue;
        ids.forEach(add); sources.add(key); mapped = true;
      }
    }
    for (const match of text.matchAll(/"mediaAsin"\s*:\s*"([A-Z0-9]{10})"\s*,\s*"parentAsin"\s*:\s*"([A-Z0-9]{10})"/g)) {
      if (match[1] === current) parents.add(match[2]);
    }
  }
  add(current);
  const root = document.createElement('section'); root.id = 'jiwu-product-details';
  const selectors = ['#productTitle', '#feature-bullets', '#featurebullets_feature_div', '#productDescription', '#productOverview_feature_div',
    '#productDetails_techSpec_section_1', '#productDetails_techSpec_section_2', '#productDetails_detailBullets_sections1',
    '#productDetails_db_sections', '#technicalSpecifications_section_1', '#technicalSpecifications_section_2',
    '#detailBullets_feature_div', '#productFactsDesktopExpander', '#wayfinding-breadcrumbs_feature_div', '#availability'];
  const nodes = [...new Set(selectors.flatMap(selector => [...document.querySelectorAll(selector)]))];
  for (const node of nodes) if (!nodes.some(other => other !== node && other.contains(node))) root.append(node.cloneNode(true));
  for (const node of root.querySelectorAll('script,style,input,form,iframe,button')) node.remove();
  for (const node of root.querySelectorAll('*')) {
    for (const attr of [...node.attributes]) if (!['id', 'class'].includes(attr.name)) node.removeAttribute(attr.name);
  }
  const attributes = {};
  for (const control of document.querySelectorAll('[id^="variation_"]')) {
    const value = (control.querySelector('.selection, option:checked')?.textContent || '').trim();
    const name = control.id.replace(/^variation_/, '');
    if (value && value.length <= 500 && !/^select|choose/i.test(value)) attributes[name] = value;
  }
  const metadata = (id, value) => { const node = document.createElement('div'); node.id = id; node.textContent = JSON.stringify(value); root.append(node); };
  metadata('jiwu-attributes', attributes);
  metadata('jiwu-variants', [...variants]);
  const standalone = !mapped && parents.size === 1 && parents.has(current) && !controls.length;
  metadata('jiwu-variant-evidence', { current_asin: current, parent_asins: [...parents], sources: [...sources], complete: (mapped || standalone) && !truncated, truncated });
  return root.outerHTML;
};
