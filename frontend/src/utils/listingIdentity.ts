import type { ListingRow } from '../api/publishing';
import type { PrelistingPreview, ProductDetail } from '../api/types';

const normalizedAsin = (value: string) => (value || '').trim().toUpperCase();

// Resolve inside this store's document: MY SKU -> its own source child ASIN.
// A source SKU label is never needed to join the two editor surfaces.
export function listingAsins(rows: ListingRow[], basis: PrelistingPreview) {
  const result: Record<string, string> = {};
  for (const row of rows) {
    const variants = basis.items
      .filter((item) => item.listing_id === row.listing_id && item.source === 'Amazon')
      .flatMap((item) => item.variants)
      .filter((variant) => variant.platform_sku === row.platform_sku);
    if (variants.length === 1 && normalizedAsin(variants[0]!.asin))
      result[row.platform_sku] = normalizedAsin(variants[0]!.asin);
  }
  return result;
}

export function listingIdentityLabel(platformSku: string, asins: Record<string, string>) {
  return asins[platformSku] ? `ASIN ${asins[platformSku]}` : platformSku;
}

export function listingSourceRecord(product: ProductDetail, sourceSku: string, asin: string) {
  const candidates = product.variants.length ? product.variants : [product];
  // Amazon joins by ASIN. Suppliers without ASIN retain their own product identity.
  const matches =
    product.source === 'Amazon'
      ? candidates.filter(
          (candidate) => !!asin.trim() && normalizedAsin(candidate.asin) === normalizedAsin(asin),
        )
      : candidates.filter((candidate) => candidate.sku === sourceSku);
  return matches.length === 1 ? matches[0] : undefined;
}
