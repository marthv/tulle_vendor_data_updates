"""
🗣️ Feedback Triage — the queue for in-app feedback, for the Tulle admin dashboard.

Self-contained: dashboard.py just does
    from feedback_triage import render_feedback_triage_tab
    with tab_fb:
        render_feedback_triage_tab(XANO_BASE, user_email)

WHAT THIS REPLACES
------------------
Reports land in Slack #feedback via a bot and then stop. There is no queue, no owner, no
state, and — the part that actually costs money — no record of whether anyone ever replied
to the person who wrote in. 112 reports, none of them closed. This tab is that record.

Slack keeps only the notification role. State lives in Xano:
    Feedback (table 35).completed          — open / closed. The ONE place status lives.
    feedback_triage_state (table 73)       — vendor link, notes, who handled it, last reply.
    admin_audit (table 72)                 — every write this tab makes, append-only.

Endpoints (all secret-gated with ANALYTICS_EXPORT_SECRET, group 10):
    GET  /admin/feedback_queue      (ep 271)
    GET  /admin/vendor_lookup       (ep 272) — mode=search | mode=ids
    POST /admin/vendor_field_update (ep 273) — the ONLY live-data write path
    POST /admin/feedback_update     (ep 274)

THE TWO THINGS TO UNDERSTAND BEFORE USING IT
--------------------------------------------
1. A feedback row does not know which vendor it is about. Nothing in table 35 references a
   vendor — the reporter never told us. Linking a report to a venue is a judgement a human
   makes by reading the complaint, which is why the vendor picker is a search box and not a
   lookup, and why the chosen id is stored back so the judgement is not made twice.

2. Hiding a vendor is not a neutral undo. `Validated_Data` = "0" removes it from Vendor
   Discovery (ep119 filters `Validated_Data == 1`). When a report says "this venue has no
   pricing", there are two very different causes, and the picker labels which one you have:
       nothing worth showing  — no pricing rows, or every price is zero. Hiding is right.
       the data is fine       — real prices exist and are not reaching the user. Hiding
                                deletes correct, expensively-extracted data to fix what is
                                actually a display or link bug.
   The second case is the expensive mistake, so it is called out in red rather than left for
   you to infer from four numbers.

Deliberately cached for only 60s: someone is waiting on a reply.
"""

import base64
import json
import os
import time
import datetime as _dt
from email.message import EmailMessage

import pandas as pd
import requests
import streamlit as st

EXPORT_SECRET = os.environ.get(
    "ANALYTICS_EXPORT_SECRET",
    "ttv_export_da19ae7c3fbcdd2c51747199117a63a33f848ca9",
)

# Reply-from address. The Gmail account that authorised the send token must own it.
FROM_EMAIL = os.environ.get("FEEDBACK_FROM_EMAIL", "hello@tulletogether.com")

# WIDGET KEYS: every key in this module is prefixed `fbt_`. Streamlit keys are global across
# the whole app, not scoped per tab — `vp_*` is Venue Pricing and `vport_*` is Vendor Portal,
# and colliding with either crashes the page with StreamlitDuplicateElementKey.

# Mirror of the server-side allowlist in ep273. The server is the authority — this copy exists
# so the UI can offer a sensible dropdown and reject obvious mistakes before a round trip.
# Keep the two in step: adding a column here without adding it there produces a confusing
# "not writable from the admin dashboard" error at apply time.
ALLOWED_COLUMNS = {
    "Validated_Data":       "Visible in search (1 = shown, 0 = hidden)",
    "Name":                 "Vendor name",
    "Website":              "Website URL",
    "Address":              "Street address",
    "State":                "State (comma-separated for multi-state)",
    "Country":              "Country",
    "Category":             "Category (Venue, Photographer, …)",
    "Venue_Type":           "Venue type (All-inclusive, Semi-inclusive, …)",
    "Max_Capacity_Seated":  "Max seated capacity (number)",
    "Description":          "Description",
    "Contact_Information":  "Contact information",
    "Flags":                "Flags",
    "Place_ID":             "Google Place ID",
    "Type_of_Photography":  "Type of photography",
    "Type_of_Entertainment": "Type of entertainment",
    "Type_of_Beauty":       "Type of beauty",
}

_SEVERITY_HELP = (
    "Severity is what the reporter chose, 1–10. It is self-reported, so treat it as a hint "
    "about how annoyed they are, not about how broken the product is."
)


# ── HTTP (same retry shape as cohorts.py / roadmap_orders.py) ──────────────────
def _xget(url, tries=4, timeout=30):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:  # transport only; Xano answers 4xx with a body
            last = str(e)
        time.sleep(0.6 * (i + 1))
    raise RuntimeError(last or "request failed")


def _xpost(url, payload, tries=3, timeout=30):
    """POST and return (ok, parsed_or_error_text).

    Xano answers a rejected precondition with a 4xx and a JSON body carrying the message we
    wrote in the endpoint — those messages explain the rails, so they are surfaced verbatim
    rather than collapsed into "failed".
    """
    last = None
    for i in range(tries):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            if r.status_code == 200:
                return True, r.json()
            try:
                body = r.json()
                return False, body.get("message") or json.dumps(body)[:400]
            except Exception:
                return False, f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as e:
            last = str(e)
        time.sleep(0.6 * (i + 1))
    return False, last or "request failed"


@st.cache_data(ttl=60, show_spinner=False)
def load_queue(xano_base: str):
    """Reports + ops annotations, joined in pandas. Newest first."""
    data = _xget(f"{xano_base}/admin/feedback_queue?secret={EXPORT_SECRET}")
    reports = pd.DataFrame(data.get("reports") or [])
    triage = pd.DataFrame(data.get("triage") or [])

    if reports.empty:
        return reports

    if triage.empty:
        for col in ("vendor_id", "admin_notes", "handled_by", "last_email_subject"):
            reports[col] = ""
        for col in ("handled_at", "last_email_at"):
            reports[col] = 0
    else:
        triage = triage.drop(columns=["id"], errors="ignore")
        reports = reports.merge(
            triage, how="left", left_on="id", right_on="feedback_id"
        ).drop(columns=["feedback_id"], errors="ignore")
        for col in ("vendor_id", "admin_notes", "handled_by", "last_email_subject"):
            reports[col] = reports.get(col, "").fillna("")
        for col in ("handled_at", "last_email_at"):
            reports[col] = reports.get(col, 0).fillna(0)

    return reports


@st.cache_data(ttl=60, show_spinner=False)
def _hydrate_vendors(xano_base: str, ids_key: str):
    """Bulk-fetch the vendors the queue links to. `ids_key` is a sorted comma string so the
    cache key is stable — a list argument is unhashable and a set reorders between runs."""
    ids = [i for i in ids_key.split(",") if i]
    if not ids:
        return {}
    qs = "&".join(f"ids[]={requests.utils.quote(i)}" for i in ids)
    rows = _xget(f"{xano_base}/admin/vendor_lookup?secret={EXPORT_SECRET}&mode=ids&{qs}")
    return {r["Vendor_ID"]: r for r in (rows or [])}


def _vendor_search(xano_base: str, q: str, limit: int = 15):
    q = (q or "").strip()
    if not q:
        return []
    url = (
        f"{xano_base}/admin/vendor_lookup?secret={EXPORT_SECRET}"
        f"&mode=search&q={requests.utils.quote(q)}&limit={limit}"
    )
    try:
        return _xget(url) or []
    except Exception as e:
        st.error(f"Vendor search failed: {e}")
        return []


# ── Formatting ────────────────────────────────────────────────────────────────
def _ts(ms) -> str:
    try:
        ms = int(ms or 0)
    except Exception:
        return "—"
    if ms <= 0:
        return "—"
    return _dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def _age_days(ms) -> int:
    try:
        ms = int(ms or 0)
    except Exception:
        return 0
    if ms <= 0:
        return 0
    return max(0, int((time.time() * 1000 - ms) / 86400000))


def _pricing_verdict(v: dict):
    """(label, tone, explanation) for whether hiding this vendor destroys real data.

    Reads the recompute_vendor_filter_aggregates denorms on table 11 rather than scanning
    table 36 — one indexed read instead of a full pricing scan, and the same numbers ep119
    itself filters on.
    """
    rows = int(v.get("flt_space_count") or 0)
    fee = int(v.get("flt_min_venue_fee") or 0)
    ppfb = int(v.get("flt_min_ppfb") or 0)
    fbmin = int(v.get("flt_min_fbmin") or 0)

    if rows == 0:
        return (
            "Nothing to show",
            "ok",
            "No pricing rows at all. Hiding this costs the user nothing.",
        )
    if not (fee or ppfb or fbmin):
        return (
            "Nothing to show",
            "ok",
            f"{rows} pricing row(s), but every price is zero. Hiding this costs the user nothing.",
        )
    parts = []
    if fee:
        parts.append(f"venue fee from ${fee:,}")
    if ppfb:
        parts.append(f"per-person F&B from ${ppfb:,}")
    if fbmin:
        parts.append(f"F&B minimum from ${fbmin:,}")
    return (
        "Real pricing exists",
        "warn",
        f"{rows} pricing row(s) with " + ", ".join(parts) + ". If a user says they see "
        "nothing here, the data is fine and the LINK is broken — hiding it deletes correct "
        "pricing instead of fixing the bug.",
    )


# ── Gmail ─────────────────────────────────────────────────────────────────────
def _gmail_token():
    """Return (access_token, how) or (None, reason).

    Two paths, in order of how little setup they need:
      1. GMAIL_SEND_REFRESH_TOKEN — a refresh token for the FROM_EMAIL mailbox with the
         gmail.send scope, exchanged against the dashboard's existing OAuth client. No
         Workspace admin involvement.
      2. GOOGLE_SERVICE_ACCOUNT_JSON + GMAIL_IMPERSONATE — domain-wide delegation. Needs a
         Workspace admin to grant the service account the gmail.send scope.
    """
    rt = os.environ.get("GMAIL_SEND_REFRESH_TOKEN", "").strip()
    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    cs = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if rt and cid and cs:
        try:
            r = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": cid,
                    "client_secret": cs,
                    "refresh_token": rt,
                    "grant_type": "refresh_token",
                },
                timeout=20,
            )
            if r.status_code == 200 and r.json().get("access_token"):
                return r.json()["access_token"], "refresh token"
            return None, f"refresh token rejected: {r.text[:200]}"
        except Exception as e:
            return None, f"token exchange failed: {e}"

    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    impersonate = os.environ.get("GMAIL_IMPERSONATE", "").strip()
    if sa_json and impersonate:
        try:
            import google.auth.transport.requests
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_info(
                json.loads(sa_json),
                scopes=["https://www.googleapis.com/auth/gmail.send"],
                subject=impersonate,
            )
            creds.refresh(google.auth.transport.requests.Request())
            return creds.token, "service account"
        except Exception as e:
            return None, f"service-account delegation failed: {e}"

    return None, "not configured"


def _gmail_send(to_addr: str, subject: str, body: str):
    """(ok, detail). Only a real Gmail message id counts as sent."""
    token, how = _gmail_token()
    if not token:
        return False, how

    msg = EmailMessage()
    msg["To"] = to_addr
    msg["From"] = FROM_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        r = requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"raw": raw},
            timeout=30,
        )
    except Exception as e:
        return False, f"send failed: {e}"

    # A 200 with no message id is not a send. Gmail answers errors with 4xx + a JSON reason,
    # so branch on the status rather than assuming the absence of an exception means delivery.
    if r.status_code == 200 and (r.json() or {}).get("id"):
        return True, f"sent via {how} (message {r.json()['id']})"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


# ── The natural-language rail ─────────────────────────────────────────────────
_PROPOSE_SYSTEM = """You turn a wedding-venue ops instruction into ONE proposed database edit.

You are given the current row from the vendor table and an instruction from an admin. Return
the single column that should change and its new value.

Rules:
- You may only propose a column from the allowed list you are given. Never invent one.
- Propose exactly one column. If the instruction needs several, pick the single most important
  one and say so in `reason`.
- `new_value` is always a string. For Max_Capacity_Seated give digits only.
- Validated_Data is "1" (visible in search) or "0" (hidden).
- If the instruction is unclear, or asks for something no allowed column can express, set
  `column` to "" and explain why in `reason`. Refusing is a correct answer — a human reads
  this before anything is written.
- Do not restate the old value as the new value.

Reply with ONLY a JSON object, no prose and no code fence:
{"column": "...", "new_value": "...", "reason": "one sentence"}"""


def _propose_edit(instruction: str, vendor: dict):
    """Ask Claude for a proposed edit. Returns (proposal_dict, error_str).

    This never writes anything. It produces a suggestion a human reads and approves; the
    approval is what triggers the write, and the write is separately guarded server-side by
    the column allowlist and an optimistic lock on the value shown here.
    """
    try:
        from anthropic import Anthropic
    except Exception as e:
        return None, f"anthropic SDK unavailable: {e}"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, "ANTHROPIC_API_KEY is not set on this deployment."

    facts = {k: vendor.get(k) for k in ALLOWED_COLUMNS if k in vendor}
    facts["Vendor_ID"] = vendor.get("Vendor_ID")
    facts["Name"] = vendor.get("Name")

    user = (
        f"Allowed columns: {json.dumps(list(ALLOWED_COLUMNS.keys()))}\n\n"
        f"Current vendor row (only the allowed columns are shown):\n"
        f"{json.dumps(facts, indent=2)}\n\n"
        f"Admin instruction:\n{instruction.strip()}"
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            system=_PROPOSE_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        return None, f"model call failed: {e}"

    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]

    try:
        prop = json.loads(text)
    except Exception:
        return None, f"could not parse the model's reply: {text[:300]}"

    col = (prop.get("column") or "").strip()
    if not col:
        return None, prop.get("reason") or "The instruction did not map to an editable field."
    if col not in ALLOWED_COLUMNS:
        return None, (
            f"Proposed a column that is not writable from here ({col}). "
            "Nothing was changed."
        )
    prop["column"] = col
    prop["new_value"] = str(prop.get("new_value", ""))
    return prop, None


# ── Writes ────────────────────────────────────────────────────────────────────
def _save_feedback(xano_base, feedback_id, actor, **fields):
    payload = {"secret": EXPORT_SECRET, "feedback_id": int(feedback_id), "actor": actor}
    payload.update({k: v for k, v in fields.items() if v is not None})
    return _xpost(f"{xano_base}/admin/feedback_update", payload)


def _write_vendor_field(xano_base, vendor_id, column, new_value, expected_old,
                        actor, feedback_id, note):
    return _xpost(
        f"{xano_base}/admin/vendor_field_update",
        {
            "secret": EXPORT_SECRET,
            "vendor_id": vendor_id,
            "column": column,
            "new_value": str(new_value),
            "expected_old": "" if expected_old is None else str(expected_old),
            "actor": actor,
            "feedback_id": int(feedback_id or 0),
            "note": note or "",
        },
    )


def _refresh():
    load_queue.clear()
    _hydrate_vendors.clear()
    st.rerun()


# ── Email modal ───────────────────────────────────────────────────────────────
@st.dialog("Reply to reporter", width="large")
def _email_dialog(xano_base, row, actor):
    rid = int(row["id"])
    to_addr = str(row.get("user_email") or "")

    token, how = _gmail_token()
    if token:
        st.caption(f"Sending is live — authorised via {how}, as {FROM_EMAIL}.")
    else:
        st.warning(
            f"Sending is not configured on this deployment ({how}). You can still write the "
            f"reply, copy it, send it yourself, and then mark it as replied so the queue "
            f"stays honest.\n\nTo enable sending: set **GMAIL_SEND_REFRESH_TOKEN** (a refresh "
            f"token for {FROM_EMAIL} with the `gmail.send` scope, against the dashboard's "
            f"existing GOOGLE_CLIENT_ID/SECRET), or **GMAIL_IMPERSONATE={FROM_EMAIL}** with "
            f"domain-wide delegation on GOOGLE_SERVICE_ACCOUNT_JSON."
        )

    c1, c2 = st.columns(2)
    c1.text_input("From", value=FROM_EMAIL, disabled=True, key=f"fbt_from_{rid}")
    to_val = c2.text_input("To", value=to_addr, key=f"fbt_to_{rid}")

    default_subject = "Re: your Tulle Together feedback"
    subject = st.text_input("Subject", value=default_subject, key=f"fbt_subj_{rid}")

    quoted = "\n".join("> " + ln for ln in str(row.get("details") or "").splitlines())
    default_body = (
        f"Hi,\n\nThanks for writing in about Tulle Together — and sorry for the trouble.\n\n"
        f"You told us:\n{quoted}\n\n"
        f"\n\nIf anything else comes up, just reply to this email and it comes straight to us.\n\n"
        f"— The Tulle Together team\n"
    )
    body = st.text_area("Body", value=default_body, height=280, key=f"fbt_body_{rid}")

    st.divider()
    b1, b2, b3 = st.columns([1, 1, 1])

    if b1.button("Send", type="primary", key=f"fbt_send_{rid}", disabled=not token):
        if not to_val.strip():
            st.error("No recipient.")
        else:
            ok, detail = _gmail_send(to_val.strip(), subject, body)
            if ok:
                # Stamp last_email_at only now — after a real message id came back. A
                # timestamp written on intent would answer "did we reply?" wrongly.
                _save_feedback(
                    xano_base, rid, actor, email_sent="1", email_subject=subject
                )
                st.success(detail)
                _refresh()
            else:
                st.error(f"Not sent — {detail}. Nothing was marked as replied.")

    if b2.button("Mark as replied", key=f"fbt_markrep_{rid}",
                 help="Use this if you sent the reply yourself from another mail client."):
        ok, res = _save_feedback(xano_base, rid, actor, email_sent="1", email_subject=subject)
        if ok:
            st.success("Recorded.")
            _refresh()
        else:
            st.error(res)

    if b3.button("Cancel", key=f"fbt_cancel_{rid}"):
        st.rerun()


# ── One report ────────────────────────────────────────────────────────────────
def _render_report(xano_base, row, actor, vendors):
    rid = int(row["id"])
    closed = bool(row.get("completed"))
    vendor_id = str(row.get("vendor_id") or "")
    emailed = int(row.get("last_email_at") or 0) > 0
    sev = int(row.get("severity") or 0)

    badge = "✅ Closed" if closed else "🔴 Open"
    age = _age_days(row.get("created_at"))
    head = (
        f"{badge} · #{rid} · {row.get('user_email') or 'no email'} · "
        f"{row.get('page') or 'unknown page'} · {age}d old"
    )
    if sev:
        head += f" · severity {sev}"
    if not emailed and not closed:
        head += " · ✉️ never answered"

    with st.container(border=True):
        st.markdown(f"**{head}**")
        st.write(row.get("details") or "_(no details)_")

        meta = [
            f"submitted {_ts(row.get('created_at'))}",
            f"category: {row.get('category') or '—'}",
            f"{row.get('operating_system') or '—'} · {row.get('device_width_size') or '—'}px",
        ]
        if emailed:
            meta.append(f"last reply {_ts(row.get('last_email_at'))}")
        if row.get("handled_by"):
            meta.append(f"last touched by {row['handled_by']}")
        st.caption(" · ".join(meta))

        with st.expander("Address this report", expanded=False):
            # ── notes + status ────────────────────────────────────────────────
            notes = st.text_area(
                "Internal notes",
                value=str(row.get("admin_notes") or ""),
                key=f"fbt_notes_{rid}",
                height=80,
                help="Never shown to the reporter.",
            )
            n1, n2, n3 = st.columns([1, 1, 1])
            if n1.button("Save notes", key=f"fbt_savenotes_{rid}"):
                ok, res = _save_feedback(
                    xano_base, rid, actor, admin_notes=(notes or "-")
                )
                if ok:
                    st.success("Saved.")
                    _refresh()
                else:
                    st.error(res)

            if closed:
                if n2.button("Reopen", key=f"fbt_reopen_{rid}"):
                    ok, res = _save_feedback(xano_base, rid, actor, set_completed="0")
                    if ok:
                        _refresh()
                    else:
                        st.error(res)
            else:
                if n2.button("Close", type="primary", key=f"fbt_close_{rid}"):
                    ok, res = _save_feedback(xano_base, rid, actor, set_completed="1")
                    if ok:
                        _refresh()
                    else:
                        st.error(res)

            if n3.button("✉️ Draft reply", key=f"fbt_email_{rid}"):
                _email_dialog(xano_base, row, actor)

            st.divider()

            # ── vendor link ───────────────────────────────────────────────────
            st.markdown("**Vendor this report is about**")
            vendor = vendors.get(vendor_id) if vendor_id else None

            if vendor:
                label, tone, why = _pricing_verdict(vendor)
                visible = str(vendor.get("Validated_Data") or "") == "1"
                st.markdown(
                    f"`{vendor['Vendor_ID']}` **{vendor.get('Name')}** — "
                    f"{vendor.get('State') or '—'} · {vendor.get('Category') or '—'} · "
                    f"{'👁️ visible in search' if visible else '🚫 hidden from search'}"
                )
                (st.warning if tone == "warn" else st.info)(f"**{label}.** {why}")

                v1, v2, v3 = st.columns([1, 1, 1])
                if visible:
                    if v1.button("🚫 Hide from search", key=f"fbt_hide_{rid}"):
                        ok, res = _write_vendor_field(
                            xano_base, vendor["Vendor_ID"], "Validated_Data", "0",
                            vendor.get("Validated_Data"), actor, rid,
                            f"Hidden from the feedback queue, report #{rid}",
                        )
                        if ok:
                            st.success(f"{vendor.get('Name')} is now hidden from search.")
                            _refresh()
                        else:
                            st.error(res)
                else:
                    if v1.button("👁️ Show in search", type="primary", key=f"fbt_show_{rid}"):
                        ok, res = _write_vendor_field(
                            xano_base, vendor["Vendor_ID"], "Validated_Data", "1",
                            vendor.get("Validated_Data"), actor, rid,
                            f"Restored from the feedback queue, report #{rid}",
                        )
                        if ok:
                            st.success(f"{vendor.get('Name')} is visible in search again.")
                            _refresh()
                        else:
                            st.error(res)

                if v2.button("Unlink vendor", key=f"fbt_unlink_{rid}"):
                    ok, res = _save_feedback(xano_base, rid, actor, vendor_id="-")
                    if ok:
                        _refresh()
                    else:
                        st.error(res)

                if vendor.get("Website"):
                    v3.link_button("Open website", vendor["Website"])

                st.divider()
                _render_field_fix(xano_base, rid, vendor, actor)

            else:
                q = st.text_input(
                    "Search by venue name or Vendor_ID",
                    key=f"fbt_vq_{rid}",
                    placeholder="e.g. Raritan Inn, or V3488",
                    help="Feedback rows carry no vendor reference — the reporter never told "
                         "us which venue. Read the complaint and pick it.",
                )
                if q:
                    hits = _vendor_search(xano_base, q)
                    if not hits:
                        st.caption("No matches.")
                    for h in hits[:10]:
                        hc1, hc2 = st.columns([5, 1])
                        vis = "👁️" if str(h.get("Validated_Data")) == "1" else "🚫"
                        hc1.write(
                            f"{vis} `{h['Vendor_ID']}` **{h.get('Name')}** — "
                            f"{h.get('State') or '—'} · {h.get('Category') or '—'} · "
                            f"{int(h.get('flt_space_count') or 0)} pricing row(s)"
                        )
                        if hc2.button("Link", key=f"fbt_link_{rid}_{h['Vendor_ID']}"):
                            ok, res = _save_feedback(
                                xano_base, rid, actor, vendor_id=h["Vendor_ID"]
                            )
                            if ok:
                                _refresh()
                            else:
                                st.error(res)


def _render_field_fix(xano_base, rid, vendor, actor):
    """The free-text rail: describe the change, read the diff, then approve it.

    The preview is not decoration. The value shown here is sent back as `expected_old` and the
    server refuses the write if the row has moved since — so what you approve is what lands,
    or nothing does.
    """
    st.markdown("**Fix a field on this vendor**")
    st.caption(
        "Describe the change in plain English. Nothing is written until you approve the diff."
    )

    prop_key = f"fbt_prop_{rid}"
    instruction = st.text_input(
        "What needs changing?",
        key=f"fbt_instr_{rid}",
        placeholder="e.g. the website should be https://example.com — the current one is a dead link",
    )

    pc1, pc2 = st.columns([1, 3])
    if pc1.button("Preview change", key=f"fbt_preview_{rid}", disabled=not instruction.strip()):
        with st.spinner("Working out what that means…"):
            prop, err = _propose_edit(instruction, vendor)
        if err:
            st.session_state.pop(prop_key, None)
            st.warning(err)
        else:
            prop["instruction"] = instruction
            st.session_state[prop_key] = prop

    prop = st.session_state.get(prop_key)
    if prop:
        col = prop["column"]
        old = vendor.get(col)
        old_disp = "" if old is None else str(old)
        st.markdown(
            f"**{ALLOWED_COLUMNS.get(col, col)}** (`{col}`) on `{vendor['Vendor_ID']}`"
        )
        d1, d2 = st.columns(2)
        d1.text_area("Current", value=old_disp, disabled=True, height=90,
                     key=f"fbt_old_{rid}")
        d2.text_area("Proposed", value=prop["new_value"], disabled=True, height=90,
                     key=f"fbt_new_{rid}")
        st.caption(prop.get("reason") or "")

        if old_disp == prop["new_value"]:
            st.info("That is already the current value — nothing to write.")
            return

        a1, a2 = st.columns([1, 1])
        if a1.button("Apply this change", type="primary", key=f"fbt_apply_{rid}"):
            ok, res = _write_vendor_field(
                xano_base, vendor["Vendor_ID"], col, prop["new_value"], old_disp,
                actor, rid, prop.get("instruction") or "",
            )
            if ok:
                st.session_state.pop(prop_key, None)
                st.success(f"{col} updated on {vendor.get('Name')}.")
                _refresh()
            else:
                st.error(res)
        if a2.button("Discard", key=f"fbt_discard_{rid}"):
            st.session_state.pop(prop_key, None)
            st.rerun()


# ── Tab ───────────────────────────────────────────────────────────────────────
def render_feedback_triage_tab(xano_base: str, user_email: str = ""):
    actor = user_email or "unknown@tulletogether.com"

    st.subheader("🗣️ Feedback triage")

    with st.expander("ℹ️ What this queue is for", expanded=False):
        st.markdown(
            "Every in-app feedback report, with an owner and a state. Three things happen "
            "here that could not happen in Slack:\n\n"
            "- **Open / closed per report**, stored on the Feedback row itself, so the "
            "backlog is a number rather than a scroll.\n"
            "- **Hide or show the vendor** a report is about. Hiding sets `Validated_Data` "
            "to `0`, which removes it from Vendor Discovery search. The picker tells you "
            "first whether that vendor actually has real pricing — if it does, hiding it "
            "destroys good data to work around a display bug.\n"
            "- **Reply to the reporter**, from "
            f"`{FROM_EMAIL}`, with the send recorded. The question this answers is *did "
            "anyone ever get back to this person* — which today has no answer at all.\n\n"
            "Every write from this tab lands in the `admin_audit` table with the value it "
            "replaced, so a bad change can be found and undone."
        )

    try:
        df = load_queue(xano_base)
    except Exception as e:
        st.error(f"Could not load the feedback queue: {e}")
        return

    if df.empty:
        st.info("No feedback reports.")
        return

    # ── headline numbers ──────────────────────────────────────────────────────
    total = len(df)
    open_n = int((~df["completed"].astype(bool)).sum())
    never_answered = int(
        ((df["last_email_at"].fillna(0).astype("int64") <= 0)
         & (~df["completed"].astype(bool))).sum()
    )
    linked = int((df["vendor_id"].astype(str) != "").sum())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Reports", total)
    m2.metric("Open", open_n)
    m3.metric("Open & never answered", never_answered)
    m4.metric("Linked to a vendor", linked)

    # ── filters ───────────────────────────────────────────────────────────────
    with st.container(border=True):
        f1, f2, f3 = st.columns([1, 1, 1])
        status = f1.radio(
            "Status", ["Open", "Closed", "All"], horizontal=True, key="fbt_status"
        )
        cats = sorted({c for c in df["category"].fillna("").astype(str) if c})
        pick_cats = f2.multiselect("Category", cats, key="fbt_cats")
        pages = sorted({p for p in df["page"].fillna("").astype(str) if p})
        pick_pages = f3.multiselect("Page", pages, key="fbt_pages")

        g1, g2, g3 = st.columns([2, 1, 1])
        search = g1.text_input(
            "Search the reports", key="fbt_search",
            placeholder="words in the report, or a reporter's email",
        )
        min_sev = g2.slider("Min severity", 0, 10, 0, key="fbt_sev", help=_SEVERITY_HELP)
        only_unanswered = g3.checkbox("Never answered only", key="fbt_unans")
        only_linked = g3.checkbox("Linked to a vendor only", key="fbt_linked")

    view = df.copy()
    if status == "Open":
        view = view[~view["completed"].astype(bool)]
    elif status == "Closed":
        view = view[view["completed"].astype(bool)]
    if pick_cats:
        view = view[view["category"].isin(pick_cats)]
    if pick_pages:
        view = view[view["page"].isin(pick_pages)]
    if min_sev:
        view = view[view["severity"].fillna(0).astype(int) >= min_sev]
    if only_unanswered:
        view = view[view["last_email_at"].fillna(0).astype("int64") <= 0]
    if only_linked:
        view = view[view["vendor_id"].astype(str) != ""]
    if search.strip():
        s = search.strip().lower()
        hay = (
            view["details"].fillna("").astype(str).str.lower()
            + " "
            + view["user_email"].fillna("").astype(str).str.lower()
        )
        view = view[hay.str.contains(s, regex=False)]

    st.caption(f"Showing {len(view)} of {total} reports.")
    if view.empty:
        return

    # One bulk call for every vendor the visible rows link to, rather than one per row.
    ids_key = ",".join(sorted({v for v in view["vendor_id"].astype(str) if v}))
    try:
        vendors = _hydrate_vendors(xano_base, ids_key)
    except Exception as e:
        st.warning(f"Could not load linked vendors: {e}")
        vendors = {}

    show_n = st.selectbox("Show", [25, 50, 100, 500], index=0, key="fbt_pagesize")
    for _, row in view.head(int(show_n)).iterrows():
        _render_report(xano_base, row, actor, vendors)
