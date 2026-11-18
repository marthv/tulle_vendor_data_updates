---
name: project_similar_vendors_price_band
description: "2026-08-06: ep119 v18 similar_to_vendor_id price band — Xano SHIPPED + verified, WeWeb collection rewiring still open"
metadata: 
  node_type: memory
  type: project
  originSessionId: 787b2e22-90b3-46d5-ae52-4b116cd9b98d
  modified: 2026-08-06T05:25:48.874Z
---

2026-08-06. Kate reported the vendor-page **"Similar Vendors"** grid isn't similar on price: American Museum of Natural History (luxury) surfaced 3 West Club (budget); Arte Cafe surfaced two 1 Hotels. An in-thread reply had claimed it matched on price range — **it never did**. The WeWeb collection `Similar_Vendors` `26a214bf-e4e3-4228-8a02-433653e1edfa` passed only Category + State + capacity. No price param of any kind.

**SHIPPED (Xano): ep119 v18**, new optional input `similar_to_vendor_id`. Endpoint looks up that vendor and derives a two-sided band (**-40% / +40%**, the `LO`/`HI` constants in the band lambda) around its own entry price. Backup of v17 at `.claude/xano_backups/ep119_v17_pre_similar_band_2026-08-06.xs`. `cache_v` 15 → **16**.

**Signal selection is mutually exclusive and that is load-bearing.** venue + `flt_min_venue_fee`>0 → band that; else venue + `flt_min_ppfb`>0 → band that (F&B-model venues); else P/E → band `Package_Price` at package grain. Only ONE pair of bounds is ever set, the other is explicitly nulled. **Setting both ANDs two ranges and requires a vendor to have BOTH a positive venue fee AND positive per-person F&B — most have only one, so the grid would collapse to near-zero.** This is why the earlier "just pass base_fee_max + fb_per_person_max from WeWeb" plan was abandoned.

**The lower bound doubles as the positivity guard.** Pre-v18 `$eff_fee_pos` was hardcoded to `1` purely to exclude unpriced (0) rows whenever a cap was set — so passing any cap silently also excluded vendors with no recorded price. Under a band, `lo = Math.max(1, floor(basis*0.6))` is always ≥1, so that behaviour is preserved for free.

**Verified live:** V122 (MAH, fee $25,000) → band 15000–35000, 616→105 rows, 3 West Club gone. V177 (Arte Cafe, `flt_min_ppfb` 75) → mode ppfb, band 45–105, 616→38 rows, both 1 Hotels gone. Response now carries a `price_band` debug object (`{mode, basis, lo, hi}`, null when inactive) — read it first when the grid looks wrong.

**PERFORMANCE — measured, not assumed.** Discovery path is byte-identical (whole block is inert unless the param is set): cold 2.7–4.1s vs 2.8–5.1s baseline, warm ~0.3s both. The **banded call is 3–4× FASTER cold than the old unbanded one** (0.69–1.23s vs 2.76–2.94s) — a narrower index range scans less and enriches fewer rows. All three added reads are indexed: table-11 `Vendor_ID` btree `636c0705`, `flt_min_venue_fee` `857cd3cd` / `flt_min_ppfb` `bcfe5b5c`, table-36 `VENDOR_ID` `9cf4f0e3`.

**TRAP — adding ANY input purges the whole response cache**, because Xano keys on inputs *after* defaults are applied (see [[reference_xano_cache_resolved_inputs]]). Post-deploy the 23 warmer buckets were cold at 4.7–7.8s; manually re-warmed to <1.5s by replaying task 8's exact buckets. Task 8 omits `cache_v` on purpose so it auto-tracks the new default — **no warmer edit needed, don't "fix" it**.

**STILL OPEN — WeWeb side (MCP CANNOT edit collections; must be done by hand in the editor).** On `Similar_Vendors` `26a214bf-…`:
1. Add param `similar_to_vendor_id` → bind to `current_vendor_id` `79e6edb5-c448-4c77-abd5-7807348a0773`.
2. `Category_Input` currently reads `ChosenCategory` `c8cc3b96-…` — the *Discovery page's* global, not the viewed vendor. Stale/empty on deep links. Point it at the vendor's own `Category` via the Get Vendor Data collection `bd7af501-e8df-4904-a6d4-1dc7b256bb15` (ep41 returns the whole table-11 row, so it also carries the flt_* price columns and is populated on deep links, unlike `vendorSelection` `75987501-…`).
3. `max_capacity` leaks Discovery's capacity filter — set to static `0`.
Do NOT also add `base_fee_max`/`fb_per_person_max`; the band supersedes them and would fight it.

**TRAP — `Country_Input` zeroes the whole grid, and it took three rounds to land.** US vendors store table-11 `Country` = `""` (only international rows carry "Italy"/"Mexico"/etc.), so ANY non-empty Country_Input matches nothing. Three separate zero-result causes hit in sequence, none of them the price band:
1. `selectedCountry` `a4da26d6-…` is **localStorage-persisted** — a country set once while testing International sticks forever and silently empties Similar Vendors on every later session. Pre-existing, predates v18.
2. Category and Country bindings got **swapped** (Country_Input read `Category`, so it sent `Country_Input=Venue` → 0).
3. Fallback written as `null` instead of `""` — **WeWeb serializes null into a query string as the literal text `"null"`**, and `Country ==? "null"` matches nothing. Verified: `Country_Input=null` → 0, `Country_Input=""` → 105.

**Therefore: use `?.Country || ''`, never `wwFormulas.if(x != "", x, null)`.** The `!= ""` shape is broken for null/undefined — `null != ""` is TRUE, so it returns the null rather than the fallback. `|| ''` catches null, undefined and "" alike. This is the same hazard the `Search Vendors` bindings already guard with explicit `!="null"` string checks — that guard exists because someone hit this before.

**TRAP — `Collections_Input` zeroes it too (4th cause, same species).** Table-11 `Collection` is an exact-match list (MAH is `['Museum']`, singular). Verified: omitted → 105, but `['']` → 0, `'null'` → 0, any mismatched value → 0. **WeWeb serializes an empty array to `Collections_Input[]=`, which Xano reads as `[""]` → matches nothing** — so the binding's own `[]` fallback is unsafe. The parameter must be DELETED, not emptied. `selectedCollection` `e887b8a0-…` is localStorage-persisted, so a collection filter set once on Discovery silently empties Similar Vendors forever after.

**FIXED PROPERLY IN XANO — ep119 v19 BLANK-INPUT HARDENING (2026-08-06), `cache_v` 16 → 17.** A lambda at the top of the stack strips entries that are empty, whitespace-only, or the literal strings `"null"`/`"undefined"` from `states` / `Collections_Input` / `pricing_models` / `venue_types` / `vibes`, and blanks `Country_Input` when it holds `"null"`/`"undefined"`. A list that cleans to empty becomes NULL so its `?` operator drops the term — `[""]` now behaves exactly like `[]`. Deliberately NOT applied to `Category_Input`/`State_Input`/`Search_Input` (blanking those widens rather than fails closed).

**WHY IT HAD TO BE SERVER-SIDE: a WeWeb REST collection's parameter rows CANNOT be deleted** — they're generated from the endpoint's OpenAPI spec, so the editor only offers bind/unbind. "Just don't send it" is not available to the client. Unbinding a list param sends `""`; the only client-side workaround is binding it to a JS formula returning `[]` (WeWeb omits empty arrays from the query string but transmits empty strings).

Verified after v19: all four fatal forms (`Collections_Input=""`, `[""]`, `="null"`, `Country_Input="null"`) → 105. Real filters still filter (`['Museum']` → 29, `['Ballrooms']` → 0 because that collection genuinely doesn't exist, `states[]=New York` → 616, `Country=Italy`+International → 394). Discovery baseline 616 unchanged. **No perf cost**: isolated cold 2.6–3.5s vs 2.7–4.1s baseline, warm 0.25s. NOTE: re-warming many cold buckets back-to-back shows 15–16s — that is CONTENTION from the warming itself, not a regression; always measure cold cost with an isolated spaced request.

**THE META-LESSON: strip ALL inherited page globals in one pass, not serially.** Similar_Vendors inherited FOUR Discovery globals irrelevant to the viewed vendor — `Country_Input`, `Collections_Input`, `max_capacity`, `Category_Input` — three of them localStorage/nav-persisted, each able to independently zero the grid with zero UI indication on the vendor page. Fixing them one at a time cost four debugging rounds because each fix merely exposed the next. With `similar_to_vendor_id` driving the grid the ONLY legitimate params are: `page`, `page_size`, `State_Input`, `Search_Input=""`, `Category_Input`, `similar_to_vendor_id`.

**Known, deliberate:** the anchor vendor still appears in its own similar list — there is no `!=?` operator in this where-clause dialect. Pre-existing, not a regression. Also `flt_min_ppfb` is the *min* across a vendor's packages, so the band basis can differ from the headline per-person price the UI shows (Arte Cafe banded on 75, Kate quoted $120).

**Separate live bug found in passing:** variable `ac799270-59f1-47b9-b4a2-1ef65fcc5c95` is named **`min_capacity`** but is wired into the `max_capacity` param on BOTH `Similar_Vendors` and the main `Search Vendors` collection `5c91206d-…`. That mis-wiring affects real Discovery filtering and is unfixed.

Related: [[project_popularity_recency_sorts]], [[project_package_grain_filters]], [[reference_xano_mcp_quirks]].
