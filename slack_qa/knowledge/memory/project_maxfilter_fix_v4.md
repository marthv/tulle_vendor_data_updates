---
name: project_maxfilter_fix_v4
description: "2026-07-07 fix for broken max-price filters on endpoints 119/121 — dual-path + max-implies-min>=1 pattern; pitfalls: unconditional >0 guards over-filter, response cache masks fixes"
metadata: 
  node_type: memory
  type: project
  originSessionId: fe571bd0-a905-4dfe-b17f-46a65b366953
---

2026-07-07: Fixed max-price filters (base_fee_max, fb_per_person_max, min_spend_max) on Xano
endpoints 119 (wptp_updated_mappings_search) and 121 (wptp_map_light), both "v4". Verified live.

**Two root causes:**
1. Zero-valued rows in table 36 trivially pass `<= max` (e.g. V122 has 4 rows from repeat
   extractions — only the oldest, id 714, has Venue_Fee=25000; the 3 newer ones have 0).
2. Empty array filters: `Venue_Offering in? []` rejects ALL rows, so fee-only queries returned
   nothing via the shared advanced-filter subquery.

**Working architecture (both endpoints):**
- Dual-path subquery: `$has_array_filters` flag → array-filter where clause OR fee-only where
  clause (never runs `in?` against empty arrays).
- Zero-row exclusion via **max-implies-min**: when a `_max` input is set and its `_min` is null,
  set effective_min = 1 in the stack. Where clause stays plain null-safe `>=? min && <=? max`.
  NO `> 0` clauses in the where.

**Pitfalls learned (cost hours):**
- Embedding unconditional `($db.X > 0 && $db.X <=? $max)` guards applies `X > 0` even when that
  filter is INACTIVE → massive over-filtering (Texas base_fee_max=45000 returned 11 instead of 785)
  and killed V122 (its only real-fee row has Food_and_Beverage_Min=0).
- `(($input.arr|count) == 0 || ...)` inside a db.query where clause → ERROR_FATAL. Filters/pipes
  don't belong in where clauses; do the branching in the stack instead.
- Response cache (ttl 3600/14400, input:true) **survives updateAPI publishes** — repeated tests of
  identical params hit stale pre-fix responses and made working fixes look broken for ~40 min.
  Always vary a real input (page_size, bbox epsilon) to force a fresh cache key when verifying.
- Instance-host DNS blips (curl exit 6 on xqtb-2ma7-ijfy.n7e.xano.io only, google fine, nslookup
  fine) block both curl AND the MCP server; passes in ~5 min. `curl --resolve host:443:34.170.63.102` works around it.

**v5b perf pass (same day):** fee-filtered uncached was 24s. Two changes → ~8s: (1) btree indexes
on table 36's three peak-Saturday pricing columns (ids 0554cbcc/6c3e37fa/9da70ffd); (2) subqueries
add `output = ["VENDOR_ID"]` — the 24s was mostly hydrating thousands of FULL rows just to map IDs.
**Output syntax pitfall:** on a non-paged `return = {type:"list"}` query, output paths have NO
`items.` prefix (`output = ["VENDOR_ID"]`); using `items.VENDOR_ID` publishes fine but rows lose the
field → runtime `Unable to locate var: $this.VENDOR_ID`. Paged queries DO use `items.x`.
Remaining slowness (~4.5s uncached baseline) is the known main-query substring full-scan
([[search-map-n-1-is-not-the-bottleneck]]); fee filter adds ~3.5s via the ~3000-id `in?` list.
121 fee-filtered ~8s is mostly its forced auto-enrich N+1.

Snapshots: `.claude/xano_backups/119_v5b_perf_2026-07-07.xs` / `121_v5b_...` (live), `*_v4_*`
(pre-perf), `*_pre_maxfilter_fix_2026-07-07.xs` (original rollback). See
[[project_wptp_search_joins]], [[project_extracted_pdf_data_dedupe]].
