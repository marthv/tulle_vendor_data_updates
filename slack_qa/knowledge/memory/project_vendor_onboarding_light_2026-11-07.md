---
name: project_vendor_onboarding_light_2026-11-07
description: "11-07 Touchbase vendor items 1+2 LIVE + VERIFIED 11-08 (main 5923966, eps 564v3/565v4/569v2); item 3 LIVE + VERIFIED main f8e0abe"
metadata:
  node_type: memory
  type: project
  originSessionId: 414a0ef5-58e4-4f90-8471-4c67caaa3e9c
  modified: 2026-11-08T05:40:08.479Z
---

Owner 11-07 (after Granola "11.7 Touchbase"): do 1 + 2, then 3 (Common App-style save-and-return dashboard + progress tracker).

1. Pricing optional at sign-up; required before the page goes live (no couple-facing page exists yet, so "live" = dashboard status "Not live yet - add pricing"). Loosen "Toured it" proof: month/year only, link/file optional.
2. Block free-mail (gmail/yahoo/...) at sign-up (portal-side only); show category "Planner" as "Wedding planner / coordinator" (keep stored key "Planner").

State at stop 11-07: branch `vendor-onboarding-light` created in worktree tulle-vendor-portal-connections (from afe4d62 = live main), NO code changes yet. Full backups written in .claude/xano_backups: ep564_..._pre_pricing_optional_2026-11-07.xs, ep565_..._pre_toured_optional_2026-11-07.xs, ep569_..._pre_pricing_file_2026-11-07.xs (565/569 copied from the files they were published from; identical apart from trailing spaces).

Xano edits needed (owner yes before publishing, all backward compatible):
- ep564 v3: drop the "non-Tulle business must bring a guide" precondition.
- ep565 v4: proof precondition also passes when payload.knowledge == "toured".
- ep569 v2: optional business_json + `file? pricing_file?` (attach new PDF, else keep old guide_file); drop the guide precondition.
Portal: cleanBusiness (lib/network/business.ts) pricing optional (empty = none; partial price = error; guide optional); GetListedFlow proof step + business step copy; get-listed/actions.ts toured + free-mail; EditBusinessForm PDF upload; dashboard readiness badge; Slack summary "pricing not added yet".
**11-08: portal code DONE, commit 5923966 on vendor-onboarding-light (NOT pushed).** tsc/lint clean, 35/35 unit tests, build clean, mock e2e 21/21 (scratchpad e2e_onboarding_light.py). New Xano sources written, NOT published: .claude/xano_backups/ep564_..._v3_pricing_optional_2026-11-07.xs, ep565_..._v4_toured_optional_2026-11-07.xs, ep569_..._v2_pricing_file_2026-11-07.xs. Order: publish 3 eps (owner yes) -> owner `git push origin vendor-onboarding-light:main` -> live check. Live behaviour UNVERIFIED until then.
**11-08 15:11 UTC LIVE (owner "yes push"):** eps 564 v3, 565 v4, 569 v2 published (live matched backups first); pushed main afe4d62..5923966, Railway deployed (new copy in live bundle). VERIFIED live: endpoint smoke 8/8 (scratchpad live_smoke_onboarding.py: signup w/o pricing, 1+ no proof 400, toured no proof 200, 569 JSON + multipart PDF, PDF kept on later JSON edit) + live browser e2e 21/21 (e2e_onboarding_live.py). QA leftovers to delete w/ owner OK: vendor_account 16 (qa-onb-*@tulle-qa.example) + the live-e2e qa-onb-live-* account, their vendor_submission rows (from #29 up), QA PDFs; QA Slack posts in #tulle-vendors labelled "please ignore". Slack delivery itself not checked. Rollback: updateAPI with the _pre_ backups + git push --force afe4d62:main.
**11-08 ITEM 3 BUILT, commit f8e0abe on branch vendor-common-app (on 5923966), NOT pushed - no Xano changes needed.** Drafts in localStorage per venue (lib/network/draft.ts; key tulle.vendor.listing-drafts.v1; no password/files; 60-day expiry) - chosen because signed-out vendors have no account until the last step; cross-device drafts would need a Xano table (not built). Update prefills last listing (draftFromListing; untouched prefill not saved). Dashboard: checklist (lib/network/progress.ts: account, expertise "Start contributing expertise", pricing, profile, 3 venues), In progress drafts, topic coverage. tsconfig allowImportingTsExtensions added. 46/46 unit, build clean, mock e2e 30/30 (scratchpad e2e_common_app.py) + 1+2 regression 21/21. Not built: reminder emails (no approval step / email sender). **11-08 ~15:55 UTC PUSHED (owner yes) main 5923966..f8e0abe, Railway deployed ~60 s; LIVE e2e 30/30 (QA acct qa-ca-live-*@tulle-qa.example, another QA listing at Bowery Hotel NY -> add to cleanup).** Rollback: git push --force 5923966:main.
**11-08 GOOGLE SIGN-IN (owner ask: Google OAuth for non-gmail) BUILT, commit da35544 on branch vendor-google-signin (on f8e0abe), NOT pushed.** GIS ID-token popup (reuses Tulle web client [token]; OWNER must add https://vendors.tulletogether.app to its Authorized JavaScript origins). Xano sources NOT created: .claude/xano_backups/new_fn_vendor_google_verify_2026-11-08.xs (tokeninfo + aud/iss/exp/email_verified/free-mail check), new_ep_vendor_network_google_login_2026-11-08.xs (NO_ACCOUNT error), ep564_vendor_network_signup_v4_google_2026-11-08.xs (backup ..._pre_google_2026-11-08.xs = live v3). Mock e2e 16/16 (e2e_google.py) + regressions 21/21, 30/30; 48 unit; build clean. **11-08 ~16:25 UTC LIVE (owner added origin + yes):** fn 158 vendor/google_verify, ep 570 vendor/network/google_login created; ep564 v4 published; main f8e0abe..da35544 deployed. Verified: fake tokens -> 403 (fn runs), password sign-up still 200, no-email 400, live /login renders Google iframe with no origin errors. UNVERIFIED: a real Google sign-in/sign-up (owner to try with Workspace account). Rollback: ep564 <- _pre_google backup; deleteAPI 570 + fn 158; git push --force f8e0abe:main. Extra QA acct qa-g4-*@tulle-qa.example for cleanup.
Open risk: many solo planners use Gmail; the block may cut real sign-ups.

Related: [[project_vendor_connections_graph_2026-11-04]] [[project_vendor_network_portal_2026-11-03]]

**11-08 PM QA CLEANUP DONE (owner yes):** deleted vendor_account 16-19 + vendor_submission 28-38 (tables 64/66); verified none left. Backup .claude/xano_backups/vendor_qa_rows_pre_delete_2026-11-08.json. NOT removed: 2 tiny QA PDFs in vault, QA Slack posts. Reminder emails still unbuilt (owner reply ambiguous 'find for email reminders').
