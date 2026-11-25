"""EXP_TAB_V2 Experiments tab: one plain-English card per test (experiments/CARDS.md, FRAMEWORK.md section 14).

Draws ``row["card"]`` from the snapshot (experiments_engine/v2_cards.py). It computes nothing and fetches nothing.
Colour is used for one thing only: the health of the test (green / amber / red). Results and prices are plain ink.
"""
from __future__ import annotations

import html as _html
import re
import secrets

from . import render as _r

LIGHT_WORDS = {"green": "Healthy", "amber": "Needs a look", "red": "Problem"}
_ORDER = {"red": 0, "amber": 1, "green": 2, None: 3}
NOTE_LABELS = {"note": "Note", "next_step": "Next step", "decision": "Decision"}

CSS = """
:root{--bg:#FAF8F4;--surface:#FFF;--line:#E8EAEE;--ink:#18191A;--ink2:#2E3035;--ink3:#52555C;--ink4:#6E7179;
--strip:#F4F5F7;--green:#0D7A50;--green-t:#EDF9F3;--amber:#8A4B08;--amber-t:#FEF3C7;--red:#9B2C2C;--red-t:#FEE2E2;
--d:"Lora",Georgia,serif;--u:"Sora",system-ui,-apple-system,"Segoe UI",sans-serif;color-scheme:light}
:root[data-theme=sepia]{--bg:#F7F1E3;--surface:#FDF9F0;--line:#E6DAC2;--ink:#33291C;--ink2:#3F3424;--ink3:#6B5D49;
--ink4:#76684F;--strip:#F3EBDA}
:root[data-theme=dark]{--bg:#15181C;--surface:#1E2228;--line:#2F353E;--ink:#E8EAEE;--ink2:#D5D8DE;--ink3:#B4B8C0;
--ink4:#9DA1AA;--strip:#262B33;--green:#7FE0B0;--green-t:#143024;--amber:#F2C47A;--amber-t:#33270F;--red:#F4A3A3;
--red-t:#3A1A1A;color-scheme:dark}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 var(--u);-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:20px 24px 40px}
h1{font:600 24px/1.25 var(--d);margin:0 0 2px}
h2{font:600 18px/1.3 var(--d);margin:26px 0 10px}
.sub{color:var(--ink3);margin:0 0 4px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,430px),1fr));gap:14px;align-items:start}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;min-width:0;
overflow-wrap:anywhere}
.top{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:6px}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;border-radius:999px;padding:2px 10px;
background:var(--strip);color:var(--ink3)}
.dot{width:9px;height:9px;border-radius:50%;background:currentColor;flex:none}
.green{color:var(--green)}.amber{color:var(--amber)}.red{color:var(--red)}
.pill.green{background:var(--green-t)}.pill.amber{background:var(--amber-t)}.pill.red{background:var(--red-t)}
.day{font-size:12px;color:var(--ink3)}
.card h3{font:600 16px/1.35 var(--d);margin:0 0 10px}
dl{margin:0}dt{font-size:12px;color:var(--ink4);margin-top:9px}dd{margin:1px 0 0;color:var(--ink2)}
.arms{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:4px}
.arm{border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-width:0}
.arm b{font-size:17px;font-variant-numeric:tabular-nums;font-weight:600}
.arm span{display:block;font-size:12px;color:var(--ink3)}
.result{margin-top:12px;border:1px solid var(--line);border-radius:8px;padding:9px 12px;font-weight:600;color:var(--ink)}
.small{font-size:12.5px;color:var(--ink3);margin:6px 0 0}
.checks{list-style:none;margin:10px 0 0;padding:0}
.checks li{display:flex;gap:8px;margin-top:5px;font-size:12.5px;color:var(--ink3)}
.checks .dot{margin-top:5px}
.checks b{color:var(--ink2);font-weight:600}
.strip{background:var(--strip);border-radius:8px;padding:8px 12px;margin-top:10px;font-size:12.5px;color:var(--ink2)}
.ov+.ov{border-top:1px solid var(--line);margin-top:6px;padding-top:6px}
details{margin-top:10px}summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--ink2)}
.tbl{width:100%;border-collapse:collapse;margin-top:6px;font-size:12.5px;table-layout:fixed}
.tbl td,.tbl th{border-top:1px solid var(--line);padding:6px 6px 6px 0;text-align:left;vertical-align:top;
overflow-wrap:anywhere;font-weight:400}
.tbl th{color:var(--ink4);font-size:12px}
.note{border-top:1px solid var(--line);padding:6px 0;font-size:12.5px;color:var(--ink2);white-space:pre-wrap}
.plainrow{border-top:1px solid var(--line);padding:9px 0;font-size:13px;color:var(--ink2);overflow-wrap:anywhere}
.warn{background:var(--amber-t);color:var(--amber);border-radius:8px;padding:9px 12px;margin:10px 0}
@media (max-width:560px){.wrap{padding:14px 12px 32px}.card{padding:14px}h1{font-size:21px}
.tbl,.tbl tbody,.tbl tr,.tbl td{display:block;width:100%}.tbl tr.h{display:none}.tbl tr{border-top:1px solid var(--line);
padding:6px 0}.tbl td{border:0;padding:1px 0}.tbl.two,.tbl.two tbody{display:table;width:100%}
.tbl.two tr{display:table-row}.tbl.two td,.tbl.two th{display:table-cell;border-top:1px solid var(--line);padding:6px 6px 6px 0;width:auto}}
"""

FIT_JS = """
(function(){function fe(){try{var f=window.frameElement;return f&&f.ownerDocument?f:null}catch(e){return null}}
function fit(){var f=fe();if(!f)return;var h=Math.ceil(document.querySelector('.wrap').getBoundingClientRect().height)+4;
if(Math.abs(f.offsetHeight-h)>2)f.style.height=h+'px'}
fit();window.addEventListener('load',fit);window.addEventListener('resize',fit);document.addEventListener('toggle',fit,true);
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(fit)})();
"""


def _e(v):
    return _html.escape(str(v if v is not None else ""), quote=True)


def _row(label, value):
    return f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" if value else ""


def _cap(v):
    s = str(v or "").strip()
    return s[:1].upper() + s[1:] if re.match(r"[a-z]+[ ,.]", s) else s


def _changed(card):
    s = _cap(card.get("changed"))
    if len(s) <= 300:
        return _row("What changed", s)
    cut = s[:280].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f'<dt>What changed</dt><dd>{_e(cut)}…<details><summary>Full text</summary>{_e(s)}</details></dd>'


def _n(v):
    return f"{int(v):,}"


def _arms(card):
    cells = []
    for a in card.get("arms") or []:
        got = "not counted yet" if a.get("n") is None else _n(a["n"])
        exp = f"about {_n(a['expected'])} expected by now" if a.get("expected") is not None else ""
        cells.append(f'<div class="arm"><span>{_e(a["label"])}</span><b>{_e(got)}</b><span>{_e(exp)}</span></div>')
    if not cells:
        return ""
    return ('<dt>Couples in each version</dt><dd><div class="arms">' + "".join(cells) + "</div>" +
            (f'<p class="small">{_e(card["sample_text"])}</p>' if card.get("sample_text") else "") + "</dd>")


def _cells(cells):
    def cell(k):
        c = cells.get(k) or {}
        if not c.get("n"):
            return "0 couples"
        return f"{c['conv']} of {_n(c['n'])} ({c['conv'] / c['n'] * 100:.1f}%)"
    return ('<table class="tbl two"><tr><th></th><th>Other test: new</th><th>Other test: old</th></tr>'
            f'<tr><td>This test: new</td><td>{cell("treatment|treatment")}</td><td>{cell("treatment|control")}</td></tr>'
            f'<tr><td>This test: old</td><td>{cell("control|treatment")}</td><td>{cell("control|control")}</td></tr></table>')


def _overlaps(card):
    rows = card.get("overlaps") or []
    out = []
    for o in rows:
        out.append(f'<div class="ov">{_e(o.get("text"))}' + (f'<br>{_e(o["note"])}' if o.get("note") else "") +
                   (_cells(o["cells"]) if o.get("cells") else "") + "</div>")
    return f'<div class="strip">{"".join(out)}</div>' if out else ""


def _history(card):
    rows = card.get("history") or []
    if not rows:
        return ""
    trs = []
    for r in rows:
        when = f"{r.get('from') or 'not started'}" + (f" to {r['to']}" if r.get("to") else (" to now" if r.get("from") else ""))
        trs.append(f"<tr><td>{_e(r.get('version'))}</td><td>{_e(when)}</td><td>{_e(r.get('outcome'))}</td></tr>")
    return (f'<details><summary>Earlier versions and what is next ({len(rows)}, one test)</summary>'
            '<table class="tbl"><tr class="h"><th>Version</th><th>Dates</th><th>What happened</th></tr>' + "".join(trs) +
            "</table></details>")


def _notes(rows):
    if not rows:
        return ""
    items = "".join(f'<div class="note"><b>{_e(NOTE_LABELS.get(n.get("kind"), "Note"))}</b>'
                    f'{" (" + _e(str(n["call"]).replace("_", " ")) + ")" if n.get("call") else ""} · {_e(n.get("author"))} · '
                    f'{_e(str(n.get("ts") or "")[:10])}<br>{_e(n.get("text"))}</div>' for n in reversed(rows[-8:]))
    return f"<details><summary>Notes ({len(rows)})</summary>{items}</details>"


def card_html(row, notes=None):
    card = row.get("card") or fallback_card(row)
    light = card.get("light") if card.get("light") in LIGHT_WORDS else None
    pill = (f'<span class="pill {light}"><i class="dot"></i>{LIGHT_WORDS[light]}</span>' if light else
            f'<span class="pill">{_e(str(card.get("status") or "").capitalize())}</span>')
    checks = "".join(f'<li><i class="dot {_e(c.get("colour"))}"></i><span><b>{_e(c.get("label"))}</b>'
                     f'{": " + _e(c["text"]) if c.get("text") else ""}</span></li>' for c in card.get("checks") or [])
    read = card.get("read_date")
    body = (_changed(card) + _row("Who counts", _cap(card.get("who"))) +
            _row("Measured by", _cap(card.get("measure"))) + _arms(card) +
            _row(card.get("read_label") or "Read date", read or "not set"))
    nxt = card.get("next")
    pend = row.get("decision_pending")
    return (f'<article class="card" id="{_e(card.get("id"))}"><div class="top">{pill}<span class="day">{_e(card.get("day_text"))}'
            f'</span></div><h3>{_e(card.get("name") or card.get("id"))}</h3><dl>{body}</dl>'
            f'<div class="result">{_e(card.get("result_text"))}</div>'
            + (f'<p class="small">{_e(card["payments_text"])}</p>' if card.get("payments_text") else "")
            + (f'<p class="small">Next: {_e(nxt)}</p>' if nxt else "")
            + (f'<p class="small">Decided in the dash ({_e(str(pend.get("call")).replace("_", " "))}), registry update pending.</p>'
               if isinstance(pend, dict) else "")
            + (f'<ul class="checks">{checks}</ul>' if checks else "") + _overlaps(card) + _history(card) + _notes(notes)
            + "</article>")


def fallback_card(row):
    """A row without a card (older engine, or a row the engine could not build): dates only, never a result."""
    hidden = row.get("result_hidden")
    return {"id": row.get("id"), "name": row.get("name"), "status": row.get("status"), "changed": row.get("what_changed"),
            "light": None, "day_text": f"Day {row['days_running']}" if row.get("days_running") is not None else "",
            "read_date": row.get("read_date") or row.get("review_date"), "read_label": "Read date" if hidden else "Review date",
            "result_text": row.get("result_text") if hidden else "Result: not available from this engine version"}


def _scorecard(snap):
    rows = (((snap.get("scorecards") or {}) or {}).get("planner")) or []
    if not rows:
        return ""

    def v(a):
        return f"{a['conv']} of {a['n']} ({a['conv'] / a['n'] * 100:.0f}%)" if isinstance(a, dict) and a.get("n") else "no data"
    trs = "".join(f"<tr><td>{_e(r.get('label'))}</td><td>{_e(v(r.get('after')))}</td><td>{_e(v(r.get('before')))}</td></tr>"
                  for r in rows)
    return ('<details><summary>Planner scorecard (last 7 days, the 7 before; not a test)</summary><table class="tbl">'
            f'<tr class="h"><th></th><th>Last 7 days</th><th>7 days before</th></tr>{trs}</table></details>')


def _unlogged(snap):
    rows = ((snap.get("change_feed") or {}).get("unlogged")) or []
    if not rows:
        return ""
    items = "".join(f'<div class="plainrow">{_e(str(r.get("ts") or "")[:16].replace("T", " "))} UTC · {_e(r.get("title"))}</div>'
                    for r in rows[:30] if isinstance(r, dict))
    return f"<details><summary>Shipped changes not in the registry ({len(rows)})</summary>{items}</details>"


def build_html(snapshot, theme_mode=None):
    snap = snapshot if isinstance(snapshot, dict) else {}
    rows = [r for r in snap.get("experiments") or [] if isinstance(r, dict) and not r.get("folded_into")]
    notes = snap.get("notes_by_id") or {}

    def light(r):
        return (r.get("card") or {}).get("light")
    running = sorted([r for r in rows if r.get("lifecycle") not in ("planned", "draft")],
                     key=lambda r: (_ORDER.get(light(r), 3), str((r.get("card") or {}).get("read_date") or "9"), str(r.get("id"))))
    planned = [r for r in rows if r.get("lifecycle") in ("planned", "draft")]
    decided = [d for d in snap.get("decided") or [] if isinstance(d, dict) and not d.get("folded_into")]
    decided.sort(key=lambda d: str(d.get("decided_on") or d.get("end_date") or ""), reverse=True)
    counts = ", ".join(f"{sum(light(r) == k for r in running)} {w}" for k, w in
                       (("red", "with a problem"), ("amber", "to look at"), ("green", "healthy")))
    when = str(snap.get("generated_at") or "")[:16].replace("T", " ")
    parts = ['<h1>Experiments</h1>',
             f'<p class="sub">{len(running)} running: {counts}. Colour is the health of the test, never its result.</p>',
             f'<p class="sub">Numbers as of {_e(when)} UTC. A split test is read once, on its read date; its result stays '
             'hidden until then.</p>']
    if snap.get("degraded"):
        parts.append(f'<div class="warn">{_e(snap.get("degraded_note") or "Engine not reachable: registry only.")}</div>')
    parts.append("<h2>Running</h2>" + (f'<div class="grid">{"".join(card_html(r, notes.get(r.get("id"))) for r in running)}</div>'
                                       if running else '<p class="sub">Nothing is running.</p>'))
    if planned:
        parts.append(f'<h2>Planned</h2><div class="grid">{"".join(card_html(r, notes.get(r.get("id"))) for r in planned)}</div>')
    if decided:
        parts.append(f"<details><summary>Decided ({len(decided)})</summary>" + "".join(
            f'<div class="plainrow"><b>{_e(d.get("name") or d.get("id"))}</b><br>{_e(d.get("outcome") or d.get("status"))}'
            f'{" · " + _e(d.get("decided_on")) if d.get("decided_on") else ""}'
            f'{"<br>" + _e(str(d.get("takeaway"))[:400]) if d.get("takeaway") else ""}</div>' for d in decided) + "</details>")
    parts += [_scorecard(snap), _unlogged(snap)]
    theme = theme_mode if theme_mode in _r.THEMES else "light"
    return (f'<!doctype html><html lang="en" data-theme="{theme}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Experiments</title>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lora:wght@600&family=Sora:wght@400;600&display=swap">'
            f'<style>{CSS}</style></head><body><div class="wrap">{"".join(parts)}</div><script>{FIT_JS}</script></body></html>')


def safe_snapshot(snap):
    """The snapshot, locked here too when the engine that built it did not have the switch on."""
    if snap.get("exp_tab_v2"):
        return snap
    from experiments_engine import v2
    return v2.lock_snapshot(snap, _r._load_registry_file())


def note_form(st, snap, on_note, author):
    """Native Streamlit form: add a note, next step or decision to one test."""
    rows = [r for r in snap.get("experiments") or [] if isinstance(r, dict) and r.get("id") and not r.get("folded_into")]
    if on_note is None or snap.get("degraded") or not rows:
        return
    with st.expander("Add a note or a decision"):
        with st.form("exp_v2_note", clear_on_submit=True):
            row = st.selectbox("Test", rows, format_func=lambda r: str(r.get("name") or r.get("id")))
            kind = st.selectbox("Kind", list(_r.NOTE_KINDS), format_func=lambda k: NOTE_LABELS.get(k, k))
            call = st.selectbox("Decision (only for a decision)", ["", *_r.NOTE_CALLS],
                                format_func=lambda c: c.replace("_", " ") or "none")
            text = st.text_area("Note", max_chars=_r.NOTE_MAX_CHARS)
            sent = st.form_submit_button("Save")
        if sent:
            parsed = _r.clean_note({"type": "note", "note_id": "n" + secrets.token_hex(8), "exp_id": row.get("id"),
                                    "kind": kind, "call": call or None, "text": text})
            if parsed is None:
                st.error("Write a note, and pick a decision if this is a decision.")
                return
            exp_id, _nid, body = parsed
            body["author"] = (" ".join(str(author or "dash user").split()) or "dash user")[:80]
            try:
                ok, err = on_note(exp_id, body)
            except Exception as exc:  # noqa: BLE001
                ok, err = False, type(exc).__name__
            if ok:
                st.rerun()
            st.error(f"Not saved: {err or 'unknown error'}")


def render(snapshot, theme_mode=None, on_note=None, author=None, height=1150):
    import streamlit as st
    if theme_mode is None:
        try:
            theme_mode = st.session_state.get("tulle_theme_mode")
        except Exception:
            theme_mode = None
    try:
        snap = safe_snapshot(snapshot)
        page = build_html(_r._clean(snap), theme_mode)
    except Exception as exc:  # noqa: BLE001 - never fall back to the unlocked page
        st.error(f"Experiments tab could not render: {type(exc).__name__}: {exc}")
        return
    if hasattr(st, "iframe"):
        st.iframe(page, height=int(height))
    else:
        import streamlit.components.v1 as components
        components.html(page, height=int(height), scrolling=True)
    note_form(st, snap, on_note, author)

