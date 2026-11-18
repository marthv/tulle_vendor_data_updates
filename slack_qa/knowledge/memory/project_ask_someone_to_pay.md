---
name: project_ask_someone_to_pay
description: "'Ask someone to cover this' pay-link for parents/partners - PARKED 2026-09-27 until the Oct 1 BNPL experiment reads out; sized at ~2.5% third-party payers; backend already works"
metadata:
  node_type: memory
  type: project
  originSessionId: b1ed54d5-9ec2-453d-9b07-d84f6865eeef
  modified: 2026-10-16T22:16:15.268Z
---

**PARKED 2026-09-27 by user: "we'll test this later... i want to see what the impact of BNPL is first."**
Revisit only after the Oct 1 geo/BNPL experiment has a read ([[project_geo_pricing_bnpl]]).

Origin: user wanted a Partiful/Yope-style viral loop ("collaboration mode", invited role).
Pushback accepted: partner/parents/planner are on the SAME wedding, so collab = more seats per
customer, not new customers. Referral baseline: 82 referral rows lifetime vs ~50k users.
User picked "ask someone to pay" as the first direction - a CONVERSION lever, not a loop.
Other directions discussed, not pursued: shareable shortlist link, making the PDF-submission
contribution loop visible, planner seats. Invitees must NOT see unlocked pricing (one payment
would cover a whole wedding party); partner full access is fine.

**Sizing (Stripe, 198 paid sessions 2026-08-24..09-27, ~$15.2k):** ~5.6% paid by someone other
than the account holder with no feature for it; ~3% partner; **~2.5% (5, ~$414) genuine third
party** (one billing name "A GIFT FOR YOU"). 95% CI ~1-5%. Checkout-email mismatch (22.7%) is NOT
a signal - mostly the same person on a wedding/work/Hide-My-Email address. Impact estimate: doubling
= ~+$400/month (~3% revenue); latent demand unmeasurable today. Sample implies ~$435/day revenue
(CLAUDE.md $183/day is stale).

**Mechanism already works (read live ep30):** access keyed ONLY on `client_reference_id`, no email
fallback. Couple's link paid on a parent's device unlocks the couple with zero backend change.

**Plan when resumed:**
1. /pricing "Ask someone to cover this" under each tier, only `total_amount_paid == 0` (ep285
   upgrade flow is auth-only, can't be shared). Link = table 17 `Stripe_Link` with query stripped
   (drop couple's prefilled email) + `?client_reference_id=<id>`, reuse Forever button's cohort
   logic (`.claude/xano_backups/weweb_forever_button_geo_url.js`). navigator.share like the NPS
   share grid. Mixpanel `pay_link_shared`. No landing page in v1 (public name-by-id = enumeration).
2. `/purchase-complete`: payer's device is logged out -> show "their access is unlocked" via ep28
   session read. Mixpanel `pay_link_completed`.
3. **ep162 referral-conversion keys on PAYER email** -> parent payment misses the couple's referral
   or marks a stranger's pending referral paid. Resolve client_reference_id -> user email. Back up first.
4. ep30: Slack "paid by X for Y" + Mixpanel `paid_by_other` when payer != account.
Kill criterion: < ~5 `pay_link_completed` in 30 days post-publish -> remove.

**2026-10-16 - GIFTING added, still BACK BURNER (owner: "something for us to think about").** Two flows:
(a) buy Tulle FOR someone (gift), (b) send Tulle to someone so THEY pay (= the plan above).
Signal: a customer texted Des asking to gift "a tulle premium account" as an ENGAGEMENT GIFT ("modern
planning binder"). Kate's shape: marketing-site "Gift a Tulle Planning Membership" -> Stripe link, buyer
enters friend's email, NO account needed; Des: then send an "egift card". Owner: Stripe side easy, thinks
many would do it. Open design gap: gift buyer has no recipient account -> needs a redeem code/claim link
(ep30 keys access on client_reference_id only), plus where a Forever gift's clock starts. Sizing above
(~2.5% third-party payers) predates gifting; engagement-gift demand is unmeasured.

Related: [[project_referrals_self_referral]] [[project_nps_survey]] [[reference_stripe_payment_webhook]]

**10-17 GIFTING DESIGNED (owner decisions):** Forever only, FLAT $135 (no geo pricing); delivery straight to recipient by default + "send it to me" (= leave recipient email blank on Stripe Checkout; 3 custom fields: recipient name, recipient email optional, message). Codes never expire. Flow/emails/claim page/plan: https://claude.ai/artifact/KtdFdPeBdbmfWz6zQnFNRH (private until owner shares). Phase 1 = Stripe link + gifts table + ep30 gift branch WITH Stripe session re-verify before minting a code (ep30 has no signature check) + Brevo emails 1-3 + /redeem page/claim endpoint + WordPress buttons. Gift payments need their own Type so fn63 upgrade credit and cohorts exclude them. Stop rule: <5 gifts in 60 days. NOT built yet.

**10-17 GIFT BUILD (in progress, nothing launched):** full inventory + unwind = tulletogether/.claude/gift_build_ledger.md (keep it updated every step - owner asked).
Built: Xano table 82 gifts; ep314 POST stripe_checkout/gift_webhook (re-verifies session with Stripe, idempotent, payment-log Type gift_forever with BLANK Client_Reference_ID); ep315 GET Weweb/gift_preview; ep316 POST Weweb/gift_claim (auth; sets date_until_access 32503680000000 + forever_access_purchased). Brevo templates 412-416. Stripe [stripe-id] / [stripe-id] / [stripe-id] (buy.stripe.com/6oU00i8vPgTWauzaik0Fi0B, active, posted nowhere). WeWeb (editor, unpublished): Redeem Gift page 09c3ca3c (/redeem), var gift_redeem 4d50e13d, Discovery page-load wf e5a1b7c6 'Gift code return'.
Blockers: Stripe connector lacks scope for webhook endpoints AND coupons -> owner adds webhook (url .../api:bFTVuNEH/gift_webhook) + 100% test promo code in Dashboard. Auto-mode classifier blocked creating ep315/316 via bash MCP helper; worked via mcp__xano-meta__createAPI after owner said continue.
- 10-17 ~04:20 UTC: end-to-end gift purchase VERIFIED (live $0.54 test, gift #2 TULLE-F4V9-ZJGG-KLE6); gift email switched to personal template 417 via fn102 ('<Buyer> via Tulle Together', reply-to buyer) - owner confirmed it arrives. Open: refund [stripe-id] (owner, Dashboard), claim test on staging, WordPress /gift, quiet ep30 false alert, deactivate link until launch.
