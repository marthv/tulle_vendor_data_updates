---
name: project_google_signin_blocked_by_app_wf_2026-11-06
description: "11-06 Google sign-in endless 'Signing you in' since 11-04 = MY b3d17f42 v2 (30-min awaited token watch on app load) blocked page-load wfs; v3 non-blocking IN EDITOR, owner must PUBLISH"
metadata:
  type: project
---

Reported 11-06 in #product-development by Kate (user [name] emailed: endless "signing you in" for days; last Google sign-up 11-04).
**Evidence:** Mixpanel 3963130 signed_up method=google 11-21/day (≈21 typical) 10-21..11-03 -> 1 (11-04) -> 0 -> 0; logged_in google 9-39/day -> 3,2,1. Email unaffected.
ep7 history (~24h): only 7 calls, 3x200 (existing accounts with a saved session), 4x403 "Precondition failed" (accessdenied = Google code exchange refused; consistent with codes expired after the 30-min stall - UNVERIFIED, Google error body not logged: fn2 has no history).
**Cause:** app wf b3d17f42 "Upgrade Offer - 401 recovery" v2 (built 11-04, [[project_upgrade_full_price_tyler_2026-11-04]]) trigger onload-app, AWAITED a loop up to 30 min while no token. Signed-out visitors (everyone returning from Google) never reached Google Sign-In Return page wf (d0f4f382) -> ep7 never called. Sibling wf u0_wait_for_token caps at 5 s, which is why Google worked before. Assumption: WeWeb awaits onload-app wfs before page onload (fits all evidence).
**Fix 11-06 (editor, NOT published):** v3 = same watch launched un-awaited, writes 603f27b8 via wwLib.wwVariable.updateValue, returns immediately; r1_store removed. Backup: .claude/weweb_backups/app_wf_b3d17f42_v2_live_pre_nonblocking_2026-11-06.js.
**After publish verify:** live main.js contains "v3 2026-11-06 NON-BLOCKING"; real Google sign-in lands on Vendor Discovery; ep7 200s incl is_new_user true; Mixpanel google signed_up back near ~20/day.
**Lesson:** NEVER await a long wait inside an onload-app workflow - it stalls every page-load workflow for that visitor.
**11-07:** published 04:29:13 UTC (main-5EkNUeji.js has v3); owner Google login 04:30:58 UTC ep7 200 in 0.47 s. New-account path unverified.
