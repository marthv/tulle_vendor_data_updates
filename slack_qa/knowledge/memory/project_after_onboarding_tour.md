---
name: project_after_onboarding_tour
description: 2026-10-16 after-onboarding tour (Discovery card -> vendor page PDFs, PI, Assistant) replaced the vendor-first-visit tour; works in preview; editor-only until published
metadata:
  node_type: memory
  type: project
  originSessionId: a9483c7d-0298-48d3-b53d-715962768463
  modified: 2026-10-16T05:12:16.841Z
---

Built 2026-10-16 in the WeWeb editor (NOT published as of writing). Replaces the old 4-step vendor_first_visit tour.

- Part 1: tulle-tour `3ee46d5b` in the SHARED Navigation Bar Section (user dragged it there; enabled formula checks path category-specific-page). Free + `Wedding_Guest_Count` set (saved by onboarding Finish; Planning_Phase is saved at step 1 so never use it). One step on first vendor card (ids tour-vcard-a..e on 5 card layouts), doneLabel "Open a vendor ->", step `clickOnDone: true` clicks the card. storageKey tt_tour_vendor_v1.
- Part 2: vendor instances da263895 (venue) / 33629def (OV): steps tulle-pdf-list then tour-pi-panel; `requireCompletedKey: tt_tour_vendor_v1`, 15 min; storageKey tt_tour_vendor_part2_v1.
- Component repo marthv/ww-tulle-tour: ec31caa clickOnDone, 1786029 wait-for-targets + requireCompletedKey, d8f24e3 `<key>:last` written even under Force open + continuation ignores `enabled`. User must ACTIVATE each version in WeWeb.
- WeWeb MCP cannot place this github component nor attach workflows to its events (triggers unknown to MCP) - put logic in the component.
- Force open: I turned it OFF, then the user turned it back ON to preview (a5c6e5a makes forced previews repeatable). 2026-10-16 user pushed to STAGING. Before production: confirm forceOpen=false on 3ee46d5b (grep live /data/0d69d71b...json for "forceOpen":true near tulle-tour). Part 2 now 3 steps: PDFs, PI, Tulle Assistant (tour-assistant / -m).
- TRAP: editElement on `content.default.steps.N` NULLS every other index - always write the whole steps array, then read back.
- Baseline: 196 of 684 onboarded users (29%) started the old tour in the week to 10-15.
Backups: .claude/xano_backups/weweb_vendor_tours_pre_disable_2026-10-16.json, weweb_discovery_tour_targets_pre_ids_2026-10-16.json. Related: [[project_signup_dropoff_2026-10-07]]
- 10-16 midday team feedback -> EDITOR (unpublished): Part 2 steps now PDFs -> Assistant (PI step removed; locked for free users). Wrote [pdfs, assistant, null, null] - a shorter array MERGES by index (left a duplicate step) so pad with nulls. Part 1 enabled also waits for onboarding_loader 387a63e6 === false (Finish refetches collections before closing; tour used to stack on the popup). Component fc6b9a8: desktop re-centres target after late layout shift (user must ACTIVATE). Backup weweb_tours_pre_pi_removal_and_popup_wait_2026-10-16.json.
- Team (Kate) on forced PDF open / +1 free view: owner said not now - Kate wanted to click the PDF on her own once the list was highlighted.
- Gold chip user-facing labels renamed 'Hot venues' (Discovery labels, chip, legend, 2 card badges, both sort selects 'Hot Venues First', mobile Filter Popup ed90717a label 53a296ff). Backup weweb_gold_labels_pre_hot_venues_2026-10-16.json.


**10-17 EDITOR (unpublished) Discovery changes:**
- Hot vendor map markers + legend dot gold -> lavender #C7A3F5 (map 43235d7f markers formula; legend bd68bb4a). Backup xano_backups/weweb_hot_vendor_marker_gold_pre_lavender_2026-10-17.json.
- Filter chips (9): tapping the chip now REMOVES the filter and re-runs the search (reset var, CTA back to 'Search Vendors', loading on, page 1, fetch 5c91206d + a5f0a42c, loading off) instead of toggling the Filters rail. X icons edited to the same chain with stop-propagation kept (Hot icon's wf deleted - bubbles). deleteElementWorkflow REJECTS non-uuid workflow ids; editFrontendWorkflow rejects type "variable" (use update-variable). Backup weweb_filter_chips_pre_tap_removes_2026-10-17.json.
- Onboarding guest count -> guest filter: NEW 2nd click workflow b7c1e2a4 on Onboarding Popup (component bbdfe17d) Finish button 77dea4e0: min_capacity ac799270 = guests, capacity_ceiling 8a3491e2 = guests+100 (>500 -> 10000). Owner chose guests..+100 over +/-25/50 (max-capacity semantics). The main Finish wf cbb346d5 has plugin actions (MCP can't edit) - it fetches 5c91206d after PATCH+fetchUser, so the parallel instant var sets land first (UNVERIFIED race; verify on staging). ~30% venues have no capacity and drop out under any guest filter.

**10-20 ASSISTANT INTRO TOUR (editor, UNPUBLISHED):** shared instance 3ee46d5b now runs TWO tours via page-aware js formulas
(steps/enabled/storageKey/tourName/doneLabel; onAssistant = page dae212b5 or /assistant path). Assistant: 4 steps -> ids
tour-asst-tabs (02068476), tour-asst-input (Chat Input Area 6833112e), tour-asst-budget (701ad8fb), tour-asst-chats (3e0c4421);
storageKey tt_tour_assistant_v1, tourName assistant_intro, done "Got it"; enabled = signed in + Wedding_Guest_Count + loader 387a63e6
false + recTopTab assistant + Chats menu var 9fd0bc7d closed (all members, paid too). Other pages: Discovery tour values unchanged.
forceOpen false. No new component version. Backup .claude/xano_backups/weweb_assistant_tour_pre_2026-10-20.json.
Caveat: component watches enabled/forceOpen/steps only (not storageKey) - fine since steps+enabled change together on navigation; UNVERIFIED in browser.

**10-20 ASSISTANT MINI TOUR (editor, UNPUBLISHED):** reuses shared nav instance 3ee46d5b with page-aware formulas (assistant page
dae212b5 or /assistant -> 4 steps tour-asst-tabs/-input/-budget/-chats on 02068476/6833112e/701ad8fb/3e0c4421, storageKey
tt_tour_assistant_v1, analytics assistant_intro, Done "Got it"; every other page gets the Discovery tour's prior values). Force open OFF.
Backup weweb_assistant_tour_pre_2026-10-20.json. RE-CHECK the Discovery tour still starts on Discovery after this.
**10-20 SPLIT (owner asked for two tours):** 3ee46d5b restored to Discovery-only (verified vs backup; Force open was ON -> OFF). Owner
dragged two tulle-tour instances into Assistant page "Main Recommendation Section": "Assistant Tour" (tabs -> input+paperclip -> Chats
-> Budget tab, last step clickOnDone "Show me my budget ->", key tt_tour_assistant_v1) and "Assistant Budget Tour" (budget hero ->
packages actions row, requireCompletedKey tt_tour_assistant_v1 15 min, key tt_tour_assistant_budget_v1). Force open OFF both. Component
limits: clickOnDone only on LAST step; steps whose targets aren't rendered at start are skipped -> chain tours. Props saved in
weweb_assistant_tour_props_2026-10-20.json. Tour1->tour2 hand-off NOT yet seen in a browser.

**10-25 PER-USER 'seen' KEYS (editor, unpublished):** storageKey/requireCompletedKey on 3ee46d5b, da263895, 33629def,
64570531, 51e65d20 are now js formulas: Discovery + vendor part 2 = '<key>_u'+user.id unless this browser already has the
legacy v1 key (then legacy key - no re-show); assistant tours = '..._v2_u'+uid (no legacy). If wwLib is unavailable in
bindings the try/catch falls back to the legacy per-browser key (safe). Verify after publish: localStorage gets
tt_tour_vendor_v1_u<id>; grep live main.js / page json for tt_tour_vendor_v1_u. Test in INCOGNITO (editor browser has v1).
Backup weweb_tour_peruser_keys_pre_2026-10-25.json.

**10-26 ONCE PER ACCOUNT (owner approved):** Xano table 1 +`tours_seen` (text list; meta REST schema/type/text, 52 old cols unchanged; backup table1_user_schema_pre_tours_seen_2026-10-26.json). ep328 POST /tour/claim (group 4, auth user, key [a-z0-9_]{1,80}) LIVE + tested (401/400/true/false; owner 14998 now has 'zz_test_claim'). Component ww-tulle-tour branch tour-claim (9207953 + b8472a5, NOT pushed; bundle .claude/ww-tulle-tour_tour-claim_2026-10-26.bundle; monorepo branch tulle-tour-account-claim d47a071): props claimUrl/authToken, claim at auto start, key minus `_u<id>`, error/2.5s -> local fallback; Mixpanel tour_suppressed + tour_started.claim. All 6 instances (3ee46d5b, da263895, 33629def, 64570531, 51e65d20, 2bb9bedb Cost Tools) bound in EDITOR (unpublished); backup weweb_tour_instances_pre_claim_2026-10-26.json. Mixpanel 10-25..26: 27 repeat user-tour pairs / 24 users; only 6 pairs cross-device, ~19 same-device restarts before completion (claim-at-start stops those too = abandoned tours never re-show).
