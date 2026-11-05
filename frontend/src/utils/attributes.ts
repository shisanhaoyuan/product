export interface AttributeRow {
  label: string;
  value: string;
}

// Parsing is a display-only projection. Never replace the stored source value.
export function structuredValue(value: unknown): unknown {
  if (typeof value !== 'string' || !/^[\s\uFEFF]*[\[{]/.test(value)) return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

export function isStructured(value: unknown): boolean {
  const parsed = structuredValue(value);
  return parsed !== null && typeof parsed === 'object';
}

export function attributeRows(value: unknown, label = '', depth = 0): AttributeRow[] {
  const parsed = depth < 20 ? structuredValue(value) : value;
  if (depth >= 20 || parsed === null || typeof parsed !== 'object') {
    return [
      {
        label,
        value:
          parsed == null
            ? '空'
            : typeof parsed === 'object'
              ? JSON.stringify(parsed)
              : String(parsed).trim() === ''
                ? '空'
                : String(parsed),
      },
    ];
  }
  if (Array.isArray(parsed)) {
    if (!parsed.length) return [{ label, value: '空' }];
    return parsed.flatMap((item, index) =>
      attributeRows(
        item,
        parsed.length === 1 ? label : [label, String(index + 1)].filter(Boolean).join(' · '),
        depth + 1,
      ),
    );
  }
  const data = parsed as Record<string, unknown>;
  const entries = Object.entries(data);
  if (!entries.length) return [{ label, value: '空' }];
  if (entries.length === 1 && 'val' in data) return attributeRows(data.val, label, depth + 1);
  if (
    'Measure' in data &&
    'Unit' in data &&
    entries.every(([key]) => ['Measure', 'Unit'].includes(key)) &&
    [data.Measure, data.Unit].every(
      (item) => item == null || ['string', 'number'].includes(typeof item),
    )
  ) {
    return [
      {
        label,
        value:
          [data.Measure, data.Unit].filter((item) => item != null && item !== '').join(' ') || '空',
      },
    ];
  }
  const consumed = new Set<string>();
  return entries.flatMap(([key, item]) => {
    if (consumed.has(key)) return [];
    const name = key.endsWith('::measure')
      ? key.slice(0, -9)
      : key.endsWith('::unit')
        ? key.slice(0, -6)
        : '';
    if (
      name &&
      `${name}::measure` in data &&
      `${name}::unit` in data &&
      [data[`${name}::measure`], data[`${name}::unit`]].every(
        (part) => part == null || ['string', 'number'].includes(typeof part),
      )
    ) {
      consumed.add(`${name}::measure`);
      consumed.add(`${name}::unit`);
      return attributeRows(
        { Measure: data[`${name}::measure`], Unit: data[`${name}::unit`] },
        [label, name].filter(Boolean).join(' / '),
        depth + 1,
      );
    }
    return attributeRows(item, [label, key].filter(Boolean).join(' / '), depth + 1);
  });
}
