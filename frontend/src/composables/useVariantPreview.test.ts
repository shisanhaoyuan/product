import { effectScope } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { catalogApi } from '../api/client';
import type { ProductDetail, Variant } from '../api/types';
import { useVariantPreview } from './useVariantPreview';

const detail = (sku: string) => ({ variants: [{ sku } as Variant] }) as ProductDetail;
const scopes: ReturnType<typeof effectScope>[] = [];
function preview() {
  const scope = effectScope();
  scopes.push(scope);
  return scope.run(useVariantPreview)!;
}
afterEach(() => {
  scopes.splice(0).forEach((scope) => scope.stop());
  vi.restoreAllMocks();
});

describe('product search variant previews', () => {
  it('loads only on expansion and reuses children after collapse and reopening', async () => {
    const fetch = vi.spyOn(catalogApi, 'product').mockResolvedValue(detail('child'));
    const variants = preview();
    expect(variants.state('parent').expanded).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
    await variants.toggle('parent');
    expect(variants.state('parent').variants?.[0]?.sku).toBe('child');
    await variants.toggle('parent');
    expect(variants.state('parent').expanded).toBe(false);
    await variants.toggle('parent');
    expect(variants.state('parent').expanded).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('keeps products independent even when responses arrive out of order', async () => {
    let resolveFirst!: (value: ProductDetail) => void;
    const fetch = vi.spyOn(catalogApi, 'product').mockImplementation(async (id) => {
      if (id === 'first')
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      return detail('second-child');
    });
    const variants = preview();
    const first = variants.toggle('first');
    await variants.toggle('second');
    expect(variants.state('first').loading).toBe(true);
    expect(variants.state('second').variants?.[0]?.sku).toBe('second-child');
    await variants.toggle('first');
    await variants.toggle('first');
    expect(fetch).toHaveBeenCalledTimes(2);
    resolveFirst(detail('first-child'));
    await first;
    expect(variants.state('first').variants?.[0]?.sku).toBe('first-child');
    expect(variants.state('second').variants?.[0]?.sku).toBe('second-child');
  });

  it('allows retry after failure without closing the panel', async () => {
    vi.spyOn(catalogApi, 'product')
      .mockRejectedValueOnce(new Error('网络错误'))
      .mockResolvedValueOnce(detail('child'));
    const variants = preview();
    await variants.toggle('parent');
    expect(variants.state('parent').error).toBe('网络错误');
    expect(variants.state('parent').expanded).toBe(true);
    await variants.retry('parent');
    expect(variants.state('parent').error).toBe('');
    expect(variants.state('parent').variants?.[0]?.sku).toBe('child');
  });

  it('caches empty results and aborts pending work when leaving the page', async () => {
    let signal: AbortSignal | undefined;
    vi.spyOn(catalogApi, 'product').mockImplementation(async (_id, requestSignal) => {
      signal = requestSignal;
      return { variants: [] } as unknown as ProductDetail;
    });
    const variants = preview();
    await variants.toggle('empty');
    await variants.toggle('empty');
    await variants.toggle('empty');
    expect(catalogApi.product).toHaveBeenCalledTimes(1);
    vi.mocked(catalogApi.product).mockImplementation(async (_id, requestSignal) => {
      signal = requestSignal;
      return new Promise(() => {});
    });
    void variants.toggle('pending');
    expect(signal?.aborted).toBe(false);
    scopes[0]!.stop();
    expect(signal?.aborted).toBe(true);
  });
});
