import { request } from './client';
import type { ProductType, ShipNode } from './publishing';
import type { ListingDocument } from './publishing';
export interface ExemptionSetting {
  brand: string;
  product_type: string;
  category: string;
  submission_id: string;
}
export interface PreparationSettings {
  revision: number;
  fulfillment_center_id: string;
  exemptions: ExemptionSetting[];
}
export interface RealIdentifier {
  kind: 'EAN' | 'UPC' | 'GTIN';
  value: string;
  brand: string;
  proof: string;
  source_system: string;
  source_sku: string;
  created_at: string;
}
export type IdentifierInput = Omit<RealIdentifier, 'kind' | 'created_at'> & {
  type: RealIdentifier['kind'];
};
export interface IdentifierImportReport {
  filename: string;
  total: number;
  valid: number;
  duplicate: number;
  invalid: number;
  inserted?: number;
  existing?: number;
  at?: string;
  sample: string[];
  errors: { sheet: string; row: number; message: string }[];
}
export interface IdentifierPage {
  items: RealIdentifier[];
  total: number;
  page: number;
  page_size: number;
  stats: { total: number; available: number; assigned: number };
  imports: IdentifierImportReport[];
}
const body = (method: string, value: unknown = {}) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(value),
});
const path = (sid: number) => `/api/walmart/accounts/${sid}/preparation`;
export const preparationApi = {
  allocate: (bid: string, revision: number) =>
    request<{ document: ListingDocument; assigned: number; missing: string[]; skipped: string[] }>(
      `/api/prelisting/batches/${bid}/walmart/identifiers/allocate`,
      body('POST', { revision }),
    ),
  read: (sid: number) => request<PreparationSettings>(path(sid)),
  options: (sid: number) =>
    request<{ nodes: ShipNode[]; types: ProductType[] }>(path(sid) + '/options', body('POST')),
  save: (sid: number, settings: PreparationSettings) =>
    request<PreparationSettings>(path(sid), body('PUT', settings)),
  codes: (q = '', status = '', page = 1) =>
    request<IdentifierPage>(
      '/api/walmart/identifiers?' + new URLSearchParams({ q, status, page: String(page) }),
    ),
  import: (items: IdentifierInput[]) =>
    request<{ imported: number }>('/api/walmart/identifiers', body('POST', { items })),
  spreadsheet: (file: File, brand = '', proof = '', preview = true) =>
    request<IdentifierImportReport>(
      '/api/walmart/identifiers/spreadsheet?' +
        new URLSearchParams({ filename: file.name, brand, proof, preview: String(preview) }),
      { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file },
    ),
};
