import { describe, expect, it } from 'vitest';
import { reviewIssues } from './reviewIssues';
import type { ProductDetail } from '../api/types';
const product = (issues: Record<string, unknown>[]) =>
  ({
    issues,
    fill_complete: false,
    variants: [
      { sku: 'child-b', asin: 'B000000002' },
      { sku: 'child-a', asin: 'B000000001' },
    ],
  }) as ProductDetail;
describe('Chinese review reasons and identity targets', () => {
  it('identifies seller aliases in Chinese and locates the exact child seller', () => {
    const issue = reviewIssues(product([{ field: 'seller_name', sku: 'child-a' }]))[0];
    expect(issue).toMatchObject({
      title: '卖家待核对',
      field: 'seller',
      sku: 'child-a',
      section: 'variants',
    });
  });
  it('routes aggregate parent price/stock checks to actual content, not attribute search', () => {
    for (const issue of [
      { section: 'fields', key: 'parent_price_stock' },
      { section: 'parent_price_stock' },
    ]) {
      const p = product([issue]);
      expect(reviewIssues(p)[0]).toMatchObject({
        title: '主商品价格与库存待核对',
        section: 'variants',
        field: '',
      });
      p.variants = [];
      expect(reviewIssues(p)[0]).toMatchObject({ section: 'identity', field: 'price' });
    }
  });
  it('uses exact SKU regardless of row order and translates price codes', () => {
    const rows = reviewIssues(
      product([
        { section: 'pricing_checks', sku: 'child-a', reason: '报价异常: multiple_original_prices' },
      ]),
    );
    expect(rows[0]?.message).toContain('多个原价');
    expect(rows[0]?.field).toBe('price');
    expect(rows[0]?.sku).toBe('child-a');
  });
  it('does not select an unrelated variant for an unknown or ambiguous identity', () => {
    const rows = reviewIssues(
      product([{ section: 'rows', key: 'missing', reason: 'persisted_value_mismatch' }]),
    );
    expect(rows[0]?.sku).toBe('');
    expect(rows[0]?.section).toBe('variants');
    expect(rows[0]?.message).toContain('未找到');
  });
  it('shows expected values and maps required units and MSRP', () => {
    const rows = reviewIssues(
      product([
        { section: 'required', field_id: 'Maximum Load Weight::unit', errors: ['请选择'] },
        {
          section: 'fields',
          key: 'MSRP',
          actual: '10',
          expected: '20',
          reason: 'persisted_value_mismatch',
        },
      ]),
    );
    expect(rows[0]?.title).toContain('最大承重单位');
    expect(rows[1]?.field).toBe('msrp');
    expect(rows[1]?.message).toContain('核验值：20');
  });
  it('expands only faulty copy cells with their own SKU', () => {
    const rows = reviewIssues(
      product([
        {
          section: 'copy',
          details: {
            cells: [
              { sku: 'child-a', field: 'title', errors: [] },
              { sku: 'child-b', field: 'description', errors: ['old_brand_residue'] },
            ],
          },
        },
      ]),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]?.sku).toBe('child-b');
    expect(rows[0]?.field).toBe('copy.description');
    expect(rows[0]?.message).toContain('旧品牌');
  });
  it('retains unknown diagnostic data and supplies readable fallback', () => {
    const raw = { section: 'unknown', reason: 'unmapped_error_132' };
    expect(reviewIssues(product([raw]))[0]?.message).toContain('人工核对');
    expect(reviewIssues(product([raw]))[0]?.raw).toEqual(raw);
  });
});
