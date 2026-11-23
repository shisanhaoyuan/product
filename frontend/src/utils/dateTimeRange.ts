export function dateTimeRangeQuery(start: string, end: string) {
  const lower = start ? new Date(start) : null;
  const upper = end ? new Date(end) : null;
  if ((lower && !Number.isFinite(lower.getTime())) || (upper && !Number.isFinite(upper.getTime())))
    throw new Error('请选择有效的日期和时间');
  if (lower && upper && lower > upper) throw new Error('开始时间不能晚于结束时间');
  // The picker has second precision; include the whole selected ending second.
  upper?.setMilliseconds(999);
  return {
    ...(lower ? { created_from: lower.toISOString() } : {}),
    ...(upper ? { created_to: upper.toISOString() } : {}),
  };
}

export function dateTimeRangeLabel(start: string, end: string) {
  const show = (value: string) => value.replace('T', ' ');
  if (start && end) return `创建时间：${show(start)} ～ ${show(end)}`;
  if (start) return `创建时间 ≥ ${show(start)}`;
  if (end) return `创建时间 ≤ ${show(end)}`;
  return '创建时间：不限';
}
