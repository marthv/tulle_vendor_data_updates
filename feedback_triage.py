"""
🗣️ Feedback Triage — the queue for in-app feedback, for the Tulle admin dashboard.

Self-contained: dashboard.py just does
    from feedback_triage import render_feedback_triage_tab
    with tab_fb:
        render_feedback_triage_tab(XANO_BASE, user_email)

WHAT THIS REPLACES
------------------
Reports land in Slack #feedback via a bot and then stop. No queue, no owner, no state, and
no record of whether anyone ever replied. 112 reports, none closed. This tab is that record —
and, more to the point, the place the report gets FIXED.

WHAT THE REPORTS ACTUALLY ASK FOR (classified over all 112, 2026-08-31)
-----------------------------------------------------------------------
The first build assumed feedback was about vendors. It mostly is not:

    38  paid but still locked out     the single biggest bucket, by a wide margin
    20  internal test rows            noise that should never have been in the queue
    13  refund / billing              several genuinely double-charged
    11  feature requests              no data fix exists; reply and close
     6  bugs                          same
     5  a VENUE writing about its own listing
     4  saved venues lost             the known ep109 favorites wipe
     4  a specific venue's data is wrong
     1  a security report             sitting unread since it arrived
     1  account detail change

So the fix lives on the USER row far more often than on a vendor. Both are here, and the
two escalation buckets (venue reps, security) are surfaced rather than left to be found.

WHERE STATE LIVES
-----------------
    Feedback (table 35).completed          — open / closed. The ONE place status lives.
    feedback_triage_state (table 73)       — vendor link, notes, who handled it, last reply.
    admin_audit (table 72)                 — every write this tab makes, append-only.

Endpoints (all secret-gated with ANALYTICS_EXPORT_SECRET, group 10):
    GET  /admin/feedback_queue      (ep 271)
    GET  /admin/vendor_lookup       (ep 272) — mode=search | mode=ids
    POST /admin/vendor_field_update (ep 273) — railed write to vendor data
    POST /admin/feedback_update     (ep 274)
    GET  /admin/user_lookup         (ep 275) — the reporter's entitlement
    POST /admin/user_field_update   (ep 276) — railed write to the user row
    GET  /admin/audit_log           (ep 277) — what has actually been done, read-only

HOW A FIX GETS RECORDED
-----------------------
    the data change itself       -> admin_audit, automatically, on Apply, with old -> new
    a non-database action        -> the report's notes ("refunded in Stripe")
    you replied to the reporter  -> last_email_at, on a real send
    the report is done           -> Feedback.completed, via Close

The trail was being written from the first version but nothing read it back, so a report
could be fixed and closed with no way to see what was done. ep277 and the per-report history
close that: an audit log nobody can read is just overhead.

EVERY WRITE GOES THROUGH ONE CONFIRM GATE
-----------------------------------------
A quick-fix button and the free-text box do the same thing: they STAGE a proposal. Nothing is
written until the diff is on screen and you press Apply. The value shown in that diff is sent
back as `expected_old`, and the server refuses the write if the row moved since — so what you
approved is what lands, or nothing does. That is why the preview is not decoration.

THE TWO THINGS TO UNDERSTAND
----------------------------
1. A feedback row does not know which vendor it is about. Nothing in table 35 references a
   vendor — the reporter never told us. Linking is a judgement a human makes by reading the
   complaint, which is why the vendor picker is a search box, and why the pick is stored back.

2. Hiding a vendor is not a neutral undo. `Validated_Data` = "0" removes it from Vendor
   Discovery (ep119 filters `Validated_Data == 1`). When a report says "this venue has no
   pricing" there are two very different causes, and the picker labels which one you have —
   hiding a vendor that HAS real pricing deletes correct, expensively-extracted data to work
   around what is actually a display bug.

Deliberately cached for only 60s: someone is waiting on a reply.
"""

import base64
import json
import os
import re
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

# Mirrors of the server-side allowlists (ep273 / ep276). The server is the authority; these
# copies exist so the UI can label fields and reject obvious mistakes before a round trip.
# Keep them in step — a column here but not there fails confusingly at apply time.
VENDOR_COLUMNS = {
    "Validated_Data":        "Visible in search (1 = shown, 0 = hidden)",
    "Name":                  "Vendor name",
    "Website":               "Website URL",
    "Address":               "Street address",
    "State":                 "State (comma-separated for multi-state)",
    "Country":               "Country",
    "Category":              "Category (Venue, Photographer, …)",
    "Venue_Type":            "Venue type (All-inclusive, Semi-inclusive, …)",
    "Max_Capacity_Seated":   "Max seated capacity (number)",
    "Description":           "Description",
    "Contact_Information":   "Contact information",
    "Flags":                 "Flags",
    "Place_ID":              "Google Place ID",
    "Type_of_Photography":   "Type of photography",
    "Type_of_Entertainment": "Type of entertainment",
    "Type_of_Beauty":        "Type of beauty",
}

USER_COLUMNS = {
    "date_until_access":        "Access expires (YYYY-MM-DD)",
    "forever_access_purchased": "Lifetime access (1 / 0)",
    "FreeViewsRemaining":       "Free PDF views remaining",
    "total_vendor_views":       "Vendor views counted",
    "total_amount_paid":        "Total paid, dollars",
    "total_payments":           "Number of payments",
    "email":                    "Email address",
    "name":                     "Name",
    "Wedding_Guest_Count":      "Guest count",
    "Wedding_Budget":           "Budget",
    "Wedding_Location":         "Wedding location",
}

# Pricing Intelligence is gated on total_amount_paid >= 50 (ep230 v7) and reads NO date.
# A $30 buyer therefore has PDF access and no PI — a support question, not a bug.
PI_THRESHOLD = 50

_SEVERITY_HELP = (
    "Severity is what the reporter chose, 1–10. Self-reported, so it says how annoyed they "
    "are, not how broken the product is."
)


# ── Buckets ───────────────────────────────────────────────────────────────────
# Keyword classification, deliberately transparent and deliberately dumb. It picks which fix
# panel opens FIRST; every panel stays reachable regardless, so a wrong guess costs a click
# rather than hiding the tool you needed. It never triggers an action on its own.
_BUCKET_RULES = [
    ("test", [
        r"^\s*(test|testing|123|abc|asdf|\d+)\b.{0,12}$",
    ]),
    # Checked early and deliberately: a venue writing about its own listing is a
    # relationship and sometimes a legal matter, not a support ticket, and it must never sit
    # in the queue behind feature requests. See report #79 (Union Station Dallas, from a
    # @hyatt.com address) and #63 (Alexander Mansion).
    ("vendor_rep", [
        r"\bi represent\b", r"\bon behalf of\b",
        r"\bi(?:'m| am) the (director|owner|manager|gm|general manager|coordinator|"
        r"sales|event)", r"\bdirector of (events|sales|catering)",
        r"\b(my|our) (venue|property|hotel|business|listing)\b",
        r"person who submitted my pricing",
        r"how do i modify what is showing",
    ]),
    ("security", [
        r"\b(admin dashboard|admin panel|someone else'?s (account|data))\b",
        r"\bi (randomly )?have access to\b.*\badmin\b",
        r"\b(security|vulnerab|exposed|leak)\w*\b",
    ]),
    ("refund", [
        r"\brefund\b", r"charged? (me )?(2|two|twice|double)", r"\b2x\b",
        r"double charge", r"\bcancel (my )?(account|subscription)\b",
        r"\$\d+ worth of charges",
    ]),
    ("access", [
        r"\b(paid|bought|purchas\w+|subscrib\w+)\b.{0,80}\b(access|lock|pricing|filter|"
        r"reflect|not work|didn'?t work|still|no longer|gone|nothing)",
        r"still (locked|unable|can'?t)", r"not (being )?reflect", r"don'?t have access",
        r"no access\b", r"unlock my access", r"advanced filters? (still )?(are |is )?lock",
        r"asks? me to (pick|choose) a plan", r"trying to have me pay again",
        r"(can'?t|cannot|unable to|no way to) (purchase|buy|pay)",
        r"will not process", r"keep getting requests to submit a pdf",
        r"unable to access my free", r"free (week|access).{0,40}(haven'?t|not)",
        r"submitted a pdf.{0,60}(free|access).{0,60}(haven'?t|no email|no update)",
    ]),
    ("favorites", [
        r"saved? (venues?|vendors?).{0,40}(disappear|gone|missing|empty)",
        r"(disappear|gone|missing).{0,40}(saved?|liked?|favorit)",
        r"\bsaved?\b.{0,25}\bdisappear",
        r"liked? list", r"favorit\w* (are |all )?(gone|disappear)",
        r"no venues saved",
    ]),
    ("account", [
        r"(update|change) (the |my )?email", r"change my (name|password|details)",
        r"wrong email on my account",
    ]),
    ("vendor_data", [
        r"\b(inaccurate|incorrect|wrong)\b.{0,40}\b(info|information|data|price|pricing|"
        r"listing|link|website)",
        r"take\w* down", r"not for this business", r"wrong location",
        r"pricing link does not", r"this venue.{0,30}(closed|no longer)",
    ]),
    ("feature", [
        r"\b(sort|filter) (view )?option", r"would (be great|love|like) if",
        r"(please|able to) add\b", r"feature request", r"can you make it so",
        r"^filter international", r"\bwish\b.{0,30}\bcould\b",
        r"i'?d like to be able to", r"would (be )?so helpful", r"is there a way to",
        r"wondering if you wanted to consider",
    ]),
]

BUCKET_LABEL = {
    "access":      "🔓 Access / paid-but-locked",
    "refund":      "💸 Refund / billing",
    "favorites":   "💔 Saved venues lost",
    "vendor_rep":  "🚨 Venue rep — about their OWN listing",
    "vendor_data": "🏛️ Vendor data wrong",
    "security":    "🔒 Security report",
    "account":     "🧾 Account detail change",
    "feature":     "💡 Feature request",
    "bug":         "🐛 Bug report",
    "test":        "🧪 Internal / test",
    "other":       "❔ Other",
}

# Buckets that should never wait behind a feature request.
PRIORITY_BUCKETS = ("vendor_rep", "security", "refund", "access")

# Mailbox providers that tell you nothing. Anything else hints the reporter wrote in from a
# work address, which for this product usually means a venue — worth surfacing, never worth
# acting on alone.
_FREEMAIL = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com",
    "me.com", "live.com", "msn.com", "comcast.net", "proton.me", "protonmail.com",
    "verizon.net", "sbcglobal.net", "att.net", "mac.com", "ymail.com", "googlemail.com",
}


def is_business_email(email: str) -> bool:
    dom = (email or "").split("@")[-1].strip().lower()
    return bool(dom) and dom not in _FREEMAIL


# Internal addresses. Matched against the EMAIL only — see the note in classify().
_INTERNAL_EMAIL = re.compile(
    r"@infiwebsolutions\.com|desi\.gaddis@|katebeckman|vivek\.marthi", re.I
)

# Anything left that reads like a malfunction is a bug; the rest stays unclassified rather
# than being forced into a bucket that would open the wrong panel first.
_BUG_HINT = re.compile(
    r"nothing happens|doesn'?t work|does not work|not working|hasn'?t worked|getting errors?"
    r"|\berrors?\b|broken|\bbug\b|won'?t let me|signed out|glitch\w*|not loading|won'?t load"
    r"|auto takes you",
    re.I,
)


def classify(details: str, email: str = "") -> str:
    """Pick which fix panel opens first. Never triggers an action on its own.

    The email is matched SEPARATELY from the body, not concatenated onto it. Concatenating
    was a real bug: several rules are `$`-anchored to catch one-word rows like "test", and
    appending the address to the text pushed the end of the string past the anchor, so every
    internal test row classified as "other" and stayed in the queue.
    """
    if _INTERNAL_EMAIL.search(email or ""):
        return "test"

    body = (details or "").strip().lower()
    for bucket, patterns in _BUCKET_RULES:
        for p in patterns:
            if re.search(p, body, re.I):
                return bucket
    if _BUG_HINT.search(body):
        return "bug"
    return "other"


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

    Xano answers a rejected precondition with a 4xx and a JSON body carrying the message the
    endpoint wrote — those messages explain the rails, so they are surfaced verbatim rather
    than collapsed into "failed".
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

    reports["bucket"] = [
        classify(d, e)
        for d, e in zip(reports["details"].fillna(""), reports["user_email"].fillna(""))
    ]
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


@st.cache_data(ttl=30, show_spinner=False)
def _load_user(xano_base: str, email: str):
    """The reporter's account. 30s cache — short, because entitlement is what gets edited."""
    email = (email or "").strip().lower()
    if not email:
        return None
    rows = _xget(
        f"{xano_base}/admin/user_lookup?secret={EXPORT_SECRET}"
        f"&email={requests.utils.quote(email)}"
    )
    return (rows or [None])[0]


@st.cache_data(ttl=30, show_spinner=False)
def _load_audit(xano_base: str, feedback_id: int = 0, limit: int = 50):
    """The append-only trail of what has actually been done. feedback_id=0 = everything."""
    return _xget(
        f"{xano_base}/admin/audit_log?secret={EXPORT_SECRET}"
        f"&feedback_id={int(feedback_id)}&limit={int(limit)}"
    ) or []


_ACTION_LABEL = {
    "vendor_field_update": "edited vendor",
    "vendor_hide":         "🚫 hid vendor from search",
    "vendor_show":         "👁️ restored vendor to search",
    "user_field_update":   "edited account",
    "user_grant_access":   "🔓 changed access",
    "feedback_update":     "changed report status",
    "feedback_email":      "✉️ replied to reporter",
}


def _render_audit_rows(rows, show_report=False):
    """One line per recorded change. Old → new, always, because that is what makes it
    reversible; a log that only says 'something was edited' is decoration."""
    if not rows:
        st.caption("Nothing has been done to this report yet.")
        return
    for r in rows:
        who = r.get("actor") or "unknown"
        what = _ACTION_LABEL.get(r.get("action"), r.get("action") or "changed")
        tgt = f"{r.get('target_table')}/{r.get('target_id')}"
        col = r.get("column_name") or ""
        old, new = r.get("old_value") or "", r.get("new_value") or ""
        head = f"**{_ts(r.get('created_at'))}** · {what} · `{tgt}`"
        if show_report and r.get("feedback_id"):
            head += f" · report #{r['feedback_id']}"
        st.markdown(head)
        if col:
            same = " *(no change)*" if old == new else ""
            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;`{col}`: `{old or '∅'}` → `{new or '∅'}`{same}")
        st.caption(f"    by {who}" + (f" — {r['note']}" if r.get("note") else ""))


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

    Reads the recompute_vendor_filter_aggregates denorms on table 11 — one indexed read
    instead of a table-36 scan, and the same numbers ep119 itself filters on.
    """
    rows = int(v.get("flt_space_count") or 0)
    fee = int(v.get("flt_min_venue_fee") or 0)
    ppfb = int(v.get("flt_min_ppfb") or 0)
    fbmin = int(v.get("flt_min_fbmin") or 0)

    if rows == 0:
        return ("Nothing to show", "ok",
                "No pricing rows at all. Hiding this costs the user nothing.")
    if not (fee or ppfb or fbmin):
        return ("Nothing to show", "ok",
                f"{rows} pricing row(s), but every price is zero. Hiding costs the user nothing.")
    parts = []
    if fee:
        parts.append(f"venue fee from ${fee:,}")
    if ppfb:
        parts.append(f"per-person F&B from ${ppfb:,}")
    if fbmin:
        parts.append(f"F&B minimum from ${fbmin:,}")
    return ("Real pricing exists", "warn",
            f"{rows} pricing row(s) with " + ", ".join(parts) + ". If a user says they see "
            "nothing here, the data is fine and the LINK is broken — hiding it deletes correct "
            "pricing instead of fixing the bug.")


def _today() -> _dt.date:
    return _dt.date.today()


def _parse_date(s):
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _entitlement(u: dict):
    """(lines, problems) — what this account can actually do, and what looks wrong.

    Three questions, deliberately separate, because a user can pass one and fail another —
    which is precisely why "I paid but I'm locked out" keeps arriving:
      PDF / general access : forever_access_purchased, OR date_until_access >= today
      Pricing Intelligence : forever_access_purchased, OR total_amount_paid >= 50 (no date)
      Free PDF meter       : FreeViewsRemaining
    """
    forever = bool(u.get("forever_access_purchased"))
    until = _parse_date(u.get("date_until_access"))
    paid = int(u.get("total_amount_paid") or 0)
    pays = int(u.get("total_payments") or 0)
    views = int(u.get("FreeViewsRemaining") or 0)

    has_access = forever or (until is not None and until >= _today())
    has_pi = forever or paid >= PI_THRESHOLD

    lines = [
        f"**Access:** {'✅ lifetime' if forever else ('✅ until ' + until.isoformat() if has_access else ('🚫 expired ' + until.isoformat() if until else '🚫 never had it'))}",
        f"**Pricing Intelligence:** {'✅ yes' if has_pi else f'🚫 no — needs lifetime or ${PI_THRESHOLD}+ paid'}",
        f"**Paid:** ${paid} across {pays} payment(s) · **free PDF views left:** {views}",
    ]

    problems = []
    if pays > 0 and not has_access:
        problems.append(
            f"They paid {pays} time(s) (${paid}) and have no access right now. This is the "
            "complaint. Note that `date_until_access` doubles as the free-PDF meter's kill "
            "switch, so an expired date does not by itself mean the payment failed."
        )
    if pays > 1 and paid and not forever:
        problems.append(
            f"{pays} separate payments totalling ${paid} — check for a double charge before "
            "replying about a refund."
        )
    if has_access and not has_pi and paid:
        problems.append(
            f"They have access but NOT Pricing Intelligence (paid ${paid}, threshold "
            f"${PI_THRESHOLD}). If they are complaining that pricing is still hidden, "
            "extending the date will not fix it — that is the bucket to address."
        )
    return lines, problems


# ── Gmail ─────────────────────────────────────────────────────────────────────
def _gmail_token():
    """Return (access_token, how) or (None, reason).

    Two paths, in order of how little setup they need:
      1. GMAIL_SEND_REFRESH_TOKEN — a refresh token for the FROM_EMAIL mailbox with the
         gmail.send scope, exchanged against the dashboard's existing OAuth client.
      2. GOOGLE_SERVICE_ACCOUNT_JSON + GMAIL_IMPERSONATE — domain-wide delegation, which
         needs a Workspace admin to grant the service account that scope org-wide.
    """
    rt = os.environ.get("GMAIL_SEND_REFRESH_TOKEN", "").strip()
    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    cs = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if rt and cid and cs:
        try:
            r = requests.post(
                "https://oauth2.googleapis.com/token",
                data={"client_id": cid, "client_secret": cs,
                      "refresh_token": rt, "grant_type": "refresh_token"},
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
    # so branch on status rather than assuming no exception means delivery.
    if r.status_code == 200 and (r.json() or {}).get("id"):
        return True, f"sent via {how} (message {r.json()['id']})"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


# ── The one confirm gate ──────────────────────────────────────────────────────
# Quick-fix buttons and the free-text box both STAGE a proposal here. Nothing reaches the
# database until the diff has been rendered and approved, and the old value in that diff is
# sent back as `expected_old` so the server can refuse a write against a row that has moved.
def _stage(rid, target, target_id, column, new_value, old_value, why, label=None):
    st.session_state[f"fbt_stage_{rid}"] = {
        "target": target,          # "user" | "vendor"
        "target_id": target_id,
        "column": column,
        "new_value": str(new_value),
        "old_value": "" if old_value is None else str(old_value),
        "why": why,
        "label": label or column,
    }


def _apply_staged(xano_base, rid, actor):
    s = st.session_state.get(f"fbt_stage_{rid}")
    if not s:
        return False, "nothing staged"
    if s["target"] == "user":
        url = f"{xano_base}/admin/user_field_update"
        payload = {"user_id": int(s["target_id"])}
    else:
        url = f"{xano_base}/admin/vendor_field_update"
        payload = {"vendor_id": str(s["target_id"])}
    payload.update({
        "secret": EXPORT_SECRET,
        "column": s["column"],
        "new_value": s["new_value"],
        "expected_old": s["old_value"],
        "actor": actor,
        "feedback_id": int(rid or 0),
        "note": s.get("why") or "",
    })
    return _xpost(url, payload)


def _render_confirm(xano_base, rid, actor):
    """The gate. Renders the staged diff and the only two buttons that end it."""
    s = st.session_state.get(f"fbt_stage_{rid}")
    if not s:
        return

    st.markdown("---")
    st.markdown("#### ⚠️ Confirm this change")
    where = (f"user `{s['target_id']}`" if s["target"] == "user"
             else f"vendor `{s['target_id']}`")
    st.markdown(f"**{s['label']}** (`{s['column']}`) on {where}")

    c1, c2 = st.columns(2)
    c1.text_area("Current", value=s["old_value"], disabled=True, height=90,
                 key=f"fbt_cold_{rid}")
    c2.text_area("After this change", value=s["new_value"], disabled=True, height=90,
                 key=f"fbt_cnew_{rid}")
    if s.get("why"):
        st.caption(s["why"])

    if s["old_value"] == s["new_value"]:
        st.info("That is already the current value — nothing would change.")

    a1, a2 = st.columns([1, 1])
    if a1.button("✅ Apply", type="primary", key=f"fbt_capply_{rid}"):
        ok, res = _apply_staged(xano_base, rid, actor)
        if ok:
            st.session_state.pop(f"fbt_stage_{rid}", None)
            st.success(
                f"Applied — {res.get('column')} on {res.get('email') or res.get('name') or res.get('vendor_id')}: "
                f"{res.get('old_value')!r} → {res.get('new_value')!r} (audit #{res.get('audit_id')})"
            )
            _refresh()
        else:
            st.error(res)
    if a2.button("Discard", key=f"fbt_cdiscard_{rid}"):
        st.session_state.pop(f"fbt_stage_{rid}", None)
        st.rerun()


# ── The natural-language rail ─────────────────────────────────────────────────
_PROPOSE_SYSTEM = """You turn a wedding-platform ops instruction into ONE proposed database edit.

You are given the current row and an instruction from an admin. Return the single column that
should change and its new value.

Rules:
- You may only propose a column from the allowed list you are given. Never invent one.
- Propose exactly one column. If the instruction needs several, pick the single most important
  one and say so in `reason`.
- `new_value` is always a string. Dates are "YYYY-MM-DD". Booleans are "1" or "0". Numbers are
  digits only.
- If the instruction is unclear, or asks for something no allowed column can express, set
  `column` to "" and explain why in `reason`. Refusing is a correct answer — a human reads this
  before anything is written.
- Do not restate the old value as the new value.

Reply with ONLY a JSON object, no prose and no code fence:
{"column": "...", "new_value": "...", "reason": "one sentence"}"""


def _propose_edit(instruction: str, row: dict, allowed: dict, today_note: str = ""):
    """Ask Claude for a proposed edit. Returns (proposal_dict, error_str).

    This never writes. It produces a suggestion a human reads and approves; the approval is
    what stages it, and the write is separately guarded server-side by the column allowlist
    and an optimistic lock on the value shown in the diff.
    """
    try:
        from anthropic import Anthropic
    except Exception as e:
        return None, f"anthropic SDK unavailable: {e}"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, "ANTHROPIC_API_KEY is not set on this deployment."

    facts = {k: row.get(k) for k in allowed if k in row}
    for k in ("Vendor_ID", "Name", "id", "email", "name"):
        if k in row:
            facts[k] = row.get(k)

    user = (
        f"Allowed columns: {json.dumps(list(allowed.keys()))}\n\n"
        f"Current row (only relevant columns shown):\n{json.dumps(facts, indent=2, default=str)}\n\n"
        + (f"Today is {today_note}.\n\n" if today_note else "")
        + f"Admin instruction:\n{instruction.strip()}"
    )

    try:
        client = Anthropic()
        resp = client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            system=_PROPOSE_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
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
    if col not in allowed:
        return None, f"Proposed a column that is not writable from here ({col}). Nothing changed."
    prop["column"] = col
    prop["new_value"] = str(prop.get("new_value", ""))
    return prop, None


# ── Writes on the feedback row itself ─────────────────────────────────────────
def _save_feedback(xano_base, feedback_id, actor, **fields):
    payload = {"secret": EXPORT_SECRET, "feedback_id": int(feedback_id), "actor": actor}
    payload.update({k: v for k, v in fields.items() if v is not None})
    return _xpost(f"{xano_base}/admin/feedback_update", payload)


def _refresh():
    load_queue.clear()
    _hydrate_vendors.clear()
    _load_user.clear()
    _load_audit.clear()
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
            f"reply, copy it, send it yourself, and mark it as replied so the queue stays "
            f"honest.\n\nTo enable: set **GMAIL_SEND_REFRESH_TOKEN** (run "
            f"`mint_gmail_token.py` once, signed in as {FROM_EMAIL}), or "
            f"**GMAIL_IMPERSONATE={FROM_EMAIL}** with domain-wide delegation."
        )

    c1, c2 = st.columns(2)
    c1.text_input("From", value=FROM_EMAIL, disabled=True, key=f"fbt_from_{rid}")
    to_val = c2.text_input("To", value=to_addr, key=f"fbt_to_{rid}")
    subject = st.text_input("Subject", value="Re: your Tulle Together feedback",
                            key=f"fbt_subj_{rid}")

    quoted = "\n".join("> " + ln for ln in str(row.get("details") or "").splitlines())
    default_body = (
        f"Hi,\n\nThanks for writing in about Tulle Together — and sorry for the trouble.\n\n"
        f"You told us:\n{quoted}\n\n"
        f"\n\nIf anything else comes up, just reply to this email and it comes straight to us.\n\n"
        f"— The Tulle Together team\n"
    )
    body = st.text_area("Body", value=default_body, height=280, key=f"fbt_body_{rid}")

    st.divider()
    b1, b2, b3 = st.columns(3)

    if b1.button("Send", type="primary", key=f"fbt_send_{rid}", disabled=not token):
        if not to_val.strip():
            st.error("No recipient.")
        else:
            ok, detail = _gmail_send(to_val.strip(), subject, body)
            if ok:
                # Stamp last_email_at only now — after a real message id came back. A
                # timestamp written on intent would answer "did we reply?" wrongly.
                _save_feedback(xano_base, rid, actor, email_sent="1", email_subject=subject)
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


# ── Fix panels ────────────────────────────────────────────────────────────────
def _panel_account(xano_base, rid, row, user):
    """Entitlement, plus the one-click grants. Every button stages; none writes."""
    if not user:
        st.info(
            f"No account found for `{row.get('user_email')}`. They may have written in from a "
            "different address than they signed up with, or never signed up at all."
        )
        return

    lines, problems = _entitlement(user)
    st.markdown(f"`user {user['id']}` **{user.get('name') or '—'}** · {user.get('email')}")
    for ln in lines:
        st.markdown("- " + ln)
    for p in problems:
        st.warning(p)

    until = _parse_date(user.get("date_until_access"))
    base = max(until, _today()) if until else _today()
    paid = int(user.get("total_amount_paid") or 0)

    st.markdown("**Grant access** — extends from whichever is later, today or their current expiry.")
    g1, g2, g3, g4 = st.columns(4)
    for col, weeks, label in ((g1, 1, "+1 week"), (g2, 4, "+4 weeks"), (g3, 12, "+12 weeks")):
        if col.button(label, key=f"fbt_grant{weeks}_{rid}"):
            new = (base + _dt.timedelta(weeks=weeks)).isoformat()
            _stage(rid, "user", user["id"], "date_until_access", new,
                   user.get("date_until_access") or "",
                   f"Granting {label.strip('+')} from {base.isoformat()} in response to "
                   f"report #{rid}.",
                   label="Access expires")
            st.rerun()
    if g4.button("Lifetime", key=f"fbt_grantforever_{rid}"):
        _stage(rid, "user", user["id"], "forever_access_purchased", "1",
               "1" if user.get("forever_access_purchased") else "0",
               f"Granting lifetime access in response to report #{rid}. Note this also "
               f"unlocks Pricing Intelligence, which the date-based grants do not.",
               label="Lifetime access")
        st.rerun()

    f1, f2 = st.columns(2)
    if paid and paid < PI_THRESHOLD:
        if f1.button(f"Unlock Pricing Intelligence (set paid to ${PI_THRESHOLD})",
                     key=f"fbt_pi_{rid}",
                     help=f"PI is gated on total_amount_paid >= {PI_THRESHOLD} and reads no "
                          f"date. Extending access will NOT unlock it."):
            _stage(rid, "user", user["id"], "total_amount_paid", str(PI_THRESHOLD), str(paid),
                   f"Raising recorded spend to the ${PI_THRESHOLD} Pricing Intelligence "
                   f"threshold in response to report #{rid}. This changes a revenue figure — "
                   f"only do it when the payment is real and under-recorded.",
                   label="Total paid, dollars")
            st.rerun()
    views = int(user.get("FreeViewsRemaining") or 0)
    if f2.button("Reset free PDF views to 3", key=f"fbt_views_{rid}"):
        _stage(rid, "user", user["id"], "FreeViewsRemaining", "3", str(views),
               f"Restoring the free PDF allowance in response to report #{rid}.",
               label="Free PDF views remaining")
        st.rerun()


def _panel_favorites(user):
    n = len(user.get("favorited_vendors") or []) if user else 0
    st.warning(
        f"This account currently has **{n}** saved vendor(s). Their previous list is not "
        "recoverable from here — the wipe overwrites the array rather than versioning it, so "
        "there is no prior value to restore. Treat this as an apology-and-explain reply, not "
        "a fix, and check whether the underlying ep109 favorites bug is still live."
    )


def _panel_billing(user):
    if not user:
        return
    pays = int(user.get("total_payments") or 0)
    paid = int(user.get("total_amount_paid") or 0)
    st.warning(
        f"Recorded: **{pays} payment(s), ${paid} total**. Refunds are not issued from this "
        "dashboard — the Stripe integration here is read-only, so issue it in Stripe and note "
        "it below. Two or more payments with no lifetime flag is the double-charge signature."
    )


def _panel_vendor(xano_base, rid, row, actor, vendors):
    vendor_id = str(row.get("vendor_id") or "")
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

        v1, v2, v3 = st.columns(3)
        if visible:
            if v1.button("🚫 Hide from search", key=f"fbt_hide_{rid}"):
                _stage(rid, "vendor", vendor["Vendor_ID"], "Validated_Data", "0",
                       vendor.get("Validated_Data"),
                       f"Hiding {vendor.get('Name')} from Vendor Discovery in response to "
                       f"report #{rid}. {why}",
                       label="Visible in search")
                st.rerun()
        else:
            if v1.button("👁️ Show in search", type="primary", key=f"fbt_show_{rid}"):
                _stage(rid, "vendor", vendor["Vendor_ID"], "Validated_Data", "1",
                       vendor.get("Validated_Data"),
                       f"Restoring {vendor.get('Name')} to Vendor Discovery in response to "
                       f"report #{rid}.",
                       label="Visible in search")
                st.rerun()

        if v2.button("Unlink vendor", key=f"fbt_unlink_{rid}"):
            ok, res = _save_feedback(xano_base, rid, actor, vendor_id="-")
            if ok:
                _refresh()
            else:
                st.error(res)
        if vendor.get("Website"):
            v3.link_button("Open website", vendor["Website"])
        return vendor

    q = st.text_input(
        "Search by venue name or Vendor_ID", key=f"fbt_vq_{rid}",
        placeholder="e.g. Union Station, or V3488",
        help="Feedback rows carry no vendor reference — the reporter never told us which "
             "venue. Read the complaint and pick it.",
    )
    if q:
        hits = _vendor_search(xano_base, q)
        if not hits:
            st.caption("No matches.")
        for h in hits[:10]:
            hc1, hc2 = st.columns([5, 1])
            vis = "👁️" if str(h.get("Validated_Data")) == "1" else "🚫"
            hc1.write(
                f"{vis} `{h['Vendor_ID']}` **{h.get('Name')}** — {h.get('State') or '—'} · "
                f"{h.get('Category') or '—'} · {int(h.get('flt_space_count') or 0)} pricing row(s)"
            )
            if hc2.button("Link", key=f"fbt_link_{rid}_{h['Vendor_ID']}"):
                ok, res = _save_feedback(xano_base, rid, actor, vendor_id=h["Vendor_ID"])
                if ok:
                    _refresh()
                else:
                    st.error(res)
    return None


def _panel_freetext(rid, user, vendor):
    """Anything the buttons don't cover. Same confirm gate, no exceptions."""
    targets = []
    if user:
        targets.append(("This reporter's account", "user"))
    if vendor:
        targets.append(("The linked vendor", "vendor"))
    if not targets:
        st.caption("Link a vendor, or find the reporter's account, to enable free-text edits.")
        return

    tgt_label = st.radio("Change what?", [t[0] for t in targets], horizontal=True,
                         key=f"fbt_tgt_{rid}")
    tgt = dict((a, b) for a, b in targets)[tgt_label]
    row = user if tgt == "user" else vendor
    allowed = USER_COLUMNS if tgt == "user" else VENDOR_COLUMNS
    tid = row["id"] if tgt == "user" else row["Vendor_ID"]

    instruction = st.text_input(
        "Describe the change", key=f"fbt_instr_{rid}",
        placeholder="e.g. give them access through the end of September — their payment "
                    "went through but the webhook missed it",
    )
    if st.button("Preview change", key=f"fbt_preview_{rid}",
                 disabled=not instruction.strip()):
        with st.spinner("Working out what that means…"):
            prop, err = _propose_edit(instruction, row, allowed, _today().isoformat())
        if err:
            st.warning(err)
        else:
            _stage(rid, tgt, tid, prop["column"], prop["new_value"],
                   row.get(prop["column"]),
                   f"{instruction.strip()} — {prop.get('reason') or ''}".strip(" —"),
                   label=allowed.get(prop["column"], prop["column"]))
            st.rerun()


# ── One report ────────────────────────────────────────────────────────────────
def _render_report(xano_base, row, actor, vendors, audit_by_report=None):
    rid = int(row["id"])
    closed = bool(row.get("completed"))
    emailed = int(row.get("last_email_at") or 0) > 0
    sev = int(row.get("severity") or 0)
    bucket = row.get("bucket") or "other"

    # History comes from ONE bulk audit load done by the tab, not a call per report — a
    # Streamlit expander executes its body whether or not it is open, so a per-report fetch
    # would fire 25 requests on every rerun.
    history = (audit_by_report or {}).get(rid, [])
    # Only real data changes count as a fix; open/closed flips are bookkeeping.
    fixes = [h for h in history
             if h.get("action") not in ("feedback_update", "feedback_email")
             and (h.get("old_value") or "") != (h.get("new_value") or "")]

    head = (
        f"{'✅ Closed' if closed else '🔴 Open'} · #{rid} · "
        f"{BUCKET_LABEL.get(bucket, bucket)} · {row.get('user_email') or 'no email'} · "
        f"{row.get('page') or 'unknown page'} · {_age_days(row.get('created_at'))}d old"
    )
    if sev:
        head += f" · severity {sev}"
    if fixes:
        head += f" · 🔧 {len(fixes)} fix{'es' if len(fixes) > 1 else ''} applied"
    if not emailed and not closed:
        head += " · ✉️ never answered"

    with st.container(border=True):
        st.markdown(f"**{head}**")
        st.write(row.get("details") or "_(no details)_")

        meta = [f"submitted {_ts(row.get('created_at'))}",
                f"category: {row.get('category') or '—'}",
                f"{row.get('operating_system') or '—'} · {row.get('device_width_size') or '—'}px"]
        if emailed:
            meta.append(f"last reply {_ts(row.get('last_email_at'))}")
        if row.get("handled_by"):
            meta.append(f"last touched by {row['handled_by']}")
        st.caption(" · ".join(meta))

        # Two buckets are not support tickets and must not read like one.
        if bucket == "vendor_rep":
            st.error(
                "**This is the venue writing about its own listing.** That is a relationship "
                "and sometimes a legal matter, not a queue item — the data they are objecting "
                "to is data we published about them. Read it before touching anything, and "
                "reply as a person rather than closing it."
            )
        elif bucket == "security":
            st.error(
                "**Someone is reporting a security problem.** Verify it before it is closed, "
                "and treat the reporter as having done you a favour."
            )
        elif is_business_email(row.get("user_email")) and bucket in ("vendor_data", "other"):
            st.info(
                f"Written from a business address (`{str(row.get('user_email')).split('@')[-1]}`). "
                "Often a venue rather than a couple — worth reading in that light before "
                "replying, though the address alone proves nothing."
            )

        with st.expander("Fix this report", expanded=bool(st.session_state.get(f"fbt_stage_{rid}"))):
            # The staged diff comes FIRST — if something is waiting for approval it should not
            # be below three panels of controls.
            _render_confirm(xano_base, rid, actor)

            user = None
            try:
                user = _load_user(xano_base, row.get("user_email"))
            except Exception as e:
                st.caption(f"Could not load the reporter's account: {e}")

            # Panels are ordered by the bucket, but all of them stay reachable — the classifier
            # picks what opens first, never what is possible.
            order = {
                "access":      ["account", "vendor", "free"],
                "refund":      ["billing", "account", "free"],
                "favorites":   ["favorites", "account", "free"],
                "vendor_data": ["vendor", "account", "free"],
                "vendor_rep":  ["vendor", "account", "free"],
            }.get(bucket, ["account", "vendor", "free"])

            names = {
                "account":   "🔓 Reporter's account",
                "billing":   "💸 Billing",
                "favorites": "💔 Saved venues",
                "vendor":    "🏛️ Vendor",
                "free":      "✍️ Anything else",
            }
            tabs = st.tabs([names[o] for o in order])
            vendor = None
            for t, name in zip(tabs, order):
                with t:
                    if name == "account":
                        _panel_account(xano_base, rid, row, user)
                    elif name == "billing":
                        _panel_billing(user)
                    elif name == "favorites":
                        _panel_favorites(user)
                    elif name == "vendor":
                        vendor = _panel_vendor(xano_base, rid, row, actor, vendors)
                    elif name == "free":
                        vid = str(row.get("vendor_id") or "")
                        _panel_freetext(rid, user, vendors.get(vid) if vid else None)

            st.divider()

            # ── notes + status + reply ────────────────────────────────────────
            notes = st.text_area(
                "Internal notes", value=str(row.get("admin_notes") or ""),
                key=f"fbt_notes_{rid}", height=70,
                help="Never shown to the reporter. For the part of the resolution that is not "
                     "a database change — 'refunded in Stripe', 'escalated to Des', 'waiting "
                     "on the venue'. Data changes record themselves in the audit log.",
            )
            n1, n2, n3 = st.columns(3)
            if n1.button("Save notes", key=f"fbt_savenotes_{rid}"):
                ok, res = _save_feedback(xano_base, rid, actor, admin_notes=(notes or "-"))
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

            # ── what has actually been done ───────────────────────────────────
            # The trail is written automatically by every Apply. Showing it back is the
            # difference between "we keep an audit log" and being able to answer "was this
            # actually fixed, and how?" without opening Xano.
            st.markdown("**What's been done to this report**")
            _render_audit_rows(history)
            if row.get("admin_notes"):
                st.info(f"📝 Note: {row['admin_notes']}")
            if emailed:
                st.caption(
                    f"✉️ Last reply {_ts(row.get('last_email_at'))}"
                    + (f" — “{row.get('last_email_subject')}”" if row.get("last_email_subject") else "")
                )


# ── Tab ───────────────────────────────────────────────────────────────────────
def render_feedback_triage_tab(xano_base: str, user_email: str = ""):
    actor = user_email or "unknown@tulletogether.com"

    st.subheader("🗣️ Feedback triage")

    with st.expander("ℹ️ What this queue is for", expanded=False):
        st.markdown(
            "Every in-app feedback report, with an owner, a state, and the tools to actually "
            "fix it.\n\n"
            "**Each report is bucketed** by what it asks for — the biggest bucket by far is "
            "*paid but still locked out*, which is fixed on the user's row, not a vendor's. "
            "The bucket decides which panel opens first; every panel stays reachable, so a "
            "wrong guess costs a click.\n\n"
            "**Every write goes through one confirm gate.** Quick-fix buttons and the "
            "free-text box both stage a proposal; nothing reaches the database until the diff "
            "is on screen and you press Apply. The value in that diff is sent back to the "
            "server, which refuses the write if the row has changed since — so what you "
            "approved is what lands, or nothing does.\n\n"
            "**Three things the buttons deliberately will not do:** issue a Stripe refund "
            "(read-only integration — do it in Stripe and note it), restore lost favourites "
            "(the wipe overwrites rather than versions, so there is no prior value), or touch "
            "a password, role or session token. Support tooling that can take over an account "
            "is not support tooling.\n\n"
            "**How a fix gets recorded** — three separate things, and you only press one of "
            "them by hand:\n\n"
            "| What | Where it lands | How |\n"
            "|---|---|---|\n"
            "| the data change itself | `admin_audit` — who, when, old → new, why | "
            "**automatic** on Apply |\n"
            "| something that is *not* a database change (\"refunded in Stripe\", \"escalated "
            "to Des\") | the report's notes | type it, **Save notes** |\n"
            "| you replied to the person | `last_email_at` | **Send**, or **Mark as replied** |\n"
            "| the report is done | the report's status | **Close** |\n\n"
            "So the normal flow is: Apply the fix → add a note only if you did something "
            "outside the database → reply → Close. Each report shows its own history under "
            "**What's been done to this report**, and the header counts the fixes applied to "
            "it, so \"was this actually fixed, and how?\" is answerable without opening Xano.\n\n"
            f"Replies send from `{FROM_EMAIL}`."
        )

    try:
        df = load_queue(xano_base)
    except Exception as e:
        st.error(f"Could not load the feedback queue: {e}")
        return

    if df.empty:
        st.info("No feedback reports.")
        return

    real = df[df["bucket"] != "test"]
    total = len(real)
    open_n = int((~real["completed"].astype(bool)).sum())
    never_answered = int(((real["last_email_at"].fillna(0).astype("int64") <= 0)
                          & (~real["completed"].astype(bool))).sum())
    access_n = int((real["bucket"] == "access").sum())

    escalate_n = int(real["bucket"].isin(("vendor_rep", "security")).sum())

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Reports", total, help="Excludes internal test rows.")
    m2.metric("Open", open_n)
    m3.metric("Never answered", never_answered, help="Open, and nobody has ever replied.")
    m4.metric("Paid-but-locked", access_n, help="The biggest actionable bucket.")
    m5.metric("Needs a person", escalate_n,
              help="Venues writing about their own listing, plus security reports. These are "
                   "not support tickets and should never sit behind a feature request.")

    with st.container(border=True):
        f1, f2, f3 = st.columns(3)
        status = f1.radio("Status", ["Open", "Closed", "All"], horizontal=True,
                          key="fbt_status")
        buckets_present = [b for b in BUCKET_LABEL if b in set(df["bucket"])]
        pick_buckets = f2.multiselect(
            "Bucket", buckets_present, format_func=lambda b: BUCKET_LABEL[b],
            key="fbt_buckets",
        )
        pages = sorted({p for p in df["page"].fillna("").astype(str) if p})
        pick_pages = f3.multiselect("Page", pages, key="fbt_pages")

        g1, g2, g3 = st.columns([2, 1, 1])
        search = g1.text_input("Search the reports", key="fbt_search",
                               placeholder="words in the report, or a reporter's email")
        min_sev = g2.slider("Min severity", 0, 10, 0, key="fbt_sev", help=_SEVERITY_HELP)
        hide_test = g3.checkbox("Hide test rows", value=True, key="fbt_hidetest")
        only_unanswered = g3.checkbox("Never answered only", key="fbt_unans")

    view = df.copy()
    if hide_test:
        view = view[view["bucket"] != "test"]
    if status == "Open":
        view = view[~view["completed"].astype(bool)]
    elif status == "Closed":
        view = view[view["completed"].astype(bool)]
    if pick_buckets:
        view = view[view["bucket"].isin(pick_buckets)]
    if pick_pages:
        view = view[view["page"].isin(pick_pages)]
    if min_sev:
        view = view[view["severity"].fillna(0).astype(int) >= min_sev]
    if only_unanswered:
        view = view[view["last_email_at"].fillna(0).astype("int64") <= 0]
    if search.strip():
        s = search.strip().lower()
        hay = (view["details"].fillna("").astype(str).str.lower() + " "
               + view["user_email"].fillna("").astype(str).str.lower())
        view = view[hay.str.contains(s, regex=False)]

    st.caption(f"Showing {len(view)} of {len(df)} reports.")
    if view.empty:
        return

    # One bulk call for every vendor the visible rows link to, rather than one per row.
    ids_key = ",".join(sorted({v for v in view["vendor_id"].astype(str) if v}))
    try:
        vendors = _hydrate_vendors(xano_base, ids_key)
    except Exception as e:
        st.warning(f"Could not load linked vendors: {e}")
        vendors = {}

    # One bulk audit load for the whole tab. Feeds both the per-report history and the
    # "N fixes applied" badge, so neither costs a request per row.
    audit_by_report, audit_recent = {}, []
    try:
        audit_recent = _load_audit(xano_base, 0, 500)
        for a in audit_recent:
            audit_by_report.setdefault(int(a.get("feedback_id") or 0), []).append(a)
    except Exception as e:
        st.caption(f"Could not load the change history: {e}")

    with st.expander(f"🧾 Recent activity — the last {len(audit_recent)} change(s) "
                     f"made from this dashboard", expanded=False):
        st.caption(
            "Written automatically on every Apply, with the value it replaced. Append-only: "
            "there is no endpoint that edits or deletes it, because an audit log you can edit "
            "is not an audit log."
        )
        _render_audit_rows(audit_recent[:60], show_report=True)

    show_n = st.selectbox("Show", [25, 50, 100, 500], index=0, key="fbt_pagesize")
    for _, row in view.head(int(show_n)).iterrows():
        _render_report(xano_base, row, actor, vendors, audit_by_report)
