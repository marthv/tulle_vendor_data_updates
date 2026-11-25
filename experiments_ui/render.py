"""Experiments tab v2 for the Tulle Admin Dash (FRAMEWORK.md section 7).

Usage in dashboard.py::

    from experiments_ui.render import render_experiments_tab_v2
    with tab_exp:
        render_experiments_tab_v2(snapshot_or_none, registry_dict, theme_mode=_theme_mode)

The whole tab is ONE self-contained HTML page (component/index.html). By default it runs as a
bidirectional Streamlit custom component (declare_component, path=component/): the snapshot goes
in as an argument, and writing a note in the drawer sends the note back as the component value,
which is the only thing that triggers a rerun. Browsing (tabs, filters, drawer, sorting,
calendar, buckets) stays client-side. EXP_TAB_COMPONENT=0 (or a declare_component failure)
falls back to the old read-only path: the snapshot injected into the same page in an iframe.

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
COMPONENT_DIR = os.path.join(HERE, "component")
TEMPLATE_PATH = os.path.join(COMPONENT_DIR, "index.html")
COMPONENT_NAME = "tulle_experiments_tab"
NOTE_KINDS = ("note", "decision", "next_step")
NOTE_CALLS = ("ship", "revert", "shelve", "inconclusive", "keep_running", "resume")
NOTE_MAX_CHARS = 2000   # the engine refuses longer (notes.MAX_TEXT)
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
        raise RuntimeError("component/index.html is missing the snapshot placeholder")
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


def _v2_on():
    return str(os.environ.get("EXP_TAB_V2", "")).strip().lower() in ("1", "on", "true", "yes")


def _split_like(e):
    a = e.get("assignment")
    return "launch_lock" in e or (isinstance(a, dict) and (str(a.get("type")) == "split" or a.get("salt") or a.get("rule") or a.get("split"))) \
        or "split" in str(e.get("design") or "").lower() \
        or str(e.get("design") or "").lower().startswith("a/b")


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
        if status in decided_status and _v2_on() and _split_like(e):
            decided.append({"id": e.get("id"), "name": name, "status": "decided", "outcome": None, "decided_on": e.get("decided_on"),
                            "takeaway": "Result not shown without the engine", "themes": []})
            continue
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
        if _v2_on() and _split_like(e):
            row.update(results=[], result_hidden=True, result_text="Result: hidden until its read date")
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


def snapshot_sig(snapshot):
    """Short content hash: the page re-renders only when the snapshot really changed."""
    import hashlib
    try:
        return hashlib.sha1(embed_json(snapshot).encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return ""


def clean_note(value):
    """Validate a note sent back by the page. Returns (exp_id, note_id, body) or None.

    body is what the engine's POST /experiments/{id}/notes takes: author is added by the caller
    (the logged-in dash user), never trusted from the page."""
    if not isinstance(value, dict) or value.get("type") != "note":
        return None
    nid, exp_id = str(value.get("note_id") or "")[:64], str(value.get("exp_id") or "")[:120]
    text = str(value.get("text") or "").strip()[:NOTE_MAX_CHARS]
    kind = value.get("kind") if value.get("kind") in NOTE_KINDS else "note"
    call = value.get("call") if value.get("call") in NOTE_CALLS else None
    if not nid or not exp_id or not text or (kind == "decision" and not call):
        return None
    body = {"kind": kind, "text": text, "client_id": nid}
    if kind == "decision":
        body["call"] = call
    return exp_id, nid, body


def handle_note_value(value, on_note, author, state):
    """Post a note from the page once. ``state`` is a dict kept in st.session_state
    (note_id -> {"ok", "error"}), so a rerun that hands back the same component value never
    double-posts. Returns True when something new was posted (the caller reruns)."""
    parsed = clean_note(value)
    if parsed is None:
        if isinstance(value, dict) and value.get("note_id") and value.get("note_id") not in state:
            state[str(value.get("note_id"))[:64]] = {"ok": False, "error": "empty note or missing call"}
            return True
        return False
    exp_id, nid, body = parsed
    if nid in state:
        return False
    if on_note is None:
        state[nid] = {"ok": False, "error": "notes are read-only here"}
        return True
    body["author"] = (" ".join(str(author or "dash user").split()) or "dash user")[:80]
    try:
        ok, err = on_note(exp_id, body)
    except Exception as exc:  # noqa: BLE001 - a failed post must show in the page, not crash the tab
        ok, err = False, f"{type(exc).__name__}"
    state[nid] = {"ok": bool(ok), "error": None if ok else (str(err)[:200] if err else "not saved")}
    # keep the last 30 so the args stay small
    for k in list(state)[:-30]:
        state.pop(k, None)
    return True


_COMPONENT = None


def _component():
    global _COMPONENT
    if _COMPONENT is None:
        import streamlit.components.v1 as components
        _COMPONENT = components.declare_component(COMPONENT_NAME, path=COMPONENT_DIR)
    return _COMPONENT


def render_experiments_tab_v2(snapshot_or_none=None, registry_fallback=None, theme_mode=None,
                              height=DEFAULT_HEIGHT, history_by_id=None, degraded_reason=None,
                              on_note=None, author=None):
    """Render the Experiments tab.

    snapshot_or_none   engine snapshot dict (FRAMEWORK section 5), or None
    registry_fallback  parsed experiment_log.json (dict); read from disk when None
    theme_mode         "light" | "sepia" | "dark" (the dash's theme.py mode); read from
                       st.session_state["tulle_theme_mode"] when None
    height             starting iframe height in px; once loaded the page asks for a frame as
                       tall as its content (Streamlit component iframes don't scroll), and
                       places the detail drawer over the part of the frame that is on screen
    history_by_id      optional {exp_id: [history rows]} for the drawer trend chart (the
                       engine snapshot does not inline history; see attach_history)
    degraded_reason    why there is no engine snapshot (e.g. "engine not configured"); shown in
                       the in-page banner so the reason sits next to the warning, not above the frame
    on_note            callable(exp_id, body) -> (ok, error) that saves a note (the engine's
                       POST /experiments/{id}/notes); None makes notes read-only
    author             name of the logged-in dash user, stamped on every note
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
    if snap.get("exp_tab_v2") or _v2_on():
        from experiments_ui import cards
        return cards.render(snap, theme_mode, on_note=on_note, author=author)
    if history_by_id:
        snap = attach_history(dict(snap), history_by_id)
    degraded = bool(snap.get("degraded"))
    if os.environ.get("EXP_TAB_COMPONENT", "1") != "0":
        try:
            state = st.session_state.setdefault("_exp_note_results", {})
            can_write = on_note is not None and not degraded
            if degraded:
                why = "The experiments engine is unreachable, so notes can't be saved right now. Try again in a few minutes."
            elif on_note is None:
                why = "Notes are read-only here."
            else:
                why = ""
            value = _component()(snapshot=_clean(snap), theme=theme_mode if theme_mode in THEMES else None,
                                 can_write=can_write, write_note=why, note_results=dict(state),
                                 sig=snapshot_sig(snap), key="tulle_experiments_tab", default=None,
                                 height=int(height or DEFAULT_HEIGHT))
            if handle_note_value(value, on_note if can_write else None, author, state):
                st.rerun()
            return
        except Exception as exc:  # noqa: BLE001 - fall back to the read-only iframe
            if type(exc).__name__ in ("RerunException", "StopException"):
                raise
    try:
        html = build_html(snap, theme_mode)
    except Exception as exc:
        st.error(f"Experiments tab could not render: {type(exc).__name__}: {exc}")
        return
    h = int(height or DEFAULT_HEIGHT)
    # st.iframe (Streamlit >= 1.52-ish) replaces the deprecated components.v1.html. This is
    # only the starting height: the page resizes its (same-origin) frame to the content
    # height and places the drawer over the visible part of the frame itself.
    if hasattr(st, "iframe"):
        try:
            st.iframe(html, height=h)
            return
        except TypeError:
            pass
    components.html(html, height=h, scrolling=True)
