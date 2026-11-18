---
name: pricing-intelligence-space-grain
description: "2026-08-12 in-progress rebuild of the venue Pricing Intelligence panel to per-space, guest-count-driven, all-in pricing with a range-free/exact-gated paywall; S1+S2 shipped"
metadata: 
  node_type: memory
  type: project
  originSessionId: 548bb6e7-2cb7-40b4-8faa-c1145ceeeaa3
  modified: 2026-08-14T05:18:37.330Z
---

Rebuilding the venue detail page's Pricing Intelligence panel. Plan file: `C:\Users\vivek\.claude\plans\look-at-the-past-serene-pebble.md`.

**Product decisions (locked by the user):** native WeWeb, no Vue component · paywall = **range free, exact number gated** · guest count from `user.guest_count` falling back to 50 · **does NOT need to reconcile** with the vendor-card `estimated_total` · ship in one release · fold in the multi-value-state cohort fix.

**The load-bearing data fact:** table 36 `Extracted PDF Data` is **already one row per (vendor, space)** — `Venue_Space_Name`, `Max_Capacity_Seated`, and all the cost columns sit on each space row, and `extract_core.py` was written for it ("Never consolidate multiple spaces into one"). **No re-extraction is needed to ship.** Table 37 `venue_pricing` is **121,575 rows** (not the ~789 in older notes), 97.6% clean space labels vs table 36's 57.4%, and is the only table with `Admin_Fee_Pct` / `Service_Fee_Pct` / `Tax_Pct` separately — but every column is text with prose like `'Inquire Within'` and `'$4,000-$6,500'`.

**Measured fill rates at the space grain** (this drives the whole UX): venue fee 73%, capacity 75%, admin/service 40%, ceremony 27%, per-person F&B 21%, F&B min 21%; **all four components only ~4%**. So the **market-estimate path is the majority experience**, not a fallback, and the paywall only has something to gate on a minority of spaces — the submit-PDF CTA monetizes the rest.

**Copy the data cannot support:** "Based on 6 contracts for this space" is impossible — 98.5% of (vendor, space) pairs have exactly ONE PDF and the max anywhere is 5. Ship singular copy.

**Math traps:** `FB_Spend_Min_Type = 'Per Person Min'` (~19% of typed rows) means the F&B minimum is **per head, not a total**. `Ceremony_fee_Type` has a `Per person` variant (242 rows) plus `Included`/`Complimentary` (18, render $0) and `Per hour`/`Per 30 minutes` (12, no hours field exists → fall back to the estimate). Percentages are whole numbers (`22` = 22%).

**Two live bugs being fixed:** ep149 computes each vendor's value as **`array_max` across ALL its table-36 rows**, so a four-room venue already reports its priciest room as its own price; and its `count` emits `state_benchmarks.pdf_count` (3,403 for NY) for **every** field when real per-field n is 1,372/1,050/573/1,299 — overstated up to 6x. Plus the multi-value-state cohort bug ([[project_flt_states_denorm]] has the `flt_states` token substrate that fixes it).

**SHIPPED so far:**
- **Table 62 `venue_spaces`** — 30 cols, one row per clean space, `space_key = "{VENDOR_ID}|{slug}"` unique (verified it rejects duplicates). Absence is carried by `has_*` flags because sources write 0 for missing and 0 is indistinguishable from genuinely free.
- **fn 54 `utils/rebuild_venue_spaces`** — per vendor, reads tables 36+37, dedupes `(PDF_ID, space)` by `MAX(last_extracted_at)` then `MAX(id)`, splits pipes, **suppresses piped aggregate rows when split siblings exist for the same PDF** (V6/P6 has both, and without this you get a phantom 5th chip), drops placeholders, title-cases ALL-CAPS, merges spelling variants within the vendor, and coalesces **field-wise, never array_max**. Upserts on `space_key`, sweeps orphans. Verified: V6 → Ballroom/Lobby/Amphitheater/Park with fees 4000/7500/1500/0; V3900 → single "Whole venue"; V3794 → 3 spaces at 3-of-3 fields; V3883 → per-person ceremony. Idempotent (re-run leaves the same 3 row ids).

Chip order is `field_count desc, has_venue_fee desc, capacity desc, label asc` — **the venue-fee tiebreak matters**: without it V6 defaults to Park, the one room with no pricing at all.

- **ep 229 `admin/rebuild_venue_spaces`** (group 10, GET, page/per_page/dry_run) — pages table 11, venue-only, calls fn54 per vendor. **Backfill COMPLETE: 10,780 venues, 29,903 spaces, 5,950 multi-chip (~57%)** — well above the ≥4,000 the table-36-only estimate predicted, because fn54 unions table 37.
- **Trigger 3 `flt_sync_on_extracted_pdf_write`** now also calls fn54, **wrapped in try_catch** so a rebuild failure degrades the panel instead of blocking an extraction write (fn21's call is deliberately left unguarded — it is load-bearing for search). Measured overhead **~0.30s → ~0.33s**, about +35ms.
- **Table 63 `venue_benchmarks`** + **fn 55 `utils/rebuild_venue_benchmarks`** + **task 15 `precompute_venue_benchmarks`** (09:40 UTC daily). 29,903 spaces → 4,354 buckets built, **2,785 published, 1,569 suppressed** below the ≥5-spaces-from-≥3-vendors floor. Runtime 79s manual, **168s from task context**. Verified it runs from a task (real history entry, not the <0.1s silent-abort signature that killed task 12).

**Benchmark numbers are sane and validate the design:** NY per-head p25/med/p75 = $140/$195/$285 (national $110/$160/$225); capacity bands move it monotonically (NY $148 → $208 → $231 → $250 as rooms grow), which is exactly what justified the capacity_band dimension; all-in per guest falls as guests rise ($317 → $266 → $248 at 75/125/200) as fixed costs amortize; **national service-charge median is 22%, matching the mockup's "assume around 22%"**. Zero sample-floor violations.

**Task 2 `daily_brevo_user_sync` was DELETED** (user-approved) to free a slot — the plan caps at 10 tasks. It had been disabled since 2026-07-14 and superseded by task 3. Snapshot: `.claude/xano_backups/task2_daily_brevo_user_sync_pre_delete_2026-08-12.xs`.

**NOT DONE — deliberately skipped:** adding fn54 to hourly task 10 as a heal path. Because the trigger's try_catch swallows failures, a failed rebuild currently goes stale until that vendor is next written (it does log to debug.log). Re-running ep229 fixes any drift.

- **fn 56 `utils/venue_pricing_panel`** + **ep 231 `venue/pricing_intelligence`** (public, `auth=""`, cached 14400) + **ep 230 `venue/pricing_intelligence_full`** (`auth="user"`, **NO cache**). Every rendered string is built server-side; WeWeb binds `sentence`/`badge_label`/`chip_label`/`status_line`/`footer` and never assembles copy. Verified end to end: V3794 Grand Hall renders the mockup exactly — $132/head x 50 + $995 ceremony + $5,495 rental, +22% = **$15,970**, solid provenance, "middle for California".

**THREE BUGS CAUGHT BY THE VERIFICATION STEPS — do not regress these:**
1. **Gated copy leaked the figures in prose.** Dropping `pp_fb` from the component object while the sentence still read "At {guests} guests that is $132 a head" left the exact price in the free payload. Free-tier sentences now assert that the venue published a figure and anchor on the MARKET median instead. The leak test greps the whole serialized payload, not just object values.
2. **`_full` must NOT be response-cached.** Xano consults the cache BEFORE running the stack, so a lapsed user would be served a paying user's cached figures. Since that endpoint deliberately returns different payloads per entitlement, the only safe options were auth-in-key (cardinality x users) or no cache. It is cheap (~0.1-0.4s) so the cache is off, deliberately. The public twin IS cached safely — one payload shape for everyone.
3. **`date_until_access` is a STRING (`'2026-11-01'`), not epoch ms.** `Number(d)` yields NaN, which would have silently denied every paying user. Parsed with `Date.parse(s + 'T23:59:59Z')` so the last day counts as still granted.

Also: provenance said "Nothing from this venue" for venues with a real rental fee but none of the 3 headline components — common, since venue fee is 73% filled vs 21-40%. Now reads "Only the rental fee came from this venue".

**S11 (ep149) — PARTIAL:**
- **fn 20 `rebuild_state_benchmarks` REWRITTEN** to group on `flt_states` tokens instead of the raw comma-joined `State`, and to add `p25`/`median`/`p75` per field (additive). Verified on Vermont. Backup `fn20_..._v2_multistate_2026-08-12.xs`.
- **Cohort bug proven live:** ep149 with `State="New York, New Jersey, Connecticut"` returns **n=90, avg $0**; with `"New York"` it returns **n=3,403, avg $20,192**. The fix is a WeWeb binding change (pass the first token — `Similar_Vendors` already does this), NOT a Xano change.
- **BLOCKED — ep149's per-field `count` bug is unfixed.** `getAPI` returns a persistent **502 for ep149's xanoscript specifically** (ep207 and others retrieve fine; ep149 retrieves fine *without* xanoscript). Cannot read its source, and a blind rewrite from the June backup would regress the 2026-07-09 `vendor_value_min` work. The new panel computes its own footer count correctly, so this only affects the legacy panel.
- **Full table-52 rebuild NOT done.** The HTTP gateway 502s at ~13s and NY alone needs longer, so ep188 cannot drive it. **Task 9 at 08:00 UTC completes it** — verify afterwards that rows carry quartiles. A single-call full rebuild briefly destabilized the instance (public API 503s for ~1 min), so do not retry it over HTTP.

**Xano instability note:** the admin API and occasionally the public API returned 502/503 intermittently during heavy writes. Always `--retry`, and re-check with a light call before concluding something is broken.

**2026-08-12 later — fn56 v2 SHIPPED: the `venue_fee` ("Rental fee") component.** The rental fee was computed into the all-in (`const vf = hasVf ? sel.venue_fee : estVf`) but **never returned** — so the paid breakdown could not show the line the mockup had, and the wiring guide's stepper formula referenced a `venue_fee` term that did not exist in the payload. `components[]` is now **5, in order food_and_drink / ceremony / venue_fee / service_charge / total**. `bkVf` was already looked up and dead; it is now the consumer. Deliberately NOT added to `field_count` / `fields_total: 3` / `provenance.marks` — the "Rental fee only for this space" status line depends on rental fee not being one of the 3. Verified: v2 payload byte-identical to v1 apart from the new component (V3794/V6/V3883), strict token-boundary leak test clean (5495 and 4000 absent as whole tokens from the free payload), paid `amount`=5495 / estimate path `est_amount`=5000, and the stepper arithmetic reproduces `all_in.at_default.total` exactly for both.

**`ep231 cache_v` default bumped 1 → 2 in the same release, and that step is mandatory:** editing fn56 does NOT purge ep231's 4h response cache, because Xano consults the cache before running the stack. Any future fn56 change that alters the response must bump it again.

**TRAP — `all_in.estimates` is the market FALLBACK set, not the addends.** It is populated even when the venue published a real figure (V3794 carries `estimates.venue_fee = 9000` next to the real `amount = 5495`). WeWeb must take `amount ?? est_amount` per component and never sum `estimates`.

**TRAP — fn54 has NO category guard.** Calling `utils/rebuild_venue_spaces` directly on a non-venue writes a junk table-62 row; only ep229 filters to venues. Did this to V3900 (Category "Flowers") while chasing a phantom `found:false`, and deleted row 29906 to undo it. `found:false` for a non-venue is CORRECT, not drift.

**2026-08-12 later still — fn56 v3 SHIPPED: `partner_vendors` (see [[project_partner_vendors]]).** Top-level `partner_vendors[{key,name}]` + `_headline` + `_more_label`, free at both tiers, `ep231 cache_v` → **3**. Regression-diffed identical to v2 on V3794/V6/V3883.

**WeWeb collections `Pricing_Intelligence` (`0ef36c86-790a-42a6-8820-42454161e7e8`) and `Pricing_Intelligence_Full` (`2ff85018-16eb-48b0-abcd-327f13e83ef7`) ALREADY EXIST and are wired correctly** — the handoff doc's "create the two collections" step is done. The venue page also already auto-fetches ep142 as collection "Extracted Data" `9a5f3d77-eaa7-469b-a753-d5a3e10342c9`.

**WeWeb venue-page traps (surveyed 2026-08-12):** Main Content `de25d763-…` and Sidebar `55d5504e-…` both switch to `display:contents` on mobile, so children are hoisted and ordered ONLY by explicit `customCss.order` (1,2,3,4,5,7 used) — a new section without one defaults to 0 and jumps to the top of mobile. Sidebar is `position:sticky`; lengthening it kills the stick. Reusable chip pattern: PI Space Chip `638dbfd5-e618-44be-b305-6c4c8bfee627`.

## 2026-08-14 — fn56 v5 (clarity release) + fn54 v2 (field-wise dedupe), both SHIPPED

Driven by Des + Kate's 2026-08-13 Slack review. **Two of their diagnoses were wrong and acting on
them as written would have made the numbers worse** — see [[project_pi_review_feedback]].

**fn54 v2 — the dedupe was ROW-WISE and ran BEFORE the field-wise coalesce.**
`best[tbl|pdf|space]` picked one winning row per key and discarded the rest, so a **re-extraction of
the same PDF that failed to read a field destroyed that field outright** — `build()`'s field-wise
coalesce never got to see the older row. V2 Terra Ballroom: newer t36 row wrote `0` over `322`, and
because `pos()` maps 0 to null the space stored `pp_fb = 0`, pinning `food = max(0*g, 70000)` and
freezing the guest stepper at every guest count. Now merges field-wise inside the key (newest still
wins every field it HAS; older rows only fill gaps), mirroring build()'s own rules for cap (max),
ctype / vtype (first non-empty) and fb_pp_flag (must travel with the row that supplied fb_min).
New `stats.recovered_fields`. Backups `fn54_..._pre_fieldwise_` / `_v2_fieldwise_`.
**Repaired 49 vendors / 56 fields via a temp endpoint (created, run, deleted); `pp_fb = 0` spaces
21,992 → 21,953. Deliberately did NOT run the full 13k ep229 backfill** — the rest heal via trigger 3.

**MEASURED, and it reframes the whole complaint: 4,120 of 29,903 spaces have `has_fb=true` +
`pp_fb=0`, but only 94 were recoverable. 4,026 are frozen because the venue genuinely published only
a total F&B minimum and no per-head rate.** So the dead guest stepper is overwhelmingly a COPY
problem, not a data bug. Don't go hunting for more data fixes here.

**fn56 v5 — `ep231 cache_v` 4 → 5.** Backups `fn56_..._pre_v5_` / `_v5_clarity_`.
- **`space.guest_sensitivity`** `{responds, food_pinned, reason, fb_min_breakeven_guests,
  breakeven_above_capacity, note}`. **`responds` (whole total) and `food_pinned` (food term only) are
  NOT the same thing** — conflating them shipped a false sentence in the first v5 push: the Bowery
  read "this total does not change" while its per-guest ceremony fee moved the ladder $120k → $198k.
  Any user-facing copy must branch on `responds`. Reasons: `fb_min_only`, `breakeven_above_capacity`,
  `breakeven_above_range`, `below_fb_min_breakeven`, `no_per_head_benchmark`.
- **LEAK GUARD: `breakeven = fb_min / pp_fb`, so the number and any copy quoting it are stripped for
  free callers.** Structural flags stay.
- **`space.guest_min`** finally surfaced (it had been in table 62 since day one, unused).
- **Badges: all four rungs now carry the place suffix** and `high` is renamed "Above average in X".
  Previously only the bottom two were scoped, so "Below average in New York" → "On the high side"
  read as two different scales. New `badge_scale` tooltip names the cohort size.
- **Tax + gratuity as `excluded_from_total` display-only components**, total retitled "What you'd pay
  this venue, before tax and gratuity" + `scope_note`. **The total's definition is UNCHANGED on
  purpose** so table 63 benchmarks and the band need no rebuild. Tax shows a figure only where
  `tax_pct > 0` (jurisdictional — a state median would be wrong, not just imprecise); gratuity is
  always generic copy, never a venue-attributed number.
- Provenance headline now states the rental's status when it disagrees with the other three
  (`from_venue_count` excludes venue_fee, so "All three costs came from this venue" could sit
  directly above "Market estimate, not this venue").
- `headline_prefix` → "A peak-season Saturday **dinner** in …" + `headline_note`.
- Footer `marketWhere` / `marketN === 0` grammar bug fixed.

**WeWeb IS BUILT** — "Pricing Intelligence Card" `073b9a22-1fc8-4cbe-91c6-826f4874135d` on Venue
Vendor Page `e49b4e74-7d3d-4293-8e27-5fe159f8a856`, ~40 elements, every binding
`(full && full.found) ? full : free`. Any handoff-doc line saying "still to build" is STALE.
All five money lines are ONE repeat (`PI Components` `94ab5295-…` over `space.components`), with
`PI Component Value` `67df9e40-…` recomputing every term locally and branching on
`context.item.data?.key` — so **a new component key renders automatically but gets no value**, which
is exactly what tax/gratuity want. Guest count = `variables['323efbd0-ee19-4906-9203-34ade0dedd8e']`
→ `user.Wedding_Guest_Count` → `default_guests` → 50, clamped 25-400.

**2026-08-14 WeWeb edits (4, all verified persisted; app still needs PUBLISHING):**
- **Mobile chip cut-off (Des) ROOT CAUSE: `mobile.customCss` REPLACES `default.customCss` wholesale,
  it does not merge.** `PI Space Chips` `c163d1f4-…` had `overflowX:auto` only in default, so mobile
  lost it entirely and clipped. Now repeated in `mobile.customCss` with `flexWrap:nowrap`.
  **Check this whenever a breakpoint seems to lose a style.**
- New `PI Guest Sensitivity Note` `47f77648-bf8d-4474-b454-b3d135f2a0a0`, inserted between the
  headline block and the band. Renders `guest_sensitivity.note` + `guest_min` + `headline_note`.
- `PI Venue Source Tag` `d6862d82-…` now also requires `source === 'venue'` — the tax row is
  `is_estimate:false` with `source:'unknown'` when no rate was stated, and would have claimed
  "From this venue" over a sentence saying the opposite.
- `PI Estimate Tag` `4b880134-…` now returns false for `excluded_from_total`, so gratuity does not
  read "Market estimate, not this venue".
- `PI Component Sentence` `ef5ae1e2-…` appends the total's `scope_note`.

**"From this venue" ALREADY EXISTED** (`PI Venue Source Tag`) before Des asked for it.

**NOT done in WeWeb:** `badge_scale` tooltip on `PI Verdict Badge` `aeeafe38-…`; visual greying of
the `excluded_from_total` rows; ep207 entertainment-panel median.
**Unverified:** only `PI Component Sentence` was confirmed to apply the `{guests}` replace — the band
/ CTA / provenance / footer sentence elements were not read, so check them before adding `{guests}`
to any other server string.

**Still open:** food line emits NO badge when `pp_fb = 0` (`badge(pp > 0 ? pp : null)`), so the
biggest number on ~4,026 panels has no context chip. Badging the effective per-head
(`fb_min / guests`) would fix it — deliberately not done, it is a design change.

**NEXT — WeWeb. `PRICING_INTELLIGENCE_HANDOFF.md` (repo root) is the transfer package:** Part A is a self-contained paste-block for a WeWeb-MCP Claude session (carries the real V3794 payload verbatim so it never guesses key names), Part B is the manual checklist. `PRICING_INTELLIGENCE_WEWEB_WIRING.md` remains the deeper reference and is updated for the 5-component shape. Old summary of what remains: two collections bound `Full ?? Free`, page vars `selectedSpaceKey` / `guestCount` (user column is `Wedding_Guest_Count`), stepper reads `all_in.ladder` (free) or raw components (paid) with zero refetch, replace blur overlay `bed88937-…` with `space.cta`, fix the `Benchmarks` State binding, then delete the 47 composite table-52 rows. MCP cannot edit WeWeb collections, so this is the user's to do.
