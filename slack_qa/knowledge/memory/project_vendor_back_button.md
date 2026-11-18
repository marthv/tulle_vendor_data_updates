---
name: vendor-back-button-prev-vendor
description: "BUILT 2026-08-25, needs a publish + click-through — the venue page now has TWO back buttons: the original unconditional one to Vendor Discovery, plus a second that appears only when you arrived from another vendor. Two new variables, one app workflow, one capture step that MUST stay first in Load Extracted Data"
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-26T03:57:09.134Z
  originSessionId: bee362a9-3340-4a1d-8dfd-92ba65d8f986
---

Des, 2026-08-25: *"I think you'd want a way to easily go back to the main vendor you were just
looking at. Kind of jarring to only be able to go back to main wptp."* After a partner-vendor
chip hop, the only way back was Vendor Discovery.

## The thing that makes this harder than it looks

**Every vendor card's variable setters are DISABLED.** Verified on all of them:

| card | `vendorSelection` | `Vendor Index` | `Vendor ID` | `Venue Name` |
|---|---|---|---|---|
| Partner Vendor Chip `72561632-…` | disabled | disabled | disabled | disabled |
| Discovery primary `d046cadb-…` | disabled | disabled | disabled | disabled |
| Similar Vendors `105ed00c-…` | disabled | disabled | disabled | disabled |

(Legacy duplicate Discovery cards `abd1ed88-…`, `b7cfba6f-…` still have them ENABLED, and
`b7cfba6f` navigates to the **Other Vendors Page** `d1d9bbf6-…`, not the venue page.)

So the four vendor variables always hold **the last vendor page seen**, which is NOT the same
as "the page you came from". Going venue A → Discovery → venue C leaves `Vendor ID` = A, and a
naive "held id != incoming id" test would offer "Back to A" on C. That is the trap.

## What was built

- **`prevVendor`** `036e2047-c734-44bf-9bc9-1821746691f3` — object `{id, idx, name}`, `{}` when
  there is nothing to go back to. Persistent on nav.
- **`cameFromVendorPage`** `2d6fc52f-4d91-47bb-b0df-60ff92dd6934` — bool. The discriminator the
  vendor variables cannot provide. Persistent on nav, deliberately **not** localStorage so a
  hard refresh starts clean.
- **App workflow `Clear Vendor Back Trail`** `1a7c9f30-0c44-4d21-9b5e-3ae0b6d51f01`, trigger
  `onload` (app scope = fires on every page). Sets `cameFromVendorPage` false on any page whose
  path does not contain `/vendors/`. **On a vendor page it writes the flag back unchanged** -
  that is what makes it safe to run in any order relative to the venue page's own onload
  workflow, since WeWeb does not order two onload workflows.
- **Page workflow `Load Extracted Data`** `362c3d59-…` gained two actions at the FRONT:
  `__CAPTURE_PREV_VENDOR__` then `__MARK_ON_VENDOR_PAGE__`, then the original `if` chain
  untouched.
- **TWO BUTTONS, NOT ONE.** Both live in section `0a0f5ca0-…`, in this order:
  - **`Back Button` `f607fc3d-…`** (workflow `96d0ecfe-…`) - behaviour UNCHANGED from before
    this project. Always goes to Vendor Discovery, label always
    "Back to Transparency Project" / "All vendors".
  - **`Back To Previous Vendor Button` `fe097503-45fb-47f9-a5e3-d2f457e98054`** (icon child
    `a5e381eb-…`, workflow `c1b8e7a4-…`) - conditionally rendered on `prevVendor.id`, green
    outline, label "Back to {name}" truncated 28 desktop / 14 mobile. Its margin is on the
    LEFT at desktop but on the TOP at tablet and mobile, because the container flips to
    `flexDirection: column` at tablet and mobile inherits that.

**WHY TWO BUTTONS AND NOT ONE CONDITIONAL BUTTON.** The first build made the single existing
button switch targets. The user caught the flaw straight away: A → B leaves B pointing at A and
A pointing at B, and **the route back to Vendor Discovery disappears entirely** while the
visitor bounces between two vendor pages. The unconditional button has to STAY unconditional.
Do not "simplify" this back into one button.

## THE ORDERING RULE — do not break it

**`__CAPTURE_PREV_VENDOR__` MUST REMAIN THE FIRST ACTION of `Load Extracted Data`.** It reads
`Vendor ID` / `Vendor Index` / `Venue Name` while they still describe the vendor being left.
`Set Vendor View Page` (fn workflow `76817e7b-…`), invoked from `bc98fbe6` further down that
same workflow, is what replaces them. Anything that runs the capture later reads the vendor
just navigated TO and the button will point at the page you are already on.

This works precisely *because* the capture lives inside the same workflow that later calls
`Set Vendor View Page` - a separate onload workflow would have no ordering guarantee. It is
also why the app-level flag reset had to be written as a no-op on vendor pages.

`Set Vendor View Page` runs on **every** vendor-to-vendor navigation, because its gate is
`query.Vendor_ID != vendorSelection.Vendor_ID` and no card sets `vendorSelection` any more.

## Navigation shape

One level, not a stack. "Back to {vendor}" always returns to the page you came from
immediately, which is what the browser back button would do. After using it, the button on the
page you land on points back at the page you just left - that is correct rather than a bug, and
it is only survivable because the Vendor Discovery button sits beside it at all times.

The change-page carries **both ids plus the route parameter**, copied verbatim from the working
Similar Vendors card: `queries` `Vendor_ID` + `vendor_idx`, and `parameters: { param: <id> }`.
**The key is `param`** - see [[project_partner_vendors]] and [[reference_weweb_mcp_limits]];
any other key resolves the route to the literal string `undefined` and navigation silently
does nothing while the rest of the workflow keeps running.

## Status

**BUILT IN THE EDITOR, NOT YET PUBLISHED AND NOT YET CLICKED THROUGH IN PRODUCTION.** The
editor re-evaluates far more eagerly than the published router, so editor-only verification
proves little here - that is exactly how the partner-chip `param` bug survived a whole session.
Publish, then walk venue → partner chip → check BOTH buttons appear and each goes where it
says, and separately Discovery → venue, where only the Transparency Project button should show.

One incidental change: the Back Button's trailing `update-collection` action (already
`disabled`, targeting `bd7af501-…`) was dropped because `editFrontendWorkflow` rejects that
action type. It was inert.

Related: [[project_partner_vendors]], [[project_pi_panel_nonvenue_bug]],
[[reference_weweb_mcp_limits]].


## 2026-09-06 — PUBLISHED
User confirmed this is published and live in production. Do not re-raise it as pending.
