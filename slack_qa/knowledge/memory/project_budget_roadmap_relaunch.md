---
name: project-budget-roadmap-relaunch
description: "2026-08-09/10 Budget Roadmap relaunch — SHIPPED end to end at $40. Xano table 60 + ep209-214/216, fn51 Brevo transactional, Streamlit ops tab, /roadmap-complete + rebuilt /purchase-complete. Read before touching ep30, roadmap orders, or the confirmation pages."
metadata: 
  node_type: memory
  type: project
  originSessionId: 15e56bb6-cbbd-4d5f-8c5c-e6d27694b014
  modified: 2026-08-10T04:38:10.515Z
---

**2026-09-27: KILLED.** User: the experiment "failed spectacularly". Admin-dash tab removed. Xano/WeWeb pieces (table 60, ep209-216, /roadmap-complete) NOT touched — still live unless removed separately.

Budget Roadmap = human-made custom wedding-budget PDF, **$40** one-time (was $75; changed
2026-08-09 during the redesign), delivered by email in **5–7 business days**, roadmap only —
the 30-min consult call was dropped from the offer.

**Shipped and verified with real $40 purchases (orders 6 & 7 — refund those):**
- **Table 60 `roadmap_orders`** — order row created BEFORE Stripe, so abandonment is measurable
  and nothing is lost at checkout. `priorities` is RANK-ORDERED, `budget_amount`/`guest_count`
  numeric (old table 9 stored `"$100,000.00"` as text and lost priority order).
- **ep 209/210/211** (group 4, auth=user) draft / owner-scoped patch / `mine`.
- **ep 212 `POST /roadmap_session`** (group 7) — real Checkout Session with
  `metadata.type=budget_roadmap` + `order_id`. Uses inline `price_data` (`unit_amount 4000`,
  product `[stripe-id]`) because no $40 Price object exists. **Amount is a server-side
  constant — never make it an input.**
- **ep 30 webhook rewritten** — event-type guard, routes `metadata.type` BEFORE `weeks`,
  idempotent roadmap branch, increments `total_payments`/`total_amount_paid` (so roadmap revenue
  now reaches Brevo TOTAL_SPEND), and a final **else that Slack-alarms unhandled events**. That
  else is the important one: previously any unrecognised product was swallowed with a 200.
- **ep 213/216 reads, ep 214 write** — secret-gated ops endpoints + `my_access_summary`.
- **fn 51 `integrations/send_brevo_email`** — first `/v3/smtp/email` call in the workspace.
  Templates **383 order confirmation**, **384 delivered**. Always call inside `try_catch`.
- **Streamlit "🗺️ Roadmap Orders" tab** in `tulle_vendor_data_updates/dashboard.py` — live on
  Railway. SLA uses `numpy.busday_count` (business days, matching the promise).
- **`/roadmap-complete`** (new) and **`/purchase-complete`** (rebuilt) WeWeb pages.

**Traps that cost real time here:**
- `stripe_api_secret` in Xano was **empty for the entire life of the app** — which is why every
  checkout had always been a Payment Link and ep 26 `/sessions` never worked. Now set.
- ep 26's Stripe `Basic` auth omits the required trailing colon (`base64("key:")`). Use
  `Authorization: [redacted] <key>` instead — ep 212 and ep 214 do.
- The delivery email can only ever be sent ONCE (`delivered_at` stamped on first transition,
  re-delivery is a deliberate no-op). ep 214 therefore **refuses `delivered` without a pdf_url**,
  checked BEFORE `db.patch`. A UI guard alone was not enough.
- The payment log now holds `budget_roadmap` rows, so any membership query must exclude them or a
  roadmap buyer's page announces "budget_roadmap" as their plan.

See [[reference-xanoscript-traps]], [[reference-stripe-write-via-xano]],
[[project-post-purchase-access-fix]].

**Still open:** refund orders 6 & 7; `/pricing` 6-Months button charges $65 for 3 months (reads
`data[2]`, needs `data[3]` and the $160 link `plink_1SvVyGD…` reactivated); `mixpanel.identify`
deferred, so server-side purchase events don't stitch to prior anonymous clicks.
