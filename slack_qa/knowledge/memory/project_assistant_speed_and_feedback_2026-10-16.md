---
name: project_assistant_speed_and_feedback_2026-10-16
description: "Tulle Assistant speed fix LIVE (51a230f, ~6s typical, was ~9-14s) + thumbs-down pills replaced by free text (editor, unpublished)"
metadata:
  node_type: memory
  type: project
  originSessionId: a9483c7d-0298-48d3-b53d-715962768463
  modified: 2026-10-17T01:47:45.109Z
---

**CORRECTION 10-17: PRODUCTION is NOT ~6s.** Mixpanel rec_server_request status=ok 10-16..17 (n=48): opening median 18.2s / p90 40.7s, refine 22.2s / p90 27.2s. Median model_ms 9.4s (~940 output tokens), tools_ms 6.7s opening / 12.2s refine (Xano search/pricing calls), prefetch ~0.2-0.7s. The ~6s figure was the 10-eval A/B, not real traffic. Per-tool timing NOT logged yet (only per-turn sums).

**Speed (LIVE 2026-10-16, Railway rec-engine 51a230f, branch rec-speed in worktree ../tulle_rec_speed):**
- Cause: after `present_recommendations` was accepted, the SDK tool_runner still made one more model turn whose text was discarded (~2-3s + full input cost). Fix: run each turn's tools right after it (`runner.generate_tool_call_response()`, cached), break when `final` is set. A bounced present_recommendations still gets its retry.
- A/B (10 evals x2, free tier, back to back): p50 8.6/9.1s -> 6.1/6.8s; mean 11.1/9.7 -> 6.4/6.9; cost $0.027 -> $0.017; checks clean 10/10, 9/10.
- **CORRECTION: thinking was NOT the cost.** think_chars = 0 on every turn at effort=low; the hidden output is card JSON in tool args. REC_THINKING switch exists (default = API default). Kill switch REC_EARLY_STOP=0.
- Remaining time per answer: model picks search ~1.5s, search+pricing ~0.8s, writing the answer ~3.5s. Next lever = stream the reply, or prefetch the opening's search.
- rec_server_request now has model_ms / tools_ms / model_turns / prefetch_ms / output_tokens.
- Main repo checkout (branch rec-free-feedback-unlock) has someone's UNCOMMITTED edits in app/config/guards/xano - don't touch.

**Thumbs-down feedback (EDITOR, unpublished):** owner wants raw words, not pills. Rec Rate Reasons 1b9fb4ce repeat -> single item; chip e2af568c hidden; added textarea 08ce7eaf + Send button 31a934cf (wf 5e1a7c20) -> rec_answer_reason {reason:'free_text', text (255), text_length}. Backup xano_backups/weweb_rec_rate_reasons_pre_freetext_2026-10-16.json. Related: [[project_ai_recommendation_engine]] [[feedback_pdf_opens_money_metric]]
- 10-16 ~02:30 FIX: box didn't render - WeWeb repeats only the FIRST child of a repeated div (the hidden chip). Removed the repeat on 1b9fb4ce (children now textarea, Send, hidden chip); wf 5e1a7c20 now reads the turn from context.item (falls back to .parent) and fire-and-forgets POST /rec/answer-feedback with the Bearer token.
- Server: rec-engine 4aa76a4 LIVE (endpoint 401s unauth, CORS ok) - posts '*Tulle Assistant thumbs down*' + question + answer snippet to #feedback via REC_FEEDBACK_SLACK_WEBHOOK; Mixpanel rec_answer_feedback. Slack delivery UNVERIFIED until a real Send. rec_service tests: 15-19 failures pre-exist at 51a230f (flaky count), none new.
- 10-16 ~03:00 local: VERIFIED by owner in editor - thumbs-down text AND thumbs up both land in Slack #feedback. Server 156e2d4 (vote=up accepted, comment optional). Send wf 5e1a7c20 reads click via context.event (bare `event` was undefined -> silent 'empty' skip) with page-search fallback; logs '[tulle] answer-feedback'. Up wf 31117257 step 4 posts vote:'up'. Both POST from the editor too (Mixpanel still skipped there). Owner publishing to STAGING 10-16; prod publish pending.
- Owner 10-16: no streaming; forced paid feedback NOT wanted (paid check-in stays optional, free stays forced).

