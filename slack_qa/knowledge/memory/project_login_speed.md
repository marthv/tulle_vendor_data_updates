---
name: project-login-speed
description: "Login slowness diagnosis 2026-07-21 — WeWeb workflow serial fetches before redirect, backend fast"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1ee0b3e6-d423-4adb-bca4-63d59b2ef261
  modified: 2026-07-21T06:50:15.822Z
---

Login feels slow (~5-6s) but Xano is NOT the bottleneck: auth/login 40-130ms, auth/me 30-90ms, all login-path calls <550ms. Cause is the WeWeb "Log In / On click" workflow: 4 variable changes + 3 serial Fetch collection actions (MapLight=wptp_map_light, ADMIN - Choices=admin_choices, + search) BEFORE the redirect to Vendor Discovery — and the destination page re-fetches all of them anyway (double-fetch proven in request history 05:09:49-55).

**VERIFIED FIXED 2026-07-21 (prod trace 06:44): login→auth/me→page data with zero 401s, one fetch per collection, no packages full scan.** Original recommendation: move Redirect right after authToken set; delete the 3 fetches from the workflow (page-load fetches cover them, run parallel). Signup flow also: Gmail email synchronous 1.17s; GET /packages with no user_id took 5.8s (full table).

Related: [[project_xano_compute_reduction]]. State-filtered wptp_updated_mappings_search cold cache = 1.6-2.9s (warmer only covers default params) — separate issue.
