import { beforeEach, expect, it, vi } from 'vitest';
import type { PrelistingPreview } from '../api/types';
const mock = vi.hoisted(() => ({
  capture: vi.fn(),
  browser: vi.fn(),
  page: vi.fn(),
  read: vi.fn(),
}));
vi.mock('../api/client', () => ({
  catalogApi: {
    sourceCapture: mock.read,
    captureSource: mock.capture,
    browserCaptureSource: mock.browser,
  },
}));
vi.mock('./browserCapture', () => ({ captureInBrowser: mock.page }));
import { collectSource, collectMissingPrices, collectProductSources } from './sourceCollection';

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
it('reports captcha without opening a browser or calling browser capture', async () => {
  mock.capture.mockResolvedValue({ status: 'verification_required', asin: 'B000000001' });
  await expect(collectSource('one', 'child', new AbortController().signal)).rejects.toThrow(
    '未打开新网页',
  );
  expect(mock.page).not.toHaveBeenCalled();
  expect(mock.browser).not.toHaveBeenCalled();
});
it('shows the server diagnosis for a blocked background attempt', async () => {
  mock.capture.mockResolvedValue({
    status: 'verification_required',
    issues: ['后台浏览器正在处理其他商品'],
  });
  await expect(collectSource('one', 'child', new AbortController().signal)).rejects.toThrow(
    '后台浏览器正在处理其他商品',
  );
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

it('collects every child independently even when another child is blocked', async () => {
  mock.read.mockResolvedValue(null);
  mock.capture.mockImplementation(async (_id, sku) =>
    sku === 'red'
      ? { status: 'verification_required', asin: 'B000000002' }
      : {
          status: 'captured',
          asin: sku === 'blue' ? 'B000000003' : 'B000000001',
          price: '10',
          original_price: '20',
          currency: 'USD',
          delivery_location: '10001',
        },
  );
  const results = vi.fn();
  const signal = new AbortController().signal;
  const outcome = await collectProductSources(
    'one',
    [
      { asin: 'B000000001' },
      { sku: 'red', asin: 'B000000002' },
      { sku: 'blue', asin: 'B000000003' },
    ],
    signal,
    true,
    vi.fn(),
    results,
  );
  expect(mock.capture.mock.calls.map((c) => c[1])).toEqual([undefined, 'red', 'blue']);
  expect(outcome.collected).toBe(2);
  expect(outcome.verifiedPrices).toBe(2);
  expect(outcome.errors[0]).toContain('red');
  expect(mock.page).not.toHaveBeenCalled();
  expect(results.mock.calls.map((c) => c[0])).toEqual([undefined, 'blue']);
});
it('skips already captured children and stops remaining child requests when cancelled', async () => {
  mock.read.mockResolvedValue({
    asin: 'B000000001',
    original_price: '20',
    price: '10',
    currency: 'USD',
    delivery_location: '10001',
    image_collection: { images: ['/api/products/images/assets/' + 'a'.repeat(64)] },
  });
  const controller = new AbortController();
  const result = await collectProductSources(
    'one',
    [{ sku: 'red', asin: 'B000000001' }],
    controller.signal,
    true,
    vi.fn(),
    vi.fn(),
  );
  expect(result.collected).toBe(0);
  expect(mock.capture).not.toHaveBeenCalled();
  controller.abort();
  await expect(
    collectProductSources(
      'one',
      [{ sku: 'blue', asin: 'B000000002' }],
      controller.signal,
      false,
      vi.fn(),
      vi.fn(),
    ),
  ).rejects.toThrow();
});

it('collects images for an older record even when its price is complete', async () => {
  mock.read.mockResolvedValue({
    asin: 'B000000001',
    original_price: '20',
    price: '10',
    currency: 'USD',
    delivery_location: '10001',
  });
  await collectProductSources(
    'one',
    [{ sku: 'red', asin: 'B000000001' }],
    new AbortController().signal,
    true,
    vi.fn(),
    vi.fn(),
  );
  expect(mock.capture).toHaveBeenCalledTimes(1);
});

it.each([502, 503, 504])(
  'pauses remaining child captures when the previous result is unknown (%s)',
  async (status) => {
    mock.capture.mockRejectedValueOnce(Object.assign(new Error('网关请求失败'), { status }));
    const result = await collectProductSources(
      'one',
      [
        { sku: 'red', asin: 'B000000001' },
        { sku: 'blue', asin: 'B000000002' },
      ],
      new AbortController().signal,
      false,
      vi.fn(),
      vi.fn(),
    );
    expect(mock.capture).toHaveBeenCalledTimes(1);
    expect(result.collected).toBe(0);
    expect(result.errors.join('；')).toContain('后续采集已暂停');
  },
);

it('pauses missing-price collection across products after a network interruption', async () => {
  const data = preview();
  data.items.push({ ...data.items[0]!, listing_id: 'next-product' });
  mock.capture.mockRejectedValueOnce(new TypeError('Failed to fetch'));
  const result = await collectMissingPrices(data, new AbortController().signal, vi.fn());
  expect(mock.capture).toHaveBeenCalledTimes(1);
  expect(result.attempted).toBe(1);
  expect(result.errors.join('；')).toContain('后续采集已暂停');
});
