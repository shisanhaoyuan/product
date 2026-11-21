import { request } from './client';
import type { PrelistingPreview, ListingStore } from './types';

export interface JsonSchema {
  type?: string;
  title?: string;
  description?: string;
  comments?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: (string | number | boolean)[];
  items?: JsonSchema;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  minimum?: number;
  maximum?: number;
  multipleOf?: number;
  format?: string;
  [key: string]: unknown;
}
export interface ListingRow {
  platform_sku: string;
  listing_id: string;
  source_sku: string;
  product_type: string;
  fulfillment_center_id: string;
  identification: {
    mode: 'identifier' | 'exemption';
    type: 'GTIN' | 'UPC' | 'EAN' | 'ISBN';
    value: string;
    submission_id: string;
  };
  visible: Record<string, unknown>;
  orderable: Record<string, unknown>;
  extra: Record<string, unknown>;
}
export interface Issue {
  path: string;
  message: string;
  code?: string;
  details?: { path: string; message: string }[];
}
export interface ListingResult {
  status: string;
  message: string;
  stock_message?: string;
  stock_verified?: boolean;
  published_status?: string;
  errors: { code: string; message: string; original: string }[];
  attempt_id?: string;
}
export interface Attempt {
  id: string;
  state: string;
  feed_id: string;
  store_id: number;
  partner_id: string;
  message: string;
  created_at: string;
  updated_at: string;
  skus: string[];
  results: Record<string, ListingResult>;
  polling?: { state: string; next_at: number; tries: number; last_error: string } | null;
}
export interface ListingDocument {
  source_price_reviews?: Record<
    string,
    {
      at: string;
      actor_id: number | null;
      price: number;
      original_price?: string;
      multiplier?: string;
      method?: string;
    }
  >;
  identifier_pool?: { total: number; available: number };
  requirements?: Record<string, { visible?: string[]; orderable?: string[] }>;
  specs?: Record<string, ListingSpec>;
  revision: number;
  version: string;
  rows: ListingRow[];
  labels: Record<string, string>;
  store: ListingStore;
  brand: string;
  basis: PrelistingPreview;
  validation: {
    id?: string;
    revision?: number;
    at?: number;
    partner_id?: string;
    binding_revision?: number;
    valid_count?: number;
    current?: boolean;
    rows?: Record<string, { valid: boolean; errors: Issue[] }>;
  };
  results: Record<string, ListingResult>;
  attempts: Attempt[];
  updated_at: string;
}
export interface ProductType {
  name: string;
  category: string;
  group: string;
  description: string;
}
export interface ShipNode {
  id: string;
  name: string;
  type: string;
}
export interface ListingSpec {
  sections: Record<string, JsonSchema>;
  digest: string;
  labels: Record<string, string>;
}
export interface GenerationJob {
  id: string;
  revision: number;
  batch_id: string;
  state: 'queued' | 'running' | 'complete' | 'failed' | 'interrupted';
  message: string;
  report: {
    model?: string;
    total?: number;
    validated_at?: number;
    rows: Record<
      string,
      {
        state: string;
        preparation_state?: string;
        collection?: { state: string; message?: string; listing_id?: string };
        source_collection?: { state: string; message?: string };
        image_collection?: import('./types').ImageCollection;
        vision?: { state: string; message?: string; warnings?: string[]; images?: string[] };
        rounds?: {
          state: string;
          message?: string;
          changes?: { path: string; before: unknown; after: unknown }[];
          rejected?: Issue[];
        }[];
        issues: Issue[];
        estimated?: number;
        fields: Record<
          string,
          {
            kind: string;
            value?: unknown;
            previous_value?: unknown;
            reason?: string;
            refs?: string[];
          }
        >;
      }
    >;
  };
}
const body = (method: string, data: unknown = {}) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
});
const path = (bid: string) => `/api/prelisting/batches/${encodeURIComponent(bid)}/walmart`;
export const publishingApi = {
  verifySourcePrice: (
    bid: string,
    revision: number,
    platform_sku: string,
    original_price?: string,
  ) =>
    request<ListingDocument>(
      path(bid) + '/verify-source-price',
      body('POST', { revision, platform_sku, ...(original_price ? { original_price } : {}) }),
    ),
  generation: (bid: string) =>
    request<{
      job: GenerationJob | null;
      ai: { configured: boolean; model: string; host: string };
    }>(path(bid) + '/generation'),
  generate: (bid: string) => request<GenerationJob>(path(bid) + '/generation', body('POST')),
  read: (bid: string) => request<ListingDocument | null>(path(bid)),
  prepare: (bid: string) => request<ListingDocument>(path(bid) + '/prepare', body('POST')),
  save: (bid: string, doc: ListingDocument) =>
    request<ListingDocument>(
      path(bid),
      body('PATCH', { revision: doc.revision, version: doc.version, rows: doc.rows }),
    ),
  refreshSource: (bid: string, revision: number) =>
    request<ListingDocument>(path(bid) + '/refresh-source', body('POST', { revision })),
  taxonomy: (bid: string) =>
    request<{ items: ProductType[] }>(path(bid) + '/taxonomy', body('POST')),
  nodes: (bid: string) => request<{ items: ShipNode[] }>(path(bid) + '/nodes', body('POST')),
  spec: (bid: string, row: ListingRow) =>
    request<ListingSpec>(path(bid) + '/spec', body('POST', row)),
  validate: (bid: string, revision: number) =>
    request<ListingDocument>(path(bid) + '/validate', body('POST', { revision })),
  submit: (bid: string, doc: ListingDocument, skus: string[], key: string) =>
    request<Attempt>(
      path(bid) + '/submit',
      body('POST', {
        revision: doc.revision,
        validation_id: doc.validation.id,
        request_key: key,
        skus,
        confirm_store_id: doc.store.id,
        confirm_partner_id: doc.validation.partner_id,
      }),
    ),
  refresh: (bid: string, aid: string) =>
    request<Attempt>(`${path(bid)}/attempts/${encodeURIComponent(aid)}/refresh`, body('POST')),
  reconcile: (bid: string, aid: string, feed_id: string) =>
    request<Attempt>(
      `${path(bid)}/attempts/${encodeURIComponent(aid)}/reconcile`,
      body('POST', { feed_id }),
    ),
};
