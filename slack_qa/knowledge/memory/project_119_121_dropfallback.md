---
name: project_119_121_dropfallback
description: ep119 v12 / ep121 v9 removed dead live-fallback enrichment branch; warmer task8 realigned to fallback_all
metadata: 
  node_type: memory
  type: project
  originSessionId: d7089329-158e-4f31-ad48-85816be92d12
  modified: 2026-07-27T02:55:30.276Z
---

Shipped 2026-07-27: low-risk cleanup of ep119 (`wptp_updated_mappings_search`) + ep121 (`wptp_map_light`).

**Why safe:** caller audit (both repos) — ep119 has ZERO code callers (only WeWeb + warmer task 8); ep121's only code caller is `_loadtest_ep199.py` (bbox only). `flt_glance == null` = exactly 1 row (id 4838, junk: blank Vendor_ID, Validated_Data != 1 → never returned). Backfill complete since 2026-07-19; task 10 self-heals hourly + table-36 trigger. So the live-fallback branch was dead.

**Change 1 — dropped the ~150-line live-compute enrichment fallback (`if flt_glance == null`) in both:**
- ep119 **v12** (cache_v 9→10): kept `flt_glance != null` glance fast-path; null-glance rows now push the base row unenriched via an `else` (never dropped). Subquery gates simplified `include_pdf_data || flt_glance==null` → `include_pdf_data`. -188 lines.
- ep121 **v9** (cache_v 5→6): deleted the `== null` branch inside `if $effective_enrich`; null-glance vendor keeps the light `$item` (121 pushes `$item` unconditionally). -162 lines.
- **Verified byte-identical** via before/after canonical-JSON diff (fresh cache_v to bypass cache): 119 filtered IDENTICAL, 121 light IDENTICAL; the only deltas were `space_count`/`pricing_row_count` on 1-2 vendors = flt_glance DATA DRIFT from the hourly sync (copied verbatim by unchanged code), NOT a regression. v12/v9 stable across repeat fresh calls.

**Change 2 — warmer task 8 (`warm_wptp_search_cache`) realigned:** WeWeb's live 119 call sends `fallback_all=true` (v10+), but the warmer omitted it → resolved false → warmed a DIFFERENT cache key than live traffic (users ran cold). Added `fallback_all: true` to all 3 warming shapes (grid params, ps4 params, the 4 multi_urls). **`cache_v` left OMITTED on purpose** — resolves to endpoint default so it auto-tracks future cache_v bumps (hardcoding would silently de-align on the next bump). Verified warmer shape → 200 @ ~0.26s.

Backups: `.claude/xano_backups/ep119_v12_drop_fallback_2026-07-27.xs`, `ep121_v9_drop_fallback_2026-07-27.xs`, `task8_pre_fallback_align_2026-07-27.xs`.

**ep119 v13 / ep121 v10 TRIM MIN-SIDE INPUTS (2026-07-27):** removed provably-unused inputs — verified via `searchApiRequestHistory` (query_id=119/121, 50 reqs each): `min_spend_min/max`, `base_fee_min`, `fb_per_person_min` sent 0/50. 119 (cache_v 10→11): dropped those 4 inputs + `$eff_fee_floor`/`$eff_ppfb_floor`/`$eff_fbmin_*` vars + conditionals + 5 WHERE conds; 25→21 inputs. 121 (cache_v 6→7): dropped `base_fee_min`+`fb_per_person_min` + floor vars + 2 WHERE conds; 19→17 inputs. Max-side (`base_fee_max`,`fb_per_person_max`) KEPT. facets untouched (has_min_spend reads flt_min_fbmin from row, not the input). Verified byte-identical (NY default+filtered on 119; bbox+filtered on 121) and removed params 200-ignored. Warmer (task 8) sends none of these → no warmer change. Traffic finding: WeWeb sends `include_facets`/`sort_by` always-empty (facets feature not called yet; sort path never triggered). Backups: `ep119_v13_trim_minside_2026-07-27.xs`, `ep121_v10_trim_minside_2026-07-27.xs`.

**Editing method that works (reuse this):** getAPI (confirm live) → Write clean vN backup → updateAPI (bump cache_v) → capture canonical-JSON BEFORE (old cache_v) vs AFTER (fresh cache_v), `diff` → expect identical modulo flt_glance counter drift. For structural deletions, writing a fresh clean file beats whitespace-matching (Xano formatter adds trailing spaces on blank lines `\n  \n`). Retry updateAPI on getaddrinfo/000 blips. Transform done via Python string-surgery on copies of the prior backups (v11/v8). See [[project_flt_denorm_aggregates]] [[reference_xano_cache_resolved_inputs]] [[project_maplight_widebbox]]. DEFERRED (not done): pruning unused inputs (119 min_spend_*; 121 faves/search_input — need per-WeWeb-collection check); ep121 enriching up to 2500 rows on any active filter (revisit whether map popup still needs full enrich now light output carries Venue_Type + flt_venue_types).
