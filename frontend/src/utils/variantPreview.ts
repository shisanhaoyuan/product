import { groupedAttributes } from './attributeLayout';
import type { AttributeRow } from './attributes';

// Keep the stored attributes intact; show decoded values in the search preview.
export function previewVariantAttributes(attributes: unknown): AttributeRow[] {
  const values = new Map<string, Set<string>>();
  for (const group of groupedAttributes(attributes)) {
    if (group.id === 'internal') continue;
    for (const row of group.rows) {
      if (row.empty || row.label.split(/ · | \/ /).some((key) => /^(tmp_|__)/i.test(key))) continue;
      const label = row.title.replace(/ · \d+(?= \/ |$)/g, '');
      if (!values.has(label)) values.set(label, new Set());
      values.get(label)!.add(row.value);
    }
  }
  return [...values].map(([label, items]) => ({ label, value: [...items].join(' / ') }));
}
