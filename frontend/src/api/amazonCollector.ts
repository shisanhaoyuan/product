import { request } from './client';
export interface CollectorItem {
  root_asin: string;
  asin: string;
  state: string;
  message: string;
  needs_review?: boolean;
  missing_fields?: string[];
}
export interface CollectorJob {
  marketplace?: 'US' | 'CA';
  revision?: number;
  execution?: { active: boolean; lease_until: number };
  progress?: {
    total: number;
    processed: number;
    done: number;
    failed: number;
    pending: number;
    needs_review: number;
  };
  id: string;
  created_at: string;
  state: string;
  items: CollectorItem[];
  result: {
    created?: number;
    updated?: number;
    unchanged?: number;
    incomplete?: number;
    listing_ids?: string[];
    stale_roots?: string[];
    failed_roots?: string[];
  };
}
export interface CollectorTask {
  marketplace?: 'US' | 'CA';
  root_asin: string;
  asin: string;
  token: string;
}
export interface SheetPreview {
  asins: string[];
  valid: number;
  duplicate: number;
  invalid: number;
  errors: { sheet: string; row: number; message: string }[];
}
const base = '/api/integrations/amazon-collector';
// A fresh identity per page, never shared through localStorage across devices/tabs.
const runnerId = crypto.randomUUID();
export const collectorApi = {
  status: () => request<{ jobs: CollectorJob[]; wraith_configured: boolean }>(base),
  get: (id: string) => request<CollectorJob>(`${base}/jobs/${id}`),
  create: (
    text: string,
    include_variants: boolean,
    request_id: string,
    marketplace: 'US' | 'CA' = 'US',
  ) =>
    request<CollectorJob>(`${base}/jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, include_variants, request_id, marketplace }),
    }),
  action: <T = CollectorJob>(id: string, action: string, data?: unknown) =>
    request<T>(`${base}/jobs/${id}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Collector-Runner': runnerId },
      body: data ? JSON.stringify(data) : undefined,
    }),
  sheet: (file: File, marketplace: 'US' | 'CA' = 'US') =>
    request<SheetPreview>(
      `${base}/spreadsheet?filename=${encodeURIComponent(file.name)}&marketplace=${marketplace}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      },
    ),
};
