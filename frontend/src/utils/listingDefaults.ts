import type { ListingDocument, ProductType, ShipNode } from '../api/publishing';

const normalized = (value: string) =>
  value.normalize('NFKC').trim().toLowerCase().replace(/&/g, ' and ').replace(/\s+/g, ' ');

// Explicit equivalents; title fallback must resolve to one official option.
const equivalentTypes: string[][] = [
  [
    'Cat Trees',
    'Cat Tree',
    'Cat Trees & Condos',
    'Cat Trees and Condos',
    'Cat Towers',
    'Cat Tree Towers',
    '猫爬架',
    '猫树',
  ],

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
  title = '',
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
  if (matches.length) return matches.length === 1 ? matches[0]!.name : '';
  // Title fallback uses an explicit product family, never arbitrary substring matches.
  const text = normalized(title);
  const context = [...hints, text].join(' ');
  if (
    /\b(?:replacement|cover|covers|accessory|accessories|spare|outdoor|patio)\b|sofa beds?|sofa tables?/i.test(
      context,
    )
  )
    return '';
  const families = equivalentTypes.filter((aliases) =>
    aliases.some((alias) => {
      const value = normalized(alias);
      return (
        value.includes(' ') &&
        (' ' + text.replace(/[^a-z0-9]+/g, ' ') + ' ').includes(' ' + value + ' ')
      );
    }),
  );
  const inferred = types.filter((type) =>
    families.some((family) => family.some((alias) => normalized(alias) === normalized(type.name))),
  );
  return inferred.length === 1 ? inferred[0]!.name : '';
}

export function applyListingDefaults(
  doc: ListingDocument,
  types: ProductType[],
  nodes: ShipNode[],
): boolean {
  let changed = false;
  const editable = (row: ListingDocument['rows'][number]) =>
    ['unsubmitted', 'failed', 'rejected', 'unpublished'].includes(
      doc.results[row.platform_sku]?.status || 'unsubmitted',
    );
  for (const lid of new Set(doc.rows.map((row) => row.listing_id))) {
    const family = doc.rows.filter((row) => row.listing_id === lid);
    const fixed = new Set(
      family
        .filter((row) => !editable(row))
        .map((row) => row.product_type)
        .filter(Boolean),
    );
    if (fixed.size > 1) continue;
    const counts = new Map<string, number>();
    for (const row of family) {
      const type = row.product_type.trim();
      if (type) counts.set(type, (counts.get(type) || 0) + 1);
    }
    const valid = new Set(types.map((type) => type.name));
    if ([...counts.keys()].some((type) => valid.has(type)))
      for (const type of counts.keys()) if (!valid.has(type)) counts.delete(type);
    const maximum = Math.max(0, ...counts.values());
    const preferred = [...family]
      .sort(
        (a, b) =>
          Number(b.visible.isPrimaryVariant === 'Yes') -
          Number(a.visible.isPrimaryVariant === 'Yes'),
      )
      .find((row) => counts.get(row.product_type.trim()) === maximum)
      ?.product_type.trim();
    const item = doc.basis.items.find((item) => item.listing_id === lid);
    const candidates = new Set(
      family
        .map((row) => {
          const variant = item?.variants?.find((v) => v.platform_sku === row.platform_sku);
          return matchProductType(
            item?.category || '',
            types,
            item?.category_hints || [],
            variant?.content?.copy?.title || String(row.visible.productName || ''),
          );
        })
        .filter(Boolean),
    );
    const choices = fixed.size ? fixed : preferred ? new Set([preferred]) : candidates;
    if (choices.size !== 1) continue;
    const type = [...choices][0]!;
    for (const row of family) {
      if (row.product_type !== type && editable(row)) {
        row.product_type = type;
        changed = true;
      }
    }
  }
  for (const row of doc.rows) {
    const status = doc.results[row.platform_sku]?.status;
    if (status && !['failed', 'rejected', 'unpublished'].includes(status)) continue;
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

export function setFamilyProductType(doc: ListingDocument, sku: string, type: string) {
  const current = doc.rows.find((row) => row.platform_sku === sku);
  if (!current) return;
  const family = doc.rows.filter((row) => row.listing_id === current.listing_id);
  const editable = (row: typeof current) =>
    ['unsubmitted', 'failed', 'rejected', 'unpublished'].includes(
      doc.results[row.platform_sku]?.status || 'unsubmitted',
    );
  if (!editable(current)) throw new Error('当前商品已提交，不能修改类目');
  if (family.some((row) => !editable(row) && row.product_type !== type))
    throw new Error('同组已有提交商品，类目必须沿用已提交的商品类型');
  for (const row of family) if (editable(row)) row.product_type = type;
}
