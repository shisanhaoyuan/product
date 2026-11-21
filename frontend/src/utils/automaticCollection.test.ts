import { afterEach, expect, it, vi } from 'vitest';
import { claimAutomaticCollection } from './automaticCollection';
afterEach(() => vi.unstubAllGlobals());
it('persists an attempt across remounts while treating each product identity separately', async () => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => values.get(k),
    setItem: (k: string, v: string) => values.set(k, v),
  });
  expect(claimAutomaticCollection('product', ['a', 'sku', 'B000000001'])).toBe(true);
  expect(claimAutomaticCollection('product', ['a', 'sku', 'B000000001'])).toBe(false);
  vi.resetModules();
  const fresh = await import('./automaticCollection');
  expect(fresh.claimAutomaticCollection('product', ['a', 'sku', 'B000000001'])).toBe(false);
  expect(fresh.claimAutomaticCollection('product', ['a', 'sku', 'B000000002'])).toBe(true);
});
