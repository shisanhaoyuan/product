export function selectedValues(value: string): string[] {
  if (!value) return [];
  if (!value.startsWith('[')) return [value];
  try {
    const result: unknown = JSON.parse(value);
    return Array.isArray(result)
      ? [...new Set(result.filter((v): v is string => typeof v === 'string' && !!v))]
      : [];
  } catch {
    return [];
  }
}

export function toggleValue(current: string, value: string): string {
  if (!value) return '';
  const selected = selectedValues(current);
  const next = selected.includes(value)
    ? selected.filter((item) => item !== value)
    : [...selected, value].slice(0, 50);
  return next.length ? JSON.stringify(next) : '';
}

export function filterGroups(value: string): Record<string, string[]> {
  try {
    const parsed: unknown = JSON.parse(value || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(
        (entry): entry is [string, string[]] =>
          Array.isArray(entry[1]) && entry[1].every((v: unknown) => typeof v === 'string' && !!v),
      ),
    );
  } catch {
    return {};
  }
}
