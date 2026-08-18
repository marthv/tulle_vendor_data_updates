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
#
# STRUCTURE: the palette is emitted as CSS custom properties by `_root_vars()`,
# and the stylesheet itself (`_STYLESHEET`) is a PLAIN string that references
# only those properties. That split is deliberate — the old version was one big
# f-string, so every `{` in the CSS had to be doubled, and a single missed brace
# silently broke the whole sheet. Nothing below needs escaping now.
#
# Redesign 2026-08-18: typography scale, segmented sticky tab bar, section-header
# accents, real cards for st.metric / expanders / alerts, and a readable content
# width. No palette values changed, so all three modes keep their audited
# contrast ratios.

_FONT_STACK = (
    "'Sora', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
    "'Helvetica Neue', Arial, sans-serif"
)
_MONO_STACK = "'JetBrains Mono', 'Fira Code', ui-monospace, SFMono-Regular, Menlo, monospace"


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
        padding: 8px;
    }
    """


def _root_vars(mode: str) -> str:
    """Emit the palette as custom properties. The only interpolated block."""
    p = _palette(mode)
    return f"""
    :root {{
        --tt-bg:          {p['bg']};
        --tt-surface:     {p['surface']};
        --tt-surface-alt: {p['surface_alt']};
        --tt-text:        {p['text']};
        --tt-text-muted:  {p['text_muted']};
        --tt-border:      {p['border']};
        --tt-brand:       {p['brand']};
        --tt-brand-hover: {p['brand_hover']};
        --tt-brand-ink:   {p['brand_ink']};
        --tt-track:       {p['track']};
        --tt-input-bg:    {p['input_bg']};
        --tt-log-bg:      {p['log_bg']};
        --tt-log-fg:      {p['log_fg']};
        --tt-log-border:  {p['log_border']};

        --tt-font:  {_FONT_STACK};
        --tt-mono:  {_MONO_STACK};

        --tt-radius:    10px;
        --tt-radius-sm: 7px;
        --tt-shadow-sm: 0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.06);
        --tt-shadow-md: 0 2px 4px rgba(16,24,40,.05), 0 4px 12px rgba(16,24,40,.07);
    }}
    """


# Everything below is palette-agnostic — it only reads the custom properties above.
_STYLESHEET = """
    @import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&display=swap');

    /* ── Global ──
       Width: was 92vw, which on a wide monitor produced ~2300px paragraphs and
       tables that had to be tracked across the whole screen. Capped so text has a
       sane measure while still leaving plenty of room for the data grids. */
    .block-container {
        max-width: 1720px !important;
        padding: 0.6rem 2.25rem 3rem !important;
    }
    .stApp {
        background: var(--tt-bg);
        color: var(--tt-text);
        font-family: var(--tt-font);
    }
    html, body, [class*="css"] { font-family: var(--tt-font); }

    /* Remove Streamlit's fixed top chrome bar — it was floating over and
       clipping our own header. We render the Tulle logo/title ourselves.
       NOTE this also removes Streamlit's theme picker, which is why the app
       ships its own mode switcher in the header. */
    [data-testid="stHeader"]     { display: none !important; }
    [data-testid="stDecoration"] { display: none !important; }
    [data-testid="stToolbar"]    { display: none !important; }

    /* ── Text colour: Streamlit paints these directly, so they do NOT inherit
       from .stApp. Every selector here was invisible in the original dark-mode
       bug — do not drop one without checking it against a dark-mode browser. ── */
    .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
    [data-testid="stMarkdownContainer"],
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li,
    [data-testid="stMarkdownContainer"] td,
    [data-testid="stMarkdownContainer"] th,
    [data-testid="stMetricValue"],
    [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] p,
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary p,
    .stRadio label, .stCheckbox label, .stSelectbox label,
    .stTabs [data-baseweb="tab"] {
        color: var(--tt-text) !important;
    }
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p,
    [data-testid="stMetricLabel"],
    [data-testid="stMetricDelta"] {
        color: var(--tt-text-muted) !important;
    }

    /* ── Type scale ──
       The tabs mix ##, ###, #### and ##### fairly freely, so the levels are given
       clearly distinct sizes and weights: the existing markup then reads as a
       hierarchy without having to rewrite every heading in dashboard.py. */
    [data-testid="stMarkdownContainer"] p { font-size: 14px; line-height: 1.55; }
    .stApp h1 { font-size: 26px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.25; margin: 0 0 .5rem; }
    .stApp h2 { font-size: 20px; font-weight: 650; letter-spacing: -0.01em; line-height: 1.3;  margin: 1.6rem 0 .6rem; }
    .stApp h3 { font-size: 16.5px; font-weight: 650; line-height: 1.35; margin: 1.5rem 0 .55rem; }
    .stApp h4 { font-size: 14px; font-weight: 650; line-height: 1.4;  margin: 1.2rem 0 .4rem; }
    .stApp h5,
    .stApp h6 { font-size: 12.5px; font-weight: 650; line-height: 1.4; margin: 1rem 0 .35rem;
                text-transform: uppercase; letter-spacing: .06em;
                color: var(--tt-text-muted) !important; }

    /* Section markers: a brand rule beside h3 makes the page scannable at a
       glance, which is the main thing the flat version was missing. */
    .stApp h3 { position: relative; padding-left: 13px; }
    .stApp h3::before {
        content: ""; position: absolute; left: 0; top: .2em; bottom: .2em;
        width: 3px; border-radius: 2px; background: var(--tt-brand);
    }

    /* ── Tab bar ──
       Rebuilt as a segmented control. Streamlit's default is a thin underline in
       the accent colour, which at six tabs gave almost no signal about where you
       were. Sticky so the answer stays on screen down a long tab. */
    .stTabs [data-baseweb="tab-list"] {
        /* display/align are set explicitly rather than inherited: this rule now
           owns the tab bar's box (padding, radius, background), and relying on
           Streamlit's own default flex here would break the bar into a vertical
           stack if that default ever changes. */
        display: flex;
        align-items: center;
        gap: 4px;
        background: var(--tt-surface-alt);
        border: 1px solid var(--tt-border);
        border-radius: 12px;
        padding: 5px;
        margin-bottom: 1.1rem;
        position: sticky; top: 0; z-index: 99;
        overflow-x: auto;
    }
    .stTabs [data-baseweb="tab"] {
        height: auto;
        padding: 9px 15px;
        border-radius: 8px;
        font-size: 13.5px;
        font-weight: 600;
        white-space: nowrap;
        transition: background .15s, color .15s;
    }
    .stTabs [data-baseweb="tab"]:hover { background: var(--tt-surface); }
    .stTabs [aria-selected="true"] {
        background: var(--tt-surface);
        color: var(--tt-brand) !important;
        box-shadow: var(--tt-shadow-sm);
    }
    /* Kill the default underline + baseline now that selection is shown by fill. */
    .stTabs [data-baseweb="tab-highlight"],
    .stTabs [data-baseweb="tab-border"] { display: none !important; }

    /* ── Inputs ── */
    .stTextInput input, .stTextArea textarea, .stNumberInput input {
        background: var(--tt-input-bg) !important;
        color: var(--tt-text) !important;
        border-color: var(--tt-border) !important;
        border-radius: var(--tt-radius-sm) !important;
        font-family: var(--tt-font) !important;
    }
    .stTextInput input:focus, .stTextArea textarea:focus, .stNumberInput input:focus {
        border-color: var(--tt-brand) !important;
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--tt-brand) 18%, transparent) !important;
    }
    [data-baseweb="select"] > div {
        background: var(--tt-input-bg) !important;
        color: var(--tt-text) !important;
        border-radius: var(--tt-radius-sm) !important;
    }
    [data-testid="stWidgetLabel"] p { font-size: 13px; font-weight: 600; }

    /* ── Header ── */
    .tulle-logo { font-size: 22px; font-weight: 700; color: var(--tt-brand); letter-spacing: -0.3px; }
    .tulle-user { font-size: 13px; color: var(--tt-text-muted); }
    .tulle-rule { border: none; border-top: 2px solid var(--tt-brand); margin: 4px 0 16px; }
    .tulle-login-title { font-size: 26px; font-weight: 700; color: var(--tt-text); }

    /* ── Native st.metric as a card ──
       These are used across tabs and previously rendered as bare floating numbers
       with no boundary, which is a large part of why the pages read as a wall. */
    [data-testid="stMetric"] {
        background: var(--tt-surface);
        border: 1px solid var(--tt-border);
        border-radius: var(--tt-radius);
        padding: 13px 15px;
        box-shadow: var(--tt-shadow-sm);
    }
    [data-testid="stMetricLabel"] p {
        font-size: 11.5px !important; font-weight: 600 !important;
        text-transform: uppercase; letter-spacing: .05em;
    }
    [data-testid="stMetricValue"] { font-size: 25px !important; font-weight: 700 !important; }

    /* ── Metric chips (hand-built cards in dashboard.py / cohorts.py) ──
       Tinted status chips. Each pairs a light background with its own dark ink, so
       they stay readable in all three modes without palette substitution. */
    .metric-card {
        border-radius: var(--tt-radius); padding: 18px 14px;
        text-align: center; margin-bottom: 8px;
    }
    .metric-card .metric-icon  { font-size: 20px; margin-bottom: 4px; }
    .metric-card .metric-value { font-size: 30px; font-weight: 700; margin: 4px 0; }
    .metric-card .metric-label { font-size: 12px; opacity: 0.75; }
    .card-green  { background: #d1fae5; color: #065f46; border: 1.5px solid #6ee7b7; }
    .card-amber  { background: #fef3c7; color: #92400e; border: 1.5px solid #fcd34d; }
    .card-purple { background: #ede9fe; color: #4c1d95; border: 1.5px solid #c4b5fd; }
    .card-red    { background: #fee2e2; color: #991b1b; border: 1.5px solid #fca5a5; }
    .card-gray   { background: #f3f4f6; color: #374151; border: 1.5px solid #d1d5db; }
    .metric-card .metric-value, .metric-card .metric-label,
    .metric-card .metric-icon { color: inherit !important; }

    /* ── Expanders as panels ── */
    [data-testid="stExpander"] {
        background: var(--tt-surface);
        border: 1px solid var(--tt-border);
        border-radius: var(--tt-radius);
        box-shadow: var(--tt-shadow-sm);
        overflow: hidden;
        margin-bottom: .6rem;
    }
    [data-testid="stExpander"] summary {
        padding: 10px 14px !important;
        font-size: 13.5px; font-weight: 600;
    }
    [data-testid="stExpander"] summary:hover { background: var(--tt-surface-alt); }
    [data-testid="stExpander"] details[open] > summary { border-bottom: 1px solid var(--tt-border); }

    /* ── Alerts ──
       st.info / st.warning carry most of the running commentary in these tabs, so
       they are toned down to left-accent notes instead of six competing colour
       blocks fighting the actual content. */
    [data-testid="stAlert"] {
        border-radius: var(--tt-radius-sm);
        border: 1px solid var(--tt-border);
        border-left: 3px solid var(--tt-brand);
        box-shadow: none;
        padding: 10px 14px;
    }
    [data-testid="stAlert"] p { font-size: 13.5px; line-height: 1.5; }

    /* ── Log box ── */
    .log-box {
        background: var(--tt-log-bg); color: var(--tt-log-fg);
        font-family: var(--tt-mono);
        font-size: 12.5px; padding: 16px; border-radius: var(--tt-radius);
        max-height: 500px; overflow-y: auto;
        white-space: pre-wrap; word-break: break-word;
        border: 1px solid var(--tt-log-border);
        line-height: 1.5;
    }

    /* ── Run result cards ── */
    .run-card {
        background: var(--tt-surface); border-radius: var(--tt-radius); padding: 14px 16px;
        margin-bottom: 10px; border: 1px solid var(--tt-border);
        border-left: 4px solid var(--tt-brand);
        box-shadow: var(--tt-shadow-sm);
    }
    .run-card.failed  { border-left-color: #ef4444; }
    .run-card.partial { border-left-color: #f59e0b; }
    /* Had no colour at all — white-on-white in the original bug. */
    .run-card-title   { font-weight: 600; font-size: 14px; margin-bottom: 6px; color: var(--tt-text); }
    .run-card-meta    { font-size: 12px; color: var(--tt-text-muted); }
    .run-card-badge   {
        display: inline-block; font-size: 11px; font-weight: 600;
        padding: 2px 8px; border-radius: 99px; margin-right: 6px;
    }
    .badge-green  { background: #d1fae5; color: #065f46; }
    .badge-amber  { background: #fef3c7; color: #92400e; }
    .badge-red    { background: #fee2e2; color: #991b1b; }

    /* ── Funnel (cohorts tab) ── */
    .tt-funnel-label { color: var(--tt-text); }
    .tt-funnel-pct   { color: var(--tt-text-muted); }
    .tt-funnel-track { background: var(--tt-track); }

    /* ── Buttons ──
       width:100% is load-bearing — the tabs lay buttons out in st.columns and
       rely on them filling the cell. Only the styling is changed. */
    .stButton>button {
        width: 100%; border-radius: var(--tt-radius-sm); font-weight: 600;
        font-size: 13.5px; padding: .5rem .9rem;
        font-family: var(--tt-font);
        transition: background .15s, border-color .15s, box-shadow .15s;
    }
    .stButton>button[kind="primary"] {
        background: var(--tt-brand) !important;
        border-color: var(--tt-brand) !important;
        color: var(--tt-brand-ink) !important;   /* was unset — label followed the theme */
        box-shadow: var(--tt-shadow-sm);
    }
    .stButton>button[kind="primary"]:hover {
        background: var(--tt-brand-hover) !important;
        border-color: var(--tt-brand-hover) !important;
        color: var(--tt-brand-ink) !important;
    }
    .stButton>button[kind="secondary"] {
        background: var(--tt-surface) !important;
        color: var(--tt-text) !important;
        border-color: var(--tt-border) !important;
    }
    .stButton>button[kind="secondary"]:hover {
        border-color: var(--tt-brand) !important;
        color: var(--tt-brand) !important;
    }

    /* ── Tables ── */
    .stDataFrame { border-radius: var(--tt-radius); overflow: hidden; }
    [data-testid="stDataFrame"] { border: 1px solid var(--tt-border); border-radius: var(--tt-radius); }

    /* ── Dividers / spacing rhythm ── */
    hr:not(.tulle-rule) { border: none; border-top: 1px solid var(--tt-border); margin: 1.4rem 0; }
    [data-testid="stCaptionContainer"] p { font-size: 12.5px; line-height: 1.5; }
"""


def build_css(mode: str) -> str:
    """The whole dashboard stylesheet, parameterised by palette.

    This replaces the stylesheet that used to be inlined in dashboard.py. The
    substantive difference from that original is the explicit `color:` rules — it
    set a background with no text colour, which is what caused the invisible text.
    """
    return (
        "<style>\n"
        + _root_vars(mode)
        + _STYLESHEET
        + _widget_paper_css(mode)
        + "\n</style>\n"
    )


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
