---
name: project-preauth-401-fix
description: "2026-07-21 pre-auth 401 fix (ep 31/70 public+cached, ep 34 webhook strip) + Slack webhook ROTATION PENDING + WeWeb dedupe checklist"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1ee0b3e6-d423-4adb-bca4-63d59b2ef261
  modified: 2026-07-21T06:12:14.393Z
---

Shipped 2026-07-21 (group 4 Weweb; backups in .claude/xano_backups/ep31_34_70_pre_auth_cache_2026-07-21.xs, uncommitted):
- **ep 31 `states` + ep 70 `admin_location_adjustment`**: auth cleared (`auth = ""` quirk, see [[reference-xano-mcp-quirks]]) + response cache 1h. Both read static "ADMIN - Location Adjustment" table (state/cost/latlng). Kills WeWeb pre-auth 401s. Verified unauth 200.
- **ep 34 `admin_choices`**: kept auth; response now strips `Slack_Webhooks` + `Promo_Slack` via foreach+|unset (it previously dumped LIVE Slack webhook URLs to every logged-in user). Verified unauth still 401. **NOT yet verified with an authed call** — check request history (include_output) after user next loads the app to confirm WeWeb bindings unbroken.

**ACTION PENDING (user): rotate the two exposed Slack webhooks** ([webhook-url] and .../B09JHSL910R/...) — they were readable by all ~35k users. After rotating, update rows 1-3 of table 2 "ADMIN - Choices" (Slack_Webhooks column) with new URLs.

**ep 112 `packages` v2 shipped 2026-07-21** (backup ep112_packages_pre_guard_2026-07-21.xs): public endpoint with `==?` was dumping the ENTIRE Packages table (all users' wedding packages, 5.2-5.9s) when called without user_id — real unauth production traffic was doing this on page load. Now user_id missing/0 → [] in <0.3s; user_id=N unchanged (verified 23805 → 2 rows).

**Verification gotcha:** request-history `include_output` shows output:null for large 200 responses (worked for 401 bodies) — can't confirm admin_choices field-strip from history; user must visually confirm dropdowns populate. Also: traffic in request history may be REAL PRODUCTION USERS (check IP/locale/referer editor vs www) — don't mistake it for the user's own test; WeWeb editor changes don't hit production until app is Published.

Remaining WeWeb-side (user to do): gate the admin_choices collection fetch on authToken being set (it still 401s pre-login); dedupe page-load double/triple fetches seen in traces: admin_choices 2x, favorites 3x, extracted_pdf_data 2x, google_data_for_vendor 2x, search 3x with identical params (likely auto-fetch + explicit workflow fetch both firing).

Related: [[project_login_speed]].
