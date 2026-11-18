---
name: project_assistant_usage_review_2026-10-19
description: "Tulle Assistant usage review 10-03..10-18: 98 real users / 131 questions; top fixes = 'Show 3 more' ignores constraints, region/radius location, free-tier budget policy; ZERO real thumbs events"
metadata:
  node_type: memory
  type: project
  originSessionId: d461cc1d-f540-4ec1-bcdd-000d8a6a23c5
  modified: 2026-10-19T01:58:52.164Z
---

Read-only review 2026-10-19 (tables 42/43/81 + Mixpanel; hand-coded + 3 LLM graders = directional). Scratchpad assistant_review/ (ephemeral).

- 98 real users, 134 chats, 131 questions (48 users asked; 50 only saw opening; 3 users = 25% of questions), 43 "Show 3 more".
  Also exclude acct **31305** (vivek.marthi email in Mixpanel) - not on the internal list before.
- Answered 55% / partial 34% / not 11%; "can't" replies 42%. After 10-14 (free mode): answered 62% -> 46%.
- Categories: location 30% (19 can't; region names like Catskills/Hudson Valley/Hill Country + drive-time), price 26%,
  style 20%, named venue 18% (6 not in data; "74 wythe" false miss - spacing-sensitive lookup), capacity 11%, international 8%,
  lodging 7/7 can't, non-venue 9 (1 answered).
- **"Show 3 more" bug:** more_cards = original search pool order, ignores later budget/location -> 23/43 partial (11 over budget 2-8x).
- Free tier strips price filters -> "can't filter by price on this plan" (owner decision pending).
- Outcomes (not causal): assistant users 52% open PDF within 1 day vs 58% all signups; card click 31%, 90% of clickers open PDF.
- **CONTRADICTS earlier notes:** rec_answer_rated / rec_answer_reason events DON'T EXIST in Mixpanel; rec_answer_feedback only internal
  -> zero real thumbs data (thumbs publish state unverified). REC_FREE_REFINES=1 in code (6f14a58), Railway value unverified.
  origin/main has photographers+entertainment since 10-11 (b233d3c newest; deployed commit unverified). Server latency median 14.8s
  over 10-03..10-18 (opening 15.9, refine 13.8; p90 28s).
- Beta feedback: 11 rows, mean 3.0/5 (wants more/varied options, accurate costs).
[[project_ai_recommendation_engine]] [[project_assistant_speed_and_feedback_2026-10-16]] [[feedback_pdf_opens_money_metric]]
