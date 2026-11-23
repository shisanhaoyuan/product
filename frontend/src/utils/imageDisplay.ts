// Keep the editable source URL intact; only the display request uses our local cache.
export function imageDisplayCandidates(url: string, listingId?: string): string[] {
  if (
    listingId &&
    /^https:\/\/(?:(?:pg-cdn-a2|ps-cdn-s3)\.datacaciques\.com|(?:[a-z0-9-]+\.)*(?:media-amazon\.com|ssl-images-amazon\.com|gigab2b\.cn|gigab2b\.com|saleyee\.com|saleyee\.cn))\//i.test(
      url,
    )
  ) {
    return [
      `/api/products/${encodeURIComponent(listingId)}/images/cached?url=${encodeURIComponent(url)}`,
      url,
    ];
  }
  return [url];
}
