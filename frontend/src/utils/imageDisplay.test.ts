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
