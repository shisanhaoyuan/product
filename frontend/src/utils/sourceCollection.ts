import { catalogApi } from '../api/client';
import { captureInBrowser } from './browserCapture';
import type { PrelistingPreview } from '../api/types';
import { errorMessage } from './display';

export async function collectSource(
  listingId: string,
  sku: string | undefined,
  signal: AbortSignal,
  onVerification: () => void = () => {},
) {
  signal.throwIfAborted();
  let result = await catalogApi.captureSource(listingId, sku, signal);
  signal.throwIfAborted();
  if (['verification_required', 'browser_required'].includes(result.status)) {
    onVerification();
    const page = await captureInBrowser(result.asin, signal, 'source');
    signal.throwIfAborted();
    result = await catalogApi.browserCaptureSource(listingId, sku, page, signal);
  }
  signal.throwIfAborted();
  if (['verification_required', 'browser_required'].includes(result.status))
    throw new Error('报价列表尚未展开，请在来源页面展开后重新采集。');
  return result;
}

export async function collectMissingPrices(
  preview: PrelistingPreview,
  signal: AbortSignal,
  progress: (message: string) => void,
) {
  const errors: string[] = [];
  const seen = new Set<string>();
  for (const item of preview.items) {
    if (item.source !== 'Amazon') continue;
    for (const variant of item.variants) {
      if (!variant.capture_required) continue;
      const key = JSON.stringify([item.listing_id, variant.capture_sku, variant.asin]);
      if (seen.has(key)) continue;
      seen.add(key);
      signal.throwIfAborted();
      progress(`正在自动采集 ${variant.sku} 的卖家与原价…`);
      try {
        await collectSource(item.listing_id, variant.capture_sku || undefined, signal, () =>
          progress(`${variant.sku} 需要人工验证，请在打开的来源网页完成验证…`),
        );
      } catch (e) {
        signal.throwIfAborted();
        errors.push(`${variant.sku}：${errorMessage(e)}`);
      }
    }
  }
  return { attempted: seen.size, errors };
}
