import { describe, expect, it } from 'vitest';
import {
  completeGroupCandidates,
  toggleVariantSelection,
  variantEditorColumns,
  reviewVariantValue,
} from './listingVariants';
import type { ListingRow } from '../api/publishing';

describe('variant group submission selection', () => {
  const groups = [{ skus: ['red', 'blue'] }];
  it('selects and deselects editable siblings together without including another product', () => {
    const valid = ['red', 'blue', 'single'];
    expect(toggleVariantSelection(['single'], 'red', true, valid, groups)).toEqual([
      'single',
      'red',
      'blue',
    ]);
    expect(toggleVariantSelection(valid, 'blue', false, valid, groups)).toEqual(['single']);
  });
  it('excludes an incomplete group, but permits retry beside a successful sibling', () => {
    expect(completeGroupCandidates(['red', 'single'], ['red', 'blue', 'single'], groups)).toEqual([
      'single',
    ]);
    expect(completeGroupCandidates(['blue'], ['blue'], groups)).toEqual(['blue']);
    expect(toggleVariantSelection([], 'blue', true, ['blue'], groups)).toEqual(['blue']);
  });
});

describe('manual variant review in one product table', () => {
  const rows = () =>
    [
      {
        platform_sku: 'red',
        visible: { color: 'Red', finish: 'Matte' },
        orderable: { price: 20 },
        identification: { value: 'one' },
      },
      {
        platform_sku: 'blue',
        visible: { color: 'Blue', finish: 'Glossy' },
        orderable: { price: 30 },
        identification: { value: 'two' },
      },
      {
        platform_sku: 'other',
        visible: { color: 'Green' },
        orderable: { price: 40 },
        identification: { value: 'three' },
      },
    ] as unknown as ListingRow[];
  it('edits two siblings independently and marks the family for manual review', () => {
    const data = rows();
    expect(
      reviewVariantValue(data, ['red', 'blue'], ['red', 'blue', 'other'], 'red', 'color', 'Brown'),
    ).toBe(true);
    expect(
      reviewVariantValue(
        data,
        ['red', 'blue'],
        ['red', 'blue', 'other'],
        'blue',
        'finish',
        'Natural',
      ),
    ).toBe(true);
    expect(data[0].visible.color).toBe('Brown');
    expect(data[1].visible.finish).toBe('Natural');
    expect(data[0].variant_reviewed_fields).toEqual(['color']);
    expect(data[1].variant_reviewed_fields).toEqual(['finish']);
    expect(data.slice(0, 2).every((r) => r.variant_manual)).toBe(true);
    expect(data[2].variant_manual).toBeUndefined();
    expect(data.map((r) => r.orderable.price)).toEqual([20, 30, 40]);
    expect(data.map((r) => r.identification.value)).toEqual(['one', 'two', 'three']);
  });
  it('allows deliberate blank edits, but blocks submitted or unrelated rows', () => {
    const data = rows();
    expect(reviewVariantValue(data, ['red', 'blue'], ['blue'], 'red', 'color', 'Changed')).toBe(
      false,
    );
    expect(
      reviewVariantValue(data, ['red', 'blue'], ['blue', 'other'], 'other', 'color', 'Changed'),
    ).toBe(false);
    expect(reviewVariantValue(data, ['red', 'blue'], ['blue'], 'blue', 'color', '')).toBe(true);
    expect(data[1].visible.color).toBeUndefined();
    expect(data[0].variant_manual).toBeUndefined();
  });
  it('shows all selected axes and supported common attributes without duplicates', () => {
    expect(
      variantEditorColumns(['finish'], {
        color: { type: 'string' },
        finish: { type: 'string' },
        size: { type: 'string' },
      }),
    ).toEqual(['finish', 'color', 'size']);
    expect(variantEditorColumns(['old'], {})).toEqual(['old']);
  });
});
