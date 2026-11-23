import type { ProductDetail } from '../api/types';
import { safeUrl } from './display';
import { listingSourceRecord } from './listingIdentity';

// Never borrow images from a parent with a different ASIN, or another child.
export function listingSourceImages(product: ProductDetail, sku: string, asin: string): string[] {
  const target = listingSourceRecord(product, sku, asin);
  if (!target) return [];
  const ownImagesEdited = product.local_edit?.changes?.some(
    (c) => c.variant_sku === target.sku && c.field === 'images',
  );
  const parentImagesEdited = product.local_edit?.changes?.some(
    (c) => !c.variant_sku && c.field === 'images',
  );
  const images =
    product.source === 'Amazon' &&
    asin &&
    product.asin === asin &&
    !ownImagesEdited &&
    (parentImagesEdited || !target.images.length)
      ? product.images
      : target.images;
  return [...new Set(images.filter((u) => !!safeUrl(u)))];
}
