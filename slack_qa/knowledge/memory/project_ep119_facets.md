---
name: project_ep119_facets
description: ep119 v9 adds optional facets block (area availability counts) for dynamic filtering + empty-state
metadata: 
  node_type: memory
  type: project
  originSessionId: d7089329-158e-4f31-ad48-85816be92d12
  modified: 2026-07-26T19:34:57.535Z
---

Shipped 2026-07-26: **ep119 v9 FACETS**. New input `bool include_facets?` returns a `facets` object scoped to STATE + CATEGORY ONLY (advanced filters intentionally ignored — "availability" counts, not live-faceted). Shape: `{area_total, pricing_models{opt:n}, venue_types{opt:n}, vibes{opt:n}, outdoor_ceremony:n, has_venue_fee:n, has_fb_pricing:n, has_min_spend:n}`. `facets=null` when not requested (backward-compatible; normal paging path untouched). Computed via ONE light `db.query` over table-11 `flt_*` columns (flt_venue_offerings/types/attributes, flt_outdoor_ceremony, flt_min_venue_fee/ppfb/fbmin) + a tally `api.lambda` (accesses rows via `$var.facet_rows`). Reuses the existing `$eff_state_single`/`$eff_country`/`$state_vendor_ids` state scoping. `cache_v` default bumped 6→7.

Purpose (per user): WeWeb keeps the logic — (a) hide filters when area has <50 vendors, (b) show "X vendors found" from `area_total`, (c) empty-state fallback: when `area_total==0`, show full repository for the category with a "no vendors found" banner. See [[project_flt_denorm_aggregates]] [[project_wptp_search_joins]] [[project_international_filter_bug]].

Smoke-tested live (api group canonical GynP5T1B, host xqtb-2ma7-ijfy.n7e.xano.io): NY Venue area_total=589 (==itemsTotal), NY+CT=715, Alaska=0 — all <0.25s. Backup: `.claude/xano_backups/ep119_v9_facets_2026-07-26.xs`. 121 NOT yet given facets (deferred; would scope to bbox). Empty-state (#2) is now mostly a WeWeb job driven by `facets.area_total`.

**How to apply:** In WeWeb, fetch 119 once with `include_facets=true` on area/category change; drive filter visibility off the per-option counts and the <50 rule off `area_total`.

**ep119 v10 EMPTY FALLBACK (2026-07-26, also shipped):** optional `bool fallback_all?` — when the fully-filtered query returns `itemsTotal==0`, endpoint re-runs scoped to CATEGORY ONLY (drops state + all advanced filters) and returns the full category repo so the UI never dead-ends. Response adds `is_fallback` (bool) for a "no matches — showing all vendors" banner. facets unchanged (still area-scoped; `area_total==0` is the miss signal). Off by default. cache_v bumped 7→8. Verified: Alaska+fb270+fallback → is_fallback=true, itemsTotal=9521; NY+fallback → is_fallback=false. **Perf caveat:** cold fallback hit pays a full-category `totals:true` count (~2–8s); WARM is ~0.2s. Because the cache key includes the varying dead filters, every distinct empty combo pays its own cold cost. If this bites, have WeWeb issue ONE canonical "show all" fetch (strip state+filters) on empty so all empty users share a single cache key — OR pre-warm it. Backup: `.claude/xano_backups/ep119_v10_empty_fallback_2026-07-26.xs`.

**ep119 v11 FILTERED_TOTAL (2026-07-26):** fixes "9521 vendors found" bug — on fallback, itemsTotal is overwritten to the full-repo count so WeWeb lost the real match count. Response now also returns `filtered_total` = the count matching the user's ACTUAL filters, captured before fallback overwrites $model. WeWeb: bind "X vendors found" to `filtered_total`; use `itemsTotal` ONLY for pagination; on `is_fallback==true` show "No vendors matched — showing all {itemsTotal} vendors". Normal search → filtered_total==itemsTotal. cache_v bumped 8→9. Verified: Alaska fallback filtered_total=0/itemsTotal=9521; NY filtered_total==itemsTotal==589. Backup: `.claude/xano_backups/ep119_v11_filtered_total_2026-07-26.xs`. Note: Xano public host had transient 000 unreachability mid-session (recovered in ~10s).
