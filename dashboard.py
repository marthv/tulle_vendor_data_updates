"""
Tulle Admin Dashboard
---------------------
Streamlit web app for the Tulle Together team.
Hosted on Railway — accessible to the whole team via a URL + Google login.

Required env vars (set in Railway dashboard):
    GOOGLE_CLIENT_ID      — OAuth 2.0 Web client ID from Google Cloud Console
    GOOGLE_CLIENT_SECRET  — OAuth 2.0 Web client secret
    APP_URL               — Full public URL of this app (e.g. https://tulle-pipeline.up.railway.app)
    ALLOWED_EMAILS        — Comma-separated list of allowed Google email addresses
    ANTHROPIC_API_KEY
    GOOGLE_SERVICE_ACCOUNT_JSON
    XANO_SUMMARY_ENDPOINT
    XANO_PRICING_ENDPOINT
    XANO_GET_ENDPOINT
    XANO_BASE_URL         — base for enrichment endpoints
    XANO_JOB_STATUS_ENDPOINT  — POST endpoint to track persistent job status (survives logouts)
    XANO_JOBS_ENDPOINT    — GET endpoint to fetch active jobs

Vendor Scraper tab (optional — see scrape_core.py / .env.example for the full list):
    XANO_PHOTOGRAPHERS_ENDPOINT, XANO_OBSERVATION_ENDPOINT, XANO_PATCH_VENDOR_ENDPOINT
    REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT (Phase 2 — Reddit source)

Usage/quota trackers (optional — widgets degrade gracefully if unset):
    GCP_PROJECT_ID        — GCP project for Places API quota reads (default "tulle-technologies").
                            Requires the GOOGLE_SERVICE_ACCOUNT_JSON service account to hold the
                            "Monitoring Viewer" role on that project. Powers the
                            "Google Data & Images" tab quota panel.

Optional fallback (if GOOGLE_CLIENT_ID is not set, password auth is used):
    DASHBOARD_PASSWORD
"""

import os
import re
import base64
import datetime
import json
import requests
import pandas as pd
import streamlit as st
import google.auth.transport.requests
import google.oauth2.id_token
from google.oauth2 import service_account
from concurrent.futures import ThreadPoolExecutor, as_completed
from extract_core import (run_extraction, get_pipeline_status,
                          run_extraction_batch, process_batch_results)
from scrape_core import run_scrape, get_scrape_status


# ── JOB STATUS TRACKING (persistent across logouts) ──────────────────────────

def _post_job_status(job_type: str, status: str, user_email: str,
                     result_summary: dict = None, batch_id: str = None) -> bool:
    """
    Post job status to Xano for persistence across logouts.
    Returns True if successful, False otherwise.
    """
    job_endpoint = os.environ.get("XANO_JOB_STATUS_ENDPOINT", "")
    if not job_endpoint:
        return False
    try:
        payload = {
            "job_type": job_type,
            "status": status,
            "user_email": user_email,
            "result_summary": result_summary,
            "batch_id": batch_id,
        }
        r = requests.post(job_endpoint, json=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def _get_active_job(job_type: str):
    """
    Get the currently active job of a given type from Xano.
    Returns job dict or None if no active job.
    """
    jobs_endpoint = os.environ.get("XANO_JOBS_ENDPOINT", "")
    if not jobs_endpoint:
        return None
    try:
        r = requests.get(f"{jobs_endpoint}?job_type={job_type}&is_active=true", timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0]
        return None
    except Exception:
        return None


def _get_job_history(job_type: str, limit: int = 20):
    """
    Get completed jobs of a given type from Xano.
    Returns list of job dicts, newest first.
    """
    jobs_endpoint = os.environ.get("XANO_JOBS_ENDPOINT", "")
    if not jobs_endpoint:
        return []
    try:
        r = requests.get(f"{jobs_endpoint}?job_type={job_type}&is_active=false&limit={limit}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data
        return []
    except Exception:
        return []


def _parse_timestamp(ts_value) -> float | None:
    """Parse a timestamp (ISO string, epoch seconds, or epoch milliseconds) to float seconds."""
    if not ts_value or ts_value == 'unknown':
        return None
    try:
        if isinstance(ts_value, (int, float)):
            ts = float(ts_value)
        elif isinstance(ts_value, str):
            if ts_value.isdigit():
                ts = float(ts_value)
            else:
                from datetime import datetime
                ts = datetime.fromisoformat(ts_value.replace('Z', '+00:00')).timestamp()
        else:
            return None

        # If value > epoch 2025 in seconds, it's probably already in seconds
        # If value looks like milliseconds (> 1e12), convert to seconds
        if ts > 1000000000000:  # definitely milliseconds
            ts = ts / 1000
        elif ts > 1e11:  # ambiguous but likely milliseconds
            ts = ts / 1000
        return ts
    except Exception:
        return None


def _format_job_display(job: dict) -> str:
    """Format job info with elapsed time, progress, and stuck detection."""
    if not job:
        return ""

    started = job.get('started_at', 'unknown')
    updated = job.get('updated_at', 'unknown')
    status = job.get('status', 'unknown').upper()
    user = job.get('user_email', 'unknown')
    result_summary = job.get('result_summary') or {}

    # Parse result summary if it's a string
    if isinstance(result_summary, str):
        try:
            result_summary = json.loads(result_summary)
        except (json.JSONDecodeError, TypeError):
            result_summary = {}

    # Extract progress data
    current_pdf = result_summary.get('current_pdf', '')
    ok_count = result_summary.get('ok', 0)
    failed_count = result_summary.get('failed', 0)
    pending_count = result_summary.get('pending', 0)
    total_count = result_summary.get('total', 0)

    # Calculate elapsed time
    elapsed = ""
    stuck_warning = ""
    try:
        started_ts = _parse_timestamp(started)
        updated_ts = _parse_timestamp(updated)
        now_ts = datetime.now(datetime.timezone.utc).timestamp()

        if started_ts:
            elapsed_secs = int(now_ts - started_ts)
            hours = elapsed_secs // 3600
            mins = (elapsed_secs % 3600) // 60
            secs = elapsed_secs % 60
            if hours > 0:
                elapsed = f" | ⏱️ {hours}h {mins}m {secs}s elapsed"
            elif mins > 0:
                elapsed = f" | ⏱️ {mins}m {secs}s elapsed"
            else:
                elapsed = f" | ⏱️ {secs}s elapsed"

        # Detect stuck jobs (last update > 2 hours ago)
        if updated_ts and status == "RUNNING":
            time_since_update = int(now_ts - updated_ts)
            if time_since_update > 7200:  # 2 hours
                update_mins = time_since_update // 60
                stuck_warning = f"\n⚠️  **Warning**: No progress update in {update_mins}m — job may be stuck"
    except Exception:
        pass

    # Build progress line
    progress = ""
    if ok_count or failed_count or pending_count:
        progress = f"\n📊 Progress: ✅ {ok_count} extracted · ❌ {failed_count} failed · ⏳ {pending_count} pending"
        if total_count:
            pct = int((ok_count + failed_count) / total_count * 100) if total_count > 0 else 0
            progress += f" ({pct}%)"

    if current_pdf:
        progress += f"\n🔍 Processing: {current_pdf}"

    return f"🔄 **Active Job**  \nStarted: {started}  \nUser: {user}  \nStatus: {status}{elapsed}  \nLast updated: {updated}{progress}{stuck_warning}"


# ── PAGE CONFIG ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Tulle Admin Dashboard",
    page_icon="tulle.png",
    layout="wide",
)

st.markdown("""
<style>
    /* ── Global ── */
    .block-container { max-width: 92vw !important; padding: 0.75rem 2rem 1.5rem !important; }
    .stApp { background: #f8f9fa; }
    /* Remove Streamlit's fixed top chrome bar — it was floating over and
       clipping our own header. We render the Tulle logo/title ourselves. */
    [data-testid="stHeader"]     { display: none !important; }
    [data-testid="stDecoration"] { display: none !important; }
    [data-testid="stToolbar"]    { display: none !important; }

    /* ── Header ── */
    .tulle-logo { font-size: 22px; font-weight: 700; color: #1B7A4A; letter-spacing: -0.3px; }
    .tulle-user { font-size: 13px; color: #52555C; }
    .tulle-rule { border: none; border-top: 2px solid #1B7A4A; margin: 4px 0 16px; }

    /* ── Metric cards ── */
    .metric-card {
        border-radius: 10px; padding: 18px 14px;
        text-align: center; margin-bottom: 8px;
    }
    .metric-card .metric-icon { font-size: 20px; margin-bottom: 4px; }
    .metric-card .metric-value { font-size: 30px; font-weight: 700; margin: 4px 0; }
    .metric-card .metric-label { font-size: 12px; opacity: 0.75; }
    .card-green  { background: #d1fae5; color: #065f46; border: 1.5px solid #6ee7b7; }
    .card-amber  { background: #fef3c7; color: #92400e; border: 1.5px solid #fcd34d; }
    .card-purple { background: #ede9fe; color: #4c1d95; border: 1.5px solid #c4b5fd; }
    .card-red    { background: #fee2e2; color: #991b1b; border: 1.5px solid #fca5a5; }
    .card-gray   { background: #f3f4f6; color: #374151; border: 1.5px solid #d1d5db; }

    /* ── Log box ── */
    .log-box {
        background: #0f172a; color: #e2e8f0;
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
        font-size: 12.5px; padding: 16px; border-radius: 8px;
        max-height: 500px; overflow-y: auto;
        white-space: pre-wrap; word-break: break-word;
        border: 1px solid #1e293b;
    }

    /* ── Run result cards ── */
    .run-card {
        background: white; border-radius: 10px; padding: 14px 16px;
        margin-bottom: 10px; border-left: 4px solid #1B7A4A;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .run-card.failed  { border-left-color: #ef4444; }
    .run-card.partial { border-left-color: #f59e0b; }
    .run-card-title   { font-weight: 600; font-size: 14px; margin-bottom: 6px; }
    .run-card-meta    { font-size: 12px; color: #6b7280; }
    .run-card-badge   {
        display: inline-block; font-size: 11px; font-weight: 600;
        padding: 2px 8px; border-radius: 99px; margin-right: 6px;
    }
    .badge-green  { background: #d1fae5; color: #065f46; }
    .badge-amber  { background: #fef3c7; color: #92400e; }
    .badge-red    { background: #fee2e2; color: #991b1b; }

    /* ── Buttons ── */
    .stButton>button {
        width: 100%; border-radius: 7px; font-weight: 500;
        transition: all 0.15s;
    }
    .stButton>button[kind="primary"] {
        background: #1B7A4A !important; border-color: #1B7A4A !important;
    }
    .stButton>button[kind="primary"]:hover {
        background: #155f39 !important; border-color: #155f39 !important;
    }

    /* ── Tables ── */
    .stDataFrame { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)


# ── AUTH CONFIGURATION ────────────────────────────────────────────────────────

_GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
_GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
_APP_URL              = os.environ.get("APP_URL", "http://localhost:8501").rstrip("/")
_ALLOWED_EMAILS       = [e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()]
_USE_GOOGLE_AUTH      = bool(_GOOGLE_CLIENT_ID and _GOOGLE_CLIENT_SECRET)

_GOOGLE_AUTH_URI  = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _build_google_auth_url() -> str:
    """Return a Google OAuth2 authorization URL."""
    from urllib.parse import urlencode
    params = {
        "client_id":     _GOOGLE_CLIENT_ID,
        "redirect_uri":  _APP_URL,
        "response_type": "code",
        "scope":         "openid email profile",
        "access_type":   "online",
        "prompt":        "select_account",
    }
    return f"{_GOOGLE_AUTH_URI}?{urlencode(params)}"


def _exchange_google_code(code: str) -> dict:
    """Exchange an authorization code for user info. Returns id_info dict."""
    # Step 1: POST code → tokens
    token_resp = requests.post(
        _GOOGLE_TOKEN_URI,
        data={
            "code":          code,
            "client_id":     _GOOGLE_CLIENT_ID,
            "client_secret": _GOOGLE_CLIENT_SECRET,
            "redirect_uri":  _APP_URL,
            "grant_type":    "authorization_code",
        },
        timeout=30,
    )
    if token_resp.status_code != 200:
        raise ValueError(f"Token exchange failed ({token_resp.status_code}): {token_resp.text[:300]}")
    tokens = token_resp.json()

    # Step 2: verify ID token via Google's public keys
    request = google.auth.transport.requests.Request()
    id_info = google.oauth2.id_token.verify_oauth2_token(
        tokens["id_token"],
        request,
        _GOOGLE_CLIENT_ID,
        clock_skew_in_seconds=10,
    )
    return id_info


# ── LOGIN GATE ────────────────────────────────────────────────────────────────

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

# ── Handle Google OAuth callback (code in URL query params) ──────────────────
if _USE_GOOGLE_AUTH and not st.session_state.authenticated:
    qp = st.query_params
    if "code" in qp:
        try:
            id_info = _exchange_google_code(qp["code"])
            email   = id_info.get("email", "").lower()
            if _ALLOWED_EMAILS and email not in _ALLOWED_EMAILS:
                st.error(f"Access denied for **{email}**. Ask your admin to add your email to `ALLOWED_EMAILS`.")
                st.query_params.clear()
                st.stop()
            st.session_state.authenticated  = True
            st.session_state.user_email     = email
            st.session_state.user_name      = id_info.get("name", email)
            st.session_state.user_picture   = id_info.get("picture", "")
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Google sign-in failed: {e}")
            st.query_params.clear()
            st.stop()

# ── Show login screen if not yet authenticated ────────────────────────────────
if not st.session_state.authenticated:
    _, login_col, _ = st.columns([2, 3, 2])
    with login_col:
        st.markdown("""
            <div style="text-align:center;padding:60px 0 16px">
                <div style="font-size:26px;font-weight:700">Tulle Admin Dashboard</div>
            </div>
        """, unsafe_allow_html=True)

    if _USE_GOOGLE_AUTH:
        auth_url = _build_google_auth_url()
        _, btn_col, _ = st.columns([2, 3, 2])
        with btn_col:
            st.link_button(
                "Sign in with Google",
                auth_url,
                use_container_width=True,
                type="primary",
            )
    else:
        # Fallback: password auth (for local dev when Google OAuth not configured)
        _, pw_col, _ = st.columns([2, 3, 2])
        with pw_col:
            pwd   = st.text_input("Password", type="password", label_visibility="collapsed",
                                  placeholder="Team password")
            login = st.button("Login", use_container_width=True)
        if login:
            expected = os.environ.get("DASHBOARD_PASSWORD", "")
            if pwd == expected and expected:
                st.session_state.authenticated = True
                st.session_state.user_email    = "local"
                st.session_state.user_name     = "Local admin"
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()


# ── USAGE / QUOTA TRACKER HELPERS ─────────────────────────────────────────────
# Read-only Google Places quota widget for the Google Data & Images tab. Cached 10 min;
# degrades gracefully when credentials/permissions are missing — never raises
# into the page.

def _card(color_class, icon, value, label):
    return f"""<div class="metric-card {color_class}">
        <div class="metric-icon">{icon}</div>
        <div class="metric-value">{value}</div>
        <div class="metric-label">{label}</div>
    </div>"""


GCP_PROJECT_ID        = os.environ.get("GCP_PROJECT_ID", "tulle-technologies")
PLACES_SERVICE        = "places-backend.googleapis.com"
GCP_QUOTAS_URL = (
    "https://console.cloud.google.com/google/maps-apis/quotas"
    f"?project={GCP_PROJECT_ID}&api={PLACES_SERVICE}"
)


def _monitoring_timeseries(token, filter_str, start, end, aligner, reducer=None, period="86400s"):
    """GET Cloud Monitoring timeSeries for the project. Returns the raw `timeSeries` list."""
    params = {
        "filter": filter_str,
        "interval.startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval.endTime":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregation.alignmentPeriod":  period,
        "aggregation.perSeriesAligner": aligner,
    }
    if reducer:
        params["aggregation.crossSeriesReducer"] = reducer
    r = requests.get(
        f"https://monitoring.googleapis.com/v3/projects/{GCP_PROJECT_ID}/timeSeries",
        headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"monitoring {r.status_code}: {r.text[:200]}")
    return r.json().get("timeSeries", [])


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_places_quota():
    """Google Places API quota limit + daily request usage via Cloud Monitoring.

    Returns dict with keys: daily_limit (int|None), limit_label (str), today_usage (int),
    daily_series (list[(date_str, count)]), error (str — "" on success).
    """
    out = {"daily_limit": None, "limit_label": "", "today_usage": 0, "daily_series": [], "error": ""}
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not creds_json:
        out["error"] = "no_creds"
        return out
    try:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(creds_json),
            scopes=["https://www.googleapis.com/auth/monitoring.read"],
        )
        creds.refresh(google.auth.transport.requests.Request())
        token = creds.token

        now   = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(days=30)

        # ── Daily request usage (DELTA metric → ALIGN_SUM per day, summed across methods) ──
        usage_filter = (
            'metric.type="serviceruntime.googleapis.com/api/request_count" '
            f'AND resource.label.service="{PLACES_SERVICE}"'
        )
        series = _monitoring_timeseries(
            token, usage_filter, start, now,
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
        )
        daily = {}
        for ts in series:
            for pt in ts.get("points", []):
                day = pt["interval"]["endTime"][:10]
                val = pt.get("value", {})
                num = val.get("int64Value") or val.get("doubleValue") or 0
                daily[day] = daily.get(day, 0) + int(float(num))
        out["daily_series"] = sorted(daily.items())
        if out["daily_series"]:
            out["today_usage"] = out["daily_series"][-1][1]

        # ── Quota limit — prefer a per-day limit, else fall back to whatever exists ──
        limit_filter = (
            'metric.type="serviceruntime.googleapis.com/quota/limit" '
            f'AND resource.label.service="{PLACES_SERVICE}"'
        )
        limits = _monitoring_timeseries(
            token, limit_filter, now - datetime.timedelta(days=1), now,
            aligner="ALIGN_MAX", period="3600s",
        )
        candidates = []  # (is_daily, limit_name, value)
        for ts in limits:
            name = ts.get("metric", {}).get("labels", {}).get("limit_name", "")
            pts  = ts.get("points", [])
            if not pts:
                continue
            v   = pts[0].get("value", {})
            val = int(float(v.get("int64Value") or v.get("doubleValue") or 0))
            candidates.append(("day" in name.lower(), name, val))
        if candidates:
            daily_ones = [c for c in candidates if c[0]]
            chosen = max(daily_ones or candidates, key=lambda c: c[2])
            out["daily_limit"]  = chosen[2]
            out["limit_label"]  = chosen[1] or ("Per day" if chosen[0] else "Limit")
    except Exception as e:
        out["error"] = f"error: {e}"
    return out


# ── HEADER ────────────────────────────────────────────────────────────────────

user_name  = st.session_state.get("user_name", "")
user_email = st.session_state.get("user_email", "")

_user_info = (f"Signed in as <strong>{user_name}</strong>"
              if user_name and user_name != "Local admin" else "")


@st.cache_data(show_spinner=False)
def _logo_data_uri():
    """Base64 data-URI for the Tulle logo, read once and cached."""
    try:
        with open("tulle.png", "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except Exception:
        return ""


# Single-row header: [logo + title] ............ [signed in as · Sign out]
_h_title, _h_user, _h_btn = st.columns([6, 3, 1.2], vertical_alignment="center")
with _h_title:
    _logo = _logo_data_uri()
    _logo_img = f'<img src="{_logo}" style="height:30px;width:auto" />' if _logo else "🌿"
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px">'
        f'{_logo_img}<span class="tulle-logo">Tulle Admin</span></div>',
        unsafe_allow_html=True,
    )
with _h_user:
    st.markdown(
        f'<div class="tulle-user" style="text-align:right">{_user_info}</div>',
        unsafe_allow_html=True,
    )
with _h_btn:
    if st.button("Sign out", use_container_width=True):
        for k in ["authenticated", "user_email", "user_name", "user_picture"]:
            st.session_state.pop(k, None)
        st.rerun()

st.markdown('<hr class="tulle-rule" />', unsafe_allow_html=True)

XANO_BASE = os.environ.get("XANO_BASE_URL", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B")


# ── TABS ──────────────────────────────────────────────────────────────────────

tab0, tab2, tab5, tab6 = st.tabs([
    "📊 Admin", "🔍 Google Data & Images", "📄 PDF Extraction", "🔎 Vendor Scraper"
])


# ── TAB 0: ADMIN DASHBOARD ────────────────────────────────────────────────────

XANO_WGW = "https://xqtb-2ma7-ijfy.n7e.xano.io/api:WGW_G49d"

EXPLORER_TABLES = {
    "WPTP Updated Mappings": {
        "url":           f"{XANO_BASE}/wptp_updated_mappings",
        "patch":         f"{XANO_BASE}/wptp_updated_mappings",
        "id_col":        "id",
        "editable":      True,
        # Only these columns can be edited (what the PATCH endpoint accepts)
        "editable_cols": ["Flags", "Max_Capacity_Seated"],
        # Rename display column → PATCH input key
        "patch_field_map": {"Flags": "Flag", "Max_Capacity_Seated": "max_capacity"},
        # Millisecond timestamp columns → format as readable date
        "ts_cols":       ["google_data_last_fetched", "Time_of_Submission"],
        # Array columns → display as comma-separated string
        "array_cols":    ["Collection", "category_tags"],
        # Columns to hide entirely (too large / not useful)
        "hide_cols":     ["google_data_cache", "Coordinates"],
    },
    "WPTP PDFs": {
        "url":           f"{XANO_BASE}/wptp_pdfs",
        "patch":         None,
        "id_col":        "id",
        "editable":      False,
        "editable_cols": [],
        "patch_field_map": {},
        "ts_cols":       [],
        "array_cols":    [],
        "hide_cols":     [],
    },
    "Users": {
        "url":           f"{XANO_WGW}/user",
        "patch":         f"{XANO_WGW}/user",
        "id_col":        "id",
        "editable":      True,
        "editable_cols": [],   # edit any field
        "patch_field_map": {},
        "ts_cols":       ["created_at"],
        "array_cols":    ["saved_vendor_ids"],
        "hide_cols":     [],
    },
    "Extracted PDF Data": {
        "url":           f"{XANO_BASE}/all_extracted_pdf_data",
        "patch":         None,
        "id_col":        "id",
        "editable":      False,
        "editable_cols": [],
        "patch_field_map": {},
        "ts_cols":       [],
        "array_cols":    [],
        "hide_cols":     [],
    },
    "Venue Pricing": {
        "url":           f"{XANO_BASE}/venue_pricing",
        "patch":         None,
        "id_col":        "id",
        "editable":      False,
        "editable_cols": [],
        "patch_field_map": {},
        "ts_cols":       [],
        "array_cols":    [],
        "hide_cols":     [],
    },
}

FILTER_OPS = ["contains", "equals", "starts with", "not equals",
              ">", "<", ">=", "<=", "is blank", "is not blank"]

def _apply_filters(df, filters):
    for col, op, val in filters:
        if col not in df.columns:
            continue
        s = df[col].astype(str)
        if op == "contains":
            df = df[s.str.contains(str(val), case=False, na=False)]
        elif op == "equals":
            df = df[s.str.lower() == str(val).lower()]
        elif op == "starts with":
            df = df[s.str.lower().str.startswith(str(val).lower(), na=False)]
        elif op == "not equals":
            df = df[s.str.lower() != str(val).lower()]
        elif op == ">":
            try:
                df = df[pd.to_numeric(df[col], errors="coerce") > float(val)]
            except Exception:
                pass
        elif op == "<":
            try:
                df = df[pd.to_numeric(df[col], errors="coerce") < float(val)]
            except Exception:
                pass
        elif op == ">=":
            try:
                df = df[pd.to_numeric(df[col], errors="coerce") >= float(val)]
            except Exception:
                pass
        elif op == "<=":
            try:
                df = df[pd.to_numeric(df[col], errors="coerce") <= float(val)]
            except Exception:
                pass
        elif op == "is blank":
            df = df[df[col].isna() | (s.str.strip() == "")]
        elif op == "is not blank":
            df = df[~(df[col].isna() | (s.str.strip() == ""))]
    return df

def _to_ms(d: datetime.date, end_of_day=False) -> int:
    t = datetime.time(23, 59, 59) if end_of_day else datetime.time(0, 0, 0)
    dt = datetime.datetime.combine(d, t, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)

with tab0:

    # ── ABOUT ─────────────────────────────────────────────────────────────────
    with st.expander("ℹ️ What is this dashboard?", expanded=False):
        st.markdown("""
**Tulle Admin** is the internal ops tool for [tulletogether.app](https://tulletogether.app) — a wedding vendor pricing platform where couples pay to access crowdsourced pricing PDFs from real vendors.

The core workflow this dashboard runs:

> Vendors submit pricing PDFs → Claude extracts structured data → rows land in Xano → WeWeb surfaces them to paying users

---

**Tab guide**

| Tab | What it does |
|---|---|
| **Admin** | Timebound reports (signups, payments, packages, to-dos) + Data Explorer for browsing/editing Xano tables |
| **Google Data & Images** | Two Google Places operations sharing one daily quota (shown at the top): **Google Data** caches Places info (rating, reviews, address) for vendors with a Place ID, and **Vendor Images** pulls photos into WPTP Updated Mappings |
| **PDF Extraction** | Production extraction queue — shows status across all 6,700+ PDFs (Pending / Extracted / Partial / Failed), with run controls (pending, failed, specific IDs, row range) and per-venue result cards. Downloads PDFs from Drive, runs Claude (4 passes), posts rows to Xano |

---

**What Claude extracts per PDF (4 passes):**
1. Summary fields — venue type, pricing year, admin fee, peak/off-peak Saturday fees
2. Pricing grid structure — spaces, seasons, day columns
3. Full pricing grid — venue fee + F&B min + per-person by month × day (up to ~96 rows/PDF)
4. Classification — venue offering (Raw/Semi-Inclusive/All-Inclusive), attributes, category

Typical cost: ~$0.20–0.40 per PDF. Model: `claude-sonnet-4-20250514`.
        """)

    st.markdown("---")

    # ── METRICS ───────────────────────────────────────────────────────────────
    st.subheader("Timebound Reporting")
    st.caption("Generate reports for user signups, to-dos created, and payments made within a specific date range.")

    col_s, col_e, col_btn = st.columns([2, 2, 1])
    with col_s:
        start_date = st.date_input("Start Date",
                                   value=datetime.date.today() - datetime.timedelta(days=30))
    with col_e:
        end_date = st.date_input("End Date", value=datetime.date.today())
    with col_btn:
        st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
        generate = st.button("Generate Report", type="primary", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    if generate:
        with st.spinner("Fetching data from Xano..."):
            try:
                start_ts = _to_ms(start_date)
                end_ts   = _to_ms(end_date, end_of_day=True)

                def _fetch_all(url):
                    """GET url, unwrap paginated envelope, return (list, status_code)."""
                    r = requests.get(url, timeout=60)
                    if r.status_code != 200:
                        return None, r.status_code
                    data = r.json()
                    if isinstance(data, dict):
                        data = data.get("items") or data.get("data") or data.get("result") or []
                    return data if isinstance(data, list) else [], 200

                with ThreadPoolExecutor(max_workers=4) as pool:
                    f_users    = pool.submit(_fetch_all, f"{XANO_WGW}/user")
                    f_todos    = pool.submit(_fetch_all, f"{XANO_WGW}/to_do_items")
                    f_packages = pool.submit(_fetch_all, f"{XANO_WGW}/packages")
                    f_payments = pool.submit(_fetch_all, f"{XANO_WGW}/donation_payment_log")
                    users_data,    users_sc    = f_users.result()
                    todos_data,    todos_sc    = f_todos.result()
                    packages_data, packages_sc = f_packages.result()
                    payments_data, payments_sc = f_payments.result()

                errors = []
                if users_sc    != 200: errors.append(f"users ({users_sc})")
                if todos_sc    != 200: errors.append(f"to_do_items ({todos_sc})")
                if packages_sc != 200: errors.append(f"packages ({packages_sc})")
                if payments_sc != 200: errors.append(f"donation_payment_log ({payments_sc})")
                if errors:
                    st.error(f"Endpoint(s) failed: {', '.join(errors)}")

                def _in_range(rows, ts_field):
                    return [
                        r for r in (rows or [])
                        if r.get(ts_field) is not None
                        and start_ts <= r[ts_field] <= end_ts
                    ]

                def _unique_users(rows):
                    seen = set()
                    for r in rows:
                        uid = (r.get("user_id")
                               or r.get("User")
                               or r.get("user")
                               or (r.get("_user") if isinstance(r.get("_user"), (int, str)) else None))
                        if uid:
                            seen.add(str(uid))
                    return len(seen)

                # Signups — filter by created_at
                users_range = _in_range(users_data or [], "created_at")
                signups     = len(users_range)

                # To-Dos
                todos_range = _in_range(todos_data or [], "created_at")
                todo_made   = len(todos_range)
                todo_uniq   = _unique_users(todos_range)
                todo_rate   = (todo_uniq * 100 / signups) if signups > 0 else 0.0

                # Packages (exclude "Example" vendor names)
                pkg_range = [
                    r for r in _in_range(packages_data or [], "created_at")
                    if "example" not in str(
                        r.get("vendor_name") or r.get("Vendor_Name") or r.get("name") or ""
                    ).lower()
                ]
                pkg_made  = len(pkg_range)
                pkg_uniq  = _unique_users(pkg_range)
                pkg_rate  = (pkg_uniq * 100 / signups) if signups > 0 else 0.0

                # Payments
                pay_range = _in_range(payments_data or [], "Time_of_Payment")
                pay_made  = len(pay_range)
                pay_uniq  = _unique_users(pay_range)
                pay_rate  = (pay_uniq * 100 / signups) if signups > 0 else 0.0

                st.markdown(_card("card-green", "👤", signups, "New Signups"),
                            unsafe_allow_html=True)
                c1, c2, c3 = st.columns(3)
                c1.markdown(_card("card-amber",  "💳", pay_made,          "Payments Made"),           unsafe_allow_html=True)
                c2.markdown(_card("card-amber",  "💳", pay_uniq,          "Unique Payers"),            unsafe_allow_html=True)
                c3.markdown(_card("card-amber",  "💳", f"{pay_rate:.1f}%","Payment Rate"),             unsafe_allow_html=True)
                c4, c5, c6 = st.columns(3)
                c4.markdown(_card("card-green",  "✅", todo_made,          "To-Dos Created"),          unsafe_allow_html=True)
                c5.markdown(_card("card-green",  "✅", todo_uniq,          "Unique Users w/ To-Dos"),  unsafe_allow_html=True)
                c6.markdown(_card("card-green",  "✅", f"{todo_rate:.1f}%","To-Do Creation Rate"),     unsafe_allow_html=True)
                c7, c8, c9 = st.columns(3)
                c7.markdown(_card("card-purple", "📦", pkg_made,           "Packages Created"),        unsafe_allow_html=True)
                c8.markdown(_card("card-purple", "📦", pkg_uniq,           "Unique Users w/ Packages"),unsafe_allow_html=True)
                c9.markdown(_card("card-purple", "📦", f"{pkg_rate:.1f}%", "Package Creation Rate"),   unsafe_allow_html=True)

            except Exception as e:
                st.error(f"Request failed: {e}")

    # ── DATA EXPLORER ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Data Explorer")

    exp_table = st.selectbox("Table", list(EXPLORER_TABLES.keys()), key="exp_table")
    exp_cfg   = EXPLORER_TABLES[exp_table]

    # Load controls
    col_lim, col_load, col_clr = st.columns([2, 2, 1])
    with col_lim:
        row_limit = st.selectbox("Row limit", [100, 500, 1000, 0], format_func=lambda x: "All" if x == 0 else str(x), key="exp_limit")
    with col_load:
        st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
        load_data = st.button("Load Data", type="primary", use_container_width=True, key="exp_load")
        st.markdown("</div>", unsafe_allow_html=True)
    with col_clr:
        st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
        if st.button("Clear", use_container_width=True, key="exp_clear"):
            for k in ["exp_raw", "exp_loaded_table", "exp_filters"]:
                st.session_state.pop(k, None)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    if load_data:
        with st.spinner(f"Loading {exp_table}..."):
            try:
                r = requests.get(exp_cfg["url"], timeout=120)
                if r.status_code == 200:
                    raw = r.json()
                    if isinstance(raw, dict):
                        raw = raw.get("items") or raw.get("data") or raw.get("result") or []
                    st.session_state["exp_raw"]          = raw
                    st.session_state["exp_loaded_table"] = exp_table
                    st.session_state["exp_filters"]      = []
                else:
                    st.error(f"Xano returned {r.status_code}: {r.text[:200]}")
            except Exception as e:
                st.error(f"Load failed: {e}")

    if st.session_state.get("exp_loaded_table") == exp_table and st.session_state.get("exp_raw"):
        raw  = st.session_state["exp_raw"]

        # ── Pre-process raw → display DataFrame ───────────────────────────
        df_all = pd.DataFrame(raw)

        # Drop hidden columns
        for col in exp_cfg.get("hide_cols", []):
            if col in df_all.columns:
                df_all.drop(columns=[col], inplace=True)

        # Format ms timestamps as readable dates
        for col in exp_cfg.get("ts_cols", []):
            if col in df_all.columns:
                df_all[col] = pd.to_datetime(
                    df_all[col], unit="ms", utc=True, errors="coerce"
                ).dt.strftime("%Y-%m-%d %H:%M")

        # Format array columns as comma-separated strings
        for col in exp_cfg.get("array_cols", []):
            if col in df_all.columns:
                df_all[col] = df_all[col].apply(
                    lambda x: ", ".join(str(i) for i in x) if isinstance(x, list) else (str(x) if x else "")
                )

        cols = list(df_all.columns)

        # ── Filter UI ──────────────────────────────────────────────────────
        st.markdown("**Filters**")
        if "exp_filters" not in st.session_state:
            st.session_state["exp_filters"] = []

        fc1, fc2, fc3, fc4 = st.columns([3, 2, 3, 1])
        with fc1:
            f_col = st.selectbox("Column", cols, key="f_col", label_visibility="collapsed")
        with fc2:
            f_op  = st.selectbox("Operator", FILTER_OPS, key="f_op", label_visibility="collapsed")
        with fc3:
            f_val = st.text_input("Value", key="f_val", label_visibility="collapsed",
                                  placeholder="value" if f_op not in ("is blank", "is not blank") else "—",
                                  disabled=f_op in ("is blank", "is not blank"))
        with fc4:
            if st.button("Add", use_container_width=True, key="f_add"):
                st.session_state["exp_filters"].append((f_col, f_op, f_val))
                st.rerun()

        for i, (fc, fo, fv) in enumerate(st.session_state.get("exp_filters", [])):
            tag_col, rm_col = st.columns([8, 1])
            tag_col.markdown(f"`{fc}` **{fo}** `{fv}`")
            if rm_col.button("✕", key=f"rm_{i}"):
                st.session_state["exp_filters"].pop(i)
                st.rerun()

        # ── Build display DataFrame ────────────────────────────────────────
        df = _apply_filters(df_all.copy(), st.session_state.get("exp_filters", []))
        if row_limit:
            df = df.head(row_limit)

        st.caption(f"{len(df):,} of {len(df_all):,} rows — {exp_table}"
                   + ("" if exp_cfg["editable"] else "  ·  read-only"))

        # Determine which columns are locked
        editable_cols = exp_cfg.get("editable_cols", [])
        if not exp_cfg["editable"]:
            disabled_arg = True
        elif editable_cols:
            disabled_arg = [c for c in df.columns if c not in editable_cols]
        else:
            disabled_arg = False

        # ── Display / Edit ─────────────────────────────────────────────────
        edited = st.data_editor(
            df,
            use_container_width=True,
            num_rows="fixed",
            disabled=disabled_arg,
            key="exp_editor",
        )

        if exp_cfg["editable"]:
            if st.button("💾 Save Changes", type="primary", use_container_width=True, key="exp_save"):
                id_col     = exp_cfg["id_col"]
                patch_base = exp_cfg["patch"]
                field_map  = exp_cfg.get("patch_field_map", {})
                orig_map   = {str(r[id_col]): r for r in raw}

                # Collect only changed editable fields
                changes: list[tuple[str, dict]] = []
                for _, row in edited.iterrows():
                    row_id = str(row[id_col])
                    orig   = orig_map.get(row_id, {})
                    watch  = editable_cols if editable_cols else [c for c in row.index if c != id_col]
                    changed = {
                        field_map.get(k, k): row[k]
                        for k in watch
                        if k in row.index and str(row[k]) != str(orig.get(k, ""))
                    }
                    if changed:
                        changes.append((row_id, changed))

                if not changes:
                    st.info("No changes detected.")
                else:
                    def _do_patch(row_id, payload):
                        try:
                            r = requests.patch(f"{patch_base}/{row_id}", json=payload, timeout=15)
                            return r.status_code in (200, 201, 204), row_id
                        except Exception:
                            return False, row_id

                    with st.spinner(f"Saving {len(changes)} row(s)..."):
                        with ThreadPoolExecutor(max_workers=10) as pool:
                            futures = [pool.submit(_do_patch, rid, payload) for rid, payload in changes]
                            results = [f.result() for f in as_completed(futures)]

                    saved  = sum(1 for ok, _ in results if ok)
                    failed = len(results) - saved

                    if failed == 0:
                        st.success(f"Saved {saved} row(s).")
                    else:
                        st.warning(f"Saved {saved}, failed {failed}.")


# ── TAB 2: GOOGLE DATA & IMAGES ───────────────────────────────────────────────
# Google Data and Vendor Images both call the Google Places API and draw from the
# same daily quota, so they live in one tab with the quota panel shown once.

with tab2:
    st.subheader("Google Data & Images")
    st.caption(
        "Both sections below call the **Google Places API** and share the daily quota shown here. "
        "Run **Google Data** first to cache Places data, then **Vendor Images** to pull photos "
        "(images require cached Google data)."
    )

    # ── Display active jobs ───────────────────────────────────────────────────
    if st.session_state.get("gg_refresh_jobs"):
        with st.spinner("Fetching latest job status..."):
            active_google = _get_active_job("google_data")
            active_images = _get_active_job("vendor_images")
        st.session_state["gg_refresh_jobs"] = False
    else:
        active_google = _get_active_job("google_data")
        active_images = _get_active_job("vendor_images")

    if active_google or active_images:
        if active_google and active_images:
            g_col1, g_col2, g_btn = st.columns([2.4, 2.4, 0.2])
            with g_col1:
                st.info(_format_job_display(active_google))
            with g_col2:
                st.info(_format_job_display(active_images))
            with g_btn:
                st.markdown("")
                if st.button("🔄", key="refresh_google_jobs", help="Refresh job status"):
                    st.session_state["gg_refresh_jobs"] = True
                    st.rerun()
        elif active_google:
            g_col1, g_btn = st.columns([5, 1])
            with g_col1:
                st.info(_format_job_display(active_google))
            with g_btn:
                st.markdown("")
                if st.button("🔄", key="refresh_google_data_job", help="Refresh job status"):
                    st.session_state["gg_refresh_jobs"] = True
                    st.rerun()
        else:
            g_col1, g_btn = st.columns([5, 1])
            with g_col1:
                st.info(_format_job_display(active_images))
            with g_btn:
                st.markdown("")
                if st.button("🔄", key="refresh_vendor_images_job", help="Refresh job status"):
                    st.session_state["gg_refresh_jobs"] = True
                    st.rerun()

    # ── Places API quota tracker (shared by both sections) ────────────────────
    _q = _fetch_places_quota()
    _q_head, _q_link, _q_refresh = st.columns([6, 1.6, 1])
    with _q_head:
        st.markdown("**Google Places API — quota & usage**")
    with _q_link:
        st.link_button("GCP quotas ↗", GCP_QUOTAS_URL, use_container_width=True)
    with _q_refresh:
        if st.button("↻", help="Refresh quota", use_container_width=True, key="refresh_quota"):
            _fetch_places_quota.clear()
            st.rerun()

    if _q["error"] == "no_creds":
        st.info("Set GOOGLE_SERVICE_ACCOUNT_JSON to read Places API quota.")
    elif _q["error"]:
        st.warning(
            f"Couldn't read Places quota — {_q['error']}. "
            "Grant the service account the **Monitoring Viewer** role on "
            f"`{GCP_PROJECT_ID}`, then refresh."
        )
    else:
        _limit  = _q["daily_limit"]
        _used   = _q["today_usage"]
        _pct    = (_used / _limit * 100) if _limit else 0
        _used_class = "card-green"
        if _limit and _pct >= 100:   _used_class = "card-red"
        elif _limit and _pct >= 80:  _used_class = "card-amber"
        _total30 = sum(c for _, c in _q["daily_series"])
        qc1, qc2, qc3 = st.columns(3)
        qc1.markdown(
            _card("card-gray", "📊",
                  f"{_limit:,}" if _limit else "—",
                  f"Quota limit · {_q['limit_label'] or 'n/a'}"),
            unsafe_allow_html=True)
        qc2.markdown(
            _card(_used_class, "✅",
                  f"{_used:,}",
                  f"Requests today{f' · {_pct:.0f}% of limit' if _limit else ''}"),
            unsafe_allow_html=True)
        qc3.markdown(
            _card("card-gray", "📈", f"{_total30:,}", "Requests · last 30 days"),
            unsafe_allow_html=True)
        if _q["daily_series"]:
            _df = pd.DataFrame(_q["daily_series"], columns=["date", "requests"]).set_index("date")
            st.bar_chart(_df, height=180)
        else:
            st.caption("No Places API requests recorded in the last 30 days.")

    st.markdown("---")

    # ── Section 1: Google Data Cache ──────────────────────────────────────────
    st.markdown("### 🔍 Google Data Cache")
    st.caption("Fetches Google Places data for vendors in WPTP Updated Mappings that have a Place ID but no cached data yet.")

    col_s2, col_e2 = st.columns(2)
    with col_s2:
        gd_start = st.number_input("Starting index (vendor ID)", min_value=1, value=1, step=1, key="gd_start")
    with col_e2:
        gd_end = st.number_input("Ending index (vendor ID)", min_value=1, value=500, step=1, key="gd_end")

    if st.button("▶ Run Google Data Batch", type="primary", use_container_width=True):
        with st.spinner("Running — Xano is fetching Google Places data for each vendor..."):
            try:
                resp = requests.get(
                    f"{XANO_BASE}/google_data_batch",
                    params={"starting_index": int(gd_start), "ending_index": int(gd_end)},
                    timeout=300,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    count = len(data) if isinstance(data, list) else "?"
                    st.success(f"Done — {count} vendors processed")
                    with st.expander("Xano response", expanded=False):
                        st.json(data)
                else:
                    st.error(f"Xano returned {resp.status_code}")
                    st.code(resp.text[:500])
            except requests.exceptions.Timeout:
                st.warning("Request timed out (Xano may still be processing). Check Xano directly.")
            except Exception as e:
                st.error(f"Request failed: {e}")

    st.markdown("---")

    # ── Section 2: Vendor Images ──────────────────────────────────────────────
    st.markdown("### 🖼️ Vendor Images")
    st.caption(
        "Pulls photos from Google Places and saves them into WPTP Updated Mappings. "
        "Run **Google Data** above first — images require cached Google data. "
        "Run all 3 in order, or individually."
    )

    col_s3, col_e3 = st.columns(2)
    with col_s3:
        img_start = st.number_input("Starting index (vendor ID)", min_value=1, value=1, step=1, key="img_start")
    with col_e3:
        img_end = st.number_input("Ending index (vendor ID)", min_value=1, value=500, step=1, key="img_end")

    def run_image_endpoint(slot: int):
        endpoint = f"{XANO_BASE}/update_vendor_image_{['one','two','three'][slot-1]}"
        try:
            resp = requests.post(
                endpoint,
                json={"starting_index": int(img_start), "ending_index": int(img_end)},
                timeout=300,
            )
            return resp.status_code, resp.json() if resp.headers.get("content-type","").startswith("application/json") else resp.text
        except requests.exceptions.Timeout:
            return None, "Timed out — Xano may still be processing. Check Xano directly."
        except Exception as e:
            return None, str(e)

    # Individual buttons
    st.markdown("**Run individually:**")
    col_i1, col_i2, col_i3 = st.columns(3)

    for slot, col in [(1, col_i1), (2, col_i2), (3, col_i3)]:
        with col:
            if st.button(f"Image {slot}", use_container_width=True, key=f"img_btn_{slot}"):
                with st.spinner(f"Updating image {slot}..."):
                    code, data = run_image_endpoint(slot)
                if code == 200:
                    count = data.get("processed_count", "?") if isinstance(data, dict) else "?"
                    st.success(f"Image {slot} done — {count} vendors")
                    with st.expander(f"Image {slot} response", expanded=False):
                        st.json(data)
                else:
                    st.error(f"Image {slot} — {'timeout' if code is None else f'status {code}'}")
                    if isinstance(data, str):
                        st.caption(data)

    st.markdown("---")

    # Run all 3 in sequence
    if st.button("▶ Run All 3 Images in Sequence", type="primary", use_container_width=True):
        for slot in [1, 2, 3]:
            with st.spinner(f"Running image {slot} of 3..."):
                code, data = run_image_endpoint(slot)
            if code == 200:
                count = data.get("processed_count", "?") if isinstance(data, dict) else "?"
                st.success(f"Image {slot} — {count} vendors updated")
            else:
                st.error(f"Image {slot} failed — {'timeout' if code is None else f'status {code}'}")
                if isinstance(data, str):
                    st.caption(data)
                st.warning("Stopping — fix image 1 before continuing to 2 and 3.")
                break


# ── TAB 5: PIPELINE ───────────────────────────────────────────────────────────

with tab5:
    st.subheader("PDF Extraction Pipeline")
    st.caption(
        "Track extraction status across all PDFs in wptp_pdfs. "
        "Run pending, failed, or specific PDFs without touching already-extracted records."
    )

    # ── Display active jobs ───────────────────────────────────────────────────
    job_info_col, job_btn_col = st.columns([5, 1])

    if st.session_state.get("pl_refresh_job"):
        with st.spinner("Fetching latest job status..."):
            active_job = _get_active_job("extraction")
        st.session_state["pl_refresh_job"] = False
    else:
        active_job = _get_active_job("extraction")

    if active_job:
        with job_info_col:
            st.info(_format_job_display(active_job))
        with job_btn_col:
            st.markdown("")  # spacing
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🔄", key="refresh_extraction_job", help="Refresh job status"):
                    st.session_state["pl_refresh_job"] = True
                    st.rerun()
            with col2:
                if st.button("❌", key="clear_extraction_job", help="Mark as failed (stuck job)"):
                    try:
                        endpoint = os.environ.get("XANO_JOB_STATUS_ENDPOINT", "")
                        if endpoint:
                            requests.post(
                                endpoint,
                                json={
                                    "job_type": "extraction",
                                    "status": "failed",
                                    "user_email": active_job.get("user_email", "unknown"),
                                    "result_summary": {"reason": "manually marked failed (stuck)"},
                                    "is_active": False,
                                    "id": active_job.get("id"),
                                },
                                timeout=10
                            )
                            st.success("✅ Job marked as failed. Refresh page.")
                    except Exception as e:
                        st.error(f"Failed to clear: {e}")

    # ── Status overview ───────────────────────────────────────────────────────
    refresh_col, _ = st.columns([2, 6])
    with refresh_col:
        load_status = st.button("🔄 Load / Refresh Status", type="primary", use_container_width=True, key="pl_refresh")

    if load_status or st.session_state.get("pl_status_loaded"):
        if load_status:
            with st.spinner("Fetching pipeline status from Xano..."):
                st.session_state["pl_data"] = get_pipeline_status()
            st.session_state["pl_status_loaded"] = True

        pl = st.session_state.get("pl_data", {})
        counts    = pl.get("counts", {})
        all_rows  = pl.get("rows", [])
        total     = pl.get("total", 0)
        with_link = pl.get("with_link", 0)

        # ── Metric cards ──────────────────────────────────────────────────────
        st.markdown("#### Status Overview")
        c_pending, c_extracted, c_partial, c_failed, c_skipped = st.columns(5)

        c_pending.markdown(
            f"""<div class="metric-card card-amber">
                <div class="metric-icon">⏳</div>
                <div class="metric-value">{counts.get('pending', 0)}</div>
                <div class="metric-label">Pending</div>
            </div>""", unsafe_allow_html=True
        )
        c_extracted.markdown(
            f"""<div class="metric-card card-green">
                <div class="metric-icon">✅</div>
                <div class="metric-value">{counts.get('extracted', 0)}</div>
                <div class="metric-label">Extracted</div>
            </div>""", unsafe_allow_html=True
        )
        c_partial.markdown(
            f"""<div class="metric-card card-purple">
                <div class="metric-icon">⚠️</div>
                <div class="metric-value">{counts.get('partial', 0)}</div>
                <div class="metric-label">Partial</div>
            </div>""", unsafe_allow_html=True
        )
        c_failed.markdown(
            f"""<div class="metric-card card-red">
                <div class="metric-icon">❌</div>
                <div class="metric-value">{counts.get('failed', 0)}</div>
                <div class="metric-label">Failed</div>
            </div>""", unsafe_allow_html=True
        )
        c_skipped.markdown(
            f"""<div class="metric-card card-gray">
                <div class="metric-icon">⏭️</div>
                <div class="metric-value">{counts.get('skipped', 0)}</div>
                <div class="metric-label">Skipped</div>
            </div>""", unsafe_allow_html=True
        )

        st.caption(f"{total:,} total rows in wptp_pdfs · {with_link:,} have a Drive link")
        st.markdown("---")

        # ── Status table with filters ─────────────────────────────────────────
        st.markdown("#### PDF Status Table")

        display_cols = [
            'id', 'PDF_ID', 'Vendor_ID', 'Name',
            'extraction_status', 'last_extracted_at',
            'extraction_cost_usd', 'extraction_attempts', 'last_error',
        ]
        df_raw = pd.DataFrame(all_rows)

        # Add missing status columns gracefully
        for col in display_cols:
            if col not in df_raw.columns:
                df_raw[col] = ""

        df_display = df_raw[[c for c in display_cols if c in df_raw.columns]].copy()

        # Normalise status: blank → pending
        if 'extraction_status' in df_display.columns:
            df_display['extraction_status'] = df_display['extraction_status'].apply(
                lambda x: x if str(x).strip().lower() in ('extracted', 'partial', 'failed', 'skipped') else 'pending'
            )

        # Filter controls
        filter_status = st.multiselect(
            "Filter by status",
            options=['pending', 'extracted', 'partial', 'failed', 'skipped'],
            default=['pending'],
            key="pl_filter_status",
        )
        search_term = st.text_input("Search by PDF_ID or venue name", key="pl_search", placeholder="e.g. PDF_042 or Cipriani")

        df_filtered = df_display.copy()
        if filter_status:
            df_filtered = df_filtered[df_filtered['extraction_status'].isin(filter_status)]
        if search_term:
            mask = (
                df_filtered.get('PDF_ID', pd.Series(dtype=str)).astype(str).str.contains(search_term, case=False, na=False) |
                df_filtered.get('Name',   pd.Series(dtype=str)).astype(str).str.contains(search_term, case=False, na=False)
            )
            df_filtered = df_filtered[mask]

        st.caption(f"Showing {len(df_filtered):,} of {len(df_display):,} rows")
        st.dataframe(df_filtered, use_container_width=True, hide_index=True)

        # CSV export
        csv_bytes = df_filtered.to_csv(index=False).encode()
        st.download_button(
            "⬇ Export filtered table as CSV",
            csv_bytes,
            file_name="pipeline_status.csv",
            mime="text/csv",
            key="pl_csv",
        )

        st.markdown("---")

        # ── Run controls ──────────────────────────────────────────────────────
        st.markdown("#### Run Extraction")

        # Batch mode toggle — 50% cost reduction via async Batch API
        _batch_col, _info_col = st.columns([3, 5])
        with _batch_col:
            batch_mode = st.toggle(
                "⚡ Batch mode (50% cheaper)",
                value=st.session_state.get("pl_batch_mode", False),
                key="pl_batch_mode",
                help=(
                    "Submits all Claude requests via the Batch API (50% discount). "
                    "Downloads happen live; Claude processing takes up to 24 hrs. "
                    "Pass 4 (classification) uses Haiku in all modes.\n\n"
                    "Normal mode: live streaming log, results in minutes.\n"
                    "Batch mode: downloads now, Claude processes overnight, "
                    "check results with the button below."
                ),
            )
        with _info_col:
            if batch_mode:
                st.info("Batch mode on — downloads PDFs now, submits to Batch API (~24hr). Pass 4 uses Haiku. ~55% cheaper total.")
            else:
                st.caption("Normal mode — live extraction. Pass 4 uses Haiku (~3% cheaper).")

        run_mode = st.radio(
            "Run mode",
            options=["🆕 All pending", "❌ Re-run all failed", "🎯 Specific PDF IDs", "📏 Row range"],
            horizontal=True,
            key="pl_run_mode",
        )

        specific_ids_input = ""
        pl_start_row = 0
        pl_end_row   = 10

        if run_mode == "🎯 Specific PDF IDs":
            specific_ids_input = st.text_area(
                "PDF IDs to run (one per line or comma-separated)",
                height=100,
                placeholder="PDF_042\nPDF_117\nPDF_203",
                key="pl_specific_ids",
            )
        elif run_mode == "📏 Row range":
            rc1, rc2 = st.columns(2)
            with rc1:
                pl_start_row = st.number_input("Start row", min_value=0, value=0, step=1, key="pl_start")
            with rc2:
                pl_end_row = st.number_input("End row (0 = all)", min_value=0, value=10, step=1, key="pl_end")

        n_pending = counts.get('pending', 0)
        n_failed  = counts.get('failed', 0)
        _mode_verb = "Submit Batch" if batch_mode else "Run"

        btn_label = {
            "🆕 All pending":        f"▶ {_mode_verb} All Pending ({n_pending})",
            "❌ Re-run all failed":   f"▶ {_mode_verb} All Failed ({n_failed})",
            "🎯 Specific PDF IDs":   f"▶ {_mode_verb} Specified PDFs",
            "📏 Row range":          f"▶ {_mode_verb} Row Range",
        }.get(run_mode, f"▶ {_mode_verb}")

        if "pl_running" not in st.session_state:
            st.session_state["pl_running"] = False

        run_btn = st.button(
            btn_label,
            type="primary",
            use_container_width=True,
            disabled=st.session_state["pl_running"],
            key="pl_run_btn",
        )

        pl_log_ph  = st.empty()
        pl_stat_ph = st.empty()

        # ── Parse run mode args (shared by both normal + batch) ───────────────
        def _parse_run_args():
            pdf_ids_list = None; rerun_failed = False; eff_start = 0; eff_end = None
            if run_mode == "🎯 Specific PDF IDs":
                raw = specific_ids_input.replace(",", "\n")
                pdf_ids_list = [v.strip() for v in raw.splitlines() if v.strip()]
                if not pdf_ids_list:
                    st.warning("Enter at least one PDF ID."); st.stop()
            elif run_mode == "❌ Re-run all failed":
                rerun_failed = True
            elif run_mode == "📏 Row range":
                eff_start = int(pl_start_row)
                eff_end   = None if int(pl_end_row) == 0 else int(pl_end_row)
            return pdf_ids_list, rerun_failed, eff_start, eff_end

        if run_btn:
            pdf_ids_list, rerun_failed, eff_start, eff_end = _parse_run_args()
            st.session_state["pl_running"] = True
            # Track job persistently (survives logouts)
            _post_job_status("extraction", "running", st.session_state.get("user_email", "unknown"))
            pl_lines = []; pl_result = None
            run_started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

            if batch_mode:
                # ── Batch path ────────────────────────────────────────────────
                for item in run_extraction_batch(
                    start_row=eff_start, end_row=eff_end,
                    pdf_ids=pdf_ids_list, rerun_failed=rerun_failed,
                ):
                    if isinstance(item, dict):
                        pl_result = item
                        break
                    pl_lines.append(item)
                    pl_log_ph.markdown(
                        '<div class="log-box">' + "\n".join(pl_lines) + "</div>",
                        unsafe_allow_html=True,
                    )
                st.session_state["pl_running"] = False
                # Track job completion persistently
                job_status = "completed" if (pl_result and pl_result.get("batch_submitted")) else "failed"
                _post_job_status("extraction", job_status, st.session_state.get("user_email", "unknown"),
                                 result_summary=pl_result)
                if pl_result and pl_result.get("batch_submitted"):
                    bid = pl_result["batch_id"]
                    st.session_state["pl_batch_id"]   = bid
                    st.session_state["pl_batch_map"]  = pl_result["pdf_map"]
                    pl_stat_ph.success(
                        f"Batch submitted ({pl_result['pdf_count']} PDFs) · ID: `{bid}`  \n"
                        "Processing takes up to 24 hrs — use **Check Batch Results** below."
                    )
                elif pl_result:
                    pl_stat_ph.error(f"Batch submission failed: {pl_result.get('error', '?')}")
            else:
                # ── Normal (live) path ────────────────────────────────────────
                for item in run_extraction(
                    start_row=eff_start, end_row=eff_end,
                    pdf_ids=pdf_ids_list, rerun_failed=rerun_failed,
                ):
                    if isinstance(item, dict):
                        pl_result = item
                        break
                    pl_lines.append(item)
                    pl_log_ph.markdown(
                        '<div class="log-box">' + "\n".join(pl_lines) + "</div>",
                        unsafe_allow_html=True,
                    )
                st.session_state["pl_running"] = False
                # Track job completion persistently
                _post_job_status("extraction", "completed", st.session_state.get("user_email", "unknown"),
                                 result_summary=pl_result)

                if pl_result:
                    ok   = pl_result["ok"]
                    part = pl_result["partial"]
                    fail = pl_result["failed"]
                    cost = pl_result.get("cost_usd", 0.0)

                    if pl_result.get("credit_exhausted"):
                        st.toast("🛑 Anthropic credits exhausted — extraction halted.", icon="🛑")
                        pl_stat_ph.error(
                            f"🛑 Halted — ran out of Anthropic credits after {ok} succeeded · ${cost:.4f}"
                        )
                    elif fail == 0 and part == 0:
                        pl_stat_ph.success(f"Done — {ok} succeeded · ${cost:.4f}")
                    elif fail > 0:
                        pl_stat_ph.error(f"Done — {ok} succeeded, {part} partial, {fail} failed · ${cost:.4f}")
                    else:
                        pl_stat_ph.warning(f"Done — {ok} succeeded, {part} partial · ${cost:.4f}")

                    pl_result["run_started_at"] = run_started_at
                    st.session_state["pl_last_result"] = pl_result
                    st.session_state["pl_data"] = get_pipeline_status()
                    st.rerun()

        # ── Batch results checker (shown when a batch_id is stored) ───────────
        _bid = st.session_state.get("pl_batch_id")
        if _bid:
            st.info(f"Pending batch: `{_bid}`")
            _chk_col, _clr_col = st.columns([3, 1])
            with _chk_col:
                check_btn = st.button("📊 Check Batch Results", use_container_width=True, key="pl_check_batch")
            with _clr_col:
                if st.button("✕ Clear", use_container_width=True, key="pl_clear_batch"):
                    st.session_state.pop("pl_batch_id", None)
                    st.session_state.pop("pl_batch_map", None)
                    st.rerun()

            if check_btn:
                _bmap = st.session_state.get("pl_batch_map", {})
                batch_lines = []; batch_result = None
                batch_log_ph = st.empty()
                for item in process_batch_results(_bid, _bmap, wait_secs=30):
                    if isinstance(item, dict):
                        batch_result = item
                        break
                    batch_lines.append(item)
                    batch_log_ph.markdown(
                        '<div class="log-box">' + "\n".join(batch_lines) + "</div>",
                        unsafe_allow_html=True,
                    )
                if batch_result:
                    if batch_result.get("batch_done"):
                        ok = batch_result.get("ok", 0); fail = batch_result.get("failed", 0)
                        st.success(f"Batch complete — {ok} extracted, {fail} failed")
                        st.session_state.pop("pl_batch_id", None)
                        st.session_state.pop("pl_batch_map", None)
                        st.session_state["pl_last_result"] = batch_result
                        st.session_state["pl_data"] = get_pipeline_status()
                        st.rerun()
                    else:
                        st.info("Still processing — check again later.")

        # ── Last run summary (persists after rerun via session_state) ─────────
        _last_result = st.session_state.get("pl_last_result")

        # Credit-exhaustion banner — extraction was halted because the Anthropic
        # balance ran out. Remaining PDFs were left pending (not failed).
        if _last_result and _last_result.get("credit_exhausted"):
            st.error(
                "🛑 **Out of Anthropic credits — extraction was halted.**  \n"
                "The run stopped to avoid mass false-failures; remaining PDFs were left "
                "**pending** (not marked failed). Add credits, then re-run "
                "**All pending** (or **Re-run failed**) to resume where it stopped."
            )
            st.link_button(
                "💳 Add credits → platform.claude.com/settings/billing",
                "https://platform.claude.com/settings/billing",
                type="primary",
            )
            st.markdown("---")

        if _last_result and _last_result.get("results"):
            st.markdown("#### Run Summary")
            for r in _last_result["results"]:
                status   = r.get("status", "")
                pdf_id   = r.get("pdf_id", "")
                venue    = r.get("venue_name", pdf_id)
                s_rows   = r.get("summary_rows", 0)
                p_rows   = r.get("pricing_rows", 0)
                cost     = r.get("cost_usd", 0.0)
                err      = r.get("reason", "")
                offering = r.get("offering", "")
                category = r.get("category", "")
                attrs    = r.get("attributes", "")

                card_class = "run-card" if status == "OK" else ("run-card failed" if status == "FAILED" else "run-card partial")
                badge      = ('<span class="run-card-badge badge-green">✓ extracted</span>' if status == "OK"
                              else '<span class="run-card-badge badge-red">✗ failed</span>' if status == "FAILED"
                              else '<span class="run-card-badge badge-amber">⚠ partial</span>')

                detail_parts = []
                if s_rows:   detail_parts.append(f"{s_rows} summary row{'s' if s_rows != 1 else ''}")
                if p_rows:   detail_parts.append(f"{p_rows} pricing rows")
                if offering: detail_parts.append(offering)
                if category: detail_parts.append(category)
                if cost:     detail_parts.append(f"${cost:.4f}")
                if err:      detail_parts.append(f"<span style='color:#ef4444'>{err}</span>")

                st.markdown(f"""
                <div class="{card_class}">
                    <div class="run-card-title">{badge}{venue} <span style="font-weight:400;color:#9ca3af;font-size:12px">({pdf_id})</span></div>
                    <div class="run-card-meta">{" · ".join(detail_parts)}</div>
                    {"<div class='run-card-meta' style='margin-top:4px;color:#6b7280'>" + attrs + "</div>" if attrs else ""}
                </div>
                """, unsafe_allow_html=True)

        if _last_result and _last_result.get("ok", 0) > 0:
            with st.expander("🔍 View rows written to Xano", expanded=False):
                run_pdf_ids = {
                    str(r.get("pdf_id", "")).strip()
                    for r in _last_result.get("results", [])
                    if r.get("status") in ("OK", "PARTIAL") and r.get("pdf_id")
                }
                st.caption(f"Fetching rows for: {', '.join(sorted(run_pdf_ids))}")
                try:
                    ep_resp = requests.get(f"{XANO_BASE}/all_extracted_pdf_data", timeout=60)
                    vp_resp = requests.get(f"{XANO_BASE}/venue_pricing", timeout=60)

                    if ep_resp.status_code == 200:
                        ep_rows = ep_resp.json()
                        if isinstance(ep_rows, dict):
                            ep_rows = ep_rows.get("items") or ep_rows.get("result") or []
                        ep_rows = [
                            r for r in ep_rows
                            if str(r.get("PDF_ID") or r.get("pdf_id") or "").strip() in run_pdf_ids
                        ]
                        st.markdown(f"**extracted_pdf_data** — {len(ep_rows)} row(s) from this run")
                        if ep_rows:
                            st.dataframe(pd.DataFrame(ep_rows), use_container_width=True, hide_index=True)
                    else:
                        st.warning(f"extracted_pdf_data fetch failed ({ep_resp.status_code})")

                    if vp_resp.status_code == 200:
                        vp_rows = vp_resp.json()
                        if isinstance(vp_rows, dict):
                            vp_rows = vp_rows.get("items") or vp_rows.get("result") or []
                        vp_rows = [
                            r for r in vp_rows
                            if str(r.get("PDF_ID") or r.get("pdf_id") or "").strip() in run_pdf_ids
                        ]
                        st.markdown(f"**venue_pricing** — {len(vp_rows)} row(s) from this run")
                        if vp_rows:
                            st.dataframe(pd.DataFrame(vp_rows), use_container_width=True, hide_index=True)
                    else:
                        st.warning(f"venue_pricing fetch failed ({vp_resp.status_code})")
                except Exception as e:
                    st.warning(f"Could not fetch written rows: {e}")

        st.markdown("---")

        # ── Job History ───────────────────────────────────────────────────────
        st.markdown("#### Job History")
        st.caption("Recent extraction jobs with their row ranges, PDFs, and vendors")

        history = _get_job_history("extraction", limit=15)
        if history:
            history_rows = []
            for job in history:
                summary = job.get('result_summary') or {}
                if isinstance(summary, str):
                    try:
                        summary = json.loads(summary)
                    except (json.JSONDecodeError, TypeError):
                        summary = {}

                status = job.get('status', 'unknown')
                started = job.get('started_at', '')
                user = job.get('user_email', '')
                start_row = summary.get('start_row', '-')
                end_row = summary.get('end_row', '-')
                pdf_count = summary.get('total', 0)
                vendors = summary.get('vendor_ids', [])
                vendor_str = ', '.join(vendors[:3]) + ('...' if len(vendors) > 3 else '')

                history_rows.append({
                    'Status': status.upper(),
                    'User': user,
                    'Started': str(started)[:19],
                    'Rows': f"{start_row}–{end_row}",
                    'PDFs': pdf_count,
                    'Vendors': vendor_str,
                })

            df_hist = pd.DataFrame(history_rows)
            st.dataframe(df_hist, use_container_width=True, hide_index=True)
        else:
            st.info("No completed jobs yet")


# ── TAB 6: VENDOR SCRAPER ─────────────────────────────────────────────────────

with tab6:
    st.subheader("Vendor Scraper — Photographer Pricing")
    st.caption(
        "Politely scrapes photographer **websites** (and Reddit, once configured) → Claude extracts "
        "package pricing → Xano `photographer_pricing` with source provenance. Same Claude credit guard "
        "as PDF Extraction. Photographers come from WPTP Updated Mappings (Category = Photographer)."
    )

    # ── Display active jobs ───────────────────────────────────────────────────
    if st.session_state.get("sc_refresh_job"):
        with st.spinner("Fetching latest job status..."):
            active_scrape = _get_active_job("scrape")
        st.session_state["sc_refresh_job"] = False
    else:
        active_scrape = _get_active_job("scrape")

    if active_scrape:
        scrape_info_col, scrape_btn_col = st.columns([5, 1])
        with scrape_info_col:
            st.info(_format_job_display(active_scrape))
        with scrape_btn_col:
            st.markdown("")  # spacing
            if st.button("🔄", key="refresh_scrape_job", help="Refresh job status"):
                st.session_state["sc_refresh_job"] = True
                st.rerun()

    sc_refresh_col, _sc_sp = st.columns([2, 6])
    with sc_refresh_col:
        sc_load = st.button("🔄 Load / Refresh Status", type="primary", use_container_width=True, key="sc_refresh")

    if sc_load or st.session_state.get("sc_status_loaded"):
        if sc_load:
            st.session_state["sc_data"] = get_scrape_status()
            st.session_state["sc_status_loaded"] = True
        sc_data = st.session_state.get("sc_data") or {"rows": [], "counts": {}, "total": 0, "with_website": 0}
        counts  = sc_data.get("counts", {})

        if not sc_data.get("rows"):
            st.info(
                "No photographers loaded. Set **XANO_PHOTOGRAPHERS_ENDPOINT** (GET worklist for "
                "Category = Photographer) in Railway, then Refresh."
            )
        else:
            sc1, sc2, sc3, sc4, sc5 = st.columns(5)
            sc1.markdown(_card("card-gray",  "📷", f"{sc_data.get('total', 0):,}",       "Photographers"),  unsafe_allow_html=True)
            sc2.markdown(_card("card-amber", "⏳", f"{counts.get('pending', 0):,}",       "Pending"),        unsafe_allow_html=True)
            sc3.markdown(_card("card-green", "✅", f"{counts.get('scraped', 0):,}",       "Scraped"),        unsafe_allow_html=True)
            sc4.markdown(_card("card-red",   "✗",  f"{counts.get('failed', 0):,}",        "Failed"),         unsafe_allow_html=True)
            sc5.markdown(_card("card-gray",  "🌐", f"{sc_data.get('with_website', 0):,}", "Have a Website"), unsafe_allow_html=True)

        st.markdown("---")

        # ── Run controls ──────────────────────────────────────────────────────
        st.markdown("#### Run Scraper")
        sc_sources = st.multiselect(
            "Sources", options=["website", "reddit"], default=["website"],
            help="Reddit requires REDDIT_CLIENT_ID / SECRET / USER_AGENT; until those are set it is skipped.",
            key="sc_sources",
        )
        sc_run_mode = st.radio(
            "Run mode",
            options=["🆕 All pending", "❌ Re-run all failed", "🎯 Specific Vendor IDs", "📏 Row range"],
            horizontal=True, key="sc_run_mode",
        )
        sc_specific = ""
        sc_start_row, sc_end_row = 0, 10
        if sc_run_mode == "🎯 Specific Vendor IDs":
            sc_specific = st.text_area("Vendor IDs (one per line or comma-separated)", height=100,
                                       placeholder="VND_018\nVND_042", key="sc_specific")
        elif sc_run_mode == "📏 Row range":
            scc1, scc2 = st.columns(2)
            with scc1:
                sc_start_row = st.number_input("Start row", min_value=0, value=0, step=1, key="sc_start")
            with scc2:
                sc_end_row = st.number_input("End row (0 = all)", min_value=0, value=10, step=1, key="sc_end")

        n_pending = counts.get("pending", 0)
        n_failed  = counts.get("failed", 0)
        sc_btn_label = {
            "🆕 All pending":         f"▶ Scrape All Pending ({n_pending})",
            "❌ Re-run all failed":    f"▶ Re-scrape Failed ({n_failed})",
            "🎯 Specific Vendor IDs":  "▶ Scrape Specified Vendors",
            "📏 Row range":            "▶ Scrape Row Range",
        }.get(sc_run_mode, "▶ Run")

        if "sc_running" not in st.session_state:
            st.session_state["sc_running"] = False
        sc_run_btn = st.button(
            sc_btn_label, type="primary", use_container_width=True,
            disabled=st.session_state["sc_running"] or not sc_sources, key="sc_run_btn",
        )

        sc_log_ph  = st.empty()
        sc_stat_ph = st.empty()

        if sc_run_btn:
            sc_vendor_ids   = None
            sc_rerun_failed = False
            sc_eff_start, sc_eff_end = 0, None
            if sc_run_mode == "🎯 Specific Vendor IDs":
                raw = sc_specific.replace(",", "\n")
                sc_vendor_ids = [v.strip() for v in raw.splitlines() if v.strip()]
                if not sc_vendor_ids:
                    st.warning("Enter at least one Vendor ID.")
                    st.stop()
            elif sc_run_mode == "❌ Re-run all failed":
                sc_rerun_failed = True
            elif sc_run_mode == "📏 Row range":
                sc_eff_start = int(sc_start_row)
                sc_eff_end   = None if int(sc_end_row) == 0 else int(sc_end_row)

            st.session_state["sc_running"] = True
            # Track job persistently (survives logouts)
            _post_job_status("scrape", "running", st.session_state.get("user_email", "unknown"))
            sc_lines, sc_result = [], None
            for item in run_scrape(
                start_row=sc_eff_start, end_row=sc_eff_end,
                vendor_ids=sc_vendor_ids, rerun_failed=sc_rerun_failed,
                sources=tuple(sc_sources),
            ):
                if isinstance(item, dict):
                    sc_result = item
                    break
                sc_lines.append(item)
                sc_log_ph.markdown('<div class="log-box">' + "\n".join(sc_lines) + "</div>", unsafe_allow_html=True)
            st.session_state["sc_running"] = False
            # Track job completion persistently
            _post_job_status("scrape", "completed", st.session_state.get("user_email", "unknown"),
                             result_summary=sc_result)

            if sc_result:
                ok   = sc_result["ok"]
                part = sc_result["partial"]
                fail = sc_result["failed"]
                cost = sc_result.get("cost_usd", 0.0)
                if sc_result.get("credit_exhausted"):
                    st.toast("🛑 Anthropic credits exhausted — scraping halted.", icon="🛑")
                    sc_stat_ph.error(f"🛑 Halted — ran out of Anthropic credits after {ok} scraped · ${cost:.4f}")
                elif fail:
                    sc_stat_ph.error(f"Done — {ok} scraped, {part} partial, {fail} failed · ${cost:.4f}")
                elif part:
                    sc_stat_ph.warning(f"Done — {ok} scraped, {part} partial · ${cost:.4f}")
                else:
                    sc_stat_ph.success(f"Done — {ok} scraped · ${cost:.4f}")
                st.session_state["sc_last_result"]   = sc_result
                st.session_state["sc_status_loaded"] = True
                st.session_state["sc_data"]          = get_scrape_status()
                st.rerun()

        # ── Persistent last-run output (survives the auto-refresh rerun) ──────
        _sc_last = st.session_state.get("sc_last_result")
        if _sc_last and _sc_last.get("credit_exhausted"):
            st.error(
                "🛑 **Out of Anthropic credits — scraping was halted.**  \n"
                "Remaining photographers were left **pending** (not failed). Add credits, then re-run."
            )
            st.link_button(
                "💳 Add credits → platform.claude.com/settings/billing",
                "https://platform.claude.com/settings/billing", type="primary",
            )
            st.markdown("---")

        if _sc_last and _sc_last.get("results"):
            st.markdown("#### Run Summary")
            for r in _sc_last["results"]:
                status   = r.get("status", "")
                vid      = r.get("pdf_id", "")
                vname    = r.get("venue_name", vid)
                posted   = r.get("summary_rows", 0)
                cost     = r.get("cost_usd", 0.0)
                offering = r.get("offering", "")
                reason   = r.get("reason", "")
                card_class = "run-card" if status == "OK" else ("run-card failed" if status == "FAILED" else "run-card partial")
                badge = ('<span class="run-card-badge badge-green">✓ scraped</span>' if status == "OK"
                         else '<span class="run-card-badge badge-red">✗ failed</span>' if status == "FAILED"
                         else f'<span class="run-card-badge badge-amber">⚠ {status.lower()}</span>')
                parts = []
                if posted:   parts.append(f"{posted} observation{'s' if posted != 1 else ''}")
                if offering: parts.append(offering)
                if cost:     parts.append(f"${cost:.4f}")
                if reason:   parts.append(f"<span style='color:#ef4444'>{reason}</span>")
                st.markdown(f"""
                <div class="{card_class}">
                    <div class="run-card-title">{badge}{vname} <span style="font-weight:400;color:#9ca3af;font-size:12px">({vid})</span></div>
                    <div class="run-card-meta">{" · ".join(parts)}</div>
                </div>
                """, unsafe_allow_html=True)
