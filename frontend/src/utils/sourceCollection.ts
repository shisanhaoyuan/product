import { catalogApi } from '../api/client';
import type { PrelistingPreview } from '../api/types';
import { errorMessage } from './display';
import { captureInBrowser } from './browserCapture';

const browserPreferenceKey = 'jiwu-source-browser-10001';
export function prefersLocalSourceBrowser() {
  try {
    return localStorage.getItem(browserPreferenceKey) === 'yes';
  } catch {
    return false;
  }
}
export function preferLocalSourceBrowser(value: boolean) {
  try {
    localStorage.setItem(browserPreferenceKey, value ? 'yes' : 'no');
  } catch {
    /* Optional preference. */
  }
}
export async function collectSourceInBrowser(
  listingId: string,
  sku: string | undefined,
  asin: string,
  signal: AbortSignal,
) {
  const page = await captureInBrowser(asin, signal, 'source');
  signal.throwIfAborted();
  const result = await catalogApi.browserCaptureSource(listingId, sku, page, signal);
  signal.throwIfAborted();
  if (result.asin !== asin) throw new Error('采集期间 ASIN 已变化，请刷新商品');
  if (!/\b10001\b/.test(result.delivery_location || ''))
    throw new Error('配送地区仍未设为 10001，请更新验证助手后重新采集。');
  preferLocalSourceBrowser(true);
  return result;
}

function captureOutcomeUnknown(error: unknown) {
  return (
    error instanceof TypeError ||
    (error instanceof Error && 'status' in error && [502, 503, 504].includes(Number(error.status)))
  );
}
const interruptedMessage =
  '本轮后续采集已暂停：上一条请求结果未知，后台可能仍在处理。请稍后查看记录，避免连续重试。';

export async function collectSource(
  listingId: string,
  sku: string | undefined,
  signal: AbortSignal,
  onVerification: () => void = () => {},
  asin?: string,
) {
  signal.throwIfAborted();
  if (asin && prefersLocalSourceBrowser()) {
    onVerification();
    return collectSourceInBrowser(listingId, sku, asin, signal);
  }
  const result = await catalogApi.captureSource(listingId, sku, signal);
  signal.throwIfAborted();
  if (
    result.asin &&
    (result.wraith_test?.status === 'location_unverified' ||
      result.wraith_test?.location_verified === false ||
      result.issues?.some((issue) => issue.includes('配送地区') && issue.includes('10001')))
  ) {
    onVerification();
    return collectSourceInBrowser(listingId, sku, result.asin, signal);
  }
  if (['verification_required', 'browser_required'].includes(result.status)) {
    onVerification();
    throw new Error(
      [result.method === 'wraith_background' ? 'Wraith 后台采集' : '', ...(result.issues || [])]
        .filter(Boolean)
        .join(' · ') || '后台采集遇到人机验证或报价无法读取，未打开新网页',
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
  targets: for (const item of preview.items) {
    if (item.source !== 'Amazon') continue;
    for (const variant of item.variants) {
      if (!variant.capture_required) continue;
      const key = JSON.stringify([item.listing_id, variant.capture_sku, variant.asin]);
      if (seen.has(key)) continue;
      seen.add(key);
      signal.throwIfAborted();
      progress(`正在自动采集 ${variant.sku} 的卖家与原价…`);
      try {
        await collectSource(
          item.listing_id,
          variant.capture_sku || undefined,
          signal,
          () => progress(`${variant.sku} 需要浏览器协助；如已打开 Amazon，请设置配送邮编 10001…`),
          variant.asin,
        );
      } catch (e) {
        signal.throwIfAborted();
        errors.push(`${variant.sku}：${errorMessage(e)}`);
        if (captureOutcomeUnknown(e)) {
          errors.push(interruptedMessage);
          break targets;
        }
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
  let verifiedPrices = 0;
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
      const result = await collectSource(
        listingId,
        target.sku,
        signal,
        () => progress(`${label} 等待浏览器协助：请在 Amazon 设置配送邮编 10001，完成后自动继续…`),
        target.asin,
      );
      if (result.asin !== target.asin) throw new Error('采集期间 ASIN 已变化，请刷新商品');
      onRecord(target.sku, result);
      collected++;
      if (hasCapturedPrice(result, target.asin)) verifiedPrices++;
      else
        errors.push(
          `${label}：${result.issues?.join('；') || '来源价格信息不完整，可人工核对售价'}`,
        );
    } catch (e) {
      signal.throwIfAborted();
      errors.push(`${label}：${errorMessage(e)}`);
      if (captureOutcomeUnknown(e)) {
        errors.push(interruptedMessage);
        break;
      }
    }
  }
  return { collected, verifiedPrices, errors };
}
