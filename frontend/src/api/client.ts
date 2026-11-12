import type {
  SourceCapture,
  ImportRecord,
  ImportContext,
  ImportResult,
  ProductDetail,
  ProductPage,
  Stats,
  SellerBlock,
  SellerBlockPage,
} from './types';
import { clearSession, csrfToken, useAuth } from '../composables/useAuth';

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let config = options;
  if (options?.method && !['GET', 'HEAD'].includes(options.method) && csrfToken()) {
    const headers = new Headers(options.headers);
    headers.set('X-CSRF-Token', csrfToken());
    config = { ...options, headers };
  }
  const response = await fetch(path, config);
  if (!response.ok) {
    if (
      response.status === 401 &&
      path !== '/api/auth/login' &&
      typeof window !== 'undefined' &&
      useAuth().auth.enabled
    ) {
      await useAuth()
        .refreshAuth()
        .catch(() => {});
      if (!useAuth().auth.user) {
        clearSession();
        // A full reload also clears data cached by a previous user.
        window.location.replace('/#/login');
        window.location.reload();
      }
    }
    const raw = await response.text();
    let message = raw;
    try {
      const data = JSON.parse(raw);
      message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* The server may return a plain-text error. */
    }
    throw new ApiError(message || `请求失败 ${response.status}`, response.status);
  }
  return response.json() as Promise<T>;
}

export const catalogApi = {
  sourceCapture: (id: string, sku?: string, signal?: AbortSignal) =>
    request<SourceCapture | null>(
      `/api/products/${encodeURIComponent(id)}/source-capture${sku ? '?variant_sku=' + encodeURIComponent(sku) : ''}`,
      { signal },
    ),
  captureSource: (id: string, sku?: string, signal?: AbortSignal) =>
    request<SourceCapture>(`/api/products/${encodeURIComponent(id)}/source-capture`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant_sku: sku }),
      signal,
    }),
  browserCaptureSource: (
    id: string,
    sku: string | undefined,
    page: { source_url: string; html: string },
    signal?: AbortSignal,
  ) =>
    request<SourceCapture>(`/api/products/${encodeURIComponent(id)}/source-capture/browser`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant_sku: sku, source_url: page.source_url, html: page.html }),
      signal,
    }),
  browserCaptureImages: (
    id: string,
    sku: string | undefined,
    page: { source_url: string; html: string },
  ) =>
    request<{ asin: string; source_url: string; fetched_at: string; images: string[] }>(
      `/api/products/${encodeURIComponent(id)}/images/capture/browser`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ variant_sku: sku, source_url: page.source_url, html: page.html }),
      },
    ),
  uploadImage: (id: string, sku: string | undefined, file: File) =>
    request<{ url: string }>(
      `/api/products/${encodeURIComponent(id)}/images/upload${sku === undefined ? '' : '?variant_sku=' + encodeURIComponent(sku)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
        body: file,
      },
    ),
  captureImages: (id: string, sku?: string) =>
    request<{
      status?: 'verification_required';
      asin: string;
      source_url: string;
      fetched_at?: string;
      images?: string[];
    }>(`/api/products/${encodeURIComponent(id)}/images/capture`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant_sku: sku }),
    }),
  editProduct: (
    id: string,
    expected_revision: string,
    target: { field: string; variant_sku?: string; key?: string },
    value: unknown,
  ) =>
    request<ProductDetail>(`/api/products/${encodeURIComponent(id)}/edits`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_revision, ...target, value }),
    }),
  resetProduct: (id: string, expected_revision: string) =>
    request<ProductDetail>(`/api/products/${encodeURIComponent(id)}/edits/reset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_revision }),
    }),
  blacklist: (query: string, signal?: AbortSignal) =>
    request<SellerBlockPage>(`/api/seller-blacklist?${query}`, { signal }),
  blockSeller: (data: { source: string; seller: string; reason: string }) =>
    request<SellerBlock>('/api/seller-blacklist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  updateSellerBlock: (id: number, data: { reason: string; active: boolean }) =>
    request<SellerBlock>(`/api/seller-blacklist/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  stats: () => request<Stats>('/api/stats'),
  products: (query: string, signal?: AbortSignal) =>
    request<ProductPage>(`/api/products?${query}`, { signal }),
  product: (id: string, signal?: AbortSignal) =>
    request<ProductDetail>(`/api/products/${encodeURIComponent(id)}`, { signal }),
  imports: (signal?: AbortSignal) => request<ImportRecord[]>('/api/imports', { signal }),
  importJson: (body: string, token: string, context?: ImportContext) =>
    request<ImportResult>('/api/imports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Import-Token': token },
      body: context ? JSON.stringify({ ...JSON.parse(body), import_context: context }) : body,
    }),
};
