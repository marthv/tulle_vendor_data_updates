---
name: project_ep119_warmer_timeouts
description: "2026-09-27 health check - task 8 \"N of 15 buckets FAILED (last status 200)\" alerts are almost certainly warmer-side HTTP timeouts, not ep119 errors; ep119 had 0 failures/24h but p95 5.4s"
metadata:
  node_type: memory
  type: project
  originSessionId: 06c27a2a-b960-40ba-a903-9be6c3720ccd
  modified: 2026-09-27T19:19:01.678Z
---

Measured 2026-09-27 (24h Xano request history = the full retention; scoped to Referer=tulletogether.app):

- **ep119: 4,006 app requests, 0 failures.** p50 0.09s, **p95 5.45s, p99 15.5s, max 40s**
  (vs p95 3.71s on 2026-09-12). Cache hit 63%; miss p50 2.34s / p95 9.7s.
  **State-filtered Venue grids (`states` +/- `State_Input`) = ~59% of all time spent above 3s.**
  Photography miss p50 ~6s.
- **ep121: 3.2% fail, all 401s, 15/16 with NO token** — same shape as ep119's pre-v24 CORS
  preflight problem. ep121 is still `auth="user"` with `cache.auth:false`.
- **Warmer alerts:** the alert's "last status" is the 15th URL's status only, so it hides the
  failing bucket. All 180 warmer requests seen in 24h were 200 at the server. The two alerting
  runs (2026-09-24 21:30, 09-25 09:30 UTC) were the only abnormally long runs (64s, 87.6s vs
  ~39s normal) = ~12s extra per failed bucket → client-side api.request timeout (~10s, task 8
  sets none). ~75% confidence; the individual statuses aged out of history before we looked.
- Warmer run time went ~15-22s (Sep 23) → ~35-45s (Sep 24 onward), across the ep119 caprange
  v1/v2 deploys (04:05 / 04:49 UTC Sep 24). Correlated, NOT proven causal.

**How to apply:** don't chase ep119 "failures" — there are none; the work is ep119 miss latency
on state filters, ep121 auth-off, and making task 8 set a timeout + report per-bucket status.
[[project_perf_baseline_digest]] [[project_ep119_401_race]] [[project_pi_guest_min_and_capacity_filter]]

## 2026-09-29: task 8 REALIGNED for capacity_ceiling (live updated_at 14:11:53 UTC)
The capacity-slider publish made the app send capacity_ceiling=10000 on every page_size=12 grid search, so 28 of
the 42 warm URLs matched no real key (health report cache hit 66.2% on 09-28 -> 63.5% on 09-29). Rebuilt from
5,414 app requests (09-28 15:43 -> 09-29 13:55 UTC): 28 grid URLs now carry capacity_ceiling=10000 right after
include_facets= (the app's position); the page_size=3 Semi-Inclusive strip (0/386) and similar-vendor shape (0/395)
do NOT carry it, so those 14 stayed. Read back from live: 28 capacity_ceiling URLs, draft null, active, schedule
unchanged. Backup task8_pre_capceiling_2026-09-29.xs; new file task8_capceiling_2026-09-29.xs.
VERIFY: 09-30 health report ep119 cache_hit_pct should recover toward >=66% (baseline 63.5%). Not verified yet.
Also found: the largest ep119 shape (similar-vendor WITH Category_Input+State_Input, ~2,177/day) is a 730-key long
tail - not warmable. Buckets 10-15 (non-Venue categories) see ~1-3 requests/day each - near-zero value.
LESSON: any WeWeb change that adds a param to the search collection silently breaks the warmer (same as gold chip).
