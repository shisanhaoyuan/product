import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, catalogApi, request } from './client';

afterEach(() => vi.unstubAllGlobals());
describe('catalog API boundary', () => {
  it('preserves SKU-specific source values and missing values exactly', async () => {
    const body = {
      variants: [
        { sku: 'B-SKU', price: '84.99', stock: '0' },
        { sku: 'A-SKU', price: null, stock: null },
      ],
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))));
    expect(await request('/api/products/id')).toEqual(body);
  });
  it('retains HTTP status and structured validation details', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ detail: [{ loc: ['variants', 1, 'sku'], msg: 'duplicate SKU' }] }),
            { status: 422 },
          ),
        ),
    );
    await expect(request('/api/imports')).rejects.toMatchObject({
      status: 422,
      message: '[{"loc":["variants",1,"sku"],"msg":"duplicate SKU"}]',
    });
  });
  it('shows a plain-text server failure without claiming success', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(new Response('需要正确的 X-Import-Token', { status: 401 })),
        ),
    );
    await expect(request('/api/imports')).rejects.toBeInstanceOf(ApiError);
    await expect(request('/api/imports')).rejects.toMatchObject({
      status: 401,
      message: '需要正确的 X-Import-Token',
    });
  });
  it('forwards cancellation and does not retry an aborted product request', async () => {
    const abort = new DOMException('Aborted', 'AbortError');
    const fetch = vi.fn().mockRejectedValue(abort);
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    controller.abort();
    await expect(catalogApi.product('store/template', controller.signal)).rejects.toBe(abort);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/products/store%2Ftemplate', {
      signal: controller.signal,
    });
  });
  it('passes the import token and body only in the write request, with no automatic retry', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('Conflict', { status: 409 }));
    vi.stubGlobal('fetch', fetch);
    await expect(catalogApi.importJson('{"listings":[]}', 'test-token')).rejects.toMatchObject({
      status: 409,
    });
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/imports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Import-Token': 'test-token' },
      body: '{"listings":[]}',
    });
  });
});
