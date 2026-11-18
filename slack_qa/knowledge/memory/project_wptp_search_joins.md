---
name: project-wptp-search-joins
description: Extending Xano endpoints 119 + 121 with joined Extracted PDF Data + venue_pricing fields. 119 fully enriched and live as of 2026-05-18 04:19 UTC. 121 still on base rollback (needs a bulk-fetch pattern).
metadata: 
  node_type: memory
  type: project
  originSessionId: 327d9694-3b5d-4d90-8d1e-e8b64eaa0e95
---

Extending `wptp_updated_mappings_search` (api 119) and `wptp_map_light` (api 121) in workspace 1, apigroup 10 ("WeWeb Transparency Project") with summary fields from `Extracted PDF Data` (table 36) and `venue_pricing` (table 37).

**Why:** Frontend wants filters on capacity, pricing ranges, venue type/offering/attributes without ballooning map payload.

## 2026-05-20 update — `Country_Input` filter added to both endpoints

Both 119 and 121 now accept an optional `text Country_Input?` input (trim filter). When empty, behavior is identical to before (verified). When populated, an extra `&& $db.WPTP_Updated_Mappings.Country ==? $input.Country_Input` clause restricts to exact-match rows. Endpoint 121 now also returns `items.Country` in its output (119 already did).

Rollback snapshots saved at `.claude/plans/rollback-119-pre-country.xs` and `.claude/plans/rollback-121-pre-country.xs` in case the change ever needs reverting.

Data note (corrected 2026-05-20): out of 6,392 rows in `WPTP Updated Mappings`, **710 have a populated `Country` value** (~11%) — NOT 38. The 38 number was rows matching exactly `"United States"`, which is just one of many distinct values present. The other ~672 populated rows hold other countries and likely string variants (`USA`, `US`, trailing-space versions). Before the frontend uses this filter, surface the distinct Country values (e.g., via a `db.query` distinct or just paging through populated rows) so it knows the canonical strings to send. **Why this matters:** the `==?` operator does case-sensitive whole-string match, so `"USA"` won't match `"United States"` — value normalization on either the data side or the input side is required for the filter to behave intuitively.

## Current production state (2026-05-20 04:47 UTC, post-Country_Input)

- **119 (`wptp_updated_mappings_search`) — FEATURE COMPLETE**: 14 summary fields by default at ~230-280ms for page_size=12. Plus opt-in `extracted_pdf_data` and `venue_pricing` nested arrays gated by `include_pdf_data=true` / `include_pricing=true` inputs (use `|set_conditional:"key":value:$input.flag` to attach only when opted in; default response omits the keys entirely).
- **121 (`wptp_map_light`) — FEATURE COMPLETE WITH ENRICH GATE + 2500 CAP**: New `bool enrich?` input. Base WPTP query capped at `per_page: 2500` (returns `$model.items`, but response is still a bare list). Default (no enrich) returns the lean base shape (id, Vendor_ID, Collection, Name, lat, lng, Category, State, image_1.url) — **~500-700ms cold for typical regional bboxes (16-200 items)**. `enrich=true` adds the 14 summary fields and the opt-in `extracted_pdf_data`/`venue_pricing` nested arrays — **~400-1500ms cold for typical bboxes**. Cache hit ~50ms either path, 1h TTL. Wide continent-scale bboxes that hit the 2500 cap take ~4.5s default (foreach pass-through cost) — acceptable as a safety net; frontend should never need to query that wide. Frontend that doesn't yet use the enrichment fields just calls 121 unchanged and pays the baseline cost.

## CRITICAL XanoScript syntax lessons (from tonight's 9 production pushes)

### 1. `|map:$$.field` filter is broken — use `array.map` stack form

The docs show `[{value: 2}]|map:$$.value*2` works. In practice, on `db.query` results, `|map:$$.fieldname` returns `[null, null, ...]` even when the field is populated. Confirmed by diagnostic: same input gave `[null,null,null,null,null]` via `|map:$$.id` but `[6733, 6736, 6741, 3, 6730]` via `array.map ($pdf_rows) { by = $this.id }`.

**Use:**
```
array.map ($pdf_rows) { by = $this.FieldName } as $values
var $aggregate { value = $values|unique }   // or |min, |max, |count
```

**DO NOT use:** `$pdf_rows|map:$$.FieldName|unique`

### 2. `|get:` with dynamic-expression key crashes at runtime

Per docs, `|get:` takes a string literal: `{'fizz':'buzz'}|get:'fizz'`. Drafts that used `$pdf_by_vendor|get:$$.Vendor_ID` parsed cleanly but `500 fatal` at runtime. **Use foreach + per-item `db.query` instead** of `array.group_by` + `|get:`.

### 3. `var.update` inside `conditional` DOES update outer-scope vars

Pattern that works:
```
var $min_field { value = 0 }
conditional {
  if (`$space_count > 0`) {
    array.map ($pdf_rows) { by = $this.X } as $vals
    var.update $min_field { value = $vals|min }
  }
}
// $min_field is now usable; 0 if count was 0, else $vals|min
```

### 4. `db.query` with `return = {type: "list"}` (no paging) returns a bare list, not a wrapped object. With `paging:{...}` it returns `{items: [...], itemsTotal, curPage, ...}`.

### 5. `|set:` accepts multi-line pipe chains

```
var $enriched {
  value = $vendor
    |set:"a":$a
    |set:"b":$b
}
```

## The working enrichment pattern for 119 (current live shape)

```
db.query "WPTP Updated Mappings" { ... paging ... } as $model
var $enriched_items { value = [] }

foreach ($model.items) {
  each as $vendor {
    db.query "Extracted PDF Data" {
      where = $db.Extracted_PDF_Data.VENDOR_ID == $vendor.Vendor_ID
      return = {type: "list"}
    } as $pdf_rows

    db.query "venue_pricing" {
      where = $db.venue_pricing.Vendor_ID == $vendor.Vendor_ID
      return = {type: "list"}
    } as $pricing_rows

    array.map ($pdf_rows) { by = $this.Pricing_Year } as $pricing_year_vals
    var $pricing_years { value = $pricing_year_vals|unique }
    // ... 4 unique-array fields total ...

    var $min_max_capacity_seated { value = 0 }
    // ... 8 zero-initialized min/max vars total ...

    conditional {
      if (`$pdf_rows|count > 0`) {
        array.map ($pdf_rows) { by = $this.Max_Capacity_Seated } as $cap_vals
        var.update $min_max_capacity_seated { value = $cap_vals|min }
        // ... etc for 4 numeric fields × 2 (min+max) = 8 updates ...
      }
    }

    var $enriched { value = $vendor|set:... 14 fields ... }
    array.push $enriched_items { value = $enriched }
  }
}

var.update $model { value = $model|set:"items":$enriched_items }
response = $model
```

## Performance (119)

- page_size=12 + full 14-field enrichment: **~230-280ms** warm. ~24 extra db.queries per request (12 PDF + 12 venue_pricing).
- Btree index `9cf4f0e3` on `Extracted_PDF_Data.VENDOR_ID` keeps lookups fast. Don't drop it.
- For 121 (map endpoint, up to 2000 items per cache miss), N+1 is too slow. Needs bulk fetch + per-item array filtering — design TBD. Probably: one db.query for all PDFs by `VENDOR_ID in $vendor_ids`, then per-item `array.find` or `array.filter`.

## Performance lesson (key takeaway)

When you need per-item enrichment from a related table with an indexed join key, **prefer N+1 foreach + per-item `db.query` over bulk-fetch + in-memory `array.filter`** in XanoScript. Counterintuitive — N+1 is the textbook anti-pattern in SQL — but in this runtime, btree-indexed single-key lookups are very fast while `array.filter` over a multi-thousand-row in-memory array carries enough overhead to lose the race. Confirmed on 121: 1.9s (N+1) vs 2.65s (bulk + column-limited filter) vs 3.45s (bulk full columns), all for 160-171 items.

## Gating worked via foreach-always-runs pattern (probe 11d, 2026-05-18 05:16 UTC)

### What worked

```
db.query "WPTP Updated Mappings" {...} as $model
var $output_items { value = [] }

foreach ($model) {
  each as $vendor {
    var $item { value = $vendor }   // start with bare vendor
    conditional {
      if (`$input.enrich == true`) {
        // do per-item enrichment db.queries + array.map + |set: chain
        var.update $item { value = $vendor|set:"space_count":$x|...all 14 fields... }
      }
    }
    array.push $output_items { value = $item }
  }
}

response = $output_items
```

The foreach runs unconditionally. With `enrich=false`, the inner conditional does nothing, so each iteration just pushes the bare vendor through. Cost on default path: small (~2-3ms per item) — much faster than full enrichment but slower than no-foreach baseline.

### What did NOT work (3 attempts burned)

1. **`var $output { value = $model }` + outer conditional gating foreach** — default returned literal `null` JSON.
2. **`var $output { value = [] }` + outer conditional with if/else (else does `var.update $output { value = $model }`)** — default returned `null`.
3. **`var $output { value = $model|safe_array }` + same structure as #2** — also returned `null` on both paths, even with the filter.

**Working theory:** when an outer-scope variable that holds a list is updated inside a `conditional` block (via `var.update`) OR initialized to another variable (`var X { value = $Y }`), XanoScript silently corrupts it to null. The foreach-always-runs pattern (probe 11d) avoids this entirely by never updating `$output_items` from outside a foreach iteration — only via `array.push` from inside.

### Performance cost of the gate

- Pre-gate baseline (no foreach at all): ~50-130ms cold
- Probe 11d default (foreach runs, no-op body): **~100-400ms cold** depending on item count
- Probe 11d enrich=true: ~1.1-1.5s cold (same as always-on used to be)

So the "lean" path now costs ~2-3ms per item for the foreach pass-through. Not free, but ~5-10x faster than always-on enrichment, and cache hits are ~50ms regardless.

### Cache stickiness gotcha (important)

**Pushing a new XanoScript via `updateAPI` does NOT invalidate the 1h response cache.** Cached entries from previous (potentially broken) deploys keep serving until their independent TTL expires. During tonight's debug, several test bboxes ended up with cached `null` responses from probe 11c that persisted across the rollback push. Two ways to handle:
- Wait 1h (the entries expire individually).
- Set `cache.active: false` temporarily, push, then push again with `cache.active: true`. (Untested.)
- For testing during development, always use a unique floating-point bbox per probe attempt.

## Next steps (no immediate work pending)

Feature is complete on both endpoints. Possible future tweaks:
- If 121 cache miss latency becomes a UX issue, gate enrichment behind explicit `enrich=true` input flag so default map calls stay at baseline ~50ms.
- `array.filter` runtime form `array.filter ($arr) if (\`predicate\`) as $out` — confirmed working syntax (parens around input, backtick'd predicate).
- `|set_conditional:"key":value:condition` works to attach keys only when condition is truthy.

## 2026-05-29 — Advanced filters v3 shipped on 119 + 121 (verified live 2026-05-30)

Both endpoints gained **7** optional advanced-filter params (the planned `vibes` was NOT shipped — deferred to v2). All exact-match, all no-op by default:
- `pricing_models text[]` → `Venue_Offering in?` (skip when empty)
- `venue_types text[]` → `Venue_Type in?` (skip when empty)
- `outdoor_ceremony text` → `Outside_Ceremony_Space ==? "yes"` (skip when `""`). **It is TEXT, not bool** — sending boolean `false` becomes `"false"` and matches zero rows. Frontend must send `"yes"`/`""`.
- `base_fee_min/max int` → `Venue_Fee_on_a_Peak_Season_Saturday >=?/<=?` (skip when 0)
- `fb_per_person_min/max int` → `Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday >=?/<=?` (skip when 0)

Mechanism: pre-query sub-select on table 36 → `array.map by VENDOR_ID |unique` → `$advanced_filter_vendor_ids`, AND'd into main WHERE via `Vendor_ID in? $advanced_filter_vendor_ids`. Gated by a multi-conditional `$advanced_filter_active` flag + nullable `$effective_*` vars (v3 preprocessing pattern — earlier inline-backtick attempt caused a ~3min outage, rolled back via `.claude/xano_backups/119|121_pre_advanced_filters_2026-05-29.xs`). 121 auto-forces `$effective_enrich=true` when any advanced param is active.

**Capacity semantics:** 119 `max_capacity` does `Max_Capacity_Seated >=? max_capacity` — i.e. "seats AT LEAST N", despite the param name. UI label "MIN capacity" is correct.

**`has_outdoor_ceremony` IS in the response** (per-card, `value = $oc_vals contains "yes"`) — the old frontend brief claiming it was cut is STALE. Correctness unverified (only 6 "yes" rows in table 36).

**Empty-intersection fix shipped 2026-05-30 (both endpoints):** previously the final clause `Vendor_ID in? $advanced_filter_vendor_ids` skipped itself when the matched-ID list was empty, so an active filter set matching zero rows returned the FULL list. Fixed with a sentinel: inside `if ($advanced_filter_active)`, after `$advanced_filter_vendor_ids = $ids_raw|unique`, added `conditional { if (($matching_rows|count) == 0) { var.update $advanced_filter_vendor_ids { value = ["__NO_MATCH__"] } } }`. Now empty match → 0 results. Verified no regression. Backups: `.claude/xano_backups/119|121_pre_emptyfix_2026-05-30.xs`. Live URL base: `https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B/` (auth:false, can curl directly for testing). `getApiRequestHistory query_id=119|121` shows actual frontend calls (great for debugging WeWeb param serialization).

**Frontend-mapping gotchas (the live WeWeb panel got these wrong as of 2026-05-30):** endpoint matches exact strings, so option labels must be copied verbatim from `extract_core.py` prompts (repo `tulle_vendor_data_updates`) — `Hotel / Resort` not `Hotel/Resort`. Pricing Model has only 3 real values (Raw Space, Semi-Inclusive, All-Inclusive — no "Full Service"/"Rental"). Venue_Type = 14 canonical; Venue_Attributes = 14 canonical. `text[]` params must serialize as a true list, not a comma-joined string. Basic-param casing differs: 119 `Search_Input`/`State_Input`/`Category_Input`; 121 `search_input` (lowercase), no State/Category (uses bbox). Full mapping spec: `C:\Users\vivek\.claude\plans\take-a-look-at-cuddly-pinwheel.md`. Only ~37 of 6187 rows populated (backfill pending) → advanced filters return small sets by design.

## 2026-05-31 — Sorting added to 119 (name + capacity), grid only

Added `text sort_by?` input to 119 (`name_asc`/`name_desc`/`capacity_asc`/`capacity_desc`; empty = default id asc). Fee/F&B/venue-type sorting DEFERRED (needs denormalizing per-vendor aggregates onto table 11 via the pipeline — fee/F&B live only as post-pagination foreach aggregates, can't drive a DB sort).

**XanoScript constraints discovered (important for any future dynamic query work):**
- `sort = $variable` is REJECTED ("Invalid kind for sort - assign:var"). Sort must be a literal `{Table.Field: "asc"|"desc"|"rand"}`.
- You CANNOT assign the same var twice (`as $model` in two places, including across if/else branches or nested conditionals) → "Invalid repeating block2: assign:var". The documented if/else-same-var pattern does NOT work in this Xano version. So query-branching-by-sort is impossible.
- `response = $x` must be OUTSIDE the `stack {}` block (sibling of stack), not inside.
- Building an object via `{}|set:...` returns null. Mutate an existing object instead: `$model|set:"k":v` (like the existing enrichment).

**Solution = in-memory sort + slice (single query):** sort the returned array with the `|sort:"Field":"text"|"number":<ascBool>` filter (quoted args; chosen via conditional `var.update $sorted_rows` — safe, like `|unique`), paginate with `|slice:$offset:$page_size`, enrich the sliced page, rebuild the paging envelope by mutating `$model` with `|set:`. Offset math via `math.sub`/`math.mul` (in-place mutators); `pageTotal` via `math.div` then `|ceil`. **Bifurcated paging for perf:** sort_by empty → native paging (per_page=$input.page_size, fetch only the page, no regression); sort_by set → fetch all (per_page=10000, page=1) for a global sort then slice. `itemsTotal` from `$model.itemsTotal` (totals:true). Array filters confirmed: `|sort`, `|slice`, `|count`, `|ceil`, `|floor`, `|first`, `|last`, `|range`, `|reverse`. Backups: `xano_backups/119_pre_sort_2026-05-31.xs` (rollback), `119_sort.xs` (live). Probe pattern: create throwaway endpoint in apigroup 10, test, deleteAPI — safe way to validate XanoScript before touching live 119/121.

## 2026-06-02 — Favorites endpoint (74) given full enrichment (was bare)

`wptp_updated_mappings_favorites` (api 74, input `vendorIDs text[]`) previously returned RAW WPTP Updated Mappings rows with zero enrichment. Rewrote it to run the SAME per-vendor enrichment block as 119 (space_count, pricing_years, venue_types/offerings/attributes [filter_empty|unique], contact_information, pdf_description, capacity + fee/F&B/per-person peak-Sat min/max aggregates, has_outdoor_ceremony) so favorites cards render identically to search cards (incl. the venue-type color formula that needs `venue_types`). Added `include_pdf_data?`/`include_pricing?` bool inputs for parity. Kept the original `if vendorIDs>0 { db.query as $model } else { var $model {value=[]} }` base (bare list, no paging), then `foreach ($model)` → `array.push $enriched_items`, `response = $enriched_items`. No `output` limiter — returns full mapping fields + enrichment. No cache. Verified live (V1/V3/V5 enriched, bad id excluded, empty input → []). Backup: `.claude/xano_backups/74_favorites_pre_enrichment_2026-06-02.xs`.

## 2026-06-02 — `pdf_description` added to 119 (AI description from table 36)

119 already returned `Description` from the WPTP Updated Mappings table (often empty or junk like "View all rates on website"). Added a NEW enriched field **`pdf_description`** sourced from table 36's AI `Description` column (the one-sentence classifier output, ~78 rows populated, much higher quality). Same proven pattern as contact_information: `$pdf_rows|filter_empty:"Description"|first|get:"Description"`, then `|set:"pdf_description":$pdf_description` on `$enriched`. Distinct key so it doesn't clobber the existing `Description`. Verified live (e.g. V1 "Hotel in Brooklyn with Brooklyn Bridge... packages from $475/guest"). **Only added to 119** (the search/grid endpoint the user asked about) — NOT yet mirrored to 121; mirror with the identical block inside 121's enrich conditional if the map needs it.

## 2026-06-02 — Two new advanced filters shipped: `vibes` + `min_spend` (119 + 121)

Now **9** advanced filters. Both live + verified.

- **min_spend** (`int? min_spend_min?` / `min_spend_max?`) → range on `Food_and_Beverage_Min_on_a_Peak_Season_Saturday` (overall F&B minimum). Pure clone of the base_fee pattern: effective vars + `>0` conditionals set `$advanced_filter_active`, added `>=?/<=?` to the existing Extracted-PDF-Data sub-query. ~1,072 rows populated. Verified: 10k–30k → 429, ≥100k → 18, ≥50k → 78 (121).
- **vibes** (`text[] vibes?`, ANY/union) → `Venue_Attributes` (semicolon-delimited text; can't use `in?`). Implemented as a SEPARATE sub-query, kept OUT of `$advanced_filter_active` (else the structured sub-query would run all-empty and return every vendor):
  ```
  var $vibe_vendor_ids { value = null }
  conditional { if (($input.vibes|count) > 0) {
    var $vibe_ids_raw { value = [] }
    foreach ($input.vibes) { each as $vibe {
      db.query "Extracted PDF Data" { where = $db.Extracted_PDF_Data.Venue_Attributes includes $vibe; return={type:"list"} } as $vrows
      foreach ($vrows) { each as $vr { array.push $vibe_ids_raw { value = $vr.VENDOR_ID } } }
    } }
    var.update $vibe_vendor_ids { value = $vibe_ids_raw|unique }
    conditional { if (($vibe_vendor_ids|count)==0) { var.update $vibe_vendor_ids { value = ["__NO_MATCH__"] } } }
  } }
  ```
  Main query AND's `&& $db.WPTP_Updated_Mappings.Vendor_ID in? $vibe_vendor_ids` (null → skips when no vibes; intersects with structured filters when both active). 121 also forces `$effective_enrich=true` when vibes present. Verified: Waterfront→12 (all genuinely Waterfront), Ballroom→12, union→20, +All-Inclusive→1 (intersection), nonsense→0 (sentinel).
- **`includes`** (no `?`) is the substring WHERE operator for text; `overlaps`/`contains` need true array fields (Venue_Attributes is text, so they don't apply). Validated on a throwaway probe before patching.
- **text[] query-string serialization** (confirms the WeWeb gotcha): repeated `?vibes=A&vibes=B` (no brackets) → Xano parses only ONE element; must use `?vibes[]=A&vibes[]=B` OR JSON `?vibes=["A","B"]` for a true multi-element list. Frontend must bind vibes/pricing_models/venue_types as real arrays.
- Sparse data: only ~150 venues have Venue_Attributes, ~1,072 have a min-spend value — filters return small sets until backfill. Rollback snapshots: `.claude/xano_backups/{119,121}_pre_vibes_minspend_2026-06-02.xs`.

## 2026-06-02 — Empty values stripped from enriched aggregates (fixes "not sure" badges)

Symptom: filtering `pricing_models=["All-Inclusive"]` returned cards showing "not sure" and "semi-inclusive" badges. **The filter was NOT broken** — the sub-query correctly returns only vendors with an All-Inclusive row. The bug was the enriched `venue_offerings` array: it was `unique(Venue_Offering over ALL the vendor's table-36 rows)`, and since only ~159 of 6248 rows have a classified offering, almost every vendor's list came back like `["All-Inclusive",""]`. The frontend renders the empty string as "not sure". (Only 2 vendors — V5282, V4305 — genuinely have mixed offerings across spaces.)

Fix: changed `$venue_offering_vals|unique` → `|filter_empty|unique` on **both 119 and 121**, and did the same for `venue_types` and `venue_attributes` (same empty-pollution). `filter_empty` works pathless on a flat string array (`["a","","b",""]|filter_empty` → `["a","b"]`) — validated on a throwaway probe (apigroup 10) against V1's real rows before patching live. Verified post-deploy: all-inclusive filter on 119 → 0 results with empty offering (16/18 clean `["All-Inclusive"]`, 2 legit mixed); 121 same. `pricing_years` left untouched (not requested).

**Filter matching is case-SENSITIVE exact-match** (`in?`): `pricing_models=["all-inclusive"]` (lowercase) returns 0 rows; must send `All-Inclusive` verbatim. 121 still has the 1h cache (vary bbox to test fresh). Did NOT save .xs backups this time — change is a 3-token additive pipe, trivially reversible by removing `|filter_empty`.

## 2026-06-02 — Venue_Attributes made mutually exclusive from Venue_Type (vibes vocab now 10, not 14)

To keep a future `vibes` filter (`Venue_Attributes`) from overlapping the `venue_types` filter (`Venue_Type`), dropped 4 values from the **Venue_Attributes** vocabulary: `Estate / Mansion` (was an EXACT duplicate of a Venue_Type value), `Garden Setting`, `Vineyard`, `Barn` (conceptual dups of Garden / Botanical Garden, Winery / Brewery / Distillery, Barn / Ranch). Remaining 10 attributes are purely aesthetic/architectural: Historic Architecture, Rooftop / Skyline Views, Scenic / Nature Views, Waterfront, Ballroom, Industrial / Warehouse, Greenhouse, Natural Light / Large Windows, Tall / Vaulted Ceilings, Tented. Prompt fix in `extract_core.py` CLASSIFICATION_PROMPT (repo tulle_vendor_data_updates, commit bcd1759) + instruction not to restate the venue type as an attribute. **Category vocab was intentionally NOT touched** (it overlaps Venue_Type even more — deferred). When the vibes filter is finally built, its option list must be these 10 strings verbatim. See [[reference_extraction_pipeline]].

**Existing data backfilled (2026-06-02):** stripped those 4 tokens from 83 affected rows in table 36 via REST PATCH `extracted_pdf_data/{id}` (partial update — only touches the field sent; verified other fields untouched). **Gotcha: the auto-gen edit endpoint IGNORES an empty-string value** (`{"Venue_Attributes":""}` returns 200 but no-ops), so rows whose only attribute was a removed token couldn't be cleared via REST — had to use MCP `updateTableContentItem` to set them empty. 0 rows now contain a removed value.

## 2026-06-01 — `contact_information` exposed on 119 + 121

Both endpoints now surface a per-vendor `contact_information` string (the venue's business email) enriched from table 36's new `Contact_Information` (email type) column. Computed null-safely inside the existing per-vendor enrichment block:
```
var $contact_rows { value = $pdf_rows|filter_empty:"Contact_Information" }
var $contact_information { value = "" }
conditional { if (($contact_rows|count) > 0) {
  var.update $contact_information { value = $contact_rows|first|get:"Contact_Information" } } }
```
then `|set:"contact_information":$contact_information` on the enriched object (119 `$enriched`, 121 `$item`). On 121 it only populates when enriched (enrich=true or any advanced filter active), like the other summary fields. Verified live end-to-end: posted a temp table-36 row for V1 → both endpoints returned the email → deleted the row. `filter_empty:"<field>"` keeps only rows whose field is non-empty; `|first|get:"field"` is the null-safe scalar pick (don't `|strlen` a possibly-null email — filter first). 119 cache is off (instant); **121 cache is ON (1h TTL) — stale entries survive the updateAPI push** (see cache-stickiness gotcha above).

The email itself is written by the extraction pipeline ([[reference_extraction_pipeline]], `extract_core.py`) which only stores vendor/business emails — a prompt rule plus a hard `_to_vendor_email()` freemail/ISP denylist drop gmail/yahoo/hotmail/proton/etc. Existing rows are NOT backfilled — `contact_information` stays "" until each PDF is re-extracted. See [[reference_pricing_two_tables]].

## 2026-07-15 — State column cleanup (backfill) + multi-select states (deferred)

**Multi-select state filter for 119 — SHIPPED 2026-07-15 (input + query only; NO denormalization needed).** Added optional `text[] states` input. When non-empty: loop selected states, `db.query WPTP Updated Mappings where State includes <each>` (substring, so multi-state comma rows still match), collect row `id`s → `|unique` → `$state_vendor_ids`; main query AND's `id in? $state_vendor_ids`; empty-match sentinel `[-1]` so an unmatched state returns 0 (not the full list). Legacy `State_Input` is blanked (`$eff_state_single=""`) when `states` is active → backward compatible. Mirrors the `vibes` advanced-filter collect-IDs pattern. Bumped `cache_v` default 3→4. (Earlier I over-scoped this as needing a derived `flt_states` array + backfill + trigger — that's ONLY needed if you want EXACT match to kill the substring "Virginia matches West Virginia" / "Washington matches Washington DC" quirk, which stays as-is, same as the old single-select.) ep119 full backup: `.claude/xano_backups/ep119_pre_multistate_2026-07-15.xs`.
Verified live 2026-07-15: NY+NJ=894 (=NY 579 + NJ 315, venues are single-location so no overlap); single NY via multiselect == legacy State_Input NY (579); bogus state→0; International→704. **WeWeb TODO (user):** bind the multiselect array to the NEW `states` param (serialize as `states[]=A&states[]=B` or JSON, NOT comma-joined), leave `State_Input` empty/unbound. Endpoint ignores State_Input when states present.

**State data cleanup SHIPPED 2026-07-15 (in-place backfill only):** normalized 31 rows via `patchTableContentBySearch` on table 11 (exact `State ==` match — verified exact, counts didn't balloon): `"California "`→`"California"` (15), `"Texas "`→`"Texas"` (3), `"Massachussetts"`→`"Massachusetts"` (12), `"New Jersey, New York , US, International"`→`"New Jersey, New York, US, International"` (1). These were the only off-values among the 103 distinct; multi-state strings were already clean. Empty `""` left as-is (missing data, not fixable). NO maintenance trigger — future dirty entries won't auto-clean.

## Critical IDs

workspace_id=1, apigroup_id=10. Tables 11 (WPTP Updated Mappings, join key `Vendor_ID`), 36 (Extracted PDF Data, join key `VENDOR_ID` — ALL CAPS), 37 (venue_pricing, join key `Vendor_ID`).

## Stale assets

Draft files at `.claude/plans/xanoscript-{119,121}-draft.xs` use the broken `|map:$$.X` + `array.group_by` + `|get:$$.X` patterns. They DO NOT match what's live. Don't push them.

**Total production pushes this session: 23 (last push 2026-05-19 03:34 UTC, 2500-cap added).** Lesson: load `mcp__xano-meta__documentation` type=fs-syntax *and* push a diagnostic probe with multiple syntax forms before committing to a XanoScript pattern. Also: cache stickiness across pushes can mask real success/failure — vary the test bbox per probe.

When using `paging: {...}` on a `db.query` that's iterated by foreach, remember:
- `return = {type:"list", paging: {...}}` produces a wrapped object `{items: [...], ...}`, so foreach must use `$model.items` not `$model`.
- The `output = [...]` field paths need to be prefixed with `items.` (e.g., `items.id`, `items.Vendor_ID`) to extract just those columns from each item in the wrapped result.

**Full plan context:** `C:\Users\vivek\.claude\plans\do-you-have-access-mossy-seal.md`.
