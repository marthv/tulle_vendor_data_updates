---
name: project_confidentiality_flagging
description: "Extraction now flags PDFs with confidentiality/sharing-restriction language; flag-for-review, NOT auto-hide"
metadata: 
  node_type: memory
  type: project
  originSessionId: 515e62d6-62b0-437f-b1b4-6e84cdf9e800
---

Cofounder Des (Slack, 2026-06-03) raised legal exposure: couples submit vendor pricing PDFs that may carry confidentiality clauses; publishing them creates liability. Decision: detect + flag, but **do NOT hide from the front-end** (less conservative — flagged venues stay live until human/lawyer review). Endpoints 119/121 deliberately NOT modified.

Implemented in `tulle_vendor_data_updates/extract_core.py` (2026-06-03):
- `SUMMARY_PROMPT` gained `confidentiality_risk` ("yes"/"no") + `confidentiality_evidence` (verbatim triggering phrase) fields; copyright lines alone don't trigger.
- `_post_summary` writes `confidentiality_flag` (bool) → table 36 (Extracted PDF Data).
- `_update_pdf_status` writes `confidentiality_flag` (bool) + `confidentiality_evidence` (text) → table 10 (WPTP PDFs) = the triage/review queue.
- PDF-level: flagged if ANY space flags; evidence = first non-empty. Logs 🚩 line per flagged PDF.
- Only the venue path flags; non-venues short-circuit (never published, so out of scope).

**Scope decision (2026-06-03):** user wants a FULL 4-pass re-extraction of ALL ~4,500 venue PDFs (not just the ~1602 un-extracted) — refresh all data AND set the flag everywhere. Added `force_all` param to `run_extraction` (bypasses dedup; venue pre-filter still applies), exposed as `--all` in run_chunk.py and launch_parallel.py. Cost ~$1,400, ~5-6h.

**Live-site re-run constraint (load-bearing):** table 36 is read LIVE by the consumer front-end (filters 119/121) — NEVER truncate it; a wipe takes pricing offline for couples during the ~6h run. Posts to tables 36 AND 37 APPEND (no upsert).

**Agreed full-refresh sequence (2026-06-03):** NO pre-clear needed. Smoke-test (`run_chunk.py 0 10 --all`) → launch force-all → AFTER, prune legacy by timestamp. Discriminator: both tables stamp `last_extracted_at` (table 37 = full ISO; confirmed column exists), so delete rows where `last_extracted_at < run start`. Table 36 prune is optional (filters already take latest row per PDF_ID, so stale copies are harmless). Table 37 = `venue_pricing` (granular space×day×month grid), surfaced only via `include_pricing` which is INTERNAL — temporary doubled rows during the run are fine. This keeps the flaky meta API OFF the launch critical path.

See [[project_extraction_venue_filter]] and [[reference_extraction_pipeline]] and [[reference_pricing_two_tables]].

**Smoke test PASSED (2026-06-04, `railway run python run_chunk.py 0 12 --all`, $2.72):** 8 ok / 1 partial / 3 benign fails (>30MB, bad download, JSON parse). Verified via meta searchTableContent: table 10 row 3376 (Met) = today's write with `confidentiality_flag`:false + `confidentiality_evidence`:"" ✅; table 36 today's row (id 6878) has `confidentiality_flag`:false ✅. Column names match, write path works. Bonus: re-extraction is RICHER than original (filled Venue_Type/Offering/Preferred_Vendors/Contact that old rows left blank) — full re-run improves data, no degradation.

**Run mechanics learned:**
- CLI runs need `PYTHONUTF8=1` on Windows console (emoji in progress prints crash cp1252). Railway/Streamlit + Linux unaffected.
- Railway env (project "tulle admin dash"/production) is MISSING `XANO_VENUE_CATEGORIES_ENDPOINT` — add `https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B/wptp_venue_categories` before the big run for the venue pre-filter cost saver.
- Dashboard (dashboard.py, the Railway app) has NO force_all toggle — modes are PDF IDs / re-run failed / all pending / row range. Full re-run needs either a new force-all UI toggle OR `railway run python launch_parallel.py 12 6781 0 --all`.
- Post-run prune: delete table 36 rows per PDF_ID keeping only MAX(id) — NOT a timestamp cutoff, because legacy rows have `last_extracted_at`=null and would be missed. (P3376 had 6 rows across 3 generations: 2026-03-04 null-ts, 05-31, 06-04.)

**Full-run execution plan (decided 2026-06-04):**
- Run on a SEPARATE Railway service (2nd service in project "tulle admin dash", same repo), NOT the user's PC — the PC's network/DNS is unreliable (a 100-PDF pilot saw 58% failures: 42x can't resolve googleapis.com, 12x getaddrinfo failed, 25x "Claude error" — all network, not pipeline/Anthropic). Stagger fix validated though: 0 worker dead-fetches.
- CHUNKED into 500-PDF batches with an approval gate between each (~12 batches, ~$155 + ~2.8h each at 12 workers). User approves before each continues.
- launch_parallel.py gained env-var batch mode: no CLI args → runs window [BATCH_START, BATCH_START+BATCH_SIZE) with BATCH_WORKERS, FORCE_ALL=1. Bump BATCH_START between deploys; start command stays `python launch_parallel.py`. Prints `BATCH SUMMARY` to stdout for Railway logs.
- Railway service settings: Start Command `python launch_parallel.py`, Restart Policy = NEVER (else it loops the whole $1.8k run), per-service env vars = the 7 creds + BATCH_* vars.
- Full-run timing corrected: NOT 5-6h. ~5,823 venues at ~3 PDFs/min/12-workers ≈ 25-30h total (the old 5-6h figure was for the ~1602 un-extracted, not the full re-run). Hence batching.
- Failed PDFs are recoverable via rerun_failed mode (marked extraction_status=failed in table 10).
- Full re-run pushed to repo: commit 19c6618 (stagger + batch mode), 543595c (confidentiality + force_all). Commit + push needed before each Railway deploy picks up code.

**Railway batch service OPERATIONAL (2026-06-04):** Second service `extraction-batch` in project "tulle admin dash" (production env), same repo. Service ID 059eff0a-c8c9-4bed-a010-cce5608fb336. Uses a dedicated config file `railway.batch.toml` (startCommand `python launch_parallel.py`, restartPolicyType NEVER) set via the service's Config-as-Code file-path field — this is REQUIRED because railway.toml (streamlit) overrides UI start-command for both services. Vars set via CLI incl. the 2 secrets as resolved references `${{Tulle Admin Dash.ANTHROPIC_API_KEY}}` / `...GOOGLE_SERVICE_ACCOUNT_JSON}}` (refs with spaces in service name DO resolve). `railway add` (service creation) returns Unauthorized from CLI — must create service in UI; but `railway variables --set`, `railway logs`, `railway redeploy` all work from CLI. 10-PDF Railway test: 7 ok / 2 failed (the 2 are deterministic bad-data, not network) / $1.91 — vs PC's 58% network failures. Railway confirmed solid.
- Drive batches: bump `BATCH_START` (+ `BATCH_SIZE`/`BATCH_WORKERS`) via `railway variables --service extraction-batch --set` → auto-redeploys → runs one window → stops (restart=NEVER). Read `BATCH SUMMARY` line from `railway logs --service extraction-batch`. Per-worker detail is in-container (invisible); only launcher stdout + BATCH SUMMARY show in railway logs.
- NOTE: user's PC network/DNS is intermittently failing (breaks git push, railway CLI, downloads) — retry CLI/git ops on "could not resolve host" / Unauthorized.

Related Slack asks still open: T&C opt-in checkbox on sign-in; "no sharing restrictions" checkbox on PDF-submission popup; enlist a lawyer ($500–1500).
