---
name: project_save_offer_60_forever_2026-11-11
description: "\"Too expensive\" save offer = 60% off Forever, 12h, + Brevo email (ep313 v3 LIVE 2026-11-11 05:14 UTC); WeWeb card edited, needs publish"
metadata:
  node_type: memory
  type: project
  originSessionId: 34930563-5246-4565-b223-1786355f5857
  modified: 2026-11-11T05:15:49.223Z
---

Owner decision 2026-11-11: replace the 20%-off/30-min "too expensive" offer with **60% off Forever, 12 hours, + email**.

- **ep313 `paywall/save_offer` v3 LIVE** 2026-11-11 05:14:44 UTC (apigroup 4, ws 1). Codes `TT60-XXXXXX`; Stripe coupon `tulle_save60_forever` (60%, once), promo code max_redemptions 1, expires_at +12h, min $100 (blocks 4-week, max $69). Forever only: NY/baseline $135 -> $54, CA/Intl $149 -> $59.60.
- Eligibility: never paid (`total_amount_paid` 0), not forever, no live access, one TT60 per account ever. Old TT20 holders who never paid ARE eligible once. Internal excluded except emails containing `+offer60test`.
- Email sent from ep313 itself via Brevo smtp/email (sender [email]), skipped if Brevo contact blacklisted. NO Make scenario, NO reminder email (deferred: needs a table-1 column + task).
- Rollback: `.claude/xano_backups/api313_ep313_save_offer_LIVE_pre_60forever_2026-11-11.xs`.
- WeWeb popup "What Stopped You" (53f82e59): Offer Title, Offer Timer (H:MM:SS), Offer Fine Print (mentions email only if email_status accepted/not_new) edited IN EDITOR - owner must publish. Editor demo in Pick Reason workflow action o1 still shows TT20-DEMO sample (editor only).
- Smoke test 2026-11-11: internal token -> `internal`; test acct **user 35757 [email]** (internal, exclude from numbers) -> TT60-UC36AL, 12.0 h, $135->$54, email accepted, 2nd call reuses. NOT verified: Stripe checkout actually applies the code (owner said Forever links allow promo codes), email delivery, 4-week rejection.
- Context: 20% offer converted 1 real person of ~33 offered (Jess, $48 4-week). Expected value of new offer ~$30-40/week. Success read after ~3 weeks: >=3 buyers in ~25 offers; watch "too expensive" share of what_stopped_you (~47%) for gaming.

Related: [[project_paywall_popup_redesign]] [[feedback_exclude_internal_accounts]]
- 11-11 first conversion: user 35823 (NY) offered TT60-KSMYGM 22:14 UTC -> paid $58 Forever (table 1 total_amount_paid 58, forever true). Offers so far: 4 real (4384, 35796, 35823, 35848). Owner's own test returned reason 'internal' (by design).
- 11-12: LIVE site already carries the 60% popup copy (title, H:MM:SS timer, email fine print - verified in live /data/e49b4e74.json), so the WeWeb publish happened. The editor DEMO sample in Pick Reason wf 5c2e8a17 action o1 (editor-only branch) updated to TT60-DEMO / 12h / Forever-only $135->$54 (was TT20-DEMO 30 min, 2 plans - what the owner saw in preview). Backup .claude/weweb_whatstopped_pick_reason_pre_demo60_2026-11-12.json. Needs publish to show in the editor preview; live users unaffected either way.
- 11-12 EMAIL DELIVERY VERIFIED (Brevo event log): subject "Forever access, 60% off - for the next 12 hours", from hello@, tag save_offer_60_forever. Test +offer60test 11-11 05:15 UTC delivered + opened x2; danielleandleopark delivered; annawedding10.03 (user 35823) HARD-BOUNCED (address does not exist) yet bought $58 Forever via the in-app card -> conversion did not need the email.
- 11-12 ~07:00 UTC OWNER DECISION: offer becomes TWO stacked plans, prices as anchors - 4 weeks 20% off ($60->$48; CA/Intl $69->$55.20) on top, Forever 60% off below ("Best value"). Straight switch, NO A/B (owner: "lets just have them sit next to each other one on top of the other"). Build in progress: ep313 v4 (coupon tulle_save20_4week restricted to 4-week products, TT20- code + TT60- code per offer, new col paywall_offer_code_4w, email with both), popup title/fine print/demo in editor (owner publishes), registry change entry. Read: Forever share + buyers per offer before vs after. Rollback api313_ep313_pre_two_plan_offer_2026-11-12.xs.
- **ep313 v4 LIVE 2026-11-12 06:40:04 UTC** - two plans: TT20-XXXXXX on coupon tulle_save20_4week (20% once; all 4 payment links share product [stripe-id] so NOT product-restricted; instead minimum_amount $59 blocks 1-week; pasted on Forever = $108, dominated) + TT60 as before; new col paywall_offer_code_4w; response adds code_4w, percent_off_4w 20, plans=[4w, forever{badge:'Best value'}]; 4w code best-effort (Forever-only if Stripe refuses). Email = 2 rows, tags save_offer_60_forever,two_plan. 4-week links: NY [stripe-id] $60, CA/Intl [stripe-id] $69. Backups api313_ep313_pre_two_plan_offer_2026-11-12.xs, table1_user_schema_pre_offer_code_4w_2026-11-12.xs. WeWeb popup title/fine print/badge/demo edited in EDITOR (owner must publish; live users already see 2 rows under the old 60% title). Registry main 7c8f428. UNVERIFIED: real 4-week checkout with TT20; Stripe minimum_amount 5900 on $60 subtotal (docs say inclusive; if refused, lower to 5800). Test acct 35757's TT60 expired -> returns `expired`; need +offer60test2 to see v4. First real v4 call = next 'too expensive' pick (check request history).
