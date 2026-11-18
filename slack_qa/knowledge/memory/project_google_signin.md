---
name: project_google_signin
description: "Couple Google sign-in/sign-up - ep7 fixed + WeWeb return page built 2026-09-27; NEEDS user's 2 plugin actions, Google Console redirect URI, button cleanup, publish, live test"
metadata:
  node_type: memory
  type: project
  originSessionId: 06c27a2a-b960-40ba-a903-9be6c3720ccd
  modified: 2026-09-28T01:55:28.660Z
---

**Why:** user wants Google sign-in to lift signup rate. Before 2026-09-27 the "Continue with Google"
buttons (Sign In `4ea90244`, Sign Up `b0dcd0b5`) sent users to `/google-redirects`, a page that did
not exist -> 404. Google client id+secret ARE set in Xano (`$env.workspace.google_oauth_client_id`
/ `google_oauth_secret`; client id starts 543358...).

**Xano (LIVE):** ep7 `oauth/google/continue` (group 2, GET, public) is THE endpoint. Requires
`verified_email`, lowercases email, matches existing account by email and links `google_oauth`;
new users get ep2 defaults (FreeViewsRemaining 3, TandC_Agree + TC_Agreement_Time, random
"Gg1"+uuid password). Returns {token,user_id,name,email,is_new_user}. ep5/ep6 left but unused
(Google-id-only lookup strands email users). Backup `google_oauth_2026-09-28/ep7_pre_google_signin.xs`.
New-user insert path (password required column, TC_Agreement_Time) is UNVERIFIED until a real signup.

**WeWeb (editor, UNPUBLISHED):** page `Google Sign-In Return` `d0f4f382-9c28-4cf6-9ea9-894269b291d9`
path `google-redirects` (spinner card); page workflow `Complete Google Sign-In` `7d2f5c1e-...`:
custom-js fetch ep7 -> if ok -> marker step `g3_store_here` -> [USER ADDS Xano Auth Store auth token
(token = [redacted]'g1_exchange'].result.token) + Fetch user] -> Mixpanel identify(String id) +
signed_up/logged_in {method:google} + referral credit (localStorage `tt_referral_code`) -> Vendor
Discovery; else -> Sign In `?google_error=1`. App workflow `Remember Referral Code` `3c8e1a90-...`.
Sign In banner `Google Error Notice` `6d440aad` (shows on google_error=1). Sign Up `Google Terms
Consent` `7896f6ef` under the Google button.

**Buttons were HIDDEN in prod:** "Continue with Google" + "OR" divider on Sign In (4ea90244, 80a22285) and
Sign Up (b0dcd0b5, 89f9688d) had conditionalRendering = empty formula (evaluates falsy). Set to `true`
in editor 2026-09-27 (ships with the publish). Google Console redirect URI already present (verified
by screenshot). Opening the return page directly -> Sign In `?google_error=1` is BY DESIGN (no code).
**Terms gate (user decision 2026-09-27: explicit tick, not implied consent):** Sign Up Google button
`b0dcd0b5` disabled + 0.45 opacity until checkbox `51dd01cb` ticked; checkbox change workflow writes
localStorage `tt_terms_agreed`; Sign Up onload clears it. ep7 input `agreed_terms` - NEW accounts
need it (else error "TERMS_REQUIRED" -> return page sends to Sign Up `?google_error=terms`, notice
`ee04b9be`); existing users sign in from either page without it. Return page workflow now starts with
a `filter` that stops in the editor (it used to redirect the editor away). Backup of ep7 before the
gate: `ep7_v2_pre_terms_gate.xs`.
**Plugin actions ADDED + verified 2026-09-27:** storeAuthToken `0a2d76ab` (authToken js
`context.workflow['g1_exchange'].result.token`) -> fetchUser `c2cc5049` -> g4. GOTCHA: WeWeb MCP
`createFrontendWorkflow` with an EXISTING id does NOT replace - it adds a DUPLICATE with the same id
(both ran on load, the old one kept redirecting the editor). Delete by id removed only the older copy.
Use `editFrontendWorkflow` to change an existing workflow, or delete first.
**STAGING VERIFIED 2026-09-28 00:39 UTC:** 3 existing accounts signed in via Google (user 14998,
2332, 2333; is_new_user false). First failure was a TRAILING SLASH: staging serves
`/google-redirects/`, button sent no slash -> Google token exchange 403. Fix: return page uses
`origin + pathname.replace(/\/+$/, '')`. Buttons' redirect_uri = `window.location.origin +
'/google-redirects'` (JS). Google Console has staging URI. NEW-account path still UNTESTED.
**Welcome email:** new Xano fn 72 `emails/send_welcome_email` (nodemailer, $env GMAIL_*; never
throws) - test send to hello@ returned sent:true. ep7 calls it for new users only. Backup before:
`ep7_v3_pre_welcome_email.xs` (logic identical, header comment older).
**SECURITY (told user 2026-09-28):** Sign Up v2 "Send email via API" step ships a Gmail APP
PASSWORD in the browser bundle -> user must revoke + rotate into Xano env GMAIL_APP_PASSWORD.
ep46 `Email` (api:Ax5V7IeO) is a PUBLIC open mail relay from Tulle's Gmail (any to/subject/html).
Same workflow also posts to Slack with the #tulle-users webhook hardcoded in the browser.
**Onboarding popup** = component `bbdfe17d` "Onboarding Popup"; user adds If is_new_user -> Open popup.
**Unticked-click feedback (2026-09-28):** user chose the form's existing red error text over a
popup (popup + its workflow were built then DELETED). Sign Up Google button now `disabled:false`,
grey via opacity binding, cursor pointer; user's own workflow: if checkbox -> Google init, else
`throw` -> error branch sets ce9cff73; user to add fallback text
`... || 'You must agree to the Terms, Privacy and Cookie Policy to continue with Google.'`.
Apex tulletogether.app 301s to www, so `window.location.origin` is always the registered URI.
**User to-do (remaining):** Google Console authorized redirect URI `https://www.tulletogether.app/google-redirects`;
add the 2 plugin actions; delete the Sign Up button's premature `mixpanel.track("signed_up")` step;
confirm new page is public; publish; live tests (new Google acct, existing same-email acct, cancel).
Not replicated for Google signups: onboarding popup (bbdfe17d) and welcome email (that call leaks a
plaintext appPassword from the browser - fix server-side separately).
**Click speed (2026-09-28, editor, UNPUBLISHED):** both Google buttons (Sign Up b0dcd0b5 wf 7c8c59c3, Sign In
4ea90244 wf ef6bbebe) no longer call ep4 oauth/google/init - the Google URL is built client-side (client_id
54335843688-vqor...; same params init returned). Click sets var `googleRedirecting` 84d8cd59 -> label
"Connecting to Google…" + wait cursor. No plugin actions left in those workflows (MCP-editable). Backups:
`.claude/weweb_backups/sign{up,in}_google_button_*_pre_loader_2026-09-28.json`. ep4 is now unused.
Measured: init was 0.02-0.5s + the first test fired it 3x; new-account ep7 1.05s vs ~0.3s existing.
First-time slowness is mostly Google's one-time consent screen (human time), not the app bundle (it's
already cached from Sign Up) - my earlier "1.7 MB download" explanation was wrong.
Onboarding popup: user fixed missing `return`. STAGING CONFIRMED by user 2026-09-28: loader + popup work
(gauntlet items 1-7 pass; continuing from item 8).
Gauntlet 8-17 PASS (user, 2026-09-28). Verified in Xano: phone signup user 31012 ([email])
FreeViews 3, TC_Agreement_Time set, google_oauth linked; Mixpanel signed_up method=google distinct_id 31012 (1 event).
Desktop Google signups (31006/31007, user-deleted soccerdog333 test accts) sent NO signed_up - cause unknown
(ad blocker on desktop Chrome suspected, unverified). #18 Instagram in-app browser WORKS (no disallowed_useragent) - no need to hide the button.
Left: #19 referral (optional), then PROD publish.
Email signups (ep2) leave TC_Agreement_Time NULL - pre-existing gap, offered fix.
[[project_token_14d_and_ep119_auth]] [[project_session_expired_notice]]
