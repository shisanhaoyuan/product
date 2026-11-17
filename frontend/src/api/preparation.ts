import { request } from './client';
import type { ProductType, ShipNode } from './publishing';
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
const body = (method: string, value: unknown = {}) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(value),
});
const path = (sid: number) => `/api/walmart/accounts/${sid}/preparation`;
export const preparationApi = {
  read: (sid: number) => request<PreparationSettings>(path(sid)),
  options: (sid: number) =>
    request<{ nodes: ShipNode[]; types: ProductType[] }>(path(sid) + '/options', body('POST')),
  save: (sid: number, settings: PreparationSettings) =>
    request<PreparationSettings>(path(sid), body('PUT', settings)),
  codes: () => request<{ items: RealIdentifier[] }>('/api/walmart/identifiers'),
  import: (items: IdentifierInput[]) =>
    request<{ imported: number }>('/api/walmart/identifiers', body('POST', { items })),
};
