---
name: feedback_signup_page_link_and_checkbox
description: "Sign-up page - no \"Not ready? try the demo\" link from page load (bad look); keep the terms checkbox (removal TBD, user leans against)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: a9483c7d-0298-48d3-b53d-715962768463
  modified: 2026-10-16T03:40:19.086Z
---

2026-10-15: user rejected showing "Not ready to sign up? See how it works" immediately on /sign-up ("that's a bad look"), and thinks dropping the terms checkbox is a bad idea ("but tbd").

**Why:** brand/trust look of the sign-up page outweighs catching the ~6s instant leavers; checkbox seen as legally/visually right.

**How to apply:** propose only delayed or behaviour-triggered versions of the demo link (form error, ~60s idle, desktop exit intent). For the Google `?google_error=terms` leak, fix within the checkbox (e.g. prompt to tick before the Google redirect), don't propose removing it again unless the user reopens it. Data: [[project_signup_dropoff_2026-10-07]].

**SUPERSEDED 2026-10-24:** owner + Des decided (after lawyer: "either option works, we recommend the checkbox") to REMOVE the
checkbox for everyone and test it; "By clicking Sign up, you agree..." next to the button. Xano now stamps
TC_Agreement_Time + terms_version "2026-10-22" (table 1 col added 10-24) on ep2 + ep7 new users (verified on a real row).
New /terms WeWeb page built from Des's PDF (text word-identical, .claude/terms_of_service_2026-10-22_paragraphs.json).
Judge by page-view -> sign-up rate (daily sign-ups swing 75-130), ~2 weeks. Termly privacy/cookie URLs in the session log.
