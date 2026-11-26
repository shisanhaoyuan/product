import type { ListingRow } from '../api/publishing';

// Use the same parent identity as the backend, never title similarity or category.
export function listingProductGroups(rows: ListingRow[], visibleRows = rows) {
  const visible = new Set(visibleRows.map((row) => row.platform_sku));
  const groups = new Map<string, { listingId: string; index: number; total: number; rows: ListingRow[] }>();
  for (const row of rows) {
    let group = groups.get(row.listing_id);
    if (!group) {
      group = { listingId: row.listing_id, index: groups.size + 1, total: 0, rows: [] };
      groups.set(row.listing_id, group);
    }
    group.total++;
    if (visible.has(row.platform_sku)) group.rows.push(row);
  }
  return [...groups.values()].filter((group) => group.rows.length);
}
