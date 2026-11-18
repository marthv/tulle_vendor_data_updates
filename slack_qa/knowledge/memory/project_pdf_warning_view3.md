---
name: project_pdf_warning_view3
description: "Last-free-PDF warning at view 3 — built in WeWeb, NOT published; plus the real PDF-meter funnel numbers and three pre-existing bugs in the View PDF workflows"
metadata: 
  node_type: memory
  type: project
  originSessionId: e3a318c0-12d9-411a-96d1-b49f5bc40302
  modified: 2026-08-17T03:18:25.551Z
---

2026-08-16. Added a non-blocking "this is your last free pricing PDF" warning on the **3rd**
(final) free PDF view. The hard block stays at view 4. **Built in the WeWeb editor and
deliberately NOT published** — live users are unaffected until someone publishes.

Full element/workflow inventory and unwind steps:
`.claude/weweb_backups/pdf_warning_pre_change_2026-08-16.md`.

## The funnel numbers (Mixpanel 3963130) — and the trap

7-day window: 592 users viewed ≥1 PDF → 396 ≥2 → **286 ≥3** → **102 ≥4**. The wall is the
3→4 step (36%). 129 users saw `pdf_limit_popup_triggered`, **15 bought = 12%**, roughly
**40% of all membership purchases**. It is by far the best-converting surface — advanced
filters CTA 502 clickers/30d, nav bar 272, PI panel 9, and all surfaces together yield only
~40 purchases/month.

⚠️ **`membership_purchased` only started firing 2026-08-09.** Any funnel window longer than
~8 days pairs a full denominator with a partial numerator and reports the popup converting
at **1.4% instead of the true ~12%**. Always match the windows. See
[[project_mixpanel_tracking_gaps]].

Sizing the warning: ~157 users/week reach 3 PDFs and never return to trigger the wall
(view3→view4 averages 4.5 hours, i.e. a later session, and only 36% come back). Estimated
**+1.5 to +3 purchases/week (+4% to +9%)**, ~+$250–500/month. A topline A/B is underpowered
at ~35 purchases/week — read `pdf_warning_triggered` and the 3→4 step conversion instead.

**The bigger lever is the pricing page, not the popup:** ~1,371 CTA clicks/month across all
surfaces produce ~40 purchases.

## Mechanism (differs from the PI allowance — do not conflate)

The PDF meter is `user.FreeViewsRemaining` (starts at 3), decremented **client-side** by the
View PDF workflows via `PATCH /user/update_record` (ep21, `api:WGW_G49d`). On exhaustion it
sets `date_until_access = today`, which is what expires the user. This is a **different
counter** from the Pricing Intelligence allowance (`total_vendor_views` / ep230 /
`FREE_VIEWS = 3`) — see [[reference_access_entitlement_model]].

Logic lives on **4 buttons across 2 pages** (desktop + mobile on both Venue Vendor Page
`e49b4e74` and Other Vendors Page `d1d9bbf6`). The Other Vendors page is a copy and reuses
the *same* workflow and action ids as the Venue page desktop button — but they are separate
instances, so each must be edited independently.

## Key implementation lesson

**`editFrontendWorkflow` cannot touch any of these four workflows** — it rejects
`cd33cf33-…-request` and `f5856798-…-fetchUser` with `[WRONG_ACTION_TYPES]`, reconfirming
[[reference_weweb_mcp_limits]].

The workaround, which is better anyway: **a WeWeb element can hold multiple workflows on the
same trigger.** So the warning is a *second* `click` workflow on each button that
re-derives the condition (`FreeViewsRemaining == 1 && !(date_until_access >= today)`)
instead of chaining off the existing one. Zero edits to existing logic; unwind is pure
deletion. `FreeViewsRemaining` is read synchronously on click, before the other workflow's
PATCH + fetchUser resolves, so it reliably sees the pre-click value.

Kill switch: set `pdfWarningEnabled` (`0909888f-0b8b-4809-9900-3e0f92c9c73c`) to false.

## Limit popup redesign (same day)

The old limit popup is **library component `48be8a6c`** — NOT an element on any page.
**The MCP cannot read or edit WeWeb library components** (only coded Vue components), so it
was *replaced* with a page-overlay of the same pattern, not edited.

**Suppressing the old popup without editing the untouchable workflows:** the four PDF Limit
Trigger workflows end with `wait 60ms → close-all-popup(48be8a6c)`. `close-all-popup` is a
no-op when nothing is open, so this works whether or not the old `open-popup` is disabled
by hand. The 60 ms delay matters — **element workflows on the same trigger have no
guaranteed order**, so without it the close can fire before the old popup opens and miss.
Closing it also lets the original workflow resume past `waitClosing` and expire
`date_until_access`, so entitlement still behaves correctly.

Optional cleanup to remove any flash: disable the `open-popup` action in the four View PDF
workflows — `fd918f2b` on three, but **`45b0b289` on the Other Vendors mobile workflow
`b6f849c2`**, whose action ids differ from its twins. Never delete it or change its `next`.

The new CTA deliberately re-fires the **existing**
`clicked_membership_for_pricing_pdf_limit_popup` event so the redesign is comparable
against the old 12% baseline on one continuous funnel.

**`/pricing` shows conflicting prices** — one grid reads 1wk $30 / 4wk **$50** / forever
**$119**, another reads $30 / **$55** / **$129**. The overlay therefore anchors only on
"from $30" (both grids agree) and leaves the plan matrix to `/pricing`. Worth reconciling —
see [[project_budget_roadmap_relaunch]], which already flags a related `/pricing` bug.

## Three pre-existing bugs found — reported, NOT fixed

1. **Mobile is a paywall bypass.** Both mobile workflows open `PDF_Link` as their FIRST
   action, before any entitlement check. A user with 0 free views gets the PDF anyway; the
   block popup just appears behind the new tab. Desktop checks first and is correct.
2. **`budget_roadmap_popup_shown` fires with no popup** — the `open-popup` is
   `disabled: true` but the tracking action downstream of it is not (~27 events/30d).
   There is a dormant view-2 warning slot there, gated on
   `Planning_Phase == "Not engaged... yet"`.
3. **Mobile patches `total_views` instead of `total_pdf_views`** (Venue `3aa63b5d`,
   Other Vendors `1c5c9048`) — likely a typo; the field may not exist.
4. **The block looks like it only bites once per day.** On exhaustion `d653f81b` sets
   `date_until_access = date()` (today), and the gate `a97818d6` tests
   `date_until_access >= date(dateRealtime())` — **`>=`, so today passes**. Every later
   click that day takes the PAID branch: PDF opens, no decrement, no popup. This is the
   client-side twin of the ep230 midnight bug fixed 2026-08-16; note the PI panel gates use
   strict `>` while this workflow uses `>=`. **Inferred from reading the workflow, not
   runtime-verified** — confirm before acting, but if real it is a live revenue leak and it
   caps how often the limit popup can ever fire.

## 2026-09-30 re-check (from LIVE page data, not the editor)
CORRECTION: bug 4 ("free rest of the day") is NOT live - date() returns a full ISO timestamp, so
"2026-09-30" >= "2026-09-30T..." is false; access-date-today users take the limit branch. Bug 3 (total_views)
is fixed on the Venue mobile row button bd4d43da but still on sticky "See Pricing" d7c1d97e (step 3aa63b5d)
and the Similar-Vendor "Desktop Pricing Pill" 34432f9e / b58e7825 (which also still opens OLD popup 48be8a6c).
MOBILE GAP (Des 09-29): the sticky bottom bar "See Pricing" d7c1d97e has NO warning/limit/NPS workflows,
always opens data[0], spends a view per tap; at 0 views "View Plans" 2afe161a navigates to /pricing instead of
the overlay. Row "View Details" bd4d43da / limit button 7d0b20c2 path works (125 iOS limit-CTA clicks/28d).
7d0b20c2 has NO tracking. No server-side PDF entitlement: ep21 accepts client free_views_remaining and
date_until_access. [[project_security_lockdown_2026-09-29]]

## 11-12 PDF LIMIT card made venue-specific (EDITOR, unpublished; owner "lets definitely do that")
Venue page e49b4e74 card 614fcf8f (heading cdf6bf40 "Unlock {Venue}'s real pricing.", body 59839e99, button c5ec82c5, new blurred preview 523d97a9 "Next: {Venue}'s full pricing guide", new plan row f5064256 Forever 0d2d249f / 4 weeks 63e92770, benefits box ad975b29 hidden on phones). Other Vendors d1d9bbf6 card 0942ec05 (heading e412d507, body e87ab7ed, button e0a1fc7a, preview 2924ba75, plan row d434c30f fd0fc9d6/cea93b66, benefits d13ab5eb). Click events keep clicked_membership_for_pricing_pdf_limit_popup (+vendor_id, vendor_name, plan, button) and go to /pricing?src=pdf_limit&vendor_id&vendor_name(&plan=forever|4w). pdf_limit_popup_triggered + z 401 unchanged; Keep browsing -> What stopped you unchanged; PDF Warning overlay + popup 48be8a6c untouched. No prices shown on plan buttons (region-dependent). Backup .claude/weweb_pdf_limit_pre_contextual_2026-11-12.json. Preview: set showPdfLimit ea10d6e8 + pdfLimitEnabled d42e8d5b true, current_venue_name fd02dbef.
