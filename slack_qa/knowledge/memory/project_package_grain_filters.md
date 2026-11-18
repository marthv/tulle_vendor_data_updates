---
name: project_package_grain_filters
description: "P/E filtering moved from vendor grain to PACKAGE grain — ep119 v15 + table 36 columns + extract_core rewrite, all SHIPPED 2026-07-31. Only WeWeb rewiring left."
metadata: 
  node_type: memory
  type: project
  originSessionId: 17de7666-f7d7-468c-9385-12731f685b9f
  modified: 2026-08-03T01:31:03.992Z
---

## 2026-08-02 — ep119 **v16** SUPERSEDES v15 for Package Timing. Engagement/Elopement now WORK.

The WeWeb panel binds all five Package Timing checkboxes to one variable →
`pricing_models`. v15 pointed that at `Coverage_Band`, which is derived in code from
`Hours_Included` and can ONLY hold the three hour bands — so Engagement/Elopement filtered a
column that structurally could not contain them. The 2026-07-31 fix below (rerouting them to
`grid_venue_types`) was reverted by the user; v16 instead moves the DATA to match the binding.

- **table 36 + `Timing_List`** (list style) = hour band ∪ Package_Type when that type is
  Engagement/Elopement. Wedding Day/Other are never tagged (no such checkbox). A 5-hour elopement
  matches BOTH "Elopement" and "6 Hours or Less".
- **ep119 v16**: `Coverage_Band in? $pkg_hours` → `Timing_List overlaps? $pkg_hours`; facet tally
  reads Timing_List too (else the two options report 0 availability and get hidden); `cache_v=14`.
  Backups `ep119_v15_pre_timing_list_2026-08-02.xs` / `table36_pre_timing_list_2026-08-02.xs`.
- **fn48 `backfill_timing_list`** — pure derivation from existing columns, NO LLM spend,
  re-runnable, has `dry_run`. Wrote 1,135 rows.
- **extract_core.py** writes it going forward (`_TIMING_PACKAGE_TYPES`); `_sweep3.py` probes it.
  ⚠️ These edits are UNCOMMITTED in tulle_vendor_data_updates as of 2026-08-02.
- **Verified live**: all 5 values non-zero (NY 47/40/96/140/45; all-states Engagement 64,
  Elopement 61 — matching the numbers the other binding produced). `_sweep3.py`: no dead options
  across all 28 values. Venue + Entertainment unchanged.
- **Bonus repair**: 53 rows still held the pre-2026-07-30 band vocabulary ("10+ hours" ×30,
  "8 hours" ×14, "Up to 6 hours" ×8, "Full day / Unlimited" ×1). Re-derived from `Hours_Included`
  rather than remapped — "10+ hours" split 15→"11+ Hours" and 15→"7-10 Hours", so any static
  lookup table would have mis-bucketed half. P2088 re-extraction is still the proper fix.
- **WeWeb needs NO binding change** — the panel is already wired to `grid_pricing_models`, which
  v16 makes correct. Still open: the cosmetic Package Timing wrapper reading `grid_venue_vibes`.

Market-rate benchmark panel built the same day: [[project_pe_benchmarks]].

---

2026-07-31. **SHIPPED end to end.** Photography/Entertainment filters now evaluate at the
PACKAGE grain. Only remaining work is the WeWeb panel rewiring.

## The bug that started it
ep119 filtered table-11 `flt_*` aggregates, which fn21 computes independently per column
across ALL of a vendor's packages — so a vendor matched when the price came from package A
and the feature from package B. Reproduced live: V3961 returned for `base_fee_max=10000`
+ `vibes=["Second Shooter"]` on a $5,000 package that has no second shooter.

## What shipped
- **table 36 + 9 columns**: `Package_UID`, `Package_Type`, `Included_List`, `Addons_List`,
  `Offers_List` (list style), `Addons_Detail`, `Package_Notes`, `Vendor_Blurb`, `Currency`,
  plus btree `(Vendor_Category, Package_Price)`. Backup:
  `table36_pre_package_columns_2026-07-30.xs`.
- **extract_core.py** (`ad13fc3` + `fbd2f9a` on main): new vocab, both prompts rewritten,
  add-on capture, purge-before-insert, currency guard, `_PACKAGE_MAX_TOKENS = 16000`.
- **ep119 v15**: package pre-pass on table 36, `cache_v=13`. Rollback =
  `ep119_v14_price_fields_2026-07-27.xs`.
- **Re-extraction**: 453/455 PDFs, **$12.87** ($0.0283/PDF), 1,944 rows.

## Param routing (NO new ep119 inputs — WeWeb API config untouched)
| param | Venue | Photography | Entertainment |
|---|---|---|---|
| `base_fee_max` | flt_min_venue_fee | Package_Price | Package_Price |
| `venue_types[]` | flt_venue_types | Package_Type | Service_Type |
| `pricing_models[]` | flt_venue_offerings | Coverage_Band | **unused — hide the panel** |
| `vibes[]` | flt_venue_attributes | Offers_List | Offers_List |
| `fb_per_person_max`, `outdoor_ceremony`, `max_capacity` | venue only | suppressed | suppressed |

New vocab: act styles `DJ / Solo Performer / Ensemble / Live Band / Other Entertainment`;
ent inclusions `Ceremony Sound / Photo Booth / Monogram / Projection / Sound & Lighting
Provided / Special Effects`; photo offers = old 9 minus Travel Included plus
`Content Creation`; hour bands `6 Hours or Less / 7-10 Hours / 11+ Hours`; package types
`Wedding Day / Engagement / Elopement / Other`. Photography style DROPPED entirely.

## Traps that cost time — do not repeat
1. **`return = {type:"list", paging:{...}}` yields a paged ENVELOPE, not an array.** That is
   why the main query's output entries are prefixed `items.`. The first v15 attempt gave the
   pre-pass a paging block, so the `foreach` pushed nothing, `$pkg_vendor_ids` became
   `["__NO_MATCH__"]`, and EVERY P/E filter silently returned zero. Subqueries here
   (`$srows`, `$pdf_rows`) use `return = {type:"list"}` with no paging — match them.
2. **Venue-side `$eff_*` must be NULL for P/E** or the query double-filters against stale
   aggregates and returns zero. Verified by probing each param returns non-zero.
3. **`text Foo?` in Xano stores `""`, not NULL** (rehearsed on a scratch table). So a unique
   index on `Package_UID` would collide across ~24k venue rows — uniqueness is enforced in
   the writer instead. Same reason `Package_Price` can't be NULL: int/nullable:false/default:0,
   so the currency guard writes 0, which both readers already skip (ep119 `>= 1`, fn21 `> 0`).
4. **The POST endpoint needs no change when adding table-36 columns** — its input is
   `dblink { table = ... }`, a live schema reference. Verified: all 9 new columns accepted
   on the first POST.
5. **No purge endpoint needed** — existing public CRUD does it: GET `?vendor_id=`, then
   DELETE `/extracted_pdf_data/{id}` (ep140, no auth). Select rows in Python so it can log
   and refuse per row.
6. `Offers_List` = included ∪ add-ons, computed in the writer. Filtering it with ONE
   `overlaps?` avoids an OR across two columns and a second query.
7. Don't date-stamp freshness checks — a long run crosses midnight UTC. Key on
   `Package_UID` presence instead.

## Measured
Cold: Photography 0.78s, Entertainment 0.44s, Photography+facets 1.48s — vs venue's
pre-existing 2.5s. The P/E path is 3–6× faster than the venue path it sits beside.
Slider ranges: Photography $175 / median $4,500 / p95 $13,200 / max $28,000;
Entertainment $295 / median $2,900 / p95 $16,000 / max $26,995.
Fill: Photography Price 96%, Coverage_Band 90%, Package_Type 100%, Offers 95%, Blurb 100%.
Entertainment Price 93%, Service_Type 100%, Offers 84%, Blurb 100%.
Ent `Included_List` 73% vs the old 85% is the 13→5 vocab shrink, NOT a regression.

## Wins beyond the brief
- **Currency guard caught 6 vendors** (EUR 34 rows, AUD 10, GBP 6) whose foreign prices were
  being ingested as USD — Villa Regina's €850 sat in the filter as $850.
- **P875 was under-extracted by 60%**: the old band-size-centric vocabulary anchored the
  model on DJ-band tiers and missed a whole Strings/Jazz menu. Real entry price $900, not
  $6,995.

## WeWeb (Vendor Discovery, page 0d69d71b) — input side DONE, NOT PUBLISHED
The panels were ALREADY built with the new vocabulary. Two fixes applied via MCP
(owner access on workspace 836a9791 / project d830a133):
- **Engagement + Elopement wrote to `grid_pricing_models`** → routed to `Coverage_Band`,
  which only holds hour bands → **silent zero**. Rerouted to `grid_venue_types`
  (→ `Package_Type`), the same var Entertainment act style uses. Verified 0→64 and 0→61.
- Dead Hourly Cost slider hidden in all 4 P/E sections (`fb_per_person_max` is suppressed
  for P/E in v15).

**Not published** — user is waiting on team review. Xano v15 IS live, so until publish,
Engagement/Elopement still return zero for real users (unchanged from before, not a new
break). Still open: price chip → `matched_min_price`, description → `package_glance` /
`vendor_blurb`, slider maxes $30k → $13.2k/$16k, hide zero-count options, and a cosmetic
Package Timing checkbox wrapper still reading `grid_venue_vibes`.

**Reading a WeWeb page:** `getPageSemantic` on Vendor Discovery is 384k chars — it
overflows and gets written to a file. Grep that file; do NOT try to read it whole. It
lists binding *dependencies as UUIDs*, not formulas, so you cannot grep it for field
names like `matched_min_price`. `grid_venue_types` is `11c6a903-…`,
`grid_pricing_models` is `3ee27d1c-…`, `grid_venue_vibes` is `dc34164a-…`,
`ChosenCategory` is `c8cc3b96-…`. Filters DO reset on category switch (user-confirmed).

## ep121 map perf — v12 shipped 2026-07-31

**v12 stops forcing enrichment.** v11 had `$any_filter_active` override the caller's `enrich`
flag; the WeWeb "MapLight" collection passes NO enrich param, so that force was the ONLY thing
ever turning enrichment on. Measured over 4 samples, filtered NY-state view (633 rows):
**light median 1,819 ms / 223 KB vs enriched 5,507 ms / 530 KB — 3x faster, 58% smaller.**
Backup `ep121_v11_pre_enrich_unforce_2026-07-31.xs`; rollback = re-add one conditional, or set
`enrich: true` on collection `a5f0a42c-3799-45db-9ee4-98ef746659b7` (no deploy).

**Every field the grid card renders is already in the LIGHT output** (image_1, Name,
Venue_Type/flt_venue_types, State, Vendor_ID — v8 added the type fields). Enrichment only adds
15 glance fields nothing on the card displays. The grid IS bound to MapLight (`limit: 3`,
matching the 834 pages x 3 = 2,502 in the UI).

**Do NOT trust single timing samples on ep121** — the same cold query measured 2,848 ms and
4,888 ms minutes apart (~70% swing). Always take 3-4 and use the median.

**Unfiltered wide views were ALREADY light** (no filter -> no force), so v12 does nothing for
the ~10s national view. That cost is pure row volume: 2.4 ms/row at 1,163 rows rising to
4.0 ms/row at 2,500 (the cap). Remaining levers there, in order:
1. **Bbox rounding in WeWeb** (biggest, not yet done — no MCP tool edits collection params, and
   the bbox vars are set by the Mapbox element's bindings). Round OUTWARD so the box is always a
   superset and no pin is dropped: `Math.ceil(v*10)/10` on north/east, `Math.floor(v*10)/10` on
   south/west. Collapses thousands of cache keys; repeat views become ~0.2s.
2. Clustering above a zoom threshold.
3. The 2500 cap — but note **"2500 vendors found" in the UI is the CAP, not a real total**, and
   ep121 returns a bare array with no paging meta so truncation is silent. Lowering it makes the
   count wronger. Fixing the count needs `totals: true`, which is why it was left off.

## Open
- **P2088** (Natalie + Kate Studio, 50 packages) still on old unmatchable vocab — failed with
  `parse_failed (truncated?)`; the 16k fix is pushed, needs a re-run. P6446 genuinely has no
  packages, leave it.
- P/E still never appear on the map: ep121 hardcodes `Category == "Venue"`.
- `_sweep3.py` updated 2026-07-31 for the new vocab + per-category routing (it is
  **gitignored**, local only). Verified **no dead options** across all 26 values.

See [[reference_xano_mcp_quirks]], [[reference_xano_unsorted_query_drift]],
[[project_multicategory_extraction]].
