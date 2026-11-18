---
name: project_pe_benchmarks
description: Free market-rate benchmark panel for Photography/Entertainment — Xano side SHIPPED 2026-08-02 (table 59 + fn49 + task 14 + ep207). Only the WeWeb section is left.
metadata: 
  node_type: memory
  type: project
  originSessionId: 78f47256-3c8d-43c8-b4cf-a251c544931a
  modified: 2026-08-14T05:32:32.026Z
---

2026-08-02. **Xano side shipped and verified.** Remaining work is entirely in WeWeb.

## What it is
A free panel on the vendor detail page: "What photography costs in New York" — medians by
package type and coverage band, with p25–p75 and sample sizes. Entertainment gets the same keyed
on `Service_Type`.

Product decisions (user, 2026-08-02): **market rates only** — no "this vendor is X% below
median", no percentile. **Fully free**, not paywalled. **Precomputed nightly**, not aggregated per
request. Add-on price rollup from `Addons_Detail` was explicitly deferred.

## Pieces
- **Table 59 `PE State Benchmarks`** — one row per bucket. `bucket_key` = `Category|State|Dimension|Bucket`
  exists because `db.add_or_edit` matches on ONE field. Upsert (not truncate+insert) so the live
  endpoint never reads an empty table; orphans swept at the end of each run.
- **fn49 `rebuild_pe_benchmarks`** — the rollup. Takes `dry_run`.
- **task 14 `precompute_pe_benchmarks`** — nightly 09:20 UTC, just calls fn49 (mirrors the
  task 9 / fn20 `state_benchmarks` split).
- **ep207 `GET /pe_benchmarks?category=&state=&state_fallback=`** — public, cache ttl 43200,
  `cache_v` default 3.

## Which market the panel describes (v3 — decided with user 2026-08-02)
Cascade: **`state` (the market the COUPLE is browsing — same var feeding ep119's `State_Input`)
→ `state_fallback` (the vendor's raw table-11 State field) → `__NATIONAL__`.**
Both params accept a comma list and are split server-side; tokens are tried left to right and the
first with a real state-scoped sample wins. `resolved_state` is returned — title the panel with
that, NEVER the raw State field (it may list four states).

Why the couple's filter takes priority: the vendor field's order is arbitrary, so
`"Connecticut, New Jersey, New York"` self-resolves to Connecticut ($4,780, n=121) for what is
almost certainly a New York vendor. Binding to the browsing state also keeps the benchmark
IDENTICAL across every vendor in one comparison set, which is the only way the number is useful.
`International` is a real market (68 pkgs, median $6,100); junk tokens like `US` never have
buckets and are skipped automatically.

## Semantics worth not re-deriving
- Sample floor **5 packages / 3 vendors** for a STATE bucket; below that it is not written and
  the endpoint substitutes the `__NATIONAL__` row, tagging that row `scope:"US-wide"`.
  **Fallback is per-row**, not all-or-nothing — NY Entertainment legitimately had every bucket
  state-scoped except "Other Entertainment".
- `overall == null` is the contract for "render nothing" (Venue always returns this).
- Median, never mean — photography is $4,500 median against a $13,200 p95.
- Deduped by `Package_UID` with `sort id desc`, so newest extraction wins (table 36 appends).

## Two bugs found by reconciling against ep119's own facet counts — do this check on any rebuild
1. **State is comma-separated multi-value** — see [[reference_table11_state_multivalue]]. Cost a
   27% undercount of the NY sample. Now fans out over every token.
2. **`Currency == "USD"` dropped 55 priced rows** whose Currency was `""` (legacy, pre-column)
   while catching nothing: ZERO priced P/E rows carry a foreign code, because the extractor's
   currency guard already zeroes those and `>= 1` then drops them. Filter is now
   `Currency in ["USD", ""]`.

After both fixes the published sample sizes track ep119's facets to within 1–7 packages
(~2%, the unpriced residual). Before: 157 vs 216.

## Measured (2026-08-02, 1,866 priced rows / 686 vendors / 99 buckets, ~5s)
NY photography: overall $4,700 · Wedding Day $5,500 · Engagement $800 · Elopement $1,800 ·
6h-or-less $2,010 · 7-10h $6,000 · 11+h $8,000.
NY entertainment: overall $2,850 · **DJ $2,750 vs Live Band $9,500** (the most striking contrast
in the dataset) · Solo Performer $900 · Ensemble $1,800.
National photography $4,625 — consistent with the $4,500 recorded pre-existing.

## v4 2026-08-13 — top-level `headline` object (SHIPPED, additive)
`overall` blends every package type and is the WRONG number to lead with. NY Photography:
overall p25 2500 / median 4700 vs **Wedding Day p25 3500 / median 5500** — Wedding Day is 75% of
the pool, so the blend runs $800 low on the median and **$1,000 (29%) low on p25**, which is the
figure a couple reads as "the budget end".

New key `headline` = `{basis, bucket, scope, n_packages, n_vendors, p25, median, p75, label,
sub_label, range_label}`, appended last; **every pre-existing key keeps its name, meaning and
position** (verified against the live response). All four strings are prebuilt server-side.
Selection: Photography → `by_package_type` bucket "Wedding Day" when `n_packages >= 5`
(`basis:"wedding_day"`), everything else → `overall` (`basis:"all_packages"`, `bucket:null`).
The market is named from the ROW ACTUALLY USED, not `resolved_state` — fallback is per-row, so a
state can have its own `overall` while its Wedding Day figure comes from the national pool.
`cache_v` default 3 → 4. Backups `ep207_pre_headline_` / `ep207_v4_headline_`.

**Entertainment with NO `service_type` input is still left on `all_packages`** (user decision): it
has no `package_type` dimension, and `service_type` runs DJ ~$2,750 to Live Band ~$9,500 with no
bucket above 40% of packages, so promoting one SIGHT UNSEEN would be an arbitrary editorial choice
dressed as a statistic. **Superseded only for the case where the caller names the vendor's own
service type — see v5 below.**

## v5 2026-08-14 — optional `service_type` input (SHIPPED, additive, `cache_v` 4 → 5)

Kate's review: the Entertainment panel led with a **$900–$9,500** span (WeWeb computes it as
min-to-max of the `by_service_type` medians — Solo Performer to Live Band), which is a range across
products nobody chooses between. Her ask: lead with the median for whatever the vendor is tagged as.

**This does NOT contradict the v4 decision.** That objection was about picking a bucket with no
vendor context; it still stands there. Naming the vendor's own product is not arbitrary.

New optional `text service_type`, hoisted to `$var.eff_service_type`. Selection order is unchanged
Photography wedding_day → **v5 service_type** → `overall`. Basis `"service_type"`, `bucket` = the
resolved bucket. All three headline strings branch on it. New echoed key `service_type_input`.
Backups `ep207_pre_servicetype_` / `ep207_v5_servicetype_`.

**THE TRAP, AND THE WHOLE REASON THIS NEEDS AN ALIAS MAP — the two vocabularies do not match.**
Measured over 666 entertainment vendors (2026-08-14):

| table 11 `Type_of_Entertainment` (vendor tag) | table 36 `Service_Type` (bucket) |
|---|---|
| DJ 341 | DJ 989 ✔ |
| **Band 171** | **Live Band 594** ✘ |
| Ensemble 100 | Ensemble 372 ✔ |
| **Soloist 96** | **Solo Performer 298** ✘ |
| **Other 11** | **Other Entertainment 149** ✘ |
| blank 58 | — |

Only 2 of 5 agree verbatim, so without `SERVICE_ALIAS` it would look like it works — for DJs and
Ensembles — and silently fall back to the blend for everyone else. **101 of 666 tags are
comma-separated**, which is why the first token that *resolves* wins, not simply the first token.

Verified live, NY/Entertainment: DJ→$2,750 n=89 · Band→Live Band $9,500 n=201 · Ensemble→$1,800
n=120 · Soloist→Solo Performer $900 n=96 · Other→Other Entertainment $1,595 n=7 · `"Band, DJ"`→Live
Band · `"Trombone"`→all_packages fallback. Backward compatible: no input → Entertainment
all_packages $2,850, Photography still wedding_day $5,500.

### WeWeb: the panel IS already built and already headline-bound — ONE parameter is missing

Page **Other Vendors Page** `d1d9bbf6-7895-422f-9e4c-44bc095eb857` (`vendors/{VendorID}-copy`),
panel root `eca970f0-8daf-41c8-af79-d4531df4b1c1` ("Inputs Card", rendered when
`collections[46a2fbe1].data.overall != null`). Collection **`46a2fbe1-9757-4d3c-b09a-14276f92f740`**
"Photography and Entertainment Benchmarks".

Seven elements already read `headline`: `aba1c388` Median Price · `ab958701` Median Tag Label ·
`1ba4c0e3` Total Budget Label · `2667ddb4` Explanation Text · `8090163d` P25 · `6e73a22d` Median
Center · `87f2359a` P75 · `b2003ec0` Track Section (conditional). The `$900–$9,500` span is a
deliberate CLIENT-SIDE OVERRIDE gated on
`h.basis === 'all_packages' && medians.length >= 2 && high >= low * 2`.

**So v5 needs no element edits at all — `basis:'service_type'` makes that override go false and all
seven fall through to the server strings.** Do NOT patch the elements client-side; it would mean
duplicating the alias map seven times.

**THE ONLY REMAINING STEP (no MCP tool exists for it — WeWeb collections are deprecated and none of
the integration tools edit their parameters, so it must be done in the editor):** add a fourth
parameter to collection `46a2fbe1`:
`service_type` ← `variables['75987501-755f-4dad-92c9-4999b0d47e5d']?.['Type_of_Entertainment']`
(same `vendorSelection` variable that already feeds `state_fallback`). Empty/absent is safe — it
reproduces v4 exactly.

Verified what flips, NY Entertainment:

| element | today | with the param |
|---|---|---|
| Median Price | `$900 - $9,500` | **`$2,750`** |
| Median Tag Label | `TYPICAL RANGE BY SERVICE TYPE` | `Median DJ package — New York` |
| Total Budget Label | `…513 entertainment packages from 136…` | `…89 DJ packages from 46 New York entertainers` |
| Explanation Text | "depends mostly on what you book…" | `Half of DJ packages cost less than $2,750, half cost more` |
| P25 / Median / P75 | `$1,400 / $2,850 / $8,000` | **`$2,100 / $2,750 / $3,500`** |

That last row is a bonus fix: the percentile track under the headline was showing the BLENDED
spread, which did not correspond to the figure above it.
Keep the `by_service_type` table below as supporting detail — it is useful, it just should not lead.

Verified live: NY/Photography → wedding_day, 3500/5500/8500, n 559 from 173 · Vermont/Photography
→ scope US-wide, wedding_day, median 4500 · NY/Entertainment → all_packages, bucket null ·
Beauty → `overall` null so `headline` null (render-nothing contract).

**⚠ THE METADATA API ROUND-TRIP MANGLES NON-ASCII IF YOU DECODE THE RESPONSE CARELESSLY.** ep207's
comments contain em dashes (U+2014) and box-drawing (U+2500); a PowerShell `Invoke-WebRequest
.Content` read returned them as `â` + U+0080 + U+0094, and pushing that copy back would have
corrupted every comment in the file. Read with an explicit `StreamReader(..., UTF8)` and push the
body as raw bytes. Verified the em dash survives end to end in the live `label`.

## LEFT TO DO — WeWeb only
One REST collection on the vendor detail page, three params, NO formula and NO workflow (the
params are bound to page data, so it refetches when the vendor resolves):
`category` ← `vendorSelection.Category`, `state` ← the state-filter variable,
`state_fallback` ← `vendorSelection.State` (raw). Render nothing when `overall` is null. Check
`scope` PER ROW, not once. Print `n_packages`/`n_vendors` — the sample size is the credibility
claim and doubles as the crowdsourcing pitch. Call it "typical", never "average".

Build spec + mockup artifact: https://claude.ai/code/artifact/7abf5704-c986-40c6-a758-e4263628aeca

See [[project_package_grain_filters]], [[reference_extraction_pipeline]].
