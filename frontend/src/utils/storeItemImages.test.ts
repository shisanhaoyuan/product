import { beforeEach, expect, it, vi } from 'vitest';
import { request } from '../api/client';
import { loadStoreItemImages, type StoreItemImage } from './storeItemImages';

vi.mock('../api/client', () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
beforeEach(() => {
  mocked.mockReset();
});
const page = () => ({
  partner: 'store-owner',
  revision: 2,
  items: ['a', 'b', 'c'].map((sku): StoreItemImage => ({ sku, image: '', image_source: '' })),
});

it('loads at most two images at once and isolates a failed lookup', async () => {
  let active = 0,
    peak = 0;
  mocked.mockImplementation(async (url) => {
    active++;
    peak = Math.max(peak, active);
    await Promise.resolve();
    active--;
    const query = new URL(url, 'https://local').searchParams;
    expect(query.get('partner')).toBe('store-owner');
    expect(query.get('revision')).toBe('2');
    const sku = query.get('sku');
    if (sku === 'b') throw new Error('rate limited');
    return {
      sku,
      image: `https://example.com/${sku}.jpg`,
      image_source: 'walmart',
      image_status: 'ready',
    };
  });
  const data = page();
  await loadStoreItemImages('21', data, new AbortController().signal);
  expect(peak).toBe(2);
  expect(data.items.map((item) => item.image_status)).toEqual(['ready', 'unavailable', 'ready']);
});

it('aborts the queue on store/page change and ignores responses already in flight', async () => {
  const controller = new AbortController();
  mocked.mockImplementation(async () => {
    controller.abort();
    return { sku: 'a', image: 'https://example.com/old-store.jpg' };
  });
  const data = page();
  await loadStoreItemImages('21', data, controller.signal);
  expect(mocked).toHaveBeenCalledTimes(1);
  expect(data.items.every((item) => !item.image)).toBe(true);
});

it('keeps existing images and rejects wrong SKU responses', async () => {
  const data = page();
  data.items[0]!.image = 'https://example.com/existing.jpg';
  mocked.mockResolvedValue({ sku: 'wrong', image: 'https://example.com/wrong.jpg' });
  await loadStoreItemImages('21', data, new AbortController().signal);
  expect(mocked).toHaveBeenCalledTimes(2);
  expect(data.items[0]!.image).toContain('existing');
  expect(data.items[1]!.image).toBe('');
});
