import { request } from './client';
import type { Attempt, ListingResult } from './publishing';
export interface ReviewRow {
  attempt_id: string;
  batch_id: string;
  store_id: number;
  store_name: string;
  partner_id: string;
  feed_id: string;
  platform_sku: string;
  source_sku: string;
  listing_id: string;
  title: string;
  image_url?: string;
  brand: string;
  status: string;
  status_label: string;
  message: string;
  errors: ListingResult['errors'];
  stock_message?: string;
  stock_verified: boolean;
  published_status: string;
  created_at: string;
  updated_at: string;
  is_latest: boolean;
  can_refresh: boolean;
  binding_matches: boolean;
  polling_state: string | null;
  next_at: number | null;
  last_error: string | null;
}
export interface ReviewPage {
  items: ReviewRow[];
  total: number;
  page: number;
  page_size: number;
  counts: Record<string, number>;
  stores: { id: number; name: string }[];
}
export const reviewApi = {
  list: (params: URLSearchParams, signal?: AbortSignal) =>
    request<ReviewPage>(`/api/walmart-review?${params}`, { signal }),
  refresh: (aid: string) =>
    request<Attempt>(`/api/walmart-review/attempts/${encodeURIComponent(aid)}/refresh`, {
      method: 'POST',
    }),
};
