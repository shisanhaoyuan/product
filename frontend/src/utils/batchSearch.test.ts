import { describe, expect, it } from 'vitest';
import { looksLikeBatchIdentifiers, parseBatchIdentifiers, productSearch } from './batchSearch';

describe('batch SKU / ASIN input', () => {
  it('recognizes identifier lists without hijacking normal title or seller paste', () => {
    expect(looksLikeBatchIdentifiers('B012345678\nMY21ABCDEF')).toBe(true);
    expect(looksLikeBatchIdentifiers('W123456 SKU-2026')).toBe(true);
    expect(looksLikeBatchIdentifiers('B012345678\nB012345678')).toBe(true);
    expect(looksLikeBatchIdentifiers('Outdoor chair, black')).toBe(false);
    expect(looksLikeBatchIdentifiers('Seller Store')).toBe(false);
    expect(looksLikeBatchIdentifiers('B012345678')).toBe(false);
  });
  it('accepts pasted lines and common separators while deduplicating case-insensitively', () => {
    expect(parseBatchIdentifiers('B012345678\r\nSKU-A，sku-a\tSKU-B;SKU-C；SKU-D、SKU-E')).toEqual({
      identifiers: ['B012345678', 'SKU-A', 'SKU-B', 'SKU-C', 'SKU-D', 'SKU-E'],
      duplicates: 1,
      error: '',
    });
    expect(parseBatchIdentifiers(' \n,；')).toEqual({ identifiers: [], duplicates: 0, error: '' });
  });
  it('uses the same input for exact batches, normal phrases and clearing', () => {
    expect(productSearch('SKU-A, sku-b;SKU-A')).toEqual({
      q: '',
      identifiers: 'SKU-A\nsku-b',
      error: '',
    });
    expect(productSearch('ALPHA\nBETA')).toEqual({ q: '', identifiers: 'ALPHA\nBETA', error: '' });
    expect(productSearch('ALPHA, BETA')).toEqual({ q: '', identifiers: 'ALPHA\nBETA', error: '' });
    expect(productSearch('W123456 B012345678')).toEqual({
      q: '',
      identifiers: 'W123456\nB012345678',
      error: '',
    });
    expect(productSearch(' Outdoor chair, black ')).toEqual({
      q: 'Outdoor chair, black',
      identifiers: '',
      error: '',
    });
    expect(productSearch('Seller Store')).toEqual({
      q: 'Seller Store',
      identifiers: '',
      error: '',
    });
    expect(productSearch('')).toEqual({ q: '', identifiers: '', error: '' });
    expect(
      productSearch(Array.from({ length: 101 }, (_, i) => `SKU-${i}`).join(', ')).error,
    ).not.toBe('');
  });
  it('validates limits after deduplication without silently truncating', () => {
    expect(parseBatchIdentifiers(Array(101).fill('SAME').join('\n')).error).toBe('');
    expect(
      parseBatchIdentifiers(Array.from({ length: 101 }, (_, i) => `SKU-${i}`).join('\n')).error,
    ).not.toBe('');
    expect(parseBatchIdentifiers('x'.repeat(201)).error).not.toBe('');
    expect(parseBatchIdentifiers('x\n'.repeat(3001)).error).not.toBe('');
  });
});
