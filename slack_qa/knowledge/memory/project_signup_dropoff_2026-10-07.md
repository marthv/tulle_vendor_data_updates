---
name: project_signup_dropoff_2026-10-07
description: Where the /sign-up drop-off happens (2026-10-07) - NOT the form, NOT mobile; 78% leave without typing; terms checkbox = 23% error rate
metadata:
  type: project
---

Measured 2026-10-07 (user asked "desktop or mobile, where on the page?").
- Device: no difference. /sign-up -> signed_up within 1d (09-28..10-05): mobile 62% (iOS 63%, Android 50% n=50),
  desktop 62%. Mobile = 77% of losses only because 77% of traffic. 13% of landers logged_in instead (existing members).
- Form submissions (Xano ep2 auth/signup request history, ~1 day kept, 165 attempts / 124 emails): 96% of people who
  SUBMIT end with an account. 31 attempts "must agree to Terms" (28 people, 26 recovered), 9 password-rule errors,
  6 "already in use" (5 people, 0 recovered - no "log in instead" path).
- Replays (27 iOS non-converters with logs): 21/27 (78%) left WITHOUT TYPING, typical ~6s on page; 4 typed partially.
  Replay logs are masked text, no video; only ~48% of events carry $mp_replay_id.
- Conclusion: the leak is pre-engagement (sign-up wall), not form friction -> value-before-wall (venue preview,
  [[project_experimentation_framework]]) and the "open product, sign in only on save actions" bet are the levers.
  Small fixes offered: replace terms checkbox with "By signing up you agree..." line (user's legal call); "Log in
  instead" link on the already-in-use error.
