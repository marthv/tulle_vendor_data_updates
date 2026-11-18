---
name: project_pricing_buy_undefined_user
description: "/pricing plan buttons build the Stripe URL from an unhydrated user object — 'undefined (undefined)' in Slack means a real customer could not buy. Meredith 27221 lost 2026-09-07."
metadata:
  type: project
---

**Traced 2026-09-07 from the Slack message `undefined (undefined) clicked to buy …`.**
Pre-existing, NOT caused by that day's publish (same message on 2026-08-30 and 2026-09-03).

## Who it hit

**Meredith, user 27221, [email].** iPhone, T-Mobile IP `172.56.166.2`.
ep216 401'd at 18:17:06 UTC then 200'd at 18:17:10 (the pre-auth race). She clicked buy
**8 times in 18 seconds** (forever x2, then 4 weeks x6) starting 18:17:51, and
`last_active_at` shows her still browsing 2m23s later. `total_payments: 0`,
`date_until_access: null` — **she never bought. A lost sale, observed end to end.**

## Where it comes from — TWO workflows on ONE click

Plan buttons live on the Membership page `bc8cb393`, in two sets (desktop + mobile), e.g.
"+ 4 Weeks" `d58dbe22` and `46f6a9e6`. Each button carries **two** click workflows that both fire:

1. **`Select 6 Weeks Plan v2` `9e3d6975`** — does the real work: fetch collection `158d5aae`
   (Stripe links) -> `change-page` external to
   `Stripe_Link + user.email + "&client_reference_id=" + user.id`.
   Its own "Post to Slack" step is **`disabled: true`**.
2. **`Select 6 Weeks Plan v2_copy` `de127fc8`** — Slack only, `disabled: false`. **This is what
   posts the message**, from
   `user?.name + " (" + user?.email + ") clicked to buy …"`.

So `undefined (undefined)` is the *symptom*: the same unhydrated `user` object that names the
Slack line is also interpolated into the Stripe URL, which becomes
`…undefined&client_reference_id=undefined`.

**Both outcomes are bad:** Stripe rejects/errors on the malformed link (customer cannot buy — what
happened to Meredith), or it accepts and ep30 sees `client_reference_id` -> `to_int` -> 0, fails
its `$user_id > 0` guard and fires the 🚨 "ACCESS PAYMENT WITH NO USER ID" alarm — money taken,
no access.

**The fix shape is the token/user wait already shipped in `a0_wait_for_token`**
([[project_tier_access_badge]]) — gate the click on the user object existing, or disable the
button until it is. Same family as [[project_post_purchase_access_fix]] and
[[project_preauth_401_fix]].

## Two incidental findings

- The Slack webhook is **hardcoded in frontend workflow code**:
  `[webhook-url]` — the exact webhook
  [[project_preauth_401_fix]] says still needs rotating. It is shipped to every browser.
- It posts through a **different Xano instance**: `x8ki-letl-twmt.n7.xano.io/api:aow91bcd`
  (`/pricing_upload`), not the usual `xqtb-2ma7-ijfy`.
- Element/event names are stale: the "+ 4 Weeks" button is named "Get Started 6 Weeks Button" and
  fires Mixpanel `clicked_to_pay_for_6_weeks` — explains the dead-event note in
  [[project_mixpanel_tracking_gaps]].

## FIX SHIPPED TO DRAFT 2026-09-07 — needs a publish

**The workflow-level guard I intended was NOT possible.** `editFrontendWorkflow` refuses every one
of these workflows with `[WRONG_ACTION_TYPES] Action "cd33cf33-…-request" not found` — they all
contain the Xano plugin "Post to Slack" action. This is the documented plugin-action limit in
[[reference_weweb_mcp_limits]], now confirmed with the exact error string.

**What shipped instead — a property binding, on all 8 buy buttons:**

    content.default.disabled  ->  !(user && user.id && user.email)   (defaultValue false)
    style.default.opacity     ->  ready ? 1 : 0.55                   (defaultValue 1)

The 8: `59da3a90` `46f6a9e6` `3d7c818f` `2da86142` `7a2da4dd` `0bb1ae9c` `d58dbe22` `75a9309e`
(two sets, desktop + mobile; "+1 Week", "+4 Weeks", "Forever.", "+3 Months", "+6 Months").

A disabled ww-button fires **no** click workflows, so during the hydration gap there is now no
broken Stripe redirect **and no Slack post** — meaning `undefined (undefined)` messages should
largely stop. **If they stop, that is the fix working, not the logging breaking.**

Both bindings fail OPEN (`defaultValue` = enabled / opaque), so a binding error can never block a
purchase.

### What this does NOT do
It does not route a signed-out or never-hydrating visitor to `/sign-in` — the button just stays
dim and inert. Better than a silent dead click, but the sign-in routing still wants doing **by
hand in the editor** (add a wait + branch before the `change-page`, as in
`a0_wait_for_token`).

### Unrelated bug seen while in here — NOT fixed
**"+ 6 Months" `7a2da4dd` and "+ 3 Months" `2da86142` both point at collection index `[2]`** — the
same Stripe link. That is the "/pricing 6-Months charges $65 for 3 months" bug already recorded in
[[project_budget_roadmap_relaunch]]. Deliberately left alone: it is a pricing decision.

## HOLDING — verified 2026-09-14

Slack has **zero** `undefined (undefined) clicked to buy` messages since the 2026-09-07
publish. Prior occurrences: 2026-08-30, 2026-09-03, and Meredith's 8-click burst on 09-07
at 14:17-14:18 EDT. The `disabled` + `opacity` guards are present on all 8 buy buttons in
the draft, with the explanatory comments intact.

**Honest limit on that evidence:** a disabled button fires no workflows, so the silence
proves no malformed Stripe URLs are being built — it does NOT prove those users went on to
buy. The signed-out -> `/sign-in` routing is still missing; such visitors get a dim, inert
button. Still worth doing by hand.

**The 3-month / 6-month `data[2]` bug is now MOOT.** The user deleted the whole detailed
comparison grid `6334e65d` on 2026-09-14 (118 elements), which is where those two tiers
lived. It was never worth money anyway: `clicked_to_pay_for_3_months` and
`clicked_to_pay_for_6_months` have **no events at all in 90 days**, and Slack has never
logged a single 3- or 6-month purchase. Real 90d click volume is 1 Week 233 / Forever 146 /
4 Weeks 120.

Collection index -> price map, read live 2026-09-14 (sort `id ASC`, Xano table 17):
`[0]`=$30, `[1]`=$50-row (charges $55), `[2]`=$65, `[3]`=$160, `[4]`=Forever.
Snapshot: `.claude/weweb_backups/pricing_page_plan_buttons_pre_fix_2026-09-14.md`.
