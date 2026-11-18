---
name: project_vendor_network_portal_2026-11-03
description: "Vendor portal reframed to vendor network \"get listed at a venue\" flow (Des mockup) - branch vendor-network 76a5dc5, Xano eps 562-567 LIVE + table 66 kinds added; branch NOT pushed (push blocked), NOT deployed; no review screen"
metadata:
  node_type: memory
  type: project
  originSessionId: 2c0addd5-f1e3-49c4-a905-b40524fb1798
  modified: 2026-11-04T05:06:45.107Z
---

2026-11-03/04: owner started roadmap #4 early - reframe tulle-vendor-portal (Railway "tulle vendor portal", deploys from main only) into a vendor network. Spec = Des's Venue Intel mockup question flow + Granola calls: Des/Vivek ("digital preferred vendors list, not paid to play", association not leads, toured-vs-worked weighting, negative answers need a reason, manual proof, annual renewal, blank until density, upsell tiers later, outreach to vendors on preferred-list PDFs) and Molly Culver florist (don't promise leads; free+easy = worthwhile; load-in vendor-only; wants user stats).

**Owner decisions (11-03):** vendors ONLY · Load-in kept as-is · storage = REUSE vendor_submission (table 66) kinds `business_profile` + `venue_intel` · proof REQUIRED for everyone incl. "Toured it".

**State 11-04 ~05:15 UTC:**
- Portal branch `vendor-network` = 4009dab + 76a5dc5, LOCAL. `git push` was blocked by the auto-mode classifier -> owner must run it. Not deployed (main = d959e3f).
- Xano LIVE: table 66 kind enum +2 (backups `.claude/xano_backups/table66_*_pre_network_kinds_2026-11-04.json`, 8 rows verified unchanged); group 13 eps **562** venue_partners, **563** businesses (SLOW 3.6-5.8 s; ep244 also 2-4 s), **564** signup, **565** intel (venue must be Category "Venue", NO claim gate), **566** listings, **567** business. Live-tested incl. 400/401/403 paths + full UI run against prod Xano (The Aviator V14516).
- QA rows left: vendor_account 4 + submissions 9/10 ([email]), plus UI-run account [email].
- **No review screen:** the Streamlit Vendor Portal tab was removed deliberately (tulle_vendor_data_updates 17578c8) - don't re-add without asking.
- Open: business-search latency (needs index), tag confirm/invite emails promised but unbuilt, "Founding vendor" undefined, couple-facing display out of scope.
- Repo also has owner's UNCOMMITTED calendar WIP (actions.ts, mock.ts, xano.ts, types.ts, api.ts, ics, calendar page) - untouched.

Related: [[project_vendor_portal]] [[project_vendor_network_recommended_by]] [[project_partner_vendors]]

**2026-11-04 05:33 UTC DEPLOYED (owner pushed vendor-network -> main, 76a5dc5).** Railway deploy SUCCESS; live checks: / new copy, /signup 307 -> /get-listed, /dashboard 307 -> /login, APIs 200; full Playwright run on https://tulle-vendor-portal.up.railway.app passed (QA [email], The Aviator listing). Rollback: Railway redeploy of the 2026-08-18 d959e3f deployment, or push --force d959e3f:main. Still open: tag-confirm/invite emails + "Founding vendor" copy promises, no review screen, ~5 s business search.

**2026-11-04 ~05:45 UTC CUSTOM DOMAIN LIVE: https://vendors.tulletogether.app** (owner added in Railway, target port 8080 = app port; Cloudflare DNS for tulletogether.app, proxied; verified 200 via cloudflare -> x-railway-edge). tulletogether.com DNS is WordPress, not Cloudflare. Stale: layout.tsx metadata description still "Claim your venue..." (one-line fix, not done).

**11-04 ~06:00 UTC local commits (owner to push):** 3de815c metadata description; a38d48b PRICING REQUIRED for businesses not already a Tulle page (manual add OR preferred-list pick; Tulle-matched skip): starting_price + basis (minimum_spend/starting_package/per_hour/per_person) + year required, typical range/guide link/note optional; enforced in form + submitListingAction; stored in business_profile.pricing. Favicon = admin dash tulle.png squared to 64px as src/app/icon.png (favicon.ico removed). Mock e2e 9/9, clean build OK. Push: `git push origin vendor-network vendor-network:main`.

**11-04 06:00 UTC DEPLOYED a38d48b (owner pushed; main = a38d48b).** Verified on vendors.tulletogether.app: new meta description, /icon.png (64x64) linked, no favicon.ico; live hand-added-business run stored table 66 row 15 with pricing {starting_price 2000, basis minimum_spend, year 2027}; Tulle-matched rows 11/13 carry no pricing (as designed). QA accounts 4-7 (@tulle-qa.example) still in tables.

**11-04 ~06:20 UTC local commit 3a395d4 (owner to push):** /help rebuilt for vendors - topics (listing/business+pricing/add venue or vendor/login/other), signed-in prefill + "Which listing?" picker, dashboard "Report a problem with this listing" -> /help?topic=listing&listing=<venue id>. Still ep250 (saves vendor_message table 67, then Slack #tulle-vendors via webhook); context written into message body. Slack route NOT re-verified since the Aug smoke test (table 67 has 1 row ever); undelivered rows only visible in Xano (review tab removed). Mock e2e 13/13, clean build OK.

**11-04 ~06:30 UTC local commits 3a395d4 (help page) + b23a887 (owner to push):** pricing guide REQUIRED for new businesses = link OR PDF (<=15MB) - form + action + ep564 v2 (multipart business_json + file? pricing_file?; PUBLIC Xano attachment because plan has no private files - "Not supported. Please upgrade" - owner chose public; guide_file never couple-facing). ep565 v2 posts one Slack notice per submission to #tulle-vendors via env slack_vendor_webhook_url (Xano header + portal summary) - VERIFIED: QA submission #25 posted 06:25 UTC with PDF link. Gotcha: XanoScript `file? x` = nullable but REQUIRED; optional needs `file? x?` (was briefly required 06:16:34-06:17:07 UTC). Backups ep564_*_pre_pricing_file / ep565_*_pre_slack in .claude/xano_backups. Live portal (a38d48b) meanwhile errors new-business signups without a guide link until b23a887 is deployed.

**11-04 06:27 UTC DEPLOYED b23a887 (main).** Live-verified on vendors.tulletogether.app: help page new topics (no venue field); guide rule (disabled without link/PDF, non-PDF error); live QA submission #27 with PDF upload -> full Slack notice in #tulle-vendors incl. PDF link. QA rows now span accounts 4-15 (@tulle-qa.example) - offer cleanup.

**11-04 ~06:45 UTC QA CLEANUP DONE (owner OK):** deleted vendor_account 1,2,4-7,9-15 (@tulle-qa.example), vendor_submission 1-6,9-16,19-27 (23), vendor_claim 1,2, vendor_message 1, Xano files 44404-44406 (QA PDFs). Full-row backup .claude/xano_backups/vendor_portal_qa_rows_pre_delete_2026-11-04.json. KEPT owner test accounts 3 ([email]) + 8 ([email], 1 Hotel Brooklyn Bridge test listing) + claim 3 + submissions 7,8,17,18. Slack QA messages #25/#27 in #tulle-vendors left for owner.

**11-04 replay-cron (tulle search) failing since 10-28 - DIAGNOSED, owner must fix in Railway (my env edit blocked by classifier):** (1) MEILI_URL still http://meilisearch.railway.internal:7700 (service deleted 10-27 23:13) -> crash at "could not store run: POST /indexes transport error"; set to http://meilisearch-b.railway.internal:7700 (master key already matches meili-b). (2) REC_TEST_TOKEN in Railway expired (401 on every search-api call); local ~/.tulle_rec.env token works (200). Tokens last 14 days -> durable fix = replay job uses the X-Admin-Secret operator path (main.py:302) instead of a user token; checking secrets was blocked [Credential Exploration].

**11-04 06:42 UTC replay-cron fix DEPLOYED (tulle-search main 529e002, owner pushed):** job signs in fresh each run via REPLAY_LOGIN_EMAIL/PASSWORD (Xano auth/login api:v1keLQ2a), REC_TEST_TOKEN fallback; failed login = fatal. 41/41 tests. Not yet run. OWNER TODO in Railway replay-cron vars: MEILI_URL -> http://meilisearch-b.railway.internal:7700, add REPLAY_LOGIN_EMAIL + REPLAY_LOGIN_PASSWORD (test account), Deploy, Run now; then verify logs show "auth: login" and no "could not store run". (Operator X-Admin-Secret path was NOT enough: ep119 on Xano also needs a user token - 76/76 Xano calls 401d too.)
