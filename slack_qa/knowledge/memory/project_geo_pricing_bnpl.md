---
name: project_geo_pricing_bnpl
description: "Oct 1 2026 geo-pricing + BNPL experiment - Stripe links BUILT, fn63 DELIBERATELY REVERTED and must be re-applied Oct 1, WeWeb still open"
metadata: 
  node_type: memory
  type: project
  originSessionId: f61915e0-4f86-4fea-bf5d-33724fe49bc9
  modified: 2026-09-22T04:29:03.694Z
---

**Oct 1 2026 experiment: raise Forever to $149 in NY/CA/International, add BNPL.** Origin is
#payments 2026-09-21. Vivek wanted geocentric pricing ($149 ≈ +15% on $129, BNPL costs ~8%,
explicitly *"i don't think we should price gouge couples"*). Des wanted **Texas held at $129
WITH BNPL as the control**, so the BNPL effect can be separated from the price effect.

**Three arms. Do not collapse Texas into the raised list — that destroys the control.**

| Arm | Who | Price | BNPL |
|---|---|---|---|
| Raised | `Wedding_Location_Updated` ∋ New York / California / International | $149 | on |
| Control | Texas (and not raised) | $129 | on |
| Baseline | everyone else | $129 | **off** |

## SELF-ACTIVATES OCT 1. NOTHING TO REMEMBER ON THE DAY.

Everything is **date-gated to `1790827200000` = 2026-10-01 00:00 America/New_York** and is a
verified no-op before then. The gate lives in three places that must stay in step:
fn63, the WeWeb price text, and the WeWeb button URL.

**fn63 IS LIVE AND DATE-GATED** (applied 2026-09-22 via
`apply_geo_upgrade_price.py --apply`). It adds `$geo_live_from`, `$geo_experiment_live`,
a cohort lambda, and returns **`geo_experiment_live` + `geo_raised_cohort` in the response**
specifically so the gate is testable without waiting for the clock.

Gate proven in BOTH directions 2026-09-22 (user 30364, NY+PA+NJ):
- gate closed (today): `geo_experiment_live:false, geo_raised_cohort:true` → **$129** ✓
- gate backdated via `--apply --live-now`: → **$149** ✓
- gate open + Maryland user 30361: `geo_raised_cohort:false` → **$129** ✓
- Oct 1 date restored and re-verified → $129 ✓

Credit maths re-verified while open: 30342 credit 59.88 → final **$89.12**; 28614 credit 30 →
final **$119**, i.e. $30 + $119 = $149, arbitrage closed. `--live-now` is TEST ONLY; always
follow with a plain `--apply` to put the Oct 1 date back.

## 🔴 KILL SWITCH — `python .claude/xano_backups/revert_geo_pricing.py --off`

**One command. No WeWeb publish, no editor session.** That was the hard requirement.

It does two server-side writes: renumbers **table 17 Product_ID 6→106 and 7→107**, and restores
fn63 from `fn63_pre_geo_price_2026-09-22.xs`. Both WeWeb bindings look their row up **by
Product_ID** and fall back to the untouched baseline row (Product_ID 5 = original $129 card-only
link) when the lookup fails — *that* is why no publish is needed. The collection auto-fetches, so
it takes effect on the next page load.

`--on` re-arms the rows; `--off`/`--on` are idempotent; no args = a read-only state report
(prints ARMED/OFF per side). **Verified 2026-09-22:** parked row 9 → report flipped to
"frontend experiment: OFF", restored → "ARMED". The `--off` write path itself was blocked for
Claude by the auto-mode classifier ([Modify Shared Resources]) — **the user can run it directly;
if Claude is ever asked to kill it, a Bash permission rule is needed first.**

Not undone by `--off`, deliberately: the two Stripe links stay active but unreferenced, the $149
price stays inert, ep30's Slack payment-method line stays, and the WeWeb bindings stay in place
correctly doing nothing.

## Stripe — BUILT AND LIVE (BNPL rendering confirmed by user in the Stripe UI)

- **$149 price `[stripe-id]`**, created under the EXISTING Forever product
  `[stripe-id]` — deliberately, so it inherits coupon eligibility.
- **Link A (raised, $149)** `[stripe-id]` → https://buy.stripe.com/eVqdR84fz336gSX1LO0Fi0u
- **Link B (TX control, $129)** `[stripe-id]` → https://buy.stripe.com/6oU4gy8vPfPS6ej8ac0Fi0v
- Both: `metadata.weeks = "forever weeks"` (so **ep30 needs no change**), promo codes on,
  automatic tax on, redirect `/purchase-complete?session_id={CHECKOUT_SESSION_ID}`,
  `payment_method_types: ["card","link","affirm","klarna","afterpay_clearpay"]`.
- **Had to DROP `payment_intent_data.setup_future_usage: "off_session"`** — Stripe rejects it
  alongside Affirm. The old Forever link still sets it.
- **The existing Forever link `[stripe-id]` ($129, `price_1U37uH…`) was NOT
  touched** — it inherits the account default, where BNPL is off. That is what keeps the
  baseline arm clean. **If BNPL is ever switched on in the DEFAULT payment method
  configuration it leaks onto every link and collapses the experiment.**
- **BNPL is per-link** via `payment_method_types` — an earlier claim that it could only be
  account-wide was WRONG. Account PMCs still report `available:false` for affirm/klarna/
  afterpay yet the links work, so do not trust `available` as the signal.
- Xano **table 17** rows added: id 9 / `Product_ID` **6** = $149 link, id 10 / `Product_ID` **7**
  = $129 BNPL link. New links use the CORRECT `?prefilled_email=`; the 5 old rows keep their
  broken `?prefilledemail=` typo (email prefill has never worked there).

## Coupons — risk cleared, but a live leak found

No coupon carries an `applies_to` product restriction, so all apply to the $149 price.
**But SPRING26, LOVE26 and ENGAGED25 are all EXPIRED/inactive while still advertised**
(/pricing shows "USE SPRING26", home page shows LOVE26; ENGAGED25 had 59 redemptions).
Only **TULLE10** (10%, $100 minimum) and **TULLE15** (15%) actually work. Unfixed.

## WeWeb — price text DONE, button is the one hand-edit left

**DONE via MCP (unpublished):** price text `af5d09b0-47b7-4e9a-a64d-116f710e498a` on the Forever
card. Three guards in order: date gate → cohort → **row-exists check against the collection**
(that last one is what makes the kill switch work without a publish). Bound at
`content.default.text.**en**` — binding `content.default.text` itself is REJECTED
("the text property itself cannot be bound", bind a per-language value instead).

**NOT DONE — hand-edit in the editor:** the button URL. Button
`75a9309e-4ca5-4de5-b761-53d3b3090fdd`, workflow `8605fcc5-25f7-4ed6-b4db-179d80ccd92a`,
action `052a0e9c-fbf3-4ca0-a930-4972806a8c50` (change-page), field External URL — still reads
`data?.[4]`. **Paste from `.claude/xano_backups/weweb_forever_button_geo_url.js`.**
`editFrontendWorkflow` fails **[WRONG_ACTION_TYPES]** on the Xano plugin action
(`cd33cf33-…-request`); confirmed 2026-09-22 and the failed attempt wrote NOTHING (workflow
re-read, byte-intact). [[reference_weweb_mcp_limits]]

**BUTTON EDIT LANDED CORRECTLY on the second attempt (2026-09-22)** — verified by reading the
live workflow: cohort code is in `052a0e9c.externalUrl`, the stray custom-js action is gone, the
chain is `fetch-collection → change-page → Post to Slack (disabled:true) → editor check →
mixpanel`, `data?.[4]` appears nowhere, and both hydration guards are intact.

**Routing proven by running the REAL binding in node** against the real table-17 rows
(`scratchpad/test_button_binding.js`, wraps the file in `new Function` with mocked
collections/pluginVariables/wwFormulas/Date so the date gate can be moved): 12 cohort cases ×
both sides of the gate all correct — NY/CA/International → PID 6 only after Oct 1, Texas → PID 7,
Maryland/"Not Sure"/empty → PID 5, `"New York,Pennsylvania"` (comma-packed) → PID 6, case and
whitespace variants → PID 6, raised beats Texas. Kill-switch simulation (rows parked 106/107)
sends every cohort back to baseline.

**One hole found and fixed IN THE FILE, not yet in the live binding:** an empty collection made
BOTH lookups fail and produced `"undefined<email>&client_reference_id=<id>"` — an invalid URL
that opens nothing. This flaw was in the ORIGINAL `data?.[4]` code too, so it is pre-existing,
and the collection is an authed endpoint that a token race can empty. The file now ends with a
hardcoded `FALLBACK_129` so a failed fetch still reaches a working checkout. **Re-paste needed
for that to be live; without it everything still works in every normal case.**

**FIRST PASTE ATTEMPT WENT WRONG 2026-09-22 — check for this.** The JS landed as a NEW
`custom-js` action `9a788206-9fc7-4e5f-9a23-63b9b8d06cb6` inserted AFTER the navigation step,
instead of into the change-page action's **External URL** field. So `change-page` kept
`data?.[4]`, cohort routing did nothing, and the pasted code ran after navigation. It also
flipped `Post to Slack` in this workflow from `disabled:true` to `false` → **click pings fire
twice** (this workflow + `Select Forever Plan_copy`). Three fixes + verification in
`.claude/xano_backups/weweb_forever_button_HOWTO.md`. The .js file now contains ONLY code (no
instructional header) precisely because the header is what invited the mis-paste.

`editElement` CANNOT reach workflow internals either — "Invalid path:
workflows[0].actions.…externalUrl.code", it only accepts name/style/content/states/classes/
subClasses/link/attributes. So between that and editFrontendWorkflow's [WRONG_ACTION_TYPES],
**there is no MCP route to a workflow action's fields on this element. It is hand-edit only.**

Remember when testing: the button is **disabled until the user hydrates**, so in the editor
preview or logged out, clicking does nothing regardless. That is the 2026-09-07 guard, not a bug.

**BNPL NOTE ON THE CARD — added via MCP 2026-09-22 (unpublished).** New `ww-text`
**`b14a17ae-a2f6-4726-86c1-6738407ef191`** "Lifetime BNPL Note", inside the Forever card
`e1297bdf` between the feature list `8fdbc226` and the button, copy: *"Prefer to spread it out?
Buy now, pay later at checkout."* (provider-agnostic on purpose — which of Affirm/Klarna/Afterpay
renders depends on eligibility, so do not name them). Styled like the subtitle: Sora 13px,
`--07b8fcf2` #7E8189, centred.

**ITS RULE IS DELIBERATELY DIFFERENT FROM THE PRICE RULE.** It shows for `Product_ID` 6 **AND 7**
— i.e. the raised arm AND Texas — because both links have BNPL enabled. **Texas therefore shows
the BNPL note while the price still reads $129.** That is correct, not a bug: Texas is the arm
that isolates the BNPL effect from the price effect. Baseline (PID 5) is card-only and shows
nothing. Same date gate and same kill switch as price and link.

Verified by running the real condition in node (`scratchpad/test_bnpl_note.js`): 9 cohort cases ×
both sides of the gate all correct, kill switch (rows parked 106/107) hides it, empty collection
hides it. Note WeWeb assigned its own uid — the one passed in `addElements` was ignored, and
`addElements` REQUIRES a `uid` field in the JSONL or it errors.

## ✅ PUBLISHED 2026-09-22 (user published the WeWeb app)

All three card pieces (price text, BNPL note, button URL) are now in production, **date-gated and
inert until Oct 1 00:00 ET**. fn63 is live and date-gated. Stripe links and table-17 rows are live.
Nothing to do on Oct 1; it switches itself on.

**NOT VERIFIED:** the publish itself. Per [[reference_weweb_mcp_limits]] a WeWeb publish can
silently fail and the standing rule is to confirm by grepping the live `main.js` for the new
code (app workflows are inlined there). Grep live main.js for `1790827200000` (the gate constant)
or `Product_ID` — if absent, the publish did not land and Oct 1 will be a no-op. User asked to
stop before this was checked.

**ALSO NOT DONE:** the optional re-paste adding `FALLBACK_129` to the button binding, so an empty
collection still yields an invalid URL (pre-existing behaviour, unchanged by this work).

**Publishing early is now SAFE** — both bindings are date-gated no-ops before Oct 1. That was
the whole reason for switching from "re-apply on the day" to a date gate. Publish whenever;
the experiment starts by itself. Until the button edit lands the card would say $149 and charge
$129 *after Oct 1 only*, so do the button edit before Oct 1, not necessarily today.

Deliberately ONE card, not a 4th: no 4-up grid re-layout at three breakpoints, no duplicated
workflow, and structurally impossible for one user to see two Forever prices. The `disabled` +
`opacity` hydration guards on the button are untouched and must stay.

## Still open
- Gate matching must be **contains, not equality**: some rows pack states into ONE list item
  e.g. `"New York,Pennsylvania"`.
- Sizing (350-user sample of 27,431): NY 17.7%, International 11.4%, Texas 9.4%, California
  5.7%; raised arm ≈ **30% of users**.
- **NY buyer sees $162.23 at checkout** ($149 + 8.875% tax), vs $140.45 today.
- Payment log cannot distinguish BNPL from card (both `Type = "forever weeks"`) — but ep30's
  Slack post now names the method, see [[project_ep30_bnpl_slack]].

[[project_upgrade_credit]] [[reference_stripe_payment_webhook]] [[project_pricing_buy_undefined_user]]

## 2026-09-30: DAILY WATCH LIVE in the health DM (13:32 UTC, Railway health-report-cron)
`bnpl_watch.py` in tulle_vendor_data_updates (commit dfb04a7), appended by `perf_digest.py --health`
in its own try/except. Reports per-arm new-Forever ("forever weeks") buys vs the 28-day pre-launch
rate, raised-arm SHARE (headline: baseline arm is the untreated control for traffic swings), routing
mismatches (amount >= $145 = $149 link; heuristic - tax + promo codes blur it), BNPL share parsed from
ep30's "Payment Method:" line in #tulle-users (C07D9U0FZF0, PRIVATE) via HEALTH_SLACK_BOT_TOKEN -
Tulle Bot probably lacks groups:history, so expect "unavailable (missing_scope)"; read Slack via MCP
instead. Local run: `python bnpl_watch.py [--as-of <ms>]` with XANO_METADATA_TOKEN.
**Pre-launch baseline (28d to 10-01): raised 1.21/day, TX control 0.07/day (2 buys!), baseline
0.89/day; raised = 56% of new-Forever buys (n=61).** Texas control is badly underpowered - ~15 months
to reach 30 buys at that rate. Payment-log Amount INCLUDES tax (NY $129 -> 140.45); "forever" = upgrade.
Meta API `/table/{id}/content` IGNORES sort/order - read all pages then filter.

## PHASE 2 — built 2026-10-02 (team decision in #payments; user picked prices)
Raised arm (NY/CA/Intl) ALSO gets **1 week $39 / 4 weeks $69**; Texas + baseline keep $30/$55. Goes live
**at WeWeb publish** (no date gate) -> set Railway health-report-cron env `BNPL_PHASE2_FROM_MS` to the publish
time (bnpl_watch 93c1ee1 reports pre / phase 1 / phase 2 rev/day, rev/buyer, plan mix, Poisson note).
- Stripe (created via claude.ai Stripe connector, which CAN create prices): `[stripe-id]` $39,
  `[stripe-id]` $69 (product [stripe-id]). Links card-only (account default PMs)
  mirroring originals: 1w `[stripe-id]` buy.stripe.com/6oU14meUdavycCHeyA0Fi0w (promo OFF like
  the old 1w), 4w `[stripe-id]` buy.stripe.com/5kQcN4h2lcDGfOT8ac0Fi0x (promo on);
  metadata.weeks "1 week"/"4 weeks" so ep30 + fn63 credit need no change.
- Table 17 rows 11 (PID 8, $39) and 12 (PID 9, $69). Backup table17_pre_phase2_2026-10-02.json.
- Kill switch: `revert_geo_pricing.py --phase2-off|--phase2-on` (only 1w/4w); `--off` reverts both phases.
- WeWeb (editor, unpublished at time of writing): price text b2204160 (1w) / 1cd8c335 (4w) bound; BNPL note
  b14a17ae text now "Pay in 4 interest-free payments of $37.25 (TX $32.25) with Klarna or Afterpay, or monthly
  with Affirm" (user's "<$35/month" was inaccurate at $149); logo row d8109232 (3 ww-image) same visibility rule.
  Buttons 0bb1ae9c / d58dbe22 need HAND paste of phase2_button_1w.js / _4w.js -> see phase2_buttons_HOWTO.md.
  Backup weweb_backups/membership_1w4w_bnpl_pre_2026-10-02.md.
- Membership live JSON also holds ORPHANED old cards ("Forever Access" 47a58128 with data?.[4], "2 Weeks Tier")
  not referenced by any section - they don't render; don't mistake them for a routing leak.
- Discovery + Sign In "Discount CTA Button" 2f18a956 goes to data?.[4] ($129 card-only) but its visibility var
  60af0988 is never set true on those pages -> appears dead (app workflows not checked).
- Evidence at decision time (10-02, 1.5 days): raised rev/day flat ($221 vs $227) but rev/buyer $92->$54; BASELINE
  also fell ($197->$80/day) with 0 Forever -> the drop was NOT attributable to $149 (P(0 Forever) 13%/21%).

## 2026-10-06: "NY payment rate cliff" checked - NOT supported
User wanted to change NY pricing. NY 1.79 -> 1.20 buys/day (7 vs 10.4 expected, P=0.19); 28d pre window was an unusually
strong NY September; NY signups flat 17.7->18.0/day; CA/Intl same prices rose ~3x; raised arm rev $227 -> $332/day.
Screenshot (hub ny, signups Aug 1-6, 4/75 payers) was a complete pre-experiment cohort, 4 payers. Rec: no change; re-check
~10-22 (pre-commit: NY rev < ~$110/day while baseline holds -> NY-only revert, which needs a NEW WeWeb rule; kill switch hits all raised).
**Phase 2 actually went live ~10-02 20:43 UTC** (first $69 session), not ~10-05 -> BNPL_PHASE2_FROM_MS must match that.
Possible double charge: user 25790 paid $69 twice 17s apart ([stripe-id] / [stripe-id]).

## NY TIER $35/$60/$135 (user decision 2026-10-06) - IN PROGRESS
NY (contains "new york") gets own tier; CA/Intl stay $39/$69/$149. Launch = add table 17 rows Product_ID 10/11/12
(links in .claude/xano_backups/ny_tier_stripe_ids.md); missing/parked rows -> NY falls back to raised arm (today's state).
- DONE: Stripe prices+links (10-07). WeWeb editor (UNPUBLISHED): price texts b2204160/1cd8c335/af5d09b0 + note b14a17ae
  now NY-aware (backup weweb_backups/membership_pre_ny_tier_2026-10-06.md). Tested in node (scratchpad ny_test.js).
- DONE 10-07 00:21 UTC: fn63 pushed by user; verified 30364 NY -> ny_cohort true, ny_tier_live false, $149; 30361 MD $129.
- TODO:
  user hand-pastes ny_button_1w/_4w/_forever.js into the 3 button External URL fields; user publishes; verify live
  /data json; then add rows (launch). Fix revert_geo_pricing.py: `--on` rebuilds fn63 WITHOUT NY block; add --ny-off/--ny-on.
- **NY TIER LIVE 2026-10-07 ~00:35 UTC (rows) / ~00:55 UTC (WeWeb publish, verified in live /data json: 4 texts +
  3 buttons in change-page externalUrl, workflows 0bb1ae9c 1w / d58dbe22 4w / 75a9309e Forever).** Table 17 rows
  13/14/15 = PID 10/11/12 (backup table17_pre_ny_tier_2026-10-07.json). fn63 verified $135 for NY 30364, $129 MD.
  Kill: `revert_geo_pricing.py --ny-off` (NY -> $39/$69/$149, no publish); `--on` now re-pushes fn63_ny_tier_PREVIEW.xs.
  bnpl_watch NY section committed 626f3b6 in tulle_vendor_data_updates (NOT pushed at time of writing - ask user).
  Re-check NY ~10-21: NY rev/day vs raised period ($105/d) and pre ($160/d 28d).

## 2026-10-07 night: NY drop NOW SUPPORTED - reverses the 10-06 "not supported" call
Age-matched NY-hub cohorts (pay within ~3.4d of signup; 77% of NY payers pay within 1 day, so censoring is small):
Jul30-Sep24 weeks pooled 49/946 = 5.18%; 10-01..10-07 = 1/122 (0.82%), P(<=1) ~1.5%. Signups flat, PDF-view 64% flat.
By pay date: NY 1.79 -> 1.01 buys/day, $160 -> $88/day (P~0.04-0.08); NY Forever share 23/50 pre -> 2/7 post.
CA 0.36->1.16/day, Intl 0.50->1.16, baseline flat 2.57->2.90, TX 0.43->0.29. Raised arm $227 -> $280/day (10-06's $332 was WRONG).
NY-region Mixpanel clicks to pay roughly held (low-n) -> price/checkout suspect, UNVERIFIED. NY tier $35/$60/$135: 0 buys in
first ~25h (P=0.15 at old rate, no signal). user 25790 $69 x2 still unresolved. Scripts: session scratchpad q1.py/q2.py.

## 2026-10-08 Stripe check (read-only): checkout abandonment NOT the cause
Raised-price links complete 58-65% post vs 52-57% pre (n=26 sessions). Sessions/day up 9.9 -> 12.8. NY BILLING-address
completed purchases 1.64 -> 1.30/day (-21%, not -45%); 4 NY-address buyers paid on old $129/$30 links (cohort = wedding
location, not billing). Expired sessions carry no address -> NY abandonment not countable. 25790's 2nd $69 already
refunded. Lowers confidence that price drives the NY drop (was ~70%).

## 2026-10-08 night: "only 2 paid today" check + 1-week completion
- 10-08 had 2 buys (both $55 baseline), only 4 Stripe checkout sessions. Live pricing page (cacheVersion 1311) checked:
  1w/4w/Forever buttons identical apart from PIDs (10/8/1, 11/9/2), cards unconditional, flow works. Slack #tulle-users
  "clicked to buy": 10-06 3, 10-07 12, 10-08 3 with pricing-page visitors 91/69/50 -> judged a low day, not a break (~25% it's the publish).
- 1-WEEK completion since 10-05: 12 sessions / 2 paid (17%) vs 47/29 (62%) 9-25..10-04; mostly raised $39 + NY $35 (1/8). Fold into 10-14.
- NY cohort 10-01..10-08: 7 buys, ~$606 incl tax (~$76/d, ~0.9/d); NY tier since 10-07: 1 buy (32035, $60 4w). CA/Intl strong at same prices.
- Cohort leak: $140.45 (= $129 + NY tax) buyers with blank or NJ wedding location get baseline price (1319, 25082) - NY drop partly overstated, size unmeasured.

## 2026-10-11 night NY readout (subagent, table 16/1; Stripe connector NOT authed)
| arm | pre 28d | 10-01..06 ($39/69/149) | 10-07..10-11 (NY $35/60/135) |
NY 1.79/d $160/d | 1.02/d $92/d net | 2.37/d $169/d (12 buys, 5 on Sun 10-11; only 3/12 Forever; rev/buy $71 vs $90)
CA 0.36/d | 1.36/d | 0 ; Intl 0.50/d | 1.36/d | 0 ; TX 0.43 | 0 | 0.59 ; baseline 2.57 | 2.72 | 2.77
NY 1-day pay rate of new sign-ups: pre 46/953 4.83%; $39 wk 1/98; NY tier 1/56 (CI 0.3-9.4%). Recovery is OLDER accounts (1.98/d) not same-day (0.40/d vs 1.11 pre).
Leaning (~65%, NOT a rec): keep NY tier, re-read ~10-21 (n>=150 sign-ups; revert case if 1-day rate stays <2%).
CA/Intl 0 buys since 10-07 (P~1.3% by chance): CHECKED live Membership /data json 10-11 - price texts ($149/$39/$69/BNPL $37.25) and button routing (PID 6/8/9) correct for CA, Intl, CA+FL; table 17 rows intact. Mixpanel $region=California: Clicked to Pay ~1/d -> 0 since 10-07, CA page visitors ~17 -> ~12/d. => upstream (fewer clicks), not a checkout break; Stripe-side (links inactive / ep30 not logging PID 6/8/9) UNVERIFIED - check Stripe dashboard.
- 10-11: BNPL take-up = ZERO. ep30 Slack posts (#tulle-users, "Payment Method:") since 09-22 show only Card / link / amazon_pay; "paid over time" (BNPL label) never appears; no klarna/afterpay. User removing BNPL. Editor: "Lifetime BNPL Note" b14a17ae already gone from Membership page in the editor (still live until publish). Stripe-side: BNPL is per-link payment_method_types on raised/TX/NY links -> turn off in Stripe dashboard (connector not authed). With BNPL gone, Texas arm (BNPL-only control) has no purpose.
- 10-11: nav "Beta" pill 34a0117d (Tulle Assistant) now hidden when sidebar collapsed (conditionalRendering = sidebar-expanded var 2e03a506), editor only.
- 10-11 ~23:00 ET BNPL OFF in Stripe (user asked; via claude.ai Stripe connector, now authed): only 3 links had explicit BNPL (ny_135 plink_1UNiMp..., raised_149 plink_1UILBR..., tx_129_bnpl plink_1UILC3...) -> payment_method_types now [card, link, cashapp] (dynamic links offer card/link/cashapp/amazon_pay; amazon_pay not settable on payment links). All other links were already dynamic (null) - untouched. Backup .claude/xano_backups/stripe_payment_links_pre_bnpl_off_2026-10-11.json.
- 10-11 Stripe check: CA/Intl raised links since 10-07 = 2 checkout sessions total (14998 test + 31190 open/unpaid), 0 paid -> NO missing payments; the CA/Intl zero is real demand, not a webhook/log break.

## 2026-10-14 night: OLD baseline Stripe links DEACTIVATED (user OK'd)
plink_1SvVwG ($30 1w cNi00), plink_1SvVxS ($55 4w fZueVc), plink_1TebFd ($129 Forever 6oUfZg) -> active=false with
inactive_message pointing to /pricing. Last real checkout on them 10-14 04:08 UTC (before --prices). Table 17 no longer
references them. **KILL-SWITCH CAVEAT: `apply_paywall_golive.py --prices-revert` points rows back at these links ->
REACTIVATE them first (PostPaymentLinksPaymentLink id=<plink> active=true) or revert sends buyers to a dead link.**
