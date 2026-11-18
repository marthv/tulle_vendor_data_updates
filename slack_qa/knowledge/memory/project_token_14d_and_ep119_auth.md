---
name: project_token_14d_and_ep119_auth
description: 2026-09-27 auth tokens 24h->14d on all 6 minters; task 8 now 42 buckets (top-12 states); health report committed; ep119 auth-on PENDING (not before 2026-09-28 20:05 UTC)
metadata:
  node_type: memory
  type: project
  originSessionId: 06c27a2a-b960-40ba-a903-9be6c3720ccd
  modified: 2026-09-29T14:38:35.783Z
---

**Why:** a 24h Xano token inside WeWeb's 1-year cookie logged returning users out daily; WeWeb
then fired searches with no token. User wants ep119 auth-REQUIRED (anti-scraping) and ep121 stays
auth-required (3.2% 401s accepted, "secret sauce" - never make it public).

**Shipped 2026-09-27 ~20:04 UTC (read back live):** `expiration = 1209600` (14d) on ep1 login,
ep2 signup, ep5/6/7 google-oauth, ep18 magic-login. ep298 (admin impersonate, disabled) untouched.
Backups: `.claude/xano_backups/token14d_2026-09-27/`. Nothing enforces `token_session`.

**Task 8 warmer 2026-09-27 20:09 UTC:** 15 -> 42 buckets (grid + Semi-Inclusive strip for NY NJ TX
FL International PA MA MD CA CT "Not Sure" VA, plus states[]-only grids where 2+ visitors).
`timeout = 30`, alert now lists each failing query + status, warm token 1800s. Backup
`task8_pre_state_warm_2026-09-27.xs`. Covers first pages only - not filters/page 2/search box.

**Health report LIVE + VERIFIED 2026-09-27:** Railway service `health-report-cron`
(`aa8ee1ae-...`), cron `32 13 * * *`, posts a SHORT traffic-light version to #tulle-users;
full text in job log + dashboard "🩺 Health" tab (on-demand runs never write history).
Xano table **78 `endpoint_health_daily`** (one row per endpoint per day). Gotchas learned:
- NEW Railway services CANNOT use config-as-code (cut-off 2026-08-28); root railway.toml
  (streamlit) still showed in the build plan but the service-level startCommand won at run.
- New services need `MISE_PYTHON_GITHUB_ATTESTATIONS=false` or python 3.11.5 install fails.
- Railway runs 3.11.5 (runtime.txt): backslash in f-string expr crashed the whole dashboard.
  Local is 3.13 - py_compile passes. Scan with a tokenizer check before pushing.
- Xano meta request_history/search 502s on 500-row all-endpoint pages: filter by query_id,
  200/page, retry 5xx.
- Cron changes apply on next deploy; runs start 1-6 min late.
- Token var is set literally on health-report-cron; dashboard references it
  `${{health-report-cron.XANO_METADATA_TOKEN}}`.
Warmer state buckets VERIFIED: CT/VA/TX probes were cache hits after the 21:30 run.
**Report delivery = DM to Vivek via Tulle Bot** (`HEALTH_SLACK_BOT_TOKEN` xoxb +
`HEALTH_SLACK_USER_ID`), falls back to #tulle-users webhook WITH the error if the DM fails.
First try (2026-09-27 22:12) failed `missing_scope`: posting to a USER id opens a new IM, which
needs `im:write`; Tulle Bot has only chat:write. Chose NOT to add the scope (a reinstall can
re-mint the incoming-webhook URLs Xano uses). RETRACTED: posting to the existing DM id
`D098L487ZNZ` ALSO failed missing_scope - Slack needs `im:write` for any bot DM; chat:write alone
is not enough. DECISION (user): give **Tulle Ops** `im:write` (feedback_bot manifest b890c3c) and
reinstall IT (no webhooks there); swap HEALTH_SLACK_BOT_TOKEN to Tulle Ops' xoxb;
HEALTH_SLACK_USER_ID back to `U05UJMCHUV6`. **DONE + VERIFIED 2026-09-27 22:45 UTC: report arrived
as a DM from Tulle Ops.** HEALTH_SLACK_BOT_TOKEN is now Tulle Ops' token (Tulle Bot's exposed token no
longer used by it). Cron reset to 32 13 * * *; test duplicate rows removed from table 78. Token was exposed
in a screenshot in chat - rotate eventually, NOT via blind reinstall.
**Task 8's own Slack alert REMOVED 2026-09-27 21:49 UTC (user request)** - the daily health report's
warmer line replaces it (up to ~24h detection delay, accepted). Backup with the alert:
`task8_pre_remove_slack_alert_2026-09-27.xs`.

**ep119 auth-on is PENDING.** Do it only after 2026-09-28 20:05 UTC (all 24h tokens dead), and
after the health report shows tokenless ep119 dropping. Re-add `auth = "user"` only; keep v24's
other edits; cache.auth stays false; don't bump cache_v. Full backup first.
**2026-09-28 check: ep119 still auth=false live** (user's Slack said "was set" - it was NOT). Table 78 tokenless
ep119 app calls did NOT drop after the 14d-token change: 276/4345 (6.4%) 09-27 -> 390/5724 (6.8%) 09-28. Unknown split
between signed-out visitors and pre-auth-race members - break down by IP before flipping.
**USER RULE 2026-09-28: "flip it unless more than 1% are signed-in members"** (member tokenless calls as a share of
ALL ep119 app calls, after 20:05 UTC). Inconclusive classification = don't flip, ask.
**2026-09-28 20:15 UTC analysis -> NOT FLIPPED (still auth=false).** 1,498 app calls 15:43-20:12 UTC, 143 tokenless
(9.5%), all 200. Same IP+UA token call within 60s: 70 = 4.67% (39 devices, not one outlier) -> fails the 1% rule as
written. BUT 0 within 5s, 8 within 10s, 20 within 20s; 67 of 70 are tokenless-THEN-token with median gap 30s. A pre-auth
race would show ~1-3s gaps, so these look like signed-out browsing then signing in, not members racing. Real open
question for the user: should signed-out visitors see the Discovery grid before signing up? Auth-on blocks them.
Data + script: session scratchpad classify_ep119.py (ephemeral).
**USER DECIDED 2026-09-28: signed-out visitors must NOT see results -> order = WeWeb redirect, publish, THEN flip.**
[SUPERSEDED 2026-10-02: venue pages are PRIVATE by user choice - see below] Venue pages stay public (organic landing) with Similar Vendors hidden for signed-out. Editor changes (UNPUBLISHED):
Discovery "Updated Collections" e5656811 now starts __SIGNEDOUT_CHECK__ -> __IS_SIGNED_OUT__ -> navigate Sign In
e96e1872; Venue "Load Extracted Data" 362c3d59 skips Similar fetch 36ddb94a when signed out; blocks 9798b265 (Venue)
and 10a72eab (Other Vendors) hidden without accessToken. Uses globalThis.document (MCP rejects wwLib in custom-js;
existing __WAIT_FOR_AUTH__ was rewritten the same way). Similar_Vendors collection auto-fetch still ON -> harmless
401s on venue pages after flip. Backup: .claude/weweb_backups/ep119_signedout_redirect_pre_2026-09-28.md.
Flip script: .claude/xano_backups/ep119_auth_on_2026-09-28.xs (backup ep119_pre_auth_on_2026-09-28.xs).
**SHIPPED: WeWeb published 2026-09-28 23:58 UTC** (verified: __SIGNEDOUT_CHECK__ etc. in live /data/0d69d71b....json,
__SIMILAR_IF_SIGNED_IN__ in venue page data - page workflows live in /data/<pageId>.json?wwlang=en, NOT in main.js).
**ep119 auth=user LIVE 2026-09-29 00:10:06 UTC.** Live script byte-identical to the flip file; no-token + bad-token
curls -> 401 incl. cached keys (cache does not leak). First 30s: 1 real token call -> 200. **30-min check (00:10-00:47 UTC): 59 app calls; 55 with token -> all 200
(0% members refused); 4 tokenless -> 401 from 3 IPs, 2 of which got token 200s 9s/31s later (signed in after).
No 5xx; 200s p50 0.08s / p95 3.0s (pre-flip p95 ~5.8s, not comparable - small evening sample). No rollback.**
**2nd check (00:47-01:52 UTC): 501 app calls; 479 with token -> ALL 200 (0% refused); 22 tokenless -> 401 from 16
IPs, ALL 16 signed in 5-75s later (median 26s). No 5xx.** p95 25.2s BUT from 35 cache-miss calls, 27 of them deep pages
(page 3-13, mostly DC/FL/NY single-state) fired in BURSTS - up to 9 calls in 1s from one IP (01:05:33), 6 bursting IPs.
Not caused by auth (cache.auth=false, same shapes were slow pre-flip).
**RESOLVED 2026-09-29 - NOT A SCRAPER.** Xano request-history `created_at` = when the request FINISHED, not started
(created_at - duration matches the Expires header = start time, within ~1s). Rebuilt by start time, the "9 pages in 1s"
was one iPhone user tapping < every 0.5-2s (DC pages 13->6 after jumping to last page); the cold queries piled up
concurrently and finished together. 7 bursting IPs in ~23h, all real app sessions (boot calls, vendor pages, filter
changes, consumer UAs). Cause: WeWeb pager (Next/Prev/number/First/Last, workflows 66b74eaf, 6925aaf7, a4797d20,
28fc53be, 1a05292f) has NO in-flight guard - every tap fires an un-cancelled ep119 fetch. Fix (not built): "if Search
Vendors collection is fetching, stop" at the top of the pager workflows. Small; not a security issue.
LESSON: never infer request timing/bursts from created_at alone - subtract duration.
Next: re-check on a full weekday (health report "Failures" line for ep119 should now show the tokenless 401s).
Staging test: signed-out VENUE page also redirected to Sign In - not from our edits; likely pre-existing page Access
setting (unverified; MCP can't read it; main.js has an unauthorizedPageId redirect).
Also still open: silent logout has no "session expired" message (separate WeWeb change, not asked for).
[[project_ep119_warmer_timeouts]] [[project_ep119_401_race]] [[project_perf_baseline_digest]]
**2026-10-02:** "Search reliability 5.2% failed" in the health DM was a METRIC artifact: 0/4,174 signed-in calls
failed; 210/232 tokenless 401s were the My Wedding `Onboarding_Recommendation` collection (ep119 page_size=3,
Semi-Inclusive fallback) auto-fetching before the signed-out redirect. Report fixed (perf_digest 90561d6: headline =
signed-in failures, refusals on a neutral line). **X-Real-Ip is now an internal 10.x proxy addr - client IP = first
X-Forwarded-For hop.** WeWeb fix: page workflow b7c4e1a2 on My Wedding fetches only when signed in; USER must turn
"Fetch automatically" OFF on that collection + publish. Backup weweb_backups/strip_signedout_pre_2026-10-02.md.

**2026-10-02 PAGE ACCESS (read from live main.js wwg_designInfo.pageUserGroups; router beforeEnter redirects signed-out
to Sign In BEFORE the page loads):** private = Vendor Discovery, My Wedding, Venue Vendor Page, Other Vendors, Profile,
Membership, Recommendations, Cost Research, Planning & Payments. Public = auth pages, purchase-/roadmap-complete,
Share Tulle!, Admin Dash. **USER 2026-10-02: vendor pages private ON PURPOSE (no public access for now); Admin Dash is
unused - fine public. Do not re-raise either.** The ~230/day tokenless ep119 401s were the Onboarding_Recommendation
auto-fetch firing BEFORE the redirect (111/179 devices signed in a median 26s later) - no data leaked (all 401).
Fix published 2026-10-02 ~22:55 UTC; expect "signed-out refusals" ~0 in the 10-03 health DM.
