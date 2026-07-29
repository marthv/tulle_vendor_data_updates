"""Appearance modes for the Tulle Admin dashboard: Light / Sepia / Dark.

WHY THIS EXISTS
---------------
dashboard.py used to inline a stylesheet that set `.stApp { background: #f8f9fa }`
with no matching `color:`, and the repo had no `.streamlit/config.toml`. Streamlit
therefore auto-detected the theme from the browser's `prefers-color-scheme`, so a
user whose OS was in dark mode got Streamlit's dark-base text (near-white) painted
onto that forced-light canvas — invisible text everywhere except elements carrying
their own colour. It was reported 2026-07-28 by two accounts who could only read the
page by selecting it, and it never reproduced on a light-mode machine. Making things
worse, dashboard.py hides Streamlit's toolbar, so affected users could not reach
Streamlit's own theme picker to work around it.

The fix is two-part and BOTH parts matter:
  1. `.streamlit/config.toml` pins `base = "light"`, making Streamlit's own text,
     widget and dataframe colours deterministic regardless of OS setting.
  2. This module layers a palette on top as CSS custom properties, and — the bit
     that was missing before — sets an explicit text colour on `.stApp` AND on the
     elements Streamlit colours directly (metrics, headings, tab labels, widget
     labels, expander summaries), because those do not inherit from `.stApp`.

KNOWN LIMITATION (deliberate)
-----------------------------
`st.dataframe` and the native `st.line_chart` / `st.bar_chart` render to
canvas/Vega and take their colours from `.streamlit/config.toml`, NOT from CSS. In
sepia/dark they therefore stay light, so `_widget_paper_css()` frames them as
intentional white "paper" panels rather than leaving them looking broken. Do not
"fix" this by changing the config base — that would re-introduce the original bug
for one of the three modes.
"""

from __future__ import annotations

import streamlit as st

MODES = ("light", "sepia", "dark")
DEFAULT_MODE = "light"
_QUERY_KEY = "theme"
_COOKIE_KEY = "tulle_theme"
_STATE_KEY = "tulle_theme_mode"

# Every palette must keep body text at >= 4.5:1 against its own background.
# `card_*` pairs are tinted status chips and are intentionally light-on-light in
# all three modes — they carry their own ink, so they stay legible everywhere.
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#F8F9FA",
        "surface": "#FFFFFF",
        "surface_alt": "#F1F3F5",
        "text": "#18191A",
        "text_muted": "#52555C",
        "border": "#E1E4E8",
        "brand": "#1B7A4A",
        "brand_hover": "#155F39",
        "brand_ink": "#FFFFFF",
        "log_bg": "#0F172A",
        "log_fg": "#E2E8F0",
        "log_border": "#1E293B",
        "track": "#EEF2F0",
        "input_bg": "#FFFFFF",
    },
    "sepia": {
        # Warm cream canvas with brown-black ink — lowest glare for long reading,
        # and it matches the Tulle brand cream (--cream #FAF8F4).
        "bg": "#F7F1E3",
        "surface": "#FDF9F0",
        "surface_alt": "#EFE7D5",
        "text": "#33291C",
        "text_muted": "#6B5D49",
        "border": "#DFD3BA",
        "brand": "#1B6B45",
        "brand_hover": "#12512F",
        "brand_ink": "#FDF9F0",
        "log_bg": "#2A2118",
        "log_fg": "#EFE3CE",
        "log_border": "#463724",
        "track": "#E6DCC6",
        "input_bg": "#FDF9F0",
    },
    "dark": {
        "bg": "#15181C",
        "surface": "#1E2228",
        "surface_alt": "#262B33",
        "text": "#E8EAEE",
        "text_muted": "#A8ABB3",
        "border": "#333A44",
        "brand": "#2FA96B",
        "brand_hover": "#268A55",
        "brand_ink": "#0A1410",
        "log_bg": "#0B0E12",
        "log_fg": "#D7DCE4",
        "log_border": "#252B33",
        "track": "#2B313A",
        "input_bg": "#1E2228",
    },
}


def _palette(mode: str) -> dict[str, str]:
    return PALETTES.get(mode, PALETTES[DEFAULT_MODE])


# ── Mode resolution / persistence ─────────────────────────────────────────────
# Precedence: an explicit in-app choice this session > ?theme= in the URL >
# the tulle_theme cookie > light. The cookie is what makes the choice survive a
# fresh visit; the query param is what makes it survive a plain reload even when
# no cookie manager exists (password-auth mode, where dashboard.py leaves
# _cookies as None).

def _clean(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in MODES else None


def resolve_mode(cookies=None) -> str:
    """Return the active mode, seeding session state on first run."""
    from_state = _clean(st.session_state.get(_STATE_KEY))
    if from_state:
        return from_state

    from_query = _clean(st.query_params.get(_QUERY_KEY))

    from_cookie = None
    if cookies is not None:
        try:
            from_cookie = _clean(cookies.get(_COOKIE_KEY))
        except Exception:
            from_cookie = None

    mode = from_query or from_cookie or DEFAULT_MODE
    st.session_state[_STATE_KEY] = mode
    return mode


def persist_mode(mode: str, cookies=None) -> None:
    """Write the chosen mode to session state, the URL and (if available) a cookie."""
    mode = _clean(mode) or DEFAULT_MODE
    st.session_state[_STATE_KEY] = mode

    # Only touch query params when the value actually changes — assigning on every
    # rerun triggers another rerun and can loop.
    if _clean(st.query_params.get(_QUERY_KEY)) != mode:
        try:
            st.query_params[_QUERY_KEY] = mode
        except Exception:
            pass

    if cookies is not None:
        try:
            cookies.set(_COOKIE_KEY, mode, key=f"{_COOKIE_KEY}_set_{mode}")
        except Exception:
            pass


def preserve_on_clear() -> None:
    """Clear query params but keep ?theme=.

    dashboard.py calls st.query_params.clear() three times while handling the
    Google OAuth callback; a blanket clear would drop the theme on every sign-in.
    """
    mode = _clean(st.query_params.get(_QUERY_KEY))
    st.query_params.clear()
    if mode:
        try:
            st.query_params[_QUERY_KEY] = mode
        except Exception:
            pass


# ── CSS ───────────────────────────────────────────────────────────────────────

def _widget_paper_css(mode: str) -> str:
    """Frame canvas-rendered widgets as white paper in the non-light modes.

    st.dataframe / st.line_chart / st.bar_chart cannot be recoloured with CSS (see
    the module docstring), so on a cream or dark canvas they are given a border and
    padding to read as deliberate inset panels.
    """
    if mode == "light":
        return ""
    return """
    [data-testid="stDataFrame"],
    [data-testid="stElementContainer"]:has(canvas) {
        background: #FFFFFF;
        border: 1px solid var(--tt-border);
        border-radius: 10px;
        padding: 6px;
    }
    """


def build_css(mode: str) -> str:
    """The whole dashboard stylesheet, parameterised by palette.

    This replaces the stylesheet that used to be inlined in dashboard.py. The
    substantive difference is the explicit `color:` rules — the old version set a
    background with no text colour, which is what caused the invisible text.
    """
    p = _palette(mode)
    return f"""
<style>
    :root {{
        --tt-bg: {p['bg']};
        --tt-surface: {p['surface']};
        --tt-surface-alt: {p['surface_alt']};
        --tt-text: {p['text']};
        --tt-text-muted: {p['text_muted']};
        --tt-border: {p['border']};
        --tt-brand: {p['brand']};
        --tt-brand-hover: {p['brand_hover']};
        --tt-brand-ink: {p['brand_ink']};
        --tt-track: {p['track']};
    }}

    /* ── Global ── */
    .block-container {{ max-width: 92vw !important; padding: 0.75rem 2rem 1.5rem !important; }}
    .stApp {{ background: var(--tt-bg); color: var(--tt-text); }}
    /* Remove Streamlit's fixed top chrome bar — it was floating over and
       clipping our own header. We render the Tulle logo/title ourselves.
       NOTE this also removes Streamlit's theme picker, which is why the app
       ships its own mode switcher in the header. */
    [data-testid="stHeader"]     {{ display: none !important; }}
    [data-testid="stDecoration"] {{ display: none !important; }}
    [data-testid="stToolbar"]    {{ display: none !important; }}

    /* ── Text colour: Streamlit paints these directly, so they do NOT inherit
       from .stApp. Every selector here was invisible in the original bug. ── */
    .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
    [data-testid="stMarkdownContainer"],
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li,
    [data-testid="stMarkdownContainer"] td,
    [data-testid="stMarkdownContainer"] th,
    [data-testid="stMetricValue"],
    [data-testid="stMetricLabel"],
    [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] p,
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary p,
    .stRadio label, .stCheckbox label, .stSelectbox label,
    .stTabs [data-baseweb="tab"] {{
        color: var(--tt-text) !important;
    }}
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p,
    [data-testid="stMetricDelta"] {{
        color: var(--tt-text-muted) !important;
    }}
    /* Active tab keeps the brand accent rather than Streamlit's default red. */
    .stTabs [aria-selected="true"] {{ color: var(--tt-brand) !important; }}

    /* ── Inputs ── */
    .stTextInput input, .stTextArea textarea, .stNumberInput input {{
        background: {p['input_bg']} !important;
        color: var(--tt-text) !important;
        border-color: var(--tt-border) !important;
    }}
    [data-baseweb="select"] > div {{
        background: {p['input_bg']} !important;
        color: var(--tt-text) !important;
    }}

    /* ── Header ── */
    .tulle-logo {{ font-size: 22px; font-weight: 700; color: var(--tt-brand); letter-spacing: -0.3px; }}
    .tulle-user {{ font-size: 13px; color: var(--tt-text-muted); }}
    .tulle-rule {{ border: none; border-top: 2px solid var(--tt-brand); margin: 4px 0 16px; }}
    .tulle-login-title {{ font-size: 26px; font-weight: 700; color: var(--tt-text); }}

    /* ── Metric cards ──
       Tinted status chips. Each pairs a light background with its own dark ink, so
       they stay readable in all three modes without palette substitution. */
    .metric-card {{
        border-radius: 10px; padding: 18px 14px;
        text-align: center; margin-bottom: 8px;
    }}
    .metric-card .metric-icon {{ font-size: 20px; margin-bottom: 4px; }}
    .metric-card .metric-value {{ font-size: 30px; font-weight: 700; margin: 4px 0; }}
    .metric-card .metric-label {{ font-size: 12px; opacity: 0.75; }}
    .card-green  {{ background: #d1fae5; color: #065f46; border: 1.5px solid #6ee7b7; }}
    .card-amber  {{ background: #fef3c7; color: #92400e; border: 1.5px solid #fcd34d; }}
    .card-purple {{ background: #ede9fe; color: #4c1d95; border: 1.5px solid #c4b5fd; }}
    .card-red    {{ background: #fee2e2; color: #991b1b; border: 1.5px solid #fca5a5; }}
    .card-gray   {{ background: #f3f4f6; color: #374151; border: 1.5px solid #d1d5db; }}
    .metric-card .metric-value, .metric-card .metric-label,
    .metric-card .metric-icon {{ color: inherit !important; }}

    /* ── Log box ── */
    .log-box {{
        background: {p['log_bg']}; color: {p['log_fg']};
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
        font-size: 12.5px; padding: 16px; border-radius: 8px;
        max-height: 500px; overflow-y: auto;
        white-space: pre-wrap; word-break: break-word;
        border: 1px solid {p['log_border']};
    }}

    /* ── Run result cards ── */
    .run-card {{
        background: var(--tt-surface); border-radius: 10px; padding: 14px 16px;
        margin-bottom: 10px; border-left: 4px solid var(--tt-brand);
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }}
    .run-card.failed  {{ border-left-color: #ef4444; }}
    .run-card.partial {{ border-left-color: #f59e0b; }}
    /* Had no colour at all — white-on-white in the original bug. */
    .run-card-title   {{ font-weight: 600; font-size: 14px; margin-bottom: 6px; color: var(--tt-text); }}
    .run-card-meta    {{ font-size: 12px; color: var(--tt-text-muted); }}
    .run-card-badge   {{
        display: inline-block; font-size: 11px; font-weight: 600;
        padding: 2px 8px; border-radius: 99px; margin-right: 6px;
    }}
    .badge-green  {{ background: #d1fae5; color: #065f46; }}
    .badge-amber  {{ background: #fef3c7; color: #92400e; }}
    .badge-red    {{ background: #fee2e2; color: #991b1b; }}

    /* ── Funnel (cohorts tab) ── */
    .tt-funnel-label {{ color: var(--tt-text); }}
    .tt-funnel-pct   {{ color: var(--tt-text-muted); }}
    .tt-funnel-track {{ background: var(--tt-track); }}

    /* ── Buttons ── */
    .stButton>button {{
        width: 100%; border-radius: 7px; font-weight: 500;
        transition: all 0.15s;
    }}
    .stButton>button[kind="primary"] {{
        background: var(--tt-brand) !important;
        border-color: var(--tt-brand) !important;
        color: var(--tt-brand-ink) !important;   /* was unset — label followed the theme */
    }}
    .stButton>button[kind="primary"]:hover {{
        background: var(--tt-brand-hover) !important;
        border-color: var(--tt-brand-hover) !important;
        color: var(--tt-brand-ink) !important;
    }}
    .stButton>button[kind="secondary"] {{
        background: var(--tt-surface) !important;
        color: var(--tt-text) !important;
        border-color: var(--tt-border) !important;
    }}

    /* ── Tables ── */
    .stDataFrame {{ border-radius: 8px; overflow: hidden; }}
    {_widget_paper_css(mode)}
</style>
"""


def inject(mode: str) -> None:
    st.markdown(build_css(mode), unsafe_allow_html=True)


# ── Switcher UI ───────────────────────────────────────────────────────────────

_LABELS = {"light": "☀️ Light", "sepia": "📜 Sepia", "dark": "🌙 Dark"}


def mode_selector(current: str, cookies=None, key: str = "tt_mode_pick") -> str:
    """Render the Light/Sepia/Dark switcher. Returns the (possibly new) mode.

    Falls back to a radio on Streamlit builds without st.segmented_control.
    """
    options = list(MODES)
    labels = [_LABELS[m] for m in options]
    index = options.index(current) if current in options else 0

    picked_label = None
    if hasattr(st, "segmented_control"):
        picked_label = st.segmented_control(
            "Appearance",
            labels,
            default=labels[index],
            key=key,
            label_visibility="collapsed",
        )
    else:
        picked_label = st.radio(
            "Appearance",
            labels,
            index=index,
            horizontal=True,
            key=key,
            label_visibility="collapsed",
        )

    if not picked_label:                       # segmented_control allows deselection
        return current
    picked = options[labels.index(picked_label)]
    if picked != current:
        persist_mode(picked, cookies)
        st.rerun()
    return picked
