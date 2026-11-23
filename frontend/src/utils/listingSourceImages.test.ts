import { describe, expect, it } from 'vitest';
import type { ProductDetail } from '../api/types';
import { listingSourceImages } from './listingSourceImages';

function product(): ProductDetail {
  return {
    source: 'Amazon',
    sku: 'parent',
    asin: 'B000000001',
    images: ['https://img.test/parent.jpg'],
    variants: [{ sku: 'child', asin: 'B000000001', images: ['https://img.test/child.jpg'] }],
    local_edit: { changes: [] },
  } as unknown as ProductDetail;
}

describe('listing current source images', () => {
  it('keeps child images, uses edited same-ASIN parent images, and respects child edits', () => {
    const p = product();
    expect(listingSourceImages(p, 'child', p.asin)).toEqual(p.variants[0]!.images);
    p.local_edit!.changes.push({ field: 'images', variant_sku: '', key: '', value: p.images });
    expect(listingSourceImages(p, 'child', p.asin)).toEqual(p.images);
    p.local_edit!.changes.push({ field: 'images', variant_sku: 'child', key: '', value: [] });
    expect(listingSourceImages(p, 'child', p.asin)).toEqual(p.variants[0]!.images);
    p.variants[0]!.images = [];
    expect(listingSourceImages(p, 'child', p.asin)).toEqual([]);
  });
  it('matches ASIN even after a source SKU changes, and never borrows another ASIN', () => {
    const p = product();
    p.variants[0]!.images = [];
    expect(listingSourceImages(p, 'child', p.asin)).toEqual(p.images);
    p.variants[0]!.asin = 'B000000002';
    expect(listingSourceImages(p, 'child', 'B000000002')).toEqual([]);
    expect(listingSourceImages(p, 'child', p.asin)).toEqual([]);
    expect(listingSourceImages(p, 'absent', p.asin)).toEqual([]);
    p.variants.push({ ...p.variants[0]! });
    expect(listingSourceImages(p, 'child', 'B000000002')).toEqual([]);
  });
  it('does not require the redundant source SKU for an Amazon image match', () => {
    const p = product();
    expect(listingSourceImages(p, 'old-source-label', p.asin)).toEqual(p.variants[0]!.images);
    p.variants[0]!.sku = 'new-label';
    p.local_edit!.changes.push({ field: 'images', variant_sku: 'new-label', key: '', value: [] });
    p.variants[0]!.images = [];
    expect(listingSourceImages(p, 'old-source-label', p.asin)).toEqual([]);
  });
  it('uses single products and preserves safe URLs exactly while filtering duplicates and unsafe URLs', () => {
    const p = product();
    p.variants = [];
    const local = '/api/products/images/assets/' + 'a'.repeat(64);
    p.images = [local, 'https://img.test/a.jpg?v=1', local, 'javascript:alert(1)'];
    expect(listingSourceImages(p, 'parent', p.asin)).toEqual([local, 'https://img.test/a.jpg?v=1']);
    expect(listingSourceImages(p, 'parent', 'B000000002')).toEqual([]);
  });
});
