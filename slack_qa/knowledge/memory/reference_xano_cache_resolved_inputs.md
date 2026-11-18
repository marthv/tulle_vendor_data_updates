---
name: xano-cache-resolved-inputs
description: "Xano response cache keys on RESOLVED inputs (defaults applied), not raw query params — verified empirically on ep 119"
metadata: 
  node_type: memory
  type: reference
  originSessionId: d9864838-9801-45f4-ad0b-6bb294105672
  modified: 2026-07-19T06:33:47.170Z
---

Xano's endpoint response cache (`input: true`) keys on the **resolved input set after defaults are applied**, not the raw query string. Verified 2026-07-19 on ep 119: a minimal-param request (`page,page_size,Category_Input,State_Input,Search_Input`) hit the cache entry created by the frontend's full-param request (which explicitly sends `base_fee_max=0`, `sort_by=""`, `Country_Input=""`, etc. — all equal to the defaults).

**Why:** means cache warmers can use minimal params and still serve real traffic, as long as omitted params default to what the frontend sends. Param ORDER also doesn't matter.

**How to apply:** when warming or debugging cache behavior, compare *resolved* inputs, not URLs. Also: warm-task freq must be > cache ttl, else runs alternate hit/no-refill and entries expire mid-window (the pre-2026-07-19 warmer bug, freq 3000 < ttl 3600). Current config: ep 119 ttl=7100, warmer (task 8) freq=7200 at odd-hours:30 UTC. Related: [[xano-compute-reduction-2026-07]]

## 2026-09-30: updateAPI does NOT clear the response cache (verified)
After the ep119 "Not Sure" fix (updated 04:26:33 UTC), "Not Sure" grid/strip keys cached at <=04:19:49 were still
served as X-Query-Cache: 1 hits at 04:47-04:48, so the user kept seeing the pre-fix empty result. Pushing new code
leaves existing cache entries in place until ttl (7100s). A fix that changes RESULTS for already-cached keys needs a
cache_v default bump (cold-cache cost for every key) or a wait of up to ttl. I wrongly told the user the push had
"likely cleared the cache" - it had not. The earlier post-push slowdown was coincidental misses, not a flush.

## 2026-09-30 VERIFIED: ep119 "Not Sure" fix returns vendors
Post-expiry, uncached (X-Query-Cache 0) "Not Sure" Venue calls at 07:30, 09:30, 09:35 (real user),
11:30 all returned itemsTotal 5641 (nationwide). A real user paged to page 2 at 06:36. Uncached
"Not Sure" is SLOW: 2.5-7.6s.
