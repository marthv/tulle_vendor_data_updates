---
name: search-map-n-1-is-not-the-bottleneck
description: "Batching the per-vendor enrichment in wptp_updated_mappings_search (119) / wptp_map_light (121) does not speed them up — the main query's substring filters do"
metadata: 
  node_type: memory
  type: project
  originSessionId: c244346d-3aac-43fa-879b-2e6ff648387a
---

2026-06-19: Investigated the N+1 enrichment in `wptp_updated_mappings_search` (ep 119) and
`wptp_map_light` (ep 121) — each runs 2 per-vendor `db.query` calls (table 36 Extracted PDF Data +
table 37 venue_pricing) inside the enrichment `foreach`. Tried replacing with 2 batched `IN` queries
+ in-memory `lambda_filter` grouping, via a cache-off clone (`wptp_search_batch`) diffed against live.

**Result: DO NOT do this.** Verified with rigor (clone byte-identical to live across 26 cases once the
batched query's row order was matched by REMOVING the explicit sort — original per-vendor queries have no
sort, default order ≠ id asc), but head-to-head uncached timing showed the batched clone was **equal or
SLOWER** than live (e.g. Maine 2.4s live vs 2.7–4.4s clone). The XanoScript `|filter`/`lambda_filter` JS
per-row scan costs as much as or more than the indexed VENDOR_ID point queries it replaces.

**Real bottleneck = the main `WPTP Updated Mappings` query.** Proof: an empty-result search (0 vendors →
enrichment never runs) still takes ~1.6–3.1s. The query uses substring `includes?` filters
(`State includes "New York"` because State is multi-value like "New York, New Jersey"; plus
`Name/Address/State includes Search_Input`) which can't use the btree composite index
(State,Category,Name,Vendor_ID) → full scan of 7,774 rows every call. The endpoint's ~597ms avg in the
audit was cache-dominated; true uncached compute is ~2s.

If revisiting search/map speed, target: (1) the substring text filters — a Xano search/trigram index or a
denormalized indexed State-membership column; (2) the sort path in 119 fetches up to 10,000 rows into
memory (`eff_per_page=10000`) then sorts — push sort into the db.query instead. Both are bigger changes
than the N+1 swap and need the same clone+diff+timing rigor. See [[project_benchmarks_redesign]].
Map 121's main query is a lat/lng bbox (indexed) so it's fast (~199ms) unless enrich=true (rare).
