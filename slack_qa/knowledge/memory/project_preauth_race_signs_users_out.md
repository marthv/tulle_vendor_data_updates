---
name: project-preauth-race-signs-users-out
description: "The pre-auth race does not just cause stale access state — it FORCIBLY SIGNS USERS OUT, because the Xano Auth plugin's fetchUser calls logout() on any error. Diagnosed 2026-09-10 from Des's mobile report."
metadata:
  type: project
---

**Symptom (Des, 2026-09-10 ~11:23 UTC, iPhone):** "vendors that are not venues are loading
really slow / not opening at all", "any time I try another vendor it's like crashing and just
signing me out", "made another account, same issue, on my 3rd account it was fine".

**Mechanism, now fully understood.** Two known facts combine into a much worse third:

1. The pre-auth race — WeWeb fires authenticated calls on app load **before** the token is
   restored ([[project_preauth_401_fix]], [[project_endpoint_lockdown_401s]]).
2. The Xano Auth plugin's `fetchUser()` catch is literally `throw this.logout(), e`
   ([[reference_weweb_mcp_limits]]).

So when the `Open Channel` app-load workflow (`1cbf6dd7-…`, contains a `-fetchUser` plugin
action) loses that race, `auth/me` goes out **with no Authorization header at all**, returns
401, and fetchUser's catch **destroys the session — cookie included**. `_initAuth` then has
nothing left to restore from, so it cannot self-heal. That is the "crashing and signing me out".
It is a race, which is exactly why a third account worked: nothing account-specific.

**Evidence (request history, Des's window 11:23-11:43 UTC):**
- `auth/me` (ep3): 6 of 51 calls 401 — and **every single 401 had `authHeader=False`**. The
  200s all had it. So it is unambiguously "sent before the token existed", not a bad token.
- `ep119` Vendor Discovery search: 25 of 71 calls 401 — **but 23 of those 25 came from the
  internal IP `10.0.224.169` with NO User-Agent, i.e. the DEAD ep119 CACHE WARMER (task 8),
  not users at all** ([[project_endpoint_lockdown_401s]] — the user deliberately left it dead).
  Only **2** were real users. ⚠️ I initially reported this 35% as user-facing "vendors not
  opening"; that was WRONG. Always split 401s by client IP / User-Agent before attributing
  them to users — an internal IP with no UA is a background job.
- `my_access_summary` 6/16 401, `admin_choices` 4/18, `favorites` (ep74) 5/37.

**NOT caused by the 2026-09-10 view-as guards.** Zero 5xx anywhere across three sampled windows;
`auth/me` averaged 0.08-0.12s; ep150/ep109/ep21 returned only 200s. And structurally a 401 is
rejected at the auth layer *before* the function stack runs, so a guard inside the stack cannot
produce one.

**Non-venue slowness is NOT server-side:** ep149 benchmarks 0.26s avg, ep207 pe_benchmarks
0.25s, ep142/ep93/ep41 all under 0.25s. The only genuinely slow endpoint is ep199 (9.8s avg)
and that is the Streamlit dashboard, not the app. ep119 is the one user-facing endpoint that is
slow-ish: p95 2.85s, max 4.33s.

**The fix** is to stop `fetchUser` ever running unauthenticated: give `Open Channel` the same
`a0_wait_for_token` poll that `Fetch Access Tier` and `Fetch Upgrade Offer (App)` already have.
⚠️ **Editor-only** — that workflow contains plugin actions the MCP cannot rewrite. Needs a publish.

This reframes the earlier decision to leave the ep119 pre-auth 401s alone: the cost is not just
a failed search, it is a logged-out customer.

## The second half: ~24 AUTO-FETCH COLLECTIONS race the same restore

`getCollections` (2026-09-10) shows **~24 collections with `isAutoFetch: true`**, every one binding
its auth header to `pluginVariables['f5856798-…']['accessToken']`. They fire as the page loads,
before the plugin restores that variable, so the header goes out empty.

Maps exactly onto the measured 401s: **ep119** ← `Onboarding_Recommendation` + `Similar_Vendors`;
**ep74** ← `Favorited Vendors`; **ep34** ← `Categories` + `ADMIN - Choices` + `ADMIN - Guest Count
Adjustment`; **ep82** ← `Payment Log` + `Pull Payments`.

**A vendor page auto-fetches EIGHT at once** — `Get Vendor Data`, `Similar_Vendors`, `Benchmarks`,
`PE Benchmarks`, `Extracted Data`, `PDF Specific`, `getgoogledata`, `Pricing_Intelligence` — which
is why losing the race looks like "the vendor page will not open". Note `Search Vendors` (the main
grid) is `isAutoFetch: false` and fired by a workflow, which is why the main search mostly works
while the similar-vendors rail and onboarding rail fail. **Timeline test: 23 of 25 ep119 401s were
NOT within 120s of a logout — so they are independent of the sign-out bug, not downstream of it.**

**RESULT after both fixes shipped (2026-09-10 15:27 UTC).** An initial 9-minute sample looked
like a clean sweep (auth/me 0/17); **that was a small-sample artifact.** Re-measured over 80
minutes / 710 requests, excluding the cache warmer:

| endpoint | before | after (80 min) | verdict |
|---|---|---|---|
| `auth/me` (ep3) | 6/51 = 11.8% | **3/66 = 4.5%** | genuinely improved ~2.6x |
| ep74 favorites | 5/37 = 13.5% | 8/57 = 14.0% | **unchanged** |
| ep34 admin_choices | 4/18 | 7/19 | no better |
| ep216 access_summary | 6/16 | 9/17 | no better |
| ep82 payments | 2/17 | 5/11 | no better |
| ep119 (real users) | 2/48 | 5/77 | no better |

**So: the `Open Channel` token-wait WORKS (it is what fixed the sign-outs, and Des confirmed
the symptom went away). The `Auth Token Early Restore` shim does NOT demonstrably help the
collections** — ep74 is the cleanest test (an auto-fetch collection, decent sample both sides)
and it is flat. The shim is presumably still losing the race to collection auto-fetch, or is
hitting one of its own skip branches.

**Diagnose before iterating:** the shim logs one of `[auth-early] token restored from cookie: X`
/ `skipped: token already present` / `skipped: no auth cookie`. That line says which branch ran
and therefore whether the problem is ordering or cookie visibility (e.g. HttpOnly would make
`document.cookie` blind to it). Do not rewrite the shim before reading it.

Note one remaining auth/me 401 DID carry an Authorization header — that is a genuinely stale
token, a different and legitimate case, not the race.

**Mitigation built 2026-09-10:** app workflow `Auth Token Early Restore`
(`f1a6b93d-47c2-4e58-b0d1-2c9a7e5f8033`, onload-app, pure custom-js, MCP-editable). It finds the
auth cookie **by token shape** — `/^eyJ[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){4}$/`, a 5-segment JWE —
because the cookie NAME is a minified constant (`F5`) that a bundle rebuild would change. It then
writes `<id>-accessToken` and `xanoClient.setAuthToken` **synchronously**, before any await.
It sets the token ONLY — never `user`/`isAuthenticated`, which must follow a validated `auth/me`.

⚠️ **WeWeb does not guarantee ordering between app-load workflows and collection auto-fetch**, so
this is a strong mitigation, not a proof. If 401s persist after it ships, the certain fix is
turning `isAutoFetch` off on the affected collections and firing them from a workflow after the
token is ready — **editor-only, the MCP cannot edit collections**.

Do NOT "fix" this by removing auth from ep119 — the user already ruled that out
([[project_endpoint_lockdown_401s]]).

## STOPPED HERE 2026-09-10 — the unfinished lead, so it is not re-derived

**`before-collection-fetch` IS a real WeWeb trigger** (not listed by
`getAppWorkflowTriggersContext`, but the existing app workflow `Fetch Favorite`
`b07e99af-9ace-4287-8737-61df0b87a5fc` uses it, `version: 2`). That is the hook an app-load
shim cannot get: it fires BEFORE collections fetch, so a token gate there would be
ordering-proof instead of a race.

**UNKNOWN, and it decides everything:** whether that trigger actually BLOCKS the fetch until the
workflow resolves, or merely fires alongside it. Not determinable from the API — needs a live test.

**Mandatory design constraint if anyone builds it:** the gate must check for an auth cookie
FIRST and skip waiting entirely when there is none. Otherwise every signed-out visitor waits the
full timeout before every collection fetch, which would make the app far slower for anonymous
traffic than the bug it fixes.

**Proven fallback, already used in this app:** the page workflow `Collection Fetch`
(`f3524b75-eec7-4457-95ba-7c3baf3360b1`) fetches five collections from a workflow with
`isFetched` guards. So "turn `isAutoFetch` off, fire from a workflow after a token wait" is an
established pattern here. Worst four offenders: `Onboarding_Recommendation`, `Similar_Vendors`,
`Favorited Vendors`, `Categories`. ⚠️ The `isAutoFetch` toggle is a COLLECTION setting — editor
only, the MCP cannot change it. The workflow half IS MCP-editable.

**The `Auth Token Early Restore` workflow (`f1a6b93d-…`) is still live and doing nothing
measurable.** It is harmless (no-ops when the token is present or no cookie exists), but if
someone revisits this, read its `[auth-early]` console line first — it says whether the problem
is ordering or cookie visibility. Do not rewrite it before reading that.

**Decision: stopped deliberately.** The sign-out bug was the one hurting customers and it is
fixed; the residual 401s degrade side rails, not the core flow. Not a dead end — a chosen stop.

## 2026-09-16 RECURRENCE — and three things that were WRONG

Customers (Amy Kuo by email, plus Kate and Des in Slack) reported being bounced to the login
page on every click, repeatedly. User rolled back a WeWeb app version and assumed the 09-10 fix
was lost. **It was not.**

**CORRECTED — the rollback did NOT drop the fix.** Grepping the published bundle
(`www.tulletogether.app/assets/main-jXqzqn2b.js`, found via `index.html` -> `entry-client-*.js`
-> `__vite__mapDeps`) shows all 9 app workflows present, including `Open Channel`'s token-wait
and `Auth Token Early Restore`. Editor and published app-workflow sets match. The only live-vs-repo
drift is the HEAD BLOCK (still has `smoke_test`, still loads Chameleon). **Re-publishing a newer
version does not fix this bug** — do not spend time there again.

**CORRECTED — Xano was not touched.** `getWorkspaceAuditLogs` shows no workspace change since
2026-09-13. Rules out server-side regression.

**CORRECTED — preflight 401s in request history are a RED HERRING.** 62 of 84 `auth/me` OPTIONS
preflights log as **401** in request history, which looks alarming and is not. Sending six real
preflights returns **200** with correct CORS headers every time. **When counting 401s, exclude
rows carrying `Access-Control-Request-Method`** — they are cors-mode too, so filtering on
`Sec-Fetch-Mode` alone overcounted 16 real failures as 78.

**MEASURED, 2026-09-16 14:27-15:47 UTC, 3000 requests.** `auth/me` excluding preflights: 195x200,
**16x401 with NO Authorization header**, **12x401 WITH a token**. 45 unauthenticated `auth/me`
401s came from devices that had ALREADY authenticated in the same window, across 29 IPs.
Collections are far worse: `admin_choices` 88/142 401-no-header, `favorites` 123/263,
`donation_payments` 67/95.

**RETRACTED 2026-09-16 — "a token that works is rejected 19-100s after auth/login mints it".**
That claim was UNSOUND and was stated too confidently. It measured *seconds since the last login
seen at that IP*, and IPs are shared across devices/users, so the gap proves nothing. A live
console probe then held one real token at **200 for 11+ minutes**, contradicting it outright.
The 12 x 401-with-token in the window are most plausibly ordinary expiry: **`storeToken` writes a
1-YEAR cookie around a 24-HOUR Xano token**, so every user returning after a day presents a
valid-looking token Xano correctly rejects, gets `logout()`d and is silently bounced to login.
That is a real, chronic, separate defect — fix it in Xano (token `expiration`) or by handling
expiry explicitly. It is NOT caused by any rollback.

Original (retracted) reasoning kept below only so it is not re-derived:
Clean single-device trace (98.0.236.102): login 15:42:21 -> 200 AUTH everywhere -> vendor page
15:42:52 all 200 AUTH -> app reload 15:43:14 every collection 401s with no header ->
**15:43:19 `auth/me` 401 WITH a token, and `upgrade_offer` 401 WITH a token in the same second**,
two different API groups. Same shape on 108.190.220.85 (41s), 38.146.193.6 (26s/19s),
163.114.130.134 (19s/66s), 192.69.184.88 (101s). One user logged in SIX TIMES IN EIGHT MINUTES.
**Root cause NOT established.** The next step is the console probe: re-fetch `auth/me` with the
cookie token every 15s and read Xano's message — `"Invalid token."` (undecryptable/wrong key) vs
an expiry message are different bugs. Do not guess between them.

**DEAD ENDS, already checked, do not redo:** stale/unrefreshed cookie (the `ww-auth-access-token`
Expires advanced 16:28:16 -> 16:33:34 in real time, so it IS rewritten every login); apex-vs-www
cookie split (DevTools cookie tree has only `https://www.tulletogether.app`, no apex node);
HttpOnly hiding the cookie (it is readable, HttpOnly unset).

**CLOSED, was a false lead:** the cookie's Expires reads `2027-09-11` while today is 2026-09-16,
which looked like a ~5-day client clock skew. **It is not.** The console probe printed
`browser clock: 2026-09-16T16:39:35.442Z` — correct real time. So WeWeb's `expire:'1y'` simply
does not compute now+365d; the offset is a quirk of its cookie lib and is harmless. Do not chase
the Expires column again.

## SHIPPED 2026-09-16 (EDITOR ONLY — NEEDS A PUBLISH)

`Auth Token Early Restore` (`f1a6b93d-47c2-4e58-b0d1-2c9a7e5f8033`) **repurposed into
`Auth Session Guard`**. Full pre-change backup:
`.claude/weweb_backups/wf_f1a6b93d_auth_token_early_restore_pre_guard_2026-09-16.json`.

Why the old body went: it was already measured to help nothing (ep74 13.5% before, 14.0% after),
AND it wrote `f5856798-...-accessToken` (the PLUGIN id) while **`fetchUser` reads
`${this.id}-accessToken` where `this.id` is the INSTANCE id `043be1a5-6494-4edc-9398-d76bc5fdc032`**
— confirmed by reading `_initAuth`/`fetchUser` out of the live bundle. So it could never have
reached the variable it was trying to fix. Auth-touching code that does nothing is not worth
keeping while sign-outs are live.

What replaced it: a guard around the plugin's `fetchUser`, because that function's catch is
`throw this.logout(), e` and `logout()` calls `removeToken()`, which **deletes the cookie**. So
any failure — no token, network blip, 5xx — signs the customer out. The guard (a) refuses to call
`auth/me` with no token, and (b) probes with plain `fetch` so only a real 401 is handed to the
original. It finds the plugin by CAPABILITY (`typeof p.fetchUser === 'function'`), not by key,
because bundle keys are minified. Logs `[auth-guard] ...`.

⚠️ **This treats the amplifier, not the cause. A genuine 401 still logs out, by design** — so if
tokens really are dying in 60s, customers still get bounced, just less often and never from a
transient blip.

**Still unguarded and NOT fixable by the MCP:** `XANO realtime updates`
(`64cdabb6-...`) calls `fetchUser` with no token wait, and `Open Channel` falls through to
`fetchUser` even when its wait times out with `tokenReady:false`. Both contain plugin actions
([[reference_weweb_mcp_limits]]) so they are EDITOR-ONLY. The harmful `auth/me` 401s cluster in
the same second as `user/update_vendor_views` (25) and `venue/pricing_intelligence` (24) — i.e.
vendor-page navigation, which writes the user row and publishes `user_updates`. That is
suggestive of the realtime workflow but NOT proven.

**Useful access note:** `xano-meta` MCP failed to connect this session, but the token in
`.mcp.json` drives both the plain metadata REST API
(`https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta/workspace/1/request_history?page=N&per_page=200`,
which returns `request_headers` including whether `Authorization` was present) and the MCP over
plain HTTP JSON-RPC (`initialize` then `tools/call`; responses come back as `event:/data:` SSE
frames). `getAPI` still returns `xanoscript: null` — metadata cannot read endpoint logic.

## 2026-09-16, later — the shim theory is DEAD too, and what actually shipped

A live browser reported **exactly ONE JWE-shaped cookie: `ww-auth-access-token`**. So the old
shim's shape-matching was never picking a wrong token. That theory is dead — do not redo it.
Useful by-product: **the cookie name is NOT a minified constant**, it is stable and can be
matched by name.

**A WeWeb VERSION RESTORE wipes editor workflow state.** Confirmed: the user restored a version
and `f1a6b93d` reverted from `Auth Session Guard` straight back to `Auth Token Early Restore`.
`Open Channel`'s `firstAction` survived as the token-wait `f6214df3`. **After any restore, re-check
f1a6b93d and re-apply from
`.claude/weweb_backups/wf_f1a6b93d_AUTH_SESSION_GUARD_reapply_2026-09-16.json`.**

**SHIPPED v2 (editor, awaiting publish):** `f1a6b93d` = `Auth Session Guard`, doing both halves —
(1) early-restore the token from `ww-auth-access-token` **by name** into
`${plugin.id}-accessToken` using the INSTANCE id read off the plugin object (the old body wrote
the PLUGIN id key, which nothing reads — that is why it measured as useless), token only, never
`user`/`isAuthenticated`; (2) wrap `fetchUser` so it never runs tokenless and only a genuine 401
reaches `logout()`. Finds the plugin by capability (`typeof p.fetchUser === 'function'`), not by
key. Logs `[auth-guard] ...`.

**Verification after publish:** grep the new live `main-*.js` for `auth-guard`, confirm
`Open Channel` `firstAction` is still `f6214df3`, then re-pull request history and compare the
unauthenticated-`auth/me` count against today's baseline of **16 in 80 minutes**.

## 2026-09-27 — seen again IN THE EDITOR (user signed out mid-session)
auth/me log: editor 200 @23:00:31, then 401 with NO Authorization header @23:03:11, then 200 after re-login.
Consequence the user saw: Vendor Discovery advanced filters VANISHED (all 3 pieces gate on
forever_access_purchased ==false/==true; with user null it is undefined, so nothing renders - incl. paywall card).
Page workflow 'MP - View Page' ran Fetch user FIRST with no token wait. Copies audited:
- Venue Vendor Page ef225677 - Fetch user REMOVED via MCP (sent only the 2 non-plugin actions; worked, read back).
- Discovery 038ed9e8, Other Vendors 8a630a64, My Wedding, Cost Research e309fd5c - already had no Fetch user.
- Membership b4489a1f STILL HAS IT (loader on -> fetchUser -> loader off -> viewed_page) - deliberate refresh on
  /pricing; NOT removed, user not yet asked. Recommend a token-wait step before it instead.
- Recommendations / Planning & Payments / Share Tulle / Profile: no MP - View Page.
Editor is more exposed: 'Auth Token Early Restore' skips in the editor. Backup:
.claude/weweb_backups/mp_view_page_pre_remove_fetchuser_2026-09-27.json
TIP: removing a plugin action works if you send ONLY the non-plugin actions (validator never sees the plugin type).
