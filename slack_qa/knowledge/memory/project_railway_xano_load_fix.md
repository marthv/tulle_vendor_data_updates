---
name: railway-xano-load-fix
description: Venue Pricing tab outages fixed via Xano ep 199 venue_pricing_dashboard; Railway repo committed but NOT pushed (push = redeploy)
metadata: 
  node_type: memory
  type: project
  originSessionId: e23e4b54-6ea6-42ff-8a6f-8875dc009779
  modified: 2026-07-21T06:54:02.245Z
---

2026-07-15: Diagnosed + fixed the "Xano goes down when using Venue Pricing tab" issue. Cause: `_vp_load()` in tulle_vendor_data_updates/dashboard.py fired 7 unpaginated full-table GETs (all of table 36 = 18k+ rows, all wptp_pdfs, 5 wptp_map_light bands), each with 120s timeout, on every 10-min cache miss / Refresh click.

Fix: **Xano endpoint 199 `venue_pricing_dashboard`** (group 10 / GynP5T1B) — slim columns, table-36→table-11 join server-side (Category=="Venue"), paginated. Sections: `pricing` (default), `pdfs`, `raw_extracted`/`raw_pricing` (filter by `pdf_ids[]`, `ts` cache-buster). Response cache ttl 600. Backup: tulletogether/.claude/xano_backups/199_*.xs. Uncached full load ~26s (4 pages of 5000), warm ~2s.

Railway repo commits c522b80 + a0c9e36 (502/504 retry fix) pushed to main 2026-07-15, deployed, and **user-verified in production: "much quicker", working as intended**. Correctness verified: 0 name/state mismatches vs old map_light source; tab now shows ~757 MORE venues (~7,869 total) — old path silently dropped venues without Validated_Data==1 + US lat/lng. Stress-tested: 3 concurrent cold-cache loads, zero canary errors, max 2s latency for other users. Gotcha: Xano throws **502** (not just 503) under load — client retries must cover 502/503/504.

Push required `gh auth login` as **marthv** + `gh auth setup-git` — vmarthi17 has no push access to marthv/tulle_vendor_data_updates; gh credential helper on this machine now authenticates as marthv. 2026-07-21: same for marthv/tulletogether ("Repository not found" = wrong account, not missing repo); vmarthi17's keyring token is INVALID — fix is `gh auth switch -u marthv` (+ `gh auth setup-git` if needed), applies to ALL marthv/* repos.

Stress-test harness kept at tulle_vendor_data_updates/_loadtest_ep199.py (gitignored) — rerun it after future Xano/dashboard changes. Also: `_get_active_job` cached 15s, Data Explorer loads cached 2 min, timebound report serialized, launch_parallel prefetches shared Xano state once to `.xano_cache/` (workers read disk instead of N×3 full scans), batch-ingest guard now fails closed.

Related: [[xano-mcp-quirks]], [[flt-denorm-aggregates]]

**XanoScript gotchas learned:** `output = [...]` field selection on db.query STRIPS paging metadata unless you list `curPage`/`nextPage` explicitly; `totals: true` on a joined query re-counts per page and dominated response time (74s→26s after removing).
