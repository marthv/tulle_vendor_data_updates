---
name: project_security_lockdown_2026-09-29
description: "2026-09-29 full exposure audit (207 eps) after user said \"lock all our data down\" - public repo leaks the shared secret, Stripe proxy, self-grant access; supersedes the \"deferred, don't re-raise\" notes on PDFs/ep46"
metadata:
  node_type: memory
  type: project
  originSessionId: 0c05ebfb-a977-4a0f-84cf-5b564a9affe4
  modified: 2026-09-29T14:49:55.498Z
---

User asked 2026-09-29 to "lock all our data down" - so the earlier DEFERRED items (ep93 PDF_Link, Drive
anyone-with-link, ep46 relay, ep34) are back IN SCOPE. Full inventory (no secret values):
session scratchpad exposure_audit_2026-09-29.md (ephemeral - re-derive if lost).

207 endpoints: 94 user-auth, 48 secret, 65 anonymous (16 are legit auth flows).

VERIFIED by me 2026-09-29:
- marthv/tulle_vendor_data_updates is PUBLIC (gh: visibility PUBLIC; anon raw fetch 200). The shared
  `ttv_export_...` literal is on origin/main in cohorts.py, dashboard.py, extract_core.py, job_status.py,
  roadmap_orders.py since 2026-07-26 (470287e). It opens 39 secret-gated eps (users export ep205, all Drive
  links ep146, bulk pricing ep199, admin metrics, WRITES ep140/143/144/163/172/301, ep214 Brevo send).
  Treat as COMPROMISED. Railway deploys from this repo - making it private needs the Railway GitHub app to
  keep access.
- ep27 stripe_checkout/sessions auth=false, proxies Stripe list with $env.stripe_api_secret (live) - customer
  emails/addresses, pageable. ep26/28/29 same group. No callers found (agent) - verify WeWeb before delete.
- ep87 PATCH userpayment auth=user but writes the CALLER's date_until_access from input + adds total_amount -
  any free account can grant itself paid access + PI (>=50). Its "guarded" comment is wrong: no guard exists.

AGENT-REPORTED, not yet re-verified: ep21 accepts client date_until_access; ep145 sets free PDF views;
Gmail app passwords (2) in Sign Up page data (appPassword param to ep46, ignored server-side); ep147/ep173
dump table 36 / venue_pricing to any signed-in user; ep121/ep74 include-flags return pricing+PDF for many
vendors; ep142 anonymous table-36 rows per vendor; ep93 PDF_Link anon; ep149 anon benchmarks; ep43/180/127
anon PII tables; ep44/52/105 anon writes; ep115/94/81/71 open relays; 3-4 Slack webhooks in page data;
ep34 went ANONYMOUS 2026-09-16 (36.8 KB); Drive roots anyone-reader + 2 outside WRITERS
(nicoleandnickaregettingmarried@gmail.com, anna@unusualbeaut.com).
Burst investigation same day: NOT a scraper (see project_token_14d_and_ep119_auth).

## 2026-10-01 01:39 UTC: ep87 LOCKED (user approved)
Stack replaced by `precondition (1 == 2)` accessdenied "This endpoint is retired."; no db.edit remains, so
self-grant is closed by construction. Read back: published, draft null. No-token PATCH -> 401. Signed-in
refusal NOT tested with a real token (none available). Callers checked first: 0 in request history; no repo
code; Make scenarios 5619049/5663404 clean (5590108 blueprint 502'd - cleared indirectly via 0 history calls);
only live reference = legacy Sign Up referral step (disabled in Sign Up v2). Abuse check over 28,435 users:
29 have total_payments > payment-log rows - all legacy (13 created 2025-12-24, same 2026-12-31 expiry,
old $15/$50/$75 prices = import/batch), none far-future -> no self-grant pattern. Separately 1,382 users
have future access with 0 payments (PDF-submission/referral/promo grants; NOT ep87 - it bumps
total_payments). Rollback: .claude/xano_backups/ep87_pre_lock_2026-09-30.xs.
Still open from this audit: ep21 (agent-reported client date_until_access), ep27 Stripe proxy, leaked secret.

**2026-10-24 NEW (found by WeWeb sweep):** Sign Up workflow "Sign Up v2" (f7e97a41) action "Send email via API"
(e447fd21) has a GMAIL APP PASSWORD hardcoded in its body (appPassword) -> ships in the public front-end bundle.
Must rotate + move server-side. Same workflow + Referral Flow (WhatsApp/Instagram) + "Book a call" (Bottom_pannel,
Contact Us components) post to hardcoded Slack webhooks (T09H9PJC334/..., T05U8AWH14Z/...).

**2026-10-25:** ep196 `venue_calculator_entries_v1` (manual venue quotes) is PUBLIC and filters only by the email passed
in -> anyone can read anyone's saved quotes by email. rec-cost-tools only passes the signed-in user's email + re-filters.
Also: Packages GET /packages?user_id= has no auth header (filters in browser). Add both to the endpoint auth audit.

## 2026-10-28 SECURITY BATCH 1 LIVE (owner-approved "Batch 1: quick closes")
- **ep26/27/28/29 (stripe_checkout /sessions, anonymous Stripe proxy) RETIRED** - precondition 1==2 "This endpoint is retired."; live check: all four return 403 (ep27 returned customer sessions with 200 the day before). Callers checked first: 0 in history except our own test, no reference in any of the 20 live WeWeb pages, main.js or the pipeline repo (payments use roadmap_session / upgrade_session + webhook ep30). Backups `.claude/xano_backups/ep2{6,7,8,9}_*_pre_lock_2026-10-28.xs`.
- **ep21 user/update_record:** client can no longer extend date_until_access (future date ignored) or raise FreeViewsRemaining (capped at stored, 3 if null; absent field now keeps stored instead of nulling it). Live-tested with test acct 34429 incl. the real client format "2026-10-28T03:45:06.000Z": expire-today still works, 2030/2031 ignored, countdown works, paid-user date (simulated 2026-11-30) preserved.
- **FOUND + FIXED: the 2026-08-16 "paid users keep their date" guard in ep21 NEVER WORKED.** `date|parse_timestamp:"Y-m-d" > now` is never true in XanoScript (parse_timestamp returns TEXT, bare `now` is not a timestamp). Now compares YYYY-MM-DD text vs `"now"|to_timestamp:"UTC"|format_timestamp:"Y-m-d":"UTC"`. Checked the 7 real expire calls in the last 24 h: all 0-payment users, so no paying customer was cut short in that window (older damage not checked). **Grep other endpoints for `parse_timestamp ... > now` - same trap likely elsewhere.**
- **ep145 userfreeviews:** can only lower the count (stored cap); legacy Sign Up referral step (+3) now gets 200 with no change instead of an error.
- Files: `.claude/xano_backups/sec_batch1_2026-10-28/` (pushed sources), `*_pre_lock_2026-10-28.xs` (rollbacks), test acct row backup `user34429_pre_sec_test_2026-10-28.json` (restored to date null / 3 views after tests).
- STILL OPEN: ep30 webhook signature, leaked export secret (repo public), ep196 quotes by email, open mail relays ep46/71/81/94/115, Gmail app password + Slack webhooks in front-end, ep162 forged referrals, ep44 Drive-link overwrite, bulk-email eps 127/180/43.

## 2026-10-28 SECURITY BATCH 2 LIVE (owner: "do 1 and 3")
- **ep30 Stripe webhook:** for checkout.session.completed / async_payment_succeeded it now re-reads the session from Stripe (GET /v1/checkout/sessions/{id}, $env.stripe_api_secret) and uses ONLY Stripe's copy; unknown id -> 500 "Could not verify this checkout session with Stripe." (Stripe retries real ones); not-yet-paid sessions -> 200, nothing granted. Live test: fake Forever payload for test acct 34429 refused, account unchanged; non-checkout events still 200. Read-only temp function (149, deleted) proved: real sessions 200/paid with weeks + ref, made-up id 404.
- **ep30 renewal bug FIXED:** buyers with time left restarted from today (same parse_timestamp>now trap). Now `(date|to_text) > today` and start = `date|to_text|to_timestamp:"UTC"`; tested Nov 30 + 2 weeks -> Dec 15. **VERIFIED ON A REAL PAYMENT 2026-10-28 04:41 UTC:** owner bought 1 week ($35) as vivek.marthi+explodingoffer@gmail.com (user 34267, internal) -> ep30 200 handled=true, date_until_access 2026-11-05 (today+8 d), total_payments 1 / $35; ep162 200, ep314 200 ignored. Renewal path (buyer with time left) still NOT observed live - only tested in the temp function. Xano's Stripe key is RESTRICTED (no prices/links write) - test links must be made in the Stripe dashboard.
- **ep196/ep197 venue quotes:** auth=user, always filter by the signed-in account's email (input email kept optional, ignored). `==?` with blank email used to return EVERY user's quotes. 33/34 real calls already sent a token. Logged out -> 401.
- **TRAP (cost ~2 min of 400s, only my curl hit it):** ep196's exported XanoScript does NOT re-import: `sort = {venue_calculator_entries.created_at: "desc"}` fails with "Unsupported parameter reference - xdo.venue_calculator_entries.created_at" - even re-pushing the untouched backup broke it. Use the $db table name: `sort = {Venue_Calculator_Entries.created_at: "desc"}`. **Lesson: a backup .xs is not a guaranteed rollback; smoke-test right after ANY push incl. rollbacks.**
- Same unverified-webhook class probably on ep314 gift_webhook and ep162 referral-conversion (both group 7, Stripe-called) - not touched.
- Files: `.claude/xano_backups/sec_batch2_2026-10-28/`, rollbacks `ep{30,196,197}_pre_sec_batch2_2026-10-28.xs` (fix the sort line before using the 196/197 ones).
- **2026-10-28 batch 2b:** ep314 gift_webhook was ALREADY safe (re-fetches the session from Stripe; mints only if id matches, status complete, paid, metadata.type gift, payment_link = gift link) - no change. Minor: a fake session posts a Slack "did NOT verify" warning, so the URL could be used to spam Slack.
  **ep162 referral-conversion FIXED:** trusted the posted customer email and any event type -> anyone could mark a referral paid ($10 creator commission). Now checkout events only, session re-read from Stripe, only Stripe's email/amount/created used; unknown/unpaid -> 200 no-op; Stripe outage (not 200/404) -> error so Stripe retries. Live test: fake payment for a real unpaid referral (row backed up `.claude/xano_backups/referral_row_pre_fake_test_2026-10-28.json`) left it unpaid; replay of a real processed session -> 200. Backup `ep162_pre_sec_batch2_2026-10-28.xs`.

## 2026-10-28 EXPORT-SECRET ROTATION (owner: "do 1") - IN PROGRESS
- New value in `~/.tulle_rec.env` as ANALYTICS_EXPORT_SECRET (never printed; read only via Python). Old value = the literal still in git HISTORY of the public repo.
- Step A DONE: all 38 live endpoints gated by `$input.secret == "ttv_export_..."` now accept OLD || NEW (verified by read-back + smoke test 200/200/403 on ep187/206/268/249). Pre-change backups `.claude/xano_backups/secret_rotation_2026-10-28/api<id>_g<group>_pre.xs`. (The other 20 secret-gated eps use different secrets: sha256 x10, ttv_slackbot x5, ttv_imp x2, $env x2 - not leaked, untouched.)
- Step B DONE: ANALYTICS_EXPORT_SECRET set (value-verified) on Railway "tulle admin dash" services Tulle Admin Dash, health-report-cron, batch-worker-cron, batch-ingest, extraction-batch (rec-engine does not use it). Hardcoded fallback removed from cohorts/dashboard/extract_core/job_status/roadmap_orders.py -> commit caa8bd8 pushed to marthv/tulle_vendor_data_updates main (worktree C:/Users/vivek/Documents/GitHub/tulle_secret_rotation, branch rotate-export-secret).
- Railway CLI is now logged in as the PERSONAL Tulle account (vivek.marthi@gmail.com), linked in scratchpad/rlink - it was on the Healthie account before; the owner may need to switch back for Healthie work.
- LOCAL scripts now need ANALYTICS_EXPORT_SECRET in their environment (no fallback) - load it from ~/.tulle_rec.env via Python.
- Step C TODO: watch Xano request history for calls still sending OLD (compare in Python, print counts only); when none for a few hours, push the 38 endpoints to NEW only. Owner TODO: make the repo private.
