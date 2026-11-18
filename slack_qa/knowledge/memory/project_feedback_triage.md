---
name: project_feedback_triage
description: "Feedback triage queue — BUILT AND SHIPPED 2026-08-31 as the 🗣️ Feedback tab in tulle_admin_dash; Xano tables 72/73 + eps 271-274"
metadata:
  node_type: memory
  type: project
  originSessionId: 0cb69066-15ef-4840-9328-f6f8a42a9333
  modified: 2026-08-31T02:53:28.790Z
---

**SHIPPED 2026-08-31.** The `🗣️ Feedback` tab is live in the Railway admin dash
(`feedback_triage.py`, wired third in `st.tabs` after Roadmap Orders and Vendor Portal).
Slack #feedback keeps only the notification role. Commit `6175fe4` on
`marthv/tulle_vendor_data_updates`. See [[reference_railway_admin_dashboard]].

## WHERE THE DATA ACTUALLY IS — the handoff doc was wrong about most of this

- **Table 35 `Feedback` is the live queue.** 112 rows, newest 2026-08-29, and **none had ever
  been closed**. Fields: `user_email, category, severity, details, page, device_width_size,
  operating_system, image, completed(bool)`. `completed` is the open/closed flag and the only
  place status lives.
- **Tables 13/14 (`FEEDBACK - Quick/General Product Feedback`) are DEAD** — Glide-era, last
  entries Sept 2025. Don't build against them.
- **Table 35 has NO vendor reference.** The reporter never says which venue. Linking a report
  to a vendor is a human judgement, which is why the UI is a search box, and why the chosen
  id is stored back (table 73) so nobody makes the judgement twice.
- **There is no `validated` int column.** It is `Validated_Data`, **TEXT** `"1"`/`"0"`.
  Verified: **ep119 filters `Validated_Data == 1`**, so `"0"` removes a vendor from Vendor
  Discovery search. That is the hide mechanism, confirmed at the source not assumed.

## WHAT WAS BUILT

New Xano tables:
- **72 `admin_audit`** — append-only, every dashboard write with the value it REPLACED.
  Nothing reads it to decide anything; it exists so a bad write is findable and reversible.
- **73 `feedback_triage_state`** — vendor link, notes, `handled_by/at`, `last_email_at/subject`.
  **Deliberately a SIDE table**: table 35 is written by the live app and the MCP's
  `updateTableSchema` is a full-schema REPLACE — not worth running on a hot table for fields
  the app will never read. Upserted lazily, unique on `feedback_id`.

Endpoints (group 10, secret-gated with ANALYTICS_EXPORT_SECRET):
- **ep271 `GET /admin/feedback_queue`** — returns `reports` + `triage` as two lists, joined in
  pandas. No vendor enrichment on purpose.
- **ep272 `GET /admin/vendor_lookup`** — `mode=search` (Name substring OR exact Vendor_ID) or
  `mode=ids` (bulk hydrate). `mode` is an explicit input, not inferred from array emptiness.
- **ep273 `POST /admin/vendor_field_update`** — **the only live-data write path**. Serves both
  hide/show and the free-text rail.
- **ep274 `POST /admin/feedback_update`** — completed / vendor link / notes / email stamp.

## THE THREE RAILS ON ep273 (the answer to "free text → DB write")

Owner chose rails over freedom on 2026-08-31. All enforced SERVER-side, because a Streamlit
guard is not a data guard — anyone with the secret can POST directly.
1. **Column allowlist**, 16 columns, written out literally. **Every `flt_*` column is
   excluded** — they are derived by `recompute_vendor_filter_aggregates` from table 36, so a
   hand write doesn't fix the pricing row underneath and gets silently reverted by the next
   recompute. Also excluded: `google_data_cache`, `Vendor_ID`, `id`.
   The client mirror in `feedback_triage.py` `ALLOWED_COLUMNS` must stay in step.
2. **Optimistic lock on `expected_old`** — the caller sends the value the human was shown in
   the preview; if the row moved, the write is refused. Without this the preview is decoration.
   `""` is a real assertion (currently blank), never "skip the check".
3. **Audit row, always**, with the pre-write value.

The free-text box calls **`claude-opus-5`** to propose ONE allowlisted column+value, shows a
side-by-side diff, and writes nothing until approved. Refusing ("no allowed column expresses
that") is a correct model answer.

## HIDE/SHOW IS NOT A NEUTRAL TOGGLE — the A-vs-C split is the whole point

The picker labels, before you press anything, using table 11's denorms (one indexed read, no
table-36 scan):

    flt_space_count == 0                                  -> "Nothing to show"    hiding is fine
    rows > 0 AND min venue_fee/ppfb/fbmin all 0           -> "Nothing to show"    hiding is fine
    rows > 0 AND any real number                          -> "Real pricing exists" DO NOT HIDE

The third case is the expensive mistake: a user reporting "no pricing here" on a vendor that
HAS pricing means the link/display is broken, and hiding it deletes correct, expensively
extracted data instead of fixing the bug. V3488 Raritan Inn = case 3 (6500 fee, 225 ppfb, 2 rows).

## EMAIL — LIVE via BREVO, no setup needed (2026-08-31)

**Replies send through Brevo, not Gmail.** `ep278 POST /admin/send_feedback_reply`. Nothing
had to be configured: **Xano already holds `$env.brevo_api_key`**, and
**[email] is already a verified, active Brevo sender** (id 1, "Des & Kate" —
the only sender on the account). `replyTo` is hello@ so answers reach the shared inbox.

**fn51 `integrations/send_brevo_email` could NOT be reused — it is template-only**
(templateId + merge params; 383/384 are the roadmap emails). A support reply is freeform, so
ep278 posts `subject` + `textContent` to `/v3/smtp/email` directly. textContent only — no
HTML, to avoid escaping user-authored prose in XanoScript for no benefit.

Two traps it handles, both of which have bitten this workspace before:
- **`api.request` does not throw on 4xx** — ep278 branches on `$brevo.response.status`.
  Verified live: an invalid recipient returned **400** with Brevo's own
  `{"code":"invalid_parameter"}`, and left **zero audit rows and no stamp**.
- **Brevo 201 = ACCEPTED, not delivered.** The UI says "accepted by Brevo", never "sent", and
  `last_email_at` is stamped **only on a 2xx**. Verified live: a real send returned 201 +
  messageId and produced exactly **one** audit row and **one** stamp.

**Gmail is the OPTIONAL alternative**, not the requirement. It puts the thread in a real
mailbox, which is nicer for a back-and-forth, but needs: Gmail API enabled, `gmail.send` on
the OAuth consent screen, redirect URI `http://localhost:8765/`, a refresh token minted by
the mailbox owner via `mint_gmail_token.py`, and `GMAIL_SEND_REFRESH_TOKEN` on Railway. The
modal only shows a channel picker when Gmail is actually configured, so it never offers a
channel that would fail.

## (superseded) EMAIL — the Gmail-first plan

Owner chose Gmail from `[email]` (not Brevo). Modal has from/to/subject/body.
**Railway has GOOGLE_CLIENT_ID/SECRET + GOOGLE_SERVICE_ACCOUNT_JSON but NO Gmail send
credential**, so sending is inert today — the modal drafts, warns honestly, and offers
"Mark as replied". Two paths, tried in order:
1. `GMAIL_SEND_REFRESH_TOKEN` — run **`mint_gmail_token.py`** once, signed in as hello@.
   Needs, in Google Cloud Console on the SAME OAuth client: Gmail API enabled, scope
   `gmail.send` added, and redirect URI `http://localhost:8765/`. No Workspace admin needed.
2. `GMAIL_IMPERSONATE=[email]` + domain-wide delegation on the service account
   (needs a Workspace admin).

**`last_email_at` is stamped only on a real Gmail message id**, never on intent — a timestamp
written on "we tried" answers "did anyone reply to this person?" wrongly and confidently.
Same trap family as [[reference_xanoscript_traps]] on `api.request` not throwing.

## WHAT THE 112 REPORTS ACTUALLY ARE (classified 2026-08-31, don't re-derive this)

The first build assumed feedback was about vendors. **It is not** — the fix lives on the USER
row far more often than on a vendor:

    38  paid but still locked out          the biggest bucket by a wide margin
    20  internal test rows                 noise (rimal@ / [email],
                                           plus Des/Kate/Vivek's own rows)
    13  refund / billing                   several genuinely double-charged
    11  feature requests
     6  bugs
     5  a VENUE writing about its own listing   <- escalation, not a support ticket
     4  saved venues lost (the ep109 wipe)
     4  a specific venue's data is wrong
     1  a SECURITY report, unread since it arrived
     1  account detail change

**Verified account states, spot-checked live** — these are the ones worth acting on:
- **#85 [email]** (user 19628): $60 across **2 payments**, `forever=False`,
  access **EXPIRED 2026-06-19**. A real double charge with nothing to show for it.
- **#108 [email]** (23652): $60 / **2 payments**, expired
  2026-08-07. Same shape.
- **#91 [email]**: **NO user row exists for that address.** She paid for a
  four-week plan. Either she signed up under a different email or the account was never
  created — worth chasing, because ep30 logs a payment with `client_reference_id` null as
  "paid, zero access" ([[reference_stripe_payment_webhook]]).
- **#104, #111, #92** were fixed at some point since — all now `forever=True`. Close and reply.
- **#79 [email]** — Union Station Dallas, "inaccurate, take it down
  immediately". The venue's own domain. **#63 [email]** (Alexander Mansion),
  **#59** (Director of Events, University of Miami) are the same class.
- **#39 [email]**: *"i randomly have access to your admin dashboard just a
  heads up"* — a security report nobody ever answered.
- Oddity: `FreeViewsRemaining` is **-1** on users 11742 and 6685. Negative should not occur.

## HOW A FIX IS RECORDED (v3, 2026-08-31) — the answer to "how do I note it's fixed?"

Four separate things, only one of which is pressed by hand:

    the data change itself       -> admin_audit (table 72), AUTOMATICALLY on Apply,
                                    with actor, timestamp, old -> new and the typed reason
    a NON-database action        -> the report's notes ("refunded in Stripe",
                                    "escalated to Des"). This is what notes are FOR —
                                    data changes record themselves.
    you replied to the reporter  -> last_email_at, stamped only on a real Gmail message id
    the report is done           -> Feedback.completed, via Close

**ep277 `GET /admin/audit_log`** — scoped to one report (`feedback_id`) or recent activity
across everything (`feedback_id=0`). **Read-only by design: there is no endpoint that edits
or deletes `admin_audit`.** The trail was written from v1 but nothing read it back, so a
report could be fixed and closed with no way to see what was done — an audit log nobody can
read is just overhead.

UI: each report shows "What's been done to this report" (old -> new per change, who, why),
the header counts **fixes applied** — excluding open/close flips, replies, and no-op writes
where old == new, since none of those are fixes — and a "Recent activity" expander sits at
the top of the tab.

**ONE bulk audit load per render** feeds both the per-report history and the header badge.
A **Streamlit expander executes its body whether or not it is open**, so the obvious
per-report fetch fires one request per visible row on every rerun. Same trap applies to any
per-row fetch added to this tab later.

## THE FIX PATHS THAT EXIST (v2, 2026-08-31)

- **ep275 `GET /admin/user_lookup`** — reporter's entitlement, output-allowlisted (table 1
  holds the password hash and session token; ep65 once leaked all 23,549 rows with them).
- **ep276 `POST /admin/user_field_update`** — railed write to the user row. Allowlist:
  date_until_access, forever_access_purchased, FreeViewsRemaining, total_vendor_views,
  total_amount_paid, total_payments, email, name, Wedding_*. **password / role /
  token_session / magic_link / google_oauth are deliberately unreachable** — support tooling
  that can take over an account is not support tooling.
- **ONE CONFIRM GATE**: quick-fix buttons and the free-text box both STAGE a proposal;
  nothing is written until the diff renders and Apply is pressed, and the old value in that
  diff is sent as `expected_old` so a moved row is refused.
- Quick fixes: +1/+4/+12 weeks (extends from **max(today, current expiry)**), lifetime, reset
  free PDF views, and **raise recorded spend to the $50 PI threshold** — separate on purpose,
  because **PI is gated on `total_amount_paid >= 50` and reads NO date** (ep230 v7), so
  extending access does not unlock pricing and the user would still see it hidden.
- **Three things the buttons will NOT do, by design:** issue a Stripe refund (read-only
  integration), restore lost favourites (the wipe overwrites rather than versions — there is
  no prior value, and claiming otherwise would be a lie in a support reply), or touch
  credentials.

**Classifier traps, both found by running it over the real corpus:** concatenating the email
onto the body pushed the end of the string past the `$` anchor, so every one-word `"test"`
row classified as `other`; and `\bglitch\b` never matches "glitching". Internal addresses
are matched against the email alone now.

## THE FILTER THAT HID REAL REPORTS (fixed 2026-08-31, don't reintroduce it)

v2 classified **any** report from an internal address (`@infiwebsolutions.com`,
`desi.gaddis@`, `katebeckman`, `vivek.marthi`) as a test row, and "Hide test rows" defaults
to ON. Symptom: the user submitted #115/#116 to try the tab and **neither appeared**.

Wrong twice over: it hid the submissions used to *test the dashboard*, and it had buried two
genuine bug reports from Des for months —
- **#48** "This page is pretty glitchy - just fairly laggy and when you add vendors..."
- **#21** "When you first log in on mobile the nav bar auto opens and eats half the screen"

**Rule: who sent a report says nothing about whether it is actionable — the BODY does.**
`classify()` now reads the body alone (empty body still = test, nothing to action); an
internal address is a 🧪 **label** plus an opt-in "Hide internal senders" filter, default OFF.
Measured on the live 114: test bucket 20 -> 16, six internal reports became visible.
#115 stays hidden because its body genuinely is "test".

Generalise it: **never let a default-on filter hide rows by sender identity.** The same shape
would hide a venue writing from its own domain.

## STILL OPEN
- Gmail credential not yet minted → sending inert.
- The 112 open reports have not been triaged; the queue is empty of decisions.
- **#39's security report has never been answered.** Verify it before closing.
- The 5 venue-rep reports have never been answered either.
- Escalation rules NOT yet encoded: repeat reports on one vendor should compound, and **a
  reporter whose email domain matches the venue's own domain is top priority** (the venue
  correcting our data — relationship exposure, not a bug). Live case: V277 Bear Brook Valley.
- `search_wptp_vendors` / `list_wptp_pdfs` MCP tools still return
  `Invalid syntax: missing parameter - dbo` — irrelevant now, ep272 replaces them.
- Incidental, unrelated: **V3488 has a New Jersey address with Georgia coordinates**
  (`lat 33.12, lng -84.03`; Califon NJ should be ~40.7/-74.8) — it plots in the wrong state.
  Suggests a lat/lng validation sweep; cf. ep123.

## XANOSCRIPT GOTCHA FOUND BUILDING THIS
**`sort` without a `return` block is SILENTLY IGNORED.** ep271 v1 shipped
`sort = {Feedback.id: "desc"}` with only an `output` allowlist and came back in heap order,
oldest first, no error. It only takes effect alongside `return = {type: "list"}` (ep119 and
ep213 both have it). Recorded in [[reference_xanoscript_traps]].
