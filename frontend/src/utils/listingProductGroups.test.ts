import { expect, it } from 'vitest';
import type { ListingRow } from '../api/publishing';
import { listingProductGroups } from './listingProductGroups';

const rows = [
  { listing_id: 'cage', platform_sku: 'c1' },
  { listing_id: 'fire', platform_sku: 'f1' },
  { listing_id: 'cage', platform_sku: 'c2' },
  { listing_id: 'fire', platform_sku: 'f2' },
  { listing_id: 'tent', platform_sku: 't1' },
] as ListingRow[];

it('keeps distinct products separate and gathers interleaved siblings', () => {
  const groups = listingProductGroups(rows);
  expect(groups.map((g) => [g.listingId, g.total, g.rows.map((r) => r.platform_sku)])).toEqual([
    ['cage', 2, ['c1', 'c2']], ['fire', 2, ['f1', 'f2']], ['tent', 1, ['t1']],
  ]);
  expect(groups[0]!.rows[0]).toBe(rows[0]);
});

it('retains product numbers and totals when status filtering hides siblings', () => {
  const groups = listingProductGroups(rows, [rows[3]!]);
  expect(groups).toEqual([{ listingId: 'fire', index: 2, total: 2, rows: [rows[3]] }]);
  expect(listingProductGroups(rows, [])).toEqual([]);
});
