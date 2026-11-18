---
name: project_advanced_filters_paywall
description: "Advanced-filters paywall card is the app's HIGHEST-volume paywall CTA (647 clicks/30d) — and a pointer-events:none inheritance likely made its button unclickable"
metadata:
  node_type: memory
  type: project
---

Vendor Discovery `0d69d71b-834d-4f2f-a13a-6d6ac66905d5`, filter rail.

**This is the highest-volume paywall CTA in the product.**
`clicked_membership_for_pricing_advanced_filters` = **647 events / 30 days**, ahead of
`clicked_membership_for_pricing_pdf_limit_popup` (**515**) — the PDF wall that supposedly drives
~40% of purchases ([[project_pdf_warning_view3]]). Treat any edit here as revenue-affecting.

**Real uids** (the page DESCRIPTION's uids are orphaned — see [[reference_weweb_mcp_limits]]):

    Venue Advanced Filters - Blur  31959483-4159-44da-8c9a-fe043113d325
    └─ Paywall Card                576513ba-6be6-41da-822d-a5eb4a47e5a8   (FIRST child)
       ├─ Container                65683015-4895-49f5-98d3-10f1c5c67476
       │  └─ Heading               11fd6343-5330-4370-b09e-7e2060e87206
       ├─ Subcopy                  337880f6-3c27-400e-aaf6-9af81905ccf9   (added 2026-08-20)
       └─ Button                   d42be733-5244-4c10-af7e-f07c71e057c9

## ⚠️ pointer-events inheritance — likely made the CTA dead
The parent sets `customCss: { pointerEvents: "none", backdropFilter: "blur(10px)" }` to make the
blurred filters inert. **`pointer-events: none` inherits**, and the Paywall Card sat inside it
with `customCss: {}` — so neither card nor button re-enabled hit-testing. Fixed 2026-08-20 by
setting `customCss.pointerEvents: "auto"` on `576513ba`.

**UNRESOLVED:** if the button was dead, the 647 clicks must come from somewhere else firing the
same event name (mobile variant / another surface). **Confirm the source before attributing that
volume to this card** — and before reading any lift from the 2026-08-20 redesign.

## Redesign shipped 2026-08-20 (published)
Copy went value-first: "Unlock Filters by purchasing 'Forever' access!" →
**"Filter by the details that matter"** + subcopy naming the actual filters (venue type, vibe,
capacity, price ceilings, outdoor ceremony) + button "Unlock advanced filters". Card went from
warm grey `#E8E6E0` with a `0.5px outset` bevel to white with a `#A8EAC8` border and `--sh2`.
Button workflow deliberately untouched so tracking keeps firing.

**No price is shown on purpose.** Forever is $129, but `/pricing` still renders a stale table grid
(1wk $30 / 4wk **$50** / forever **$119**) against its card grid ($30/$55/$129). Printing $129
here creates a visible contradiction one click later. **Fix `/pricing` first, then add the price.**
See [[project_budget_roadmap_relaunch]].

Full before/after: `tulletogether/.claude/weweb_backups/advanced_filters_paywall_card_2026-08-20.md`

## 2026-09-07 — THE GATE IS `forever_access_purchased == false`, SO IT FIRES AT PAYING CUSTOMERS

Verified on the live elements. Both the blur wrapper `31959483` and the Paywall Card `576513ba`
gate on exactly this and nothing else:

    pluginVariables['f5856798-…']['user']?.['forever_access_purchased'] == false

**There is no distinction between "never paid" and "paid but timed."** A member who paid $30
(1 week) or $55 (4 weeks) has the flag false, so they get the identical blurred filters and the
identical paywall card as a total stranger. This is the single most important fact about this
surface: **it fires precisely at the upgrade-eligible population** (~54 standing, ~23/week — see
[[project_upgrade_credit]]).

Card copy has been revised since the 2026-08-20 note: heading is now **"Filter what matters"**,
subcopy **"Included in 'Forever' Plan"**, button **"Unlock filters"**. There are FOUR sibling
cards, one per category (Venue `576513ba`, Photography `cc34e1fc`, Entertainment `07cd9479`, +1),
each with its own uids — **edit all of them or the behaviour goes inconsistent by category.**

Button workflow `Navigate to Payment` `626dab60` runs in this order:
`change-page -> bc8cb393 (/pricing)` **then** `close-all-popup` **then** if-not-editor ->
mixpanel `clicked_membership_for_pricing_advanced_filters`. **Tracking fires AFTER navigation is
initiated** — fragile, though the 647 events/30d show it does currently survive.

The popup shown over the rail is a **library component `ed90717a-1752-4f7b-8afa-841629cb68c8`**
(that is why it is absent from the page semantic — do not go looking for it in
`getPageSemantic`). Its copy as of 2026-09-07 is acquisition copy shown to people who already
paid: *"You're making progress! … Unlock the database to compare vendors…"*, offering **"Submit a
pricing PDF -> PDF Access for 1 Week"** to someone who already has access. It also uses a
**purple** card, which CLAUDE.md forbids outright ("NO purple anywhere").

So today a paid timed member who clicks filters gets: stranger-grade paywall -> full-price
`/pricing` grid ($129 anchor) -> and only then, if `Fetch Upgrade Offer` resolves, the gold credit
banner. The credit is two navigations away from the moment of intent.

## ONE-CLICK UPGRADE SHIPPED TO THE DRAFT 2026-09-07 (needs a publish)

There are **THREE** cards, not four: Venue `576513ba`, Photography `cc34e1fc`,
Entertainment `07cd9479`.

Built (all conditional on `upgradeOffer.eligible`, so **the free-user path is byte-identical**):
- New app workflow **`Fetch Upgrade Offer (App)` `c7f1a930-52b8-4e6d-9c14-3f0a7de81b45`**
  (onload-app) fills the same `upgradeOffer` var `603f27b8` app-wide, using the same
  `a0_wait_for_token` guard as `Fetch Access Tier`. The Membership page keeps its own page-level
  copy so /pricing re-reads on SPA navigation, when onload-app does not re-fire.
- Subcopy `337880f6` / `9f24d17f` / `6796bd59`: eligible -> "Your $30 already counts — add filters
  for $99."; otherwise the original "Included in 'Forever' Plan".
- Button labels `d42be733` / `1e6b78c7` / `aec397a1`: eligible -> "Upgrade to Forever — $99".
- Click workflow on **all three** buttons: new `w0_is_upgrade_eligible` if-branch ->
  `w1_upgrade_checkout` POSTs ep285 and redirects to Stripe. False branch is the untouched
  original chain.

**The upgrade path fires `clicked_upgrade_with_credit` with `source:'advanced_filters'`, NOT
`clicked_membership_for_pricing_advanced_filters`** — deliberately, so the 647/30d baseline on the
free-user metric stays comparable.

⚠️ These three buttons look like one shared workflow and are not — see
[[reference_weweb_mcp_limits]].

## PDF Limit popup `48be8a6c` — copy fixed for existing payers (needs a publish)

The screenshot modal ("You're making progress!") is component **`48be8a6c` "PDF Limit Reached"**,
root `27335d2f` "UPDATED Upload Pricing Popup" — NOT the Filter Popup `ed90717a`, and it is
invisible to `getPageSemantic` because it lives in a component.

Made offer-aware: title `9b667e98` -> "Make your access permanent"; coupon line `1ee8b110` ->
"Your $30 already counts toward Forever — you pay" with pill `47af3eeb` -> "$99" (it was telling
existing customers **"First-time buyers get 25% off with ENGAGED25"**); "View Plans" badge
`5ef7a2b2` -> "Upgrade to Forever — $99".

**Deliberately NOT touched, and still open:**
- Message body `f4f33cca` — `getComponentSemantic` truncates it, so the original copy could not be
  preserved in a binding's false branch, and this popup drives ~40% of purchases.
- The **"Submit a pricing PDF -> PDF Access for 1 Week"** option `5a3c8eef` still shows to people
  who already have access. Hiding it needs `conditionalRendering`, which cannot be read on a
  component element, so writing one risks clobbering an existing condition.
- The "View Plans" card still routes to /pricing rather than one-click Stripe (its workflow id is
  unreadable inside the component; adding a second click workflow would race the existing one).
- The purple "View Plans" card still violates CLAUDE.md's "NO purple anywhere".
