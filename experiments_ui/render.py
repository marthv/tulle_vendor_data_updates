"""Experiments tab v2 for the Tulle Admin Dash (FRAMEWORK.md section 7).

Usage in dashboard.py::

    from experiments_ui.render import render_experiments_tab_v2
    with tab_exp:
        render_experiments_tab_v2(snapshot_or_none, registry_dict, theme_mode=_theme_mode)

The whole tab is ONE self-contained HTML page (experiments.html) rendered in a
``streamlit.components.v1.html`` iframe with the snapshot JSON injected. Every interaction
(tabs, filters, drawer, sorting) is client-side, so clicks never cause a Streamlit rerun.

This module fetches nothing. If the engine snapshot is None (engine down, not deployed yet),
it builds a degraded snapshot from the registry alone: the engine's own build_snapshot with
no metric runner (spec checks, manual results[], health from dates), or, if even that fails,
a minimal date-only view. Every field is read defensively: the v1 tab KeyErrored on entries
without "name"; this one never raises on a malformed entry.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(HERE, "experiments.html")
SNAPSHOT_TOKEN = "/*__SNAPSHOT__*/null"
THEME_TOKEN = "/*__THEME__*/null"
THEMES = ("light", "sepia", "dark")
DEFAULT_HEIGHT = 1150


# --------------------------------------------------------------------------------------
# safe embedding
# --------------------------------------------------------------------------------------

def _clean(obj):
    """Make anything JSON-safe: NaN/inf -> None, unknown types -> str, keys -> str."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_clean(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    return str(obj)


def embed_json(obj):
    """JSON for inline <script>: escapes <, >, & and U+2028/9 so no string can close the tag."""
    s = json.dumps(_clean(obj), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def build_html(snapshot, theme_mode=None):
    """The tab page with the snapshot (and theme) injected."""
    html = _template()
    theme = theme_mode if theme_mode in THEMES else None
    if SNAPSHOT_TOKEN not in html:
        raise RuntimeError("experiments.html is missing the snapshot placeholder")
    html = html.replace(SNAPSHOT_TOKEN, embed_json(snapshot if isinstance(snapshot, dict) else {}), 1)
    html = html.replace(THEME_TOKEN, embed_json(theme), 1)
    return html


# --------------------------------------------------------------------------------------
# degraded snapshot (registry only)
# --------------------------------------------------------------------------------------

def _date(v):
    try:
        return _dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _minimal_snapshot(registry, today):
    """Last-resort view: no engine import, dates only. Never raises."""
    exps, decided = [], []
    reg = registry if isinstance(registry, dict) else {}
    raw = reg.get("experiments") if isinstance(reg.get("experiments"), list) else []
    decided_status = {"shipped", "reverted", "shelved", "inconclusive", "concluded", "ended", "decided"}
    for e in raw:
        if not isinstance(e, dict):
            continue
        name = e.get("name") or e.get("what") or e.get("id") or "(unnamed)"
        status = str(e.get("status") or "").lower()
        if status in decided_status:
            res = e.get("results") if isinstance(e.get("results"), list) else []
            last = res[-1] if res and isinstance(res[-1], dict) else {}
            decided.append({"id": e.get("id"), "name": name, "status": status,
                            "outcome": e.get("outcome") or (status if status in ("shipped", "reverted", "shelved", "inconclusive") else None),
                            "decided_on": e.get("decided_on") or e.get("end_date"),
                            "decision": e.get("decision"), "takeaway": e.get("decision") or last.get("note"),
                            "themes": e.get("themes") if isinstance(e.get("themes"), list) else [],
                            "start_date": e.get("start_date"), "end_date": e.get("end_date")})
            continue
        sd = _date(e.get("start_date"))
        rd = _date(e.get("review_date"))
        row = {"id": e.get("id"), "name": name, "status": status,
               "lifecycle": status if status in ("live", "planned", "paused", "draft") else "unknown",
               "themes": e.get("themes") if isinstance(e.get("themes"), list) else [],
               "what_changed": e.get("what_changed") or e.get("what"),
               "hypothesis": e.get("hypothesis") or e.get("goal"),
               "start_date": e.get("start_date"), "review_date": e.get("review_date"),
               "results": [r for r in (e.get("results") or []) if isinstance(r, dict)],
               "spec_issues": [], "overlapping_changes": [], "health": None, "stale": False}
        if status == "live" and sd:
            row["days_running"] = (today - sd).days
            if rd and today > rd:
                row.update(health="needs_call", action="Owner call due", overdue_days=(today - rd).days,
                           stale=(today - rd).days >= 3,
                           reason=f"Review date {rd.isoformat()} passed {(today - rd).days} day(s) ago")
            else:
                row.update(health="on_track", action="Keep running", reason="Health from dates only")
        exps.append(row)
    live = [r for r in exps if r.get("health")]
    return {"summary": {"needs_call": sum(r["health"] == "needs_call" for r in live),
                        "stale": sum(bool(r.get("stale")) for r in live),
                        "healthy": sum(r["health"] == "on_track" for r in live),
                        "guardrail": 0, "live": len(live), "decided": len(decided)},
            "action_queue": [{"id": r["id"], "name": r["name"], "health": r["health"], "stale": r["stale"],
                              "action": r.get("action"), "reason": r.get("reason")}
                             for r in live if r["health"] != "on_track"],
            "experiments": exps, "decided": decided,
            "changes": [c for c in (reg.get("changes") or []) if isinstance(c, dict)], "errors": []}


def degraded_snapshot(registry, today=None):
    """Snapshot from the registry only (no Xano). Uses the engine when importable."""
    today = _date(today) or _dt.datetime.now(_dt.timezone.utc).date()
    note = "Engine snapshot unavailable: showing the registry only (spec, manual readouts, dates). No live numbers."
    try:
        from experiments_engine.snapshot import build_snapshot
        snap = build_snapshot(registry if isinstance(registry, dict) else {}, metric_runner=None,
                              history_store=None, today=today)
        # without a runner, auto experiments carry "no metric runner configured" errors; that
        # is the expected degraded state, not something to alarm on
        snap["errors"] = [x for x in snap.get("errors") or []
                          if "no metric runner" not in str(x.get("error"))]
    except Exception as exc:  # engine missing or broken: dates-only view
        snap = _minimal_snapshot(registry, today)
        note += f" (engine import failed: {type(exc).__name__})"
    snap["degraded"] = True
    snap["degraded_note"] = note
    snap.setdefault("generated_at", _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"))
    return snap


def _load_registry_file():
    for p in (os.path.join(os.path.dirname(HERE), "experiments", "experiment_log.json"),):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return {}


# --------------------------------------------------------------------------------------
# Streamlit entry point
# --------------------------------------------------------------------------------------

def attach_history(snapshot, history_by_id):
    """Put readout history rows (engine GET /experiments/{id}/history) on each experiment row
    as ``history`` [{date, p_beat, n_control, n_treatment}], unless the row already has it."""
    if not isinstance(snapshot, dict) or not isinstance(history_by_id, dict):
        return snapshot
    for row in snapshot.get("experiments") or []:
        if not isinstance(row, dict) or row.get("history"):
            continue
        rows = history_by_id.get(row.get("id"))
        if isinstance(rows, dict):
            rows = rows.get("rows") or rows.get("history")
        if isinstance(rows, list):
            row["history"] = [{"date": r.get("date"), "p_beat": r.get("p_beat"),
                               "n_control": r.get("n_control"), "n_treatment": r.get("n_treatment")}
                              for r in rows if isinstance(r, dict) and r.get("date")]
    return snapshot


def render_experiments_tab_v2(snapshot_or_none=None, registry_fallback=None, theme_mode=None,
                              height=DEFAULT_HEIGHT, history_by_id=None, degraded_reason=None):
    """Render the Experiments tab.

    snapshot_or_none   engine snapshot dict (FRAMEWORK section 5), or None
    registry_fallback  parsed experiment_log.json (dict); read from disk when None
    theme_mode         "light" | "sepia" | "dark" (the dash's theme.py mode); read from
                       st.session_state["tulle_theme_mode"] when None
    height             iframe height in px; the page scrolls inside it and the detail
                       drawer is pinned to the frame, so a fixed height is deliberate
    history_by_id      optional {exp_id: [history rows]} for the drawer trend chart (the
                       engine snapshot does not inline history; see attach_history)
    degraded_reason    why there is no engine snapshot (e.g. "engine not configured"); shown in
                       the in-page banner so the reason sits next to the warning, not above the frame
    """
    import streamlit as st
    import streamlit.components.v1 as components

    if theme_mode is None:
        try:
            theme_mode = st.session_state.get("tulle_theme_mode")
        except Exception:
            theme_mode = None
    snap = snapshot_or_none if isinstance(snapshot_or_none, dict) and snapshot_or_none else None
    if snap is None:
        reg = registry_fallback if isinstance(registry_fallback, dict) else _load_registry_file()
        snap = degraded_snapshot(reg)
        if degraded_reason:
            prior = str(snap.get("degraded_note") or "")
            snap["degraded_note"] = (f"No live numbers: {str(degraded_reason).rstrip('.')}. Showing the registry "
                                     "only (spec, hand-written readouts, dates), so nothing here is a measured result."
                                     + (prior[prior.index(" (engine import failed"):] if " (engine import failed" in prior else ""))
    if history_by_id:
        snap = attach_history(dict(snap), history_by_id)
    try:
        html = build_html(snap, theme_mode)
    except Exception as exc:
        st.error(f"Experiments tab could not render: {type(exc).__name__}: {exc}")
        return
    h = int(height or DEFAULT_HEIGHT)
    # st.iframe (Streamlit >= 1.52-ish) replaces the deprecated components.v1.html. A fixed
    # height is used on purpose: the page scrolls inside the frame, so the slide-over drawer
    # stays pinned to the visible frame instead of opening off-screen in a very tall iframe.
    if hasattr(st, "iframe"):
        try:
            st.iframe(html, height=h)
            return
        except TypeError:
            pass
    components.html(html, height=h, scrolling=True)
