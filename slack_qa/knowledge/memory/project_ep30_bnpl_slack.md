---
name: project_ep30_bnpl_slack
description: "ep30 payment Slack post now names the payment method (BNPL vs card) via one extra Stripe read - LIVE 2026-09-22, not yet proven on a real payment"
metadata: 
  node_type: memory
  type: project
  originSessionId: f61915e0-4f86-4fea-bf5d-33724fe49bc9
  modified: 2026-09-22T04:29:16.781Z
---

**ep30 `/webhooks` membership Slack post now ends with `Payment Method: …`** — LIVE 2026-09-22.
Asked for so BNPL take-up is visible without digging in the Stripe dashboard.

**Why it needs a Stripe call at all:** `checkout.session.completed` does NOT say how they paid.
`payment_intent` arrives as a bare id and **webhook payloads cannot be expanded**. So ep30 does
`GET /v1/payment_intents/<id>?expand[]=latest_charge` and reads
`latest_charge.payment_method_details.type` → `card` / `affirm` / `klarna` / `afterpay_clearpay`.
Output is `Card`, `BNPL - <type> (paid over time)`, or `unknown (Stripe lookup failed)`.

**The safety argument — do not break it:** the block sits immediately before the Slack message is
composed, i.e. **AFTER** the payment-log row, the `db.edit user` that grants access, and the
realtime event. It is wrapped in `try_catch` AND null-checked at every hop, because
`api.request` does **not** throw on 4xx in XanoScript. A Stripe outage, bad key, 404 or timeout
degrades to "unknown" and can never fail a payment or withhold access. **Moving this block
earlier destroys that property.**

Only the MEMBERSHIP branch changed. ep30 has **two** `var $slack_text` blocks, so the patch is
anchored on the unique `"A DATABASE payment was made by"` marker, not the variable name. The
roadmap post, the legacy concierge post and both alarm posts are untouched (asserted in-script).

Tooling: `.claude/xano_backups/apply_bnpl_slack.py` (`--apply` / `--restore`), backup
`ep30_pre_bnpl_slack_2026-09-22.xs`.

**UNVERIFIED:** that the lookup returns the right method on a real payment. Verified only that
the script is live and well-formed. First real purchase confirms it — check the Slack post says
`Card` and not `unknown (Stripe lookup failed)`. If it says unknown, suspect `$env.stripe_api_secret`
or the `.response.result` path.

Noticed while reading ep30, NOT fixed: **it has no Stripe signature verification at all** — no
`Stripe-Signature` header read, no HMAC. It is `auth: false` and grants lifetime access purely
from `metadata.weeks` in the POST body, so anyone who can POST to the URL can forge a paid
checkout. Also: `$amount` is trusted straight from the payload. [[project_geo_pricing_bnpl]]
[[reference_stripe_payment_webhook]] [[reference_xanoscript_traps]]

**2026-10-02 22:06 UTC: ep30 Slack "Location:" now prints ALL saved locations** (`Wedding_Location_Updated|join:", "`,
was `|first` - showed "Not Sure" for a buyer whose list also held California, which looked like a $149 misroute but
was correct). Read-back byte-identical; UNVERIFIED at runtime until the next payment. Backup
.claude/xano_backups/ep30_live_pre_locations_2026-10-02.xs.
**Missing "clicked to buy" lines (~1 in 3 payers):** cause = the _copy workflow's Slack ping races the same-tab
navigation to Stripe and gets cancelled (Mixpanel click event likely undercounts the same way). USER DECLINED a
fix 2026-10-02 - do not re-raise unless asked.
