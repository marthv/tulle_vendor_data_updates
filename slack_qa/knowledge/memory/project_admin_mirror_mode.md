---
name: project_admin_mirror_mode
description: Admin mirror mode was REMOVED ENTIRELY on 2026-09-19 - do not rebuild it without asking
metadata:
  type: project
---

# REMOVED IN FULL — 2026-09-19. Do not rebuild without asking.

The user asked to revert mirror mode completely. It is gone from Xano AND WeWeb.
Everything below the divider is the historical design record, kept only so the decisions
are not lost. **None of it is live.**

## What was deleted
- **Xano endpoints:** ep303 `admin/mirror/status`, ep304 `start`, ep305 `stop`,
  ep306 `search` (all group 4). All four now 404.
- **Xano functions:** fn68 `mirror_target` and fn67 `imp_write_blocked` — both deleted
  after all callers were unwired.
- **user table columns:** `mirror_of_user_id`, `mirror_started_at`, `mirror_expires_at`
  (50 -> 47 columns). Used `deleteTableSchemaElement`, NOT `updateTableSchema` — verified
  afterwards that every surviving column is byte-identical and the password/email
  validators survived. **That is the safe way to drop a column on table 1.**
- **15 endpoints unwired** (mirror_target and/or imp_write_blocked calls removed, guarded
  writes unwrapped back to unconditional): ep3 auth/me, ep20, ep21, ep66, ep72, ep87,
  ep109, ep145, ep150, ep158, ep212, ep216, ep230, ep284, ep285.
  Every guarded write had an `else` branch that existed only to fake the response shape
  while blocked — those went too, which restores the pre-mirror behaviour exactly.
- **WeWeb:** app workflow `Admin Mirror Banner` (7c4e1a90, ran on EVERY page load), page
  workflow `Admin - Load Mirror Status` (4e54b0c7), 3 element workflows, section
  `888b703b` Admin Console Section, and variables adminMirrorResults / adminMirrorMessage
  / adminMirrorStatus.

## What deliberately stayed
- **The /admin page shell** (`d1e1d814`, path `admin`) — **the WeWeb MCP has no
  deletePage tool**, so the page had to be emptied instead. Delete it by hand in the
  editor if you still want it gone.
- The two nav entries that link to it (`e5660b39` "Nav Admin Dash" and dropdown item
  `b44eb6d7`). They pre-date mirror mode and were already broken.
- **The OLD impersonation feature is still there**: ep297 `admin/impersonate/mint`
  (group 3, disabled), ep298 `admin/impersonate/exchange` (group 4), and the
  `impersonation_session` table. Separate abandoned feature — see [[project_admin_view_as]].
  **WARNING: deleting fn67 removed the write-guard those endpoints relied on.** ep297 is
  disabled so nothing can mint a token today, but re-enabling impersonation without
  rebuilding a guard would let an impersonated session write to the customer's row.

## Rollback
Full pre-removal scripts: `.claude/xano_backups/mirror_removal_2026-09-19/` (21 endpoint
scripts + both functions + the table-1 schema JSON), patched versions in its `patched/`
subfolder. WeWeb side, including every custom-js body verbatim:
`.claude/weweb_backups/mirror_mode_pre_removal_2026-09-19.md`.

## Verification status (per rule 12.1)
- **VERIFIED:** all four mirror endpoints 404; no `function.run mirror_target` or
  `function.run imp_write_blocked` anywhere in all 248 workspace items; mirror columns
  gone with no validator drift; WeWeb variable/workflow searches return nothing.
- **UNVERIFIED AT RUNTIME:** the 15 patched endpoints were confirmed only by fetching the
  live script back (parses, braces balanced, no mirror refs). The smoke tests could not
  run because the available user token had expired ("This token is expired"), and the
  write endpoints (ep20 delete-account, ep72 password, ep109, ep150, ep158, ep21) were
  deliberately NOT exercised against production data. Load the app with a fresh login and
  check `searchApiRequestHistory` for ep3/ep109/ep150 before calling this fully proven.

---

# HISTORICAL RECORD (pre-2026-09-19, no longer live)

**This replaces the abandoned in-browser view-as.** The admin signs in normally and STAYS signed
in; a pointer on their OWN user row makes the read endpoints serve a customer's data. The router
never sees anything but an ordinary authenticated session, so the boot race that killed
ep297/ep298 cannot happen. See [[project_admin_view_as]] for why the token approach is dead.

## Why not the two obvious alternatives

- **Magic links are NOT a way in.** `/update-password`'s only onload workflow parks `?token=` in a
  variable; the exchange (ep18 `auth/magic-login`) runs *inside the submit button's workflow, next
  to the password write*. The only way to get a session from a magic link is to actually reset the
  customer's password and lock them out. Do not revisit this.
- **Copying attributes onto your own row (the user's first instinct) does not work** — fn63 walks
  the **payment log** by `Client_Reference_ID`, not the user row, so the upgrade banner could only
  be reproduced by fabricating payment rows. And every "reset" would be a destructive write on
  your own account.

## The design

`user` table gained **`mirror_of_user_id`, `mirror_started_at`, `mirror_expires_at`** (all int;
epoch ms, because `timestamp_add_*` only resolves inside a db.query where-clause). Added via
`updateTable` XanoScript — **never `updateTableSchema`, which strips every validator on table 1**.

**`fn 68 mirror_target()`** is the single source of truth: returns the target row when the caller
is `role == "admin"`, has a pointer, and it has not expired; null otherwise. **Expiry is enforced
on READ**, so nothing has to run on a schedule. Both db.gets carry output allowlists, so
credentials can never reach a response.

**`fn 67 imp_write_blocked` now ALSO returns true while mirroring**, which switched on all ten
existing guards at once. Cost: one extra indexed db.get per authenticated write.

**IDENTITY IS NOT MIRRORED — this is the load-bearing decision.** `auth/me` and `user/{id}` serve
the target's entitlement and profile but overwrite `id`, `email` and `role` back to the admin's:
1. The browser never learns the target's id, so an endpoint that trusts a client-supplied
   `user_id` cannot be aimed at them — **41 of 51 authed writers have never been audited for that
   bug**.
2. `role` stays `admin`, so the nav and the **Stop button cannot vanish mid-session**.
3. `$auth.id` is unchanged regardless, so every writer keyed on it hits the ADMIN's row. **The
   customer is untouchable by construction** — the opposite of impersonation, where stray writes
   landed on the customer.

## What exists (all LIVE in Xano)

- eps **303** status / **304** start / **305** stop / **306** search, group 4, `auth=user`.
  **First authorisation-by-identity in the workspace** — ep297/ep299 check a shared secret, which
  authenticates knowledge, not identity.
- Routed through `mirror_target`: **ep3 auth/me** (group 1), **ep66 user/{user_id}**, **ep284
  upgrade_offer**, **ep216 my_access_summary**, **ep230** (group **10**, not 4).
- **ep65 was deliberately left un-mirrored** — it returns a LIST so identity-restore is awkward,
  and it has zero production calls.
- **ep87 userpayment finally got the write guard** it was missed for on 2026-09-10. It writes
  `date_until_access`/`total_payments`/`total_amount_paid` and had none.
- Audit rows go to **admin_audit (table 72)**, actions `mirror_start` / `mirror_stop`.
- Admins are `role = "admin"` on table 1: **14998, 2333, 5622, 118**.
  `[email]` has NO account — 118 `[email]` is Des's real one.

## Verified end-to-end 2026-09-11 (real HTTP, real token)

Mirroring 13023 → auth/me returns her name/access/$190 with **my** id+email+role; upgrade banner
**$30 credit → $99**; access badge timed 8 days; PI unlocked. Mirroring 29112 (Des's test acct,
$32 paid) → **$32.66 → $96.34** and **PI LOCKED**, because the gate is `total_amount_paid >= 50`.
Writes: `userfreeviews` silently skipped (sent 999, row stayed -1), `upgrade_session` hard-refused,
**ep87 sent `date_until_access: "3000-01-01"` + `$500` and changed nothing**. Nesting refused,
stop idempotent, own row byte-identical afterwards.

## WeWeb — the console is REAL ELEMENTS (v5, 2026-09-11), not injected

The first build injected the whole console with JS. That works but is **invisible and uneditable
in the WeWeb editor**, which the user rightly rejected — they have to be able to change it
themselves. Rebuilt as native elements on the `/admin` page:

- **Section `888b703b-f067-42d2-a8de-d12c57c76314`** on page `d1e1d814-…`, after the nav section.
  Its `margin` is bound to the sidebar state — **`marginLeft` is NOT bindable in WeWeb, bind
  `margin` instead** (the tool rejects it outright).
- Cards gate on `adminMirrorStatus.is_admin` + `.mirroring` via **`display` style bindings**, the
  same idiom the nav Admin Dash link already used. Key elements: console card
  `0bb33e93`, mirroring card `5eaf14f1`, search input `773fc469`, Search button `b1a36d20`,
  results repeat `a79b3deb`, "View as" button `31a251a2`, Stop button `72aac91d`.
- Variables (folder `Admin`): `adminMirrorStatus` `136333a8-…`, `adminMirrorResults`
  `c9bb1ff2-…`, `adminMirrorMessage` `52c35ebf-…`. The search input's value is read from its OWN
  exposed `…-value` path — never mirrored into a global.
- Workflows, all `custom-js` so they stay MCP-editable: page onload `Admin - Load Mirror Status`,
  and element clicks `Admin - Search Customers` / `Admin - Start Mirroring` / `Admin - Stop
  Mirroring`. The "View as" button is INSIDE the repeat, so it reads `context.item.data.id` —
  elements in a repeat expose no variables of their own.

**The app-level workflow is now ONLY the banner** (`7c4e1a90-…`, renamed `Admin Mirror Banner`).
The floating launcher was deleted as redundant. The banner stays injected because it must appear
on every page and the alternative is editing the reused nav section that 11 pages share.

## Original injected build (superseded)

App workflow **`Admin Mirror Console` `7c4e1a90-3b6d-4f52-9a18-2e5d7c081f44`**, trigger
`onload-app`, folder `Admin/`, ONE `custom-js` action (no plugin actions, so it stays
MCP-editable). Draws the MIRRORING banner + Stop, and a "View as…" launcher with search.
**Costs ordinary customers ZERO network**: it polls client-side for the plugin user object and
returns unless `role === 'admin'`.

**The banner reads `admin/mirror/status`, never `user.role`** — while mirroring the rendered user
object is the target's and would lie.

**`Admin View-As Session` `c9e21a54` was DELETED** — it swapped the session token wholesale and
would have fought the Stop button. `Auth Token Early Restore` `f1a6b93d` was KEPT.

**To edit the console workflow: DELETE IT FIRST — `createFrontendWorkflow` APPENDS on an existing
id** (that is how three live duplicates happened once before).

### The nav "Admin Dash" link was a DEAD LINK, now fixed
It pointed at page `04737e17-b522-4e86-a73b-bb38520d8474`, **which does not exist in the project**,
so clicking it fell through to the app root — the Sign In page (whose path is literally `home`).
Nobody had ever noticed because the link is gated on `role === 'admin'` and **no account had that
role until 2026-09-11**; setting the flag is what made the dead link visible.

Fixed by creating page **`Admin Dash` `d1e1d814-2d90-475d-9406-b95b4e5913b1`, path `/admin`**
(hidden from sitemap, not prerendered) and repointing the link. The nav is a REUSED SECTION, so
that single `editElement` covered all 10 pages.

**THERE ARE TWO "Admin Dash" ENTRIES and both pointed at the dead page.** The sidebar item
`e5660b39-…` AND a profile-dropdown item `b44eb6d7-9971-48de-b3fb-ef8457472caf`. Both links are
now repointed. **The dropdown item's `click` workflow still references `04737e17-…`** — element
workflows are not reachable through `searchFrontendAppPageFunctionWorkflows` (it only covers
app/page/function scopes), so that one has to be checked by hand in the editor.

The page carries the **real reused Navigation Bar Section** `de36599e-…` (added 2026-09-11, so it
is now on 11 pages), because an admin surface you cannot navigate away from is useless. The
console is still injected by the workflow rather than built from WeWeb elements, so it has to
reproduce the sidebar offset itself: **260px expanded / 80px collapsed / none below 769px**, read
from `isSidebarVisible` `16c410c4-…` and `isSidebarExpanded` `2e03a506-…`, re-applied on resize
and on a 750ms interval (the sidebar toggle is a WeWeb workflow with no hook). A non-admin who
guesses the URL gets a plain "no admin access" message rather than a white screen.

**PREVIEW COUNTS AS `editor` — CONFIRMED 2026-09-11** (the user hit a blank /admin in Preview
while the nav rendered fine, because the nav is real elements and the console is injected). The
editor guard inherited from the old view-as workflow has therefore been **REMOVED** in v4. It is
safe to drop here: that guard existed because view-as called `storeToken` and would have swapped
the EDITOR's own session; this workflow only reads status and injects DOM.

**Xano's CORS allowlist includes `https://editor.weweb.io`** — verified by sending Origin headers
at `auth/me`: `www.tulletogether.app` and `editor.weweb.io` are echoed back, `preview.weweb.io`
and `example.com` are refused. That is why the console's fetches work in Preview at all, and it
is worth knowing before assuming any other origin can call Xano.

## Gotchas found here

- **A token minted by `security.create_auth_token` inside `runWorkspaceFunction` is REJECTED by
  the API runtime** ("Invalid token"). Mint through a real HTTP endpoint instead. This is also why
  `$auth.id` functions cannot be tested with `runWorkspaceFunction` at all — it returns
  `ERROR_CODE_ACCESS_DENIED`.
- **There is no `user/me` endpoint.** `/user/me` resolves to `user/{user_id}` (ep66) and errors
  with "Value is not a valid integer". ep66 already keys on `$auth.id`.
- Empty HTTP responses with no headers during testing were transient network blips, not code
  faults — the same class as the `ENOTFOUND` flapping on both MCP servers that day.

## Still open

- ~~The WeWeb publish.~~ **DONE 2026-09-12.** `https://www.tulletogether.app/admin/` now 200s and
  serves the real page (`<title>Admin - Tulle Together</title>`); it was a hard 404 before.
  For future triage: an unpublished route makes the nav link do **nothing at all** — no
  navigation, no error. Check the route's status code before debugging the link or the workflow.
  Note WeWeb 301s `/admin` → `/admin/`, and a page with an empty `title` inherits the app default
  ("Tulle Together: Sign In"), so an explicit title is the reliable signal a route really resolved.
- **Not yet verified in production: the console rendering for a signed-in admin** — the route is
  live, but the mirror panel itself needs a real admin session to confirm.
- ~20 authenticated writers on user-owned CHILD data (roadmap orders, referrals, to-dos, packages,
  venue-calculator entries, feedback, uploads) still carry no guard. Deliberately out of scope.
- Nothing from the dangling-link problem — fixed, see above.

Related: [[project_admin_view_as]], [[project_upgrade_credit]], [[reference_xano_mcp_quirks]],
[[reference_access_entitlement_model]], [[reference_weweb_mcp_limits]].


## "Will mirroring dump me on Sign In like view-as did?" — NO, and here is why

Asked 2026-09-11. **The router race cannot happen in mirror mode.** It was caused by SWAPPING the
session for a freshly minted token: the next boot had to restore a token it had just been handed,
and the router evaluated page access before that async restore resolved. Mirror mode mints,
stores and swaps NOTHING — your own cookie and `$auth.id` are untouched end to end, and the
reload after `start` is an ordinary authenticated refresh. If that raced fatally, every returning
customer would hit Sign In on every F5.

The one path that COULD sign you out is `auth/me` erroring, because the Xano Auth plugin's
`fetchUser` catch is `throw this.logout(), e` — it destroys the cookie. Verified 200 while
mirroring (13023, 118, 29112) and production traffic on ep3 is all 200s.

**`/` IS THE SIGN IN PAGE.** Its path in this app is literally `home` (My Wedding is
`home-page`). That is why the dangling Admin Dash link "redirected to Sign In" — it was never a
router race, just an unresolvable route falling through to the app root. **Never redirect an
authenticated flow to `/`**: the Start-mirroring workflow now lands on `/home-page`, and Stop
lands on `/admin`. Landing a fresh mirror session on `/` would look exactly like the abandoned
view-as bug and send the next person down a week-long rabbit hole.

## FAVOURITES WHILE MIRRORING — traced end to end 2026-09-13 (code read, not a live click)

Question asked: "if I mirror someone and unfavourite, am I writing to their favourites?" **No**, and
there are two independent reasons — either alone would be sufficient.

**ep109 `user/update_user_attributes` is the live favourites writer** — 127 PATCHes in 24h; it is
the only one. (ep12's `Is_Favorited` is a column on "WPTP - Pending Uploads", unrelated. ep158 is
still at zero calls.)

1. **Identity is not mirrored, so the customer's id is never in the write path.** ep109's `db.get`
   and `db.edit` both use `field_value = $auth.id`, which under mirror mode is still the ADMIN's.
   **Worth knowing: the client DOES send `id: <number>` in the PATCH body and ep109 IGNORES it
   entirely** — so ep109 is clean on the `$input`-id class of bug that the 41 unaudited writers
   may not be.
2. **The write is skipped regardless.** `function.run imp_write_blocked` (fn 67) then
   `if ($imp_blocked == false)`. fn67 returns true when the caller's own row has a live unexpired
   `mirror_of_user_id`. The else branch re-reads the admin's own row with an identical 18-field
   output list, so the response shape is preserved and the row is byte-identical.

**ep158 `user/toggle_favorite` carries the same guard**, so pointing the WeWeb hearts at it later
does not regress this. ep158 is better behaved: its blocked branch returns the UNCHANGED
`is_favorited`, whereas ep109 lets WeWeb's optimistic local update flip the heart until reload.

**The real hazard is the REVERSE of the one feared**, and it is why fn67 covers mirror mode at
all: without the guard the mirrored UI would PATCH the CUSTOMER'S favourites array onto the
ADMIN'S OWN row. fn67's header: "five minutes of mirroring quietly rewrites your own account to
look like theirs."

**UNVERIFIED:** whether the mirrored Saved Vendors view is self-consistent. ep3 auth/me is
mirrored but **ep74 `wptp_updated_mappings_favorites` is NOT in the mirrored list** (ep3, ep66,
ep284, ep216, ep230), so the grid may resolve against the admin's own favourites. Data-safe
either way; possibly confusing. Not checked.
