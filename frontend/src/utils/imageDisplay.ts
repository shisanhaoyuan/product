// Keep the editable source URL intact; only the display request uses our local cache.
export function imageDisplayCandidates(url: string, listingId?: string): string[] {
  if (listingId && /^https:\/\/(?:pg-cdn-a2|ps-cdn-s3)\.datacaciques\.com\//.test(url)) {
    return [
      `/api/products/${encodeURIComponent(listingId)}/images/cached?url=${encodeURIComponent(url)}`,
      url,
    ];
  }
  return [url];
}
