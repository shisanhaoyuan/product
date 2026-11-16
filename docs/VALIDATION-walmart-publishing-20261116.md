# Walmart publishing validation — 2026-11-16

This change adds an independent, versioned Walmart US listing document to each
prelisting batch, a grouped Vue editor, official-schema validation, explicit
store-confirmed feed submissions, per-SKU results and restricted retries.

## Automated checks

- Full Python regression: **283 passed, 5 skipped** (SQLite, before the final removed-category regression was added).
- Final publishing tests: **22 cases**, covering source/catalog isolation, idempotent persistence, exact SKU/store binding,
  nested/conditional schema constraints, UPC/GTIN checks, exact approved exemption,
  real official sofa schema, atomic submission claims, repeated request keys,
  failed-only retries, uncertain submission recovery, stale sources/validation,
  fresh stock after submission and during the remote inventory GET, historical
  attempts unable to overwrite new stock, account binding locks, roles/CSRF,
  signed image exposure, nonexistent local images, non-finite numbers, Chinese
  field-specific errors, and a removed product type with a cached exemption spec.
- PostgreSQL parity: **56 passed** for publishing/accounts/warehouse/storage at the
  earlier stage, then **20 updated publishing tests passed** and the new
  stock-change-during-GET test passed separately. The final removed-category case
  only changes schema-validation branching and was verified on SQLite.
- Frontend: **48 passed** across 10 test files; Vue TypeScript check and production
  Vite build passed after the final UI changes.
- Changed-file whitespace checks passed. The unrelated pre-existing trailing-space
  edit in `catalog/__main__.py` was preserved.

## Browser checks

An isolated temporary database and credential key were used on port 8774. Every
Walmart request used `httpx.MockTransport`; fake feeds and inventory never left
the process. The real official Sofas & Couches schema was used. Illustrative sofa
images and all product data were explicitly marked DEMO.

Verified in the browser:

1. Required and optional groups, bilingual labels, unit/measure layout, desktop
   layout at 1440×1000 and narrow layout at the browser's default width.
2. Image enlargement and closure.
3. Editing a title, clicking away, automatic save, reload and persistence without
   changing the other SKU.
4. Removing a required title, validating, switching tabs and clicking its Chinese
   error to reach the correct title input, then correcting and revalidating.
5. Typed target-store confirmation, a mock feed submission, one published SKU and
   one failed SKU with Chinese feedback, and returning only the failed SKU to edit.
6. Previously published SKU fields locked, JSON collapsed, stable SKU mapping,
   optional fields searchable, and sticky product selection on long forms.

Screenshot: [listing form](screenshots/walmart-listing-editor-demo.png).

The temporary demo process and PostgreSQL cluster were stopped. Local application
services on 8765 and 8767 were restarted with their original environment; both
reported schema version 16. Local SQLite backups were taken before restart.
Browser helper 8766 was left running.

**No real Walmart submission or inventory write was performed. No Git push or
server deployment was performed in this task.** Actual seller restrictions,
exemption interpretation, platform publication and media accessibility still
require a small controlled production acceptance run before large batches.
