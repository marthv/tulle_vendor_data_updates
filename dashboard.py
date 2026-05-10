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

Optional fallback (if GOOGLE_CLIENT_ID is not set, password auth is used):
    DASHBOARD_PASSWORD
"""

import os
import re
import datetime
import json
import requests
import pandas as pd
import streamlit as st
import google.auth.transport.requests
import google.oauth2.id_token
from concurrent.futures import ThreadPoolExecutor, as_completed
from extract_core import run_extraction, get_pipeline_status

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Tulle Admin Dashboard",
    page_icon="tulle.png",
    layout="wide",
)

st.logo("tulle.png")

st.markdown("""
<style>
    /* ── Global ── */
    .block-container { max-width: 92vw !important; padding: 1.5rem 2rem !important; }
    .stApp { background: #f8f9fa; }

    /* ── Header ── */
    .tulle-header {
        display: flex; align-items: center; justify-content: space-between;
        padding: 12px 0 16px; border-bottom: 2px solid #1B7A4A; margin-bottom: 20px;
    }
    .tulle-logo { font-size: 22px; font-weight: 700; color: #1B7A4A; letter-spacing: -0.3px; }
    .tulle-user { font-size: 13px; color: #52555C; }

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


# ── HEADER ────────────────────────────────────────────────────────────────────

user_name  = st.session_state.get("user_name", "")
user_email = st.session_state.get("user_email", "")

_user_info = (f"Signed in as <strong>{user_name}</strong>"
              if user_name and user_name != "Local admin" else "")
st.markdown(f"""
<div class="tulle-header">
    <div class="tulle-logo">🌿 Tulle Admin</div>
    <div class="tulle-user">{_user_info}</div>
</div>
""", unsafe_allow_html=True)
# Keep the sign out button separately in a right-aligned column
_, signout_col = st.columns([9, 1])
with signout_col:
    if st.button("Sign out", use_container_width=True):
        for k in ["authenticated", "user_email", "user_name", "user_picture"]:
            st.session_state.pop(k, None)
        st.rerun()

XANO_BASE = os.environ.get("XANO_BASE_URL", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B")


# ── TABS ──────────────────────────────────────────────────────────────────────

tab0, tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Admin", "📄 PDF Extraction", "🔍 Google Data", "🖼️ Vendor Images", "🗂️ Sync Collections", "🔁 Pipeline"
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

def _card(color_class, icon, value, label):
    return f"""<div class="metric-card {color_class}">
        <div class="metric-icon">{icon}</div>
        <div class="metric-value">{value}</div>
        <div class="metric-label">{label}</div>
    </div>"""

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
| **PDF Extraction** | One-off or targeted extraction runs — download PDFs from Drive, run Claude (4 passes), post rows to Xano |
| **Google Data** | Fetches Google Places data (rating, reviews, address) for vendors with a Place ID but no cached data |
| **Vendor Images** | Pulls photos from Google Places and saves them into WPTP Updated Mappings |
| **Sync Collections** | Reads the `CATEGORY` from extracted PDF data and writes it into the `Collection` field on WPTP Updated Mappings |
| **Pipeline** | Production extraction queue — shows status across all 6,700+ PDFs (Pending / Extracted / Partial / Failed), with run controls and per-venue result cards |

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


# ── TAB 1: PDF EXTRACTION ────────────────────────────────────────────────────

with tab1:
    st.subheader("PDF Extraction")
    st.caption("Downloads PDFs from Google Drive, runs Claude extraction (4 calls/venue with caching), posts to Xano.")

    run_mode_label = st.radio(
        "Run mode",
        [
            "Normal (skip already-extracted)",
            "Rerun specific Vendor IDs (delete old rows first)",
            "Rerun specific Vendor IDs (keep old rows — creates duplicates)",
        ],
        index=0,
        help=(
            "Normal: respects the start/end row range below and skips PDFs already in extracted_pdf_data.\n\n"
            "Rerun (delete): re-extracts the Vendor IDs you specify. Deletes their existing rows from "
            "extracted_pdf_data and venue_pricing first. Ignores the start/end row range.\n\n"
            "Rerun (no delete): re-extracts the Vendor IDs you specify but leaves old rows in place. "
            "Will produce duplicate rows — useful for A/B comparison."
        ),
    )

    if run_mode_label.startswith("Normal"):
        ex_run_mode = "normal"
    elif "delete old rows" in run_mode_label:
        ex_run_mode = "rerun_delete"
    else:
        ex_run_mode = "rerun_no_delete"

    rerun_vendor_ids: list[str] = []

    if ex_run_mode == "normal":
        col_s, col_e = st.columns(2)
        with col_s:
            ex_start_row = st.number_input("Start row", min_value=0, value=0, step=1,
                                           help="0 = beginning of WPTP PDFs list", key="ex_start")
        with col_e:
            ex_end_row_input = st.number_input("End row (0 = all)", min_value=0, value=10, step=1,
                                               help="Set to 0 to process all remaining PDFs", key="ex_end")
        ex_end_row = None if ex_end_row_input == 0 else int(ex_end_row_input)
    else:
        vendor_ids_raw = st.text_input(
            "Vendor IDs to rerun",
            placeholder="VND_018, VND_042, VND_103",
            help="Comma-separated Vendor IDs. The script will pull every PDF in wptp_pdfs that matches.",
        )
        rerun_vendor_ids = [v.strip() for v in vendor_ids_raw.split(",") if v.strip()]
        if rerun_vendor_ids:
            st.caption(f"Will rerun **{len(rerun_vendor_ids)}** Vendor ID(s): {', '.join(rerun_vendor_ids)}")
        if ex_run_mode == "rerun_delete":
            st.warning("⚠ Existing rows in extracted_pdf_data and venue_pricing for these Vendor IDs will be "
                       "**deleted** before re-extraction. This cannot be undone.")
        else:
            st.info("Old rows will be kept — new rows will be added alongside them, creating duplicates.")
        ex_start_row = 0
        ex_end_row = None

    if "extraction_running" not in st.session_state:
        st.session_state.extraction_running = False

    run_disabled = (
        st.session_state.extraction_running
        or (ex_run_mode != "normal" and not rerun_vendor_ids)
    )

    ex_run_btn = st.button(
        "▶ Run PDF Extraction",
        disabled=run_disabled,
        type="primary",
        use_container_width=True,
        key="ex_run_btn",
    )

    log_placeholder  = st.empty()
    stat_placeholder = st.empty()

    if ex_run_btn:
        st.session_state.extraction_running = True
        lines = []
        summary_result = None
        for item in run_extraction(
            int(ex_start_row), ex_end_row,
            pdf_ids=rerun_vendor_ids if ex_run_mode != "normal" else None,
        ):
            if isinstance(item, dict):
                summary_result = item
                break
            lines.append(item)
            log_placeholder.markdown(
                '<div class="log-box">' + "\n".join(lines) + "</div>",
                unsafe_allow_html=True,
            )
        st.session_state.extraction_running = False

        if summary_result:
            ok       = summary_result["ok"]
            part     = summary_result["partial"]
            fail     = summary_result["failed"]
            cost_usd = summary_result.get("cost_usd", 0.0)
            tokens   = summary_result.get("tokens", {})

            if fail == 0 and part == 0:
                stat_placeholder.success(f"Done — {ok} succeeded")
            elif fail > 0:
                stat_placeholder.error(f"Done — {ok} succeeded, {part} partial, {fail} failed")
            else:
                stat_placeholder.warning(f"Done — {ok} succeeded, {part} partial")

            if cost_usd > 0:
                tok_in  = tokens.get("input", 0)
                tok_out = tokens.get("output", 0)
                tok_cr  = tokens.get("cache_read", 0)
                tok_cw  = tokens.get("cache_create", 0)
                st.metric(
                    "Claude API cost this run",
                    f"${cost_usd:.4f}",
                    help=(
                        f"claude-sonnet-4-20250514 · "
                        f"{tok_in:,} input · {tok_out:,} output · "
                        f"{tok_cw:,} cache writes · {tok_cr:,} cache reads\n\n"
                        f"Rates: $3/M input · $15/M output · $3.75/M cache write · $0.30/M cache read\n"
                        f"Google Drive downloads: free (service account)"
                    ),
                )


# ── TAB 2: GOOGLE DATA ────────────────────────────────────────────────────────

with tab2:
    st.subheader("Google Data Cache")
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


# ── TAB 3: VENDOR IMAGES ─────────────────────────────────────────────────────

with tab3:
    st.subheader("Vendor Images")
    st.caption(
        "Pulls photos from Google Places API and saves them into WPTP Updated Mappings. "
        "Run **Google Data** first — images require cached Google data. "
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


# ── TAB 4: SYNC COLLECTIONS ───────────────────────────────────────────────────

with tab4:
    st.subheader("Sync Collections")
    st.caption(
        "Reads CATEGORY from Extracted PDF Data and writes it into the **Collection** array "
        "on WPTP Updated Mappings, matched by Vendor ID. "
        "Enter the Vendor IDs you want to sync (one per line, or comma-separated)."
    )

    vendor_ids_input = st.text_area(
        "Vendor IDs",
        placeholder="V001\nV002\nV003",
        height=150,
        help="Enter Vendor_ID values — one per line or comma-separated.",
    )

    if st.button("▶ Run Sync Collections", type="primary", use_container_width=True):
        # Parse: split on newlines and commas, strip whitespace, drop blanks
        raw = vendor_ids_input.replace(",", "\n")
        vendor_ids = [v.strip() for v in raw.splitlines() if v.strip()]

        if not vendor_ids:
            st.warning("Enter at least one Vendor ID before running.")
        else:
            with st.spinner(f"Syncing {len(vendor_ids)} vendor(s)..."):
                try:
                    resp = requests.post(
                        f"{XANO_BASE}/sync_collections",
                        json={"vendor_ids": vendor_ids},
                        timeout=300,
                    )
                    if resp.status_code == 200:
                        data    = resp.json()
                        updated = data.get("updated", 0)
                        found   = data.get("found",   0)
                        skipped = data.get("skipped", [])
                        if found == 0:
                            st.warning("None of those Vendor IDs were found in WPTP Updated Mappings.")
                        elif updated == 0:
                            st.warning(f"Found {found} vendor(s) but none had a matching category in Extracted PDF Data yet.")
                        else:
                            st.success(f"Done — {updated} of {found} vendor(s) updated.")
                        if skipped:
                            with st.expander(f"{len(skipped)} skipped (no category)", expanded=False):
                                st.json(skipped)
                        if data.get("vendors"):
                            with st.expander("Vendors updated", expanded=True):
                                st.json(data["vendors"])
                    else:
                        st.error(f"Xano returned {resp.status_code}")
                        st.code(resp.text[:500])
                except requests.exceptions.Timeout:
                    st.warning("Request timed out (Xano may still be processing). Check Xano directly.")
                except Exception as e:
                    st.error(f"Request failed: {e}")


# ── TAB 5: PIPELINE ───────────────────────────────────────────────────────────

with tab5:
    st.subheader("PDF Extraction Pipeline")
    st.caption(
        "Track extraction status across all PDFs in wptp_pdfs. "
        "Run pending, failed, or specific PDFs without touching already-extracted records."
    )

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
            default=['pending', 'failed', 'partial'],
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

        # Pending/failed counts for button label
        n_pending = counts.get('pending', 0)
        n_failed  = counts.get('failed', 0)

        btn_label = {
            "🆕 All pending":        f"▶ Run All Pending ({n_pending})",
            "❌ Re-run all failed":   f"▶ Re-run All Failed ({n_failed})",
            "🎯 Specific PDF IDs":   "▶ Run Specified PDFs",
            "📏 Row range":          "▶ Run Row Range",
        }.get(run_mode, "▶ Run")

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

        if run_btn:
            # Parse run mode into run_extraction args
            pdf_ids_list   = None
            rerun_failed   = False
            eff_start      = 0
            eff_end        = None

            if run_mode == "🎯 Specific PDF IDs":
                raw = specific_ids_input.replace(",", "\n")
                pdf_ids_list = [v.strip() for v in raw.splitlines() if v.strip()]
                if not pdf_ids_list:
                    st.warning("Enter at least one PDF ID.")
                    st.stop()
            elif run_mode == "❌ Re-run all failed":
                rerun_failed = True
            elif run_mode == "🆕 All pending":
                pass  # default mode, dedup handles it
            elif run_mode == "📏 Row range":
                eff_start = int(pl_start_row)
                eff_end   = None if int(pl_end_row) == 0 else int(pl_end_row)

            st.session_state["pl_running"] = True
            pl_lines = []
            pl_result = None

            run_started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

            for item in run_extraction(
                start_row=eff_start,
                end_row=eff_end,
                pdf_ids=pdf_ids_list,
                rerun_failed=rerun_failed,
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

            if pl_result:
                ok   = pl_result["ok"]
                part = pl_result["partial"]
                fail = pl_result["failed"]
                cost = pl_result.get("cost_usd", 0.0)

                if fail == 0 and part == 0:
                    pl_stat_ph.success(f"Done — {ok} succeeded · ${cost:.4f}")
                elif fail > 0:
                    pl_stat_ph.error(f"Done — {ok} succeeded, {part} partial, {fail} failed · ${cost:.4f}")
                else:
                    pl_stat_ph.warning(f"Done — {ok} succeeded, {part} partial · ${cost:.4f}")

                pl_result["run_started_at"] = run_started_at
                st.session_state["pl_last_result"] = pl_result

                # Auto-refresh status after run
                st.session_state["pl_data"] = get_pipeline_status()
                st.rerun()

        # ── Last run summary (persists after rerun via session_state) ─────────
        _last_result = st.session_state.get("pl_last_result")

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
