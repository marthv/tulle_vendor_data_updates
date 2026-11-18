---
name: project-estimate-card-whatif-2026-11-18
description: "Kate's 11-16 Slack feedback on the Planner cost estimate (re-asking, location only via settings) -> guests+state what-if on the estimate card; built + committed locally, NOT pushed"
metadata:
  node_type: memory
  type: project
  originSessionId: a03a31fa-d97b-4e0c-8697-0f5cdbf6e87b
  modified: 2026-11-18T04:58:00.171Z
---

Kate (#C065B5YEG1G, 2026-11-16 15:58/15:58/16:00 ET): estimate card can't be adjusted - you re-ask for a new guest count, change location only in settings, cost tools hard to find (Tools menu only); maybe people prefer asking in chat - measure vs before. Owner replied "will respond later this week".

Evidence (Mixpanel 3963130, unique users/week, internal NOT excluded): old calculator `calculate_estimate` 38-53/wk Aug-Sep, fading to 0 by 11-09 (pages folded into Planner); Planner `rec_free_tool` (ALL cost tools, not just estimate) 10 (wk 11-09), 5 (11-16 partial). Typed estimate questions in chat are NOT counted by any event.

Built (owner said "deal with this"), local commits only:
- Server worktree `tulle_rec_est_inputs` branch rec-est-inputs 1576c60: estimate widget gets `state_options` (table-4 names with an average; widget only, not facts). Suite 1054 passed + known next_step_chips failure (test_et_day_boundary flaked once, passes alone and on rerun).
- Component worktree `tulle_chat_est_inputs` branch est-inputs 6c702b4 (on ww-planner-chat/main 03d4ff5): Guests box + State select + Update on the card -> /rec/tool noSave what-if; note "Just a what-if"; event `rec_estimate_whatif {changed, guests, state}`. Build exit 0. node_modules is a junction to tulle_chat_whole_wedding. NOT rendered/tested in a browser.
- Go-live order: push server main -> push component -> owner activates + publishes. Registry entry when live.
- Not done: discoverability (estimate entry outside Tools), inline state picker when the profile has no state.
