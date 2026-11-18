---
name: project_vendor_portal
description: "Vendor portal at vendors.tulletogether.com — append-only vendor submissions, Xano group 13, Streamlit review tab. START HERE before touching vendor-facing anything."
metadata: 
  node_type: memory
  type: project
  originSessionId: 06d3ac63-1262-442a-a2b8-22c1373aa9e1
  modified: 2026-08-18T05:28:02.662Z
---

Built 2026-08-18. Vendors claim their venue, see the data we hold, and **propose**
additions across six areas (pdfs, pricing, description, attributes, images, availability).
Strategic arc is direct booking / lead-gen; v1 is data collection + a reason for vendors
to engage.

## THE INVARIANT — do not break this

**Nothing a vendor sends is ever rewritten or erased. No path to table 11 / 36 / 62 / 63.**
Enforced by the endpoints *not existing*, not by a permission flag.

The ONE state change a vendor can cause is `pending → withdrawn` on their own row
(ep 257, added 2026-08-18). It is not an edit and not a delete: `payload` is untouched and
the row survives, it just leaves the review queue — so a typo can be retracted instead of
leaving the reviewer two contradictory rows. Guarded on ownership (`$auth.id`) AND on the
row still being pending.

Reason: venues have a direct incentive to misreport pricing, and the percentile rankings
and state benchmarks derive from that pricing. **Verified by test**, not asserted — the
venue payload was snapshotted, all six submission kinds were sent, and it came back
byte-identical.

Approving in Streamlit *also* does not write to live tables. It records a decision.
Copying a change across is manual, which is why `approved` and `applied` are separate
statuses — the gap between them is the only record of what actually landed.

## Two access levels, deliberately distinct

- approved **account** (`vendor_account`, table 64) = we know who this person is
- approved **claim** (`vendor_claim`, table 65) = they may speak for ONE venue

Venue data needs the claim. Claims are their own table, not a column on the account,
because venue groups own several properties.

`vendor_account` is a **SEPARATE auth table from table 1 (`user`)** — a vendor must never
resolve as a paying couple. See [[reference_access_entitlement_model]].

## Where things live

| Piece | Where |
|---|---|
| Portal app | `marthv/tulle-vendor-portal`, Next.js 16 + Tailwind v4, local `C:\Users\vivek\Documents\GitHub\tulle-vendor-portal` |
| Deploy | Railway project `70ffd3be-…`, service `vendor-portal`, **`tulle-vendor-portal.up.railway.app`** (renamed 2026-08-18; the old `vendor-portal-production-4542` URL now 404s with "Application not found") |
| Xano | **API group 13 "Vendor Portal" = `api:BjliGATR`** — NOT the `GynP5T1B` base the rest of the stack uses. A 404 usually means the wrong base. |
| Tables | 64 `vendor_account`, 65 `vendor_claim`, 66 `vendor_submission`, 67 `vendor_message`, 68 `vendor_password_reset` |
| Endpoints | 241 signup · 242 login · 243 me · 244 venues/search · 245 claim · 246 venue/{id} · 247 submission · 248 submissions · 249 admin queue · 250 contact · 251 claim_update · 252 submission_update · 253 message_update · 254 venue_snapshot · 255 password/request · 256 password/reset · 257 submission/withdraw |
| Review tab | `vendor_portal.py` in `tulle_vendor_data_updates`, wired as `tab_vendor` in dashboard.py. **`tab_vp` is Venue Pricing — different thing.** |

**STREAMLIT KEY COLLISION — hit on first run.** Streamlit widget keys are global across the whole app, NOT scoped per tab. Venue Pricing owns the entire `vp_*` namespace (`vp_refresh`, `vp_state`, `vp_year`, …), so `key="vp_refresh"` in the vendor tab crashed the page with `StreamlitDuplicateElementKey`. Everything in `vendor_portal.py` is now prefixed **`vport_`**. Check `grep -o 'key="[^"]*"' dashboard.py` before adding any new tab.

Admin endpoints are secret-gated with the same `ANALYTICS_EXPORT_SECRET` as
`roadmap_orders_admin`. Streamlit module cloned from `roadmap_orders.py`.

## XanoScript trap that cost two endpoints

`$db."WPTP Updated Mappings".Name` **parses cleanly and fails at RUNTIME** with
"Unsupported parameter reference". The table is quoted-with-spaces in the `db.query`
header but must be **underscored** in `where`/`sort`: `$db.WPTP_Updated_Mappings.Name`,
`sort = {WPTP_Updated_Mappings.Name: "asc"}`. Pattern confirmed in `admin/google/coverage`
(ep 191). Adds to [[reference_xanoscript_traps]].

**`now + 3600000` also fails at runtime** with "Not numeric." — `now` is a timestamp, not an
integer. Do timestamp arithmetic inside an `api.lambda` (`Date.now() + ms`, plain JS) and
return the number; Xano stores timestamps as epoch ms.

## Design decisions worth keeping

- Venue pricing shown to vendors comes from **`venue_spaces` (62)**, not raw table 36 — it
  is the cleaned per-space grain the couple-facing PI panel renders, and it carries
  `fb_min_is_per_person` / `fb_min_type_corrected`. See [[project_fb_min_type_bug]]. The
  pricing submission form asks "total or per person?" in deliberately verbose language for
  the same reason.
- The vendor-facing venue endpoint **omits `PDF_Link`** — it reports coverage, not access.
- Claim review leads with the domain match between the requester's email and the venue's
  extracted `Contact_Information`. `extract_core._to_vendor_email()` already strips
  freemail/ISP domains, so a match is real evidence. A mismatch is flagged as *worth a
  reply*, not disqualifying — planners and venue groups use other domains.
- `backendConfigured()` is read **per request**; `/login`, `/signup`, `/help` are
  `force-dynamic`. As static pages the "offline" state got baked in at build time and kept
  serving after the backend was wired up.

## Open gaps

- ~~No password reset~~ **SHIPPED 2026-08-18.** `/forgot` → emailed link → `/reset`.
  Single-use, 1h expiry, and redeeming burns every other outstanding token for the account.
  The request endpoint ALWAYS returns ok for any address — anything else is an
  account-enumeration oracle. Email is raw HTML via Brevo `/v3/smtp/email` (fn51 is
  template-only), sender `[email]` — **sender and domain VERIFIED, delivery
  PROVEN end to end 2026-08-18**: Brevo 201 + messageId, and `/v3/smtp/statistics/events`
  shows `delivered`. The config was never the problem.
- **File uploads store metadata only** (`filename`/`size`), not bytes — Xano file storage
  is not wired yet. The review queue reads the same shape either way.
- ~~Slack routing needs config~~ **RESOLVED 2026-08-18.** `$env.slack_vendor_webhook_url`
  is set to an incoming webhook bound to #tulle-vendors; verified by reading the channel,
  not just by `slack_delivered`. `slack_bot_token` is still unset and no bot is in the
  channel (members: Des, Vivek, Kate, weweb_slack) — the bot branch of ep250 has therefore
  **never executed in production**. Rows are always written before the ping;
  `slack_delivered=false` is surfaced in the review tab.
- **QA rows are still in the tables** — accounts 1/2 (`@tulle-qa.example`), account 3
  (`[email]`, the user's own), 3 claims, 6 submissions, 1 message.
- `vendor_claim.vendor_account_id` has `tableref_id: 0` (the table link did not resolve).
  Cosmetic — no addons depend on it; fixing needs `updateTableSchema`, which **strips
  validators** ([[reference_xano_mcp_quirks]]).
- **`updateTableSchema` strips `validators` EVEN WHEN YOU PASS THEM EXPLICITLY** — measured
  2026-08-18 adding the `withdrawn` enum value to table 66. Column list, types, defaults,
  enum values and all 8 rows survived; the `trim` validators on `vendor_id`, `review_note`
  and `reviewed_by` did not. Harmless here because every endpoint already applies
  `filters=trim` on input, but assume any table you reshape loses them.
- `vendors.tulletogether.com` CNAME not attached; apex stays on WeWeb.
