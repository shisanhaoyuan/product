import type { ListingVariantGroup, ListingRow, JsonSchema } from '../api/publishing';

export function completeGroupCandidates(
  valid: string[],
  editable: string[],
  groups: Pick<ListingVariantGroup, 'skus'>[],
) {
  return valid.filter((sku) => {
    const group = groups.find((g) => g.skus.includes(sku));
    return !group || group.skus.filter((s) => editable.includes(s)).every((s) => valid.includes(s));
  });
}

export function toggleVariantSelection(
  selected: string[],
  sku: string,
  checked: boolean,
  valid: string[],
  groups: Pick<ListingVariantGroup, 'skus'>[],
) {
  const members = (groups.find((g) => g.skus.includes(sku))?.skus || [sku]).filter((s) =>
    valid.includes(s),
  );
  return checked
    ? [...new Set([...selected, ...members])]
    : selected.filter((s) => !members.includes(s));
}

export const reviewedVariantAttributes = [
  'color',
  'finish',
  'size',
  'sizeDescriptor',
  'pattern',
  'shape',
  'material',
  'frameColor',
  'bedSize',
];

export function variantEditorColumns(axes: string[], properties: Record<string, JsonSchema>) {
  return [...new Set([...axes, ...reviewedVariantAttributes.filter((key) => properties[key])])];
}

export function reviewVariantValue(
  rows: ListingRow[],
  skus: string[],
  editable: string[],
  sku: string,
  key: string,
  value: unknown,
) {
  if (!skus.includes(sku) || !editable.includes(sku)) return false;
  const row = rows.find((r) => r.platform_sku === sku);
  if (!row) return false;
  for (const member of rows.filter(
    (r) => skus.includes(r.platform_sku) && editable.includes(r.platform_sku),
  ))
    member.variant_manual = true;
  row.variant_reviewed_fields = [...new Set([...(row.variant_reviewed_fields || []), key])];
  if (value === undefined || value === '') delete row.visible[key];
  else row.visible[key] = value;
  return true;
}
