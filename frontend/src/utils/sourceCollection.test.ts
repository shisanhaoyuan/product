import { beforeEach, expect, it, vi } from 'vitest';
import type { PrelistingPreview } from '../api/types';
const mock = vi.hoisted(() => ({ capture: vi.fn(), browser: vi.fn(), page: vi.fn() }));
vi.mock('../api/client', () => ({
  catalogApi: { captureSource: mock.capture, browserCaptureSource: mock.browser },
}));
vi.mock('./browserCapture', () => ({ captureInBrowser: mock.page }));
import { collectSource, collectMissingPrices } from './sourceCollection';

function preview(): PrelistingPreview {
  return {
    items: [
      {
        listing_id: 'one',
        source: 'Amazon',
        variants: [
          { sku: 'child', asin: 'B000000001', capture_sku: 'child', capture_required: true },
          { sku: 'duplicate', asin: 'B000000001', capture_sku: 'child', capture_required: true },
          { sku: 'ready', asin: 'B000000002', capture_required: false },
        ],
      },
      {
        listing_id: 'two',
        source: 'Saleyee',
        variants: [{ sku: 'vendor', capture_required: true }],
      },
    ],
  } as unknown as PrelistingPreview;
}
beforeEach(() => {
  vi.resetAllMocks();
  mock.capture.mockResolvedValue({
    status: 'captured',
    asin: 'B000000001',
    original_price: '72.89',
  });
});
it('automatically captures each missing Amazon target once, skipping existing prices and suppliers', async () => {
  const signal = new AbortController().signal;
  const result = await collectMissingPrices(preview(), signal, vi.fn());
  expect(result).toEqual({ attempted: 1, errors: [] });
  expect(mock.capture).toHaveBeenCalledExactlyOnceWith('one', 'child', signal);
});
it('hands human verification to the existing browser helper, then persists the same target', async () => {
  const signal = new AbortController().signal;
  const page = {
    asin: 'B000000001',
    source_url: 'https://www.amazon.com/dp/B000000001',
    html: 'verified',
  };
  mock.capture.mockResolvedValue({ status: 'verification_required', asin: page.asin });
  mock.page.mockResolvedValue(page);
  mock.browser.mockResolvedValue({ status: 'captured', asin: page.asin, original_price: '72.89' });
  const notify = vi.fn();
  const result = await collectSource('one', 'child', signal, notify);
  expect(notify).toHaveBeenCalledOnce();
  expect(mock.page).toHaveBeenCalledWith(page.asin, signal, 'source');
  expect(mock.browser).toHaveBeenCalledWith('one', 'child', page, signal);
  expect(result.original_price).toBe('72.89');
});
it('reports failures without looping and continues to other targets', async () => {
  const data = preview();
  data.items[0]!.variants[2]!.capture_required = true;
  mock.capture.mockRejectedValueOnce(new Error('无法读取来源'));
  const result = await collectMissingPrices(data, new AbortController().signal, vi.fn());
  expect(mock.capture).toHaveBeenCalledTimes(2);
  expect(result.errors).toEqual(['child：无法读取来源']);
});
it('stops all remaining collection on cancellation', async () => {
  const controller = new AbortController();
  controller.abort();
  await expect(collectMissingPrices(preview(), controller.signal, vi.fn())).rejects.toThrow();
  expect(mock.capture).not.toHaveBeenCalled();
});
