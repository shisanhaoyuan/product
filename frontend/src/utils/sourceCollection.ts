import { catalogApi } from '../api/client';
import type { PrelistingPreview } from '../api/types';
import { errorMessage } from './display';

export async function collectSource(
  listingId: string,
  sku: string | undefined,
  signal: AbortSignal,
  onVerification: () => void = () => {},
) {
  signal.throwIfAborted();
  const result = await catalogApi.captureSource(listingId, sku, signal);
  signal.throwIfAborted();
  if (['verification_required', 'browser_required'].includes(result.status)) {
    onVerification();
    throw new Error(
      '后台采集遇到人机验证或报价无法读取，未打开新网页；可稍后重试，或核对售价后点击已验证',
    );
  }
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
          progress(`${variant.sku} 后台采集受限，保留提醒供人工核对…`),
        );
      } catch (e) {
        signal.throwIfAborted();
        errors.push(`${variant.sku}：${errorMessage(e)}`);
      }
    }
  }
  return { attempted: seen.size, errors };
}

export function hasCapturedPrice(
  record: import('../api/types').SourceCapture | null,
  asin: string,
) {
  return (
    !!record &&
    record.asin === asin &&
    Number(record.original_price) > 0 &&
    Number(record.price) > 0 &&
    record.currency === 'USD' &&
    /\b10001\b/.test(record.delivery_location || '')
  );
}

export function hasCollectedImages(record: import('../api/types').SourceCapture | null) {
  return !!record?.image_collection?.images.length;
}

export async function collectProductSources(
  listingId: string,
  targets: { sku?: string; asin: string }[],
  signal: AbortSignal,
  missingOnly: boolean,
  progress: (message: string) => void,
  onRecord: (sku: string | undefined, record: import('../api/types').SourceCapture) => void,
) {
  const errors: string[] = [];
  const seen = new Set<string>();
  let collected = 0;
  for (const target of targets) {
    signal.throwIfAborted();
    const key = JSON.stringify([target.sku, target.asin]);
    if (seen.has(key)) continue;
    seen.add(key);
    const label = target.sku || '主商品';
    if (!/^[A-Z0-9]{10}$/.test(target.asin)) {
      errors.push(`${label}：缺少有效 ASIN`);
      continue;
    }
    progress(`后台采集 ${label} · ${seen.size}/${targets.length}`);
    try {
      const old = missingOnly
        ? await catalogApi.sourceCapture(listingId, target.sku, signal)
        : null;
      signal.throwIfAborted();
      if (old && hasCapturedPrice(old, target.asin) && hasCollectedImages(old)) {
        onRecord(target.sku, old);
        continue;
      }
      const result = await collectSource(listingId, target.sku, signal);
      if (result.asin !== target.asin) throw new Error('采集期间 ASIN 已变化，请刷新商品');
      onRecord(target.sku, result);
      collected++;
      if (!hasCapturedPrice(result, target.asin))
        errors.push(
          `${label}：${result.issues?.join('；') || '来源价格信息不完整，可人工核对售价'}`,
        );
    } catch (e) {
      signal.throwIfAborted();
      errors.push(`${label}：${errorMessage(e)}`);
    }
  }
  return { collected, errors };
}
