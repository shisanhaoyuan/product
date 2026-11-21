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
  // A completed/failed/interrupted attempt is resumed only by an explicit user action.
  return !job && !doc.basis.items.some((item) => item.reuse_template);
}
