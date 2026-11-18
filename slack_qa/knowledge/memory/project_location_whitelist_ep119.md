---
name: project_location_whitelist_ep119
description: "SHIPPED 2026-08-11 — ep119 v23 gates vendors on table 61 (Vendor Whitelisting by Location) by filtering the v22 state-token list; zero cost, and unticking a cell is measurably FASTER not slower."
metadata: 
  node_type: memory
  type: project
  originSessionId: 8fc7a4aa-e04c-416e-a3f8-64ac549fb307
  modified: 2026-08-11T04:24:24.359Z
---

**SHIPPED + verified 2026-08-11.** ep 119 **v23**. Table **61 "Vendor Whitelisting by Location"**
(53 Location rows × 7 category booleans) now decides which vendors ep 119 returns.

**The whole trick: it is NOT a new predicate.** v22 had already collapsed state filtering to one
`flt_states overlaps? $state_tokens` term ([[project_flt_states_denorm]]). v23 filters *that token
list*. Three gated effects, all behind `wl.denied > 0`:
- states selected → denied tokens dropped before the existing `overlaps?` (all denied → the v22
  `__NO_STATE_MATCH__` sentinel)
- no states → `$state_tokens` becomes the allow-list; **this one var feeds BOTH the main and facet
  queries**, since both read it downstream
- `fallback_all` needs its own `overlaps? $wl_fb_tokens` because it drops all state scope

**Semantics = location-scoped, not vendor-intrinsic** (user's call). A vendor serving NY+TX with
Venue allowed in NY but not TX shows under NY, is hidden under TX, still shows in the national
browse. **Fail open** everywhere: unreadable/empty table 61, a category with no column, or a
location token with no row all mean "allowed".

**Resolution keys on the REQUEST category (`Category_Input`), never the row's own `Category`** —
ep 119 already matches Category exactly, so table 11's comma-valued rows never enter the logic.

## Performance — the estimate was WRONG, in the safe direction

I predicted +0.8–1.0s on the all-states browse once a cell is unticked. Measured, 6 cold reps each
on Venue (10,745 rows):

| grid | median | range |
|---|---|---|
| all-true (no predicate added) | **2.10s** | 1.85–3.27 |
| Wyoming×Venue denied (allow-list active) | **1.12s** | 0.95–2.09 |

**Unticking a cell made it ~1s FASTER.** The narrower `overlaps?` scan beats the unfiltered
`totals: true` count — same effect [[project_similar_vendors_price_band]] saw. So there is no perf
argument against using the grid. Don't re-derive this; it's measured.

## Verified (don't redo)

- **Parity, all-true grid: 51/51 cases, ZERO material differences** vs the v22 baseline (6
  categories × {no states, 1, 4, International+country, State_Input, facets, 2 fallback variants}
  + page 2 + sort + search). The only byte diffs were `Clicks_on_Venue` counters ticking from live
  ep150 vendor views — always expect those in an ep119 diff, compare ids + `filtered_total`.
- **Behaviour, NY×Beauty unticked:** NY search 56→0; 7/7 NY+NJ multi-state vendors survive under
  NJ; no NY-only vendor leaks into `[NY,NJ]`; `[NY,NJ]` becomes identical to `[NJ]`; NJ untouched
  at 13; **Venue NY untouched at 617** (category isolation); legacy `State_Input=New York` → 0;
  national browse 73→28.

## Data findings, measured over all 13,378 rows

**Category vocabulary (validated rows).** The user cleaned this mid-session, so as of 2026-08-11:
Venue 10,738 / Photography 1,569 / Entertainment 665 / Flowers 140 / Food and Beverage 114 /
Beauty 73 / Wedding Planner 9 (was split 3 + 6 "Wedding Planning") / Catering 0 (folded into F&B) /
leftovers Rentals 2, Transit 2, "Both" 2, "" 2. Table 61's column was renamed
`Food_and_Beveerage` → `Food_and_Beverage`. **The v23 lambda accepts BOTH spellings and aliases
Catering→F&B, Wedding Planning→Wedding Planner**, so it is immune to that cleanup either way.
Matching is **case-sensitive** (`venue` → 0 rows).

**8 location tokens have NO row in table 61** and are hardcoded as `EXTRAS` (fail open):
`US` 18, `DC` 8, `New England`, `New Orleans`, `East Coast`, `Mexico`, `Australia`, `Puerto Rico`.
Note **`DC` is really Washington DC** and won't respond to that row's checkbox until normalised.

**`country:` tokens need no handling.** 917 of 918 rows with a Country also carry the plain
`International` token; the lone exception is an Indiana vendor with `Country="United States"`,
which carries `Indiana` anyway. So the International row alone governs the whole international
bucket — no country enumeration needed.

## ⚠ THE BIG ONE: a denied search does NOT show an empty grid — `fallback_all` fills it with other states

**Verified 2026-08-11 against the exact live WeWeb request shape.** Live request history confirms
**WeWeb sends `fallback_all=true` on every search-grid call.** So when the whitelist zeroes a
search, ep 119's pre-existing fallback fires:

```
Beauty + states[]=New York, New York DENIED
  -> filtered_total = 0, is_fallback = true, but TWELVE ITEMS RETURNED
     from Alabama, California, Illinois, New Jersey, International, ...
```

The user asked for New York beauty vendors, gets a full grid of out-of-state ones. Worse, some
returned rows are *New-York-serving* multi-state vendors ("New York, New Jersey", "New York,
Connecticut") — correctly allowed under location-scoped semantics because NJ/CT are allowed, but
to the user it looks like the whitelist did nothing.

**The whitelist alone therefore does NOT hide a category from a state in the UI.** To get a real
empty state, WeWeb must either stop sending `fallback_all=true` or read `is_fallback` and render
an empty/"nothing here" state instead of the rows. `filtered_total` is already 0 and
`is_fallback` already true, so the client has everything it needs — this is a **WeWeb-side change,
no Xano work**. Same applies to the legacy `State_Input`-only path (Similar Vendors), verified
identically.

**Also corrects the v22 note "WeWeb always sends State_Input empty" — it does NOT.** Live traffic
shows `State_Input=Pennsylvania` with no `states[]` (Similar Vendors + filter probes) and
`State_Input=International` alongside `states[]`. When `states[]` is present ep119 forces
`$eff_state_single=""` so it is harmless there, but the single-state path is genuinely live and
that is why v23's `state_input_denied` guard matters.

## Known gaps / open items

- **10 validated rows have `State=""` and `Country=""`**, so `flt_states` is `[]` and an allow-list
  `overlaps?` can never match them. They vanish from the national browse once any cell is unticked.
  Measured: **7 of them are Venue** (Venue dropped 10,745→10,737 with one Wyoming cell off = 1
  Wyoming venue + 7 blank rows).
- **ep 121 (map pins) and the vendor-detail path ignore the whitelist** — deliberate, scoped to
  ep119 this round. A hidden vendor is still a map pin and still loads by direct URL.
- **Every whitelist edit needs a `cache_v` bump** (currently 18) or it is invisible for the 7100s
  ttl. Task 8 omits `cache_v` and auto-tracks the default, so the warmer needs no edit.
- **No on-disk copy of v23 was saved** — the user declined the `cp` into `.claude/xano_backups/`.
  Rollback point `ep119_v22_pre_whitelist_2026-08-11.xs` DOES exist. The v23 script and its builder
  live only in the session scratchpad (`build_ep119_v23.py`, `ep119_v23.xs`).

Harness (scratchpad, reusable): `ep119_matrix.py` (51-case capture, resumable, checkpoints per
case) + `ep119_diff.py`. **Use different `cache_v` values for before/after or the second run just
reads the first run's cache entries and "proves" parity trivially.**

See [[reference_xano_mcp_quirks]] §14 — `publish: false` writes a draft whose response looks
published; that cost a whole verification cycle here.
