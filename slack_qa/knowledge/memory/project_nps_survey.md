---
name: project_nps_survey
description: "NPS microsurvey - component + table 77 + ep308 built and Xano-verified 2026-09-22; the whole WeWeb half is still open, so nothing is live"
metadata: 
  node_type: memory
  type: project
  originSessionId: 27f4732e-39e9-4e62-bd0d-8d608998c9b6
  modified: 2026-09-22T16:06:02.844Z
---

**Settings trimmed to 13 (pushed e5a8968):** removed firstSeenAt/minDaysSinceFirstSeen/minSessions/shareUrl/shareText/apiUrl; Xano URL is now a constant (an empty apiUrl field would have silently stopped all writes). minValueMoments = 2 is FINAL (user confirmed 2026-09-27); trimmed version activated by user.

**(superseded: now 2)** minValueMoments = 3 (user call; prop set in WeWeb, component pushed 0451fe7 = next version to activate).** 3 good moments IN ONE VISIT (counter resets on full reload). Gate tested headless without forceOpen: 0/1/2 hidden, 3 shown.

**STATUS 2026-09-27 end of day: in WeWeb STAGING, NOT published (live origin build = Sep 22 15:09 UTC; user holding publish for other updates). forceOpen=false. Table 77 empty. NEVER OBSERVED: the trigger path (counter -> 5s -> show) - every test used forceOpen; page-hiding via globalContext.page id/name; real-phone rendering; Mixpanel receipt on live. Verify these first after publish (curl -I live site: Last-Modified must move past Sep 22).**

**2026-09-27 evening:** user's own Preview test landed (row 11, user 14998, 4 stars) = FIRST real app write, v14 works. ep308 v5 posts to `$env.nps_slack_webhook_url` (#feed-nps), falling back to `$env.slack_webhook_url` (#tulle-users) while unset - `nps_slack_webhook_url` now points at the FEEDBACK channel (user swapped it 2026-09-27; #feed-nps being deleted). Test sent, slack 2xx; channel landing confirmed by user only. ep308 comments still say #feed-nps - cosmetic. Never write the webhook URL into scripts/backups/memory. Prompt hidden on Membership + Profile (page id/name in `suppressed`) and for the rest of the visit after 'Give Feedback' (var npsFeedbackOpened c53ba4c6, set by workflows e1a9c040/041 on nav a9e09635 desktop + 5263ee46 mobile; both open Contact Us popup 7774dc2b).

**COMPONENT NOW POSTS TO XANO ITSELF (pushed 12a367e = v14, needs activating).** The WeWeb http-request workflows 500'd with Xano "Error parsing JSON: Syntax error" (never logged in request history - rejected pre-endpoint): a whole-body JS binding is NOT serialised as JSON by WeWeb's http-request action. Fix: `postResponse()` in the component does fetch(apiUrl, JSON, keepalive). VERIFIED END-TO-END from a real browser (localhost origin) against live ep308: detractor (2, comment, slack) and promoter share (5, review_prompt_state=clicked, slack). Both old workflows a1f0c7d2-...5c01/5c02 DELETED. Don't re-add a workflow that POSTs the trigger events - it would double-write.

**Overlay TELEPORTED to <body> (pushed 17982c5 = next WeWeb version):** the instance sits in the nav section, which is fixed with z-index 100 on MOBILE (9999 desktop) - in place, anything >z100 drew over the prompt. Scrim now z 10000; tokens declared on `.nps-survey, .nps-scrim`. Shows on the 11 pages that carry Navigation Bar Section; not on auth/confirmation pages (Sign In/Up, Forgot/Update Password, purchase-/roadmap-complete) - user confirmed that is intended.

**Mixpanel events (pushed 0de59d1, needs next WeWeb version active):** nps_shown, nps_score_submitted (score, bucket), nps_completed (was nps_comment_submitted - renamed before any data existed), nps_dismissed (step), nps_suppressed, nps_share_opened, nps_share_clicked / nps_shared (channel), click_to_refer (source=nps_prompt). Sent via mixpanel.track directly; SKIPPED on *editor.weweb.io, so editor/Preview tests never show in Mixpanel - verify on the live site. Reports: all-events https://mixpanel.com/project/3963130/view/4458885/app/insights#96biYmEwKnxE , funnel ...#aRAAS43WMhi2. Zero events as of 2026-09-27.

**2026-09-27 LATEST — trigger loosened (user call):** no tenure/visit gates, 5s dwell, cooldowns 30d answered / 14d not-now (component props, pushed 786a7b7 = needs next WeWeb version activated), ep308 v4 dedupe 30d (smoke-tested). `npsValueMoments` now isPersistentOnNav and is the ONLY value-moment source (search-collection clause removed). 22 `NPS/` counter workflows e1a9c010..031: calc Search! 3358ac7e; package-save = submit on form fc34de02 inside POPUP 8ae8e9d7 (componentElement scope — popups ARE reachable via MCP now); favorite-only elements (Not Favorite hearts x5 Discovery + 1 home, 'Add to Favorites' 634ddf3b Venue / 0e8cdc4c Other); Similar Vendor cards x6 per vendor page. Plus the 4 PDF ones e1a9c001..004.

**2026-09-27 WIRED (v9 active), forceOpen STILL TRUE for testing, NOT PUBLISHED.** Instance = `95bea395-19e2-4740-a552-e17b34c75e7a` inside "NPS Survey Container" 878fcd47 in Navigation Bar Section (it existed from 9/22 with workflows `a1f0c7d2-...5c01` score / `...5c02` complete — edited IN PLACE; body now normalises payload, reads `context.event.value`, user_id -> int). ep308 had ZERO WeWeb requests before this fix. Server dedupes on user_id for 90 days: a 2nd test from the same account writes NO row — delete the test row between tests. Earlier notes: Done via MCP: 4 click workflows `e1a9c001..004-nps0-...` (folder NPS/) on the View PDF buttons 94885c62/bd4d43da (Venue) + b0fd4fee/462ef392 (Other Vendors) that do `npsValueMoments` (249e5619) +1 only when the PDF opens (paid || FreeViewsRemaining>0). **The MCP CANNOT PLACE a github-type coded component** (`Unknown element tag "nps-survey"`; listCodedComponents shows isReady:false for every github import) — user must drag it into Navigation Bar Section `de36599e` (shared by all core + vendor pages; fixed, z 9999, no backdrop-filter so fixed scrim works). After the drag: bind props via editElement + 2 http-request workflows (score-submitted, nps-completed; body `context.event.value`, throw:false, keepAlive:true). suppressed = showPdfLimit|showPdfWarning|isUpgradeModalVisible|isDiscountPopupVisible|showCreditsModal|showAdjustProfileModal|Show Mobile Menu|sessionExpiredNotice. NOTHING PUBLISHED. Also bind new `osName` prop to `pluginVariables['66a79c98-70e7-4bc4-8859-20776b024ec2']['deviceInfo']?.os?.name` (iMessage tile Apple-only; pushed 1ee8a94 = next WeWeb version, needs activating).

**2026-09-27 later: copy finalised by user + Quick Share grid + ALL SORA (user: "the font all other parts of Tulle use") — commit 5fd48ce, pushed to `marthv/ww-nps-survey` c505f38 (WeWeb builds from THAT repo, not tulletogether; push needs marthv creds on pull AND push). Promoter "Share Tulle" -> iMessage/WhatsApp/Instagram/More grid copied from the /invite page's Share Actions Row (same message, `sign-up/?referralcode=<userId>` built automatically, `click_to_refer` Mixpanel). WeWeb project id `d830a133-815b-419b-8efe-a0f104b3ece2`.

**2026-09-27 REWORKED to a 5-STAR prompt (commit 307ab5d, branch `nps-survey`, NOT pushed).**
Centred iOS-style "Enjoying Tulle Together?" card. 4-5 = promoter -> "Share Tulle Together"
(navigator.share, else copy link) -> thanks. 1-3 = detractor -> "What could be better?" ->
thanks. No passive. **ep308 is now v3: accepts 1-5 only** (smoke-tested live: 7 and 0 -> 400;
5/4 -> promoter, 3 -> detractor; `share_action` -> `review_prompt_state` offered/clicked/declined;
replay does not flip it). Test rows deleted, table empty. v2 rollback:
`.claude/xano_backups/ep308_nps_response_pre_5star_2026-09-27.xs`. Trustpilot props REMOVED
(share replaced them). New props `logoUrl`, `shareUrl` (bind to referral link), `shareText`.
Visual checked via headless Chrome at 500px only (headless min width) - not in WeWeb yet.
Everything below about 0-10 / pills / Trustpilot is v1 history.

Built 2026-09-22 on branch `nps-survey` (commit 70a58a5). **Xano half is live and
verified. WeWeb half is not started, so the feature is 0% live.**

**What exists**
- `components/nps-survey/` — coded component, builds clean. Two-step card: 0–10 scale,
  then one open-text question branched by bucket, then thank-you.
- **Table 77 `nps_response`** — user / time / page / score / comment / cohort / bucket /
  device_width / completed_at / slack_posted_at. Unique on `response_uid`.
- **ep308 `POST /nps/response`** (API group 4 `Weweb`, **auth=false**). One endpoint,
  two stages, upserts on a client-minted `response_uid` so there is **no round trip**.
  Backup: `.claude/xano_backups/ep308_nps_response_v1_2026-09-22.xs`.

**Verified live** (not just "it parsed"): create writes the row with bucket/cohort computed
server-side; bad stage and out-of-range score 400; dedupe returns `deduped:true` and writes
no row for the same user_id within 90 days; complete fills the comment and posts to Slack;
a replayed complete does not post twice. Test rows deleted, table confirmed empty.

**UNVERIFIED:** that the Slack message actually reached the channel. Slack returned 2xx but
`$env.slack_webhook_url` points at a **private** channel that public search cannot see.
Needs one human glance. (Same blind spot applies to the existing payment webhook posts.)

**Still open — the whole WeWeb side.** Register the component as a coded-component source,
activate the version, place **one** instance in the shared layout (not per page), bind the
props, and wire `score-submitted` + `nps-completed` to ONE `http-request` workflow. Use the
built-in action, not the Xano plugin action. `forceOpen` must be false in production.

**Before tuning anything, measure the eligible pool.** Volume is an ESTIMATE: from 3,472
monthly uniques, ~140–360 scores/month if 25–35% qualify and 15–30% respond. If the pool
turns out under ~300/month the score will not trend and the comments are the only real
deliverable. Answerable from Mixpanel 3963130. See [[project_experimentation_framework]]
for why per-conversion metrics are hopeless at this volume.

**Two things not to re-litigate.** `auth=false` is deliberate — an authed endpoint 401s the
CORS preflight ([[project_ep119_401_race]]). And the gates (≥3 days, ≥2 sessions, value
moment, 20s dwell, 90/45-day cooldowns) are the product: the reference microsurvey in the
screenshots converts 5% because it prompts everyone.

Trustpilot promoter branch is **written and dark** behind `enableReviewPrompt`. An incentive
may be offered for *a* review, never a positive one, and to everyone invited.

Slack is the only read surface — [[project_feedback_triage]]'s admin tab and eps 271–278
were deleted 2026-09-08.

## 2026-09-28 first hour on PROD (publish 14:48 UTC)
Mixpanel: nps_shown 7 (7 users; 1 was staging) -> nps_dismissed 7, ALL at step 1 with no score. nps_completed and
nps_suppressed have never been received (not in Lexicon). Table 77: 0 rows. 6 real users is too small to judge, but
0 ratings is the thing to watch - if it holds at ~50 shown, the card/timing needs rethinking, not just more exposure.

## 2026-09-28 end of day: 53 shown, 0 rated - NOT a bug, a targeting problem
52 users shown (37 free_engaged, 15 paid; 45 on Venue Vendor Page), 49 dismissed at step 1, 0 nps_score_submitted.
Live bundle HAS the star build (wwElement chunk contains "Enjoying Tulle", nps_score_submitted). Mixpanel replay of
user 31070: signed up -> 6 min later card shown -> dismissed 1s later with a button tap (taps work). Most viewers
were accounts created that day (ids 310xx-311xx). Cause = asking brand-new users within minutes of signup; the
"no tenure/visit gates" choice (commit 24838d8) is what allows it. Suggested fix (not built): min account age
(e.g. 3+ days) or 2nd+ visit. Buckets are only promoter (4-5) / detractor (1-3).
Mixpanel board "NPS survey (5-star)": https://mixpanel.com/project/3963130/app/boards#id=11553802

## 2026-09-29: age gate + pause pages PUSHED (marthv/ww-nps-survey c07d5fd = tulletogether f2356b1)
New props accountCreatedAt (bind user.created_at, auth/me returns it in ms), minAccountAgeDays (3), promptPages
("Vendor Discovery,My Wedding"). Age gate FAILS CLOSED: until accountCreatedAt is bound on instance 95bea395 the
survey shows to NO ONE. Needs: user activates new version in WeWeb -> bind accountCreatedAt -> staging test with
minAccountAgeDays=0 -> set back to 3 -> publish.
PUSH RECIPE: git's credential helper is `gh auth git-credential` (active gh account). gh has both vmarthi17 (active)
and marthv; `gh auth switch --user marthv`, clone/push, `gh auth switch --user vmarthi17` in ONE command. The deploy
repo differs from tulletogether only by line endings - compare with diff --strip-trailing-cr before pushing.
2026-09-29: new version ACTIVATED by user; instance 95bea395 now has accountCreatedAt = user.created_at, minAccountAgeDays 3,
promptPages "Vendor Discovery,My Wedding" (read back from My Wedding page = shared section). Editor only, NOT published.
Backup: .claude/weweb_backups/nps_instance_pre_age_gate_2026-09-29.md

## 2026-10-02: MODAL -> NON-BLOCKING CORNER CARD (pushed marthv/ww-nps-survey 8853fde = tulletogether 6de9e15)
Why: 09-28..10-02 the modal was shown 128x (unique/day), dismissed 121, scored 3 (2.3%, all 09-29). ALL dismissals at
step 1, desktop as much as mobile (Mac 51/51, Win 27/28, iOS 44/48), median shown->dismissed ~23s, 0 nps_suppressed
-> real rejection of the interrupting modal, not a bug. nps_dismissed has no "via" property (scrim/Not now/Esc
indistinguishable). User chose the format change + "must not close on a background tap".
Now: no scrim at all (`.nps-dock` only spans the card; tokens moved to `.nps-survey, .nps-dock`), desktop bottom-right
340px, mobile (<768px) docked bottom with 12px gutters + safe-area inset, max-height 70vh; step 1 compact row (40px
logo + question) + stars; "Not now" pill replaced by x (aria-label "Not now"); Esc still dismisses; NO focus steal on
show. Verified headless Chromium 1280x800 + 390x844 with all network blocked (harness compiles the SFC with the
component's own @vue/compiler-sfc + sass; playwright from npx cache, chromium-1228 via executablePath).
NEEDS: user activates the new version in WeWeb, staging check on a real phone (possible overlap with fixed bottom
elements on Discovery / My Wedding mobile - mock only), then publish. Re-read the shown->scored rate ~1 week after.
Deploy-repo gotcha: ww-nps-survey stores LF; copying a CRLF working-tree file makes a whole-file diff - normalise to LF.

## 2026-10-04: corner card LIVE (live wwElement chunk has nps-dock, no scrim) - and 0 ratings
nps_shown unique/day 09-30..10-03: 12,16,16,10; nps_score_submitted 0 since 09-29 (only 3 ever, all modal era).
DAU ~230, PDF openers ~80/day. User goal: >= 1 answer/day, "force that experience a bit more". Options put to user.

## 2026-10-04: modal back + ask on return + loose gates (tulletogether b8885e6 = marthv/ww-nps-survey 921f4b6)
User picked "widen + modal at the right moment", HOLD on one-click email stars. presentation prop (modal default |
corner), askOnReturn (card waits while tab hidden - old build showed it in the hidden tab 5s after the PDF opened),
returnDwellSeconds 1.5, defaults age 1d / 1 moment / any page. nps_shown now has presentation + trigger.
Headless harness in session scratchpad (build.mjs + run.cjs, 16/16). NEEDS: user activates new version; instance
95bea395 has OLD explicit values (minValueMoments 2, minAccountAgeDays 3, promptPages "Vendor Discovery,My Wedding")
that override the new defaults - clear/set them; then staging check + publish. Re-read shown->scored after 3 days.

## 2026-10-07 read: new build live (presentation=modal on all nps_shown since 10-05)
Since 10-04: 82 shown / 70 users, 3 scored (4.3%), 75 dismissed at step 1. trigger dwell 73 / return 6. Pages: Discovery 77,
My Wedding 2. Editor props: promptPages "Vendor Discovery,My Wedding, Cost Research", age 1d, moments 1, dwell 4s, askOnReturn on.
LIKELY CAUSE of few return-triggers: PDFs open from the vendor pages, which are NOT in promptPages. Table 77 = 6 rows; only
comment ever: "no pricing shown" (free user, 1 star). nps_score_submitted lacks `trigger`. All promoters declined share.

## 2026-10-08: comment on the star step + promoter "what made it worth it" (tulletogether ba7f32c, LOCAL, not pushed)
Share grid/osName/share-clicked removed. Score still posts on first tap + locks (ep308 never rewrites score).
Headless 38/38. Deploy = push to marthv/ww-nps-survey (normalise LF) -> user activates + publishes. ep308 Slack still
prints "(did not share)" for promoters without a comment - cosmetic, needs an ep308 edit.
- 10-08 PROD VERIFIED: live wwElement chunk has "made it worth it", no "Quick share". Re-read ratings + comment rate ~10-11.

## 2026-10-11 read: comment-on-star-step build did NOT help
Since 10-08: 79 shown, 74 dismissed at step 1, 1 real score (row 25, 10-09). 9/28..10-11 total: 6 real scores ever
(rows 16,17,21,22,23,25; 24 = Vivek), ~20 shown/day (unique). Triggers since 10-08: return 59 / dwell 19; iOS 42, Mac 25.
User wants 2-3/day and asked to make it impossible to close without rating + comment. Math: at ~20 shown/day a voluntary
card needs 10-15% response (we've had 1-4% across 4 formats); forced = up to ~20/day. Put trade-offs to user, awaiting go.

## 2026-10-11 forced-on-return build (LOCAL, uncommitted, NOT pushed)
- User approved: return-from-PDF card has no close/Esc until star + comment >= 10 chars (props requireAnswerOnReturn=true, minCommentChars=10). Dwell card unchanged (closable, comment optional). nps_shown/nps_completed carry `blocking`.
- Headless 33/33 PASS (scratchpad npsforce). Awaiting user OK to push to marthv/ww-nps-survey, then activate + publish.
- Expected volume: return trigger 12/15/18/13 unique/day 10-08..10-11 (~15/day) -> those are the people forced. Watch: blocking completions/day, tab-close rate, purchases week after.
- 10-11 later: user asked "second PDF at the earliest". Added minPdfTripsOnReturn (default 2): a PDF trip = tab hidden within 5s of a value-moment bump (either order), each moment counted once; return card waits until trips >= 2 (pendingOnReturn kept). nps_shown carries pdf_trips. Headless 37/37. Data: 10-08..10-11 funnel viewed_vendor_pdf -> nps_shown[return] = 44 users, only 8 had 2 PDFs first; 177/299 PDF viewers (59%) open a 2nd within 3h -> forced est ~8-10/day (LOW confidence). Still NOT pushed.
- Blank "Name/Email/Message" contact-form post in #feedback 10-11 18:15:35 ET (2nd ever; 09-11 one came 41s after a real msg). Source endpoint not traced (Xano MCP DNS down). Fix = reject empty server-side + required fields; awaiting user.
- 10-11 SHIPPED: marthv/ww-nps-survey 029a74c (tulletogether 71f995e). User must activate new version in WeWeb + publish. Then verify live chunk has "more character" / minPdfTripsOnReturn; read nps_shown{blocking:true} + completions after ~3 days.
- 10-11 also built (editor only, UNPUBLISHED): onboarding question "What do you want most from Tulle?" multi-select (Onboarding Popup bbdfe17d, step 1, element 6f3ecf5e, optional) -> Mixpanel onboarding_goals_selected + people tulle_goals (workflow 7a1c0e52). New popup "What Stopped You" 53f82e59 (8 one-tap reasons -> what_stopped_you {reason}; skip -> what_stopped_you_skipped), opened once/session from vendor-page PDF limit "Maybe later" (workflow c1e7d006 edited; backup weweb_pdf_limit_dismiss_c1e7d006_pre_whatstopped_2026-10-11.json).
- 10-11 night: Xano ep310 POST /what_stopped_you/answer (group 4 Weweb, auth=false, reason whitelist of 8 codes, vendor_name<=200) -> Slack via $env.nps_slack_webhook_url (#feedback). LIVE + smoke-tested: ""/"hack" -> 400, valid -> slack 200, message seen in #feedback 22:30 ET (test row "TEST from Claude"). WeWeb popup 53f82e59 now posts to it; "vendor isn't on Tulle" -> step var 678fc946='vendor' -> text box 4a2e6dba + Send (workflow 3e7a9c41). User said: Slack + vendor box only; discounts/+PDFs/Brevo/partner NOT built (my rec: read 2 wks of answers first; ~10/day hit Maybe later max).
- Xano MCP DNS fails inside the session but curl works: harness scratchpad/xmcp/xmcp.sh (curl --resolve to 34.170.63.102, header file from .mcp.json, never printed).
- SECURITY: ep94 contact_us_feedback (group 3 slack) is an OPEN RELAY - POSTs client-supplied text to client-supplied url; the Slack webhook therefore ships in the browser. Likely source of the blank #feedback posts. Not fixed (raise with user).
- 10-11 late: onboarding goals question MOVED to end of step 2 (before Back/Finish), new uids field 94a67b27 / select 10ad0df7, 6 options: one_vendor_price, compare_area, plan_everything, contact_negotiate, just_browsing, other; tracking workflow 4b8e2d71 (old 6f3ecf5e + 7a1c0e52 deleted with the element). Unpublished.
- 10-11: International selectedState error ALSO from Vendor Discovery left-panel "Location Dropdown Select" ec03ead0 (multi) workflow 9f1c716b -> fixed (last picked as string). Backup weweb_location_dropdown_update_state_9f1c716b_pre_string_fix_2026-10-11.json.
- 10-11: added 'Figure out my budget' (set_budget) as 3rd goal option (7 choices incl Other). User publishing 10-11 night -> next session: verify live /data json + Mixpanel events (map_marker_clicked, map_popup_cta_clicked, searched_map, onboarding_goals_selected, what_stopped_you), / no longer logs out, re-login rate vs 10.8%.
- 10-11: ep310 v2 adds reason maybe_later (":door: Paywall walk-away") - smoke 200, junk 400; backup ep310_what_stopped_you_v1_pre_maybe_later_2026-10-11.xs. WeWeb c1e7d006 ("Maybe later" on vendor-page PDF limit) now posts maybe_later on EVERY tap, then opens What Stopped You once/session. Unpublished.
- 10-12 ~04:05 UTC: Xano table 1 +column onboarding_goals (text list) via meta REST POST schema/type/text (48 cols, no existing col changed; backup table1_user_schema_pre_onboarding_goals_2026-10-12.json). ep311 POST /user/onboarding_goals (group 4, auth=user, whitelist 7 codes + unique) LIVE, tested with REC_TEST_TOKEN (junk dropped). WeWeb workflow 4b8e2d71 edit to ALSO POST to ep311 FAILED (WeWeb MCP DNS ENOTFOUND x4) - still pending; Mixpanel-only goals fix (reads select value) is in editor.
- 10-12: walk-away LIVE (first real post 12:27 UTC). #feedback "duplicates" = NOT new tabs (ep310 request history, workspace 1 query 310): (a) by design maybe_later post + popup reason post 1-5s later; (b) maybe_later re-posted on each re-tap of a locked PDF (3s-62s apart, same IP/UA, all iPhone). 9 users -> 18 posts by 20:45 UTC. Also `page` = "/vendors/undefined" on 8/9 users (vendor id not bound). Fix proposed (Slack once per session, Mixpanel every tap), awaiting user. Slack MCP was on Healthie; user re-authed 10-12 -> now Tulle. #feedback = C07JML6NMLM (private). Slack posts matched ep310 log 1:1.
- 10-12 eve: user OK'd dedupe fix. Root cause of undefined vendor: path is literally /vendors/undefined, vendor id is in ?Vendor_ID= (name var fd02dbef, id var 79e6edb5). Full backups of c1e7d006 / 5c2e8a17 / 3e7a9c41 in tulletogether/.claude/xano_backups/weweb_whatstopped_workflows_pre_dedupe_2026-10-12.json. User then approved; 3 workflows EDITED in editor 10-12 eve + re-read confirmed (c1e7d006: Mixpanel paywall_maybe_later every tap w/ tap_in_session, Slack only 1st tap/session via sessionStorage tulle_maybe_later_taps; all 3 send page = "current_venue_name (Vendor_ID)"). UNPUBLISHED - user must publish, then verify live /data json + next #feedback posts show venue name.
- 10-12: assistant user 827 rated 2/5: 3 rated questions went via no-chat_id legacy path (text NOT saved, likely no history), profile has no state -> assistant kept asking for state (0 cards on 3/4 replies); "glasshouse" not mapped to Greenhouse style tag (rec_service/agent.py:356). Fixes proposed, not applied. See [[project_ai_recommendation_engine]].
- 10-12 late: user PUBLISHED. Live /data/e49b4e74...json VERIFIED has new code (tulle_maybe_later_taps, 3x vendor label). Found 4th poster: Skip button 8d4f1b63 (element 539b985e, vendor step sends vendor_missing) still old pathname -> fixed in editor (backup weweb_whatstopped_skip_8d4f1b63_pre_vendorlabel_2026-10-12.json), NEEDS ANOTHER PUBLISH. No real post since publish yet -> next session: confirm #feedback posts show "Venue (Vxxxx)" + one walk-away per person, Mixpanel paywall_maybe_later.

## 2026-11-07: detractor comment required (tulletogether a6a91ce = marthv/ww-nps-survey d97c609, PUSHED 11-08 00:58 UTC)
Prop commentRequiredAtOrBelow (default 3, 0=off): after a 1-3 star tap, no close/Esc until comment >= minCommentChars (10).
Star still posts on tap. nps_completed carries comment_required. Also Trustpilot thank-you ask (reviewUrl/reviewMinScore, 7964bf1, 10-24).
NEEDS: owner activates new version in WeWeb + publishes; then verify live wwElement chunk has "What went wrong, or what were you hoping".
