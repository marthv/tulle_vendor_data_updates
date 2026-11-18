---
name: project-international-filter-bug
description: "PINNED BUG 2026-07-21 — \"International\" state selection on Vendor Discovery returns zero results (ep 119)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1ee0b3e6-d423-4adb-bca4-63d59b2ef261
  modified: 2026-07-21T05:46:35.030Z
---

**FIXED 2026-07-21 — TWO causes, both resolved:**
1. WeWeb stale page (page=49 sent after filter change) — user added page-reset-to-1 on filter change; verified working.
2. ep 119 Country_Input ANDed globally, so NY+International+France returned only France. **v8 shipped 2026-07-21:** when states[] active, $eff_country blanked (global country skipped) and country applied only to the "International" entry inside the per-state id loop. cache_v default 5→6. Smoke-tested: NY+Intl+France=701 (589 NY + 112 FR exactly); Intl+France=112; Intl=715; legacy NY=589 unchanged. Note: id-asc sort means French rows land on later pages (pre-existing behavior). Backups in .claude/xano_backups/ep119_{pre_intl_country_scope,v8_intl_country_scope}_2026-07-21.xs (uncommitted).

Earlier debugging trail: ep 119 works perfectly at page=1 — International alone = 715 results/60 pages; International+AZ+AL = 961; International+Country=France = 112/10 pages (matches the "10 pages shown, grid empty" screenshot exactly). Bug is purely WeWeb sending stale page=49 after filter change. Fix = reset currentPage var to 1 in every filter-change workflow (state, country, category, search, advanced, Reset all) — user applying in WeWeb editor.

Originally reported 2026-07-21: On Vendor Discovery, selecting **International** in the STATE multi-select returns nothing — both standalone (International + Country=Australia → 0 results) and mixed with real states (International + Arizona + Alabama + Country=France → 0 results, but pagination still showed 10 pages, suggesting count/query mismatch). User expects a fix will be needed in Xano ep 119 (wptp_updated_mappings_search).

**Hypothesis to verify:** "International" is likely not a literal value in the flt_* state/denorm columns — it probably should map to a country-based branch (Country_Input / non-US rows). Mixing states[] real values with "International" likely ANDs into a contradiction instead of OR-ing (cf. [[project_maxfilter_fix_v4]] dual-path pattern). Check how states[] and Country_Input interact in ep 119 v6 where clause, and what value WeWeb sends for "International".

**STRONG LEAD (2026-07-21 trace):** every International search in request history was sent with **page=49** — WeWeb does not reset pagination when filters change, and page 49 of a small filtered set = empty items (matches "shows 10 pages but renders nothing"). Fix WeWeb page-reset-to-1-on-filter-change FIRST, then retest before touching ep 119. Those stale-page International searches also run 1.5-3.6s uncached.

Related: [[project_flt_denorm_aggregates]], [[project_wptp_search_joins]]. Remember: cache can mask fixes; where-clause pipes fatal.
