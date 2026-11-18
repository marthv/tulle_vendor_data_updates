---
name: project_planner_feedback_onetap_2026-10-27
description: "Planner feedback dried up 10-21 (card shown to 58 free users, 1 sent: needed rating + keep-using + 10 chars). 10-27: one-tap rating LIVE on server (49d3c83), REC_FREE_FEEDBACK_BONUS=1, REC_BETA_FEEDBACK_AFTER=2; WeWeb card edits need owner publish"
metadata:
  type: project
---

Diagnosis 2026-10-27 (owner: "we used to get feedback and now we don't"): NOT a Slack break. Free users are capped at
REC_FREE_REFINES=2 since 10-24 and paid check-in was after 3 questions, which almost nobody reaches (3+ questions:
7 users 10-14..10-20 -> 2 users 10-21..10-27). Mixpanel rec_feedback_prompt_shown 10-10..10-27: free 58 users shown,
1 submitted; paid 5 shown, 2 submitted. Form demanded rating + keep-using + >=10 chars (server 400 text_too_short).

Shipped 10-27 ~23:56 UTC (owner yes): rec-engine 49d3c83 on main (rating alone accepted; Slack "(rating only)"),
Railway REC_BETA_FEEDBACK_AFTER 3 -> 2, REC_FREE_FEEDBACK_BONUS 0 -> 1 (one more question for the rating).
Verified live with test acct 34429 (fbtest+freelimit1027b@tulletogether.com; 1027 without b is unused):
rating-only 200, questions_left 0 -> 1, Slack post landed. WeWeb Planner page (dae212b5) EDITOR ONLY, needs publish:
tap a pill = send (unless "Tell us more" open), Send needs rating only, body "Tap a rating to unlock 1 more question",
text label "Anything else? (optional)". Backup .claude/weweb_backups/planner_feedback_card_pre_onetap_2026-10-27.md.
Read after ~1 week: rec_feedback_submitted / rec_feedback_prompt_shown by tier (baseline free 1/58, paid 2/5).
Rollback: REC_FREE_FEEDBACK_BONUS=0, REC_BETA_FEEDBACK_AFTER=3, revert 49d3c83.
Side bug seen: "And one with a rooftop?" triggered a "Venue not in Tulle" post (name lookup treats phrases as names).
