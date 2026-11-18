---
name: project_upgrade_credit
description: "Lifetime upgrade credit-back — fn63 + ep284 + ep285 charge a discounted Stripe price. LIVE end to end. fn63 v2 (2026-09-11) credits only the CURRENT access period, not a 120-day sum."
metadata:
  type: project
---

Phase 2 of the upgrade path, built **at the user's explicit direction** on 2026-09-06 after they
were shown the spec's own rule that the credit must never appear on `/pricing`. They reaffirmed:
*"i am interested in having the amount shown in the credit -- i think people will like the
discount."* **That is a decided question — do not re-raise it.**

The constraint was honoured a different way: **the offer is gated on AUTH + having paid, not on
which page it sits on.** Both endpoints return 401 unauthenticated (verified), and `eligible` is
false for anyone with no payments and for anyone who already owns Lifetime. A prospect browsing
`/pricing` sees the ordinary grid and no mention of credit.

## LIVE PRICES — verified from the payment log 2026-09-06, NOT from the spec

**Forever = $129. 4 weeks = $55. 1 week = $30.** The spec's "$30/$75" (section 5) is **stale**, and
its appendix "$119" is also wrong. There are **no 2-week or 6-week purchases** in recent history —
those tiers are retired, so any cohort analysis built on them is measuring something nobody can buy
(see [[project_mixpanel_tracking_gaps]]). Some rows carry tax (e.g. $140.45 forever).

Worked examples, all tested live: 1-week buyer pays **$99**; 4-week buyer pays **$74**; someone who
bought both ($85) pays **$44**.

## What exists

- **fn63 `compute_upgrade_offer`(user_id)** — the ONLY place the rule lives. Credit = membership
  money paid in the last **120 days**, capped at `forever_price - 15` ($114). Eligible while the
  pass is live, with a **14-day floor from the last payment**. Roadmap rows excluded. Aggregation
  is an `api.lambda`, not chained filters, because it needs a sum + max + date bound over one pass.
- **ep284 `GET /upgrade_offer`** (group 4, Weweb) — read-only wrapper, creates nothing.
- **ep285 `POST /upgrade_session`** (group 7, stripe_checkout) — recomputes server-side, creates
  the Stripe Checkout Session, returns `checkout_url`.

**Both endpoints call fn63 so the price shown and the price charged cannot drift.** That was the
whole reason for the function; do not inline the rule into either endpoint.

## Two deliberate departures from the spec's Appendix A

1. **No coupon or promotion-code objects, and no `upgrade_credits` table.** Because the Checkout
   Session is built server-side we just charge the discounted `unit_amount`. That deletes the
   entire failure class the spec worried about: nothing shareable to leak to a wedding forum, no
   `max_redemptions` to police, no `expires_at` to sync, and no dependency on "enable promotion
   codes" being ticked on a Payment Link. The table's stated purpose was stopping five clicks
   minting five live coupons — with no coupons, that reason is gone.
2. **No ep30 change was needed.** Setting `metadata[weeks]="forever"` routes the payment through
   ep30's **existing** membership branch, which already extends access, sets
   `forever_access_purchased`, writes the payment-log row and fires `membership_purchased`.
   ep30 routes on `metadata.type` FIRST then `weeks`, so `metadata[type]="membership_upgrade"`
   falls through correctly. The log records the DISCOUNTED amount, which is what they paid.

## Stripe pattern (reusable)

Key is `$env.stripe_api_secret`; header is `"Authorization: [redacted] " ~ $key` (NOT the Basic+base64
form in ep26, which omits the required trailing colon). Copy **ep212 `roadmap_session`**.
**`line_items[0][price_data][product_data][name]` works** — no catalog product id needed, which
matters here because the amount differs per user.

**The Stripe leg is PROVEN as of 2026-09-07** — see "What IS proven" below. (Historical note: the
earlier "smoke-tested" claim did not go through ep285; ep285's own history showed zero successful
calls. The block was re-proven properly on 2026-09-07 via a temporary probe function.)

## WeWeb — CONFIRMED LIVE 2026-09-07

**The banner shipped with the 2026-09-07 publish and is serving production traffic.** ep284
request history shows ~12 calls/2h from `www.tulletogether.app` (many distinct consumer IPs,
iPhone/Mac), all 200. No further build needed to make it exist.

**But every production caller so far returned `eligible: false`** — 12 of 13 had
`payment_count: 0`, one had `already_forever: true`. That is expected, not a bug: `/pricing` is
overwhelmingly visited by non-payers. Do NOT read it as the offer being broken.

**Audience sizing (from payment log table 16, measured 2026-09-07):** in the 79 days to
2026-09-07 there were 359 distinct payers, 249 of whom bought a timed pass and never bought
forever. Modelling pass length + the 14-day floor, **~54 people are eligible at any given moment
and ~23 new timed buyers enter the pool each week** (~90/month). At $74–$99 each this is worth
real money — the upgrade path is not a rounding error.

**Neither `clicked_upgrade_from_nav_badge` nor `clicked_upgrade_with_credit` has EVER fired**
(Mixpanel 3963130; `membership_purchased` logged 15 in the same 3 days, so tracking works). The
cause is upstream and is now fixed but unpublished — see [[project_tier_access_badge]].

`upgradeOffer` var `603f27b8-…`; page workflow `Fetch Upgrade Offer` `b4d90c17-…` (Membership page
`bc8cb393-…`, onload); banner `cdf4e8f7-…` inserted into `Pricing Section` `98c3e14f-…`; CTA
`5be3cb57-…` runs `Start Lifetime Upgrade Checkout` `9a3e1c76-…` which POSTs to ep285 and
redirects. Fires `clicked_upgrade_with_credit`.

## What IS proven (2026-09-07, against real production data)

`compute_upgrade_offer` run directly via `runWorkspaceFunction` for two real recent buyers:
- user **28697** (1 week, $30) -> `eligible:true, credit:30, final_price:99, final_price_cents:9900`
- user **28460** (4 weeks, $55) -> `eligible:true, credit:55, final_price:74, final_price_cents:7400`

So the whole chain up to the Stripe call is correct for real customers: eligibility, the credit,
the dollar price, and the cents figure ep285 charges. `final_price_cents` **is** in fn63's
response, so ep285's `>= 1500` precondition is safe (an earlier worry that it might be missing
is ruled out).

### Stripe leg — PROVEN 2026-09-07

Ran ep285's `api.request` block verbatim (via a throwaway function `tmp_stripe_upgrade_probe`,
run through `runWorkspaceFunction` so nothing was ever exposed publicly, then **deleted** —
verified gone). Both live tiers:

| case | amount_total | session | metadata |
|---|---|---|---|
| 1 week -> $99 | `9900` | `cs_live_a1De3bPp…` status **open** | type=membership_upgrade, weeks=forever, upgrade_credit=30, list_price=129 |
| 4 weeks -> $74 | `7400` | `cs_live_a1EdnK9g…` status **open** | type=membership_upgrade, weeks=forever, upgrade_credit=55, list_price=129 |

`GET` on the returned checkout_url -> **HTTP 200, `<title>Stripe Checkout</title>`, 564KB.** The
page genuinely serves. (Price strings are NOT in the raw HTML — Checkout renders client-side, so
`amount_total` + `status:open` from the API is the authoritative check, not grepping the page.)

`metadata[weeks]="forever"` + `metadata[type]="membership_upgrade"` are confirmed present on the
real session, which is what routes ep30 down its existing membership branch.

**`customer_email` risk is CLEARED.** The leading-whitespace emails are a table-16 (Make-era)
artifact only; table 1 emails are clean (checked real eligible users 28697 `smclain22@yahoo.com`,
28460 `dlainag@gmail.com`). fn63's lambda already `.trim()`s `Client_Reference_ID` for the same
reason.

**Still unproven: only the final webhook** — no one has actually paid, so ep30 granting Forever
off a `membership_upgrade` session has not been observed. That is the last untested link.

### Probe-writing gotcha

The first probe threw `Unable to locate var: …result.error.message` — a **direct path to a key
that does not exist is FATAL in a response block**. ep285 itself is safe because it uses
`($stripe_session.response.result|get:"error") == null`. **Always read optional Stripe response
keys through `|get:` , never a dotted path.**

**The CTA's failure path is a silent bounce:** on any refusal it sends the user to
`/pricing?upgrade=unavailable` (or `?upgrade=error`), and **nothing on the pricing page reads that
query param** — so the member lands back on the grid with no explanation. That is exactly what
"the link doesn't work" looks like. Testing from an account that never bought a timed pass will
always produce it, correctly.

Related: [[project_tier_access_badge]], [[reference_stripe_payment_webhook]],
[[project_budget_roadmap_relaunch]], [[reference_access_entitlement_model]].

## ep30 WEBHOOK LEG — VERIFIED BY CODE INSPECTION 2026-09-07 (no test purchase needed)

Read ep30's XanoScript and traced an upgrade session through it. **It routes correctly and does
the right thing.** Do not re-derive this.

Branch order is `if type=="budget_roadmap"` -> `elseif weeks in (...,"forever",...)` ->
`elseif type=="concierge"`. ep285 sends `type="membership_upgrade"` + `weeks="forever"`; the first
test fails (only matches `budget_roadmap`), so it falls into the **weeks** branch. `$weeks` is
`|to_lower|trim`'d, so `"forever"` matches literally.

With `weeks=="forever"` it sets `forever_access_purchased=true`, `date_until_access=32503680000000`
(12/31/2999), `total_amount_paid += amount_total/100`, logs a payment row with `Type:"forever"`,
publishes the `user_updates` realtime event, and fires Mixpanel `membership_purchased`
(`plan:"forever"`). Requires `client_reference_id>0` — ep285 sets it from `$auth.id`; if it were
missing ep30 sends a 🚨 Slack alarm rather than failing silently.

**Anti-double-dip confirmed:** after the upgrade `forever_access_purchased=true`, so fn63 returns
`eligible:false`. The credit cannot be claimed twice.

### Do NOT fire a synthetic webhook at ep30 to "test" this
ep30 is `auth:false` so it is trivially POST-able, but doing so writes a fake payment-log row,
fires a "💰💰😱 A DATABASE payment was made" alert into the team Slack, and sends a real
`membership_purchased` revenue event to Mixpanel — **which cannot be deleted** and corrupts the
cohort/funnel work in [[project_cohort_analytics]] and [[project_mixpanel_tracking_gaps]].
It also would NOT test the only remaining unknown (below), so it is pure downside.

### The ONLY remaining unknown
Whether Stripe delivers `checkout.session.completed` to ep30 for sessions created via the **API**
(ep285) as opposed to a **Payment Link**. It should — endpoints receive all subscribed event types
regardless of how the session was created — but it has never happened. Closing it requires a real
purchase; watching the first organic upgrade is sufficient given ep30's unhandled-event alarm.
Confirm via a payment-log row `Type:"forever"` at $74/$99 plus `forever_access_purchased` flipping.

## 2026-09-07 LIVE TEST on the owner's own account (user 14998, vivek.marthi@gmail.com)

Bought a 1-week pass at 20:13:22 UTC ($32.66 with NY tax). fn63 then returns
**eligible:true, credit 32.66, final_price 96.34, final_price_cents 9634** — confirmed both by
`runWorkspaceFunction` and by two real production ep284 calls at 20:13:27 and 20:13:48.
**So the Xano half is correct for a real paying customer.**

**The banner still did not appear, for two DIFFERENT reasons:**

1. **The filter-card / popup upgrade work was never published** — it was all built after the last
   publish. The production screenshot still showed "Included in 'Forever' Plan" and
   "You're making progress!".
2. **On the device used for the screenshot (Mac Chrome, IP 24.96.13.159) ep284 returned 401**
   at 20:24:49. A 401 makes `p2_store_offer` fall back to the hidden default, so the banner is
   hidden from someone who genuinely paid. **Same pre-auth race** — the page-level
   `Fetch Upgrade Offer` `b4d90c17` had NOT been given the token-wait guard; only
   `Fetch Access Tier` and the new app-level fetch had it.

**Fixed 2026-09-07 (draft):** added `p0_wait_for_token` as the first action of `b4d90c17`.
That workflow contains no plugin actions, so `editFrontendWorkflow` accepts it.

**Lesson: every workflow that reads `accessToken` on a load trigger needs the wait.** Grep for
`accessToken` across workflows before assuming a surface is safe — page onload is NOT late enough.

## SALES TAX — fixed 2026-09-07 (Xano LIVE, WeWeb copy needs a publish)

**Tax is NOT a flat ~8%.** From real webhook payloads: NY 8.875% (subtotal 12900 -> total 14045),
but **MD, NJ, FL and GB all came back tax 0**. Stripe computes it from the buyer's address at
checkout, so **there is no single tax-inclusive number the app can print.**

**The gap:** every Payment Link on the account has `automatic_tax` enabled, but the two
API-created Checkout Sessions did not — so an upgrade or a roadmap was the one purchase on which
no sales tax was collected. Verified in both scripts and by probe (`unit_amount 9900` ->
`amount_total 9900`, no tax).

**Fixed, LIVE in Xano now:** `automatic_tax[enabled]=true` +
`line_items[0][price_data][tax_behavior]=exclusive` added to **ep285 upgrade_session** AND
**ep212 roadmap_session**. Both smoke-tested through a throwaway probe function (deleted, verified
gone): Stripe returns 200 with
`automatic_tax: {enabled:true, status:"requires_location_inputs"}` — tax resolves once the buyer
enters an address, exactly like the Payment Links. The catalog-product shape in ep212
(`[stripe-id]`) was tested separately because automatic tax needs a tax code on the
product; it works.

**ep26 `sessions` (POST) also creates sessions and was NOT touched** — it is `auth:false` and takes
`line_items` straight from the client (so the caller sets the price). Per its own comment it never
worked. Leave it, but it is a live unauthenticated endpoint worth deleting.

### The credit stays TAX-INCLUSIVE, deliberately
The payment log stores only the tax-inclusive total, so the pre-tax portion of a past payment
**cannot be reconstructed**. A NY buyer therefore gets their old tax back as discount (~$2.66 on a
1-week pass); everyone else is exact. In the customer's favour. **Do not "fix" this by guessing a
pre-tax figure.**

### Copy rule adopted
Displayed prices exclude tax everywhere (the $30/$55/$129 cards already work this way).
**A surface that goes STRAIGHT to Stripe says "+ tax"; a surface that routes to /pricing does
not**, because /pricing carries the disclosure. So: banner fine print `bce7479f` gained
"Sales tax added at checkout where applicable."; the three filter-card subcopies say
"add filters for $X + tax"; the PDF popup's "View Plans" card was left alone since it routes to
/pricing. No "+ tax" on any CTA button — most buyers owe none and it would be noise.

**The popup's Coupon Banner (`c4b01f69`, ENGAGED25) was deleted from the component by the user
around 2026-09-07** — it no longer exists, so the two coupon-text bindings written earlier are
moot. The title `9b667e98` and the "Upgrade to Forever — $X" badge `5ef7a2b2` survive.

## ✅ PROVEN END TO END 2026-09-08 01:09 UTC — a real discounted upgrade completed

User 14998 (the owner's own account) upgraded for real. The ep30 webhook payload settles every
remaining question:

- **`payment_link: null`** -> this was an **API-created session (ep285)**, NOT a Payment Link.
  **That was the last unknown: Stripe DOES deliver `checkout.session.completed` for
  API-created sessions.**
- `metadata: {type:"membership_upgrade", weeks:"forever", user_id:"14998", list_price:"129",
  upgrade_credit:"32.66"}` and `client_reference_id: "14998"` — all intact.
- **Tax worked:** `amount_subtotal 9634` + `amount_tax 855` = **`amount_total 10489`**
  ($96.34 + 8.875% NY = $104.89), `automatic_tax: {status:"complete", enabled:true}`.
- ep30 returned **`handled: true`**, and the app shows
  **"Access granted until December 31, 2999!"** — the forever stamp.
- fn63 afterwards: **`already_forever: true, eligible: false`** — the credit cannot be reused.

**The whole chain is now proven: eligibility -> credit -> price -> Stripe session -> checkout ->
tax -> webhook -> forever access.** Nothing about the upgrade path is unverified any more.

### Remaining nit (cosmetic, editor-only)
Right after upgrading, the /pricing banner was still VISIBLE showing the fallback numbers
("You've already paid $0 ... you pay $129"). The DATA was correct (`eligible:false`); the banner's
`conditionalRendering` simply was not being applied — that happens in the WeWeb **editor** when
"Force display in Editor" is On or when viewing in design rather than Preview mode. In a published
build `eligible !== true` hides it. Confirm in Preview, not design mode.

### Store step now preserves a truthful not-eligible answer
Both `Fetch Upgrade Offer` (page) and `Fetch Upgrade Offer (App)` used to overwrite ANY
not-eligible response with a `hidden` constant that said `already_forever: false` — losing the real
flag. They now only fall back on an UNUSABLE response (401 / wrong shape) and otherwise keep the
real body, so `already_forever` and `payment_count` stay true for any surface reading them.


## fn63 v2 — CREDIT ONLY THE CURRENT ACCESS PERIOD (shipped + live 2026-09-11)

**The v1 rule (sum every non-roadmap payment in a rolling 120 days) was wrong and is gone.**
`/tulle view-as` caught it: Isabelle (13023) had five passes bought over five months, $190
lifetime, and was being offered **Forever for $15** — the 120-day sum hit the `forever_price - 15`
cap. **That caps a repeat buyer's LTV at the Forever price.** Des: *"if their month expired the $$
amount of forever should reset… not capping is ideal."* The user also flagged that PDF-submission
access is free, so those people must never see the banner.

**v2 rule:** credit = money paid for the access period they are inside RIGHT NOW. Payments are
walked oldest→newest in the lambda and chained into periods **exactly the way ep30 builds them**
(each payment extends from the current expiry, or from its own timestamp if access had lapsed).
**A gap RESETS the credit.** Stacked top-ups inside one live period all count.

**STRICT liveness — v1's 14-day grace floor is deleted.** Decided by the user; the offer
disappears when the paid coverage runs out.

### The duration map is ep30's, not the marketing names
`1 week`/`1 weeks` **8 days** (691200s), `2 weeks` 15d, `4 weeks` 29d, `6 weeks` 43d,
`12 weeks` 85d, `24 weeks` 169d. Copy them from ep30's `add_secs_to_timestamp` calls or the
reconstruction builds access periods that never existed. **Proven exact:** fn63's computed
`coverage_ends_at` for 13023 lands on **2026-09-19**, the same day as her stored
`date_until_access`.

Four AND-ed gates: `already_forever == false`, `credit_raw > 0`, `coverage_ends_at > now`
(this is what excludes PDF-granted and hand-granted access), `access_live` (`date_until_access`
parsed at END of day, per ep299, so the DATE column's truncation can't shave a day off a payer).
A **$0 comped row of a known tier still chains** — it adds no credit but it did grant access, and
dropping it would look like a lapse. Forever rows, the two stray `database` rows and unknown
tiers buy no timed coverage, so they neither start nor extend a period.

### Verified against real production users 2026-09-11 (runWorkspaceFunction)
| user | shape | v1 | v2 |
|---|---|---|---|
| 13023 Isabelle | 5 passes, months apart, $30 today | credit 114 → **$15** | credit **30 → $99** |
| 29045 Edward | one 4wk $55, live | 55 → $74 | 55 → **$74** (unchanged) |
| 27271 / 28151 | 1wk $30 **then** 4wk $55 seconds later | — | credit **85 → $44** (stacking) |
| 25886 Alyssa Korch | two 1wk passes **9 days apart**, both expired | — | **eligible:false, $129** (reset) |
| 25168 Kayla | 4wk $55 then upgraded | — | `already_forever:true` (no double-dip) |

The PDF-submission case is structural, not separately testable: those users have **no payment-log
row at all**, so `credit_raw` is 0. A PDF extension can only raise `access_live`, which is an AND
gate, so it can never flip `eligible` true — 25886 is the live proof of the lapsed half.

### Response keys were kept stable on purpose
`paid_in_window` now carries the **current run total**; `window_ends_at` now carries
`coverage_ends_at`. Added `credit_payment_count` and `coverage_started_at`. **ep284 and ep285
needed NO edit** — ep284 whitelists fields (so the two new keys are not exposed to WeWeb, and
nothing needs them) and ep285 reads only `already_forever` / `eligible` / `final_price_cents` /
`credit` / `forever_price`. ep299 maps fn63 straight through, so `/tulle view-as` self-corrects:
it now prints **"$30 credit, Forever for $99"** for Isabelle (confirmed by calling ep299).

The `$max_credit` cap ($114) is now unreachable — max credit is one live period, $55 today. Left
in as a guard against a future long tier.

v1 backup: `.claude/xano_backups/fn63_v1_120day_sum_2026-09-11.xs`.

### Stale above, corrected here
**User 14998 (the owner) now has ZERO payment-log rows and `already_forever:false`** — the test
rows from the 2026-09-07/08 end-to-end proof were cleaned up. Do not use 14998 as a forever
fixture; use **25168**. The end-to-end proof itself still stands.
