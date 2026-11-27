"""🧪 Experiments tab: the experiment registry, timeline, confound check and a significance check.

Source of truth is experiments/experiment_log.json in this repo (added 2026-10-25). Claude Code records
an experiment by editing that file and pushing to main; Railway redeploys the dashboard, and git keeps the
history of every read and decision. No Xano table or endpoint is involved, so this tab is read-only and
cheap: it reads one local file per rerun and makes no network calls.

Confounds are computed, not typed: any other experiment or logged change whose dates overlap an
experiment's window is flagged on that experiment. The significance check is a two-proportion z-test on
counts stored in results[].counts, or typed in by hand.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re

import altair as alt
import pandas as pd
import streamlit as st

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments", "experiment_log.json")
STATUSES = ["planned", "live", "reading", "concluded", "reverted"]


# ── Deep link (2026-11-27): <dash>/?exp=<EXPERIMENT_ID> opens this tab with that experiment open ──────────────
# The id survives Google sign-in in the OAuth `state` (dashboard.py), since the redirect_uri carries no params.
EXP_QUERY_KEY = "exp"
_STATE_PREFIX = "exp:"
_EXP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def clean_exp_id(value) -> str | None:
    """A safe experiment id from a URL value (ids look like EXP-2026-11-slug), else None."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    text = str(value or "").strip()
    return text if _EXP_ID_RE.match(text) else None


def exp_to_state(exp_id) -> str | None:
    """OAuth `state` value that carries the deep-linked id through Google sign-in."""
    e = clean_exp_id(exp_id)
    return f"{_STATE_PREFIX}{e}" if e else None


def exp_from_state(state) -> str | None:
    s = str(state or "")
    return clean_exp_id(s[len(_STATE_PREFIX):]) if s.startswith(_STATE_PREFIX) else None


def focus_exp_id() -> str | None:
    """The ?exp= id on the current URL (None when absent or not a valid id)."""
    try:
        return clean_exp_id(st.query_params.get(EXP_QUERY_KEY))
    except Exception:  # noqa: BLE001
        return None


def known_ids(registry=None, snapshot=None) -> dict:
    """{id: name} of every experiment in the registry and the engine snapshot."""
    out = {}
    for src in (registry, snapshot):
        if not isinstance(src, dict):
            continue
        for key in ("experiments", "decided"):
            for e in src.get(key) or []:
                if isinstance(e, dict) and e.get("id"):
                    out.setdefault(str(e["id"]), str(e.get("name") or e["id"]))
    return out


def focus_note(focus, ids) -> tuple[str, str] | None:
    """(kind, text) shown above the tab for a deep link: 'ok' names it, 'missing' says it isn't there."""
    if not focus:
        return None
    if focus in ids:
        return "ok", f"Opened from a link: **{ids[focus]}** (`{focus}`)."
    return "missing", (f"No experiment with id `{focus}` in the registry, so nothing is opened. "
                       "Showing every experiment; check the link's spelling.")


def _load() -> dict:
    with open(LOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _d(s):
    try:
        return dt.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _window(e, today):
    """(start, end) used for overlap. Planned experiments have no window yet."""
    s = _d(e.get("start_date"))
    if not s:
        return None
    return s, _d(e.get("end_date")) or today


def _overlaps(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


def two_proportion_test(n1, c1, n2, c2):
    """Control (n1, c1) vs treatment (n2, c2). Returns rates, relative lift, 95% CI of the difference and
    a two-sided p-value. None when the inputs can't support a test."""
    if min(n1, n2) <= 0 or not (0 <= c1 <= n1 and 0 <= c2 <= n2):
        return None
    p1, p2 = c1 / n1, c2 / n2
    pooled = (c1 + c2) / (n1 + n2)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se_pooled == 0:
        return None
    z = (p2 - p1) / se_pooled
    p_value = math.erfc(abs(z) / math.sqrt(2))
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    diff = p2 - p1
    return {
        "control_rate": p1, "treatment_rate": p2,
        "lift": (diff / p1) if p1 else None,
        "ci_low": diff - 1.96 * se, "ci_high": diff + 1.96 * se,
        "p_value": p_value, "significant": p_value < 0.05,
    }


def _verdict(r):
    if r is None:
        return "Not enough data for a test."
    lift = "n/a" if r["lift"] is None else f"{r['lift']:+.1%}"
    sig = "**significant** (p < 0.05)" if r["significant"] else "**not significant**"
    return (f"Control {r['control_rate']:.2%} vs treatment {r['treatment_rate']:.2%} · relative lift {lift} · "
            f"difference 95% CI {r['ci_low']:+.2%} to {r['ci_high']:+.2%} · p = {r['p_value']:.3f} → {sig}")


def _what(change):
    """Text of a logged change. Seven entries (2026-11-11..15) were written with "name"
    instead of "what" and the KeyError took every tab down - never index this directly."""
    return change.get("what") or change.get("name") or "(no description)"


def _render_legacy(focus=None):
    try:
        log = _load()
    except (OSError, ValueError) as exc:
        st.error(f"Could not read experiments/experiment_log.json: {exc}")
        return
    exps = log.get("experiments", [])
    changes = log.get("changes", [])
    today = dt.date.today()

    st.caption("Every experiment: goal, dates, results, confounds and significance. "
               "Source: `experiments/experiment_log.json` (edit + push to update).")

    groups = {"Running": ("planned", "live", "reading"), "Past": ("concluded", "reverted"), "All": tuple(STATUSES)}
    pick = st.segmented_control("Show", list(groups), default="Running", key="exp_view") or "All"
    shown = [e for e in exps if e.get("status") in groups[pick]]
    if focus:   # deep link: that experiment first and open, whatever the filter
        hit = [e for e in exps if e.get("id") == focus]
        shown = hit + [e for e in shown if e.get("id") != focus]

    # Timeline: experiments as bars, logged changes as dashed rules. Overlap = possible confound.
    rows = []
    for e in shown:
        w = _window(e, today)
        if w:
            rows.append({"experiment": e["name"][:60], "start": w[0], "end": w[1], "status": e["status"]})
    if rows:
        df = pd.DataFrame(rows)
        bars = alt.Chart(df).mark_bar(cornerRadius=3, height=14).encode(
            x=alt.X("start:T", title=None), x2="end:T",
            y=alt.Y("experiment:N", title=None, sort=None),
            color=alt.Color("status:N", legend=alt.Legend(orient="bottom", title=None), scale=alt.Scale(
                domain=STATUSES, range=["#CDD0D8", "#00A368", "#D97706", "#0D5C3A", "#C53030"])),
            tooltip=["experiment", "status", alt.Tooltip("start:T"), alt.Tooltip("end:T")],
        )
        ch = pd.DataFrame([{"date": _d(c["date"]), "what": _what(c)} for c in changes if _d(c.get("date"))])
        layers = [bars]
        if not ch.empty:
            layers.append(alt.Chart(ch).mark_rule(color="#7E8189", strokeDash=[4, 3]).encode(
                x="date:T", tooltip=["what", alt.Tooltip("date:T")]))
        st.altair_chart(alt.layer(*layers).properties(height=max(120, 26 * len(rows))), use_container_width=True)
        st.caption("Dashed lines = other changes shipped (hover). Anything overlapping an experiment can muddy its result.")
    elif not shown:
        st.info("Nothing here.")

    def _latest(e):
        r = e.get("results") or []
        return r[-1].get("note", "") if r else ""

    if shown:
        st.dataframe(pd.DataFrame([{
            "Experiment": e["name"], "Status": e["status"],
            "Dates": f"{e.get('start_date') or 'planned'} → {e.get('end_date') or ('open' if e.get('start_date') else '')}",
            "Latest result": _latest(e) or "—",
        } for e in shown]), use_container_width=True, hide_index=True)

    for e in shown:
        w = _window(e, today)
        with st.expander(f"{e['name']}  ·  {e['status']}", expanded=bool(focus) and e.get("id") == focus):
            st.markdown(f"**Goal:** {e.get('goal') or '—'}  \n**Primary metric:** {e.get('primary_metric') or '—'}"
                        + (f"  \n**Guardrail:** {e['guardrail']}" if e.get("guardrail") else "")
                        + f"  \n**Design:** {e.get('design') or '—'}"
                        + f"  \n**Dates:** {e.get('start_date') or 'planned'} → {e.get('end_date') or 'open'}"
                        + (f" (review {e['review_date']})" if e.get("review_date") else "")
                        + (f"  \n_{e['dates_note']}_" if e.get("dates_note") else ""))

            flagged = []
            if w:
                for o in exps:
                    ow = _window(o, today)
                    if o is not e and ow and _overlaps(w, ow):
                        flagged.append(f"Experiment running at the same time: {o['name']}")
                for c in changes:
                    cs, ce = _d(c.get("date")), _d(c.get("end_date")) or _d(c.get("date"))
                    if cs and _overlaps(w, (cs, ce)):
                        flagged.append(f"{c['date']}: {_what(c)}")
            noted = e.get("confounds_noted", [])
            if flagged or noted:
                st.markdown("**Possible confounds**")
                for x in noted:
                    st.markdown(f"- {x}")
                for x in flagged:
                    st.markdown(f"- {x} _(auto: overlapping dates)_")
            if e.get("risks"):
                st.markdown("**Risks:** " + "; ".join(e["risks"]))

            st.markdown("**Results**")
            if not e.get("results"):
                st.caption("No reads yet.")
            for r in e.get("results", []):
                st.markdown(f"- **{r.get('date', '')}** {r.get('note', '')}")
                cnt = r.get("counts")
                if cnt:
                    res = two_proportion_test(cnt["control"]["n"], cnt["control"]["conv"],
                                              cnt["treatment"]["n"], cnt["treatment"]["conv"])
                    st.markdown("  " + _verdict(res))
            if e.get("stat_sig"):
                st.caption(f"Significance noted: {e['stat_sig']}")

            with st.popover("Check significance"):
                st.caption("Two-proportion z-test (conversion-style metrics). Pre/post designs are not randomized: "
                           "treat a significant result as suggestive, and check the confounds above.")
                k = e["id"]
                a, b = st.columns(2)
                n1 = a.number_input("Control: visitors", min_value=0, step=1, key=f"{k}_n1")
                c1 = a.number_input("Control: conversions", min_value=0, step=1, key=f"{k}_c1")
                n2 = b.number_input("Treatment: visitors", min_value=0, step=1, key=f"{k}_n2")
                c2 = b.number_input("Treatment: conversions", min_value=0, step=1, key=f"{k}_c2")
                if n1 and n2:
                    st.markdown(_verdict(two_proportion_test(int(n1), int(c1), int(n2), int(c2))))

    concluded = [e for e in exps if e.get("status") in ("concluded", "reverted")]
    with st.expander("What we've learned, by theme"):
        if not concluded:
            st.caption("Nothing concluded yet.")
        for t in sorted({t for e in concluded for t in e.get("themes", [])}):
            st.markdown(f"**{t}**")
            for e in concluded:
                if t in e.get("themes", []):
                    st.markdown(f"- {e['name']} ({e['status']}): {e.get('decision') or _latest(e)}")


# ── Framework v2 (2026-11-19, experiments/FRAMEWORK.md) ─────────────────────────────────────────────────────
# The tab now renders one self-contained HTML view (experiments_ui/) from the experiments-engine snapshot.
# Every click happens inside the page; only saving a note (drawer > Notes) reruns the dash, to post it to the engine. The engine runs
# as its own Railway service (private network); if it is unreachable the view is built from the registry
# alone (spec + manual results), so the tab never breaks. EXP_TAB_V2=0 brings back the old tab;
# unset = this tab; 1 / on / true / yes (2026-11-25) = read lock + one card per test (FRAMEWORK section 14).
ENGINE_URL = os.environ.get("EXP_ENGINE_URL", "").rstrip("/")
ENGINE_KEY = os.environ.get("EXP_API_KEY", "")


@st.cache_data(ttl=120, show_spinner=False)
def _engine_snapshot(url: str, key: str):
    """(snapshot | None, note). Cached 2 min: st.tabs runs every tab's code on every rerun."""
    if not url or not key:
        return None, "engine not configured - showing the registry only"
    import requests
    try:
        r = requests.get(f"{url}/snapshot", params={"history": 30}, headers={"X-Exp-Key": key}, timeout=6)
        if r.status_code == 503:
            return None, "engine is warming up (first run) - showing the registry only"
        r.raise_for_status()
        return r.json(), ""
    except Exception as exc:  # noqa: BLE001 - the tab must render regardless
        return None, f"engine unreachable ({type(exc).__name__}) - showing the registry only"


def _post_note(exp_id: str, body: dict):
    """Save a note on the engine (private network). Returns (ok, error). On success the 2-minute
    snapshot cache is cleared so the rerun shows the note from the engine, not just locally."""
    if not ENGINE_URL or not ENGINE_KEY:
        return False, "engine not configured"
    import requests
    from urllib.parse import quote
    try:
        r = requests.post(f"{ENGINE_URL}/experiments/{quote(exp_id, safe='')}/notes", json=body,
                          headers={"X-Exp-Key": ENGINE_KEY}, timeout=6)
    except Exception as exc:  # noqa: BLE001
        return False, f"engine unreachable ({type(exc).__name__})"
    if r.status_code >= 300:
        try:
            detail = r.json().get("detail") or r.json().get("error")
        except Exception:  # noqa: BLE001
            detail = None
        return False, f"engine said {r.status_code}" + (f": {str(detail)[:120]}" if detail else "")
    _engine_snapshot.clear()
    return True, None


def _dash_author() -> str:
    """The logged-in dash user (dashboard.py keeps the Google login in session_state)."""
    name = st.session_state.get("user_name") or ""
    email = st.session_state.get("user_email") or ""
    if name and email and name != email:
        return name
    return name or email or "dash user"


def render_experiments_tab(focus=None):
    """focus: an experiment id from ?exp= (dashboard.py); that experiment opens at the top."""
    focus = clean_exp_id(focus)
    if os.environ.get("EXP_TAB_V2", "1") == "0":
        if focus:
            try:
                ids = known_ids(_load())
            except (OSError, ValueError):
                ids = {}
            _show_focus_note(focus, ids)
            if focus not in ids:
                focus = None
        return _render_legacy(focus)
    from experiments_ui.render import render_experiments_tab_v2
    snap, note = _engine_snapshot(ENGINE_URL, ENGINE_KEY)
    # the reason goes into the page's own degraded banner (one warning, not two)
    reason = note.split(" - ")[0] if note else None
    history = (snap or {}).pop("history_by_id", None) if isinstance(snap, dict) else None
    reg = None
    try:
        reg = _load()
    except (OSError, ValueError):
        pass
    if focus:
        ids = known_ids(reg, snap if isinstance(snap, dict) else None)
        _show_focus_note(focus, ids)
        if focus not in ids:
            focus = None
    render_experiments_tab_v2(dict(snap) if snap else None, reg, history_by_id=history,
                              degraded_reason=None if snap else reason,
                              on_note=_post_note if snap else None, author=_dash_author(), focus=focus)


def _show_focus_note(focus, ids):
    n = focus_note(focus, ids)
    if not n:
        return
    if n[0] == "ok":
        st.caption(n[1])
    else:
        st.info(n[1])
