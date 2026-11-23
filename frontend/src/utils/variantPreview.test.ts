import { describe, expect, it } from 'vitest';
import { previewVariantAttributes } from './variantPreview';

describe('readable variant preview attributes', () => {
  it('unwraps imported ERP values and hides empty and internal fields without modifying them', () => {
    const source = {
      Color: { val: ['White'] },
      Finish: { val: ['Matte'] },
      TMP_CHANGE_ATTR_KEY: { val: [''] },
      TMP_OTHER: { val: ['internal value'] },
      listingStageAttrsPlugin: 'internal metadata',
      Unfilled: { val: [''] },
    };
    const original = JSON.stringify(source);
    expect(previewVariantAttributes(source)).toEqual([
      { label: '颜色', value: 'White' },
      { label: '饰面', value: 'Matte' },
    ]);
    expect(JSON.stringify(source)).toBe(original);
  });

  it('supports JSON-encoded attributes, multiple values, and measurements', () => {
    expect(
      previewVariantAttributes(
        JSON.stringify({
          Color: { val: ['White', 'Black', 'White'] },
          Size: { val: ['{"Measure":"42","Unit":"in"}'] },
          Count: 0,
          Adjustable: false,
        }),
      ),
    ).toEqual([
      { label: '颜色', value: 'White / Black' },
      { label: '尺寸', value: '42 in' },
      { label: 'Count', value: '0' },
      { label: 'Adjustable', value: 'false' },
    ]);
  });

  it('returns no rows when only placeholders or empty values were imported', () => {
    expect(previewVariantAttributes({ TMP_CHANGE_ATTR_KEY: { val: [''] }, Color: null })).toEqual(
      [],
    );
  });
});
