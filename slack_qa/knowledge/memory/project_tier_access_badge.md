---
name: project_tier_access_badge
description: "Tier-aware nav Access Badge (upgrade path phase 1) — ep216 returns tier, 4 states, gold only on timed. LIVE since 2026-09-07 but was dead for ~2/3 of users: pre-auth race on ep216. Fix made 2026-09-07, NEEDS A PUBLISH."
metadata:
  node_type: memory
  type: project
  originSessionId: 0c05ebfb-a977-4a0f-84cf-5b564a9affe4
  modified: 2026-09-28T16:00:37.494Z
---

Phase 1 of the upgrade path: give the UI a way to tell a 1-week buyer from a Lifetime buyer, so
an upgrade affordance has somewhere to render. **Badge only — no credit, no coupons.**
Built 2026-09-06. **Needs a WeWeb publish + a real logged-in check.**

## What shipped

**Xano ep216 `my_access_summary` (group 4)** now returns `tier`: `forever` | `timed` | `free` |
`locked`, and `days_remaining` is **null for lifetime**. Backup:
`.claude/xano_backups/ep216_v1_pre_tier_2026-09-06.xs`.
- `days_remaining` **already existed** — `tier` was the only genuinely new field.
- Columns are `FreeViewsRemaining` and `total_amount_paid`; there is **no `paid_at`** on the user
  table, so time-boxed cohort work still has to come from the payment log (table 16).
- **Comped users resolve to `timed`, not `locked`.** Access can be granted straight onto the user
  row with no payment (the feedback-triage flow does this); the spec's strict
  `total_amount_paid > 0` rule would badge someone with full access "Locked". `amount_paid` in the
  same response separates comped from paid.
- Nulling `days_remaining` is safe for `/purchase-complete`: its "Access until" formula returns
  `'Never expires'` on `forever_access` before it ever reads the field.

**WeWeb** (project `d830a133-815b-419b-8efe-a0f104b3ece2`):
- `accessState` (`97b18c0a-…`, string) **already existed and nothing populated it**.
  New `accessDaysRemaining` (`28ff491e-…`, number).
- App workflow **`Fetch Access Tier`** `7e2a91c4-…`, trigger `onload-app`.
- Badge tap workflow **`Access Badge Tap`** `5f81b3d0-…` — fires
  `clicked_upgrade_from_nav_badge` **only on `timed`** (tracking every state would inflate the
  number this is meant to measure), then routes `forever` → `/user-profile-page`, everything
  else → `/pricing`.
- Chevron `390925d0-…` added, rendered only when tier is `timed`.

## The badge is in a REUSED SECTION — edit it once

`Navigation Bar Section` **`de36599e-f04d-4778-8776-ca7621b16c13`** is reused across **12 pages**
(Profile, Recommendations, My Wedding, Membership, Vendor Discovery, Venue Vendor Page, …).
**Do not rebuild it per page** — the spec assumed per-page badges and that is wrong.
- Access Badge container `669f7222-2968-4650-9dd8-ec4c2da4eacb`
- Access Checkmark icon `db907f62-6d0e-4ed9-b144-7b7619840452`
- Access Text `8c040a48-1d01-4696-9f32-4cc7ce2af524`

## Two behaviour changes worth knowing

1. **The badge used to be invisible to unpaid users entirely** — its only conditional was
   `date_until_access > now`. `free` and `locked` states are new surface area.
2. **It was `display:none` on mobile.** It now shows, with the gold re-declared at the Mobile
   breakpoint (the Default→Tablet→Mobile cascade does not carry it).
3. Desktop `display` is still gated on the sidebar-expanded variable `2e03a506-…`, so with a
   collapsed sidebar the badge is hidden — **pre-existing**, deliberately not changed.

**Safety property: `unknown` falls back to the old date-based "Access until …" badge**, so a
failed fetch or a logged-out visitor is never worse than before. `hasPaid` (`73f04eb1-…`) and the
PI blur overlay `bed88937-…` were deliberately **left untouched**.

## THE BADGE WAS LIVE BUT DEAD FOR MOST USERS — pre-auth race (fixed 2026-09-07, needs publish)

Measured 2026-09-07 over 24h of ep216 production traffic: **101 of 152 calls from
`www.tulletogether.app` returned 401 — 66%. 32 of 54 distinct devices got ONLY 401s** and never
resolved a tier at all. Same IPs alternate 401 / 200 across page loads minutes apart, always in
exact duplicate pairs (the fetch double-fires).

**The trap that cost time: request history shows an `Authorization` header present on all 101
401s, which reads like "valid token, rejected".** It is not. The header formula returns the empty
string `''` when the token is not yet hydrated, and an empty header still logs as a header. Compare
`auth/me` (ep3) in the same window: 41 of its 401s carry NO header at all and only 9 carry one — a
different signature for the same underlying race. **A token cannot un-expire, so alternating
401/200 on one device is always a race, never expiry.**

Cause: `Fetch Access Tier` `7e2a91c4-…` runs on `onload-app`, which fires before WeWeb restores the
saved token from storage. Consequence chain: no tier → `accessState` stays `unknown` → badge falls
back to the old date-based render → **no chevron, no tap workflow → `clicked_upgrade_from_nav_badge`
has never once fired**, and the /pricing credit banner has no traffic route. This blocked the whole
phase-1 → phase-2 upgrade path while looking, from the outside, like nobody was interested.

**Fix applied 2026-09-07 (WeWeb draft, NOT yet published):** new first action `a0_wait_for_token`,
a `custom-js` step that polls `pluginVariables['f5856798-…']['accessToken']` every 100ms for up to
5s, then proceeds. Logged-out visitors simply time out and keep the safe `unknown` fallback. Same
"wait for the thing to appear rather than assume it is ready" shape as the A/B framework fix in
commit `d70d89c`.

**There is no auth-resolved app trigger in WeWeb** — the only app-level triggers are onload,
onload-app, page-unload, page-scroll, page-resize, keydown, keyup. Waiting inside the workflow is
the available fix, not a workaround chosen over a better one.

**Verify after publishing:** ep216's production 401 share should collapse from ~66%, and
`clicked_upgrade_from_nav_badge` should start appearing in Mixpanel 3963130.

## Still open

- **`current_tier` + `days_since_first_payment` on `membership_purchased` are NOT applied.**
  Ready to paste: `.claude/xano_backups/ep30_current_tier_PATCH_2026-09-06.xs`. Done by hand
  because `updateAPI` replaces the whole script and ep30 is the live Stripe webhook.
- Phase 2 credit-back window is specced in the source doc and explicitly out of scope.

Related: [[reference_weweb_mcp_limits]], [[project_mixpanel_tracking_gaps]],
[[reference_access_entitlement_model]], [[project_budget_roadmap_relaunch]].

## STALE BADGE AFTER A PURCHASE — fixed 2026-09-07 (draft, needs publish)

**Symptom:** upgraded to Lifetime, `accessState` stayed `"timed"`. The DATA was correct the whole
time — user 14998 had `forever_access_purchased: true` and `date_until_access: "3000-01-01"`, and
ep216 was observed returning `tier:"forever"` for another Lifetime user in the same minute.

**Cause, and it affects EVERY purchase, not just upgrades:** `Fetch Access Tier` runs only on
`onload-app`. Stripe redirects the buyer to `/purchase-complete`, which IS a full load — but
**ep30's webhook is async and typically lands ~15s later**, so ep216 is asked before the new
entitlement exists and returns the OLD tier. `accessState` also has *Preserve on navigation: Yes*,
so the stale value then survives every SPA navigation for the rest of the session. A fresh
Lifetime buyer kept the gold "timed" badge until they happened to hard-reload.

**Fix:** extended the EXISTING `/purchase-complete` poller `Confirm Purchase On Load`
`0e49d2de-…` (page `d9af3a12-…`) with two new steps, `SET_ACCESS_STATE` and `SET_ACCESS_DAYS`,
between `SET_PURCHASE_CONFIRM` and `TRACK_PURCHASE_CONFIRMED`. That workflow already polls ep216
every 1.5s for up to 20s and holds the fresh summary, so this adds **zero** extra network calls —
which matters because **ep216 scans the whole ~1,200-row payment log on every call**, so a second
poller (or moving the tier fetch to page-onload) would have been real load.

Both setters **return the existing variable value** when the poll produced no summary, so a
network failure can never blank a correct badge.

`upgradeOffer` needs no equivalent: `/pricing` has its own page-onload fetch, so it self-corrects
on navigation.

**The better fix, not done:** re-fetch on the `user_updates` realtime event, which ep30 already
publishes to immediately after writing the row (app workflow `XANO realtime updates` `64cdabb6`
listens on it). That would update live with no polling. Blocked from the MCP: that workflow
contains plugin actions, and `createFrontendWorkflow` cannot set `triggerConditions`, so a new
workflow cannot be scoped to the `user_updates` channel. Editor-only.


## THE DUPLICATE IS GONE — rebuilt 2026-09-11 (needs a publish to take effect)

`Fetch Access Tier` was registered **TWICE under one uid** (`7e2a91c4-…`) with two different
bodies. One started with `a0_wait_for_token`; the other started straight at `a1_get_summary` with
**no token wait at all**. The no-wait copy raced WeWeb's auth restore, 401'd, and wrote its
fallback over the good answer — so **paying customers intermittently saw "Locked" in the nav**.
Measured on live traffic 2026-09-11: **4 of the 6 most recent ep216 calls were 401**, from real
customer devices (iPhone, Firefox/Windows), not from testing.

Found while investigating "why does the badge say Locked when I'm mirroring a timed member" — the
mirror was innocent; auth/me, ep216 and ep284 were all serving the right data.

**Deleting the uid removed ONE copy and left the BAD one**, so it took two `deleteFrontendGlobalWorkflow`
calls. The workflow could never be cleaned up in place because it held an `http-request` plugin
action, which the MCP cannot safely rewrite.

Rebuilt as **`acc70000-1a2b-4c3d-9e4f-5a6b7c8d9e01`**, same name, same two variables
(`accessState` `97b18c0a-…`, `accessDaysRemaining` `28ff491e-…`), but:
- **plain `fetch` instead of the http-request action**, so it stays MCP-editable
- waits for the token, then **retries with backoff [0, 800, 2500]ms** — one lost race should not
  cost a member their badge for the whole session
- any unusable answer leaves `accessState` **"unknown"**, never a guessed tier

**NOT YET VERIFIED — the 401s come from the published app, so this proves nothing until the user
publishes.** After publishing, re-check the ep216 401 rate with `searchApiRequestHistory`
(workspace 1, apigroup 4, query_id 216); it should fall close to zero for signed-in traffic.

## 2026-09-13 — STILL UNPUBLISHED, AND IT NOW LIES TO FREE USERS

Verified by grepping the LIVE published bundle
(`https://www.tulletogether.app/assets/main-DWkRhVi5.js`) — this is how to check publish state
without the WeWeb editor, because app-level workflows ARE inlined in that file (element/section
configs are not):
- `7e2a91c4` appears **twice** → the duplicate `Fetch Access Tier` is still what production runs.
- `acc70000` / `a0_fetch_tier` → **0 hits**. The 2026-09-11 rebuild is still not published.
- `auth-early` → 1 hit and `r0_recover` → 4 hits, so *those* two fixes ARE live. ep216's is the
  only one of the three still sitting in draft.

**ep216 401 rate has gone to 100%, not down.** Last 20 production calls: 20 × 401, every one from
`www.tulletogether.app` with an Authorization header present (the empty-string header, same trap as
2026-09-07). Over one full hour of traffic: 38 of 53 = 72%. The 401 pairs are **exactly 5s apart**,
which is `a0_wait_for_token` timing out — so the published copy never sees the token even though
`Auth Token Early Restore` is live. Worth understanding before assuming the publish alone fixes it.

**New symptom, worse than "Locked":** a brand-new signup with no purchase (user 29392,
`date_until_access: null`, `total_amount_paid: 0`, `FreeViewsRemaining: 3`) renders
**"Member · 3 days left"** in the nav. ep216 would answer `tier:"free", days_remaining:0`, and the
editor's Access Text formula would render "3 free views left" — so this is the published
**`unknown` fallback**, and the published fallback copy does NOT match the editor's. The "3" almost
certainly comes from `FreeViewsRemaining`, but the published Access Text formula could not be read
(element configs are not in the bundle) — **UNVERIFIED**. Re-check after publishing.

Cost of leaving it: free users are told they are Members, so the nav badge argues *against* buying.

## 2026-09-27 — STALE TIER ACROSS ACCOUNTS (fixed in editor, unpublished)
Never-paid test account (vivek.marthi+test_pi, user 30614) showed "Member": accessState was still 'forever' from the
lifetime main account in the same tab - ep216 log showed NO call for the test account after the switch. Fetch Access
Tier only runs on onload-app. Fix: var accessTierOwner c4c09912 (email ep216 returned; 'pending' in flight, '-' signed
out); Fetch Access Tier now 5 actions (a_pending ... a3_store_owner); NEW app workflow 'Refresh Access Tier On Account
Change' acc70001-... on trigger `onload` re-fetches only when the signed-in email != owner. Free users will now see
"N free views left" (designed free state) - user floated "New" wording, not decided.
**2026-09-28 post-publish: ep216 ~30 calls 14:48-15:53 UTC, 0 x 401; but only ONE 401 in retained history at all
(03:43 UTC), so the drop predates this publish.** CORRECTION: clicked_upgrade_from_nav_badge HAS fired - 5 on 09-27
(14-16h) + 2 on 09-28 10h (Mixpanel project tz); "never once fired" above is stale.
**2026-09-28: free state now reads "Free plan" (no count)** - Des worried a nav countdown makes free users ration
PDF views; the view-4 limit popup is the best converter. Low-confidence call, unmeasured. Editor only, unpublished.
Backup: .claude/weweb_backups/access_text_pre_free_plan_2026-09-28.md.
Same session: mobile sticky CTA See Pricing d7c1d97e / View Plans 2afe161a made exact complements (canView = forever ||
date_until_access >= today || FreeViewsRemaining > 0), both width <= 850. Backup:
.claude/weweb_backups/access_badge_and_mobile_cta_pre_fix_2026-09-27.md
