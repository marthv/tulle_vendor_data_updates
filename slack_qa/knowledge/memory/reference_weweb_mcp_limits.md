---
name: reference_weweb_mcp_limits
description: "WeWeb MCP limits — can't edit workflows containing plugin actions, can't edit collections; editElement 'not found' = orphaned element; changes need a publish"
metadata: 
  node_type: memory
  type: reference
  originSessionId: f766efea-9c3b-4351-bcf8-87e1c009cbf7
  modified: 2026-08-16T20:41:25.808Z
---

Hard limits of the WeWeb MCP on project `d830a133-815b-419b-8efe-a0f104b3ece2`, all hit in practice:

- **`editFrontendWorkflow` CANNOT edit any workflow containing plugin actions.** It requires the
  full action map to be sent back, and its validator rejects the very action types already living in
  the workflow: `cd33cf33-e29f-4e8c-ac26-b997fe507ce7-request` (Xano REST) and
  `f5856798-485d-47be-b433-d43d771c64e1-fetchUser` → `[WRONG_ACTION_TYPES] … not found in the list
  of all workflow actions`. **Validation fails before any write, so a rejected call is safe — verify
  by re-fetching.** Sending only the changed actions *might* merge or *might* replace the whole map
  and gut the workflow; untested, do not gamble on a live one. **These workflows must be edited by
  hand in the WeWeb editor.** Workflows made only of `custom-js` / `if` / `update-variable` /
  `open-popup` / `change-page` DO edit fine (e.g. the purchase-complete poll).
- **MCP cannot edit collections at all** — no tool exists. Collection config (auth headers,
  `isAutoFetch`, endpoints) is editor-only.
- **`editElement` reporting "not found in semantic representation" means the element is ORPHANED**,
  not that the uid is wrong. `getPageElementsByUid` reads element STORAGE and will happily return
  it; `editElement` reads the page TREE. If no slot references it, it exists but never renders.
  Confirm with `getPageSemantic` (which includes every element on the page).
- **Every WeWeb change needs a publish to reach production** — the editor and `www.tulletogether.app`
  are different states. A rollback needs a publish too, so it is not instant.
- `getWeWebDocumentation type=wewebAuthContext` describes **weweb-auth, which is NOT installed here**
  — auth is the Xano plugin `f5856798-…`. Neither `advancedFrontend` nor `dataAccess` documents
  `pluginVariables` at all, so plugin variable names cannot be looked up from the docs; find a
  working example in the project instead. See [[reference_access_entitlement_model]] for the
  auth-token accessor problem this caused.

## `customCss` swallows pasted CSS RULE BLOCKS — broke Sign In and Sign Up

2026-08-18. Both auth pages rendered as an ~84px (Sign In) / ~44px (Sign Up) **green square**
with the whole login form clipped out of view. Cause: a scrollbar CSS rule was pasted into the
`customCss` field on `Auth Container`, and WeWeb flattened the rule block into individual
properties instead of keeping it as a nested rule:

```
"customCss": { "": ":-webkit-scrollbar-thumb:hover {", "  width": "4px",
               "  height": "4px", "  background-color": "#03a87c", ... }
```

So the element itself got `width:4px; height:4px; background-color:#03a87c`, overriding the real
`width:420px` / `#F6FAF8`, and `overflow:hidden` on the same element clipped every child. The
form was never missing — it was intact in the tree the whole time. **A green/black tiny box
where a container should be = look at `customCss` first.**

Not an editor artifact: it is real CSS and renders the same live. It only stayed out of
production because WeWeb editor changes need a Publish.

**`editElement` on `customCss`: `value: {}` SILENTLY NO-OPS, `value: null` WORKS.** Both return
"Element updated". Confirmed by reading back — this is the same lying-success-string family as
the `initValueSingle` no-op in [[project_popularity_recency_sorts]]. Always read back.

Fixed on `540ea276-…` (Sign In) and `7ffb2dee-…` (Sign Up, the Auth Container inside
Right Container).

### Two other things found on those pages, NOT fixed
- **Sign Up has two sign-up forms.** `03bc72da-…` at depth 1 (Name/Email/Password/Checkbox) is a
  dead duplicate held off by `conditionalRendering: false`; the live one is `7ffb2dee-…` inside
  Right Container, whose inputs are suffixed `Name Input1` / `Email Input1` / `Password Input1`.
  Edit the wrong one and nothing changes on screen.
- **A "Temporary Outage" banner sits on both auth pages** (`a1704f13-…` on Sign In) whose
  `conditionalRendering` is a formula with **no `code`** — `{"__wwtype":"f","defaultValue":true}`.
  It currently evaluates falsy so the banner is hidden, but the declared default is `true`.

## POPUPS ARE NOT REACHABLE THROUGH THE MCP

2026-08-18. WeWeb popups are a separate object type from pages, and the MCP exposes **no**
popup tool — no list/get/edit equivalent. `searchPages` only matches pages; `getPageSemantic`
requires a pageId; `getPageElementsByUid` needs uids you can only obtain from a page semantic.

Symptom that wastes the most time: a popup's elements appear in **no page semantic at all**.
Hunting for the Onboarding Popup cost four full page dumps (Vendor Discovery, Sign Up, Sign In,
My Wedding) plus `listCodedComponents`, all empty, before the user said it was a popup. **If an
element is visibly in the editor but absent from every page semantic, suspect a popup early.**
The editor tell is a "Back to page" button above the Layout Tree — that means you are inside a
popup/section editor, outside the page tree.

Its state variables ARE visible via `searchVariables` even though the elements are not:
`Onboarding_Step` `0a49d072-…` (default "1"), `onboardingData` `0c6d4d8c-…`
(age/budget/isVendor/location/guestCount/currentStep/mainInterest/planningStage/referralSource),
`onboarding_loader` `387a63e6-…`, `onboarding_flow_step` `4105c4ff-…`. Variables are therefore
the only handle on popup logic from here.

**Workaround for popup design work:** build the redesign as a static HTML mock against the
CLAUDE.md tokens, screenshot it, and hand over a value-by-value spec to apply by hand. Inherently
reversible because nothing is touched. Mock + render kept at
`tulletogether/.claude/weweb_backups/onboarding_popup_redesign_2026-08-18.{html,png}`.

If popups ever need to be MCP-editable, the precedent is `upgrade-popup` — an existing **coded
component** (type `ai`, `isEditable: true`), and coded components ARE reachable
(`listCodedComponents`, `loadComponentFiles`, `publishComponent`).

## `editElement` CANNOT REMOVE a `link` — and that decides element structure

2026-08-18. `link` edits MERGE; they never delete. On the New Category Banner
(`27e9b4c5-…`, Vendor Discovery) all of these returned "Element updated" and left the link
fully intact on read-back:

- `{"path":"link","value":null}` → no-op
- `{"path":"link","value":{}}` → no-op

Only sub-property writes land: `{"path":"link.pageId","value":"<id>"}` does change `pageId`.
So **once an element is a link, the MCP can only re-point it, never un-link it.** Clearing a
link is an editor-only action (select element → clear Link).

**Why this matters structurally:** a linked container makes every descendant navigate on click,
and there is **no stopPropagation / preventDefault** anywhere in the frontend workflow model
(confirmed against `getWeWebDocumentation type=frontendWorkflows` and
`getFrontendWorkflowActions`). So a dismiss "X", or any child button, **cannot live inside a
linked container** — its click will also fire the link. Plan the tree accordingly: put the link
on an inner content div, not the root, and keep interactive children as siblings of it.

Neutralising trick when un-linking is impossible: point `link.pageId` at the **current page**.
That is inert (same-route nav is a no-op) and deterministic — unlike `pageId: ""`, whose rendered
href is unknown, and unlike leaving a real destination.

Related no-op family: `customCss` `{}` above, and `initValueSingle` in
[[project_popularity_recency_sorts]]. **Always read back after `editElement`.**

Also: placeholder UIDs in `addElements` JSONL are substituted in `slots`, but **NOT inside string
values** — an inherited-state id written as `_wwParent_UID0__wwHover` is stored with the literal
`UID0`. Write those after creation using the real uid. And `states` edits merge too, so a bad
state id cannot be removed afterwards (a stale unlinked state is harmless — it just never fires).

## `customCss` compiles to a CLASS, not an inline style — and how to get @keyframes in

2026-08-19/20. Setting `style.default.animation` via `editElement` is silently rerouted by WeWeb
into **`style.default.customCss.animation`** — a flat property object, which is the SAFE form
(contrast the pasted-rule-block disaster above). Read back and expect it at that path, not where
you wrote it.

Consequence that bit: WeWeb compiles `customCss` into a **generated CSS class**, so the value
never appears in an inline `style` attribute. A guard written as
`[style*="my-animation"] { animation: none }` matches NOTHING. To disable an animation under
`prefers-reduced-motion`, **redefine the `@keyframes` to a flat no-op inside the media query** —
a later same-name definition wins while the query matches, and it stays scoped to that one
animation instead of nuking every animation on the page.

**There is no MCP-reachable global-CSS field.** To define `@keyframes` at all, add a `ww-html`
element holding a `<style>` block; it renders nothing in production. Put it **outside any
repeater** — inside one, the block is duplicated per row. Working example on Vendor Discovery:
"Pulse Keyframes" `6f181c19-…`, a child of Filter Rail Sidebar.

`addElements` **requires an explicit `uid` field** in the JSONL or it errors with
`expected string, received undefined` — and then WeWeb assigns its OWN uid anyway, so always
take the uid from the response, never the one you supplied.

**On `NETWORK_ERROR` / "transport dropped mid-call", the write may or may not have landed.**
Hit both: a two-element change where the first edit applied and the second silently did not,
leaving a half-applied state that reported as a failure. **Always re-read before retrying or
assuming either outcome.**

**Uids quoted in a page DESCRIPTION can be stale/orphaned.** The Vendor Discovery description
names the paywall card as `e7a82a97-…` and a Section Blur Overlay `e15ebfed-…`; neither resolves.
Trust `getPageSemantic`, never the prose. See [[project_advanced_filters_paywall]].

## RE-PARENTING IS IMPOSSIBLE, AND SOME WORKFLOWS CANNOT BE EDITED AT ALL

2026-08-24, both hit while finishing the partner-vendors card.

- **`editElement` refuses `slots` outright**: `{"path":"slots","value":[...]}` returns
  **`Editing slots is not allowed, invalid path: slots`**. There is no move/reparent tool
  either (checked: only `addElements`, `deleteElement`, `replaceElement`, `editElement`).
  **So moving an element to a different parent is EDITOR-ONLY, a manual drag.** Plan around
  it: if a task says "move X into Y", that half of the work cannot be automated. The refusal
  is a clean pre-write validation, so nothing is half-applied.
- **A workflow with no `id` cannot be edited or deleted.** `editFrontendWorkflow` and
  `deleteElementWorkflow` both address workflows by uid, and some existing element workflows
  come back from `getPageElementsByUid` with **no `id` key at all** (e.g.
  `Partner Vendors Expand` on `af65ca6b-…`, Venue Vendor Page). Workflows created through the
  current MCP DO get an id. Creating a replacement with a fresh id would leave TWO workflows
  on the same element+trigger, both firing — so for an id-less workflow the only safe fix is
  by hand in the editor.
- `createElementWorkflow` / `editElementWorkflow` are **deprecated**; use
  `createFrontendWorkflow` / `editFrontendWorkflow` (`scope: "element"` + `elementUid` +
  `pageId` + `trigger`).
- **Style values accept JS bindings and WeWeb rewrites them.** Sending
  `style.default.cursor = {code, __wwtype:"js"}` reads back as `__wwtype:"f"` with the
  expression preserved and **the comments stripped**. Don't put load-bearing explanation in a
  style binding — it will not survive.
- **To navigate on click, prefer a `change-page` workflow over an element `link`.** Given
  `editElement` can never remove a `link`, a bound link is unrevertible; a workflow is not.
  `change-page` takes `parameters.param` for a route param plus `queries[]`. Working example:
  `Partner Vendor Open` `3f0c9a71-…` (an `if` gate + `change-page`) and the older
  `Vendor Card 1` `105ed00c-…`, both on the Venue Vendor Page.

## `change-page` PATH PARAMS: THE KEY IS `param`, AND A WRONG KEY FAILS SILENTLY

2026-08-24, cost several production round-trips on the partner-vendor chips.

The Venue Vendor Page route is `vendors/{{param|VendorID}}`. In a `change-page` action the
`parameters` map key must be **`param`** — NOT the display name from the route
(`VendorID`), and NOT a field name like `Vendor_ID`:

```
"parameters": { "param": { "code": "return context.item.data?.['vendor_id']", "__wwtype": "js" } }
```

**THE SIGNATURE OF A WRONG KEY IS THE LITERAL STRING `undefined` IN THE URL PATH:**
`tulletogether.app/vendors/undefined?Vendor_ID=V573&vendor_idx=573`. Queries populate fine,
the path slot gets `undefined`, and **nothing errors** — no console message, no failed request.
If a navigation "does nothing", read the URL bar first.

**A `change-page` that fails to navigate does NOT stop the workflow.** Actions chained AFTER it
still run. On the partner chips that meant every dead click still fired `update_vendor_views`
(burning one of the user's metered vendor views) and logged `viewed_vendor_page` to Mixpanel.
**That is also the cheapest way to tell the two failure modes apart:** open Network, click, and
look for a request from an action that sits after the navigate. If it fires, the workflow is
running and only the navigation is broken; if it does not, the chain is halting earlier.

**"Works in the editor, dead in production" is expected for routing bugs** — the editor
re-evaluates bindings and re-runs page logic far more eagerly than the published SPA router.
Never sign off a navigation change on the editor alone.

Also: `parameters` and `queries` are independent. Getting the queries right is not enough when
the destination page keys its data fetch off a path param, and vice versa - the venue page needs
BOTH (`vendor_idx` query for the collection fetch, `param` for the route change).

## 2026-09-06 — plugin actions cannot be CREATED either, and the way around it

The limit is wider than "can't edit workflows containing plugin actions": `createFrontendWorkflow`
**rejects plugin action types outright** — `[WRONG_ACTION_TYPES] Action "…-fetchUser" not found`,
same for `…-request` (the Xano API Request action). The MCP validates against its built-in action
list only.

**The way around it: `http-request/http-request` and `custom-js` are built-in**, so a workflow
built from those stays fully MCP-editable. For an authenticated Xano call, bind the header
yourself — the token lives at
`pluginVariables['f5856798-485d-47be-b433-d43d771c64e1']['accessToken']`
and Xano accepts `Authorization: [redacted] <token>`. (Existing WeWeb *collections* send it as an
`Auth_Token` header instead; both work.) Set `throw: false` so a logged-out visitor does not
surface an error. Confirmed working pattern in `Fetch Access Tier` `7e2a91c4-…`.

Two shape gotchas that cost a retry each:
- **`custom-js` takes `code` at the TOP LEVEL of the action**, not inside `args`. Putting it in
  `args` fails with a bare `Cannot read properties of undefined (reading 'includes')`.
- **`addElements` requires an explicit `uid` on every JSONL line** or it errors with
  `expected string, received undefined` — but then **assigns its own uid anyway**, so read the
  returned semantic for the real one instead of reusing what you sent.

`f5856798-…` is the auth plugin (`user`, `accessToken`); `cd33cf33-…` is the Xano REST plugin.
The project uses **Custom Auth**, not the Xano auth integration (which reports uninstalled).

## `editFrontendWorkflow` action-arg shapes (learned 2026-09-07)

Action args are NOT uniformly nested under `args`. It varies **per action type**, and getting it
wrong fails with the useless message **`Cannot read properties of undefined (reading 'includes')`**:
- `custom-js` → `code` at the **top level** of the action object (NOT `args.code`).
- `update-variable` → `varId` / `varValue` at the **top level**.
- `http-request/http-request` → nested under **`args`** (`url`, `method`, `headers`, `timeout`, `throw`).

Copy the shape from an existing action of the same type before writing a new one. Omitting
`actions` entirely gives a different error, `Cannot convert undefined or null to object` — so
`actions` is required even when you only mean to change the description.

Searching a workflow by id can return **two entries with the same id**: the draft and the currently
published version. The first is the draft you just wrote. That is normal and resolves on publish.

## Element workflows are stored PER ELEMENT even when they share an id (2026-09-07)

Three buttons on Vendor Discovery all reported the same element-workflow id
`626dab60-13df-49f9-9789-92f019e98d9f` ("Navigate to Payment"). **They are NOT one shared
workflow.** `editFrontendWorkflow` with `scope:"element"` writes only the copy belonging to the
`elementUid` you pass; the other elements keep the old definition and still report the same id.

**Always re-read every element after editing a supposedly shared workflow.** The verification is
`getPageElementsByUid` on each one and comparing `firstAction`. Missing this ships the change on
one of three surfaces and silently leaves the others on the old behaviour.

`getPageElementsByUid` takes only `pageId` — there is **no way to read style, conditionalRendering
or element-workflow ids for elements INSIDE a library component**. `getComponentSemantic` gives
the tree and text but truncates long strings (`[TRUNCATED_TEXT: …]`) and omits styles entirely.
`editElement` and `createFrontendWorkflow` DO accept `componentUid`, so component internals are
write-only through the MCP. **Never blind-write `conditionalRendering` onto a component element** —
you cannot read what is already there to preserve it.

## `http-request/http-request` result shape — assuming `.data` silently discards a 200

**2026-09-07, the bug that hid the whole upgrade path.** ep284 returned **HTTP 200 with
`eligible: true`** (confirmed in Xano request history), yet the WeWeb variable it fed stayed at its
hidden default `eligible: false`. Nothing errored; the value was simply thrown away.

Cause: the store step read `context.workflow['<action>'].result['data']`. For the native
**HTTP Request** action that path was empty, so the guard `if (!body …) return hidden` fired on a
perfectly good response.

**Write it shape-agnostically:**

    const r = context.workflow?.['<action_id>']?.result;
    const body = (r && typeof r === 'object' && r['data'] && typeof r['data'] === 'object')
      ? r['data'] : r;

**This silently affected `Fetch Access Tier` too** — same pattern — and is the more likely reason
`clicked_upgrade_from_nav_badge` had never fired, more than the 401 race. Both were real; only
this one survives a 200.

**Diagnostic lesson: a 200 in Xano request history does NOT mean the frontend consumed it.**
Check the WeWeb variable's *Current value* in the editor (Variables panel) before concluding the
data layer is healthy. The endpoint being right and the variable being right are two separate
facts.

## Xano Auth plugin internals (read from the live bundle 2026-09-10)

Plugin id `f5856798-485d-47be-b433-d43d771c64e1`, instance at `wwLib.wwPlugins.xanoAuth`.
Members: `xanoManager, _onLoad, _initAuth, storeToken, removeToken, fetchUser, login,
loginProvider, continueLoginProvider, signUp, logout, storeAuthToken, request, resolveUrl,
settings`. Endpoints live in `settings.publicData` (`getMeEndpoint`, `loginEndpoint`,
`signupEndpoint`, `afterNotSignInPageId`).

**`storeToken(token)` is the front door — writing the variable alone is NOT enough.** It does
four things: sets the auth **cookie** (1-year expiry, secure), sets `<id>-accessToken`, calls
`wwLib.wwPlugins.xano.xanoClient.setAuthToken(token)` **and** `setRealtimeAuthToken` +
`realtimeReconnect()`. Setting only `wwLib.wwVariable.updateValue('<id>-accessToken', t)` leaves
**xanoClient unauthenticated**, so every data request 401s while the variable looks correct.
`storeAuthToken({authToken})` is just a wrapper around it.

**⚠️ `fetchUser()` LOGS YOU OUT ON ANY ERROR.** Its catch is literally
`throw this.logout(), e`. So one 401 — an expired token, a blip — wipes the cookie, the token,
`<id>-user` and `<id>-isAuthenticated`, and the page guard then bounces to the sign-in page with
no way back. **Never call it speculatively.** On success it only does
`updateValue('<id>-user', data)` and `updateValue('<id>-isAuthenticated', true)`, so if you
already hold an `auth/me` response you can write those two yourself and skip it entirely.

**`_initAuth()`** reads the cookie, writes `<id>-accessToken`, then calls `fetchUser()` and
re-arms xanoClient. That is the "returning visitor" path — which also means a stale/expired
cookie logs the user out on load, by design.

**Page guards read `<id>-user` / `<id>-isAuthenticated`, not the token.** A valid token with no
user object still redirects to `settings.publicData.afterNotSignInPageId`.

Learned building [[project_admin_view_as]]; cost two failed publishes before reading the source.

## ⚠️ `createFrontendWorkflow` with an existing id APPENDS — it does not replace

Measured 2026-09-10. The tool description says "Pass an existing workflow id to replace it."
It does not. Three successive calls with the same id `d4e91b07-…` produced **three separate
live app workflows, all on `onload-app`, all sharing that id** — and all three ran on every page
load. The symptom was baffling from the outside: v3's code was correct, but v2 was still running
alongside it and its `fetchUser` call was logging out the session v3 had just built. The console
gave it away by printing two different diagnostic lines from two VM contexts.

This is the same defect behind `Fetch Access Tier` (`7e2a91c4-…`) coming back **twice with two
different bodies** from `searchFrontendAppPageFunctionWorkflows` — that is not a stale record, it
is two real duplicates.

**So: to change a workflow, DELETE it first, verify with a search that zero remain, then create.**
`deleteFrontendGlobalWorkflow` removes ONE copy per call, so call it until the search comes back
empty. Recreating under a fresh id afterwards is the safer habit.

Corollary worth remembering: **the same trigger can silently host several generations of a
workflow**, so "my new code is right but the behaviour is wrong" should prompt a duplicate check
before any further debugging.


## Repeats: `context.item.data` vs `context.item` — and two style keys that are not bindable

Found 2026-09-11 building the admin mirror console.

**A repeat bound to a WeWeb COLLECTION wraps each row as `context.item.data`. A repeat bound to a
plain ARRAY variable puts the row at `context.item` directly.** Binding `context.item.data.name`
against an array repeat does NOT error — it renders the fallback, so a real search result came out
as "(no name) · " and "id · · $0 paid · PI locked" with the row count correct. That looks like the
API returned junk, and it sends you debugging the wrong layer entirely.

**How to apply:** write the binding as `(context.item?.data ?? context.item)?.field` and read
fields off that. Survives the repeat later being repointed at a collection. Same trap applies inside element
workflows attached to elements in the repeat — and note **elements inside a repeat expose no
variables of their own**, so an input in a repeat needs an `onchange` workflow instead.

**`marginLeft` is NOT bindable** — `addSection`/`addElements` refuse it outright with "Style
marginLeft is not bindable. Bind margin instead." Bind the `margin` shorthand
(`'0px 0px 0px 260px'`). Worth assuming the other per-side properties behave the same way.

**`ww-section` comes back as `ww-div`** from `getPageElementsByUid` after creation. Not a failure.

**Conditional rendering via a bound `display` style works well** and is the idiom this project
already uses (the nav Admin Dash link gates on
`pluginVariables['<auth plugin>']?.user?.role === 'admin' ? 'flex' : 'none'`). Prefer it over
hunting for a `conditionalRendering` key.

**Do not rely on flex defaults for alignment.** A column flex parent left children looking
mysteriously offset; setting `alignItems: flex-start` on the parent and `width: 100%` +
`alignSelf: flex-start` on the cards fixed it deterministically. Also note WeWeb writes BOTH
`rowGap` and `columnGap` when you only asked for one.


## A BINDING (`__wwtype: "f"`) takes ONE EXPRESSION — a `return` statement renders NOTHING

Found 2026-09-11, immediately after the repeat fix above, and it is the more dangerous of the two.

Workflow `custom-js` actions take a full function body, so `const x = ...; return x;` is correct
there. **A style/text BINDING does not.** Rewriting a working single-expression binding as
`const it = context.item?.data ?? context.item ?? {}; return (it.name || '(no name)');` made the
text render as an EMPTY STRING — no error, no warning, and it looks exactly like "the data is
missing" rather than "the binding is malformed". It is worse than the original bug it was meant
to fix, because at least the broken-path version still rendered its fallback text.

**How to apply:** bindings get ONE expression — ternaries and `?.` and `??` are fine, `const` and
`return` are not. If an expression needs a value twice, repeat it rather than reaching for a
local. Reserve statement bodies for workflow actions.


## A REPEAT MUST BE SET WITH `editRepeat` — writing `content.default.children` by hand does nothing

Found 2026-09-11, and it cost three wrong diagnoses before the user's screenshot of the Items
panel settled it ("Current value: undefined").

Creating a repeating list by passing
`"content":{"default":{"children":{"code":"variables['…']","__wwtype":"f"}}}` to `addSection` /
`addElements` **appears to work**: the call succeeds, `getPageElementsByUid` echoes the formula
back, and the element renders one card. But WeWeb never repeats it, because the config is
incomplete — the editor's Items binding reads as EMPTY.

`editRepeat` writes the missing half:

```
before  children: { code: "variables['…']", __wwtype: "f" }
after   children: { code: "variables['…']", __wwtype: "f",
                    repeatable: [{ uid: "<child template uid>", isWwObject: true }] }
```

`repeatable` names WHICH child is the template. Without it there is data and no template, so the
list silently renders as a single static row with every per-item binding resolving to its
fallback — which reads exactly like "the API returned empty rows" and sends you to debug the
backend. The workflow returning `{ok: true, count: 1}` while the page shows a blank row is the
signature of this bug.

**How to apply:** build the element tree first, then call `editRepeat(uid, slotKey, value)` for
every repeat. Verify with `getPageElementsByUid` and assert `repeatable` is present — the formula
alone is not evidence.

## `editElement` silently no-ops on a REWRITE of a nested path — 2026-09-14

Confirmed on three `/pricing` text elements. Setting `style.default.conditionalRendering`
from a plain boolean to a formula object **worked**. Every subsequent write to that same
nested path — including setting it to `null` — returned `"Element updated."` and changed
**nothing**. Re-reading with `getPageElementsByUid` showed the original value intact.

**The workaround: write the WHOLE parent object.** `path: "style.default"` with the entire
style object (every existing key reproduced verbatim, plus the changed one) applies
correctly. Read the element first and copy `style.default` exactly, or you will silently
drop styles. Sibling breakpoint keys (`style.mobile`, `style.tablet`) are untouched by a
`style.default` write, so mobile overrides survive.

WeWeb also NORMALIZES what you write: a `{"__wwtype":"js","code":"...with comments..."}`
formula came back as `{"__wwtype":"f","code":"<single expression>"}` with the comments
stripped. **Do not put load-bearing explanation in a binding formula's comments** — it is
discarded. Put it in the workflow that feeds the binding instead.

So the rule is: **always re-read after `editElement` and diff the field you meant to change.**
"Element updated." is not evidence the write landed. Same family as the `customCss` `{}`
no-op and the `initValueSingle` no-op already recorded above.

## addElements SCHEMA — learned the hard way 2026-09-15

Two non-obvious rules, each of which fails the whole call with a Zod error:

1. **`uid` is REQUIRED on every element** and you supply it yourself (any UUID-shaped string).
   The server then REMAPS it to its own uid, so **read the returned semantic to learn the real
   uids** — the ones you sent are not what ends up on the page.
2. **`slots` is an ARRAY, not an object**, and `elements` inside it holds **UID STRINGS, not
   nested element objects**: `"slots":[{"slotKey":"children","elements":["<uid>","<uid>"]}]`.

So the payload is **FLAT JSONL — one complete element per line** — with the tree expressed purely
by uid references. Nesting objects inside `slots` is rejected. The write shape is the same shape
`getPageElementsByUid` returns.

Other things that worked first time and are worth copying:
- Bindings anywhere: `{"code":"...","__wwtype":"js","defaultValue":<fallback>}` — valid on style
  properties (`backgroundColor`, `color`, `conditionalRendering`) and on `content.default.text.en`.
- **Never bind `content.default.text` itself** (it is an object, binding it throws) — bind the
  per-language value `content.default.text.en`.
- Links: `"link":{"type":"internal","pageId":"<pageId>","targetBlank":false}` as a TOP-LEVEL key
  on the element, sibling to `content`/`style`.
- Hover: `"states":[{"id":"_wwHover","label":"hover"}]` plus a `"_wwHover_default":{...}` block
  inside `style`.
- **Always `checkIconNames` first** — partial icon names silently do not ship to the published app.

## ELEMENT WORKFLOWS: editElement CANNOT touch them — use createFrontendWorkflow

`editElement` accepts only `name, style, content, states, classes, subClasses, link, attributes`.
**`workflows` and `slots` are both rejected.** The routes that do work:
- **Element workflows** -> `createFrontendWorkflow` with `scope:"element"` + `elementUid` + `pageId`
  + `trigger`. **Passing an EXISTING workflow id replaces that workflow in place** — the clean way
  to rewrite a click handler without leaving a duplicate behind.
- **Repeat bindings** -> `editRepeat` (uid + slotKey + binding), not `editElement`.

**DUPLICATE WORKFLOW IDS ARE REAL IN THIS PROJECT.** On Vendor Discovery, the State chip
(`61d551ea`) and the Pricing Model chip (`ecd99a60`) both carried workflow id
`75a70072-2853-4982-9160-fa767de017f3` with **different action bodies**. Same class of defect as
the access-tier workflow registered twice under one uid. Because `createFrontendWorkflow` targets
by `elementUid`, each can be rewritten independently — but **never assume a workflow id identifies
one workflow.** Check the element it hangs off.

## Vendor Discovery filter chips — how they actually work (2026-09-15)

Chips are NOT a repeater: eight hand-built divs in `7b2d2b88` "Filter Chips Scroll Area", each
conditionally rendered off its own filter variable. Each chip = a `Chip Label` (bound text) plus a
`Remove Filter Icon`.

**The X icons had NO workflow of their own** — removal lived on the chip DIV and the X only
"worked" by event bubbling, while the chip was styled `cursor: default`. So clicking anywhere on a
chip silently deleted that filter with no affordance. Fixed 2026-09-15: removal moved onto each X,
chip body now calls the global Toggle Filter Sidebar function
(`execute-workflow:0bdac5a3-92ad-4270-9128-ec41705eded4`, the same one the Filters pill uses), and
all eight chips got `cursor: pointer`.

**Chip labels were formulas like `"Venue Types: " + variables[...]`.** When that variable is an
ARRAY, JS joins it with commas — which is why one chip rendered ~110 characters wide and forced a
horizontal scrollbar. Now each shows `first value + "  +N"`.

Guest Capacity's remove action is `return variables[...] == NaN`, which is ALWAYS false in JS; it
writes `false`, and the chip's conditional hides on `false == 0`. It works by accident. Replicated
verbatim rather than "fixed" — do not tidy it without checking the conditional.

### CORRECTION same day: do NOT make those chip strips `flex-wrap: wrap`

First attempt fixed the scrollbars by switching both chip strips to wrap. **That broke the
layout.** Both strips live inside the Vendor Discovery toolbar, whose `Right Hand Side`
(`6b5a45e4`) is `min-height: 50px` with `align-items: center`, and `Hub Filter` (`6e79d342`) is
centre-aligned too. A strip that wraps to three lines therefore grows vertically and CENTRES
itself against single-line siblings, bleeding over the row above and below. Verified on screen.

Both strips are back to `flex-wrap: nowrap; overflow-x: auto` — the original mechanic. **The real
fix was never the overflow rule, it was the chip LABELS**: one chip was ~110 characters because a
formula concatenated an array. With `first value + "+N"` labels the chips fit and no scrollbar
appears. The Quick Link row is additionally sliced to 6, so its scrollbar only returns if the user
opts in via "+15 more".

**Rule of thumb for this toolbar: never introduce vertical growth inside it.** Shorten content or
scroll horizontally; wrapping silently overlaps neighbouring rows.

### customCss FLATTENING also fires when you edit a SIBLING style property (2026-09-15)

The known hazard is broader than pasting into `customCss`. Editing an UNRELATED property in the
same `style.<breakpoint>` block can flatten a JS binding that lives inside that breakpoint's
`customCss`.

Observed on the Vendor Discovery map (`43235d7f`). Editing only `style.default.overflow`:

```
before:  customCss: { "aspect-ratio":"unset",
                      "conditional-rendering": {code:"return (wwFormulas.not(...))", __wwtype:"js", defaultValue:true} }
after:   customCss: { "aspect-ratio":"unset",
                      "conditional-rendering": "true" }
```

The binding was evaluated once and written back as the literal string `"true"`. The `mobile`
breakpoint, which was edited in the same call, kept its binding intact — so it is not reliably
reproducible, which makes it worse, not better.

Harmless in this instance: `conditional-rendering` is NOT a real CSS property, so it was always
inert; the element's real gate lives in `style.default.conditionalRendering` and was untouched.
**But always re-read an element after editing any style property and diff its `customCss`** — on
an element where customCss holds a live binding this would silently freeze it at one value.
- **addElements ignores `parentUid`/`slotKey` inside JSONL lines** (2026-10-02): child lines landed as SIBLINGS in
  the call's parent. Add the container first, then a second addElements with parentUid = the container's NEW uid
  (WeWeb reassigns uids). Read the parent's slots back after every add.


## Slot/children edits (LEARNED 2026-10-03, /recommendations cleanup)
- deleteElement removes the element but can leave its uid in the PARENT's children list -> editor shows
  "Element undefined (click for more info)". Calling deleteElement on the dangling uid again "succeeds" but
  does not clear it.
- editElement `content.default.children` with an array MERGES BY INDEX (old tail entries survive).
- Setting `content.default.children` to null DETACHES all children (they become orphans - still fetchable by
  uid, not deleted). Re-attach with per-index paths `content.default.children.0`, `.1`, ... which works.
- Fix recipe for dangling refs: null the list, then set each index explicitly; re-read to verify.

- **TRAP (10-20):** a WeWeb change-variable value written as a JS expression like `({ tour: 'x', at: Date.now() })` runs as a FUNCTION
  BODY - without `return` it saves undefined, silently. Always `return ...` in multi-token JS formulas. (Broke tour replay 10-20.)
- tulle-tour forceOpen IS reactive after mount (verified in source 10-20).
- Hardcoded Slack webhook URLs found in browser-shipped WeWeb workflows 10-20: Add Vendor popup save step, and Discovery Vendor Card 1
  click workflow b7cfba6f -> add to security lockdown ([[project_security_lockdown_2026-09-29]]).

**2026-10-24 TRAP:** `editElement` can return "Element updated" WITHOUT saving when several edits are sent in
parallel (4 parallel edits -> only 1 saved). Send edits ONE AT A TIME and read back every element. Library-component
children can't be read via getPageElementsByUid (PAGE_NOT_FOUND) - backups of component internals are text-only.

**2026-10-24 TRAP 2:** editElement with path `content.default.text` and value `{en: "..."}` returns "Element updated" but
DOES NOT SAVE. Use path `content.default.text.en` with a plain string. The 10-24 popup title edit (70816f9e) silently failed
this way even though an agent reported a read-back. Verify text edits by reading the exact string back, not the call result.

**2026-10-31 — element workflows on a coded (github) component's custom events.** `createFrontendWorkflow`
(scope element) validates `trigger` against the triggers the AI server knows for that element: DOM events
plus the `events[]` of the component's **AI.json**. No AI.json in the component repo = only
`[click, dblclick, contextmenu, mousedown, mouseup, mousemove, mouseenter, mouseleave]` and every custom
event is rejected with `[INCORRECT_TRIGGERS]`. Proven on nps-survey (has AI.json: its 5 events listed) vs
ww-planner-chat (no AI.json: DOM only). Fix = add AI.json (events mirror ww-config triggerEvents), push,
let WeWeb build/activate, then create the workflows with the event name as `trigger`. Probe for the known
list without creating anything: pass a bogus trigger; the error lists the allowed ones.

- **2026-11-01 FORMULA GOTCHA (cost ~2 h of 'padding is off' on the planner page):** a binding with `__wwtype: "f"` is a single EXPRESSION. A leading `// comment` line makes it invalid and WeWeb silently falls back to `defaultValue` (or nothing). Comments are only allowed in `__wwtype: "js"` bindings, which need a `return`. Symptoms seen: Planning Shelf flex, Shelf Body padding, Chat Header Bar borderBottom and the chat's startMessage all 'ignored'. Never put comments in `f` formulas; use `js` + return when a note is needed.

- **2026-11-01 editElement ARRAY GOTCHA (tours fired the old Budget step twice):** writing a SHORTER array to a content path (e.g. `content.default.steps` 5 -> 4 items) MERGES by index and leaves the old trailing items in place; `steps[4] = null` then wiped EVERY item to null, and `steps[1].clickOnDone` nulled item 0. Reliable recipe: two edits in ONE call - `{path: 'content.default.steps', value: null}` then `{path: 'content.default.steps', value: [full array]}` - and always read the element back afterwards.

**UPDATE 2026-11-14: editFrontendWorkflow DID accept a workflow containing Xano `request` + `fetchUser` plugin actions** (Profile page Submit Form d320efcd, sent the FULL action map; re-read confirmed intact). The validator now only demanded change-page root fields (openInNewTab, loadProgress, loadProgressColor). Still send the full map and re-read after. Element workflows on ww-mapbox (trigger map:load) are still rejected (trigger list only has click/mouse events), and editElement cannot touch `workflows`.
