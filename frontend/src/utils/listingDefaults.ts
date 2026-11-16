import type { ListingDocument, ProductType, ShipNode } from '../api/publishing';

const normalized = (value: string) =>
  value.normalize('NFKC').trim().toLowerCase().replace(/&/g, ' and ').replace(/\s+/g, ' ');

// Explicit equivalent names only. No substring/fuzzy match: e.g. sofa covers
// and sofa beds must not be silently classified as couches.
const equivalentTypes: string[][] = [
  [
    'Sofas & Couches',
    'Sofas',
    'Sofa',
    'Couches',
    'Couch',
    'Sectional Sofas',
    'Sectionals',
    '沙发',
    '组合沙发',
  ],
];

// A source category is a hint, never an option to append to Walmart's taxonomy.
export function matchProductType(
  category: string,
  types: ProductType[],
  categories: string[] = [],
): string {
  const hints = new Set(
    [category, ...categories]
      .flatMap((value) => {
        const leaf = value.split(/\s*(?:>|\/|→|\||::)\s*/).at(-1) || '';
        return [normalized(value), normalized(leaf)];
      })
      .filter(Boolean),
  );
  const candidates = new Set(hints);
  for (const aliases of equivalentTypes) {
    if (aliases.some((alias) => hints.has(normalized(alias))))
      aliases.forEach((alias) => candidates.add(normalized(alias)));
  }
  const matches = types.filter((type) => candidates.has(normalized(type.name)));
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
      const type = matchProductType(item?.category || '', types, item?.category_hints || []);
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
