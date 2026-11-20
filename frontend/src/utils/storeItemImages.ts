import { request } from '../api/client';

export interface StoreItemImage {
  sku: string;
  image: string;
  image_source: string;
  image_status?: 'loading' | 'ready' | 'missing' | 'unavailable';
}

export async function loadStoreItemImages(
  store: string,
  page: { items: StoreItemImage[]; partner: string; revision: number },
  signal: AbortSignal,
) {
  const queue = page.items.filter((item) => !item.image);
  for (const item of queue) item.image_status = 'loading';
  async function worker() {
    while (!signal.aborted) {
      const item = queue.shift();
      if (!item) return;
      try {
        const result = await request<StoreItemImage>(
          `/api/store-items/${store}/image?` +
            new URLSearchParams({
              sku: item.sku,
              partner: page.partner,
              revision: String(page.revision),
            }),
          { signal },
        );
        if (signal.aborted) return;
        if (result.sku !== item.sku) throw new Error('商品图片不匹配');
        item.image = result.image;
        item.image_source = result.image_source;
        item.image_status = result.image_status;
      } catch {
        if (signal.aborted) return;
        item.image_status = 'unavailable';
      }
    }
  }
  await Promise.all([worker(), worker()]);
}
