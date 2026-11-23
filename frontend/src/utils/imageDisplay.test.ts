import { expect, it } from 'vitest';
import { imageDisplayCandidates } from './imageDisplay';

it('displays imported images through the bound server cache before the original CDN', () => {
  const url = 'https://pg-cdn-a2.datacaciques.com/photo.jpg';
  expect(imageDisplayCandidates(url, 'listing')).toEqual([
    '/api/products/listing/images/cached?url=' + encodeURIComponent(url),
    url,
  ]);
  expect(imageDisplayCandidates(url)).toEqual([url]);
  for (const safe of [
    '/api/products/images/assets/' + 'a'.repeat(64),
    'https://evil.example/a.jpg',
  ])
    expect(imageDisplayCandidates(safe, 'listing')).toEqual([safe]);
});

it('caches trusted supplier galleries with a stable URL and keeps other hosts untouched', () => {
  for (const host of ['m.media-amazon.com', 'images.gigab2b.com', 'image.saleyee.com']) {
    const url = `https://${host}/current.jpg`;
    expect(imageDisplayCandidates(url, 'listing')).toEqual([
      '/api/products/listing/images/cached?url=' + encodeURIComponent(url),
      url,
    ]);
  }
  for (const url of [
    'https://saleyee.com.evil.example/photo.jpg',
    'https://secret@saleyee.com/photo.jpg',
    'http://m.media-amazon.com/photo.jpg',
  ])
    expect(imageDisplayCandidates(url, 'listing')).toEqual([url]);
});
