---
name: project_pdf_dead_click
description: "PDF 'nothing opens, no error' = window.open called AFTER an await in the desktop View PDF workflows; mobile works because it opens first"
metadata: 
  node_type: memory
  type: project
  originSessionId: 30aa8b0d-371d-4c16-8392-19a194145536
  modified: 2026-09-07T13:43:49.954Z
---

2026-09-07. Three reports (Brianna McCormick, Krista, Deborah Lopez) of "clicked to view
pricing, nothing opened, no error." **Root cause: the desktop View PDF workflows call
`window.open` only after awaiting Xano, so the browser silently blocks the popup.**

## The proof — mobile vs desktop is a natural experiment

Same button, same PDF link, same user state. Venue Vendor Page `e49b4e74`:

- **Desktop** `94885c62` / workflow `3cb30243` — `firstAction: a97818d6` (entitlement `if`).
  - Paid branch: `39f41b1e` **PATCH /user/update_record** → `4394b242` Go to Page
    (`openInNewTab: true`).
  - Free branch: PATCH (`3aa63b5d`/`e7257e7a`) → `18a2a1d3` **fetchUser** → `64d7c82c`
    Go to Page. **Two** sequential round trips before the open.
- **Mobile** `bd4d43da` / workflow `7f238eae` — `firstAction: d615915f` = **Go to Page
  (PDF_Link, openInNewTab: true)**, THEN the entitlement check.

`change-page` + `navigateMode: external` + `openInNewTab: true` compiles to
`window.open(url, '_blank')`. It only escapes the popup blocker while the click still has
transient user activation. After an `await` of a network request that activation is gone,
`window.open` returns `null` **silently** — no tab, no error, no exception. Safari/WebKit is
strictest (same synchronous turn only); Chrome/Firefox fail once the round trip outruns the
activation window, which is why it is intermittent and browser-dependent.

**The known mobile paywall bypass is exactly why mobile never fails.** Do not "fix" the
bypass by moving the mobile open after the check — that would break mobile the same way.

## Why the user evidence fits

- Brianna (28708): `forever_access_purchased: true`, `date_until_access: 3000-01-01`,
  `total_amount_paid: 129`. Paid branch is CORRECT for her, so 0 limit popups is expected,
  not a fall-through. `total_pdf_views: 47` — the PATCH ran every time; only the open
  no-oped. 46 `viewed_vendor_pdf` events because that tracking fires *after* Go to Page
  regardless of whether a tab opened.
- Krista (28586): `FreeViewsRemaining: 1`, `total_pdf_views: 2`, unpaid. The gate ALLOWED
  her — she **burned free views on dead clicks**.
- Deborah: no user row — **RESOLVED: she deleted her account.** Not a data bug.

Kate's fresh-account test succeeding does **not** prove user-state dependence: both open
paths have the same defect, so the discriminator is browser/latency, not state.

## What was ruled OUT (do not re-investigate)

- **The three erroring Xano MCP tools are a red herring.** `get_vendor_pricing_details`,
  `list_wptp_pdfs`, `search_wptp_vendors` are the already-diagnosed broken MCP *tool
  definitions* — see [[reference_broken_xano_mcp_tools]]. `mvpw1:0` is an alias inside the
  tool script. Nothing to do with production or with tables #10/#11.
- **Tables #10/#11 are intact.** Full schema present (PDF_ID, Vendor_ID, PDF_Link,
  Show_PDF). Workspace audit log shows **zero** changes Sept 1–6 — only `workspace:read`.
- **Vendor data is fine.** V380/P380, V492/P492, and V2679 (P11402 **exists**, 2028,
  Show_PDF true, valid Drive link) all resolve. The §3 "missing wptp_pdfs row" worry is dead.
- **ep21 is healthy** — 54–414 ms, all 200.
- The Aug-30/31 endpoint lockdown is REAL and live, but it is a SEPARATE bug — see
  [[project_endpoint_lockdown_401s]]. It does not touch the PDF open path: opening the PDF
  is a `window.open` on a Google Drive URL and calls no Xano endpoint at all, so auth
  cannot break it. Reporters were provably logged in (Brianna's row took 47 writes).
- The Sept 6 `tulle.paywall` instrumentation only touches the *block* branch
  (`d1f7e00*`), never the open path, and is guarded. Not the trigger.

## ⚠️ CORRECTION 2026-09-07 — THE DIAGNOSIS BELOW DOES NOT COVER BRIANNA

**Brianna was on an iPhone.** Mixpanel "Event Volume by Device" for brimccormick9@gmail.com
is essentially all iPhone (avg 3.8 events/hr iPhone vs 0.2 "not set"). On iPhone she gets the
**mobile** button `bd4d43da` ("View Details") — the desktop "See Pricing" `94885c62` is hidden
below 480px — and **the mobile workflow ALREADY opened the PDF as its first action.** So the
popup-block-after-await mechanism cannot explain her, and **the reorder fix does not touch
the path she was on.**

Also ruled out: **Drive permissions are fine.** All three reported PDFs
(Bohemia `1AFKJQ52…`, Shadowbrook `1y6mqXPK…`, Calamigos `1M-McFJ1…`) return
`{"role":"reader","type":"anyone"}`.

**Best remaining hypothesis, NOT verified:** WeWeb's workflow engine is async, so even a
"first action" runs a microtask after the gesture rather than inside it. Desktop Chrome
forgives that (transient activation lasts seconds); **WebKit does not** — it wants
`window.open` in the same synchronous turn. Every iOS browser is WebKit. That would mean
**iOS is broken on BOTH the mobile and desktop paths**, desktop Safari was broken and is now
fixed, and desktop Chrome was broken only on the slow path and is now fixed. Kate's passing
test fits desktop Chrome.

**THE iOS HYPOTHESIS ALSO FAILED.** `viewed_vendor_pdf` events per unique user, 30d, by $os:
**Mac OS X 10.3, Android 9.1, Windows 8.7, iOS 4.1.** If iOS were dead-clicking, iOS users
would click MORE per person; they click the LEAST. So there is **no population-level signal
that any platform is broken**, and Brianna (46 events in a day vs an iOS mean of 4.1) is an
extreme individual outlier, not a representative case. **Do not re-run this — it is answered.**

Net: the desktop ordering defect is real IN THE CODE, but nothing in the data shows it
caused failures at scale. Whatever hit those three users is rare, individual, or invisible
to current instrumentation. Two hypotheses (desktop popup-block, iOS/WebKit) have now both
failed against population data — **do not advance a third from inference alone.**

**NEXT STEP IS REPRODUCTION, NOT MORE LOG-READING.** Open Bohemia Overlook on an iPhone and
tap "View Details". Every signal available remotely (counter increments, tracking fires,
Drive readable, workflow shape) is equally consistent with "tab blocked" and "tab opened and
she didn't notice" — only a human on an iPhone can separate those. If it is the WebKit timing
issue, the likely fix is rendering the PDF row as a real `<a href target="_blank">` so the
browser treats it as a genuine link instead of a script-opened window.

Krista's device is unknowable — she has **zero Mixpanel events** attached to her email
(identity failure), per the original handoff. Deborah deleted her account.

## STATUS 2026-09-07: DESKTOP REORDER DONE IN THE EDITOR, NEEDS A PUBLISH

Applied by hand: on workflow `3cb30243` both allow-branches now open FIRST.
- Paid: `a97818d6` -> `4394b242` Go to Page -> `39f41b1e` PATCH -> `deae6144` -> `66028b7e`
- Free: `48c1dc23` -> `64d7c82c` Go to Page -> `cd9e2f34` -> patches -> `18a2a1d3` fetchUser -> `17308c4b`

This also incidentally fixes the `auth/me` failure mode: the PDF now opens BEFORE
`18a2a1d3` fetchUser, so a 401 there can no longer abort the click short of the open.

**The Other Vendors page needs NO separate edit.** Its button
`b0fd4fee` carries **the SAME workflow object, id `3cb30243`** — not a copy. One edit
covered both pages. (The older note in [[project_pdf_warning_view3]] calling it a separate
instance needing independent edits is WRONG for this workflow — verified 2026-09-07.)
Both mobile buttons were never affected.

**Do NOT replace `change-page` with a hand-written `window.open` custom-js** — it was tried
and reverted 2026-09-07. It threw a ReferenceError (`url`/`vendorId` are not in scope in a
custom-js action) and, sitting after Go to Page, opened a SECOND tab. Not worth the risk in
the highest-value click path for a measurement nicety.

**Verifying it without new instrumentation:** repeat `viewed_vendor_pdf` on the SAME
`vendor_id` within one session is the dead-click fingerprint (Brianna: 46 events / 51 vendor
pages in a day). If that cluster disappears post-publish, it worked.

## Original fix analysis (editor-by-hand — MCP CANNOT edit these four workflows)

`editFrontendWorkflow` rejects them with `[WRONG_ACTION_TYPES]` (Xano plugin actions) —
see [[reference_weweb_mcp_limits]] and [[project_pdf_warning_view3]].

The branch conditions read only already-loaded user state, so **nothing needs to be awaited
to decide whether to open**. Safest minimal change — two `disabled` toggles plus one new
workflow, no `next`-pointer retyping:

1. Add a new `click` workflow on each PDF button whose FIRST action is a `custom-js` that
   synchronously opens the PDF when entitled, and reports failure:
   `const w = window.open(url,'_blank'); if(!w) mixpanel.track('pdf_open_blocked',{...})`
   — this closes the instrumentation gap at the same time.
2. Set `4394b242.disabled = true` and `64d7c82c.disabled = true` so the old late opens do
   not spawn a second tab.

Alternative (riskier, rewires pointers): move `4394b242` ahead of `39f41b1e` on the paid
branch, and `64d7c82c` to the front of `48c1dc23`'s TRUE branch.

**Instrumentation gap this explains:** `viewed_vendor_pdf` fires on click *after* the open,
so the funnel structurally cannot see the failure — it looked flat at 68% throughout.

Minor, unrelated, noticed while reading: desktop `a97818d6` compares
`date_until_access >= date(dateRealtime())` while mobile wraps it in
`wwFormulas.formatDate(...)`; and `4394b242`'s gview wrapper is dead code on a
desktop-only button.
