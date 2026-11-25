"""Read lock for split tests: one read, on one fixed date (FRAMEWORK.md section 14).

Standard library only and no imports from the rest of the engine, so every surface that can show a result
(engine snapshot and HTTP API, Streamlit tab, Tulle Bot, daily brief, Slack DMs) can use it.

Switch: ``EXP_TAB_V2`` = 1 / on / true / yes. Unset, empty, 0, or anything else = today's behaviour exactly.

A split's result is hidden while it is running (any status that is not draft / planned / decided) and the UTC
date is before its read date. Read date = ``read_date``, else ``review_date``, else ``start_date`` +
``planned_days``; ``launch_lock.settings.read_date`` (the date locked at launch) wins when it is later. A split
with no read date at all stays hidden until one is set.
"""
from __future__ import annotations

import datetime as _dt
import os

_ON = ("1", "on", "true", "yes")
DECIDED = ("shipped", "reverted", "shelved", "inconclusive", "concluded", "ended", "decided")
NOT_STARTED = ("draft", "planned")

# The only fields of a running split that may leave through a text surface before its read date: the design,
# fixed at launch. Everything else (results, notes, decision text, audit notes, ...) could carry a result.
DESIGN_FIELDS = frozenset({
    "id", "name", "status", "themes", "tier", "bucket", "what_changed", "surfaces", "audience", "hypothesis",
    "tests", "goal", "primary_metric", "guardrail", "metric", "guardrails", "assignment", "start_date",
    "planned_days", "review_date", "read_date", "max_days", "reporting", "rollback", "links", "owner",
    "launched_by", "in_rotation", "framework_version", "exposure", "family", "family_card", "family_history",
    "decision_rule", "design", "end_date",
})


def enabled():
    return str(os.environ.get("EXP_TAB_V2", "")).strip().lower() in _ON


def utc_today():
    return _dt.datetime.now(_dt.timezone.utc).date()


def to_date(v):
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v).strip()[:10]) if v not in (None, "") else None
    except ValueError:
        return None


def is_split(entry):
    """True when couples are divided into arms (assignment.type, else the v1 ``design`` text)."""
    e = entry if isinstance(entry, dict) else {}
    a = e.get("assignment")
    if isinstance(a, dict) and a.get("type"):
        return str(a["type"]).strip().lower() == "split"
    if e.get("assignment_type") or e.get("_assignment_type"):
        return str(e.get("assignment_type") or e.get("_assignment_type")) == "split"
    d = str(e.get("design") or "").lower()
    return d.startswith("a/b") or "split" in d


def registry_read_date(entry):
    e = entry if isinstance(entry, dict) else {}
    for k in ("read_date", "review_date"):
        d = to_date(e.get(k))
        if d:
            return d
    sd = to_date(e.get("start_date"))
    try:
        n = int(float(e.get("planned_days")))
    except (TypeError, ValueError):
        n = 0
    return sd + _dt.timedelta(days=n) if sd and n > 0 else None


def read_date(entry, locked=None):
    """The fixed read date. ``locked`` = the date stored at launch (engine volume); the later one wins, so
    moving the date earlier after launch can never open a result early."""
    e = entry if isinstance(entry, dict) else {}
    ds = [registry_read_date(e), to_date(locked)]
    ll = e.get("launch_lock")
    if isinstance(ll, dict) and isinstance(ll.get("settings"), dict):
        ds.append(to_date(ll["settings"].get("read_date")))
    ds = [d for d in ds if d]
    return max(ds) if ds else None


def running(entry):
    s = str((entry or {}).get("status") or "").strip().lower()
    return s not in DECIDED and s not in NOT_STARTED


def hidden(entry, today=None, locked=None):
    """True when this entry's result must not be shown or sent."""
    if not (isinstance(entry, dict) and is_split(entry) and running(entry)):
        return False
    rd = read_date(entry, locked)
    return rd is None or (to_date(today) or utc_today()) < rd


def hidden_text(entry, locked=None):
    rd = read_date(entry, locked)
    return f"Result: hidden until {rd.isoformat()}" if rd else "Result: hidden until a read date is set"


def public_history(entry, today=None, locked=None):
    """family_history rows; the row for this entry's own running split carries no outcome before the read."""
    rows = [dict(r) for r in (entry.get("family_history") or []) if isinstance(r, dict)]
    if hidden(entry, today, locked):
        for r in rows:
            if r.get("entry") == entry.get("id"):
                r["outcome"] = hidden_text(entry, locked)
    return rows


def redact_entry(entry, today=None):
    """Registry entry without anything that could carry a result (unchanged when it may be shown)."""
    if not hidden(entry, today):
        return entry
    out = {k: v for k, v in entry.items() if k in DESIGN_FIELDS}
    if isinstance(out.get("family_history"), list):
        out["family_history"] = public_history(entry, today)
    out["result"] = hidden_text(entry)
    return out


def redact_registry(reg, today=None):
    """Copy of the registry that is safe to put in a prompt, message or API answer. No-op when the switch is off."""
    if not enabled() or not isinstance(reg, dict):
        return reg
    out = dict(reg)
    exps = reg.get("experiments")
    out["experiments"] = [redact_entry(e, today) for e in exps] if isinstance(exps, list) else exps
    return out
