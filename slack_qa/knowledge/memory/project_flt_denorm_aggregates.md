---
name: project-flt-denorm-aggregates
description: "2026-07-08: flt_* denorm filter aggregates SHIPPED — 119/121 v6 filter on indexed table-11 columns; trigger + sync task maintain; Railway unchanged; parallel-session race hazard on 'continue'"
metadata: 
  node_type: memory
  type: project
  originSessionId: a3379777-f497-485d-a269-09c4048c4267
---

2026-07-08 (~06:15 UTC): **Denormalization shipped.** Endpoints 119 & 121 are at **v6**: advanced
filters read indexed `flt_*` columns on table 11 directly — the table-36 subquery + vendor-ID
`in?` list is GONE. Parity-verified vs v5e across 41 cases (38 exact, 3 expected cross-field
divergences where v6 is slightly MORE inclusive: vendor-level matching vs live's same-row
coupling, e.g. TX allinc+fee45k 170→177). Uncached: search fee-filter ~2.7-4.7s (was 7-24s),
map filtered ~2.6s (was 8s); cached 0.25s.

**Architecture:**
- Table 11 columns: flt_min/max_venue_fee, flt_min/max_ppfb, flt_min/max_fbmin (btree-indexed),
  flt_venue_offerings/types/attributes (text lists), flt_outdoor_ceremony (bool),
  flt_space_count, flt_synced_at. Semantics: "any row ever" over ALL table-36 rows (NO PDF_ID
  dedupe); mins are min-POSITIVE (0 if none); attributes = ";"-split trimmed tokens.
- Filter mapping: base_fee_max=X → flt_min_venue_fee>=1 && <=X; base_fee_min=Y → flt_max>=Y;
  lists via `overlaps?` (any-of, verified); outdoor "yes" → bool ==? true. Empty inputs →
  effective var stays null → null-safe op ignores. NO dual-path needed.
- Maintenance: function 21 `recompute_vendor_filter_aggregates(vendor_id)` (v2); table_trigger
  id 3 on table 36 insert/update (**syntax: `table_trigger name { table=".." actions=
  {insert:true,update:true} input{json new json old text action text data_source} stack{...} }`**
  — actions MUST be object literal); task 10 `sync_vendor_filter_aggregates` every 5 min heals
  flt_synced_at null-or->24h (400/run). Railway extract_core.py: ZERO changes.

**Pitfalls learned:**
- `$arr contains "yes"` in a var value expression silently yields false — use explicit foreach +
  conditional. (Also made 119's `has_outdoor_ceremony` enrichment false in v4-v5e; fixed in v6.)
- updateTableSchema = FULL REPLACE; times out client-side but applies progressively server-side —
  poll getTableSchema; it DROPPED trim validators on 5 table-11 text columns (open follow-up).
- Parallel curl backfill drives silently drop contiguous request bursts during DNS blips —
  verify by set-diff vs live results, never by exit codes.
- Cache purge lever: bump `cache_v` input DEFAULT (now 3 on both). Cache auth:false ttl 3600 both.

**Race hazard (from parallel session f559550a):** a "continue" prompt spawned a second background
session mid-build. Before making Xano changes on this project, check `getWorkspaceAuditLogs` for
creations/updates in the last ~10 min by hello@tulletogether.com; if present, another session is
live — stand down or coordinate. Pre-denorm endpoint snapshots also committed on branch
`xano-backups-pre-denorm-snapshots`.

**v3 (2026-07-09, session f559550a):** price aggregates (min/max fee, ppfb, fbmin) now sourced from
PRIMARY PDF rows only (table 10 `Current_Assignment == "Primary"`), fallback to all rows when no
primary data — fixes venues passing max-fee filters via cheaper supplemental/older PDFs (Arrow Park
V172: primary $27.5k, supplemental $13.75k, passed $15k cap). Categorical facets + space_count still
all-rows. Backfilled 8441 vendors via temp endpoint (deleted after; 3 of 29 parallel-curl pages
silently dropped — the known pitfall — caught by per-page count check and retried). NOTE: cache_v
default bump 3→4 was BLOCKED by permission classifier in auto mode; stale caches self-expired via
ttl 3600. Reassigning Current_Assignment in table 10 does NOT fire the table-36 trigger — only the
5-min heal task / endpoint 192 picks it up.

**Display alignment (2026-07-09):** endpoint 149 vendor/benchmarks now returns `vendor_value_min`
per field (primary-PDF flt_min_* for the 3 filterable price fields; min-positive across rows for
others) alongside unchanged `vendor_value` (max) — UI shows "from $X" so multi-space venues
(Boscobel V410: one primary PDF spanning $15k–$35k) stop looking like filter bugs. 149 gained
`cache_v?=2`. WeWeb "from $X" binding is Vivek's side.

**v4 (2026-07-16):** fn21 also syncs `Max_Capacity_Seated` into table 11, FILL-ONLY: max across
primary-PDF $price_rows, written only when computed>0 AND existing table-11 value==0 (legacy seed
capacities + manual dashboard edits never overwritten). Trigger fills new extractions instantly;
task 10's 24h heal backfills the rest. Context: guest-capacity filter reads table-11
Max_Capacity_Seated directly; old values were legacy seed data (NO prior backfill existed —
verified via transcript sweep); Description deliberately NOT denormed (runtime `pdf_description`
join in ep 119). The 2026-07 new-vendor import (~950 PDFs, table-10 ids ~10570-11014) extracts
alphabetically; "Capacity varies"/no-desc cards = not-yet-extracted vendors, not a sync bug.
Backup: `.claude/xano_backups/fn21_v3_pre_capdesc_sync_2026-07-16.xs`. Verified: V10120 0→120
written, V1000 existing 800 preserved.

**PENDING as of 2026-07-16 08:00 UTC:** extraction run STALLED at table-10 id 10567 (Rockwood
Manor, 07:17 UTC) — 447 new-import PDFs (ids 10568-11014, Rockwoods→Yosemite, all Drive links,
0 attempts) + ~506 older stragglers (mostly non-Drive links, auto-filtered) still unextracted.
Vivek to relaunch from Railway dashboard: DEFAULT row-range run, start 10568 (or 1), NOT
force-all — dedup by PDF_ID vs table 36 (extract_core.py:2160) makes re-runs duplicate-safe.
Once extracted, capacity/fees flow to table 11 via trigger automatically; description is
runtime-joined. "Capacity varies" cards on Venue Pricing = these not-yet-extracted vendors.

**Open:** trigger end-to-end test needs a synthetic table-36 insert (auto-mode blocked; needs user
approval or the next real extraction exercises it). Endpoint 192 `test_recompute_vendor_aggregates`
kept as manual heal lever until then. Remaining uncached latency = main-query text scan
([[search-map-n-1-is-not-the-bottleneck]]). Supersedes perf notes in [[project_maxfilter_fix_v4]]
(correctness fixes there still accurate). Backups: `.claude/xano_backups/*_pre_denorm_2026-07-08*`.
