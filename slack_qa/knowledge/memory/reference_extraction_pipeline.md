---
name: reference-extraction-pipeline
description: Where the active PDF extraction pipeline lives and how it connects to Xano — different from the legacy tulletogether/pipeline/app.py
metadata: 
  node_type: memory
  type: reference
  originSessionId: 28d016f9-6b83-47bd-b376-89b275082963
---

The **active** PDF pricing extraction pipeline lives in a separate repo:

- **Repo path:** `C:\Users\vivek\Documents\GitHub\tulle_vendor_data_updates`
- **Remote:** `github.com/marthv/tulle_vendor_data_updates` (private)
- **Deploy target:** Railway (auto-deploys on push to `main`)
- **Key files:**
  - `extract_core.py` — Claude-based PDF extraction core (SUMMARY_PROMPT, STRUCTURE_PROMPT, PRICING_PROMPT*, CLASSIFICATION_PROMPT, run_extraction generator)
  - `dashboard.py` — Streamlit/Flask admin dashboard for running batches
  - `.env.example` — env var template (ANTHROPIC_API_KEY, GOOGLE_SERVICE_ACCOUNT_JSON, XANO_SUMMARY_ENDPOINT, XANO_PRICING_ENDPOINT, XANO_GET_ENDPOINT, XANO_PATCH_PDF_ENDPOINT)
- **NOT to confuse with:** `tulletogether/pipeline/app.py` — older/legacy version, covered by [[feedback-pipeline-ignore]].

**Xano workspace + table IDs (workspace 1 "Tulle Together"):**
- Table 10 — `WPTP PDFs` (source PDFs, has PDF_Link to Drive, extraction_status, last_error)
- Table 11 — `WPTP Updated Mappings` (vendor master with Vendor_ID, Name)
- Table 36 — `Extracted PDF Data` (extraction OUTPUT — summary rows per venue space)
- Table 37 — `venue_pricing` (pricing grid output — N rows per PDF)

**Status values written to `wptp_pdfs.extraction_status`:**
`extracted | partial | failed | skipped | skipped_non_venue | pending` (blank → pending).
`skipped_non_venue` was added in commit 9fc2f52 — non-venue PDFs (planners, photographers, florists, DJs, bands, caterers) short-circuit after Pass 1 and don't pollute Extracted PDF Data.
