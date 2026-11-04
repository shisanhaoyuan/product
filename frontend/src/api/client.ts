import type { ImportRecord, ImportResult, ProductDetail, ProductPage, Stats } from './types';
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
  stats: () => request<Stats>('/api/stats'),
  products: (query: string, signal?: AbortSignal) =>
    request<ProductPage>(`/api/products?${query}`, { signal }),
  product: (id: string, signal?: AbortSignal) =>
    request<ProductDetail>(`/api/products/${encodeURIComponent(id)}`, { signal }),
  imports: (signal?: AbortSignal) => request<ImportRecord[]>('/api/imports', { signal }),
  importJson: (body: string, token: string) =>
    request<ImportResult>('/api/imports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Import-Token': token },
      body,
    }),
};
