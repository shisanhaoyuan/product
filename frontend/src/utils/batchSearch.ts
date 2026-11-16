export function parseBatchIdentifiers(value: string) {
  const tokens = value.split(/[\s,，;；、]+/u).filter(Boolean);
  const seen = new Set<string>();
  const identifiers = tokens.filter((token) => {
    const key = token.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const error =
    value.length > 6000
      ? '批量搜索内容不能超过 6000 个字符'
      : identifiers.length > 100
        ? '一次最多搜索 100 个不同的 SKU 或 ASIN'
        : identifiers.some((token) => token.length > 200)
          ? '单个 SKU 或 ASIN 不能超过 200 个字符'
          : '';
  return { identifiers, duplicates: tokens.length - identifiers.length, error };
}

// Explicit list separators also allow letters-only SKUs. Plain phrases retain
// keyword search; space-separated batches must look like identifiers.
export function looksLikeBatchIdentifiers(value: string) {
  const tokens = value.split(/[\s,，;；、]+/u).filter(Boolean);
  const entries = value
    .split(/[\r\n\t,，;；、]+/u)
    .map((entry) => entry.trim())
    .filter(Boolean);
  return (
    tokens.length > 1 &&
    ((/[\r\n\t,，;；、]/u.test(value) && entries.every((entry) => !/\s/u.test(entry))) ||
      tokens.every(
        (token) => /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(token) && /[\d._:-]/.test(token),
      ))
  );
}

export function productSearch(value: string) {
  const batch = looksLikeBatchIdentifiers(value);
  const parsed = parseBatchIdentifiers(value);
  return {
    q: batch ? '' : value.trim(),
    identifiers: batch ? parsed.identifiers.join('\n') : '',
    error: batch ? parsed.error : '',
  };
}
