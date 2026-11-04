import { describe, expect, it } from 'vitest';
import { money, priceRange, productStatus, safeUrl } from './display';

describe('source value display', () => {
  it('distinguishes missing or malformed prices from a real zero', () => {
    for (const value of [undefined, null, '', '  ', 'unknown', Infinity, NaN]) {
      expect(money(value)).toBe('—');
    }
    expect(money('0')).toBe('$0.00');
    expect(money('37.99')).toBe('$37.99');
  });
  it('excludes missing prices from a range without recalculating source prices', () => {
    expect(priceRange([null, '', '  ', 'invalid'])).toBe('—');
    expect(priceRange(['84.99', null, '  ', '70.99', '98.99'])).toBe('$70.99 – $98.99');
    expect(priceRange(['0', '0'])).toBe('$0.00');
    expect(priceRange(['37.99', '37.99'])).toBe('$37.99');
  });
  it('rejects executable URLs and embedded credentials', () => {
    for (const value of [
      'javascript:alert(1)',
      'data:text/html,test',
      'file:///tmp/test',
      'https://user:secret@example.com',
      '/relative',
      null,
    ]) {
      expect(safeUrl(value)).toBe('');
    }
    expect(safeUrl('https://www.amazon.com/dp/B012345678')).toBe(
      'https://www.amazon.com/dp/B012345678',
    );
  });
  it('keeps save and completeness statuses independent', () => {
    const snapshot = {
      fill_complete: false,
      save_succeeded: true,
      readback_verified: false,
      evidence_kind: 'editor_snapshot' as const,
    };
    expect(productStatus(snapshot).label).toBe('保存待确认');
    expect(productStatus({ ...snapshot, evidence_kind: 'persisted_readback' }).label).toBe(
      '待复核',
    );
    expect(
      productStatus({ ...snapshot, evidence_kind: 'persisted_readback', fill_complete: true })
        .label,
    ).toBe('已核验完整');
  });
});
