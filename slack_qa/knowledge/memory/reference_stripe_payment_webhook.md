---
name: reference-stripe-payment-webhook
description: "Xano endpoint 30 (stripe_checkout group 7) is the Stripe webhook that processes payments — logs to DONATION - Payment Log, extends user access, posts Slack"
metadata: 
  node_type: memory
  type: reference
  originSessionId: b9dade59-037b-4ccc-b85e-6b88e7988603
  modified: 2026-08-11T05:17:29.214Z
---

The Stripe payment processor is **API endpoint 30 `webhooks`** (`POST /webhooks`, api group 7 `stripe_checkout`, base `bFTVuNEH`, auth:false, no cache). Desc: "handles subscription access extension and payment logging. Replaces Make integration." It reads the Stripe event from `$input.__self.data.object`.

**Payment type is driven by Stripe Checkout session metadata:**
- `metadata.weeks` ∈ {`1 week`/`1 weeks`,`2 weeks`,`4 weeks`,`6 weeks`,`12 weeks`,`24 weeks`,`forever`,`forever weeks`} → DATABASE/access branch.
- Day mapping (each = weeks×7 + 1 grace day, via `add_secs_to_timestamp`): 1wk=8d/691200s, 2wk=15d/1296000s, 4wk=29d/2505600s, 6wk=43d/3715200s, 12wk=85d/7344000s, 24wk=169d/14601600s, forever=far-future. Extends from existing `date_until_access` if still future (stacks).
- `metadata.type == "concierge"` → ROADMAP branch.
- Branches are mutually exclusive (`if weeks… elseif type==concierge`). A payment with neither is logged NOWHERE and posts no Slack.

**Side effects of the DATABASE branch:** `db.add "DONATION - Payment Log"` (Type = the weeks string, Stripe_Payment=1); `db.edit user` (extends `date_until_access`, increments `total_payments`, adds `amount` to `total_amount_paid`, and `forever_access_purchased`); `api.realtime_event` on `user_updates`; `api.request` to `$env.slack_webhook_url` posting the "💰💰😱 A DATABASE payment was made…" alert. ROADMAP branch only logs + posts a short "ROADMAP payment…" Slack. **Any live test of this endpoint fires a real Slack alert to the team.**

**2026-06-04:** added `1 week`/`1 weeks` → +8 days (691200s) branch for a new 1-week payment link, matching the others' weeks×7+1 grace-day pattern. Purely additive; existing tiers untouched. Verified live.

**2026-06-01:** added `forever_access_purchased` write — a `$forever_access` bool (default false, preserves an existing true so a later non-forever purchase can't downgrade it) set true in the `forever`/`forever weeks` branch, written via the existing `db.edit user`. The column already existed on table 1 `user` (bool). Rollback snapshot: `tulletogether/.claude/xano_backups/30_webhooks_pre_forever_2026-06-01.xs`.

## 2026-08-11 — LIVE price points, and where `metadata.weeks` actually comes from

The three access tiers are **Stripe Payment Links**, not ep 26 (`sessions`). ep 26 exists but the
grid does not use it — and note it forwards client-supplied `line_items` verbatim to Stripe, so
it would honour any amount a caller sends. Live links (URLs are stable across price edits, which
is why WeWeb needs no change when a price moves):

| link | `metadata.weeks` | price after 2026-08-11 | URL |
|---|---|---|---|
| `plink_1SvVwG…` | `1 week` | $30 | `buy.stripe.com/cNi00ih2lbzCcCHaik0Fi0n` |
| `plink_1SvVxS…` | `4 weeks` | **$50 → $75** | `buy.stripe.com/fZueVc13ncDGdGL1LO0Fi0o` |
| `plink_1TebFd…` | `forever weeks` | **$119 → $129** | `buy.stripe.com/6oUfZgfYheLOcCH2PS0Fi0s` |

**CRITICAL: `session.metadata` comes from the PAYMENT LINK's metadata, not the Price's.** ep 30
reads `$session|get:"metadata"`, so the Price's own metadata is never consulted. Verified
empirically twice: the old $50 sessions carried `weeks:"4 weeks"` while that Price had empty
metadata. This matters because the $75 tier now reuses **`price_1SyIlB…`**, an old forever-access
price that was renamed to "4 weeks" but **still carries `metadata.weeks = "forever weeks"`**. It
is inert today — but it is a live landmine if anyone ever wires logic to price metadata, and it
makes the Stripe UI read as if the 4-week tier grants lifetime access.

**Price nicknames are unreliable — never identify a tier by nickname.** Before the 2026-08-11
edit the $50/"4 weeks" tier's Price was nicknamed *"6 weeks v2"* and the $30/"1 week" tier's is
*"2 weeks v2"*. Identify tiers by the **link's `metadata.weeks`**, confirmed against real
Checkout Sessions. Also: three separate active Prices were all $119 "forever access" on three
different products; only the one behind `plink_1TebFd…` was live.

Separate second Stripe webhook: **endpoint 162 `referral-conversion`** (same group) credits referral commissions on `checkout.session.completed` — independent of endpoint 30. Payment Log table is `DONATION - Payment Log`; the `Type` column holds the payment kind. See [[project_tulle_focus]].

## 2026-08-18 — routing is metadata.type-FIRST now, and there are THREE separate Slack blocks

⚠️ The "branches are mutually exclusive (`if weeks… elseif type==concierge`)" note above is
**stale**. Live ep30 routes on **`metadata.type` first, then `weeks`**:
`if type=="budget_roadmap" … elseif weeks∈{…} … elseif type=="concierge"` (legacy link). There is
also a real `else` now — an unrecognised product fires a "⚠️ UNHANDLED STRIPE EVENT" Slack alarm
instead of being swallowed with a 200 — and the whole stack is gated on
`event_type == checkout.session.completed || checkout.session.async_payment_succeeded`.
All top-level reads use `|get:` so a payload missing `metadata`/`customer_details` cannot fatal it.

**ep30 builds FOUR different Slack messages, and only one carries the user-detail rows.** When
asked to "add a row to the payment Slack alert", confirm which: the detail rows (Location /
Planning Stage / Updated Date of Access / Sign Up Date / Source / Total Vendor Views / Wedding
Vendor) exist **only** in the membership `$slack_text` ("💰💰😱 A DATABASE payment…"). The
budget_roadmap, legacy-concierge, orphan-order and no-user-id alerts each build their own text
and share none of those fields.

**Added `Wedding Vendor: True/False`** as the last row of the DATABASE-payment alert, from
`user.Wedding_Vendor` (bool, table 1). Two things that generalise to any future row here:
- `$current_user` in that branch is an **unprojected** `db.query user … return single`, so every
  table-1 column is already on the row — adding a field needs **no query change**.
- A Xano **bool through `|to_text` renders `1`/`0`, not `True`/`False`.** Build a display var with
  a conditional instead. Bare truthy `if ($current_user.Wedding_Vendor)` is the right test and is
  null-safe (unset → False); it matches the existing `if ($current_user.forever_access_purchased)`
  idiom already in this file.

Snapshots (pre + post) pushed to branch `worktree-ep30-backup-wedding-vendor`:
`.claude/xano_backups/ep30_pre_wedding_vendor_2026-08-18.xs` and `ep30_v2_wedding_vendor_2026-08-18.xs`.
