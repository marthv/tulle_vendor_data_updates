"""Slack DMs to the owner (FRAMEWORK.md section 9): format + send.

* :func:`format_digest`    daily, ~14:00 UTC. Returns None when nothing needs action, so the
                           caller sends nothing (calls due, stale, callable, guardrail, unlogged).
* :func:`format_guardrail` urgent, one experiment, once per breach (caller de-duplicates).
* :func:`format_weekly`    Monday: what was decided in the last 7 days and the one-line takeaway.
                           None when nothing was decided.
* :func:`send`             chat.postMessage with ``SLACK_BOT_TOKEN`` to ``SLACK_DM_CHANNEL`` (an
                           existing D... conversation id; Tulle Bot lacks im:write). Never raises.
                           ``DRY_RUN`` (default "1" in code, for safety) logs instead of sending.

Every message ends with a link to the dash (``DASH_URL``, default https://admin.tulletogether.app).
"""
from __future__ import annotations

import datetime as _dt
import logging
import os

log = logging.getLogger("experiments_engine.alerts")

SLACK_URL = "https://slack.com/api/chat.postMessage"
MAX_ITEMS = 5
MAX_CHARS = 3800


def dash_url():
    return (os.environ.get("DASH_URL") or "https://admin.tulletogether.app").rstrip("/")


def dry_run():
    return str(os.environ.get("DRY_RUN", "1")).strip().lower() not in ("0", "false", "no", "off")


def _esc(s):
    """Slack mrkdwn needs &, <, > escaped in user text."""
    return str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _name(row, limit=90):
    n = str(row.get("name") or row.get("id") or "?")
    return _esc(n if len(n) <= limit else n[:limit - 1].rstrip() + "…")


def _pct(x, nd=0):
    try:
        return f"{float(x) * 100:.{nd}f}%"
    except (TypeError, ValueError):
        return "?"


def counts_line(row):
    """'control 12 of 300 (4.0%) vs treatment 20 of 310 (6.5%), 93% chance better'."""
    st = row.get("stats") or {}
    c_n, c_x, t_n, t_x = st.get("c_n"), st.get("c_conv"), st.get("t_n"), st.get("t_conv")
    if c_n is None or t_n is None:
        arms = (row.get("readout") or {}).get("arms") or {}
        c, t = arms.get("control") or {}, arms.get("treatment") or {}
        c_n, c_x, t_n, t_x = c.get("n"), c.get("conv"), t.get("n"), t.get("conv")
    bits = []
    if c_n and t_n is not None and c_x is not None and t_x is not None:
        bits.append(f"before/control {c_x} of {c_n} ({_pct(c_x / c_n, 1)}) vs "
                    f"after/treatment {t_x} of {t_n} ({_pct(t_x / t_n, 1) if t_n else '?'})")
    if st.get("p_beat") is not None:
        bits.append(f"{_pct(st['p_beat'])} chance it's better")
    return ", ".join(bits)


def _bullets(rows, render):
    lines = [render(r) for r in rows[:MAX_ITEMS]]
    if len(rows) > MAX_ITEMS:
        lines.append(f"  ...and {len(rows) - MAX_ITEMS} more on the dash")
    return lines


def _exp_line(row):
    action = str(row.get('action') or '').rstrip('.?!')
    line = f"• *{_name(row)}*: {_esc(action)}. {_esc(row.get('reason') or '')}".rstrip()
    c = counts_line(row)
    return line + (f"\n    {c}" if c else "")


def _change_line(ev):
    ts = str(ev.get("ts") or "")[:16].replace("T", " ")
    where = ", ".join(ev.get("surfaces") or []) or ev.get("source") or "?"
    ref = f" `{_esc(ev.get('ref'))}`" if ev.get("ref") and ev.get("source") != "xano" else ""
    return f"• {ts} UTC · {_esc(where)}{ref}: {_esc(ev.get('title'))}"


def actionable(snapshot, unlogged=None):
    s = (snapshot or {}).get("summary") or {}
    return bool(s.get("needs_call") or s.get("stale") or s.get("guardrail") or unlogged)


def format_digest(snapshot, unlogged=None):
    """Daily digest text, or None when nothing needs action (section 9)."""
    snap = snapshot or {}
    unlogged = list(unlogged or [])
    if not actionable(snap, unlogged):
        return None
    s = snap.get("summary") or {}
    exps = [r for r in snap.get("experiments") or [] if r.get("health")]
    guard = [r for r in exps if r["health"] == "guardrail_breach"]
    calls = [r for r in exps if r["health"] in ("callable_win", "callable_loss", "needs_call")]
    stale = [r for r in exps if r.get("stale") and r not in calls and r not in guard]
    head = []
    if s.get("needs_call"):
        head.append(f"{s['needs_call']} need{'s' if s['needs_call'] == 1 else ''} a call")
    if s.get("stale"):
        head.append(f"{s['stale']} stale")
    if s.get("guardrail"):
        head.append(f"{s['guardrail']} guardrail alert{'s' if s['guardrail'] != 1 else ''}")
    if unlogged:
        head.append(f"{len(unlogged)} unlogged change{'s' if len(unlogged) != 1 else ''}")
    out = [f"*Experiments today: {', '.join(head)}*"]
    if guard:
        out += ["", "*Guardrail alerts (pause or revert)*"] + _bullets(guard, _exp_line)
    if calls:
        out += ["", "*Calls due*"] + _bullets(calls, _exp_line)
    if stale:
        out += ["", "*Going stale*"] + _bullets(stale, _exp_line)
    if unlogged:
        out += ["", "*Shipped changes not in the registry* (add a changes[] entry, or they confound live tests)"]
        out += _bullets(unlogged, _change_line)
    healthy = s.get("healthy") or 0
    out += ["", f"{healthy} running healthy. <{dash_url()}|Open the Experiments tab>"]
    return _cap("\n".join(out))


def format_guardrail(row):
    lines = [f":rotating_light: *Guardrail breach: {_name(row)}*",
             f"{_esc(row.get('reason') or 'A guardrail crossed its threshold.')}"]
    for g in row.get("guardrails") or []:
        if isinstance(g, dict) and g.get("breached"):
            bits = [str(g.get("key") or "guardrail")]
            if g.get("p_breach") is not None:
                way = "rise" if g.get("max_drop_rel") is None and g.get("max_rise_rel") is not None else "drop"
                bits.append(f"{_pct(g['p_breach'])} chance of a real {way}")
            if g.get("max_drop_rel") is not None:
                bits.append(f"threshold: a drop of more than {_pct(g['max_drop_rel'])}")
            elif g.get("max_rise_rel") is not None:
                bits.append(f"threshold: a rise of more than {_pct(g['max_rise_rel'])}")
            lines.append("• " + _esc(", ".join(bits)))
    c = counts_line(row)
    if c:
        lines.append(c)
    lines.append(f"Recommended: *{_esc(row.get('action') or 'Pause or revert now')}*. <{dash_url()}|Open the dash>")
    return _cap("\n".join(lines))


def format_weekly(snapshot, today=None):
    """Decided in the 7 days before ``today`` (UTC date) with takeaways; None when none."""
    snap = snapshot or {}
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    if isinstance(today, str):
        today = _dt.date.fromisoformat(today[:10])
    lo = today - _dt.timedelta(days=7)
    rows = []
    for d in snap.get("decided") or []:
        try:
            dd = _dt.date.fromisoformat(str(d.get("decided_on"))[:10])
        except ValueError:
            continue
        if lo <= dd < today:
            rows.append(d)
    if not rows:
        return None
    rows.sort(key=lambda d: str(d.get("decided_on")))
    out = [f"*Experiments, week to {today.isoformat()}: {len(rows)} decided*"]
    for d in rows[:MAX_ITEMS * 2]:
        outcome = d.get("outcome") or d.get("status") or "decided"
        take = d.get("takeaway") or d.get("decision") or "no takeaway recorded"
        out.append(f"• *{_name(d)}*: {_esc(outcome)}. {_esc(take)}")
    s = snap.get("summary") or {}
    out += ["", f"{s.get('live', 0)} still live, {s.get('needs_call', 0)} need a call. <{dash_url()}|Open the dash>"]
    return _cap("\n".join(out))


def _cap(text):
    if len(text) <= MAX_CHARS:
        return text
    return text[: MAX_CHARS - 40].rstrip() + f"\n...(cut; see <{dash_url()}|the dash>)"


def send(text, http=None):
    """Post ``text`` as a DM. Never raises. Returns {ok, dry_run, error}."""
    try:
        if not text:
            return {"ok": False, "dry_run": dry_run(), "error": "empty message"}
        if dry_run():
            log.warning("DRY_RUN: would DM the owner:\n%s", text)
            return {"ok": True, "dry_run": True, "error": None}
        token = os.environ.get("SLACK_BOT_TOKEN", "")
        channel = os.environ.get("SLACK_DM_CHANNEL", "")
        if not token or not channel:
            log.error("Slack not configured (SLACK_BOT_TOKEN / SLACK_DM_CHANNEL); message not sent")
            return {"ok": False, "dry_run": False, "error": "not configured"}
        if http is None:
            import requests as http  # noqa: PLC0415 - lazy so import stays network-free
        r = http.post(SLACK_URL, headers={"Authorization": f"Bearer {token}"},
                      json={"channel": channel, "text": text, "unfurl_links": False,
                            "unfurl_media": False, "mrkdwn": True}, timeout=20)
        try:
            body = r.json()
        except Exception:
            body = {}
        if body.get("ok"):
            return {"ok": True, "dry_run": False, "error": None}
        err = body.get("error") or f"HTTP {getattr(r, 'status_code', '?')}"
        log.error("Slack DM failed: %s", err)
        return {"ok": False, "dry_run": False, "error": err}
    except Exception as exc:  # never raise out of send
        log.error("Slack DM failed: %s: %s", type(exc).__name__, exc)
        return {"ok": False, "dry_run": dry_run(), "error": f"{type(exc).__name__}: {exc}"}
