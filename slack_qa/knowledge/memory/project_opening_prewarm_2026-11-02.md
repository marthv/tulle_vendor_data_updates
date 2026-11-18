---
name: project_opening_prewarm_2026-11-02
description: "Planner opening prewarm (POST /rec/prewarm) built 11-02 on branch rec-prewarm (worktree tulle_rec_prewarm, commit 3a7ad85) - NOT pushed; WeWeb onboarding-finish call not added; owner goal = time-to-value"
metadata:
  node_type: memory
  type: project
  originSessionId: ef38e591-dfc1-4a78-8b2d-14d683988a45
  modified: 2026-11-02T19:16:38.591Z
---

**Owner 2026-11-02:** "my biggest concern is that the first prompt takes so long to load" / "time-to-value needs
to decrease a lot". Approved: build step 1 (prewarm after onboarding) + reuse-miss logging; step 3 (stream text for
free members) dropped - REC_FREE_PRICING defaults to light, so free members already stream (UNVERIFIED on Railway;
I had first told the owner they saw a blank screen - wrong, corrected in-session).

**Evidence at build time:** openings since 03:49 UTC 11-02: p50 4.5 s / p90 7.4 s (n=36), search 0.07 s, rest =
model writing ~400 tokens. 9 of 20 multi-opening user-days regenerated (reuse store missed - reason unknown, hence
reuse_miss logging). Opening cost mean $0.031.

**Built (3a7ad85, branch rec-prewarm off origin/main 1518511, 601 tests pass, 1 pre-existing failure
test_flag_off_prompt_and_chips_identical_to_origin_main also fails on clean main):**
- POST /rec/prewarm (Bearer user token) -> {"status": started|limit|off}; job on its own 4-thread pool runs the
  opening via _run_guarded(prewarm=True) into opening_reuse.STORE (src "prewarm"). Max 3/couple/ET day
  (REC_PREWARM_MAX_PER_DAY), REC_PREWARM=0 kill.
- Usage row kind "prewarm" (cost, never counted: guards._prompts excludes it). First visit served from it writes the
  normal "opening" row with model "prewarm", cost 0, latency = real wait; later visits = plain reuse (claim flips src).
- opening_reuse.INFLIGHT: an opening finding a same-fingerprint run in flight waits up to REC_PREWARM_JOIN_S=15 s
  (also collapses the double-mount duplicate openings into one model run).
- reuse_miss reason (none | day | age | fp:<keys> | join_timeout | joined_*) -> Railway log line
  "opening_reuse miss", Mixpanel rec_server_request.reuse_miss, table 79 key reuse_miss (DROPPED by Xano until a
  column is added - needs schema backup + owner OK).

**Still to do:** owner OK to push (deploy), then WeWeb onboarding Finish workflow: Custom JS fire-and-forget fetch
AFTER the profile save, token = [redacted] accessToken first ([[reference_authtoken_variable_email_only]]). Read after
1-2 days: share of new-member openings with model "prewarm" in table 79, their latency, prewarm rows never followed
by an opening (wasted cost). Step 2 (prewarm on sign-in for returning members) waits on the reuse_miss read.
Related: [[project_planner_chat_component_2026-10-31]], [[project_onboarding_finish_spinner]].

**11-02 ~19:30 UTC DEPLOYED (owner "ok"):** pushed rec-prewarm -> main (1518511..3a7ad85, verified ls-remote); live
POST /rec/prewarm unauthenticated = 401 (was 404 before the deploy). **WeWeb (owner "can you do this yourself?"):**
new 4th click workflow 5f2c9e41-7a3d-4b8e-9c61-2d0e8a4b7f13 "Onboarding Finish - prewarm Tulle Planner opening" on
Finish button 77dea4e0 in Onboarding Popup bbdfe17d (EDITOR, needs PUBLISH): custom-js start time -> wait 400 ms ->
while onboarding_loader 387a63e6 true (max 20 s, 250 ms ticks) -> if loader off -> native http-request POST
/rec/prewarm, Bearer = plugin accessToken || authToken, keepAlive, throw false. Main workflow cbb346d5 (Xano plugin
PATCH) untouched. Backup of the live element: tulletogether/.claude/xano_backups/weweb_onboarding_finish_77dea4e0_pre_prewarm_2026-11-02.json.
GOTCHA: WeWeb MCP returns a bare nginx 403 when a custom-js action contains fetch(...) with a URL + Bearer header
(content filter); a harmless payload passes. Use the native http-request/http-request action instead.
editFrontendWorkflow with a NEW id = "Workflow not found for edit" -> use createFrontendWorkflow.
Onboarding lands on Vendor Discovery (handleComplete), not the Planner - prewarm only pays off if they open it within 6 h.
