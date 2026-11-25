import type {
  ImageCollection,
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
    const gatewayMessage: Record<number, string> = {
      502: '网关暂时无法取得服务响应（502），本次处理结果未知，请稍后查看结果。',
      503: '服务暂时不可用（503），请稍后查看结果，避免连续重试。',
      504: '服务器等待处理结果超时（504），后台任务可能仍在运行，请稍后查看结果，避免连续重试。',
    };
    let message = gatewayMessage[response.status] || `请求失败（HTTP ${response.status}）`;
    try {
      const data = JSON.parse(raw);
      if (typeof data.detail === 'string' && data.detail.trim()) message = data.detail;
      else if (data.detail != null) message = JSON.stringify(data.detail);
    } catch {
      // Reverse proxies may return whole HTML error pages; never show their markup.
      if (!gatewayMessage[response.status] && raw.length <= 1200 && !/<[^>]+>/.test(raw)) {
        message = raw.trim() || message;
      }
    }
    throw new ApiError(message || `请求失败 ${response.status}`, response.status);
  }
  return response.json() as Promise<T>;
}

export const catalogApi = {
  syncSource: (id: string, sku?: string, signal?: AbortSignal) =>
    request<SourceCapture>(`/api/products/${encodeURIComponent(id)}/source-capture/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant_sku: sku }),
      signal,
    }),
  sourceCapture: (id: string, sku?: string, signal?: AbortSignal) =>
    request<SourceCapture | null>(
      `/api/products/${encodeURIComponent(id)}/source-capture${sku ? '?variant_sku=' + encodeURIComponent(sku) : ''}`,
      { signal },
    ),
  captureSource: (id: string, sku?: string, signal?: AbortSignal) =>
    request<SourceCapture>(`/api/products/${encodeURIComponent(id)}/source-capture`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant_sku: sku, background: true }),
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
    request<{
      asin: string;
      source_url: string;
      fetched_at: string;
      images: string[];
      image_collection?: ImageCollection;
    }>(`/api/products/${encodeURIComponent(id)}/images/capture/browser`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant_sku: sku, source_url: page.source_url, html: page.html }),
    }),
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
      status?: 'verification_required' | 'browser_required';
      reason?: string;
      asin: string;
      source_url: string;
      fetched_at?: string;
      images?: string[];
      image_collection?: ImageCollection;
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
  reviewProduct: (id: string, expected_revision: string, complete: boolean, note: string) =>
    request<ProductDetail>(`/api/products/${encodeURIComponent(id)}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_revision, complete, note }),
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
  stats: (source = '') => request<Stats>('/api/stats?' + new URLSearchParams({ source })),
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
