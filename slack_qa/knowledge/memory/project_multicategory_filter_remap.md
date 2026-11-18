---
name: project_multicategory_filter_remap
description: Photography/Entertainment filtering needs NO Xano work — fn21 v6 reuses generic flt_* slots; remap is WeWeb-only against ep119/121
metadata: 
  node_type: memory
  type: project
  originSessionId: 152bb8f9-8877-4da2-ab6f-7ab53f6e3636
  modified: 2026-07-29T03:25:48.057Z
---

2026-07-28. Verified live: **the Photography/Entertainment filter backend is already
complete.** No new table-11 columns, no fn21 change, no new endpoint inputs.

fn21 v6 reuses the existing venue-*named* but category-*generic* `flt_*` slots via per-row
COALESCE. ep121 v11's description documents this explicitly. Param → meaning:

| ep119/121 param | flt_ column | Venue | Photography | Entertainment |
|---|---|---|---|---|
| `venue_types` | flt_venue_types | Venue_Type | photo style | act type (DJ/Band/…) |
| `pricing_models` | flt_venue_offerings | Venue_Offering | Coverage_Band | Coverage_Band |
| `vibes` | flt_venue_attributes | Venue_Attributes | Included_Services | Included_Services |
| `base_fee_max` | flt_min/max_venue_fee | venue fee | Package_Price (floor ≤ input) | same |
| `fb_per_person_max` | flt_min/max_ppfb | per-person F&B | Hourly_Rate | Hourly_Rate |
| `outdoor_ceremony`, capacity | — | venue-only | HIDE | HIDE |

`Category_Input` = exact `Venue` / `Photography` / `Entertainment`. Filter values are exact
strings from `extract_core.py` vocab constants (COVERAGE_BANDS, PHOTO_STYLES,
PHOTO_INCLUSIONS, ENT_SERVICE_TYPES, ENT_INCLUSIONS) — code-enforced, spacing matters
(`Band - Solo / Duo`). Mismatch = silent zero, not an error.

**TRAP — `fallback_all=true` masks empty filter results.** On zero matches it re-runs
category-only AND drops the state scope, returning the whole category nationally (492
Photography / 194 Entertainment). A zero-match filter looks like a match-everything filter.
WeWeb must not send it while filters are active, or must read `is_fallback`.

**Gap:** ep121 (map) has **no `vibes` input** — Included filter is list-only.

**Prod endpoints are 119 + 121.** ep201/ep204 (`..._v1`/`_v2`) are
rimal@infiwebsolutions.com's optimization work — both have ZERO production request history.
ep204 is behind 119 (at v8: no include_facets/fallback_all/sort_by) but adds `min_capacity`
and `external_sorting`. Don't build against them; check with the user before assuming a
migration. See [[reference_xano_mcp_quirks]], [[project_wptp_search_joins]].

**20-PDF pilot results (2026-07-28, 19/20 ok, 69 package rows):**
- `Hourly_Rate` is **0/69 rows in BOTH categories** — the Hourly Rate slider
  (`fb_per_person_max`) is dead. Hide it. Photography uses `Overtime_Hourly_Rate` (50%)
  instead, which no filter is wired to.
- Photography `Service_Type` only **33% of rows / 4 of 11 PDFs**, and only ever
  `Documentary` + `Film / Analog`. Pricing PDFs mostly don't state a style. Entertainment
  `Service_Type` is **100%** because it is seeded from table 11 `Type_of_Entertainment` via
  ep177; photography has no equivalent seed (`Type_of_Photography` holds
  "Videography,Photography", i.e. service not style — not a usable substitute).
- Price ranges: Photography $550–$16,000 (median $4,500); Entertainment $395–$25,000
  (median $2,995). Set slider maxes accordingly, NOT the venue $100k.
- `Coverage_Band` 81% photo / 73% ent. `Included_Services` 78% / 100%.
- **`State_Input=""` returns national counts** on ep119 (492 Photography / 194 Entertainment)
  — useful for facets and "X vendors found" without a state.
- **`extraction_cost_usd` wrote 0 for all 20** — the BATCH path hardcoded `cost_usd=0` on
  every `_update_pdf_status` and results_log entry (sync path was always correct).
  **FIXED 2026-07-28 (uncommitted in tulle_vendor_data_updates):** `process_batch_results`
  now accumulates per-PDF `usage` from each batch result, applies `_BATCH_DISCOUNT = 0.5`,
  and writes real per-row cost + a batch total. Needs push by **marthv** + Railway redeploy.
- **PDF_ID ≠ Vendor_ID** in general (P5923 → V5574). Never derive one from the other; join
  through table 10.
- P874 root-caused: in the **batch submit loop**, download-failure / too-large /
  upload-failure each did a bare `continue` with **no status write**, so the row kept its
  stale June status+error, `attempts` never incremented, and it was indistinguishable from
  never-submitted. The sync path always wrote a status — batch-only bug.
  **FIXED 2026-07-28 (uncommitted):** all three now `_update_pdf_status(..., "failed", ...)`
  with the real reason. At 1-in-20 this silently dropped ~24 PDFs per 484-PDF run.

## The 464-PDF backfill ran WRONG on 2026-07-28 — ROOT-CAUSED + FIXED 2026-07-28 late

448 PDFs were extracted as VENUES and wrote blank table-36 rows marked `extracted`.
**The stale-flat-cache theory was WRONG** — the pre-2026-07-27 prefetch wrote a file named
`venue_vendor_ids`, NOT `vendor_categories`, so a flat-shaped `vendor_categories.json` could
never be read (`git show d5aca3d^:extract_core.py`). Two chained bugs, both now fixed in
commit **`49fb0d7`** of tulle_vendor_data_updates:

1. **Fail-open category gate.** `_fetch_vendor_categories` returned `{}` on any failure,
   `if vendor_categories:` skipped the gate, and `_category_of` defaults to `CAT_VENUE` — so
   all 464 took `MERGED_PROMPT` (`__m`) instead of the P/E prompt (`__k`). ep177 itself is
   healthy (10,559 rows, **bare JSON list**, no paging envelope), so the fetch failed
   transiently — consistent with the Xano 502 bursts, since the dashboard batch submit fetches
   wptp_pdfs (11,101 rows) + the summary table immediately before it.
2. **The masking bug.** `MERGED_PROMPT` returns the non-venue 2-field short-circuit *inside
   the `summary` key*; the merged-batch split did a bare `p1[pdf]=parsed["summary"]` with no
   detection, and `__non_venue__` was only ever produced by the SEQUENTIAL `_extract_summary`.
   So `_post_summary` mapped none of those keys, POSTed a row with only
   PDF_ID/VENDOR_ID/VENUE_NAME/last_extracted_at, returned `ok_s=1` → `extracted`.
   **Without bug 2 this would have been 448 × `skipped_non_venue` — loud and harmless.**
   Bug 2 also hit ordinary VENUE merged-batch runs. **Swept + CLEANED 2026-07-28: exactly 61
   blank rows across 58 PDFs** (dates 07-16 ×21, 06-26 ×21, 06-25 ×17, 06-04, 07-20), all
   table-11 `Venue`, all genuinely non-venue sheets (Pizzana Catering, SPECtacular PLAYCARE,
   Star of Honolulu Cruises). Rows deleted AND the 58 wptp_pdfs rows repatched
   `extracted` → `skipped_non_venue` (leaving them `extracted` was a lie that both hid them
   and blocked re-extraction via the dedup). Zero were Photography/Entertainment.
   **How to sweep table 36 for blank rows:** the metadata API `searchTableContent` IGNORES
   empty-string filters (returns all 26,250 rows) and the public `/extracted_pdf_data`
   returns only 3 rows without a `vendor_id` — so it must be a per-vendor sweep over all
   ~10,556 ep177 vendor ids at max_workers=3 (~40 min, aggregate in Python, never into context).

**Fix shape (49fb0d7):** `_fetch_vendor_categories` now three-state (`None`=endpoint unset →
filter disabled; `{}`=configured but unreachable/empty → **FATAL, abort**; dict=usable), new
`_load_vendor_categories` centralises the cache-then-live load, all three run paths +
`ingest_batch_by_id` abort with `vendor_categories_unavailable`, `_category_of` raises on a
non-dict entry, `_non_venue_sentinel` shared by both paths, and a `_has_content` /
`_blank_row_reason` blank-row guard at all four write sites (content-free write → `failed`
with a reason, never `extracted`).

**Remediation done 2026-07-28:** 447 blank rows deleted from table 36 (ids 25022–25500);
**id 25169 (P5620) KEPT** — real venue data, because table 11 miscategorises **V646**
(P5620 = Brooklyn Winery, a venue) as Photography → **flag for category review**.
(P5620 → V646: another reminder that PDF_ID ≠ Vendor_ID.)

**Miscategorised-venue candidates (table 11 Category is wrong):** V646 (Brooklyn Winery,
"Photography"), V3876 (Villa Regina, "Entertainment", extracted 30 Entertainment package
rows), V4488/P4488 (Curio Vendi — the P/E prompt self-caught this one as `rentals` →
`category_mismatch`, no rows written). Name heuristics are weak (bands named "…Club Band",
"Golden Estate Entertainment" are false positives); the reliable screen is the **surviving
Feb-2026 venue-extraction rows in table 36** — a real venue has genuine capacity / per-person
F&B / venue fee / Venue_Type there, a photographer has `Venue_Space_Name = "Not applicable"`.

**To reclassify a vendor to Venue:** patch table 11 `Category`='Venue' + clear
`Type_of_Entertainment`; DELETE its P/E table-36 rows (fn21 v6 coalesces per row, so mixing
corrupts the flt_* slots); re-run its PDFs via 🎯 Specific PDF IDs (venue path is automatic
once the category is patched, ~$0.055/PDF); the table-36 insert trigger + hourly task 10
recompute fn21.
Table-36 trigger 3 is `delete: false`, so deletes fire no `fn21` recompute.

**Re-run lists (untracked, in tulle_vendor_data_updates):** `pe_probe.txt` (5),
`pe_run1/2/3.txt` (148/148/147) = the 448. The other 16 of 464 are dead Drive links, zero
cost, left alone. Mix: 273 Photography / 174 Entertainment / 1 both. Must use dashboard
🎯 **Specific PDF IDs** (they are `extracted`, which is NOT in `RERUNNABLE_STATUSES`).
Probe verified live: log read `categories: 2 Entertainment, 3 Photography` and submitted
5 requests for 5 PDFs (= the `__k` single-prompt path, not the venue 3-pass).

## ✅ BACKFILL COMPLETE 2026-07-28 — 1,753 package rows live

448 PDFs re-run: **440 extracted / 5 category_mismatch / 2 failed / 1 partial**, total **$9.58**.
**1,753 P/E package rows across 441 PDFs** (1,075 Photography / 678 Entertainment).
Facet sweep now real (was ~2 vendors): 492 Photography / 194 Entertainment nationally.

**Fill rates — use these to decide which filters to ship:**
| field | Photography | Entertainment |
|---|---|---|
| Package_Name / Price | 100% / 99% | 100% / 99% |
| Coverage_Band (+Hours) | 92% | 63% |
| Included_Services | 95% | 85% |
| Team_Size | 85% | 91% |
| Service_Type | **17%** | **100%** |
| Overtime_Hourly_Rate | 37% | 12% |
| Hourly_Rate | **2%** | **4%** |
| Pricing_Year | 30% | 43% |

**Prices (set slider maxes from these, NOT the venue $100k):** Photography median $4,980,
p95 $13,000, range $250–25,000. Entertainment median $2,950, p95 $16,400, range $100–26,995.

**DEAD filter options (0 results) — hide them:** photography styles Traditional / Classic,
Light & Airy, Dark & Moody; Entertainment `10+ hours`. Photography style covers only ~8% of
vendors even where non-empty (Documentary 17, Film/Analog 16, Editorial 4, Fine Art 3) →
**drop the photography style filter.** `Hourly_Rate` is 2–4% → **the Hourly Rate slider
(`fb_per_person_max`) is dead, hide it.**

**"Are any of these actually venues?" — essentially NO.** Venue-signal screen over all 448
(scoring surviving Feb-2026 venue rows for capacity / per-person F&B / venue fee / Venue_Type)
returned exactly ONE: P5620/V646 Brooklyn Winery — and the P/E prompt self-caught it
(`category_mismatch: PDF looks like venue`, zero rows written). Name heuristics are worthless:
"Villa Regina" is an Italian entertainment AGENCY performing at Villa Regina Teodolinda
(30 rows, legit); "Dexter Lake Club Band", "Golden Estate Entertainment", "Red Barn Wedding
Studio" all false positives. Other 4 mismatches are out-of-scope categories, not venues:
P2215+P4488 rentals, P2691 other, P2903 planner.

## Two NEW bugs found by the backfill

1. **PDF-level `service_type`** broke multi-service vendors (P3876 Villa Regina: `Ensemble` on
   all 30 rows incl. its DJ and solo-piano tiers). **FIXED `5930aad`:** ENTERTAINMENT_PROMPT now
   returns `service_type` per package (top-level = fallback only) and states band size must match
   that package's performer count; `_build_package_entries` resolves per row —
   package value → PDF-level/`Type_of_Entertainment` seed → band size from that package's
   `team_size` → an "Ensemble" of 1 becomes "Soloist". PHOTO_PROMPT deliberately unchanged
   (style is genuinely one-per-vendor, and only 17% populated).
   Retro-patched **38 existing rows** Ensemble→Soloist (all literally "Solo Cellist/Violinist/
   Pianist" etc.) → Soloist 55→93, Ensemble 175→137. Zero band-size fixes were needed because
   `693173b` was already live during the backfill.
   **Latent bug found doing this: `_to_number` returns a numeric STRING** (for Xano posting), so
   comparing it to an int silently never matches. New `_team_int()` is the only correct way to
   read `team_size`; both call sites use it.
2. **Non-USD prices ingested as USD — STILL UNFIXED.** P4511 + P4515 (both "Laraudio",
   Spanish-language) each wrote `Package_Price` 234375, almost certainly MXN (~$13.7k USD).
   No currency detection anywhere in the pipeline. **Both rows deleted 2026-07-28** so the
   Entertainment max is back to $26,995, but the bug remains and undetectable cases
   (plausible-magnitude foreign prices) are surely still in there.

## Legacy venue rows on P/E vendors — CLEANED 2026-07-28

322 leftover Feb-2026 venue-extraction rows (`Vendor_Category=''`) sat on 320 P/E vendors.
**Measured against fn21's ACTUAL 18-column read list, not assumptions** — the real impact was
much narrower than "pollutes the price filters":
- 322 (all) inflated `flt_space_count` + `glance.space_count` by 1
- **0** polluted `flt_venue_types` / `_offerings` / `_attributes` (the old venue extraction of a
  photographer's sheet left those columns blank) — **0** set `flt_outdoor_ceremony`
- **1** polluted `flt_min/max_venue_fee` (V488 Cafe Wha? Band, $25,000)
- **5** fed a bogus `Max_Capacity_Seated`
- **fn21 does NOT read `Ceremony_Fee`** — 41 rows with a non-zero ceremony fee were harmless.
  Check fn21's `output = [...]` list before claiming a column pollutes anything.

All 322 deleted. Also had to: **zero `Max_Capacity_Seated` in table 11 for V149/V2114/V859/
V4337/V4341** — fn21's capacity sync is **FILL-ONLY and never clears**, so deleting the source
row does NOT undo the denormalized value. And **null `flt_synced_at` on all 320** so task 10
picks them up: deletes do NOT fire trigger 3 (`delete:false`) and task 10's staleness window is
**7 DAYS**, so freshly-synced vendors would otherwise keep stale aggregates for a week.
Verified via fn21: V488 space_count 4→3 + fee 25000→16000; V22 4→3.
7 vendors now have 0 table-36 rows (V1687, V2215, V2691, V2903, V4314, V4337, V4341) — all
failures/mismatches with no valid data, which is honest; they still list from table 11.

**`table 11 row id == int(Vendor_ID[1:])`** — verified 106/106 (`/wptp_updated_mappings_search`
returns both). Lets you bulk-patch table 11 without reading its blob-heavy rows.

**Enumerating table 36 per-vendor over the public API needs max_workers≈3 + 8-retry backoff**
— 12 threads produced 90/462 failures (silent undercount: 361 vs the true 447).

**Verified working from that run:** cost tracking ($11.54 / 448 rows = **$0.026 per PDF**,
~10x cheaper than the $109 guess) and the silent-drop fix (P874 now reports
`Download failed: Not a valid PDF`). 16 of 464 failed, **all dead Drive links, zero cost**
(3.4%; table-wide base rate is 1.7% = 189/11101).

**Working files in tulle_vendor_data_updates (untracked):** `pe_todo.txt` (464 to run),
`pe_all_ids.txt` (484), `pe_chunk1-3.txt`, `_pe_check.py`, `_pe_audit2.py`, `_sweep3.py`.
`_sweep3.py` hardcodes a `cache_v` base — **bump it each run** or you re-read the 7100s
response cache and get identical numbers (this fooled me once).

**Local DNS quirk:** `getaddrinfo` fails for `xqtb-2ma7-ijfy.n7e.xano.io` on this machine, so
**all `mcp__xano-*` tools error out**. `nslookup` resolves fine. Workaround for HTTP:
`curl --resolve xqtb-2ma7-ijfy.n7e.xano.io:443:34.170.63.102 …`. Metadata API stays
unavailable until it clears.

**Data state 2026-07-28:** only 2 Photography + 1 Entertainment vendor extracted. 916 PDFs
sit at `skipped_non_venue`, ~484 of them Photography/Entertainment and now in scope
(`RERUNNABLE_STATUSES` includes that status). Backfill is the USER's to launch. Non-venue
uses a single merged LLM call, not the venue 3-pass, so it is much cheaper than the venue
backfill. Until it lands, any filter collapses 225 → ~2 and will read as broken — use
`include_facets` to hide zero-availability options. See [[project_multicategory_extraction]].
