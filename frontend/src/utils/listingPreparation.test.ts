import { describe, expect, it } from 'vitest';
import type { GenerationJob, ListingDocument } from '../api/publishing';
import { shouldPrepareOnEntry } from './listingPreparation';

function document(): ListingDocument {
  return {
    rows: [{ listing_id: 'a', platform_sku: 'sku', orderable: { price: 0 } }],
    results: {},
    basis: {
      items: [
        {
          listing_id: 'a',
          source: 'Amazon',
          variants: [{ platform_sku: 'sku', capture_required: true }],
        },
      ],
    },
  } as unknown as ListingDocument;
}
const completed = { state: 'complete' } as GenerationJob;

describe('automatic preparation on editor entry', () => {
  it('retries missing quotes/prices even when an earlier generation result exists', () => {
    const doc = document();
    expect(shouldPrepareOnEntry(doc, completed)).toBe(true);
    doc.rows[0]!.orderable.price = 102.05;
    expect(shouldPrepareOnEntry(doc, completed)).toBe(true);
    doc.basis.items[0]!.variants[0]!.capture_required = false;
    expect(shouldPrepareOnEntry(doc, completed)).toBe(false);
  });
  it('does not duplicate active jobs or touch submitted rows', () => {
    const doc = document();
    for (const state of ['queued', 'running'] as const)
      expect(shouldPrepareOnEntry(doc, { state } as GenerationJob)).toBe(false);
    for (const status of ['processing', 'published', 'unknown', 'stock_pending']) {
      doc.results.sku = { status } as ListingDocument['results'][string];
      expect(shouldPrepareOnEntry(doc, completed)).toBe(false);
    }
  });
  it('respects manually verified prices and usable reused templates', () => {
    const doc = document();
    doc.rows[0]!.orderable.price = 102.05;
    doc.source_price_reviews = { sku: { price: 102.05, actor_id: null, at: '' } };
    expect(shouldPrepareOnEntry(doc, completed)).toBe(false);
    doc.rows[0]!.orderable.price = 0;
    expect(shouldPrepareOnEntry(doc, completed)).toBe(true);
    doc.rows[0]!.orderable.price = 102.05;
    doc.basis.items[0]!.reuse_template = {} as NonNullable<
      (typeof doc.basis.items)[0]['reuse_template']
    >;
    expect(shouldPrepareOnEntry(doc, null)).toBe(false);
    delete doc.source_price_reviews;
    expect(shouldPrepareOnEntry(doc, null)).toBe(true);
  });
  it('does not retrigger finished supplier content, and starts fresh documents', () => {
    const doc = document();
    doc.basis.items[0]!.source = 'GigaB2B';
    expect(shouldPrepareOnEntry(doc, completed)).toBe(false);
    expect(shouldPrepareOnEntry(doc, null)).toBe(true);
    expect(shouldPrepareOnEntry(null, null)).toBe(false);
  });
});
