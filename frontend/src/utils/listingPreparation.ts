import type { GenerationJob, ListingDocument } from '../api/publishing';

// Called once on entry/prepare, never from the status polling loop.
export function shouldPrepareOnEntry(doc: ListingDocument | null, job: GenerationJob | null) {
  if (!doc || (job && ['queued', 'running'].includes(job.state))) return false;
  const editable = doc.rows.filter((row) =>
    ['unsubmitted', 'failed', 'rejected', 'unpublished'].includes(
      doc.results[row.platform_sku]?.status || 'unsubmitted',
    ),
  );
  if (!editable.length) return false;
  if (!job && !doc.basis.items.some((item) => item.reuse_template)) return true;
  return editable.some((row) => {
    const item = doc.basis.items.find((item) => item.listing_id === row.listing_id);
    if (item?.source !== 'Amazon') return false;
    const price = Number(row.orderable.price);
    if (!Number.isFinite(price) || price <= 0) return true;
    const variant = item.variants.find((v) => v.platform_sku === row.platform_sku);
    return !!variant?.capture_required && !doc.source_price_reviews?.[row.platform_sku];
  });
}
