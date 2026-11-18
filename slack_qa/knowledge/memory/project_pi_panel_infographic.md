---
name: project_pi_panel_infographic
description: "2026-08-15 redesign of the venue Pricing Intelligence panel from server-built prose to drawn distributions — engagement baselines, the leak-safe charting insight, and the pi-price-position component"
metadata: 
  node_type: memory
  type: project
  originSessionId: eb784596-f4ad-4135-92e2-358536c92040
  modified: 2026-09-07T16:45:32.634Z
---

Redesigning the venue Pricing Intelligence panel because it reads as a wall of text. Plan:
`C:\Users\vivek\.claude\plans\take-a-look-at-shimmying-castle.md`. Preview artifact:
https://claude.ai/code/artifact/2e94db5c-b2d7-4120-bc17-d9a691601bb3

**MEASURED BASELINES (Mixpanel project 3963130, 21 days to 2026-08-15) — use these to judge
any future change:**
- `viewed_vendor_page` 1,910 → `open_pricing_intelligence_breakdown` **702** (37%, strong)
- → `guest_count_slider_changed` **53** (7.5% of openers)
- → `clicked_membership_for_pricing_venue_pricing_intelligence` **5** (0.7%)
- `clicked_membership_for_pricing_pdf_limit_popup` **302** by contrast — but that popup is a
  HARD BLOCK and the panel CTA is optional, so the 60x gap is NOT apples-to-apples. The 7.5%
  stepper number is the unconfounded one.

**Attention is not the problem — interaction is.** People open the panel and don't touch it.

**The Cost Research page (`/cost-research`, 4 calculators) is effectively dead:** 37–126
uniques each per 21 days, `go_to_venue_calc` 49, `saved_venue_calc_quote` 16. Don't invest
there; the venue-page PI panel is where the traffic is.

**MEASURED: the live free-tier payload for V3794 is 509 words across 50 strings.** Worst
offender: `badge_scale` is a **29-word legend repeated verbatim 4x** (116 words, 23% of the
panel). Plus `badge_label` x4, `status_line` x4, and `all_in.ladder` as 16 text labels.

**ROOT CAUSE IS ARCHITECTURAL:** fn56 builds every user-facing string server-side and WeWeb
"never assembles copy", so the only lever when something reads as confusing is a LONGER
SENTENCE. v5's clarity release added `guest_sensitivity.note`, `scope_note`, `headline_note`,
`badge_scale` and a longer provenance headline. **The panel gets wordier every time it gets
clearer.** Don't fix future clarity complaints with more prose.

**THE KEY INSIGHT — the charts are already in the payload and they are LEAK-SAFE.** Every
component ships `p10/p25/median/p75/p90` + `n_spaces`/`n_vendors`, and `band` does the same
for the all-in. **Those are MARKET figures, not the venue's**, so plotting them leaks nothing.
The venue's own position is already bucketed to `badge_code`. So: free tier draws the market
distribution and shades the venue's quartile zone; paid tier drops an exact pin on the same
axis. **No fn56 change, so no `ep231 cache_v` bump.**

**BUILT: `components/pi-price-position/`** — Vue/WeWeb element, builds clean
(`./node_modules/.bin/weweb build name=pi-price-position type=wwobject`, exit 0,
`dist/manager.js`). One distribution-strip component intended for **six** instances (5 money
rows + the all-in band). `showLegend` true on the first instance only — that one legend is
what replaces `badge_scale`.

**Design decisions worth keeping:**
- **The axis is PERCENTILE RANK, not value.** These distributions are hard right-skewed
  (CA food p90 $482 vs median $156); a linear value axis crushes the middle half into a few
  pixels. Dollar labels ride the percentile axis so both read. Zones are fixed at 0/25/75/90/100,
  which mirrors `badge_scale`'s own definition exactly.
- **`unit` prop is required** — `service_pct` benchmarks are 18/22/24, so that row must render
  `22%` not `$22`.
- **Percentile ties are real** (`service_pct` often has p25 === median) — the value→percentile
  interpolation must guard `dx === 0` or it divides by zero.
- Zone palette validated with the dataviz validator against the Tulle tokens: g500 / ink5 /
  amber / red **passes all checks on the light surface**. The gray midpoint fails the chroma
  floor by design — that check is categorical-only and a diverging midpoint must be neutral.
  **The Tulle system is light-only** (no dark tokens in CLAUDE.md); don't invent a dark palette.

**TRAP FOUND: `:root` inside a Vue `<style scoped>` block compiles to `:root[data-v-…]` and
NEVER MATCHES.** `components/paywall-blur-gate/src/wwElement.vue` line 144 does this, so its
token block is dead (it only works if the WeWeb page defines the tokens globally). New
components define tokens on their own root class instead.

**Deliberately NOT doing photography inside the card** — the venue gallery already sits
directly above it, and [[project_frontend_images_issue]] is an unresolved blank-image bug.
Infographic quality here comes from marks, not pictures.

## 2026-08-15 — CORRECTIONS to the baselines above, and fn56 v6 SHIPPED

**THE PANEL WAS REBUILT AND PUBLISHED ON 2026-08-14. The 702/53/5 baselines describe the
OLD legacy panel and must NOT be used to justify anything.** Evidence:
`open_pricing_intelligence_breakdown` went 47 (Aug 13) → 5 → 0, while
`clicked_membership_for_pricing_venue_pricing_intelligence` fired its first-ever events (5)
on the same day. The element that fired the old event belongs to the panel that was replaced.
Any "app still needs PUBLISHING" line in [[project_pricing_intelligence_spaces]] is STALE.

**THE PANEL ALREADY HARD-BLOCKS LOCKED USERS — "move it from tease to block" is already
done.** Both `PI Components` (`94ab5295-…`) and `PI Band Block` (`910309ed-…`) carry
`conditionalRendering: p?.space?.all_in?.gated !== true`, so a locked user gets NO cost rows
and NO band — only header, chips, headline range, sensitivity note and CTA. A second gate,
`Section Blur Overlay` `bed88937-…`, adds `total_vendor_views > 3`.

**SO THE 509-WORD FIGURE IS THE PAYLOAD, NOT THE RENDER.** A locked user sees ~55–60 words.
The wordiness is a **PAID-user** experience problem, not a conversion problem. fn56 still
emits "Unlock to see it…" teaser sentences on component rows that WeWeb never shows to the
people they address — dead copy worth cleaning up.

**SHIPPED — fn56 v6 (CTA) + ep231 `cache_v` 6 → 7.** Backups `fn56_pre_v6_cta_2026-08-15.xs`
and `ep231_v6_pre_cta_2026-08-15.xs`. The old CTA had a possessive typo ("this venue real
figures"), named no benefit or price, and wasted `cc` (contract_count) — the panel's
strongest trust signal — which only appeared in the 11px footer BELOW the button. Now:
`label: 'See the exact number - from $30'` and a sentence tying the free range to the gated
figures via `cc`. **Entry price $30 verified against live Stripe (23 of 45 succeeded charges
Aug 8–15 were exactly $30.00); the label must track /pricing.**
Verified: live script byte-identical to intent; payload identical apart from `cta`; leak
test clean.

**LEAK-TEST FALSE POSITIVE, do not chase it:** a naive whole-token scan flags `995` in
V3794's free payload. It is `components[1].benchmark.p25` — the market 25th percentile
across 86 California venues — which coincidentally equals this venue's own ceremony fee.
A token scan cannot tell a market percentile from the venue's figure; diff against a known
baseline instead.

**Driving the Xano MCP over curl WORKS and is the right way to write a large function**
(`updateFunction` needs the FULL script; fn56 is 50KB, too big to retype safely because
whitespace inside `"""` blocks is load-bearing). The server is **stateless — no
`mcp-session-id` needed**, just POST `tools/call` with the Authorization header from
`.mcp.json`. Responses are SSE. **`getAPI` REQUIRES `apigroup_id` as well as `api_id`** or it
returns "Unable to get successful response from executing tool" — that is a missing-param
error, not the ep149-style 502. Helper scripts in the session scratchpad: `mcp_parse.py`,
`build_payload.py`, `verify_panel.py`. **Never build these payloads in a bash heredoc — it
ate a backslash on the first attempt, exactly as [[reference_xano_write_access]] warns.**

**SHIPPED — fn56 v7 + ep231 `cache_v` 7 → 8: the badge_scale dedupe.** Backup
`fn56_v6_pre_legend_2026-08-15.xs`. `badge_scale` was carrying TWO jobs in one 29-word
string rebuilt identically per component — the **cohort** (varies per row: 92 / 86 / 190 /
146, and the whole reason the code comment says it matters, since `lookup()` silently falls
back state→national and capacity_band→venue_type→overall) and the **rung definition**
(invariant — a legend). Now `badge_scale` = `'Ranked against 92 comparable California
venues.'` and the definition ships once as the new root key **`badge_scale_legend`**.
**Chose to SHORTEN the key rather than remove it** — same key, same type, so nothing that
binds it can break; it just gets shorter. Verified: payload **524 → 455 words**, rung
definition on zero rows, regression clean apart from the intended keys, leak test PASS.

**The remaining word-kill items are NOT server-side and must not be "fixed" in fn56:**
`badge_label` reading "Typical in California" on all four rows is a **coincidence, not
duplication** — each row is scored independently and they genuinely all landed typical here.
Deduping that server-side would be wrong. Collapsing it is a RENDERING decision (one shared
scale/mark instead of four text chips), as is putting row sentences behind tap-to-expand.
Both need WeWeb.

**SHIPPED IN WEWEB — `PI Badge Scale Legend` `8af7d6b1-489f-42c1-9b43-d4e6f4792b1f`**, a ww-text
bound to the new `badge_scale_legend`, sitting between `PI Components` `94ab5295-…` and
`PI CTA Block` `d9c866c3-…` on the Venue Vendor Page. Conditional hides it for locked users
(they have no rows for it to explain). **The WeWeb app still needs PUBLISHING for this to go
live.** `addElements` **requires a `uid` in the JSONL** or it errors "could not extract UID";
WeWeb then assigns its own uid, so read the response rather than assuming yours was used.

**BLOCKED — `publishComponent` returns Bad Request, and it is NOT our payload.** Tried the
full component (4 files, ~20KB) and then a deliberately minimal 4-file scaffold; both fail
identically, and a 2-file attempt failed with Internal Server Error. So the fault is
structural in that endpoint for this project, not size or content. **Do not burn more
attempts guessing.** The proven route for this workspace is still the per-component GitHub
repo in [[feedback_weweb_component_deploy]] — 4 components are already installed that way.
Note `listCodedComponents` shows `Paywall Blur Gate` as `type: github, isReady: false`
(no AI.json), so github-sourced components are read-only to the MCP and cannot be completed
through it.

## 2026-08-15 — RANGE GATING (user decision, after Des+Kate feedback)

**SHIPPED: fn56 v9 + ep230 v2.** After the 3 free views are spent, the price RANGE is
withheld too, not just the exact figures. Backups `fn56_v8_pre_range_gate_2026-08-15.xs`,
`ep230_v1_pre_range_gate_2026-08-15.xs`.
- New fn56 input **`hide_range`**. **Polarity is HIDE, not SHOW, deliberately** — ep231 does
  not pass it and an unset Xano bool is falsy, so the anonymous tier keeps its range by
  default. A `show_range` flag would have silently gated every visitor.
- When set: `all_in = { gated, range_gated: true, at_default: { guests, label: 'Hidden
  until you unlock' }, ladder: [] }`. **`at_default` keeps its SHAPE so the existing WeWeb
  binding renders a string and needs NO edit** — it just carries no number.
- CTA branches too: "That range comes from…" is wrong when there is no range, so it becomes
  "N couples uploaded contracts for this space. Unlock to see what this venue actually
  charges, line by line."
- ep230's `$access` lambda now returns `hide_range` on all four paths (false for forever /
  active / free_allowance, true for allowance_spent / lapsed) rather than computing it as an
  expression on the `function.run` call.
- Verified: public ep231 payload **byte-identical** (no cache bump needed), and the gated
  branch exercised directly via `runWorkspaceFunction` — no low/high, empty ladder, no
  venue figures.

**`runWorkspaceFunction` takes `name` + `input` (a JSON object), NOT `function_id`/`args`.**
Guessing the arg shape over curl wastes calls; use the connected MCP tool.

**KNOWN HOLE, user informed:** ep231 is public and uncached-by-caller, and WeWeb fetches
BOTH collections, so the range is still in the ep231 network response and **logging out
still shows it**. The UI is gated; the data is not. Closing it means gating ep231, which
removes the range for logged-out visitors entirely.

**IMPORTANT — three Des/Kate diagnoses have now failed against data.** This one's premise
("people can see the price without clicking into the PDF") was also wrong: locked users
already saw only a range, and the 3-view gate already existed server-side in ep230. Their
"sales are randomly slow" rested on a single Saturday at n=1 charge / $129 against a
~$304/day mean over 11 days — roughly a 1-in-25 day at ~5.8 charges/day, and the Aug 14
rebuild day itself did $314 on 7 charges. **Always check the Stripe daily series before
accepting a conversion diagnosis from this pair.** The user chose to gate anyway, which is
a legitimate product call — see [[project_pi_review_feedback]].

## 2026-08-15 — THE FREE-VIEW GATE: what was actually wrong

**THE INCREMENT IS NOT BROKEN. Do not "fix" it.** 18 of the 70 accounts created 2026-08-15
already have >3 vendor views (max 26); Aug 14: 30 of 87. **ep150 increments it server-side.**
The WeWeb action that PATCHes `/user/update_user_attributes` inside function workflow
`Set Vendor View Page` `76817e7b-…` is **`disabled: true`** — and must STAY disabled, or the
counter double-counts and burns the allowance twice as fast. The `viewed_vendor_page`
Mixpanel track in that same workflow is also disabled.

**ep230 IS returning the right verdict** — 16 of those 18 have no payment and no
`date_until_access`, so it answers `entitled: false`. The gate was being BYPASSED, not
mis-computed.

**THREE REAL CAUSES:**
1. **`Section Blur Overlay` `bed88937-…` is ORPHANED — 0 occurrences in the page tree.** It
   still resolves via `getPageElementsByUid` (which is why it looks alive), but it is no
   longer attached to the page. It was the old panel's 3-view gate.
2. **Its condition was broken anyway:** `date_until_access < date()`. That column is **NULL
   for anyone who never paid**, and `null < "2026-08-15"` is **false** in JS, so the blur only
   ever fired for LAPSED users, never for never-paid ones — the majority. **Never reuse that
   comparison; parse the date explicitly.**
3. **`Pricing_Intelligence_Full` has `isAutoFetch: false` + `isPersistentOnNav: true`**, and is
   fetched by ONE action behind two `if`s. So an entitled payload from an early free view can
   outlive a vendor change and leave the panel looking unlocked on every later click. Its auth
   also rides a **custom `Auth_Token` header, not `Authorization: [redacted] — unverified
   whether Xano accepts it; if it does not, ep230 silently falls back to the free collection
   and paying users never see exact figures either (and `hide_range` can never take effect).

**FIX SHIPPED — client-side free-view guard on 3 elements** (needs WeWeb PUBLISH):
`PI Headline Value Row` `e884c95e-…`, `PI Components` `94ab5295-…`, `PI Band Block`
`910309ed-…`. Rule: `hasAccess || views <= 3`, where hasAccess = `forever_access_purchased`
OR `Date.parse(date_until_access + 'T23:59:59Z') > now`. On the two content blocks the
server verdict (`all_in.gated !== true`) still applies after the client guard. Deliberately
client-side: it is the only check that survives a stale persistent payload.

**The venue COST CALCULATOR is a separate, still-open leak** (user deprioritised it): its
sliders seed straight from ep142 — `Per Person Cost Slider` and `Base Venue Range` bind
`collections['9a5f3d77-…'].data[0]['Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday']`
and `['Venue_Fee_on_a_Peak_Season_Saturday']` with NO entitlement check. **ep142 is
`auth: false`** and returns raw pricing for any vendor_id to anyone. That is a real
"prices without paying" hole, independent of the PI panel.
**`getPageSemantic` REDACTS binding code** (`bindings=[BINDINGS IN ELEMENT]`) — grepping it
for field names proves nothing; fetch the element to see the actual binding.

## 2026-08-15 — badge fix + progressive disclosure (WeWeb, needs PUBLISH)

**BADGE FIX.** `PI Badge Unlocked Label` `3af80b02-…` and `PI Badge Unlocked Icon`
`7339f151-…` read `variables['73f04eb1-…']` = **`hasPaid`, a stored global Boolean**
(default false, folder Payment/, description "Tracks whether the user has paid to unlock the
price bar section"). It is set once and never re-derives, so it read "Member" on accounts
that had paid nothing. **Both are now static** ("Member" / `lucide/shield-check`) because
their parent `PI Paid Badge` `09d017eb-…` is ALREADY gated on
`access_reason === 'forever' || 'active'` — inside a paid-only badge the label can only ever
contradict the server. `PI Locked Badge` `a526acc1-…` and `PI Free Allowance Badge`
`a38d9019-…` are separate elements and were already correct.
**Do NOT "fix" `hasPaid` itself** — it also gates the price bar section elsewhere in the app;
blast radius unknown. Point elements away from it instead.

**PROGRESSIVE DISCLOSURE.** New global `piShowDetails`
`c313b57a-3235-4ba3-9bdd-503a62351e07` (boolean, default false, NOT persistent on nav so
each vendor starts collapsed). `PI Component Row` `d2d1d1df-…` now renders when
`key === 'total' || piShowDetails` — **the total always shows, the other rows open on
demand**. New `PI Details Toggle` `7a8dc922-…` sits between the band and the rows, mirrors
`PI Components`' gating exactly so a locked user gets the CTA not a dead control, and its
click workflow re-fires **`open_pricing_intelligence_breakdown`** — the event that died when
the panel was replaced on 2026-08-14, so the engagement metric is measurable again.
`PI Badge Scale Legend` `8af7d6b1-…` now follows the toggle too (it defines the badge chips).
**Locked behaviour is deliberately unchanged.**

**TRAPS HIT:**
- **`addElements` SILENTLY DROPS a `workflows` array** — the element was created without it.
  `editElement` then REFUSES `path: "workflows"` ("not a name, style, content, states,
  classes, subClasses, link or attributes"). Use **`createFrontendWorkflow`** with
  `scope: "element"` + `elementUid` + `trigger`.
- **The action type is `update-variable`, not `variable`.** Existing elements on this page
  use `"type":"variable"` (legacy) but `createFrontendWorkflow` rejects it. Call
  `getFrontendWorkflowActions` for the valid list.
- `addElements` also needs a `uid` in the JSONL, and WeWeb then assigns its OWN uid — read it
  from the response.

**THERE ARE TWO PRICING INTELLIGENCE PANELS.** Besides Venue Vendor Page
`e49b4e74-…`, the **Other Vendors Page `d1d9bbf6-7895-422f-9e4c-44bc095eb857`**
(`vendors/{{param|VendorID}}-copy`, Transparency Project folder) carries a **complete
mirror** with its own uids — 70+ PI elements, same collections. **Any PI change must be
applied twice.** Its key uids: Card `91857962`, Components `95734d84`, Component Row
`3383979a`, Band `461ea958`, Headline Value Row `ea224c25`, Legend `88dfaf9c`,
Badge Label `4d85f7bc` / Icon `f7094484`, Details Toggle `142a08da`.
Elements are NOT shared (distinct uids), so edits do NOT propagate — the page is a
duplicate whose content drifts. **Worth asking whether it should just be deleted.**

Both panels are now in sync on: badge fix, free-view guards, progressive disclosure
(rows + total + legend behind `piShowDetails`), and the toggle + `open_pricing_intelligence_breakdown`
tracking. **Still ONLY on the venue page:** `PI Component Strip` `b2988191-…`, the
gradient distribution bar.

**To find PI elements on a page:** `getPageSemantic` output uses a LITERAL backslash-n as
its separator, not real newlines — replace `chr(92)+'n'` with `\n` before regexing, or every
pattern silently matches zero. Helper: `scratchpad/parse_semantic.py`.

## 2026-08-15 — THE BAND IS MISCALIBRATED: almost nothing reads "budget"

**MEASURED over 120 live venues via ep231: luxury 45.8%, average 48.3%, budget 1.7%.**
Quartile bands should be ~25/50/25. **78% of venues price above their OWN cohort median**
(should be 50%); median ratio venue/cohort = **1.35**.

**ROOT CAUSE — the all-in is computed TWICE, on two different bases:**
- **fn55 `perGuest(s,g)`** (builds table 63) uses **REAL DATA ONLY**: `food=0` if `!has_fb`,
  `cer=0` if `!has_ceremony`, `vf=0` if `!has_venue_fee`, `svc=0` if `!has_service`. It only
  requires **2 of 3** headline components (`have < 2 return null`), so cohort members
  routinely contribute totals carrying ZEROS.
- **fn56** fills every missing component with the **market median** and sums them.

So the panel's number is ranked against a distribution built on a systematically lower
basis. Proof in the gradient — median venue/cohort ratio by how much real data exists:
**0/3 → 1.37, 1/3 → 1.30, 2/3 → 1.48, 3/3 → 1.08.** Complete-data venues are calibrated;
the bias appears exactly where imputation does.

**BUDGET TEST VENUES (only 2 in 120):** `V47` Aashirwad Palace, New Jersey ($107/guest vs
$201 median, cohort n=30 — the good one) and `V110` Alpine Country Club, Rhode Island
($160 vs $340, **cohort n=5, verdict is noise**). Near-budget, currently mislabelled
"average": `V75` (0.58x), `V50` (0.68x), `V3` and `V37` (0.69x).

**FIX — never rank on imputed data.** Short term: have fn56 compute the BAND POSITION from
real components only, matching fn55; the displayed all-in keeps its estimates. One function,
no table 63 rebuild. Long term: extract the all-in into ONE shared function both fn55 and
fn56 call — the two-implementations split is the actual defect, and it already taxed the
product once (the 2026-08-14 tax/gratuity work deliberately left the total's definition
unchanged *specifically* so table 63 would not need rebuilding).

**THE SCALE ANSWER IS AN INVARIANT, NOT A FIX:** verdict distribution across the catalogue
must be ~25/50/25 by construction. Sampling ep231 over ~120 vendors catches this in about
two minutes (`scratchpad/sample_bands.py`, `diagnose_band.py`). Run it nightly; alert if
budget share < ~15% or luxury > ~35%. That catches the NEXT drift whatever causes it.

**Cohort depth, measured:** component cohorts median 85 vs all-in 62, deeper in 68% of
venues but 18% still under 30 spaces. So component-based ranking is better because it is
**calibrated (real vs real)**, NOT because it is meaningfully deeper — do not oversell depth.
The `>=5 spaces / >=3 vendors` publish floor is far too low for a quartile.

### CALIBRATION FIX SHIPPED — fn56 v10 + ep231 `cache_v` 10 (2026-08-15)
Backup `fn56_v9_pre_band_calibration_2026-08-15.xs`. New `bandPerGuest(g)` mirrors fn55
literally (real components only, zero for missing, null below 2 of 3) evaluated at `ref`,
and is used ONLY for `band.position` — the displayed all-in keeps its estimates.
`perGuest` (which feeds `all_in.at_default.per_guest`) was deliberately left alone.

**MEASURED AFTER, of venues that can be placed: budget 1.7% → 18.0%, luxury 45.8% → 36.0%,
average 46.0%.** Much closer to 25/50/25.

**BUT 58% OF VENUES ARE NOW UNPLACEABLE** ("We cannot place this on the scale yet"). The
all-in metric structurally needs **2 of 3** headline costs, and fill rates are ~21% F&B /
27% ceremony / 40% service, so only ~40% of spaces ever qualified. fn55 always excluded the
rest; imputation was hiding it by labelling everyone. **This is the case for component-based
ranking (Tier 2): a component benchmark needs only ONE known cost, so it would place nearly
the whole catalogue AND is already calibrated (real vs real).** Do Tier 2 before the
shared-function extraction — extraction only prevents recurrence, Tier 2 fixes coverage too.

## 2026-09-07 — TIER 2 IS MEASURING CORRECTLY. The calibration FAIL is a FALSE POSITIVE.

`band_calibration_check` fails on `[component] budget share 11.3% is below 15%`. **It is not
a defect. Do not "fix" Tier 2 over it, and do not re-investigate the basis — that is done.**

**VERIFIED by direct measurement:** across 500 rows of `venue_benchmarks` (table 63) spanning
all 8 metrics, **not one row has `p10 == 0` or `p25 == 0`**. Min p10: venue_fee 150, pp_fb 20,
ceremony_fee 250. The benchmarks are built from **POSITIVE VALUES ONLY**, so Tier 2's
`val > 0` guard compares a positive value against a positive-only distribution — like with
like. **The v10 failure mode (ranking against a differently-based distribution) is NOT
present.**

Two hypotheses were raised and both are DISPROVEN — do not resurrect them:
1. *Cohort-fallback breadth.* Wrong: the flipped venues carry state-scoped cohorts of
   43–1,474 spaces.
2. *Zero-rental-fee venues dragging the benchmark p25 down.* Wrong: see the measurement above.

**So the 11.3% is a real property of the Tier 2 SUBPOPULATION.** Venues disclosing fewer than
2 of 3 headline costs genuinely sit away from the cheap tail on rental fee. Luxury is 24.2%
(expected ~25%), so only the bottom quartile is thin — not a general skew.

**The check is applying its invariant outside its assumption.** "A quartile band must give
25/50/25" holds when you sample the population the quantiles were computed over. Tier 2 is a
DELIBERATELY SELECTED subpopulation, so it does not.

**LOGGED, NOT BUILT — the right fix:** keep the strict 25/50/25 assertion for `all_in`, where
it is valid and where a real regression would surface. For `component`, replace it with a
**drift check** — record the observed 11.3 / 64.5 / 24.2 in the baseline and assert future
runs stay within a few points. That is the invariant that actually holds for a selected
population: not "these should be quartiles" but "these should not move unless something
changed". ~20 lines in `.claude/band_calibration_check.py`.

Until then the script exits 1 on every run. **A permanent known-false FAIL is worse than no
check** — it trains readers to ignore it — so this should not sit unfixed for long.

## 2026-09-07 — GUEST COUNT: default 50 -> 100, and the stepper is ALWAYS shown

**MEASURED, 400 most recent users: median guest count 100, mean 123, p25 75, p75 150.
0.0% plan under 25 guests and only 4.9% plan under 50.** So the old default of 50 sat
around the 8th percentile and the panel was costing nearly every visitor's wedding at a
size they were not having. (Separately, 6,645 of ~25,843 users have `Wedding_Guest_Count`
0 — those are legacy accounts; only 4% of recent signups are unset.)

**A '25 guests' reading is NOT a bug in the fallback chain.** The live WeWeb binding is
`variables['323efbd0'] || user.Wedding_Guest_Count || payload.default_guests || 100`,
clamped `Math.max(25, Math.min(400, ...))` — it correctly uses `||`, so 0 falls through.
25 only appears when the user has a REAL count of 1-24. The `hello@tulletogether.com` test
account has **Wedding_Guest_Count: 10**, which is why the panel read "Assumes 25 guests" in
every screenshot. Set that account to ~100 before judging the panel.
`PRICING_INTELLIGENCE_WEWEB_WIRING.md:62` still documents `?? 50`, which is STALE and
would be the bug if anyone implemented it — `??` passes 0 through.

**THE STEPPER WAS HIDDEN ON EXACTLY THE VENUES THAT NEEDED IT.** `PI Guest Stepper`
`f09662d9` rendered only when `provenance.from_venue_count > 0`, on the reasoning that "a
stepper implies precision". That is backwards: the strip counts only the 3 headline
components, so a venue with a real rental fee and nothing else scored 0 and lost the dial —
while its food term was a market per-head rate, i.e. the case where guest count is the one
input the visitor knows and the biggest lever on the total. The Beach rendered **$1,042 a
guest** at a clamped 25 with no way to change it. Now `return true`, always shown, and the
read-only stand-in `PI Guest Basis Line` `4be4e860` ("Assumes N guests") is retired to
`return false` rather than deleted, so the revert is one flag.

⚠️ **WeWeb STRIPS COMMENTS from a binding that reduces to a constant** — both edits were
stored back as `{"code":"true"|"false", "__wwtype":"f"}`, converted from `js`. The reasoning
above exists ONLY here; there is no trace of it in the project.

**SHIPPED + VERIFIED LIVE 2026-09-07.** fn56 v18 (`DEFAULT_GUESTS` 50 -> 100 plus the two
early-exit `default_guests` literals), ep231 `cache_v` 16 -> 17, WeWeb published.
**`ref` is UNCHANGED at 75** — nearest of [75,125,200] to 100 on a first-wins tie — so the
band cohort did not move. Confirmed in production on The Beach (Maryland, 300 seats):
stepper renders as `- 100 guests +` on a venue that previously hid it, and the headline
went from **$25,000 / $1,042 a guest** at the clamped 25 to **$45,000 / $432 a guest**.
$432 sits just above the measured all-in range of $317/$266/$248 at 75/125/200, which is
what the band independently calls "expensive end" — two code paths agreeing.

**KNOWN EDGE, NOT YET FIXED: the stepper is now a DEAD CONTROL for `hide_range` users.**
When ep230 sets `hide_range` (free PDF views spent) fn56 returns `all_in.ladder: []` and an
`at_default` carrying only `{guests, label:'Hidden until you unlock'}` — so the +/- render
but nothing moves. The fix is a NARROWER condition (show unless `all_in.range_gated`), NOT
a revert to the old `from_venue_count > 0`, which is what hid it on the venues that needed
it most.

## 2026-09-07 — TIER 2 COMPONENT RANKING SHIPPED (fn56 v17)

The Tier 2 the v10 note called for. When the all-in band cannot be computed (needs 2 of 3
headline costs), rank on ONE real component instead. `band` gains `basis`
('all_in' | 'component') and `basis_component`; on the component path every percentile and
cohort count comes from that component's benchmark row, `reference_guests` is 0, and
`venue_per_guest` is absent (wrong units).

**Two guards beyond the original spec.** `service_pct` is NEVER a basis — it is a
percentage, not a price level, and ranking a venue "expensive" for charging 24% vs 22%
would be the same category error v10 fixed. Ceremony counts only as a FLAT fee, because
`ceremony_fee_type` can be `per_person` or `included` while `bkCer` is a flat-fee
distribution. Priority order is `pp_fb` → `venue_fee` → `ceremony_fee`.

**MEASURED BASELINE before the push (120 venues, 2026-09-07):** unplaceable **58.3%**
(70/120) — independently reproducing the 58% measured on 2026-08-15. Of the 50 placed:
budget 18.0%, average 48.0%, luxury **34.0%** — `band_calibration_check` PASSED (luxury had
drifted just under the 35% ceiling from the 36% recorded in August, so it is marginal and
Tier 2 could tip it either way).

**New test: `.claude/tier2_check.py`** — coverage, non-regression (every venue placeable
before must stay `basis: all_in`), units, basis guards, leak, and position-by-basis so a
skew can be ATTRIBUTED rather than guessed. Writes `.claude/tier2_baseline.json` on first
run; run it BEFORE the push or the comparison has nothing to compare against.

**Both check scripts were rewritten to use urllib instead of shelling out to `curl`** —
`subprocess.run(check=False)` swallowed the error and surfaced it as a confusing
FileNotFoundError on a missing temp file. They now print the real HTTP error, which is the
only reason the ep231 lockout was found at all — see [[project_endpoint_lockdown_401s]].

Backup: `.claude/xano_backups/fn56_v16_pre_tier2_2026-09-07.xs`. Pushed as
`fn56_v17_tier2_2026-09-07.xs`, 1067 lines. **ep231 `cache_v` 15 -> 16 in the same release**
— editing fn56 does not purge the 4h response cache.

**INVARIANT SHIPPED: `.claude/band_calibration_check.py`** (repo). Samples ep231 over N
vendors, tallies the distribution, exits 1 on breach. Thresholds: budget ≥15%, luxury ≤35%,
unplaceable ≤60%. **It currently FAILS at 36.0% luxury — one point over.** Residual skew or
a smaller remaining mismatch; deliberately NOT papered over by moving the threshold.

**2026-08-15 END STATE: user published WeWeb and parked further work.** Open, in rough
priority: Tier 2 component ranking · the 36% luxury residual · the ep21 unearned-access
grant (`date_until_access = now()` on the 4th PDF view) · the cost-calculator/ep142 raw
pricing leak · the duplicate Other Vendors Page.

**NEXT:** Phase 0 (WeWeb-only word kill — don't render `badge_scale` as prose, collapse the
duplicate badges, put row sentences behind tap-to-expand), then wire `pi-price-position` into
the `PI Components` repeat `94ab5295-…`, then Phase 2 `pi-guest-curve` over `all_in.ladder`.
Deploy needs its own repo `marthv/ww-pi-price-position` — see [[feedback_weweb_component_deploy]].

**Verify live state first:** [[project_pricing_intelligence_spaces]] records the 2026-08-14
WeWeb edits as still needing PUBLISHING, so the baselines above may predate the v5 work.

Related: [[project_pricing_intelligence_spaces]], [[project_pi_review_feedback]].

## 2026-11-09 — LOCKED VENUE PANEL SHOWS STATE AVERAGES (editor, needs publish)
Owner: venue PI locked state showed only a sentence + CTA; florist panel shows median/middle-50%/blurred 'This florist'. Added
ww-html `PI Locked State Averages` 4ad60bf4-b42c-435b-b132-d20d8f7b6ff5 as FIRST child of `PI CTA Block` 527cea99 on Venue
Vendor Page e49b4e74 (inherits the lock gate). Pure display from data ALREADY in the locked payload: space.band (metric
all_in_pg_N / pp_fb / venue_fee -> title adapts) + component benchmarks (rental, F&B, ceremony, service). 'This venue' values
are PLACEHOLDER text under blur, never real numbers. Rollback = delete the element. Source + Node test + previews in that
session's scratchpad (pi_locked_avgs.js). NOT on the Other Vendors Page mirror. Data oddity seen: MN all-in band median
$76/guest with F&B $45/guest - looks low.
- 11-09 owner: 'What comparable venues charge' rows REMOVED from the locked block ('just enough, not too much'); locked = headline median + sentence + blurred This venue + middle-50% bar only. Component averages stay paid-only.
