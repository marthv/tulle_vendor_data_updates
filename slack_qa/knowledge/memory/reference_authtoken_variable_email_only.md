---
name: reference_authtoken_variable_email_only
description: WeWeb global variable `authToken` (6fcad7ad) is filled ONLY by the email Log In workflow; Google sign-in stores the token only in the Xano Auth plugin. Bind components to pluginVariables accessToken, never to 6fcad7ad alone.
metadata:
  type: reference
---

**Fact (2026-11-01):** WeWeb global variable `authToken` = `variables['6fcad7ad-d2da-4a19-a0ee-34bd4a204a71']`
is written by the email Log In flow and cleared by the Log Out workflows. The Google return page
workflow `Complete Google Sign-In` (7d2f5c1e) only runs Xano Auth `storeAuthToken` + `fetchUser`, so for a
Google session 6fcad7ad stays "". The planner-chat element f9ec7409 on Tulle Planner bound `authToken` to
6fcad7ad alone -> Google users saw "Sign in to use the Tulle Planner" while the rest of the page (Member
badge, shelf) worked. Owner hit it in prod 2026-11-01 ~14:20 right after a publish; the binding itself
predates today (no edit of content.default.authToken in the session before the fix).

**Fix applied in the editor 2026-11-01:** f9ec7409 `content.default.authToken` =
`pluginVariables['f5856798-485d-47be-b433-d43d771c64e1']?.['accessToken'] || variables['6fcad7ad-...'] || ''`.
Needs the owner's publish; verify by signing in with Google on prod and loading /assistant.

**How to apply:** any new element/workflow that needs the token uses the plugin accessToken first.
Other 6fcad7ad users checked 2026-11-01: Nav Log Out 668e40d7 / menu Item 676cf118 / Membership
"session expired" button f39151c7 only reset it; Shelf Saved Unsave c8c7e9ab and the planner-chat
profile-updated / favorite-toggled workflows already fall back to the plugin token.
Related: [[project_google_signin]], [[project-preauth-race-signs-users-out]], [[reference_weweb_component_gotchas]].

**Status check 2026-11-01 (later session):** live www.tulletogether.app /data/dae212b5...json (cacheVersion 1366) STILL has f9ec7409 authToken = [redacted] only; editor has the plugin-first binding. So the bug persists in prod only because production has not been published since the fix. Other live elements (3ee46d5b, e6063bd0, tour 64570531) already read the plugin accessToken, so that path works for Google sessions. Next: owner publishes production, then re-check the live JSON + sign in with Google on /assistant.

**RESOLVED 2026-11-01 (evening):** owner published production (cacheVersion 1367); live /data JSON now shows f9ec7409 authToken = [redacted] accessToken || 6fcad7ad || ""; owner confirmed Google sign-in works on the live Planner. Rule stands for any new element: plugin accessToken first.
