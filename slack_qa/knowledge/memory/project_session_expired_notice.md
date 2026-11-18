---
name: project_session_expired_notice
description: "Your session expired" banner on Sign In - built in WeWeb editor 2026-09-27, NOT PUBLISHED; detector watches XHR/fetch for auth/me 401-with-token
metadata:
  type: project
---

Asked for 2026-09-27: users were silently logged out (expired token -> auth/me 401 -> the
Xano Auth plugin's fetchUser catch calls logout() -> Sign In, no explanation).

**Built in the WeWeb editor, UNPUBLISHED (user must publish):**
- App workflow `Session Expired Detector` `a6ec64eb-c17b-4250-a509-0b6a0156f51b` (onload-app,
  custom-js). Patches XMLHttpRequest + fetch, observe-only: when a request to `/auth/me` that
  carried `Authorization: [redacted] ...` gets 401, writes sessionStorage `tt_session_expired`
  = Date.now(). Skips in editor. Logs `[session-expired] detector installed`.
- Variable `sessionExpiredNotice` `9d73d343-8d70-4472-afd1-c67b8ae5afb4` (bool, folder Auth).
- Sign In page (`e96e1872...`, path `home`) page workflow `Show Session Expired Notice`
  `65047ebe-68a8-4e4c-8316-8d7da7ca365d` (onload): reads + removes the flag, true if < 1h old,
  `update-variable` sets the notice var.
- Banner `Session Expired Notice` `1fe48204-05b2-4d07-a212-9623e0fae4f6` in Auth Container,
  after the logo block; conditionalRendering on the var. Green g50/g200 card.

**Why XHR/fetch, not wrapping fetchUser:** the WeWeb MCP validator now REJECTS `wwLib` inside
custom-js (JS_INSTEAD_OF_NATIVE_ACTION) and setting variables in JS. Old workflows that use
wwLib still exist but new ones cannot. Use `update-variable` action with varId at root.

**Verify after publish:** grep live `main-*.js` for `session-expired` (APP workflows are in
main.js; the PAGE workflow is not - see [[project_ep119_401_race]]). Live test: signed in, set
`document.cookie="ww-auth-access-token=[redacted]; path=/"`, reload -> should land on Sign In
with the banner. Log Out button must NOT show it.

Note: the Sep-16 `Auth Session Guard` (f1a6b93d) is NOT live - editor and bundle both hold the
old `Auth Token Early Restore`; a version restore wiped it. Not re-applied (out of scope).
[[project_token_14d_and_ep119_auth]] [[project_preauth_race_signs_users_out]]
