# Official Walmart schema fixture

`sofas-mp-item-20260829.json` is the MP_ITEM Draft-07 schema for version
`5.0.20260829-12_49_39-api`, extracted on 2026-11-16 from:

https://developer.walmart.com/file/mp/us/Consolidated_Schema_File_MP_ITEM_OCT26.zip

Only `properties.MPItem.items.properties.Visible.properties` was reduced to
`Sofas & Couches` to avoid checking a 453 MB consolidated file into Git.
All other header, Orderable, optional sections and the chosen product type's
properties, required lists and conditional constraints are retained unchanged.
Whitespace was removed. This is a validation fixture, never a production fallback.
Production uses the bound store's actual taxonomy/spec responses.

The tests use `httpx.MockTransport`; no test publishes to Walmart or modifies a real store.
