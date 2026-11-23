import { describe, expect, it } from 'vitest';
import type { ListingRow } from '../api/publishing';
import type { PrelistingPreview, ProductDetail } from '../api/types';
import { listingAsins, listingIdentityLabel, listingSourceRecord } from './listingIdentity';

describe('listing ASIN and store SKU identity', () => {
  it('joins by MY SKU within its source product regardless of source labels or order', () => {
    const rows = [
      { platform_sku: 'MY21AAAAA1', listing_id: 'parent' },
      { platform_sku: 'MY21BBBBB2', listing_id: 'parent' },
      { platform_sku: 'MY22CCCCC3', listing_id: 'other-store-parent' },
    ] as ListingRow[];
    const basis = {
      items: [
        {
          listing_id: 'parent',
          source: 'Amazon',
          variants: [
            { sku: 'same-label', platform_sku: 'MY21BBBBB2', asin: 'B000000002' },
            { sku: 'same-label', platform_sku: 'MY21AAAAA1', asin: ' b000000001 ' },
          ],
        },
        {
          listing_id: 'other-store-parent',
          source: 'Amazon',
          variants: [{ sku: 'another-label', platform_sku: 'MY22CCCCC3', asin: 'B000000001' }],
        },
      ],
    } as PrelistingPreview;
    expect(listingAsins(rows, basis)).toEqual({
      MY21AAAAA1: 'B000000001',
      MY21BBBBB2: 'B000000002',
      MY22CCCCC3: 'B000000001',
    });
    expect(listingIdentityLabel('MY21AAAAA1', listingAsins(rows, basis))).toBe('ASIN B000000001');
    basis.items[0]!.variants[0]!.platform_sku = 'MY21AAAAA1';
    expect(listingAsins(rows, basis)).toEqual({ MY22CCCCC3: 'B000000001' });
  });

  it('does not fall back to another Amazon ASIN when the source SKU happens to match', () => {
    const product = {
      source: 'Amazon',
      variants: [
        { sku: 'old-label', asin: 'B000000002' },
        { sku: 'new-label', asin: 'B000000001' },
      ],
    } as ProductDetail;
    expect(listingSourceRecord(product, 'old-label', 'B000000001')).toBe(product.variants[1]);
    expect(listingSourceRecord(product, 'old-label', '')).toBeUndefined();
    expect(listingSourceRecord(product, 'old-label', 'B000000003')).toBeUndefined();
    product.variants.push({ ...product.variants[1]! });
    expect(listingSourceRecord(product, 'old-label', 'B000000001')).toBeUndefined();
  });

  it('keeps supplier source matching available without displaying a redundant editor SKU', () => {
    const product = {
      source: 'Saleyee',
      sku: 'supplier-1',
      variants: [],
    } as unknown as ProductDetail;
    expect(listingSourceRecord(product, 'supplier-1', '')).toBe(product);
    expect(listingSourceRecord(product, 'supplier-2', '')).toBeUndefined();
    expect(listingIdentityLabel('MY21AAAAA1', {})).toBe('MY21AAAAA1');
  });
});
