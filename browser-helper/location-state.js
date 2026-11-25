// An empty or changing header during page load is not evidence that the ZIP expired.
globalThis.jiwuLocationState = () => {
  let value = '', since = 0, readySince = null;
  return (text, ready, now, marketplace = 'US') => {
    text = (text || '').trim();
    if ((marketplace === 'CA' ? /\bM5V\s*3L9\b/i : /\b10001\b/).test(text)) { value = text; since = now; return 'valid'; }
    if (!ready) { readySince = null; value = ''; since = now; return 'pending'; }
    if (readySince === null) readySince = now;
    if (text !== value) { value = text; since = now; }
    if (now - readySince < 8000) return 'pending';
    if (!text) return now - readySince >= 20000 ? 'invalid' : 'pending';
    return now - since >= 6000 ? 'invalid' : 'pending';
  };
};
