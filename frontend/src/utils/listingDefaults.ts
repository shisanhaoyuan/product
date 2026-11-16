import type { ListingDocument, ProductType, ShipNode } from '../api/publishing';

const normalized = (value: string) =>
  value.normalize('NFKC').trim().toLowerCase().replace(/\s+/g, ' ');

// A source category is a hint, never an option to append to Walmart's taxonomy.
export function matchProductType(category: string, types: ProductType[]): string {
  const leaf = category.split(/\s*(?:>|\/|→|\|)\s*/).at(-1) || '';
  const hints = new Set([normalized(category), normalized(leaf)].filter(Boolean));
  const matches = types.filter((type) => hints.has(normalized(type.name)));
  return matches.length === 1 ? matches[0]!.name : '';
}

export function applyListingDefaults(
  doc: ListingDocument,
  types: ProductType[],
  nodes: ShipNode[],
): boolean {
  let changed = false;
  for (const row of doc.rows) {
    const status = doc.results[row.platform_sku]?.status;
    if (status && !['failed', 'rejected', 'unpublished'].includes(status)) continue;
    if (!row.product_type) {
      const item = doc.basis.items.find((item) => item.listing_id === row.listing_id);
      const type = matchProductType(item?.category || '', types);
      if (type) {
        row.product_type = type;
        changed = true;
      }
    }
    if (!row.fulfillment_center_id && nodes.length === 1) {
      row.fulfillment_center_id = nodes[0]!.id;
      changed = true;
    }
    if (
      row.identification.mode === 'identifier' &&
      !row.identification.value.trim() &&
      !row.identification.type
    ) {
      row.identification.type = 'EAN';
      changed = true;
    }
  }
  return changed;
}
