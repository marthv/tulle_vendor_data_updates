---
name: project_home_page_logout_2026-10-11
description: "Sign In page (/ , e96e1872) onload 'Reset Authentication' cce99a01 LOGS OUT every signed-in visitor (verified in LIVE /data json 10-11); ~11% of new sign-ups re-login within 1h; fix drafted, edit blocked pending user OK"
metadata:
  type: project
---
- 2026-10-11 VERIFIED in live /data/e96e1872-...json: workflow cce99a01 "Reset Authentication" (onload) first action ad9696cb = Xano Auth logout, enabled; next two actions disabled.
- Impact (subagent, Mixpanel): logged_in only exists from ~09-27. 09-29..10-10: 125 of 1,153 sign-ups (10.8%) logged in again within 1h, 121/127 same session; 73% of ALL logged_in fire on bare `/`. Desktop >= mobile, TikTok not higher -> in-app browser storage is NOT the main cause. auth/me 401 still 9.6% (45/48 no auth header) on 10-11/12 sample.
- Log Out buttons (mobile nav 5ef4f2cd, desktop nav, etc.) call logout THEMSELVES then go to Sign In, so removing the page-load logout does not break Log Out.
- Drafted fix: r0 custom-js waits <=3s for token if JWE cookie present; signed in -> change-page Vendor Discovery 0d69d71b; else -> original logout; sessionStorage loop guard tt_home_signedin_redirect_at (20s). editFrontendWorkflow was DENIED by the auto-mode classifier -> needs user OK (or user hand-edit). Backup: .claude/xano_backups/weweb_signin_reset_auth_cce99a01_pre_signedin_redirect_2026-10-11.json
- Live site fetch trick when DNS fails: curl --resolve www.tulletogether.app:443:52.85.193.32 (CloudFront IP, may change).
