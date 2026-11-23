import { describe, expect, it } from 'vitest';
import type { ListingDocument, ProductType } from '../api/publishing';
import { applyListingDefaults, matchProductType, setFamilyProductType } from './listingDefaults';

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
  it('inherits the family type even when the unclassified child is listed first', () => {
    const doc = document();
    doc.rows[1]!.listing_id = 'a';
    doc.rows[1]!.product_type = 'Chairs';
    const before = doc.rows.map((row) =>
      JSON.stringify([row.visible, row.orderable, row.identification]),
    );
    expect(applyListingDefaults(doc, types, [])).toBe(true);
    expect(doc.rows.map((row) => row.product_type)).toEqual(['Chairs', 'Chairs']);
    expect(
      doc.rows.map((row) => JSON.stringify([row.visible, row.orderable, row.identification])),
    ).toEqual(before);
    expect(applyListingDefaults(doc, types, [])).toBe(false);
  });
  it('changes the type for the entire source family and leaves other products untouched', () => {
    const doc = document();
    doc.rows.push({
      ...structuredClone(doc.rows[0]!),
      platform_sku: 'a-child',
      product_type: 'Chairs',
    });
    setFamilyProductType(doc, 'a-child', 'Desks');
    expect(doc.rows.map((row) => row.product_type)).toEqual(['Desks', '', 'Desks']);
    setFamilyProductType(doc, 'a', '');
    expect(doc.rows.map((row) => row.product_type)).toEqual(['', '', '']);
  });
  it('inherits a submitted sibling type but prevents changing its family routing', () => {
    const doc = document();
    doc.rows[1]!.listing_id = 'a';
    doc.rows[1]!.product_type = 'Chairs';
    doc.results.b = { status: 'published', message: '', errors: [] };
    applyListingDefaults(doc, types, []);
    expect(doc.rows[0]!.product_type).toBe('Chairs');
    expect(() => setFamilyProductType(doc, 'a', 'Desks')).toThrow('沿用');
    expect(doc.rows.every((row) => row.product_type === 'Chairs')).toBe(true);
  });
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
  it('uses returned category names and explicit sofa equivalents only from actual options', () => {
    const sofas = [{ ...types[0]!, name: 'Sofas & Couches' }];
    for (const category of ['Sofas', 'Furniture > Sectional Sofas', '沙发', 'Sofas and Couches'])
      expect(matchProductType(category, sofas)).toBe('Sofas & Couches');
    expect(matchProductType('Furniture', sofas, ['Sofas'])).toBe('Sofas & Couches');
    expect(matchProductType('Furniture', sofas)).toBe('');
    expect(matchProductType('Sofas', types)).toBe('');
    for (const category of ['Sofa Covers', 'Sofa Beds', 'Outdoor Sofas', 'Sofa Tables'])
      expect(matchProductType(category, sofas)).toBe('');
    expect(matchProductType('Sofas', [...sofas, { ...sofas[0]!, name: 'Sofas' }])).toBe('');
    expect(matchProductType('Chairs', [...types, ...sofas], ['Sofas'])).toBe('');
  });
  it('fills every matching SKU, keeps unrelated fields and choices, and skips submitted rows', () => {
    const doc = document();
    doc.basis.items[0]!.category = 'Furniture';
    doc.basis.items[0]!.category_hints = ['Sofas'];
    doc.rows[1]!.identification = {
      mode: 'identifier',
      type: 'UPC',
      value: '012345678905',
      submission_id: '',
    };
    const before = doc.rows.map((r) => JSON.stringify([r.visible, r.orderable, r.identification]));
    const options = [...types, { ...types[0]!, name: 'Sofas & Couches' }];
    expect(applyListingDefaults(doc, options, nodes)).toBe(true);
    expect(doc.rows.map((r) => r.product_type)).toEqual(['Sofas & Couches', 'Desks']);
    expect(doc.rows.map((r) => JSON.stringify([r.visible, r.orderable, r.identification]))).toEqual(
      before,
    );
    expect(applyListingDefaults(doc, options, nodes)).toBe(false);
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

it('matches Amazon cat tree titles to real store types and rejects unrelated or ambiguous options', () => {
  const cats = [{ ...types[0]!, name: 'Cat Trees' }];
  expect(matchProductType('', cats, [], '63-inch Cat Tree with Scratching Posts')).toBe(
    'Cat Trees',
  );
  expect(matchProductType('Pet Supplies > Cat Trees & Condos', cats)).toBe('Cat Trees');
  expect(matchProductType('', types, [], 'Cat Tree')).toBe('');
  expect(matchProductType('', [...cats, { ...cats[0]!, name: 'Cat Towers' }], [], 'Cat Tree')).toBe(
    '',
  );
  for (const title of [
    'Cat Tree Replacement Cover',
    'Cat Tree Accessories',
    'A tree for the garden',
  ])
    expect(matchProductType('', cats, [], title)).toBe('');
  const sofas = [{ ...types[0]!, name: 'Sofas & Couches' }];
  expect(matchProductType('Outdoor Sofas', sofas, [], 'Patio Sectional Sofas')).toBe('');
  expect(matchProductType('', sofas, [], 'Sectional Sofas Bed Replacement Covers')).toBe('');
});

it('uses the current Amazon variant title and preserves submitted rows', () => {
  const doc = document();
  const item = doc.basis.items[0]!;
  item.category = '';
  item.variants = [
    { platform_sku: 'a', content: { copy: { title: 'Cat Tree' } } },
  ] as typeof item.variants;
  const cats = [{ ...types[0]!, name: 'Cat Trees' }];
  expect(applyListingDefaults(doc, cats, [])).toBe(true);
  expect(doc.rows[0]!.product_type).toBe('Cat Trees');
  doc.rows[0]!.product_type = '';
  doc.results.a = { status: 'processing', message: '', errors: [], attempt_id: 'attempt' };
  expect(applyListingDefaults(doc, cats, [])).toBe(false);
});
