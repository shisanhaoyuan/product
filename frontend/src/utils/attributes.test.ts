import { describe, expect, it } from 'vitest';
import { attributeRows, isStructured } from './attributes';

describe('readable attributes', () => {
  it('decodes ERP val wrappers and embedded measurements without changing the source', () => {
    const source = {
      Depth: { val: ['{"Unit":"in","Measure":"106.30"}'] },
      Brand: { val: ['HPZFEIJJ'] },
    };
    const before = JSON.stringify(source);
    expect(attributeRows(source)).toEqual([
      { label: 'Depth', value: '106.30 in' },
      { label: 'Brand', value: 'HPZFEIJJ' },
    ]);
    expect(JSON.stringify(source)).toBe(before);
  });
  it('pairs actual measure and unit fields, preserving zero and unpaired units', () => {
    expect(
      attributeRows({
        'Depth::unit': 'in (英寸)',
        'Depth::measure': '106.30',
        Stock: 0,
        Active: false,
        'Weight::unit': 'lb',
      }),
    ).toEqual([
      { label: 'Depth', value: '106.30 in (英寸)' },
      { label: 'Stock', value: '0' },
      { label: 'Active', value: 'false' },
      { label: 'Weight::unit', value: 'lb' },
    ]);
  });
  it('preserves extra metadata and array entries instead of dropping facts', () => {
    const rows = attributeRows({
      Choice: { val: ['A', 'B'], note: 'verified' },
      Measure: '3',
      Unit: 'in',
      source: 'page',
    });
    expect(rows).toContainEqual({ label: 'Choice / val · 2', value: 'B' });
    expect(rows).toContainEqual({ label: 'Choice / note', value: 'verified' });
    expect(rows).toContainEqual({ label: 'source', value: 'page' });
  });
  it('keeps malformed JSON as text, supports JSON strings, and displays empty values', () => {
    expect(isStructured('{bad')).toBe(false);
    expect(attributeRows('{bad')).toEqual([{ label: '', value: '{bad' }]);
    expect(isStructured('{"a":{"val":["1"]}}')).toBe(true);
    expect(attributeRows('{"a":{"val":["1"]}}')).toEqual([{ label: 'a', value: '1' }]);
    for (const value of [null, '', [], {}])
      expect(attributeRows(value)).toEqual([{ label: '', value: '空' }]);
  });
});
