---
name: benchmarks-api-redesign-vendor-spaces
description: In-progress redesign of GET /vendor/benchmarks to return per-space data instead of collapsing to array_max
metadata: 
  node_type: memory
  type: project
  originSessionId: c244346d-3aac-43fa-879b-2e6ff648387a
---

The `GET /vendor/benchmarks` endpoint (API id 149, apigroup 10, "WeWeb Transparency Project" group) currently collapses all `Extracted PDF Data` rows for a vendor into a single `vendor_value` using `array_max`. A vendor can have multiple rows — one per venue space (e.g. "Garden Room", "Ballroom") identified by `Venue_Space_Name`.

The user wants to list all spaces and let the user flip through them in the frontend.

**Agreed proposed response shape (pending 3 sign-off questions):**

```json
{
  "vendor_id": "...",
  "state": "NY",
  "state_benchmarks": [
    ["Venue_Fee_on_a_Peak_Season_Saturday", { "min": 5000, "max": 28000, "avg": 14200, "count": 47 }]
    // same 6 fields, no vendor_value or percentile (those move per-space)
  ],
  "vendor_spaces": [
    {
      "id": 123,
      "space_name": "The Garden Room",
      "pdf_id": "pdf_abc",
      "fields": {
        "Venue_Fee_on_a_Peak_Season_Saturday": { "value": 12000, "percentile": 0.68 },
        "Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday": { "value": 150, "percentile": 0.72 },
        "Food_and_Beverage_Min_on_a_Peak_Season_Saturday": { "value": 18000, "percentile": 0.55 },
        "Ceremony_Fee": { "value": 2500, "percentile": 0.60 },
        "Admin_Service_Fee": { "value": 22, "percentile": 0.45 },
        "Max_Capacity_Seated": { "value": 300, "percentile": 0.80 }
      }
    }
  ]
}
```

**3 open questions to answer at start of next session before writing any code:**
1. Does this shape work for the WeWeb frontend, or is a different format needed?
2. If a space has no value for a field — show as `null` or omit the field entirely?
3. Should spaces with ALL fields null be filtered out of the list, or included?

**Why:** `vendor_value` and `percentile` move from `state_benchmarks` to per-space so each space can be independently compared against the state. Frontend renders first space by default, prev/next flips through `vendor_spaces`.

**2026-06-19 fix — divide-by-zero crash:** endpoint 149 returned `500 ERROR_FATAL "Infinite value."` for thin-data states (e.g. `Washington, DC` / V6997). Cause: `min`/`max`/`avg` ran on an empty array (`$updated_mapped_vals`) when a state cohort has no extracted pricing values → `avg = 0/0 = Infinity` → Xano can't serialize → fatal. The percentile calc was already guarded; min/max/avg weren't. Fix: wrapped them in `if (($updated_mapped_vals|count) > 0) … else null`. Pushed live. Verified V6997/DC → 200 insufficient_data, V1/NY → 200 with real stats. Lesson: every aggregation filter (avg especially) needs an empty-array guard.

**DC state-spelling fragmentation (separate backlog cleanup, not the crash cause):** `State` in WPTP Updated Mappings (table 11) is not normalized. DC is split: `Washington DC` (136 vendors), `Washington, DC` (26), plus combos like `Maryland, Washington DC`. The benchmark query uses exact `State == $state`, so each spelling is a separate cohort — V6997 only benchmarks vs the 26 comma-DC vendors. Also: commas in State are mostly LEGITIMATE multi-state service lists (`New York, New Jersey, Connecticut`) — 128 vendors / 46 distinct comma-values — so NEVER strip commas globally. See [[reference_pricing_two_tables]].

**2026-06-19 PERF: benchmarks now precomputed (was 1.1–2.5s/call → 20–80ms).** Root cause: ep 149 recomputed the whole-state distribution (scan + array-map over thousands of table-36 rows ×6 fields) on EVERY call; per-vendor cache key = always cold. Fix shipped:
- New table **`state_benchmarks` (id 52)**: one row per State (unique index on State), cols `pdf_count` + `benchmarks` json = array of {field_name,min,max,avg,count,values[]} for the 6 fields. `count`=non-empty values (percentile denom); `pdf_count`=cohort size (response `count`, matches old).
- New function **`rebuild_state_benchmarks` (id 20)**: optional `state` input (one state) else all; does the old aggregation centrally. Uses `db.add_or_edit` upsert on State.
- New nightly task **`precompute_state_benchmarks` (id 9)**, 08:00 UTC daily, calls the function.
- New admin endpoint **`admin/rebuild_state_benchmarks` (id 188, group 10)** = manual refresh; `?state=` for one, omit for all (~20s, 104 states). Call after big extraction loads.
- Ep 149 rewritten: reads the state_benchmarks row + this vendor's own table-36 rows, computes vendor_value (array_max) + percentile (filter_count values < value / count) live. Response shape IDENTICAL (verified V1/NY exact-match: Per_Person_FB pct 0.99210…, Ceremony 0.81002…). Falls back to insufficient_data if no state row.
- Also added standalone btree on **table 11 `Vendor_ID`** (was only in composite State,Category,Name,Vendor_ID → by-vendor lookups full-scanned 7774 rows).
- STILL TODO (WeWeb-side, config not code): Benchmarks collection fires TWICE per click (once w/o State, once with) and State has trailing-space variants ("Texas " vs "Texas") fragmenting cache — make it fire once with trimmed State.
- Per-user price ranking (estimate by guest count, sort most/least expensive) was DEFERRED by user on 2026-06-19; see plan ok-great-that-worked-soft-island.md.

**Xano context:**
- Endpoint: API id 149, apigroup_id 10
- Tables: `Extracted PDF Data` (table id 36), `WPTP Updated Mappings`
- Key field for labeling spaces: `Venue_Space_Name`
- Also available: `PDF_ID`, `id`, `VENUE_NAME`, `CATEGORY`
- 6 fields analyzed: Venue_Fee_on_a_Peak_Season_Saturday, Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday, Food_and_Beverage_Min_on_a_Peak_Season_Saturday, Ceremony_Fee, Admin_Service_Fee, Max_Capacity_Seated
- Stripe webhook group token: [redacted] (stripe_checkout group, NOT the api:v1keLQ2a base URL in CLAUDE.md)
