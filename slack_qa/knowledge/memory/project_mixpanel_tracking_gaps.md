---
name: project_mixpanel_tracking_gaps
description: "2026-08-15 Mixpanel audit — traffic is flat, not down; three instrumentation gaps make the dashboard under-report monetization"
metadata: 
  node_type: memory
  type: project
  originSessionId: eb784596-f4ad-4135-92e2-358536c92040
  modified: 2026-08-15T19:23:22.293Z
---

Investigated a worry that activity was down on 2026-08-15. **It was not.** Recording this so
the same false alarm isn't re-investigated, and so the real gaps found aren't lost.

**Project is `Tulle - WeWeb` = 3963130** (there is also an older `Tulle` 3355708 and an
unrelated `E-Commerce`). `Get-Business-Context` fails — the MCP connection lacks the
`business_context` scope.

**Traffic is FLAT, not declining.** `viewed_page` uniques by week: Jul 25–31 **1,111**,
Aug 1–7 **1,082**, Aug 8–14 **1,134**. Signups 648 / 596 / 628.

**Saturdays are the structural weekly low** — the three lowest days in a 21-day window were
all Saturdays (Jul 25 = 94, Aug 8 = 114, Aug 1 = 125). **Always check the day of week before
calling a dip a problem**, and always check whether the day is partial: Mixpanel data ran
~10 hours behind the clock when queried.

**Acquisition mix is stable** — ~95% of `signed_up` arrives via `$initial_referring_domain =
tulletogether.com` every single day. If that ratio holds, no channel has broken.

## The three real gaps (all still OPEN as of 2026-08-15)

1. **`membership_purchased` / `roadmap_purchased` fire ZERO before 2026-08-09**, then start.
   Stripe shows revenue throughout that period, so this is an instrumentation start date, not
   a business change. **Any trend chart spanning it is misleading — including the Railway
   Cohorts tab**, which reads these same events ([[project_cohort_analytics]]).
2. **`clicked_to_pay_for_6_weeks` has been flat zero since 2026-08-11** after regularly
   posting 7–10/day. Either the plan was pulled from the pricing UI or its tracking broke —
   check against the recent `/pricing` work ([[project_budget_roadmap_relaunch]]) before
   assuming a bug.
3. **Purchases land with no tracked pay-click.** On 2026-08-15 Stripe recorded a real $129
   charge while Mixpanel logged 0 across all three `clicked_to_pay_*` events and 0
   `membership_purchased`. `pdf_limit_popup_triggered` — the top of the monetization funnel —
   read **1** that day vs 15–54 on prior days, a steeper fall than traffic explains.

**Stripe is the ground truth for revenue and it is easy to check:** account
`[stripe-id]`, livemode, `GetCharges` with `created.gte`. Daily succeeded/non-refunded
totals for Aug 8–14 2026 ran $92–$554 against a ~$183/day baseline — **day-to-day variance is
large, so a single low day means nothing.** The response exceeds the tool's token cap; write it
to a file and aggregate with python (no `jq` on this machine).

**Xano public API base is `https://xqtb-2ma7-ijfy.n7e.xano.io`; the WeWeb Transparency Project
group (id 10) canonical is `GynP5T1B`** — e.g.
`GET /api:GynP5T1B/venue/pricing_intelligence?vendor_id=V3794` returns the live PI payload
unauthenticated.

Related: [[project_pi_panel_infographic]], [[project_cohort_analytics]].

## 2026-08-19/20 — the same false alarm recurred. Read this BEFORE investigating a dip again.

**Project timezone is US EASTERN, and hourly buckets run ~3–4h behind the clock.** So a
same-day total is ALWAYS partial, and the missing hours are the evening peak: hours 20:00–23:00
carry **14–34%** of a day's `viewed_vendor_page`. Reading a day-total at 7pm and comparing it to
yesterday's full day manufactures a ~30% "drop" out of nothing.

**The only valid method is same-clock-window comparison.** Pull `unit: "hour"`, sum hours 0..N
for today, and sum the SAME hours on prior days. Done that way, Aug 19 was flat:
page views 366 (Mon) / 334 (Tue) / **323 (Wed)**; PDF views 212 / 226 / **216**. Signups 69 vs a
64–119 baseline. Nothing was wrong.

**Gap #2 above is now EXPLAINED**: `clicked_to_pay_for_6_weeks` went dead because the plan became
**4-week $55 with no replacement event**. So `Clicked to Pay` has under-counted since ~Aug 11 and
its apparent Aug-12 cliff is largely an artifact. Still open — the new plan needs an event.

**Do NOT blame the PI paywall for the PI usage collapse.** `open_pricing_intelligence_breakdown`
ran 102/117/103/68/89 through Aug 13, then fell to **11 on Aug 14 and 0 on Aug 15** — three days
BEFORE PI was gated to $50+ on Aug 17. The gating held it at 8–9; it did not cause the fall.

**The action events are where real problems show, not pageviews.** On Aug 19
`submitted_vendor_pricing` was **0 across all 23 hours** (only zero-day in 21 days; prior min 2)
while upload clicks still fired — the kind of decoupling that indicates a broken funnel rather
than soft demand. Compare an action event against the traffic that should drive it: PDF views
flat + `pdf_limit_popup_triggered` down 70% is a real signal; both down together is just a quiet day.

## 2026-09-06 — gap #1 invalidates the "purchase event is broken" reading. Check before re-raising.

The Tier-Aware Access Badge spec called `membership_purchased` a **hard blocker**: "fired for 143
unique users over 180 days against 860 `clicked_to_pay_*` events. Those can't both be right."

**They can, and both are.** Verified by weekly series over 120 days: `membership_purchased` is
**exactly zero every week through 2026-07-27**, then 4 / 38 / 32 / 32 / 45. Its 180-day total
(151) and its 30-day total (151) are **identical** — every event it has ever recorded falls in
the last four weeks. `Clicked to Pay` meanwhile ran continuously at 13–75/week across the whole
120 days. So the comparison was **4 weeks of purchases against 26 weeks of clicks**.

At steady state the instrumentation looks healthy: last 30d = 151 `membership_purchased` against
418 click events across all four `clicked_to_pay*` variants (~36%). **Do not rebuild the purchase
event on the strength of that ratio.**

Two real things did surface:
- **There is no `clicked_to_pay_for_2_weeks` event at all**, despite 2-week being a live tier.
  Only `_1_week`, `_6_weeks`, `_lifetime_access` and a generic `Clicked to Pay` (892/180d) exist.
- **`membership_purchase_confirmed` is a SECOND purchase event** (84 uniques / 99 events, same
  Aug-9 start) alongside `membership_purchased` (143/151). Settle which is canonical before
  hanging `current_tier` on either.

**Consequence for any upgrade-rate baseline:** a "9.2% over 180 days" figure cannot have come
from Mixpanel — there is no purchase data before Aug 9. It came from the Xano payment log
(table 16), so the post-ship read must come from there too, not from Mixpanel.

**The 6-week cohort no longer exists.** Per gap #2 above the plan became 4-week $55 around
Aug 11, so a success measure that expects lift to concentrate in 6-week buyers is measuring a
tier nobody can buy any more. Segment on the Xano payment log `Type`, not on the old tier names.


## 2026-11-08 Payment Funnel "Clicked to Pay" gap
- "Clicked to Pay" fired only on 1wk/6wk buttons; Forever sent clicked_to_pay_for_lifetime_access only, so the funnel showed ~0 at the pay step.
- FIX in EDITOR (live on publish): Membership page bc8cb393, Forever button 75a9309e wf 8605fcc5 action 5fec1bf3 + payer upgrade button 2ceb156f wf 6b1e9f40 now ALSO track "Clicked to Pay" {plan:"lifetime", source}. Backup: .claude/xano_backups/weweb_lifetime_pay_tracking_pre_clicked_to_pay_2026-11-08.json
- Funnel counts before 11-08 publish undercount Forever; compare only after publish date.
- 11-08 (owner yes): Forever button 75a9309e had a 2nd click wf 7156fb28 "Select Forever Plan_copy" that ALSO tracked clicked_to_pay_for_lifetime_access -> every Forever click logged TWICE (the "77 in 30d" was ~2x). Removed its Mixpanel step; kept its Slack ping (the ONLY live "clicked to buy forever" ping; main wf ping disabled). Backup .claude/xano_backups/weweb_forever_copy_wf_pre_dedupe_2026-11-08.json. Pre-publish counts of that event are doubled.
