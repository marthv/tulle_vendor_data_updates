---
name: project_slowness_breakdown_2026-09-30
description: "Des 'everything is slow' (09-30) - measured breakdown: ep119 misses = 57% of user wait; vendor page PI waits serially on Similar Vendors; boot is ~1s (corrects 7s lab estimate)"
metadata:
  type: project
---
Measured 2026-09-30 (17.3h Xano history, 33k app calls, 255 IPs; Mixpanel app_ready n=1,118). Scripts in
that session's scratchpad perf/ (ephemeral).

- **Boot is fine:** app_ready p50 ~0.95s, p90 2.1-3.2s by OS. CORRECTS the 7s phone figure in
  [[project_auth_page_speed]] (that was a throttled lab profile).
- **ep119 = 57% of all user wait**; misses = 98% of it (miss p50 1.65s, p95 7.5s, max 33s).
  Discovery page>=2 misses p50 4-5s / p95 ~24s = 51% of ep119 miss time (18% of clients page).
  Only 138/1,392 keys shared -> warming can't fix it. "Not Sure" is rare (~125 s/day) - not a priority.
- **Vendor page serial chain** (WeWeb wf `Load Extracted Data` 362c3d59): auth -> view -> Extracted ->
  Similar Vendors (ep119) -> PDF -> Benchmarks -> PI -> PI Full. PI start tracks Similar finish
  (0.9s / 2.9s / 4.8s / 8.8s). Fix candidate: move Similar Vendors to the END. ~1,600 views/day.
- Concurrent ep119 misses slow each other (1 alone 2.1s, 3+ overlapping ~23s) - UNVERIFIED cause (Xano CPU?).
- 19% duplicate calls (benchmarks 48%) = cleanup only; ~1,080 race-401s/day (favorites, auth/me, upgrade_offer).
[[project_perf_baseline_digest]] [[project_preauth_race_signs_users_out]]

## 2026-09-30 later: ROOT CAUSE FOUND by probe (measured, not inferred)
The agent's "page 2+ is slow, cache the id list" diagnosis was WRONG: same search page1 miss p50 3.1s vs
page>=2 4.1s; page 1 only LOOKED fast because most page-1 misses are text searches (0.15s).
Real cause = the capacity-range OR added 2026-09-23/24:
`((cap >= $cap_floor && cap <= $cap_ceil) || cap == $cap_unknown)` in ep119's main query (and ep121's).
Probe (temp fn = ep119 stack + Date.now() marks, run via POST /workspace/1/function/run - scriptable!):
Illinois browse main query 6.6-9.0s with OR, 1.1-2.7s without. Rewrite:
`cap >=? $cap_floor_q && cap <=? $cap_ceil_q` with *_q = null unless $cap_unknown == -1 (range set).
34-case matrix (25 costliest real shapes + range cases): ALL exact itemsTotal+ids parity;
mean 3.67s -> 1.50s; one-state browse 4.4-8.5s -> 1.5-1.8s; range-set cases unchanged.
Probes 73/74 DELETED (404 verified). Builder/matrix: session scratchpad ep119/ (ephemeral).
Backups: ep119_pre_pagecache_2026-09-30.xs, ep121_pre_capor_2026-09-30.xs.
Quirk kept for parity: ceiling-only range still INCLUDES capacity-0 venues (floor 0 >= 0) despite the
v2 comment saying they're excluded once a range is set.
Vendor page #1 (Similar Vendors moved last) edited by user 09-30; side effect: "Store extracted data"
(extractedData/isExtractedDataLoaded, bound by page elements) now runs after EVERYTHING - suggest moving
Store->Log->Reset to right after Fetch Extracted Data.

## SHIPPED 2026-09-30 17:21:27 UTC - ep119 capacity-OR fix LIVE (user approved)
Read back: published (draft null), fix present, OR gone, auth=user + ttl 7100 kept. cache_v NOT bumped.
First 15 min: 50/50 calls 200. Uncached: NY 1.06-1.12s, Intl 1.05s, PA 0.81s, all-venues 2.30s (were ~4-5s).
Pre-push 16:00-17:21 uncached p90 was 38s / max 85s (contention pile-up). App traffic was light after push
(whole-app dip, ~10 IPs) - so real-user proof = next health DM (pre-fix: "11% of searches over 3s").
Rollback: re-push ep119_pre_pagecache_2026-09-30.xs. As-shipped copy: ep119_v_capor_fix_2026-09-30.xs.
NEXT: ep121 has the identical OR (backup ep121_pre_capor_2026-09-30.xs) - parity-test then push.
Photography/Entertainment uncached still ~5.3-5.8s (P/E package path, not capacity) - separate, low volume.
Vendor page #1 PUBLISHED 16:53:40 UTC (Similar last) - verify PI start time vs Similar finish 10-01;
"Store extracted data" still runs last (tweak not applied).

## ep121 (map): capacity-OR fix tested and NOT pushed (2026-09-30)
30-case parity (25 costliest real map shapes + range cases): all identical, but NO speed gain (mean 3.23s vs
3.11s, noise, mixed per case). Map cost tracks rows returned / bbox size (2,500-row caps at 60 sq deg take
8-15s either way), not the predicate. 1,041 uncached map calls had 1,023 distinct shapes - bbox makes caching
useless. The health report's "biggest cause: capacity_ceiling" for the MAP is an artifact: every map call
carries capacity_ceiling, so it shows up in every slow shape. Don't re-try this fix on ep121.
Probes 75/76 deleted (404). ep121 untouched since 2026-09-24.
