"""
🏛️ Vendor Portal — review queue for the Tulle admin dashboard.

Self-contained: dashboard.py just does
    from vendor_portal import render_vendor_portal_tab
    with tab_vendor:
        render_vendor_portal_tab(user_email)

NOT tab_vp — that is the Venue Pricing tab, which is a different thing entirely and
already owns the vp_* widget-key namespace. See the note above KIND_LABEL.

Backs the vendor portal at vendors.tulletogether.com (repo marthv/tulle-vendor-portal),
where vendors claim their venue, see the data we hold, and PROPOSE additions.

THE THING TO UNDERSTAND BEFORE USING THIS TAB
---------------------------------------------
Vendors have append-only access to one table. Nothing they send has touched live data,
and approving something here still does not touch live data — it records a decision and
stores a note the vendor sees. Applying an approved change to table 11 / 36 is a
deliberate manual step you do yourself, which is why every submission is shown beside
the current live values rather than on its own.

That separation is the product: venues have a direct incentive to misreport pricing, and
the percentile rankings and state benchmarks derive from that pricing.

Status vocabulary for submissions:
    pending   — nobody has looked
    approved  — the content is good, but has NOT been copied into live tables
    applied   — someone has since copied it in by hand
    rejected  — not accepted, note explains why
    withdrawn — the VENDOR retracted it before review. Not a decision you made, and
                not a delete: the payload is still on the row. Nothing to action.
Do not collapse approved and applied: the gap between them is the only record of what has
actually landed.

Endpoints (Xano API group 13 "Vendor Portal", api:BjliGATR), all secret-gated with the
same ANALYTICS_EXPORT_SECRET as roadmap_orders_admin:
    GET  /vendor_admin/queue              (ep 249) — accounts, claims, submissions, messages
    GET  /vendor_admin/venue_snapshot     (ep 254) — live values for the side-by-side diff
    POST /vendor_admin/claim_update       (ep 251) — approve/reject a claim (+ its account)
    POST /vendor_admin/submission_update  (ep 252) — record a review decision
    POST /vendor_admin/message_update     (ep 253) — mark a help request handled

Deliberately cached for only 30s: this is a live queue and a vendor is waiting.
"""

import os
import time
import datetime as _dt

import pandas as pd
import requests
import streamlit as st

# The Vendor Portal group has its own canonical base — NOT the XANO_BASE the rest of the
# dashboard uses (api:GynP5T1B). A 404 here almost always means the wrong base.
VENDOR_BASE = os.environ.get(
    "XANO_VENDOR_BASE",
    "https://xqtb-2ma7-ijfy.n7e.xano.io/api:BjliGATR",
)

EXPORT_SECRET = os.environ.get(
    "ANALYTICS_EXPORT_SECRET",
    "ttv_export_da19ae7c3fbcdd2c51747199117a63a33f848ca9",
)

# WIDGET KEYS: every key in this module is prefixed `vport_`.
# The Venue Pricing tab in dashboard.py already owns the entire `vp_*` namespace
# (vp_refresh, vp_state, vp_year, ...). Streamlit keys are global across the whole
# app, not scoped per tab, so `key="vp_refresh"` here crashed the page with
# StreamlitDuplicateElementKey the moment both tabs rendered. vp = Venue Pricing.

KIND_LABEL = {
    "pdfs": "📄 Pricing PDFs",
    "pricing": "💰 Pricing",
    "description": "📝 Description",
    "attributes": "🏷️ Attributes",
    "images": "🖼️ Images",
    "availability": "📅 Availability",
}

SUBMISSION_STATUS_LABEL = {
    "pending": "🕓 Pending review",
    "approved": "✅ Approved — not yet applied",
    "applied": "📥 Applied to live data",
    "rejected": "🚫 Rejected",
    # Vendor-initiated, not a review outcome. Their payload is still on the row.
    "withdrawn": "↩️ Withdrawn by the vendor",
}

CLAIM_STATUS_LABEL = {
    "pending": "🕓 Pending",
    "approved": "✅ Approved",
    "rejected": "🚫 Rejected",
}


# ── HTTP (same retry shape as roadmap_orders.py / cohorts.py) ───────────────────
def _xget(url, tries=4, timeout=30):
    for i in range(tries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                return None                      # 4xx won't self-heal
        except Exception:                        # noqa: BLE001
            pass
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    return None


def _xpost(url, payload, tries=4, timeout=30):
    """Returns (ok, parsed_or_error_text). Retries 5xx/network only — a review write
    must survive a Xano 503 blip, which this app has hit before."""
    last = "no response"
    for i in range(tries):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            if r.status_code == 200:
                return True, r.json()
            if r.status_code < 500:
                return False, f"HTTP {r.status_code}: {r.text[:300]}"
            last = f"HTTP {r.status_code}"
        except Exception as e:                   # noqa: BLE001
            last = str(e)
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    return False, last


# ── Time / formatting ──────────────────────────────────────────────────────────
def _ms_to_dt(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return _dt.datetime.utcfromtimestamp(v / 1000.0)


def _fmt_dt(v, fmt="%Y-%m-%d %H:%M"):
    d = _ms_to_dt(v)
    return d.strftime(fmt) if d else "—"


def _age_hours(v):
    d = _ms_to_dt(v)
    if not d:
        return None
    return (_dt.datetime.utcnow() - d).total_seconds() / 3600.0


def _domain(email):
    return str(email or "").split("@")[-1].strip().lower()


def _money(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    return f"${n:,.0f}" if n else "—"


def _val(v):
    """Render a payload value for display."""
    if v is None or v == "" or v == []:
        return "—"
    if isinstance(v, list):
        parts = []
        for x in v:
            if isinstance(x, dict) and "filename" in x:
                parts.append(str(x["filename"]))
            else:
                parts.append(str(x))
        return ", ".join(parts)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items())
    return str(v)


# ── Data ───────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner=False)
def load_queue():
    d = _xget(f"{VENDOR_BASE}/vendor_admin/queue?secret={EXPORT_SECRET}")
    if not isinstance(d, dict):
        return None
    return {
        "accounts": d.get("accounts") or [],
        "claims": d.get("claims") or [],
        "submissions": d.get("submissions") or [],
        "messages": d.get("messages") or [],
    }


@st.cache_data(ttl=30, show_spinner=False)
def load_snapshot(vendor_id):
    """Live values for one venue — the right-hand side of every diff."""
    if not vendor_id:
        return None
    return _xget(
        f"{VENDOR_BASE}/vendor_admin/venue_snapshot"
        f"?secret={EXPORT_SECRET}&vendor_id={vendor_id}"
    )


@st.cache_data(ttl=30, show_spinner=False)
def load_calendars():
    """Connected vendor calendars.

    include_urls is deliberately NOT set. That flag is for the sync job, which has
    to open the feed; this table only renders status, and pulling vendor calendar
    credentials into a browser session to draw a status column would be careless.
    """
    d = _xget(f"{VENDOR_BASE}/vendor_admin/calendars?secret={EXPORT_SECRET}")
    if not isinstance(d, dict):
        return []
    return d.get("calendars") or []


def _update_claim(claim_id, status, actor, also_approve_account=True):
    return _xpost(f"{VENDOR_BASE}/vendor_admin/claim_update", {
        "secret": EXPORT_SECRET,
        "claim_id": int(claim_id),
        "status": status,
        "actor": actor,
        "also_approve_account": bool(also_approve_account),
    })


def _update_submission(submission_id, status, actor, review_note=None):
    payload = {
        "secret": EXPORT_SECRET,
        "submission_id": int(submission_id),
        "status": status,
        "actor": actor,
    }
    if review_note is not None:
        payload["review_note"] = review_note
    return _xpost(f"{VENDOR_BASE}/vendor_admin/submission_update", payload)


def _update_message(message_id, status, actor, admin_notes=None):
    payload = {
        "secret": EXPORT_SECRET,
        "message_id": int(message_id),
        "status": status,
        "actor": actor,
    }
    if admin_notes is not None:
        payload["admin_notes"] = admin_notes
    return _xpost(f"{VENDOR_BASE}/vendor_admin/message_update", payload)


def _after_write(ok, res):
    if ok:
        st.cache_data.clear()
        st.rerun()
    else:
        st.error(f"Write failed: {res}")


# ── Sections ───────────────────────────────────────────────────────────────────
def _render_claims(claims, accounts_by_id, actor):
    pending = [c for c in claims if c.get("status") == "pending"]
    st.subheader(f"Access requests ({len(pending)} waiting)")

    if not pending:
        st.caption("Nothing waiting. Decided requests are listed below.")
    for c in pending:
        acct = accounts_by_id.get(c.get("vendor_account_id"), {})
        acct_email = acct.get("email", "")
        venue = c.get("venue_name") or c.get("vendor_id")
        age = _age_hours(c.get("created_at"))
        age_txt = f"{age:.0f}h ago" if age is not None else "—"

        with st.expander(f"**{venue}** — {acct_email} · {age_txt}", expanded=False):
            snap = load_snapshot(c.get("vendor_id"))
            venue_row = (snap or {}).get("venue") or {}
            on_file = venue_row.get("Contact_Information", "")

            left, right = st.columns(2)
            with left:
                st.markdown("**Who is asking**")
                st.write(f"Name: {acct.get('contact_name', '—')}")
                st.write(f"Email: {acct_email or '—'}")
                st.write(f"Company: {acct.get('company') or '—'}")
                st.write(f"Phone: {acct.get('phone') or '—'}")
                st.write(f"Account status: {acct.get('status', '—')}")
                st.markdown("**Their note**")
                st.write(c.get("evidence_note") or "_none given_")
            with right:
                st.markdown("**What we hold on this venue**")
                st.write(f"Venue: {venue_row.get('Name', '—')}")
                st.write(f"Address: {venue_row.get('Address') or '—'}")
                st.write(f"Contact on file: {on_file or '—'}")
                st.write(f"Website: {venue_row.get('Website') or '—'}")

            # The strongest signal available: the contact email we extracted from this
            # venue's OWN pricing PDFs. extract_core._to_vendor_email() already discards
            # freemail and ISP domains, so anything present here is a business address.
            if on_file and acct_email:
                if _domain(on_file) == _domain(acct_email):
                    st.success(
                        f"Email domain matches the address on file "
                        f"(@{_domain(acct_email)}). Strong evidence."
                    )
                else:
                    st.warning(
                        f"Domain mismatch: they wrote from @{_domain(acct_email)}, "
                        f"our file says @{_domain(on_file)}. Not disqualifying — "
                        f"planners and venue groups often use another domain — but "
                        f"worth a reply before approving."
                    )
            else:
                st.info(
                    "No contact email extracted for this venue, so there is nothing to "
                    "match against. Judge on the note and the website."
                )

            b1, b2, _ = st.columns([1, 1, 3])
            if b1.button("Approve", key=f"vport_claim_ok_{c['id']}", type="primary"):
                _after_write(*_update_claim(c["id"], "approved", actor, True))
            if b2.button("Reject", key=f"vport_claim_no_{c['id']}"):
                _after_write(*_update_claim(c["id"], "rejected", actor, False))

    decided = [c for c in claims if c.get("status") != "pending"]
    if decided:
        with st.expander(f"Decided requests ({len(decided)})", expanded=False):
            st.dataframe(
                pd.DataFrame([{
                    "Venue": c.get("venue_name") or c.get("vendor_id"),
                    "Account": accounts_by_id.get(
                        c.get("vendor_account_id"), {}).get("email", ""),
                    "Status": CLAIM_STATUS_LABEL.get(c.get("status"), c.get("status")),
                    "By": c.get("approved_by") or "—",
                    "When": _fmt_dt(c.get("approved_at")),
                } for c in decided]),
                width="stretch", hide_index=True,
            )


def _render_live_side(kind, snap):
    """The right-hand column: what we currently hold, for this kind."""
    if not snap:
        st.caption("Could not load current values.")
        return
    venue = snap.get("venue") or {}
    spaces = snap.get("spaces") or []
    docs = snap.get("documents") or []

    if kind == "description":
        st.write(venue.get("Description") or "_nothing on file_")
    elif kind == "attributes":
        st.write(f"Venue type: {venue.get('Venue_Type') or '—'}")
        st.write(f"Max seated: {venue.get('Max_Capacity_Seated') or '—'}")
        st.write(f"Tags: {_val(venue.get('flt_venue_attributes'))}")
    elif kind == "pricing":
        if not spaces:
            st.caption("No pricing on file.")
        for s in spaces:
            per_person = s.get("fb_min_is_per_person")
            corrected = s.get("fb_min_type_corrected")
            st.markdown(f"**{s.get('space_label', '—')}**")
            st.write(
                f"venue fee {_money(s.get('venue_fee'))} · "
                f"F&B min {_money(s.get('fb_min'))} "
                f"({'per person' if per_person else 'total'}"
                f"{', corrected by fn54' if corrected else ''}) · "
                f"per-person {_money(s.get('pp_fb'))} · "
                f"seats {s.get('capacity_seated') or '—'}"
            )
    elif kind == "pdfs":
        if not docs:
            st.caption("No documents on file.")
        for d in docs:
            st.write(
                f"{d.get('Name') or d.get('PDF_ID')} · "
                f"{d.get('Year_of_Pricing') or '—'} · "
                f"{d.get('extraction_status') or 'not extracted'}"
            )
    elif kind == "images":
        st.write(f"{venue.get('flt_space_count') or 0} spaces on file")
        st.caption(
            "Image slots live on table 11 (image_1/2/3) and are not shown here — "
            "check the vendor page itself."
        )
    else:
        st.caption("No live counterpart — availability is not stored yet.")


def _render_submissions(submissions, accounts_by_id, actor):
    st.subheader("Submissions")

    if not submissions:
        st.caption("No submissions yet.")
        return

    counts = pd.Series([s.get("status") for s in submissions]).value_counts().to_dict()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pending", counts.get("pending", 0))
    c2.metric("Approved, not applied", counts.get("approved", 0))
    c3.metric("Applied", counts.get("applied", 0))
    c4.metric("Rejected", counts.get("rejected", 0))

    f1, f2 = st.columns(2)
    status_filter = f1.selectbox(
        "Status",
        ["pending", "approved", "applied", "rejected", "withdrawn", "all"],
        index=0,
        key="vport_status",
    )
    kinds = sorted({s.get("kind") for s in submissions if s.get("kind")})
    kind_filter = f2.selectbox("Type", ["all"] + kinds, index=0, key="vport_kind")

    rows = [
        s for s in submissions
        if (status_filter == "all" or s.get("status") == status_filter)
        and (kind_filter == "all" or s.get("kind") == kind_filter)
    ]
    if not rows:
        st.caption("Nothing matches that filter.")
        return

    st.caption(
        f"{len(rows)} shown. Approving records a decision — it does **not** write to "
        f"table 11 or 36. Copy the change across by hand, then mark it applied. "
        f"Withdrawn ones were retracted by the vendor before anyone looked; there is "
        f"nothing to do with them."
    )

    for s in rows:
        acct = accounts_by_id.get(s.get("vendor_account_id"), {})
        kind = s.get("kind")
        header = (
            f"{KIND_LABEL.get(kind, kind)} · {s.get('vendor_id')} · "
            f"{acct.get('email', 'unknown')} · {_fmt_dt(s.get('created_at'))} · "
            f"{SUBMISSION_STATUS_LABEL.get(s.get('status'), s.get('status'))}"
        )
        with st.expander(header, expanded=False):
            left, right = st.columns(2)
            with left:
                st.markdown("**They propose**")
                payload = s.get("payload") or {}
                if isinstance(payload, dict):
                    for k, v in payload.items():
                        st.write(f"**{k.replace('_', ' ')}**: {_val(v)}")
                else:
                    st.write(_val(payload))

                if kind == "pricing" and isinstance(payload, dict):
                    fb_type = payload.get("fb_min_type")
                    if fb_type == "per_person":
                        st.warning(
                            "They marked the F&B minimum **per person**. Check that "
                            "against the sheet: a total minimum typed as per-head gets "
                            "multiplied by guest count and is the single most damaging "
                            "error in this dataset."
                        )
            with right:
                st.markdown("**We currently hold**")
                _render_live_side(kind, load_snapshot(s.get("vendor_id")))

            if s.get("review_note"):
                st.info(f"Existing note: {s['review_note']}")

            note = st.text_area(
                "Note back to the vendor (they see this)",
                value=s.get("review_note") or "",
                key=f"vport_note_{s['id']}",
                height=80,
            )
            b1, b2, b3, _ = st.columns([1, 1, 1, 2])
            if b1.button("Approve", key=f"vport_sub_ok_{s['id']}", type="primary"):
                _after_write(*_update_submission(s["id"], "approved", actor, note))
            if b2.button("Mark applied", key=f"vport_sub_app_{s['id']}"):
                _after_write(*_update_submission(s["id"], "applied", actor, note))
            if b3.button("Reject", key=f"vport_sub_no_{s['id']}"):
                _after_write(*_update_submission(s["id"], "rejected", actor, note))


def _render_messages(messages, actor):
    new = [m for m in messages if m.get("status") == "new"]
    st.subheader(f"Help requests ({len(new)} new)")

    undelivered = [m for m in messages if not m.get("slack_delivered")]
    if undelivered:
        st.error(
            f"{len(undelivered)} message(s) never reached Slack. They are safe here — "
            f"the row is always written before the ping — but nobody was notified. "
            f"Check the Slack config in Xano."
        )

    if not new:
        st.caption("Nothing new.")
    for m in new:
        with st.expander(
            f"**{m.get('email')}** · {m.get('topic') or 'no topic'} · "
            f"{_fmt_dt(m.get('created_at'))}",
            expanded=False,
        ):
            st.write(m.get("message") or "")
            st.caption(
                f"Name: {m.get('name') or '—'} · Venue: {m.get('vendor_id') or '—'} · "
                f"Slack: {'delivered' if m.get('slack_delivered') else 'NOT delivered'}"
            )
            notes = st.text_input(
                "Internal note", value=m.get("admin_notes") or "", key=f"vport_msg_n_{m['id']}"
            )
            if st.button("Mark handled", key=f"vport_msg_h_{m['id']}", type="primary"):
                _after_write(*_update_message(m["id"], "handled", actor, notes))

    done = [m for m in messages if m.get("status") == "handled"]
    if done:
        with st.expander(f"Handled ({len(done)})", expanded=False):
            st.dataframe(
                pd.DataFrame([{
                    "From": m.get("email"),
                    "Topic": m.get("topic") or "—",
                    "When": _fmt_dt(m.get("created_at")),
                    "By": m.get("handled_by") or "—",
                    "Note": m.get("admin_notes") or "",
                } for m in done]),
                width="stretch", hide_index=True,
            )


# ── Entry point ────────────────────────────────────────────────────────────────
def _render_calendars(calendars, accounts_by_id):
    """Calendar connections and their sync health.

    READ ONLY ON PURPOSE. There is no approve/reject here because a calendar is
    not a submission: it proposes nothing about pricing, feeds no benchmark, and
    a vendor misreporting their own availability only costs them bookings. What
    this table is actually for is spotting a feed that has silently stopped
    syncing — which looks exactly like a vendor with no bookings unless someone
    is watching last_sync_at.
    """
    st.markdown("### 📅 Calendar connections")
    st.caption(
        "Vendors paste a secret iCal address; we read busy dates only, nightly. "
        "Not shown to couples yet."
    )

    if not calendars:
        st.info("No vendor has connected a calendar yet.")
        return

    stale, errored = [], []
    for c in calendars:
        if (c.get("status") or "") == "error":
            errored.append(c)
        else:
            age = _age_hours(c.get("last_sync_at"))
            if age is not None and age > 48:
                stale.append(c)

    if errored:
        st.warning(
            f"⚠️ {len(errored)} calendar(s) failed to sync. Their dates are frozen at the "
            "last good read."
        )
    if stale:
        st.warning(
            f"⏳ {len(stale)} calendar(s) have not synced in over 48h — check the nightly job."
        )

    rows = []
    for c in calendars:
        acct = accounts_by_id.get(c.get("vendor_account_id")) or {}
        rows.append({
            "id": c.get("id"),
            "Venue": c.get("vendor_id") or "—",
            "Vendor": acct.get("email") or f"account {c.get('vendor_account_id')}",
            "Label": c.get("label") or "—",
            "Covers": c.get("represents") or "—",
            "Status": c.get("status") or "—",
            "Dates taken": c.get("busy_count") or 0,
            "Last synced": _fmt_dt(c.get("last_sync_at")) or "never",
            "Error": (c.get("last_sync_error") or "")[:120],
        })

    st.dataframe(rows, hide_index=True, use_container_width=True)


def render_vendor_portal_tab(actor=""):
    """actor is the signed-in admin email; it is stamped on every decision."""
    st.header("🏛️ Vendor Portal")

    with st.expander("ℹ️ What this tab is", expanded=False):
        st.markdown(
            """
**Vendors can only ever add a row to one table.** They cannot edit or delete anything,
and nothing they send has touched live data.

**Approving here still does not touch live data.** It records a decision and stores a
note the vendor sees. Copying an approved change into table 11 / 36 is a manual step you
do yourself — which is why every submission is shown beside what we currently hold.

Statuses: *pending* (unseen) → *approved* (content is good, not yet copied across) →
*applied* (copied in by hand). The gap between approved and applied is the only record of
what has actually landed, so don't skip straight to applied unless you really did it.

*withdrawn* is the vendor retracting something before you looked — a typo fix, usually.
It is not a delete: their payload is still on the row, it has just left your queue.

Two levels of access: an approved **account** says we know who someone is; an approved
**claim** says they may speak for one venue. Approving a claim lifts the account too.
"""
        )

    if st.button("🔄 Refresh", key="vport_refresh"):
        st.cache_data.clear()
        st.rerun()

    data = load_queue()
    if data is None:
        st.error(
            "Could not load the vendor queue. Check ANALYTICS_EXPORT_SECRET and that "
            f"the Vendor Portal group is reachable at {VENDOR_BASE}."
        )
        return

    accounts_by_id = {a["id"]: a for a in data["accounts"] if "id" in a}

    _render_claims(data["claims"], accounts_by_id, actor)
    st.divider()
    _render_submissions(data["submissions"], accounts_by_id, actor)
    st.divider()
    _render_messages(data["messages"], actor)
    st.divider()
    _render_calendars(load_calendars(), accounts_by_id)
