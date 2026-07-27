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
import time
import hmac
import hashlib
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
from anthropic import Anthropic
from extract_core import (run_extraction, get_pipeline_status,
                          run_extraction_batch, process_batch_results,
                          list_recent_batches, ingest_batch_by_id, validate_merge)
from cohorts import render_cohorts_tab


# ── JOB STATUS TRACKING (persistent across logouts) ──────────────────────────

def _xano_get_with_retry(url: str, timeout: int = 8, attempts: int = 3):
    """GET a Xano URL, retrying transient 5xx / network errors with short backoff so
    a momentary Xano 502/503 doesn't blank the UI (job history, active job). Kept
    deliberately light — these run on every render, so a SUSTAINED outage must fail
    fast rather than hang the whole page. Returns parsed JSON or None. 4xx not retried."""
    for i in range(attempts):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                return None  # 4xx won't self-heal
        except Exception:
            pass
        if i < attempts - 1:
            time.sleep(1.0 * (i + 1))   # 1s, 2s — ~3s worst case per read
    return None

def _post_job_status(job_type: str, status: str, user_email: str,
                     result_summary: dict = None, batch_id: str = None) -> bool:
    """
    Post job status to Xano for persistence across logouts.
    Returns True if successful, False otherwise.
    """
    job_endpoint = os.environ.get("XANO_JOB_STATUS_ENDPOINT", "")
    if not job_endpoint:
        return False
    payload = {
        "job_type": job_type,
        "status": status,
        "user_email": user_email,
        "result_summary": result_summary,
        "batch_id": batch_id,
    }
    # Retry on transient 5xx / network errors. This write is load-bearing for
    # batches: it persists the batch_id + pdf_map, and if it's lost the dashboard
    # can't map results back (the batch is orphaned). A Xano 503 blip must not
    # orphan a batch, so retry with backoff. (Upsert endpoint, so a duplicate from
    # a lost-response retry is harmless — resume dedups by batch_id.)
    for i in range(4):
        try:
            r = requests.post(job_endpoint, json=payload, timeout=10)
            if r.status_code == 200:
                try:
                    _get_active_job.clear()   # job state changed — drop the 15s cache
                except Exception:
                    pass
                return True
            if r.status_code < 500:
                return False  # 4xx won't self-heal
        except Exception:
            pass
        if i < 3:
            time.sleep(min(8, 1.5 * (i + 1)))
    return False


@st.cache_data(ttl=15, show_spinner=False)
def _get_active_job(job_type: str):
    """
    Get the currently active job of a given type from Xano.
    Returns job dict or None if no active job.

    Cached 15s: Streamlit re-runs the whole script on every widget interaction,
    and these calls fire on every render — uncached they hit Xano (with retry
    sleeps) on each click anywhere in the app. The 🔄 refresh buttons call
    _get_active_job.clear() for an immediate re-read.
    """
    jobs_endpoint = os.environ.get("XANO_JOBS_ENDPOINT", "")
    if not jobs_endpoint:
        return None
    data = _xano_get_with_retry(f"{jobs_endpoint}?job_type={job_type}&is_active=true")
    if isinstance(data, list) and len(data) > 0:
        return data[0]
    return None


@st.cache_data(ttl=120, show_spinner=False)
def _explorer_fetch(url: str):
    """Data Explorer table load, cached 2 min per URL so repeated Load Data clicks
    on the same table don't each trigger another full-table scan on Xano (these
    endpoints are unpaginated). Save Changes clears this cache. Returns (rows, err)."""
    r = requests.get(url, timeout=120)
    if r.status_code != 200:
        return [], f"Xano returned {r.status_code}: {r.text[:200]}"
    raw = r.json()
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("data") or raw.get("result") or []
    return (raw if isinstance(raw, list) else []), ""


def _get_job_history(job_type: str, limit: int = 20):
    """
    Get completed jobs of a given type from Xano.
    Returns list of job dicts, newest first.
    """
    jobs_endpoint = os.environ.get("XANO_JOBS_ENDPOINT", "")
    if not jobs_endpoint:
        return []
    data = _xano_get_with_retry(
        f"{jobs_endpoint}?job_type={job_type}&is_active=false&limit={limit}")
    return data if isinstance(data, list) else []


def _get_resumable_batch(skip=None):
    """Find the most recent submitted batch job in Xano whose pdf_map is still
    available, so batch results can be checked after a logout / page refresh.
    `skip` is a set of batch_ids already handled this session — they're passed
    over so the user can walk through several submitted batches one after another
    (newest first). Returns (batch_id, pdf_map, job) or (None, None, None)."""
    skip = skip or set()
    for job in _get_job_history("extraction", limit=40):
        summary = job.get("result_summary") or {}
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except (json.JSONDecodeError, TypeError):
                summary = {}
        bid = summary.get("batch_id") or job.get("batch_id")
        pmap = summary.get("pdf_map")
        if bid and bid not in skip and isinstance(pmap, dict) and pmap:
            return bid, pmap, job
    return None, None, None


@st.cache_data(ttl=120)
def _get_google_coverage():
    """Coverage counts for Google data + images across WPTP Updated Mappings.
    Returns the coverage dict from admin/google/coverage, or None on failure."""
    try:
        r = requests.get(f"{XANO_BASE}/admin/google/coverage", timeout=90)
        if r.status_code == 200 and isinstance(r.json(), dict):
            return r.json()
    except Exception:
        return None
    return None


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


# A genuinely-running extraction job heartbeats updated_at every ~30s
# (extract_core._maybe_post_progress). If we haven't heard from it in this long,
# the process is dead (crash / Railway redeploy / user closed tab) and we should
# stop showing it as "active" rather than leaving a permanent stuck card.
STALE_AFTER_SEC = 600  # 10 minutes


def _fmt_ts(ts_value) -> str:
    """Render a stored timestamp as a readable 'YYYY-MM-DD HH:MM UTC' string."""
    ts = _parse_timestamp(ts_value)
    if ts is None:
        return "unknown"
    try:
        return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "unknown"


def _job_age_seconds(job: dict):
    """Seconds since the job last posted progress (updated_at), or None if unknown."""
    if not job:
        return None
    updated_ts = _parse_timestamp(job.get("updated_at"))
    if updated_ts is None:
        return None
    return datetime.datetime.now(datetime.timezone.utc).timestamp() - updated_ts


def _is_job_stale(job: dict) -> bool:
    """True if a 'running' job has gone silent past STALE_AFTER_SEC (process is dead)."""
    if not job or str(job.get("status", "")).lower() != "running":
        return False
    age = _job_age_seconds(job)
    return age is not None and age > STALE_AFTER_SEC


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
        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()

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

    return f"🔄 **Active Job**  \nStarted: {_fmt_ts(started)}  \nUser: {user}  \nStatus: {status}{elapsed}  \nLast updated: {_fmt_ts(updated)}{progress}{stuck_warning}"


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

# ── Persistent login cookie ───────────────────────────────────────────────────
# st.session_state (where the login lives) is wiped on every server restart
# (each redeploy) and on WebSocket reconnects — that's what was signing people
# out "randomly". We mirror the login into a signed browser cookie and restore
# from it on load, so a session reset re-authenticates silently. All cookie ops
# are best-effort: a failure degrades to the old session-only behavior, never a
# lockout. The signing key persists across restarts (env SESSION_SECRET, falling
# back to the always-set GOOGLE_CLIENT_SECRET).
_SESSION_SECRET   = os.environ.get("SESSION_SECRET") or _GOOGLE_CLIENT_SECRET or "tulle-dev-secret"
_SESSION_TTL_DAYS = 30


def _sign_session(email: str, name: str, picture: str) -> str:
    payload = {"email": email, "name": name, "pic": picture,
               "exp": int(time.time()) + _SESSION_TTL_DAYS * 86400}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(_SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _verify_session(token: str):
    """Return the payload dict if the token is well-formed, unexpired, and
    correctly signed; else None."""
    try:
        raw, sig = token.split(".", 1)
        expect = hmac.new(_SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


# Cookie manager (best-effort — None if the component can't load).
_cookies = None
if _USE_GOOGLE_AUTH:
    try:
        import extra_streamlit_components as stx
        _cookies = stx.CookieManager(key="tulle_auth_cookies")
    except Exception:
        _cookies = None


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

# ── Silent re-auth from the persistent cookie (survives redeploys/reconnects) ──
if _USE_GOOGLE_AUTH and not st.session_state.authenticated and _cookies is not None:
    try:
        _tok = _cookies.get("tulle_auth")
    except Exception:
        _tok = None
    if _tok:
        _p = _verify_session(_tok)
        if _p and (not _ALLOWED_EMAILS or _p.get("email") in _ALLOWED_EMAILS):
            st.session_state.authenticated = True
            st.session_state.user_email    = _p.get("email", "")
            st.session_state.user_name     = _p.get("name", _p.get("email", ""))
            st.session_state.user_picture  = _p.get("pic", "")

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
            # Same-tab sign-in: st.link_button always renders target="_blank", which
            # sends the Google OAuth flow to a NEW tab and strands the user on the login
            # screen in the original tab. A plain anchor with target="_self" navigates the
            # current tab, so the redirect back to the app lands where the user started.
            st.markdown(
                f"""
                <style>
                .tulle-gsignin {{
                    display:block; width:100%; box-sizing:border-box;
                    text-align:center; text-decoration:none;
                    background:#1B7A4A; color:#fff !important; font-weight:500;
                    padding:0.55rem 1rem; border-radius:7px;
                    font-family:'Source Sans Pro', sans-serif; font-size:1rem;
                    transition:background .15s;
                }}
                .tulle-gsignin:hover {{ background:#155f39; }}
                </style>
                <a class="tulle-gsignin" href="{auth_url}" target="_self">Sign in with Google</a>
                """,
                unsafe_allow_html=True,
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

# Past the gate → authenticated. Persist (or refresh) the login cookie once per
# session so a later server restart / reconnect re-auths silently. Self-heals a
# dropped set: runs on the OAuth-success rerun and on cookie-restored sessions.
if _USE_GOOGLE_AUTH and _cookies is not None and not st.session_state.get("_auth_cookie_set"):
    try:
        _cookies.set(
            "tulle_auth",
            _sign_session(st.session_state.get("user_email", ""),
                          st.session_state.get("user_name", ""),
                          st.session_state.get("user_picture", "")),
            expires_at=datetime.datetime.now() + datetime.timedelta(days=_SESSION_TTL_DAYS),
            key="tulle_auth_set",
        )
        st.session_state["_auth_cookie_set"] = True
    except Exception:
        pass


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
        for k in ["authenticated", "user_email", "user_name", "user_picture", "_auth_cookie_set"]:
            st.session_state.pop(k, None)
        if _cookies is not None:
            try:
                _cookies.delete("tulle_auth", key="tulle_auth_del")
            except Exception:
                pass
        st.rerun()

st.markdown('<hr class="tulle-rule" />', unsafe_allow_html=True)

XANO_BASE = os.environ.get("XANO_BASE_URL", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B")


# ── TABS ──────────────────────────────────────────────────────────────────────

tab0, tab2, tab5, tab_vp, tab_co = st.tabs([
    "📊 Admin", "🔍 Google Data & Images", "📄 PDF Extraction",
    "💰 Venue Pricing", "📈 Cohorts"
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

                # Sequential on purpose: these are 4 full-table reads, and firing
                # them concurrently spikes Xano's small worker pool. Serial is a
                # few seconds slower but keeps the load to one query at a time.
                users_data,    users_sc    = _fetch_all(f"{XANO_WGW}/user")
                todos_data,    todos_sc    = _fetch_all(f"{XANO_WGW}/to_do_items")
                packages_data, packages_sc = _fetch_all(f"{XANO_WGW}/packages")
                payments_data, payments_sc = _fetch_all(f"{XANO_WGW}/donation_payment_log")

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
            _explorer_fetch.clear()   # Clear + Load Data = guaranteed fresh read
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    if load_data:
        with st.spinner(f"Loading {exp_table}..."):
            try:
                raw, err = _explorer_fetch(exp_cfg["url"])
                if err:
                    st.error(err)
                else:
                    st.session_state["exp_raw"]          = raw
                    st.session_state["exp_loaded_table"] = exp_table
                    st.session_state["exp_filters"]      = []
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

                    # max_workers=3 (was 10): bulk edits shouldn't burst-write Xano.
                    with st.spinner(f"Saving {len(changes)} row(s)..."):
                        with ThreadPoolExecutor(max_workers=3) as pool:
                            futures = [pool.submit(_do_patch, rid, payload) for rid, payload in changes]
                            results = [f.result() for f in as_completed(futures)]

                    saved  = sum(1 for ok, _ in results if ok)
                    failed = len(results) - saved
                    _explorer_fetch.clear()   # table changed — next Load Data re-reads

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
        _get_active_job.clear()
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

    # ── Coverage — what still needs Google data / images ──────────────────────
    st.markdown("### 📊 Coverage")
    st.caption(
        "How many vendors in WPTP Updated Mappings already have Google data and images, "
        "and exactly which rows still need a run (so you know where to point the batches below)."
    )
    if st.button("📊 Load / Refresh coverage", key="gg_load_coverage"):
        _get_google_coverage.clear()
        st.session_state["gg_cov_loaded"] = True

    if st.session_state.get("gg_cov_loaded"):
        with st.spinner("Counting coverage in Xano..."):
            cov = _get_google_coverage()
        if not cov:
            st.warning("Couldn't load coverage — try again.")
        else:
            total   = cov.get("total", 0) or 0
            g_done  = cov.get("google_done", 0) or 0      # real data (cache.name set)
            g_never = cov.get("google_never", 0) or 0     # never run (cache null)
            g_empty = cov.get("google_empty", 0) or 0     # ran but pulled nothing
            i1 = cov.get("image_1_done", 0) or 0
            i2 = cov.get("image_2_done", 0) or 0
            i3 = cov.get("image_3_done", 0) or 0
            i_rem = cov.get("image_remaining", 0) or 0
            g_pct = int(g_done / total * 100) if total else 0
            i_pct = int(i1 / total * 100) if total else 0

            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.markdown(_card("card-gray", "🏷️", f"{total:,}", "Total vendors"),
                         unsafe_allow_html=True)
            cc2.markdown(_card("card-green", "✅",
                               f"{g_done:,}", f"Google data — real · {g_pct}%"),
                         unsafe_allow_html=True)
            cc3.markdown(_card("card-gray" if g_never == 0 else "card-red", "🔍",
                               f"{g_never:,}", "Never run (no cache)"),
                         unsafe_allow_html=True)
            cc4.markdown(_card("card-gray" if g_empty == 0 else "card-amber", "⚠️",
                               f"{g_empty:,}", "Ran but no data pulled"),
                         unsafe_allow_html=True)
            st.caption(
                f"**Ran but no data pulled** = a cache exists but it's an empty shell "
                f"(Google returned nothing usable). The current batch **skips** these "
                f"(it only fetches rows with no cache), so they need a re-pull to retry.  \n"
                f"Image slots populated — slot 1: **{i1:,}** ({i_pct}%) · "
                f"slot 2: **{i2:,}** · slot 3: **{i3:,}**"
            )

            g_never_sample = cov.get("google_never_sample") or []
            if g_never_sample:
                st.info(f"▶ **Google Data (never run)** — {g_never:,} vendors with no cache. "
                        f"Next un-cached vendor ID: **{g_never_sample[0].get('id')}**.")
                with st.expander(f"Never-run vendors — showing {min(len(g_never_sample), 50)} of {g_never:,}"):
                    st.dataframe(pd.DataFrame(g_never_sample[:50]), use_container_width=True, hide_index=True)
            else:
                st.success("✅ Every vendor has been run at least once.")

            g_empty_sample = cov.get("google_empty_sample") or []
            if g_empty_sample:
                st.warning(f"⚠️ **Ran but empty** — {g_empty:,} vendors have a cache with no real data. "
                           f"These won't be retried by the normal batch.")
                with st.expander(f"Empty-cache vendors — showing {min(len(g_empty_sample), 50)} of {g_empty:,}"):
                    st.dataframe(pd.DataFrame(g_empty_sample[:50]), use_container_width=True, hide_index=True)

            i_sample = cov.get("image_sample") or []
            if i_sample:
                st.info(f"▶ **Vendor Images** — {i_rem:,} vendors have real Google data but no image 1. "
                        f"Next vendor ID needing images: **{i_sample[0].get('id')}**.")
                with st.expander(f"Vendors missing images — showing {min(len(i_sample), 50)} of {i_rem:,}"):
                    st.dataframe(pd.DataFrame(i_sample[:50]), use_container_width=True, hide_index=True)
            else:
                st.success("✅ Every vendor with real Google data has at least image 1.")

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
        _get_active_job.clear()
        with st.spinner("Fetching latest job status..."):
            active_job = _get_active_job("extraction")
        st.session_state["pl_refresh_job"] = False
    else:
        active_job = _get_active_job("extraction")

    # A running job that hasn't heartbeated in STALE_AFTER_SEC is dead, not active —
    # don't show it as a live job (this is what auto-clears stuck cards).
    if active_job and _is_job_stale(active_job):
        active_job = None
        st.caption("No active extraction job (last run stalled or ended).")

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
                            _get_active_job.clear()
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

        # Normalise status: only a truly blank/unknown status → pending. Every known
        # status (incl. batch_submitted and skipped_non_venue) must display as itself,
        # otherwise a submitted/skipped row wrongly shows as 'pending'.
        _KNOWN_STATUSES = ('extracted', 'partial', 'failed', 'skipped',
                           'skipped_non_venue', 'batch_submitted')
        if 'extraction_status' in df_display.columns:
            df_display['extraction_status'] = df_display['extraction_status'].apply(
                lambda x: str(x).strip().lower()
                if str(x).strip().lower() in _KNOWN_STATUSES else 'pending'
            )

        # Filter controls
        filter_status = st.multiselect(
            "Filter by status",
            options=['pending', 'batch_submitted', 'extracted', 'partial',
                     'failed', 'skipped', 'skipped_non_venue'],
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

        # Merged mode — 1 Claude call per PDF (summary+pricing+classification in one
        # response) instead of 3, so the PDF is sent once. ~half the input cost.
        merged_mode = st.toggle(
            "💸 Merged mode — 1 call/PDF (~50% cheaper input)",
            value=st.session_state.get("pl_merged_mode", False),
            key="pl_merged_mode",
            help=("Sends each PDF to Claude ONCE (combined summary+pricing+classification) "
                  "instead of 3 times. Validate it first in the 🧪 expander below before "
                  "trusting it on a big run. Applies to Batch mode submissions."),
        )
        if merged_mode and not batch_mode:
            st.caption("⚠ Merged mode currently applies to **Batch** submissions — turn on Batch mode too.")

        with st.expander("🧪 Validate merged mode (run before trusting it)", expanded=False):
            st.caption(
                "Runs a few PDFs through BOTH the 3-pass and merged paths and diffs the "
                "output, so you can confirm the cheaper merged mode matches quality before "
                "using it on a big run. Costs a few PDFs' worth of tokens. Comma-separate IDs."
            )
            _val_ids = st.text_input("PDF IDs to validate", key="pl_val_ids",
                                     placeholder="P6786, P6788, P6791")
            if st.button("▶ Run validation", key="pl_val_btn") and _val_ids.strip():
                _ids = [s.strip() for s in _val_ids.replace("\n", ",").split(",") if s.strip()]
                v_lines = []; v_result = None
                v_ph = st.empty()
                for item in validate_merge(_ids):
                    if isinstance(item, dict):
                        v_result = item
                        break
                    v_lines.append(item)
                    v_ph.markdown('<div class="log-box">' + "\n".join(v_lines) + "</div>",
                                  unsafe_allow_html=True)
                if v_result is not None:
                    if v_result.get("recommend"):
                        st.success("✅ Merged matches the 3-pass output — safe to turn on Merged mode.")
                    else:
                        st.warning("⚠ Differences found — review the log above before enabling Merged mode.")

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
            st.caption("Selects by the **`id`** column shown in the PDF Status Table "
                       "(inclusive). e.g. 6785–6795 = rows with id 6785 through 6795.")
            rc1, rc2 = st.columns(2)
            with rc1:
                pl_start_row = st.number_input("Start id", min_value=0, value=0, step=1, key="pl_start")
            with rc2:
                pl_end_row = st.number_input("End id (0 = no upper limit)", min_value=0, value=10, step=1, key="pl_end")

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
            _run_user = st.session_state.get("user_email", "unknown")
            # Let extract_core attribute its own job posts (immediate batch_id
            # capture + live heartbeats) to the real user, not "extraction-batch".
            os.environ["LOGGED_IN_USER"] = _run_user
            # Track job persistently (survives logouts)
            _post_job_status("extraction", "running", _run_user)
            pl_lines = []; pl_result = None
            run_started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

            if batch_mode:
                # ── Batch path ────────────────────────────────────────────────
                for item in run_extraction_batch(
                    start_row=eff_start, end_row=eff_end,
                    pdf_ids=pdf_ids_list, rerun_failed=rerun_failed,
                    merged=merged_mode,
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
                if pl_result and pl_result.get("batch_submitted"):
                    # batch_id + pdf_map were already persisted to Xano by
                    # run_extraction_batch the instant Anthropic accepted the batch
                    # (closes the lost-id window), so we don't double-post here —
                    # a second "completed" post would create a duplicate job row.
                    bid = pl_result["batch_id"]
                    st.session_state["pl_batch_id"]   = bid
                    st.session_state["pl_batch_map"]  = pl_result["pdf_map"]
                    pl_stat_ph.success(
                        f"Batch submitted ({pl_result['pdf_count']} PDFs) · ID: `{bid}`  \n"
                        "Processing takes up to 24 hrs — use **Check Batch Results** below."
                    )
                else:
                    # Submission failed / returned nothing — close the running row as failed.
                    _post_job_status("extraction", "failed",
                                     st.session_state.get("user_email", "unknown"),
                                     result_summary=pl_result)
                    pl_stat_ph.error(
                        f"Batch submission failed: {pl_result.get('error', '?') if pl_result else 'unknown'}")
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

        # ── Live batch status from Anthropic (source of truth) ────────────────
        # Lists ALL recent Message Batches on Anthropic's side — including any
        # whose batch_id we lost — with live processing_status + request counts.
        with st.expander("📡 Anthropic batch jobs (live status)", expanded=False):
            st.caption(
                "Pulled directly from Anthropic's Batch API — what's actually "
                "queued / processing / done, independent of our Xano tracking. "
                "Batches finish within 24h; results are retrievable for 29 days."
            )
            if st.button("🔄 Load batches from Anthropic", key="pl_load_batches"):
                try:
                    with st.spinner("Querying Anthropic batches…"):
                        st.session_state["pl_anthropic_batches"] = list_recent_batches(limit=20)
                except Exception as e:
                    st.error(f"Couldn't reach Anthropic Batch API: {e}")

            _batches = st.session_state.get("pl_anthropic_batches")
            if _batches is not None:
                if not _batches:
                    st.info("No batches found on this Anthropic account.")
                else:
                    _status_icon = {"in_progress": "⏳ processing", "ended": "✅ ended",
                                    "canceling": "🛑 canceling"}
                    n_proc = sum(1 for b in _batches if b["status"] in ("in_progress", "canceling"))
                    n_done = sum(1 for b in _batches if b["status"] == "ended")
                    if n_proc:
                        st.success(f"⏳ **{n_proc} batch(es) queued/processing on Anthropic** · "
                                   f"✅ {n_done} ended. Processing batches finish within 24h.")
                    else:
                        st.warning(f"⏳ **0 processing** · ✅ {n_done} ended — **nothing is currently "
                                   f"queued or running on Anthropic.** A batch you submitted won't "
                                   f"appear here until it's been accepted by Anthropic (after all its "
                                   f"PDFs finish downloading). If it's missing, the submission didn't "
                                   f"complete — re-submit and watch for the “Batch submitted · ID” log line.")
                    # Join each Anthropic batch to what WE submitted (row range / PDF
                    # count), looked up by batch_id from our own job history — so it's
                    # obvious which batch corresponds to which run.
                    _submitted_by_bid = {}
                    for _job in _get_job_history("extraction", limit=40):
                        _s = _job.get("result_summary") or {}
                        if isinstance(_s, str):
                            try: _s = json.loads(_s)
                            except (json.JSONDecodeError, TypeError): _s = {}
                        _bid = _s.get("batch_id") or _job.get("batch_id")
                        if _bid and _bid not in _submitted_by_bid:
                            _submitted_by_bid[_bid] = _s.get("submitted_as") or (
                                f"{_s.get('pdf_count')} PDFs" if _s.get("pdf_count") else "")
                    rows = []
                    for b in _batches:
                        done = b["succeeded"] + b["errored"] + b["canceled"] + b["expired"]
                        total = done + b["processing"]
                        rows.append({
                            "Batch ID":  b["id"],
                            "Submitted": _submitted_by_bid.get(b["id"], "—"),
                            "PDFs":      b.get("pdfs", "") or "",
                            "Status":    _status_icon.get(b["status"], b["status"]),
                            "Progress":  f"{done}/{total}" if total else "—",
                            "✓ ok":      b["succeeded"],
                            "✗ err":     b["errored"],
                            "Created":   b["created_at"],
                            "Ended":     b["ended_at"],
                            "Expires":   b["expires_at"],
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    st.caption(
                        "An `ended` batch we have a saved PDF→vendor map for can be ingested "
                        "via **Resume this batch** / **Check Batch Results** below. An ended "
                        "batch with no saved map (ID lost) shows here but must be re-run to "
                        "ingest, since results can't be mapped back to vendors."
                    )

        # ── Resume a batch from Xano (survives logout / refresh / redeploy) ───
        # The session loses pl_batch_id/pl_batch_map on logout, but the batch_id
        # and pdf_map are persisted on the job row — reload them to keep checking.
        if not st.session_state.get("pl_batch_id"):
            # Walk through every submitted batch (newest first), skipping ones
            # already checked this session — so multiple batches can be ingested
            # one after another, not just the latest.
            _r_bid, _r_map, _r_job = _get_resumable_batch(
                skip=st.session_state.get("pl_checked_batches", set()))
            if _r_bid:
                _r_when = _fmt_ts(_r_job.get("started_at")) if _r_job else ""
                st.warning(
                    f"📦 Submitted batch to check — `{_r_bid}` "
                    f"({len(_r_map)} PDFs, submitted {_r_when}). "
                    "Load it to check/ingest results."
                )
                if st.button("↩️ Resume this batch", key="pl_resume_batch"):
                    st.session_state["pl_batch_id"]  = _r_bid
                    st.session_state["pl_batch_map"] = _r_map
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
                        # Remember this batch is done so the resume prompt won't
                        # re-offer it (re-checking would re-write its rows).
                        st.session_state.setdefault("pl_checked_batches", set()).add(_bid)
                        st.session_state.pop("pl_batch_id", None)
                        st.session_state.pop("pl_batch_map", None)
                        st.session_state["pl_last_result"] = batch_result
                        st.session_state["pl_data"] = get_pipeline_status()
                        st.rerun()
                    else:
                        st.info("Still processing — check again later.")

        # ── Recovery: ingest any ended batch by its ID ────────────────────────
        # No saved job record needed — reconstructs the PDF→vendor map from the
        # batch's own results. Use this when a batch shows ENDED in the Anthropic
        # panel above but never got a Resume / Check Batch Results button (e.g. the
        # job record was lost to a Xano outage at submit time).
        with st.expander("🔧 Ingest a batch by ID (recovery)", expanded=False):
            st.caption(
                "Paste a batch ID from the **Anthropic batch jobs** panel above. "
                "Works even if the batch never appeared in Job History — it rebuilds "
                "the vendor map from the batch results and posts them to Xano."
            )
            _rec_id = st.text_input("Batch ID", key="pl_recover_id",
                                    placeholder="msgbatch_…")
            if st.button("⤓ Ingest this batch ID", key="pl_recover_btn") and _rec_id.strip():
                rec_lines = []; rec_result = None
                rec_ph = st.empty()
                for item in ingest_batch_by_id(_rec_id.strip(), wait_secs=0):
                    if isinstance(item, dict):
                        rec_result = item
                        break
                    rec_lines.append(item)
                    rec_ph.markdown('<div class="log-box">' + "\n".join(rec_lines) + "</div>",
                                    unsafe_allow_html=True)
                if rec_result and rec_result.get("batch_done"):
                    ok = rec_result.get("ok", 0); fail = rec_result.get("failed", 0)
                    st.success(f"Ingested — {ok} extracted, {fail} failed. "
                               "Refresh status to see the rows update.")
                    st.session_state["pl_data"] = get_pipeline_status()
                elif rec_result:
                    st.warning(f"Could not ingest: {rec_result.get('error', 'still processing or no mapping')}")

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
                    # Server-side PDF_ID filter (ep 199 raw sections) — fetches only this
                    # run's rows instead of dumping both full tables. ts busts the
                    # endpoint's response cache so rows written seconds ago show up.
                    _q = "&".join(f"pdf_ids[]={pid}" for pid in sorted(run_pdf_ids))
                    _ts = int(time.time())
                    ep_resp = requests.get(
                        f"{XANO_BASE}/venue_pricing_dashboard?section=raw_extracted&{_q}&ts={_ts}",
                        timeout=60)
                    vp_resp = requests.get(
                        f"{XANO_BASE}/venue_pricing_dashboard?section=raw_pricing&{_q}&ts={_ts}",
                        timeout=60)

                    if ep_resp.status_code == 200:
                        ep_rows = ep_resp.json().get("items") or []
                        st.markdown(f"**extracted_pdf_data** — {len(ep_rows)} row(s) from this run")
                        if ep_rows:
                            st.dataframe(pd.DataFrame(ep_rows), use_container_width=True, hide_index=True)
                    else:
                        st.warning(f"extracted_pdf_data fetch failed ({ep_resp.status_code})")

                    if vp_resp.status_code == 200:
                        vp_rows = vp_resp.json().get("items") or []
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
                # Batch jobs store pdf_count; live jobs store total — accept either.
                pdf_count = summary.get('pdf_count') or summary.get('total', 0)

                # The ACTUAL ids that ran (from pdf_map), so you can confirm a batch hit
                # the rows you intended — not just the requested range. Shown as span+count.
                pdf_map = summary.get('pdf_map') or {}
                actual_ids = sorted(
                    int(m['xano_id']) for m in pdf_map.values()
                    if isinstance(m, dict) and str(m.get('xano_id') or '').strip().isdigit()
                )
                if actual_ids:
                    ran_ids = (f"{actual_ids[0]}–{actual_ids[-1]} ({len(actual_ids)})"
                               if len(actual_ids) > 1 else f"{actual_ids[0]}")
                else:
                    ran_ids = '-'

                history_rows.append({
                    'Status': status.upper(),
                    'User': user,
                    'Started': _fmt_ts(started),
                    'Requested ids': f"{start_row}–{end_row}",
                    'Ran ids': ran_ids,
                    'PDFs': pdf_count,
                })

            df_hist = pd.DataFrame(history_rows)
            st.dataframe(df_hist, use_container_width=True, hide_index=True)
        else:
            st.info("No completed jobs yet")


# ══ VENUE PRICING TAB ══════════════════════════════════════════════════════════
# Approximates an all-in venue cost for a chosen guest count from raw Xano pricing
# (table 36 all_extracted_pdf_data) joined to the vendor master (table 11
# wptp_updated_mappings), then aggregates across a filtered grid. Venues only.
# No tax field exists in Xano, so tax is not included.

def _vp_num(x):
    """Coerce None / '' / '$1,200' / decimal-strings to float (default 0.0)."""
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _vp_ts(v):
    """last_extracted_at -> comparable number (epoch int/ms or ISO string; 0 if missing)."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _vp_fetch_paged(section, breaker=None, per_page=5000):
    """All rows from /venue_pricing_dashboard (Xano ep 199) — slim columns, venue-joined
    server-side, paginated. Replaces the old unpaginated full-table dumps that could
    hold a Xano worker for up to 120s and take the instance down. Each page is a small
    (~1MB) request. One quick retry per page on a transient 503. Returns (rows, error_str).

    `breaker` is a shared dict for one _vp_load() call: once Xano is seen to be
    fully down (503 after a retry), it trips and every later fetch in the same
    load returns immediately — so a down instance fails fast instead of
    sleep-retrying across every page."""
    rows, page = [], 1
    while True:
        if breaker is not None and breaker.get("down"):
            return rows, "503"
        url = (f"{XANO_BASE}/venue_pricing_dashboard"
               f"?section={section}&page={page}&per_page={per_page}")
        data, last = None, ""
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=60)
            except Exception as e:
                last = str(e)
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                data = r.json()
                break
            last = str(r.status_code)
            # Xano throws 502 (not just 503) under load — retry all gateway errors.
            if r.status_code in (502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break                        # other errors (4xx) won't fix on retry
        if data is None:
            if last in ("502", "503", "504") and breaker is not None:
                breaker["down"] = True   # Xano is down — short-circuit the rest of this load
            return rows, last
        if not isinstance(data, dict):
            return rows, ""
        items = data.get("items") or []
        rows.extend(items)
        next_page = data.get("nextPage")
        if not next_page or not items:
            return rows, ""
        page = next_page


def _vp_dedup_latest(rows):
    """Table 36 appends a new row per extraction; keep the latest per PDF_ID."""
    best, passthrough = {}, []
    for r in rows:
        pid = r.get("PDF_ID")
        if not pid:
            passthrough.append(r)
            continue
        prev = best.get(pid)
        if prev is None or _vp_ts(r.get("last_extracted_at")) >= _vp_ts(prev.get("last_extracted_at")):
            best[pid] = r
    return list(best.values()) + passthrough


def _vp_join(t36_dedup):
    """Shape deduped pricing rows for the tab. The venue join (name/state via
    WPTP Updated Mappings, Category == Venue) already happened server-side in
    Xano ep 199, so this is just a per-row field mapping."""
    out = []
    for r in t36_dedup:
        vid = str(r.get("VENDOR_ID") or "").strip()
        if not vid:
            continue
        out.append({
            "vendor_id":  vid,
            "name":       str(r.get("venue_name") or "").strip() or (r.get("VENUE_NAME") or "").strip(),
            "state":      str(r.get("venue_state") or "").strip(),
            "venue_type": (r.get("Venue_Type") or "").strip(),
            "year":       str(r.get("Pricing_Year") or "").strip(),
            "space":      (r.get("Venue_Space_Name") or "").strip(),
            "pdf_id":     r.get("PDF_ID"),
            "guest_min":  _vp_num(r.get("Guest_Min_Highest_Sat")),
            "max_cap":    _vp_num(r.get("Max_Capacity_Seated")),
            "venue_fee":  _vp_num(r.get("Venue_Fee_on_a_Peak_Season_Saturday")),
            "pp_fb":      _vp_num(r.get("Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday")),
            "fb_min":     _vp_num(r.get("Food_and_Beverage_Min_on_a_Peak_Season_Saturday")),
            "admin_pct":  _vp_num(r.get("Admin_Service_Fee")),
            "cer_fee":    _vp_num(r.get("Ceremony_Fee")),
            "cer_type":   (r.get("Ceremony_fee_Type") or "").strip(),
        })
    return out


def _vp_estimate_parts(row, G):
    """All-in estimate for one pricing row. Returns (total, parts) where parts holds
    the components that roll up to the total: base + max(per-head*G, F&B min) +
    admin% + ceremony. No tax (none in Xano)."""
    fb = max(row["pp_fb"] * G, row["fb_min"])
    subtotal = row["venue_fee"] + fb
    admin = subtotal * (row["admin_pct"] / 100.0)
    t = row["cer_type"].lower()
    per_person = ("per person" in t) or ("per head" in t) or (t == "pp")
    ceremony = row["cer_fee"] * G if per_person else row["cer_fee"]
    total = subtotal + admin + ceremony
    return total, {"base": row["venue_fee"], "fb": fb, "admin": admin, "ceremony": ceremony}


def _vp_year_int(s):
    """Pull a 4-digit year out of Pricing_Year text → int (None if absent)."""
    m = re.search(r"\d{4}", str(s or ""))
    return int(m.group()) if m else None


def _vp_money(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n >= 1000:
        return "$%.*fK" % (0 if n >= 100000 else 1, n / 1000.0)
    return "$%d" % round(n)


def _vp_build_pdf_map(pdf_rows):
    """{Vendor_ID: [{name, link, year}]} from wptp_pdfs, skipping hidden / link-less."""
    m = {}
    for r in pdf_rows:
        vid  = str(r.get("Vendor_ID") or "").strip()
        link = str(r.get("PDF_Link") or "").strip()
        if not vid or not link:
            continue
        show = r.get("Show_PDF")
        if show is False or str(show).strip() in ("0", "false", "False"):
            continue
        m.setdefault(vid, []).append({
            "name": (str(r.get("Name") or r.get("Current_Assignment") or "PDF")).strip() or "PDF",
            "link": link,
            "year": str(r.get("Year_of_Pricing") or "").strip(),
        })
    return m


@st.cache_data(ttl=600, show_spinner="Loading venue pricing from Xano…")
def _vp_analyze_vendors(vendors, guest_count, price_mode):
    """Quick AI analysis of vendor data for anomalies and insights. Returns markdown summary or empty string."""
    if not vendors or len(vendors) < 3:
        return ""

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return ""

    try:
        client = Anthropic(api_key=api_key)

        # Build a summary of the data
        ests = [v["Estimate"] for v in vendors]
        states = set(v["State"] for v in vendors if v["State"] != "—")
        types = set(v["Type"] for v in vendors if v["Type"] != "—")
        avg_est = sum(ests) / len(ests)
        std_dev = (sum((x - avg_est) ** 2 for x in ests) / len(ests)) ** 0.5

        # Pick a few representative venues for context
        cheapest = min(vendors, key=lambda v: v["Estimate"])
        expensive = max(vendors, key=lambda v: v["Estimate"])
        top_quoted = max(vendors, key=lambda v: v["Quotes"])

        prompt = f"""You are analyzing venue pricing data from Tulle Together (a wedding vendor pricing platform).

Data summary ({len(vendors)} venues, {guest_count} guests):
- Pricing mode: {price_mode}
- Average estimate: ${avg_est:.0f}, std dev: ${std_dev:.0f}
- Range: ${min(ests):.0f} – ${max(ests):.0f}
- States represented: {', '.join(sorted(states)) if states else 'N/A'}
- Venue types: {', '.join(sorted(types)) if types else 'N/A'}

Notable outliers:
- Cheapest: {cheapest['Venue']} ({cheapest['State']}, {cheapest['Type']}) @ ${cheapest['Estimate']:.0f} ({cheapest['Quotes']} PDFs)
- Most expensive: {expensive['Venue']} ({expensive['State']}, {expensive['Type']}) @ ${expensive['Estimate']:.0f} ({expensive['Quotes']} PDFs)
- Most documented: {top_quoted['Venue']} ({top_quoted['State']}, {top_quoted['Type']}) with {top_quoted['Quotes']} pricing PDFs

Provide a VERY SHORT (1–2 sentences max) insight: identify one striking pattern, anomaly, or trend.
Focus on: extreme price gaps, unusual F&B/admin ratios, sparse data coverage, or state-level quirks.
Ignore missing/null fields. If nothing stands out, say "No major anomalies detected."
Do NOT explain what we did. Format as plain text, no markdown."""

        response = client.messages.create(
            model="claude-opus-4-1",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )

        return response.content[0].text.strip()
    except Exception as e:
        return f"Analysis unavailable ({type(e).__name__})"


@st.cache_data(ttl=600)
def _vp_load():
    """Pull + dedup + join (venues only) + PDF links. Cached 10 min; _vp_load.clear() to refresh.
    Returns (joined_rows, pdf_map, meta)."""
    breaker = {}   # trips on the first confirmed 503 so a down Xano fails fast
    t36, e36 = _vp_fetch_paged("pricing", breaker)
    pdfs, ep = _vp_fetch_paged("pdfs", breaker)
    t36d = _vp_dedup_latest(t36)
    joined = _vp_join(t36d)
    pdf_map = _vp_build_pdf_map(pdfs)
    meta = {
        "errors": [x for x in (f"pricing:{e36}" if e36 else "",
                               f"pdfs:{ep}" if ep else "") if x],
        "counts": {"t36_raw": len(t36), "t36_deduped": len(t36d),
                   "venues": len({r["vendor_id"] for r in joined}),
                   "joined": len(joined),
                   "pdf_vendors": len(pdf_map)},
        "states":      sorted({r["state"] for r in joined if r["state"]}),
        "venue_types": sorted({r["venue_type"] for r in joined if r["venue_type"]}),
        "years":       sorted({r["year"] for r in joined if r["year"]}),
    }
    return joined, pdf_map, meta


def _vp_compute_vendors(joined, G, f_state, f_type, f_year, min_quotes, search, price_mode="All-in formula"):
    """One representative ('from') estimate per venue + quote count, after filters."""
    by_vendor = {}
    for r in joined:
        # f_state and f_type are now lists (multiselect). Empty list means "all".
        if f_state and r["state"] not in f_state:
            continue
        if f_type and r["venue_type"] not in f_type:
            continue
        if f_year != "All" and r["year"] != f_year:
            continue
        by_vendor.setdefault(r["vendor_id"], []).append(r)

    q = (search or "").strip().lower()
    vendors = []
    for vid, rows in by_vendor.items():
        qualify = [r for r in rows if r["guest_min"] <= G and (r["max_cap"] == 0 or G <= r["max_cap"])]
        cand = qualify or rows
        # Representative = cheapest qualifying space; keep ITS breakdown so the
        # components shown sum to the displayed estimate.
        best_est, best_parts, best_row = None, None, None
        for r in cand:
            if price_mode == "Base fee only":
                est = r["venue_fee"]
                parts = {"base": r["venue_fee"], "fb": 0, "admin": 0, "ceremony": 0}
            else:
                est, parts = _vp_estimate_parts(r, G)
            if est <= 0:
                continue
            if best_est is None or est < best_est:
                best_est, best_parts, best_row = est, parts, r
        if best_est is None:
            continue
        n_quotes = len({r["pdf_id"] for r in rows})
        if n_quotes < min_quotes:
            continue
        name = rows[0]["name"] or ""
        if q and q not in name.lower():
            continue
        cap = int(max((r["max_cap"] for r in rows), default=0)) or None
        years = [y for y in (_vp_year_int(r["year"]) for r in rows) if y]
        vendors.append({
            "Venue":    name,
            "State":    rows[0]["state"] or "—",
            "Type":     rows[0]["venue_type"] or "—",
            "Capacity": cap,                              # numeric (sortable)
            "Quotes":   n_quotes,                         # numeric
            "Year":     max(years) if years else None,    # numeric
            "Base fee": round(best_parts["base"]),
            "F&B":      round(best_parts["fb"]),
            "Admin":    round(best_parts["admin"]),
            "Ceremony": round(best_parts["ceremony"]),
            "Estimate": round(best_est),                  # "from" price — tunable
            # detail-only (underscore keys are dropped from the displayed table):
            "_vid":       vid,
            "_space":     best_row.get("space", ""),
            "_per_head":  best_row["pp_fb"],
            "_fb_min":    best_row["fb_min"],
            "_admin_pct": best_row["admin_pct"],
            "_cer_type":  best_row["cer_type"],
        })
    vendors.sort(key=lambda v: v["Estimate"], reverse=True)
    return vendors


with tab_vp:
    _mode_display = st.session_state.get("vp_price_mode_applied", "All-in formula")
    st.markdown("### 💰 Venue Pricing — approximate all-in cost")
    if _mode_display == "Base fee only":
        st.caption(
            "Peak-season Saturday · **base fee only** (venue rental, no F&B / admin / ceremony). "
            "Venues only. Estimate per venue is the cheapest qualifying space."
        )
    else:
        st.caption(
            "Peak-season Saturday estimate · base fee + F&B (with minimum) + admin% + ceremony "
            "· no tax (none in Xano). Venues only. Estimate per venue is the cheapest qualifying space."
        )

    _vp_rc, _ = st.columns([2, 6])
    with _vp_rc:
        if st.button("🔄 Load / Refresh", type="primary", use_container_width=True, key="vp_refresh"):
            _vp_load.clear()

    # Load data (cached 10min). Filters don't trigger reloads — only Apply/Run buttons trigger computation.
    vp_joined, vp_pdf_map, vp_meta = _vp_load()

    if vp_meta["errors"]:
        st.error("Xano fetch issue — " + ", ".join(vp_meta["errors"]))

    if not vp_joined:
        st.info("No venue pricing data available. Click Load / Refresh to retry.")
    else:
        # ── Filters — staged: nothing recomputes until you click Apply ────────
        st.markdown("#### Filters")
        c1, c2, c3, c4, c5 = st.columns([1.0, 1.4, 1.6, 1.2, 1.1])
        guests  = c1.selectbox("Guest count", [50, 75, 100, 125, 150, 175, 200, 250, 300],
                               index=4, key="vp_guests")
        f_state = c2.multiselect("State", vp_meta["states"], key="vp_state",
                                 placeholder="All states")
        f_type  = c3.multiselect("Venue type", vp_meta["venue_types"], key="vp_type",
                                 placeholder="All types")
        f_year  = c4.selectbox("Year", ["All"] + vp_meta["years"], key="vp_year")
        min_q   = c5.number_input("Min quotes", min_value=0, value=0, step=1, key="vp_minq")
        search  = st.text_input("Filter by venue name", key="vp_search", placeholder="e.g. Mansion")

        price_mode = st.radio(
            "Pricing mode",
            ["All-in formula", "Base fee only"],
            horizontal=True,
            key="vp_price_mode",
            help="All-in: base fee + F&B + admin% + ceremony.  Base fee only: venue rental only.",
        )

        apply = st.button("✅ Apply filters & compute", type="primary", key="vp_apply")
        st.caption("Change filters freely — the grid only recomputes when you click **Apply** "
                   "(keeps it fast and avoids extra work).")

        if apply:
            st.session_state["vp_vendors"] = _vp_compute_vendors(
                vp_joined, guests, f_state, f_type, f_year, int(min_q), search, price_mode)
            st.session_state["vp_guests_applied"] = guests
            st.session_state["vp_price_mode_applied"] = price_mode
            st.session_state.pop("vp_drill", None)  # reset drill on a fresh compute

        vendors = st.session_state.get("vp_vendors")
        if vendors is None:
            st.info("Set your filters above and click **✅ Apply filters & compute** to build the grid.")
        elif not vendors:
            st.warning("No venues match these filters.")
        else:
            G = st.session_state.get("vp_guests_applied", guests)

            ests = sorted(v["Estimate"] for v in vendors)
            avg = sum(ests) / len(ests)
            mid = len(ests) // 2
            med = ests[mid] if len(ests) % 2 else (ests[mid - 1] + ests[mid]) / 2.0
            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("Avg estimate", _vp_money(avg))
            m2.metric("Median",       _vp_money(med))
            m3.metric("Min",          _vp_money(ests[0]))
            m4.metric("Max",          _vp_money(ests[-1]))
            m5.metric("Venues",       len(vendors))
            m6.metric("Total quotes", sum(v["Quotes"] for v in vendors))

            # AI Summary
            ai_summary = _vp_analyze_vendors(vendors, G, _mode_display)
            if ai_summary:
                with st.expander("🤖 AI Summary (anomalies & patterns)", expanded=True):
                    st.caption(ai_summary)

            with st.expander("ℹ️ How the estimate is calculated"):
                if _mode_display == "Base fee only":
                    st.markdown(
                        f"""
For each venue we take its **cheapest qualifying space** (one that can host **{G} guests**)
and show its **base fee** (venue rental only) using peak-season-Saturday pricing.

**F&B, admin %, and ceremony fees are excluded** in this mode. This helps you focus on raw venue
capacity costs without the event-size scaling. Click a grid cell below, hit **Run**, then click a
venue row to see its exact numbers and pricing PDFs.
                        """
                    )
                else:
                    st.markdown(
                        f"""
For each venue we take its **cheapest qualifying space** (one that can host **{G} guests**)
and add up, using peak-season-Saturday pricing:

- **Base fee** — the venue rental.
- **F&B** = `max(per-person F&B × {G} guests, F&B minimum)` — scales with the guest count.
- **Admin** = `(Base fee + F&B) × admin/service %`.
- **Ceremony** — flat, or `× {G}` when charged per person.

There is **no tax** in the data. Click a grid cell below, hit **Run**, then click a venue row
to see its exact numbers and pricing PDFs.
                        """
                    )

            _mode_applied = st.session_state.get("vp_price_mode_applied", "All-in formula")
            if _mode_applied == "Base fee only":
                _vp_order = ["Venue", "State", "Type", "Capacity", "Quotes", "Year", "Estimate"]
                _vp_colcfg = {
                    "Capacity": st.column_config.NumberColumn("Capacity", format="%.0f"),
                    "Quotes":   st.column_config.NumberColumn("Quotes",   format="%.0f"),
                    "Year":     st.column_config.NumberColumn("Year",     format="%.0f"),
                    "Estimate": st.column_config.NumberColumn("Base fee", format="$%.0f"),
                }
            else:
                _vp_order = ["Venue", "State", "Type", "Capacity", "Quotes", "Year",
                             "Base fee", "F&B", "Admin", "Ceremony", "Estimate"]
                _vp_colcfg = {
                    "Capacity": st.column_config.NumberColumn("Capacity", format="%.0f"),
                    "Quotes":   st.column_config.NumberColumn("Quotes",   format="%.0f"),
                    "Year":     st.column_config.NumberColumn("Year",     format="%.0f"),
                    "Base fee": st.column_config.NumberColumn("Base fee", format="$%.0f"),
                    "F&B":      st.column_config.NumberColumn("F&B",      format="$%.0f"),
                    "Admin":    st.column_config.NumberColumn("Admin",    format="$%.0f"),
                    "Ceremony": st.column_config.NumberColumn("Ceremony", format="$%.0f"),
                    "Estimate": st.column_config.NumberColumn("Estimate", format="$%.0f"),
                }

            df = pd.DataFrame(vendors)
            df = df[[c for c in _vp_order if c in df.columns]].reset_index(drop=True)

            # ── 1) Pricing grid ON TOP (clickable) ────────────────────────────
            st.markdown("#### Average estimate · State × Venue type")
            st.caption("Click a **State** row (and optionally a **Venue type** column header) to pick a "
                       "section, then hit **Run** to filter the venue table below to it · greener = cheaper")
            pivot = df.pivot_table(index="State", columns="Type", values="Estimate", aggfunc="mean")
            sel_state = sel_type = None
            if pivot.size and pivot.notna().any().any():
                _flat = [v for v in pivot.values.flatten() if pd.notna(v)]
                _lo, _hi = min(_flat), max(_flat)

                def _vp_cell_style(val):
                    if pd.isna(val):
                        return ""
                    t = (val - _lo) / (_hi - _lo) if _hi > _lo else 0.0
                    a, b = (234, 244, 239), (245, 201, 138)  # sage-lt → amber
                    c = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
                    return f"background-color: rgb({c[0]},{c[1]},{c[2]})"

                _sty = pivot.style
                _elementwise = getattr(_sty, "map", None) or _sty.applymap
                styled = _elementwise(_vp_cell_style).format(
                    lambda v: "" if pd.isna(v) else _vp_money(v))
                _grid = st.dataframe(
                    styled, use_container_width=True, key="vp_grid",
                    on_select="rerun", selection_mode=["single-row", "single-column"],
                )
                _gs = _grid.selection if _grid else None
                if _gs:
                    if _gs.rows:
                        sel_state = pivot.index[_gs.rows[0]]
                    if _gs.columns:
                        _c = _gs.columns[0]
                        sel_type = pivot.columns[_c] if isinstance(_c, int) else _c

                # Run is enabled as soon as a row OR column is selected — a single
                # cell click selects the row (State); also click a column header to
                # narrow to one venue type.
                if sel_state is not None or sel_type is not None:
                    _picked = " × ".join([str(x) for x in (sel_state, sel_type) if x is not None])
                    _rc1, _rc2 = st.columns([3, 5])
                    with _rc1:
                        if st.button(f"▶ Run — show venues in {_picked}", key="vp_drill_run",
                                     type="primary", use_container_width=True):
                            st.session_state["vp_drill"] = (sel_state, sel_type)
                    with _rc2:
                        st.caption(f"Selected: **{_picked}**")
                else:
                    st.caption("Click a State row (and optionally a Venue-type column header) to enable Run.")
            else:
                st.caption("Not enough venue-type data to build the matrix.")

            # ── 2) Venue line-item table — how each estimate is built ─────────
            # Always visible (defaults to every venue in the applied filters); Run
            # narrows it to the picked grid section.
            _drill = st.session_state.get("vp_drill")
            if _drill:
                ds, dt = _drill
                subset = [v for v in vendors
                          if (ds is None or v["State"] == ds) and (dt is None or v["Type"] == dt)]
                _lbl = " × ".join([str(x) for x in (ds, dt) if x is not None]) or "selection"
                _h1, _h2 = st.columns([5, 1])
                with _h1:
                    st.markdown(f"#### Venues in {_lbl} @ {G} guests — {len(subset)} venue(s)")
                with _h2:
                    if st.button("Show all", key="vp_drill_clear", use_container_width=True):
                        st.session_state.pop("vp_drill", None)
            else:
                subset = vendors
                st.markdown(f"#### All venues @ {G} guests — {len(subset)} venue(s)")
                st.caption("Each row shows how the estimate is built (base · F&B · admin · ceremony). "
                           "Pick a grid cell above + **Run** to filter to one section; click a row for its PDFs.")

            if not subset:
                st.caption("No venues for this selection.")
            else:
                ddf = pd.DataFrame(subset)
                ddf = ddf[[c for c in _vp_order if c in ddf.columns]].reset_index(drop=True)
                _vp_event = st.dataframe(
                    ddf, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row", key="vp_drill_table",
                    column_config=_vp_colcfg,
                )
                _sel = (_vp_event.selection.rows if _vp_event and _vp_event.selection else [])
                if _sel:
                    v = subset[_sel[0]]
                    st.markdown(f"##### {v['Venue']} — breakdown @ {G} guests")
                    per_head, fb_min = v["_per_head"], v["_fb_min"]
                    by_head = per_head * G
                    which = "per-head applies" if by_head >= fb_min else "F&B minimum applies"
                    cer_pp = any(s in v["_cer_type"].lower() for s in ("per person", "per head", "pp"))
                    d1, d2 = st.columns([3, 2])
                    with d1:
                        st.markdown(
                            f"- **Base fee:** {_vp_money(v['Base fee'])}"
                            + (f"  ·  space: {v['_space']}" if v["_space"] else "") + "\n"
                            f"- **F&B:** max( ${per_head:,.0f}/guest × {G} = ${by_head:,.0f} ,  "
                            f"min ${fb_min:,.0f} ) → **{_vp_money(v['F&B'])}**  _({which})_\n"
                            f"- **Admin:** (base + F&B) × {v['_admin_pct']:.0f}% → {_vp_money(v['Admin'])}\n"
                            f"- **Ceremony:** {_vp_money(v['Ceremony'])}  _({'per person × ' + str(G) if cer_pp else 'flat'})_\n"
                            f"- **Estimate (from):** {_vp_money(v['Estimate'])}"
                        )
                    with d2:
                        pdfs = vp_pdf_map.get(v["_vid"], [])
                        if pdfs:
                            st.markdown("**Pricing PDFs**")
                            for p in pdfs:
                                lbl = p["name"] + (f" · {p['year']}" if p["year"] else "")
                                st.markdown(f"- [{lbl}]({p['link']})")
                        else:
                            st.caption("No pricing PDFs linked for this venue.")

            cnt = vp_meta["counts"]
            st.caption(
                f"Data: {cnt['joined']:,} priced venue rows · {cnt['venues']:,} venues (all states) · "
                f"deduped {cnt['t36_deduped']:,}/{cnt['t36_raw']:,} table-36 rows"
            )


# ── TAB: COHORTS (📈) ─────────────────────────────────────────────────────────
with tab_co:
    render_cohorts_tab(XANO_BASE)
