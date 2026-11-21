import type { GenerationJob, ListingDocument } from '../api/publishing';

// Called once on entry/prepare, never from the status polling loop.
export function shouldPrepareOnEntry(doc: ListingDocument | null, job: GenerationJob | null) {
  if (!doc || (job && ['awaiting_sources', 'queued', 'running'].includes(job.state))) return false;
  const editable = doc.rows.filter((row) =>
    ['unsubmitted', 'failed', 'rejected', 'unpublished'].includes(
      doc.results[row.platform_sku]?.status || 'unsubmitted',
    ),
  );
  if (!editable.length) return false;
  if (!job && !doc.basis.items.some((item) => item.reuse_template)) return true;
  // Entering an editable Amazon listing refreshes quotes before generating its fields.
  return editable.some((row) =>
    doc.basis.items.some((item) => item.listing_id === row.listing_id && item.source === 'Amazon'),
  );
}
