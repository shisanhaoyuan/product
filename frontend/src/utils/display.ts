import type { EvidenceStatus } from '../api/types';

export const evidenceLabels = {
  persisted_readback: '独立回读',
  editor_snapshot: '编辑快照',
  source_only: '来源资料',
};
export const text = (value: unknown): string =>
  typeof value === 'object' && value !== null
    ? JSON.stringify(value, null, 2)
    : String(value ?? '');
export function plain(value: unknown): string {
  const doc = new DOMParser().parseFromString(String(value ?? ''), 'text/html');
  doc.querySelectorAll('script,style,iframe,object').forEach((node) => node.remove());
  doc
    .querySelectorAll('p,div,li,br,h1,h2,h3,h4')
    .forEach((node) => node.appendChild(doc.createTextNode('\n')));
  return (doc.body.textContent || '').trim();
}
export function safeUrl(value: unknown): string {
  try {
    const url = new URL(String(value ?? ''));
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : '';
  } catch {
    return '';
  }
}
export function money(value: string | number | null | undefined): string {
  const number = monetaryNumber(value);
  return number !== null
    ? '$' + number.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '—';
}
function monetaryNumber(value: string | number | null | undefined): number | null {
  if (value == null || (typeof value === 'string' && !value.trim())) return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}
export function priceRange(values: (string | number | null)[]): string {
  const numbers = values.map(monetaryNumber).filter((value): value is number => value !== null);
  if (!numbers.length) return '—';
  const min = Math.min(...numbers),
    max = Math.max(...numbers);
  return min === max ? money(min) : `${money(min)} – ${money(max)}`;
}
export function productStatus(product: EvidenceStatus) {
  return product.fill_complete
    ? { label: '已核验完整', type: 'good' }
    : product.evidence_kind === 'persisted_readback'
      ? { label: '待复核', type: 'warn' }
      : { label: '保存待确认', type: 'bad' };
}
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
