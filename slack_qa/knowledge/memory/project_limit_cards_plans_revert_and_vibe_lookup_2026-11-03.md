---
name: project_limit_cards_plans_revert_and_vibe_lookup_2026-11-03
description: "11-03: PDF limit + last-PDF warning cards reverted to 'Check out our plans' (submit-PDF link hidden) IN EDITOR, owner PUBLISH; vibe/style messages misread as venue names fixed on branch rec-vibe-lookup cd4b209 (NOT pushed); Planner->Google Sheet log proposed, not built"
metadata:
  node_type: memory
  type: project
  originSessionId: 0f4bd34b-8e11-434d-b330-76db01f2cbf9
  modified: 2026-11-03T23:17:04.455Z
---

**Limit cards (owner 11-03: the 10-24 "keep going free" copy may have cost payments, didn't lift PDF submissions).**
Edited in WeWeb editor, both Venue e49b4e74 + Other Vendors d1d9bbf6 pages, read back OK (14 elements):
limit heading "See what vendors actually charge.", body "Members see every pricing PDF on Tulle - real fees...",
green CTA "Check out our plans" (workflow unchanged -> Membership page), submit-PDF link d22cf2f0/92afca4b hidden
(conditionalRendering false); warning card heading/body back to pre-10-24, CTA "Check out our plans". Dismiss + What
Stopped You untouched. Backup weweb_pdf_limit_cards_pre_plans_revert_2026-11-03.json. **Owner must PUBLISH.**
Old library popup 48be8a6c still says "or share a pricing PDF you've received for a free week" (mostly suppressed).
Evidence (Mixpanel weekly uniques): wall CTA click/wall 32%/26%/27% (wks 10-05/12/19) -> 25% (10-26, first week of the
free copy); purchases 39/39/34 -> 34; upload clicks 20/24/24 -> 25 (no lift). Payment harm NOT shown (confounded by
Planner open week, which also cut wall views 119 -> 83). Read again 1-2 wks after publish.

**Vibes:** chat 1170 "Only gardens and nature please" hit lookup.detect (name fast path) -> "We don't have Only gardens
and nature please in Tulle yet". Fix: lookup.DESCRIPTORS + _descriptive_only(); garden/nature mapping in search_venues
docstring. Replay of 30d (2,659 msgs): 214 lookups unchanged, 23 move to the model (style/needs; 2 maybe names "The
Greenhouse", "The bay view"). Worktree C:\Users\vivek\Documents\GitHub\tulle_rec_vibe, branch rec-vibe-lookup, commit
cd4b209, 618 tests pass (1 pre-existing fail test_flag_off_prompt...). NOT PUSHED - needs owner yes. Model's vibe
filtering on that exact message NOT verified live.

**Vibe fix PUSHED + LIVE (cd4b209, 20:15 UTC 11-03)**: test acct 14998 sent "Only gardens and nature please" -> Ella's Garden,
Houston Arboretum And Nature Center, Houston Botanic Garden ("I kept this to gardens and nature near Houston").
**Google Sheet log BUILT, NOT LIVE:** sheet "Tulle Planner Activity Log" https://docs.google.com/spreadsheets/d/[token]
(owner's Drive). Server: rec_service/sheet_log.py + hooks in app.py + POST /rec/event, commits ba871e4 + 5b103e1 on branch
rec-vibe-lookup in worktree tulle_rec_vibe - push to main was BLOCKED by the permission classifier (owner pushes or allows).
Off until Railway rec-engine has REC_SHEET_WEBHOOK_URL + REC_SHEET_SECRET. Apps Script = rec_service/planner_sheet.gs (owner pastes,
sets SECRET script property, runs setup, deploys web app). Component 175818c (View vendor/save -> /rec/event) PUSHED, owner ACTIVATE+PUBLISH.
Python urllib here fails TLS (stale local CA bundle, Railway cert valid to Dec 26) - use curl.
[[project_planner_chat_component_2026-10-31]] [[project_focus_2026-10-31]] [[project_pdf_warning_view3]]

**2026-11-03 ~21:10 UTC ALL LIVE (owner published + activated):** live /data json for both vendor pages has "Check out our plans" x2, no "Keep exploring free", submit link conditionalRendering False; server main 5b103e1 (owner pushed); planner page chunk has /rec/event vendor_click + save/unsave. Sheet got real rows from users 35013/35030 within a minute of deploy (all columns filled).

**Sheet skips internal accounts BY DESIGN** (sheet_log.INTERNAL_IDS + vivek.marthi*/@tulletogether.com). 11-03 owner thought the sheet "stopped" - it was their own test accts (14998, 31619) being skipped; tell the owner this up front. Owner had 31619 ([email]) removed from the sheet skip list: commit 9bc1444 pushed to main ~23:00 UTC 11-03 (sheet only; 31619 still internal for real-user numbers). **Device + Browser columns (N:O, after Notes)**: server e303ca7 PUSHED to main 23:15 UTC 11-03 (UA via ASGI middleware -> sheet_log.UA contextvar). Columns stay blank until owner pastes new planner_sheet.gs + Deploy > Manage deployments > New version (URL unchanged). Verify = a real row with N:O filled. test_prefetch_cache::test_agent_prefetch_uses_caches_and_reports_parts is FLAKY (fails ~3/4 runs alone, independent of change).
