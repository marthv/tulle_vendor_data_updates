---
name: reference_xano_unsorted_query_drift
description: Unsorted Xano db.query returns heap order — array order and first-non-empty picks silently drift after any ALTER TABLE
metadata: 
  node_type: memory
  type: reference
  originSessionId: 9657cfb4-9eb1-4b0a-96ed-3cc944fe3ad1
  modified: 2026-07-27T04:30:55.697Z
---

A Xano `db.query` with no `sort` returns Postgres **heap-scan order**, which is not stable.
Anything downstream that depends on row order silently changes with no data change — and an
`ALTER TABLE` (adding a column or index) reshuffles it.

Hit on 2026-07-27: adding columns to table 36 changed `fn21`'s output for V37 — array element
order in `venue_types`/`venue_offerings`/`venue_attributes`/`pricing_years`, plus the
first-non-empty `contact_information` and `pdf_description`. All VALUES were identical as sets;
only order moved. The tell that it was row order and not a code bug: `pricing_years` moved too,
and its code had not been touched.

Fixed with `sort = {Extracted_PDF_Data.id: "desc"}`. **DESC, not ASC** — table 36 appends a row
per re-extraction, so descending id makes "first non-empty" mean *newest extraction wins*.
ASC was tried first and surfaced stale copy: V3 resolved to a description containing "hosts up
to 350 guests", which violates the current prompt's no-guest-count rule, i.e. it came from an
older prompt version. The fix also corrected V37's `contact_information` from
`summerbereton@upperline.nyc` (a different business) to `info@99scott.com`.

When diffing endpoint responses before/after a change, compare lists as **sets** first — an
order-only diff is usually this, not a regression.

Still open: `fn21` does not dedupe table-36 rows by PDF_ID, so stale rows from earlier
extractions still feed the min/max and unique aggregates. Related: [[project_extracted_pdf_data_dedupe]].
