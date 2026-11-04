import { request } from './client';
import type {
  ListingStore,
  PlanInput,
  PrelistingPreview,
  PrelistingBatch,
  BatchPage,
} from './types';
const json = (method: string, data: unknown) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data),
});
export const prelistingApi = {
  stores: () => request<ListingStore[]>('/api/listing-stores'),
  addStore: (name: string, marketplace_name: string) =>
    request<ListingStore>('/api/listing-stores', json('POST', { name, marketplace_name })),
  setStoreActive: (id: number, active: boolean) =>
    request(`/api/listing-stores/${id}`, json('PATCH', { active })),
  preview: (input: PlanInput, signal?: AbortSignal) =>
    request<PrelistingPreview>('/api/prelisting/preview', { ...json('POST', input), signal }),
  create: (input: PlanInput, preview_hash: string, request_key: string) =>
    request<PrelistingBatch>(
      '/api/prelisting/batches',
      json('POST', { ...input, preview_hash, request_key }),
    ),
  batches: (page = 1, signal?: AbortSignal) =>
    request<BatchPage>(`/api/prelisting/batches?page=${page}`, { signal }),
  batch: (id: string, signal?: AbortSignal) =>
    request<PrelistingBatch>(`/api/prelisting/batches/${encodeURIComponent(id)}`, { signal }),
  action: (id: string, action: 'confirm' | 'cancel') =>
    request<PrelistingBatch>(
      `/api/prelisting/batches/${encodeURIComponent(id)}/action`,
      json('POST', { action }),
    ),
};
