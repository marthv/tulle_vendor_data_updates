---
name: project_multicategory_extraction
description: "Multi-category (Photography/Entertainment) extraction — backend SHIPPED+DEPLOYED 2026-07-27; next session = run the pilot on Railway, then backfill, then WeWeb filters"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9657cfb4-9eb1-4b0a-96ed-3cc944fe3ad1
  modified: 2026-07-27T04:53:31.988Z
---

Extraction beyond venues. **Backend is COMPLETE, verified end-to-end, and DEPLOYED.**
Stopped here the night of 2026-07-27; nothing is half-applied.

## Where to pick up (in order)
1. **Pilot on Railway** — ~50 Photography + ~50 Entertainment via `run_chunk.py --pdf-ids`.
   Do NOT start a default full run: the gate is now open, so default mode would sweep in all
   698 new PDFs at once.
2. Score per-field fill rate, hand-check 10 per category, **drop any field under ~70%**.
   `Style` is the expected casualty — most photo pricing sheets never state one, and returning
   "" is correct behaviour, not a bug.
3. Backfill the 698 **in chunks** (~$40 sequential / ~$20 batch; measured $0.055/PDF).
4. WeWeb filters (see below).

## Deployed state
- Pipeline: commit `d5aca3d` on `main` of `marthv/tulle_vendor_data_updates`. Push required
  `gh auth switch --user marthv`, then switched back to vmarthi17.
- Xano live: table 36 +10 columns (`Vendor_Category` btree), fn21 v6, ep121 v11
  (`Category_Input`), ep119 v14 (exposes `flt_min/max_venue_fee`), ep177 (+`Type_of_Entertainment`).
- Backups in `tulletogether/.claude/xano_backups/`: `fn21_v5_pre_multicategory_*`,
  `ep119_v13_live_pre_pricefields_*`, `ep119_v14_price_fields_*`, plus golden ep119/121
  responses in `golden_2026-07-27/` for regression diffing.

## Gotchas for tomorrow
- **3 vendors already have package rows** from the local validation run: V3961 Weddings by Nato
  (16 rows), V815 David Perlman (1), V434 Bravura Weddings (1). Their `extraction_status` was
  NOT patched, so re-extracting them with explicit `--pdf-ids` will APPEND a duplicate set
  (table 36 appends, no dedupe). Default mode would skip them correctly.
- Nothing auto-extracts. Railway's main service is the Streamlit dashboard; `launch_parallel`
  is manual (`restartPolicyType = "NEVER"`); `batch_worker` cron only INGESTS, never submits.
- Xano task 10 (hourly :15) was mid-sweep recomputing all ~10.5k vendors with fn21 v6 — should
  be finished. It refreshes `pdf_description`/`contact_information` to the newest extraction
  catalog-wide. Expect changed (better) copy, not a bug.
- Backfill will fire the table-36 trigger once PER ROW (~2,500 fn21 recomputes, each rewriting a
  blob-heavy table-11 row). Chunk it.

## Design decisions worth not relitigating
- **No renames.** `flt_*` slots are venue-NAMED but category-generic, so non-venue data rides the
  existing ep119/121 path with zero new params/indexes: `flt_venue_types` = style/service type,
  `flt_venue_offerings` = coverage band, `flt_venue_attributes` = included services,
  `flt_min/max_venue_fee` = package price, `flt_min/max_ppfb` = hourly rate.
  `flt_outdoor_ceremony` stays venue-only — do NOT overload it.
- **fn21 v6 = coalesce, not a category branch**, and fn21 is the SINGLE copy (trigger 3 and
  task 10 both just `function.run` it — no inlined duplicates).
- Real categories are `Venue` / `Photography` / `Entertainment` — NOT DJ/Band. DJ-vs-Band is a
  Service_Type facet, seeded from `Type_of_Entertainment` as a FALLBACK only (extracted wins),
  covering 111/196 unambiguously.
- **Table 46 `photographer_pricing` exists and is empty** — deliberately unused; it has no path
  into fn21 → flt_* → ep119/121 so nothing there is filterable.

## WeWeb, still to build
Category tiles set `Category_Input`; DJ/Band tiles = `Entertainment` + a pre-applied
`venue_types[]`. Three multi-selects bind to `venue_types[]` / `pricing_models[]` / `vibes[]`,
relabelled per category, options+counts from the existing `include_facets` block —
**hide zero-count options** or sparse facets look broken.
**Bind the price chip to `flt_min_venue_fee`, NOT `min_venue_fee_peak_sat`** — the glance value is
a raw min including zeros and renders a false "from $0" (Nato: glance 0, real floor $5,000).

See [[reference_xano_unsorted_query_drift]], [[reference_xano_post_dropped_rows]] and
[[reference_xano_mcp_quirks]] §12 for three bugs this work surfaced and fixed.
