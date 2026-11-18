---
name: project_fb_min_type_bug
description: "2026-08-14 SHIPPED - a total F&B minimum typed 'Per Person Min' was multiplied by guest count twice, inflating Pricing Intelligence up to 100x; fn54 guard + 112 source rows repaired + audit endpoint"
metadata: 
  node_type: memory
  type: project
  originSessionId: 91271f22-af11-4573-8c2c-b96fe2eb1b6c
  modified: 2026-08-14T20:17:57.182Z
---

Kate flagged Please Touch Museum (`V2371`, Carousel) reading **$1,450,000 all-in at 50 guests**,
food line $1,200,000. The PDF says *"catering packages are $240pp + tax at a guest minimum of 100
people"* — the right answer is $24,000 food / **$39,000 all-in**. FIXED and verified live.

## The mechanism — read this before touching any F&B math

fn56 computes `food = fb_min_is_per_person ? max(pp*g, fb_min*g) : max(pp*g, fb_min)`.
Table 62 stored `pp_fb 240` (**correct**), `guest_min 100`, `fb_min 24000` with
`fb_min_is_per_person = true`. 24,000 IS 240 x 100 — a **total** — so the per-head flag multiplied
it by guests a second time. **`pp_fb` was never wrong; the flag was.**

Origin: two table-36 rows for P2371. The older said `FB_Spend_Min_Type = "Overall Min Spend"`
(correct); a **re-extraction on 2026-06-04 regressed it to "Per Person Min"** without changing the
value. fn54 takes the newer row. Dates cluster on **2026-06-04 and 2026-06-25** — one or two bad
extraction runs, not drift.

**The panel's own copy contradicted itself** — *"A minimum of $24,000 a head applies. At 50 guests
that is $240 a head"* — because the two sentences read `fb_min` and `pp_fb`. That self-inconsistency
is the cheapest detector if this ever recurs.

## Why the fix went in fn54 and NOT fn56

`PRICING_INTELLIGENCE_WEWEB_WIRING.md:111-116` — the WeWeb `PI Component Value` element
**recomputes the identical food formula client-side** from the raw components. Patching fn56 would
have fixed the free ladder and left the paid guest stepper wrong. **Normalize where the value is
WRITTEN, never at read time**, or the two tiers disagree. Do not add a mirror clamp to fn56.

## What shipped

- **fn54 v3** (backups `fn54_..._pre_fbmintype_` / `_v3_fbmintype_`). In `build()`, a per-head-flagged
  `fb_min` is reclassified to a total when **(1)** `fb_min > 2000` (the per-head ceiling) or
  **(2)** `fb_min == pp_fb * guest_min` within 1%. Sets new `fb_min_type_corrected` + `stats.fb_min_retyped`.
  **Deliberately NOT a ratio test** (`fb_min >= 5x pp_fb`) — the ceiling catches every measured case
  and a ratio would fire on a legitimate per-head minimum that bundles bar on a food-only menu rate.
- **Second fn54 guard, `ppOf()`**: rejects `pp_fb` above the same $2,000 ceiling or where table-36
  `Currency` is non-USD. 21 rows were MXN/TRY or whole-event totals (V946 94,000 MXN, V5410 9,800 TRY).
  Space falls back to the market estimate instead of rendering $94,000 a head.
- **Table 62** `+ fb_min_type_corrected` (bool, additive).
- **112 table-36 rows repaired** at source (`"Per Person Min"` -> `"Overall Min Spend"`), via a
  temporary endpoint using `db.edit` so triggers fire. Used the **ceiling rule (112 rows), not the
  narrower product identity (~78)**, so source and guard agree exactly.
- **ep236 `admin/pricing_sanity_audit`** (KEPT, secret-gated, uncached) — answers Kate's "how do I
  know if others are wrong". Returns suspect rows, `food_at_50_guests`, and `vendors_needing_rebuild`.
- **ep238 `admin/rebuild_suspect_spaces`** (KEPT) — the remediation half; re-run until
  `vendors_found: 0`. Kept because trigger 3 wraps its fn54 call in try_catch, so a failed rebuild
  goes stale silently. ep237 (one-shot source repair) was deleted.
- **ep231 `cache_v` 5 -> 6.** Needed even though fn56 did not change — the cached RESPONSE is built
  from the values that changed.
- **extract_core.py** (tulle_vendor_data_updates, push needs **marthv**): all three prompt sites now
  say the type describes the NUMBER IN THE F&B MIN FIELD, not how the menu is quoted, with the
  $240pp/100-guest worked example. Plus `_fix_fb_min_type(payload)` before the venue writer's
  `_post_row`, mirroring fn54's two rules. 10 unit cases pass.
  **SHIPPED — commit `d2648e9` on `main`, deploys via Railway.** Only the F&B hunks were
  committed: the same file also carried unrelated uncommitted `_fetch_vendor_categories`
  diagnostics, and `launch_parallel.py` is modified too — both **still uncommitted** in the
  working tree, deliberately. Staged selectively with
  `git diff <file> > p.patch`, strip unwanted hunks, `git apply --cached`.

## Measured, and the numbers to re-check against

412 table-62 rows carried the per-person flag; **86 suspect** (all >2000), 62 of them exactly
`pp_fb * guest_min`. After the fix: **327 flagged, all plausible, 0 suspect, 0 pp_fb over ceiling**.
Worst case was V673 Cipriani Ballroom rendering **$5,312,500** of food at 50 guests (425 x 250).
`corrected_already: 7` — the source repair fixed most at origin; the 7 take `fb_min` from table 37
(`FB_Min_Type`), which was NOT repaired.

**The 309 rows with `fb_min < 500` are genuine per-head minimums — do not touch them.**

## Knock-on that is easy to miss

**Table 63 `venue_benchmarks` is computed FROM table 62**, so it was contaminated: Pennsylvania's
all-in p75 read **$24,113 per guest**. Rebuilt via `utils/rebuild_venue_benchmarks` (82s, 2,791
published) and it dropped to $436. **After any bulk table-62 repair, rebuild 63 or every venue is
compared against a poisoned cohort.**

## Controls that must stay unchanged (regression set)

- **PMA `V2324`** (Kate's "this one is correct"): `fb_min 320 = pp_fb 320`, genuinely per-head, below
  the ceiling. Total **$66,600 / $1,332 a guest** — matches her screenshot. Never rebuilt.
- **Bowery `V425`**: `fb_min 100000` already typed as a total. `fb_min_retyped: 0`. Per
  [[project_pi_review_feedback]] pulling its minimum out of the F&B slot understates it by ~$68k.
- **V3794** Grand Hall: 132/995/5495/22% unchanged.

Related: [[project_pricing_intelligence_spaces]], [[project_pi_review_feedback]],
[[reference_xano_mcp_quirks]].

## Traps hit while doing this

- `searchTableContent` **multi-key `search` objects are SILENTLY IGNORED** (returned all 29,903 rows).
  Single-key filters correctly — already noted in [[project_multicategory_filter_remap]]'s fn48 note.
- Inputs are **not visible to a lambda as `$input`** — bridge through a stack var first, or
  `$var.input_limit` is silently `undefined` and the param is ignored with no error.
- The Xano hostname had **repeated transient DNS failures** (`getaddrinfo ENOTFOUND`) while google
  resolved fine — retry loops around every call. See [[reference_xano_connection_drops]].
