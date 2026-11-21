import type { ListingVariantGroup } from '../api/publishing';

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
