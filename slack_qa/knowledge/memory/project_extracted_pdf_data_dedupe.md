---
name: project-extracted-pdf-data-dedupe
description: Extracted PDF Data table accumulates rows per re-extraction; filter queries must dedupe by PDF_ID
metadata: 
  node_type: memory
  type: project
  originSessionId: 28d016f9-6b83-47bd-b376-89b275082963
---

`Extracted PDF Data` (Xano table 36) **appends** a new row on every extraction run rather than replacing the previous one. After multiple re-runs, the same PDF_ID has multiple rows — only the most recent reflects the current prompt's fields.

**Why:** The current `_post_summary` in `extract_core.py` does a plain POST per entry. No upsert, no delete-before-insert. Re-extracting a PDF creates fresh rows; the old ones stay.

**How to apply:**
- Any filter / read query against table 36 must dedupe — typically `sort by last_extracted_at desc` and take first row per PDF_ID, or only consider rows where `last_extracted_at >= <recent cutoff>`.
- ~500 historical "non-venue contamination" rows (planners, photographers, florists, etc. — no pricing, empty Venue_Type) exist from extractions before the `vendor_type` short-circuit shipped (commit 9fc2f52, 2026-05-27). The user explicitly deferred backfill cleanup — these rows still exist.
- New fields like `Description` and `Preferred_Vendors` (also shipped 9fc2f52) populate only on rows extracted on or after 2026-05-27. Pre-existing rows for the same PDF_ID have these fields blank.

Linked: [[reference-extraction-pipeline]].
