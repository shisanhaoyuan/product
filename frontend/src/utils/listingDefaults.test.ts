import { describe, expect, it } from 'vitest';
import type { ListingDocument, ProductType } from '../api/publishing';
import { applyListingDefaults, matchProductType } from './listingDefaults';

const types: ProductType[] = ['Chairs', 'Desks'].map((name) => ({
  name,
  category: 'Furniture',
  group: 'Furniture',
  description: '',
}));
const nodes = [{ id: 'store-node', name: 'DH', type: 'PHYSICAL' }];
function document(): ListingDocument {
  return {
    rows: ['a', 'b'].map((sku) => ({
      platform_sku: sku,
      source_sku: sku,
      listing_id: sku,
      product_type: '',
      fulfillment_center_id: '',
      identification: { mode: 'identifier', type: 'EAN', value: '', submission_id: '' },
      visible: { productName: sku },
      orderable: { price: 49.99 },
      extra: {},
    })),
    basis: {
      items: [
        { listing_id: 'a', category: 'Furniture > CHAIRS' },
        { listing_id: 'b', category: 'Desks' },
      ],
    },
    results: {},
  } as unknown as ListingDocument;
}

describe('listing metadata defaults', () => {
  it('matches each source listing, preserves content, and is idempotent', () => {
    const doc = document();
    const content = doc.rows.map((r) => JSON.stringify([r.visible, r.orderable, r.identification]));
    expect(applyListingDefaults(doc, types, nodes)).toBe(true);
    expect(doc.rows.map((r) => r.product_type)).toEqual(['Chairs', 'Desks']);
    expect(doc.rows.map((r) => r.fulfillment_center_id)).toEqual(['store-node', 'store-node']);
    expect(doc.rows.map((r) => JSON.stringify([r.visible, r.orderable, r.identification]))).toEqual(
      content,
    );
    expect(applyListingDefaults(doc, types, nodes)).toBe(false);
  });
  it('does not invent options, use a broad category or choose an ambiguous warehouse', () => {
    expect(matchProductType('Furniture', types)).toBe('');
    expect(matchProductType('Chair Covers', types)).toBe('');
    expect(matchProductType('Chairs', [...types, types[0]!])).toBe('');
    const doc = document();
    expect(applyListingDefaults(doc, [], [...nodes, { ...nodes[0]!, id: 'other' }])).toBe(false);
  });
  it('preserves manual selections, identifiers, exemptions and submitted rows', () => {
    const doc = document();
    doc.rows[0]!.product_type = 'Manual';
    doc.rows[0]!.fulfillment_center_id = 'manual-node';
    doc.rows[0]!.identification = {
      mode: 'exemption',
      type: 'UPC',
      value: '',
      submission_id: 'approved',
    };
    doc.results.b = { status: 'processing', message: '', errors: [], attempt_id: 'attempt' };
    const before = JSON.stringify(doc);
    expect(applyListingDefaults(doc, types, nodes)).toBe(false);
    expect(JSON.stringify(doc)).toBe(before);
  });
});
