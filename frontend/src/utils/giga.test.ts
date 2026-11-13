import { describe, it, expect } from 'vitest';
import { warehouseRange, warehouseEmpty, highRisk, level } from './giga';

describe('GIGA supplier quantities and risk levels', () => {
  it('preserves exact counts and ranges without inventing stock', () => {
    expect(warehouseRange(4, 4)).toBe('4');
    expect(warehouseRange(100, 199)).toBe('100–199');
    expect(warehouseRange(100, null)).toBe('100+');
    expect(warehouseRange(null, 10)).toBe('最多 10');
    expect(warehouseRange(null, null)).toBe('未提供');
    expect(warehouseRange('10', true)).toBe('未提供');
    expect(warehouseRange(10, 4)).toBe('数据异常');
    expect(warehouseEmpty(0, 0)).toBe(true);
    expect(warehouseEmpty(0, 10)).toBe(false);
    expect(warehouseEmpty(0, null)).toBe(false);
    expect(warehouseEmpty(null, null)).toBe(false);
  });
  it('flags only explicit high return levels, preserving unknown and numeric rates', () => {
    expect(highRisk('High')).toBe(true);
    expect(highRisk('高')).toBe(true);
    for (const value of ['Low', 'Moderate', null, 'N/A', '9%']) expect(highRisk(value)).toBe(false);
    expect(level('Moderate')).toBe('中');
    expect(level('N/A')).toBe('未提供');
    expect(level('9%')).toBe('9%');
  });
});
