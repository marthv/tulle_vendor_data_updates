---
name: project_batch_extraction_filesapi
description: Batch PDF extraction uses Files API (file_id); needs sonnet-4-6 (old sonnet-4 errors on file_id); each PDF = 3 passes
metadata: 
  node_type: memory
  type: project
  originSessionId: e65ca513-385d-4e99-9403-13e7992bca2f
---

The batch path of `extract_core.py` (`run_extraction_batch` → `process_batch_results`) in the **tulle_vendor_data_updates** repo submits PDFs to the Anthropic Batch API via the **Files API** (`file_id` document references, beta `files-api-2025-04-14`) instead of base64. Reason: base64 embeds PDF bytes in every request and hits the **256 MB per-batch cap** after a few dozen PDFs; `file_id` is a few KB per request → thousands of PDFs per batch.

Each PDF = **3 batch requests** (custom_ids `__p1` summary/Sonnet, `__p3` pricing/Sonnet, `__p4` classification/Haiku). At ingest a PDF is marked `failed` if p1 (summary) failed even when p4 succeeded.

**KEY GOTCHA (diagnosed 2026-06-20):** the old `claude-sonnet-4-20250514` does **NOT** accept `file_id` document references — batches errored on exactly the 2 Sonnet passes per PDF (e.g. 9 ok / 18 err on a 9-PDF test; 185/376 on a 187-PDF run) while the Haiku pass succeeded. Removing the system-prompt `cache_control` did not fix it. Fix: `_MODEL_SONNET = "claude-sonnet-4-6"` (same $3/$15 price, supports file_id). base64 path (live/sequential `run_extraction`) worked fine on the old model — this only bit the file_id batch path.

`file_id` enables big batches but does NOT reduce per-token cost (model still reads the full PDF each pass). **Cost driver = input tokens** (June usage: 1.52B in vs 57M out — input is ~95% of the bill), because each PDF is sent 3×. **Merged mode** (shipped 2026-06-20, OFF by default, toggle/`EXTRACT_MERGED` env): one Sonnet call returns `{summary, pricing, classification}` via `_build_merged_batch_request` (custom_id `__m`) + `MERGED_PROMPT` (the 3 prompts composed VERBATIM); `process_batch_results` splits it back into p1/p3/p4 so downstream is unchanged → PDF sent 1× = ~50% input cut. Validate first with `validate_merge(pdf_ids)` / the dashboard "🧪 Validate merged mode" expander (runs both paths, diffs). NEVER use `force_all` to re-extract (table 36 appends; the Jun-04 ~700M-token spike was a full re-run).

Status writes: batch path PATCHes `wptp_pdfs` to `batch_submitted` at submit (no attempt bump) and to the final status at ingest. `rerun_failed` re-selects both `failed` and stuck `batch_submitted` rows. Tables 36 (Extracted PDF Data) + 37 (venue_pricing) fill ONLY at ingest (Check Batch Results / the worker) — a `batch_submitted` batch has written nothing to 36/37 yet.

**Row-range selects by `id`** (2026-06-20): "Start id/End id" in the dashboard = inclusive `wptp_pdfs.id` bounds (matches the PDF Status Table's id column), via `_select_by_id_range`. Previously it sliced `rows_with_links` by POSITION in Xano's non-id return order — so entering 6785-6795 processed unrelated PDFs. Applies to both batch and live paths.

**Auto-ingest worker** (2026-06-20): `batch_worker.py` → `poll_and_ingest_batches()` (in extract_core, no Streamlit) is meant to run as a Railway cron service (`python batch_worker.py`, `*/15 * * * *`) — finds ended batches not yet ingested and posts results to Xano, removing the manual Check Batch Results click. Idempotent via `_batch_already_ingested` (skips batches whose PDFs already left `batch_submitted`). Needs XANO_JOBS_ENDPOINT (pipeline_jobs list). The cron service must be created manually in the Railway UI. Dashboard display bug fixed same day: PDF Status Table was collapsing any status outside a 4-value list (incl. `batch_submitted`, `skipped_non_venue`) to 'pending'. Related: [[reference_extraction_pipeline]], [[project_extraction_venue_filter]], [[reference_xano_connection_drops]].

## 2026-10-02 rows 17341-19266 (1,906 PDFs) -> [token] (submitted ~01:47 UTC 10-03)
- First submit FAILED at batch create: Batch API custom_id must match ^[a-zA-Z0-9_-]{1,64}$ and one bad id rejects the
  WHOLE batch. Culprit PDF_ID "P 17425" (space; Mayflower Inn V1960, never extracted) -> renamed to P17425 in table 10
  (backup .claude/xano_backups/table10_row17425_pre_pdfid_fix_2026-10-02.json). 12 rows have EMPTY PDF_ID.
- tulle_vendor_data_updates cb8d433: skip+mark-failed any PDF_ID not matching ^[a-zA-Z0-9_-]{1,60}$ before download.
- 571df4f: re-submit reuses Files-API uploads named "<PDF_ID>.pdf" from the last 24h (no re-download) - VERIFIED live:
  1,909 rows in ~2 min, all "Reusing".
- After batch create the dashboard marks each PDF batch_submitted in Xano one-by-one (~3/s, ~10 min) BEFORE printing
  the batch id - looks "stuck at Submitting". Batch id is readable from table 10 last_error "batch=<id>".
- Merged mode = one Sonnet 4.6 call/PDF; Haiku only used when merged is OFF. Sonnet 5.5 cheaper per token ($2/$10 vs
  $3/$15) but unvalidated - user to decide after a ~30-PDF side-by-side. Dashboard insight blurb still on claude-opus-4-1.
- dca981d: dashboard Sign out fixed (cookie re-auth raced the CookieManager delete); USER CONFIRMED working 2026-10-03.
