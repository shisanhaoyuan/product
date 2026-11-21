import { describe, expect, it } from 'vitest';
import { money, plain, priceRange, productStatus, safeUrl } from './display';

describe('source value display', () => {
  it('extracts source text without a browser DOM and preserves entities and line breaks', () => {
    expect(
      plain(
        '<div style="color:red"><p>商品 &amp; 参数</p><ul><li>宽度 30</li><li>高度 60</li></ul></div>',
      ),
    ).toBe('商品 & 参数\n宽度 30\n高度 60');
    expect(plain('<p>A <strong>metal</strong> cabinet<br>71&#34; &lt; 80</p>')).toBe(
      'A metal cabinet\n71" < 80',
    );
    expect(plain('2 < 3 &amp; 4 > 1')).toBe('2 < 3 & 4 > 1');
    expect(plain(null)).toBe('');
  });
  it('excludes nested hidden content, executable markup and resource attributes', () => {
    expect(
      plain(
        '<STYLE>body{color:red}</STYLE><script>window.bad=true</script><template><div>hidden<template>nested</template></div></template><p title="a > b">Visible<img src="https://example.invalid/a" onerror="alert(1)"></p><iframe>hidden</iframe><object>hidden</object>',
      ),
    ).toBe('Visible');
    expect(plain('<table><tr><td>Width</td><td>30 in</td></tr></table>')).toBe('Width 30 in');
    expect(plain('&lt;style&gt;plain text&lt;/style&gt;')).toBe('<style>plain text</style>');
  });
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
