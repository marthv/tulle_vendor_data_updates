---
name: project_dashboard_theme_modes
description: Railway admin dashboard invisible-text bug (dark-mode browsers) FIXED + Light/Sepia/Dark modes shipped in 65c1a6f
metadata: 
  node_type: memory
  type: project
  originSessionId: 234ada71-2375-4f80-b319-e7a248c3f43b
  modified: 2026-07-29T04:57:55.570Z
---

2026-07-29 SHIPPED to `main` of tulle_vendor_data_updates as **`65c1a6f`** (Railway
auto-deploys main). Reported by Des + one other account: the Streamlit admin dashboard
rendered a LIGHT background with WHITE text, readable only by selecting the page. Never
reproduced for Vivek.

**Root cause:** the repo had **no `.streamlit/config.toml`**, so Streamlit auto-detected its
theme from the browser's `prefers-color-scheme`. `dashboard.py` forced `.stApp { background:
#f8f9fa }` with **no companion `color:`**, and there was no global text-colour rule and zero
uses of `var(--text-color)` anywhere. A dark-mode browser therefore got Streamlit's dark-base
text (~#fafafa) on that light canvas. Everything without its own explicit colour vanished:
headings, `st.metric`, `st.caption`, tab labels, widget labels, the "What is this dashboard?"
expander body, dataframe cells. `dashboard.py` also hides `stToolbar`, which removed
Streamlit's own theme picker — so affected users had **no workaround at all**.

**Fix:** `.streamlit/config.toml` pins `base="light"` + brand colours (this is the part that
actually fixes it, and it replaces Streamlit's default red accent with Tulle green); new
`theme.py` holds Light/Sepia/Dark palettes as CSS custom properties plus the former inline
stylesheet, now with explicit `color` on `.stApp` AND on the elements Streamlit paints
directly (they do NOT inherit): `stMetricValue/Label`, `h1`–`h6`, `stMarkdownContainer`,
`stWidgetLabel`, expander summaries, tab labels, captions. Header switcher persisted via a
`tulle_theme` cookie with `?theme=` fallback; `theme.preserve_on_clear()` replaced three
`st.query_params.clear()` calls in the OAuth callback that would have dropped the choice on
every sign-in. Also fixed background-without-colour sites: `.run-card-title` (white-on-white),
primary button label, the venue-pricing heatmap Styler, login title, cohorts funnel label.
`requirements.txt` streamlit pinned `>=1.40,<2.0` (was unbounded, no lockfile → theming could
drift on any redeploy).

**KEY CONSTRAINT — do not "fix" by changing the config base:** `st.dataframe` (12 uses) and
`st.line_chart`/`st.bar_chart` render to canvas/Vega and read colours from
`.streamlit/config.toml`, **not CSS**. So in Sepia/Dark they stay light and are deliberately
framed as white "paper" panels. The select dropdown likewise keeps light styling (Streamlit
1.60 does not expose `data-baseweb="select"`, so that CSS rule is inert) — cosmetic only.

**Verification technique worth reusing:** local `streamlit` import was broken by a
starlette/streamlit mismatch in the global Python env, so testing used a throwaway venv +
a gitignored `_theme_preview.py` harness importing only `theme.py` (avoids extract_core's
heavy deps) + headless Chrome driven over **CDP** for screenshots. Plain
`chrome --headless --screenshot` only captures Streamlit's loading skeleton — virtual time
never lets the websocket connect; you need CDP with a real `sleep` and
`--remote-allow-origins=*`. `Emulation.setEmulatedMedia` with `prefers-color-scheme: dark`
reproduced Des's environment: post-fix it renders pixel-identical to light mode. All palettes
pass WCAG AA, lowest ratio 5.07:1.

**Repo note:** this landed via cherry-pick onto `main` because the working branch
`perf/batch-api-routing` also carried two unrelated, unpushed commits (`0f03685` Batch API
routing, `f287f05` API-key scoping) that were deliberately NOT pushed. Those are still
unpushed on that branch. See [[reference_railway_admin_dashboard]].

## 2026-08-18 — visual redesign (commit `1cd3bc8`, theme.py only)

Requested as "the admin dash is abysmal, too difficult to follow". Diagnosed as
STRUCTURAL, not cosmetic: six tabs separated only by a thin underline, headings from
`##` to `#####` all rendering at near-identical weight, metrics/controls floating with
no container, and `max-width: 92vw` producing ~2300px paragraphs on a wide monitor.

Fixed **entirely in the shared stylesheet** — one file, zero logic touched, every tab
improves at once. That was deliberate: `dashboard.py` is 2,485 lines and the local
Streamlit cannot run, so a type scale in CSS makes the existing mixed heading depths
read as a hierarchy without editing hundreds of call sites.

Changes: type scale; sticky segmented tab bar (selection by fill, not underline); brand
rule beside `h3`; `st.metric` / expanders / alerts as bordered cards; width capped
1720px; Sora webfont with full fallback stack.

**`_root_vars()` + plain `_STYLESHEET` replaced the single giant f-string.** Every CSS
brace used to need doubling and one miss silently killed the whole sheet. Only the small
`:root` block interpolates now. Keep it that way.

**TRAP:** `display:flex` is now set explicitly on `.stTabs [data-baseweb="tab-list"]`.
The mock render caught the tabs stacking VERTICALLY — that rule now owns the element's
box (padding/radius/background), so inheriting Streamlit's default flex is not safe.

### Cheaper verification than the venv + CDP method above
The earlier note prescribes a throwaway venv + `_theme_preview.py` + headless Chrome over
CDP. **Not needed for pure-CSS work.** Faster: stub `sys.modules['streamlit']` with a
bare `types.ModuleType`, import `theme`, and render `build_css(mode)` into a static HTML
file that mimics Streamlit's DOM (`data-testid="stMetric"`, `[data-baseweb="tab-list"]`,
`stExpander`, `stAlert`, …), then plain `chrome --headless=new --screenshot`. No
websocket, so no CDP or virtual-time problem. Harness kept at
`scratchpad/mock.py`. Assert brace balance, palette emission and that every legacy class
(`metric-card`, `card-*`, `run-card*`, `badge-*`, `log-box`, `tulle-*`, `tt-funnel-*`)
still exists — those are used by dashboard.py, cohorts.py, roadmap_orders.py and
vendor_portal.py, and dropping one is silent.

**NOT changed:** `.streamlit/config.toml` (still `base="light"`), all palette values, and
the `st.dataframe`/chart canvas limitation — those still read colours from config.toml,
so the white-paper framing in sepia/dark stays.
