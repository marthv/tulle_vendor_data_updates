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
import datetime
import json
import requests
import pandas as pd
import streamlit as st
import google.auth.transport.requests
import google.oauth2.id_token
from extract_core import run_extraction

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Tulle Admin Dashboard",
    page_icon="tulle.png",
    layout="centered",
)

st.logo("tulle.png")

st.markdown("""
<style>
    .stButton>button { width: 100%; }
    .log-box {
        background: #0e1117;
        color: #e0e0e0;
        font-family: monospace;
        font-size: 13px;
        padding: 16px;
        border-radius: 8px;
        max-height: 480px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .status-ok   { color: #4ade80; font-weight: bold; }
    .status-fail { color: #f87171; font-weight: bold; }
    .status-warn { color: #fbbf24; font-weight: bold; }
    .metric-card {
        border-radius: 12px;
        padding: 20px 16px;
        text-align: center;
        margin-bottom: 8px;
    }
    .metric-card .metric-icon { font-size: 22px; margin-bottom: 4px; }
    .metric-card .metric-value { font-size: 32px; font-weight: 700; margin: 4px 0; }
    .metric-card .metric-label { font-size: 13px; opacity: 0.8; }
    .card-green  { background: #d1fae5; color: #065f46; border: 1.5px solid #6ee7b7; }
    .card-amber  { background: #fef3c7; color: #92400e; border: 1.5px solid #fcd34d; }
    .card-purple { background: #ede9fe; color: #4c1d95; border: 1.5px solid #c4b5fd; }
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
    st.markdown("""
        <div style="text-align:center;padding:60px 0 24px">
            <div style="font-size:36px">🌿</div>
            <div style="font-size:26px;font-weight:700;margin-top:8px">Tulle Admin Dashboard</div>
        </div>
    """, unsafe_allow_html=True)

    if _USE_GOOGLE_AUTH:
        auth_url = _build_google_auth_url()
        # Centre the button with padding columns
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

col_title, col_user, col_logout = st.columns([4, 3, 1])
with col_title:
    st.markdown("""
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0">
            <span style="font-size:28px">🌿</span>
            <span style="font-size:24px;font-weight:700">Tulle Admin Dashboard</span>
        </div>
    """, unsafe_allow_html=True)
with col_user:
    if user_name and user_name != "Local admin":
        st.markdown(
            f"<div style='text-align:right;padding-top:14px;font-size:13px;color:#52555C'>"
            f"Signed in as <strong>{user_name}</strong></div>",
            unsafe_allow_html=True,
        )
with col_logout:
    st.markdown("<div style='padding-top:12px'>", unsafe_allow_html=True)
    if st.button("Sign out", use_container_width=True):
        for k in ["authenticated", "user_email", "user_name", "user_picture"]:
            st.session_state.pop(k, None)
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

XANO_BASE = os.environ.get("XANO_BASE_URL", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B")


# ── TABS ──────────────────────────────────────────────────────────────────────

tab0, tab1, tab2, tab3, tab4 = st.tabs(["📊 Admin Dashboard", "📄 PDF Extraction", "🔍 Google Data", "🖼️ Vendor Images", "🗂️ Sync Collections"])


# ── TAB 0: ADMIN DASHBOARD ────────────────────────────────────────────────────

XANO_WGW = "https://xqtb-2ma7-ijfy.n7e.xano.io/api:WGW_G49d"

EXPLORER_TABLES = {
    "WPTP Updated Mappings": {
        "url":      f"{XANO_BASE}/wptp_updated_mappings",
        "patch":    f"{XANO_BASE}/wptp_updated_mappings",   # + /{id}
        "id_col":   "id",
        "editable": True,
    },
    "WPTP PDFs": {
        "url":      f"{XANO_BASE}/wptp_pdfs",
        "patch":    None,
        "id_col":   "id",
        "editable": False,
    },
    "Users": {
        "url":      f"{XANO_WGW}/user",
        "patch":    f"{XANO_WGW}/user",    # PATCH /user/{id}
        "id_col":   "id",
        "editable": True,
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
        with st.spinner("Fetching metrics..."):
            try:
                resp = requests.get(
                    f"{XANO_BASE}/admin_metrics",
                    params={
                        "start_ts": _to_ms(start_date),
                        "end_ts":   _to_ms(end_date, end_of_day=True),
                    },
                    timeout=60,
                )
                if resp.status_code == 200:
                    d = resp.json()
                    signups   = d.get("new_signups", 0)
                    pay_made  = d.get("payments_made", 0)
                    pay_uniq  = d.get("unique_payments", 0)
                    pay_rate  = d.get("payment_rate", 0)
                    todo_made = d.get("todos_created", 0)
                    todo_uniq = d.get("unique_users_todos", 0)
                    todo_rate = d.get("todo_rate", 0)
                    pkg_made  = d.get("packages_created", 0)
                    pkg_uniq  = d.get("unique_users_packages", 0)
                    pkg_rate  = d.get("package_rate", 0)

                    st.markdown(_card("card-green", "👤", signups, "New Signups"),
                                unsafe_allow_html=True)
                    c1, c2, c3 = st.columns(3)
                    c1.markdown(_card("card-amber",  "💳", pay_made,  "Payments Made"),    unsafe_allow_html=True)
                    c2.markdown(_card("card-amber",  "💳", pay_uniq,  "Unique Payments"),  unsafe_allow_html=True)
                    c3.markdown(_card("card-amber",  "💳", f"{pay_rate:.2f}%", "Payment Rate"), unsafe_allow_html=True)
                    c4, c5, c6 = st.columns(3)
                    c4.markdown(_card("card-green",  "✅", todo_made, "To-Dos Created"),          unsafe_allow_html=True)
                    c5.markdown(_card("card-green",  "✅", todo_uniq, "Unique Users w/ To-Dos"),  unsafe_allow_html=True)
                    c6.markdown(_card("card-green",  "✅", f"{todo_rate:.2f}%", "To-Do Creation Rate"), unsafe_allow_html=True)
                    c7, c8, c9 = st.columns(3)
                    c7.markdown(_card("card-purple", "📦", pkg_made, "Packages Created"),          unsafe_allow_html=True)
                    c8.markdown(_card("card-purple", "📦", pkg_uniq, "Unique Users w/ Packages"),  unsafe_allow_html=True)
                    c9.markdown(_card("card-purple", "📦", f"{pkg_rate:.2f}%", "Package Creation Rate"), unsafe_allow_html=True)
                else:
                    st.error(f"Xano returned {resp.status_code}")
                    st.code(resp.text[:500])
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
        cols = list(raw[0].keys()) if raw else []

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

        # ── Build DataFrame ────────────────────────────────────────────────
        df_all = pd.DataFrame(raw)
        df     = _apply_filters(df_all.copy(), st.session_state.get("exp_filters", []))
        if row_limit:
            df = df.head(row_limit)

        st.caption(f"{len(df):,} of {len(df_all):,} rows — {exp_table}"
                   + ("" if exp_cfg["editable"] else "  ·  read-only"))

        # ── Display / Edit ─────────────────────────────────────────────────
        edited = st.data_editor(
            df,
            use_container_width=True,
            num_rows="fixed",
            disabled=not exp_cfg["editable"],
            key="exp_editor",
        )

        if exp_cfg["editable"]:
            if st.button("💾 Save Changes", type="primary", use_container_width=True, key="exp_save"):
                id_col   = exp_cfg["id_col"]
                patch_base = exp_cfg["patch"]
                orig_map = {str(r[id_col]): r for r in raw}
                saved = failed = 0
                for _, row in edited.iterrows():
                    row_id  = str(row[id_col])
                    orig    = orig_map.get(row_id, {})
                    changed = {k: v for k, v in row.to_dict().items()
                               if str(v) != str(orig.get(k, "")) and k != id_col}
                    if not changed:
                        continue
                    try:
                        r = requests.patch(
                            f"{patch_base}/{row_id}",
                            json=changed,
                            timeout=15,
                        )
                        if r.status_code in (200, 201, 204):
                            saved += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
                if saved + failed == 0:
                    st.info("No changes detected.")
                elif failed == 0:
                    st.success(f"Saved {saved} row(s).")
                else:
                    st.warning(f"Saved {saved}, failed {failed}.")


# ── TAB 1: PDF EXTRACTION ─────────────────────────────────────────────────────

with tab1:
    st.subheader("PDF Extraction")
    st.caption("Downloads PDFs from Google Drive, runs Claude extraction (4 calls/venue with caching), posts to Xano.")

    col_s, col_e = st.columns(2)
    with col_s:
        start_row = st.number_input("Start row", min_value=0, value=0, step=1,
                                    help="0 = beginning of WPTP PDFs list")
    with col_e:
        end_row_input = st.number_input("End row (0 = all)", min_value=0, value=10, step=1,
                                        help="Set to 0 to process all remaining PDFs")

    end_row = None if end_row_input == 0 else int(end_row_input)

    if "extraction_running" not in st.session_state:
        st.session_state.extraction_running = False

    run_btn = st.button(
        "▶ Run PDF Extraction",
        disabled=st.session_state.extraction_running,
        type="primary",
        use_container_width=True,
    )

    log_placeholder  = st.empty()
    stat_placeholder = st.empty()

    if run_btn:
        st.session_state.extraction_running = True
        lines = []
        summary_result = None

        for item in run_extraction(int(start_row), end_row):
            if isinstance(item, dict):
                summary_result = item
                break
            lines.append(item)
            log_placeholder.markdown(
                f'<div class="log-box">' + "\n".join(lines) + "</div>",
                unsafe_allow_html=True,
            )

        st.session_state.extraction_running = False

        if summary_result:
            ok   = summary_result["ok"]
            part = summary_result["partial"]
            fail = summary_result["failed"]
            if fail == 0 and part == 0:
                stat_placeholder.success(f"Done — {ok} succeeded")
            elif fail > 0:
                stat_placeholder.error(f"Done — {ok} succeeded, {part} partial, {fail} failed")
            else:
                stat_placeholder.warning(f"Done — {ok} succeeded, {part} partial")


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
