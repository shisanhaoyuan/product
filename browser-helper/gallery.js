// Current product gallery only. Shared by Chrome and the server browser.
globalThis.jiwuImageGallery = function () {
  function initialGallery(script) {
    const match = /["']colorImages["']\s*:\s*\{\s*["']initial["']\s*:\s*/.exec(script);
    if (!match) return '';
    const payload = script.slice(match.index + match[0].length);
    const wrapped = /^A\.\$\.parseJSON\(\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")\s*\)/.exec(payload);
    if (wrapped) return wrapped[0];
    if (payload[0] !== '[') return '';
    let depth = 0, quoted = false, escaped = false;
    for (let i = 0; i < Math.min(payload.length, 1000000); i++) {
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
  const valid = value => {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && !url.username && !url.password &&
        ['m.media-amazon.com', 'images-na.ssl-images-amazon.com'].includes(url.hostname) &&
        url.pathname.startsWith('/images/I/');
    } catch { return false; }
  };
  const gallery = [...document.scripts].map(script => initialGallery(script.textContent || '')).find(Boolean);
  const section = document.createElement('section'); section.id = 'jiwu-image-gallery';
  section.setAttribute('data-asin', document.querySelector('input#ASIN')?.value || '');
  const urls = new Set();
  const nodes = [...new Set(document.querySelectorAll('#landingImage, #imgBlkFront, #ebooksImgBlkFront, #imgTagWrapperId img, #imageBlock img, #altImages .imageThumbnail img'))];
  for (const node of nodes) {
    if (node.closest('[id*="recommend"], [id*="sponsored"], [id*="twister"], [id^="variation_"], .videoThumbnail')) continue;
    let dynamic = [];
    try {
      dynamic = Object.entries(JSON.parse(node.getAttribute('data-a-dynamic-image') || '{}'))
        .filter(([url]) => valid(url)).sort((a,b) => {
          const area = entry => Array.isArray(entry[1]) ? Number(entry[1][0]) * Number(entry[1][1]) || 0 : 0;
          return area(b) - area(a);
        }).map(([url]) => url);
    } catch { /* A malformed old attribute must not hide the displayed image. */ }
    const value = [node.getAttribute('data-old-hires'), ...dynamic, node.currentSrc, node.getAttribute('src'), node.getAttribute('data-src')].find(valid);
    if (value && urls.size < 100) urls.add(value);
  }
  for (const url of urls) { const image = document.createElement('img'); image.setAttribute('src', url); section.append(image); }
  const html = section.outerHTML + (gallery ? `'colorImages': {'initial': ${gallery}}` : '');
  return { html, available: !!gallery || urls.size > 0, count: urls.size, signature: html };
};
