---
name: project_extraction_venue_filter
description: extract_core.py venue pre-filter + new Xano endpoint 177; real remaining ~1602 venue PDFs as of 2026-06-02
metadata: 
  node_type: memory
  type: project
  originSessionId: 0048d46a-8307-46fe-a35d-82a8b9199aa3
---

extract_core.py (tulle_vendor_data_updates repo) gained a venue pre-filter on 2026-06-02: `_fetch_venue_vendor_ids()` loads venue Vendor_IDs and the run loop skips any PDF whose vendor's mapping Category != 'Venue' BEFORE download/model calls (logged skipped_non_venue). Safety: empty/failed fetch disables the gate (falls back to LLM non-venue detection) so it never skips everything.

Requires env var `XANO_VENUE_CATEGORIES_ENDPOINT` = https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B/wptp_venue_categories (new read-only endpoint 177 in API group 10 "WeWeb Transparency Project", returns slim {Vendor_ID, Category} from table 11).

Live counts 2026-06-02: 6392 vendors (5622 venue / 770 non-venue); 6516 table-10 PDFs with Drive links (5823 venue / 692 non-venue); table 36 = 4791 distinct done PDF_IDs (~570 non-venue = historical contamination). **Real remaining venue PDFs to extract ≈ 1602.** Expected spend ~$500 (@~$0.31/PDF), ~53h sequential / ~5-6h at 10-12 parallel workers.

Per-PDF: 4 sequential Claude Sonnet-4 calls (summary/structure/pricing-grid/classification), PDF prompt-cached after call 1. Cost rates in script: $3/$15/$3.75/$0.30 per M (in/out/cache-write/cache-read). Parallelism is via row-range chunking (run_chunk.py + launch_parallel.py, both uncommitted as of writing). Ceiling for a FULL re-pull of all 6781 = ~$2000-2400. See [[reference_extraction_pipeline]] [[project_extracted_pdf_data_dedupe]].