"""Read lock for split tests: one read, on one fixed date (FRAMEWORK.md section 14).

Standard library only, so every surface that can show a result (engine snapshot and HTTP API, Streamlit tab, Tulle
Bot, daily brief, Slack DMs) uses the same rule and the same source: the registry entry and its ``launch_lock``.

Switch: ``EXP_TAB_V2`` = 1 / on / true / yes. Unset, empty, 0, or anything else = today's behaviour exactly.

* Once a split, always a split: ``launch_lock.settings.type`` (or a left-over split rule, salt or percent) counts, so
  editing ``assignment.type`` later opens nothing.
* Read date = ``launch_lock.settings.read_date``; a later date on the entry also counts, an earlier one never does.
  Without a lock: the latest of (``read_date``, else ``review_date``) and, when ``read_date`` is not set,
  ``start_date`` + ``planned_days``.
* Hidden while not started, while running before the read date, and after being closed before the read date
  ("Stopped early, result not read"). A running split with no read date stays hidden.
"""
from __future__ import annotations

import datetime as _dt
import os

_ON = ("1", "on", "true", "yes")
DECIDED = ("shipped", "reverted", "shelved", "inconclusive", "concluded", "ended", "decided")
NOT_STARTED = ("draft", "planned")
SPLIT_RULES = ("user_id_parity", "hash_pct")
STOPPED_EARLY = "Stopped early, result not read"

# The only fields of a hidden split that may leave through a text surface: the design, fixed at launch. Everything
# else (results, notes, decision text, audit notes, ...) could carry a result.
DESIGN_FIELDS = frozenset({
    "id", "name", "status", "themes", "tier", "bucket", "what_changed", "surfaces", "audience", "hypothesis",
    "tests", "goal", "primary_metric", "guardrail", "metric", "guardrails", "assignment", "start_date",
    "planned_days", "review_date", "read_date", "max_days", "reporting", "rollback", "links", "owner",
    "launched_by", "in_rotation", "framework_version", "exposure", "family", "family_card", "family_history",
    "decision_rule", "design", "end_date", "decided_on", "plain_name", "plain_line",
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


def status(entry):
    return str((entry or {}).get("status") or "").strip().lower()


def lock_settings(entry, stored=None):
    """Launch settings: the registry ``launch_lock``, else the engine's stored copy ({} when neither)."""
    for lk in ((entry or {}).get("launch_lock"), stored):
        if isinstance(lk, dict) and isinstance(lk.get("settings"), dict):
            return lk["settings"]
    return {}


def has_lock(entry):
    lk = (entry or {}).get("launch_lock")
    return isinstance(lk, dict) and bool(lk.get("hash")) and isinstance(lk.get("settings"), dict)


def is_split(entry, stored=None):
    """True when couples are, or at launch were, divided into arms."""
    e = entry if isinstance(entry, dict) else {}
    if e.get("_split") or str(lock_settings(e, stored).get("type") or "") == "split":
        return True
    a = e.get("assignment")
    if isinstance(a, dict) and a.get("type"):
        if str(a["type"]).strip().lower() == "split":
            return True
        return a.get("rule") in SPLIT_RULES or bool(a.get("salt")) or a.get("pct") is not None
    if e.get("assignment_type") or e.get("_assignment_type"):
        return str(e.get("assignment_type") or e.get("_assignment_type")) == "split"
    d = str(e.get("design") or "").lower()
    return d.startswith("a/b") or "split" in d


def registry_read_date(entry):
    """The read date written on the entry itself (no lock)."""
    e = entry if isinstance(entry, dict) else {}
    d = to_date(e.get("read_date"))
    if d:
        return d
    sd = to_date(e.get("start_date"))
    try:
        n = int(float(e.get("planned_days")))
    except (TypeError, ValueError):
        n = 0
    ds = [x for x in (to_date(e.get("review_date")), sd + _dt.timedelta(days=n) if sd and n > 0 else None) if x]
    return max(ds) if ds else None


def read_date(entry, stored=None):
    """The fixed read date. ``stored`` = the engine's own copy of the launch lock (a dict) or a date. The latest wins."""
    ds = [registry_read_date(entry), to_date(lock_settings(entry).get("read_date"))]
    if isinstance(stored, dict):
        ds.append(to_date((stored.get("settings") or {}).get("read_date")))
    else:
        ds.append(to_date(stored))
    ds = [d for d in ds if d]
    return max(ds) if ds else None


def _stored_lock(stored):
    return stored if isinstance(stored, dict) else None


def stopped_early(entry, stored=None):
    """Closed before its read date: the result was never read, and stays that way."""
    if status(entry) not in DECIDED or not is_split(entry, _stored_lock(stored)):
        return False
    rd = read_date(entry, stored)
    when = to_date(entry.get("decided_on")) or to_date(entry.get("end_date"))
    return bool(rd and when and when < rd)


def hidden(entry, today=None, locked=None):
    """True when this entry's result must not be shown or sent."""
    if not isinstance(entry, dict) or not is_split(entry, _stored_lock(locked)):
        return False
    st = status(entry)
    if st in NOT_STARTED:
        return True
    rd = read_date(entry, locked)
    now = to_date(today) or utc_today()
    if st in DECIDED:
        if rd is None:                     # decided before read dates existed
            return has_lock(entry) or bool(_stored_lock(locked))
        return now < rd or stopped_early(entry, locked)
    return rd is None or now < rd


def hidden_text(entry, locked=None):
    if status(entry) in DECIDED:
        return STOPPED_EARLY
    rd = read_date(entry, locked)
    return f"Result: hidden until {rd.isoformat()}" if rd else "Result: hidden until a read date is set"


def public_history(entry, today=None, locked=None):
    """family_history rows; the row for this entry's own hidden split carries no outcome."""
    rows = [dict(r) for r in (entry.get("family_history") or []) if isinstance(r, dict)]
    if hidden(entry, today, locked):
        for r in rows:
            if r.get("entry") == entry.get("id"):
                r["outcome"] = hidden_text(entry, locked)
    return rows


def setup_warning(entry):
    """"Settings changed after launch: ..." when the entry no longer matches its launch_lock, else None. Same check on
    every surface (v2.settings_changes)."""
    if not (isinstance(entry, dict) and is_split(entry)) or status(entry) in NOT_STARTED:
        return None
    try:
        from . import v2
        diff, _src = v2.settings_changes(entry)
    except Exception:
        return "Setup could not be checked" if entry.get("launch_lock") else None
    return "RED: settings changed after launch: " + ", ".join(diff) if diff else None


def redact_entry(entry, today=None):
    """Registry entry without anything that could carry a result (unchanged when it may be shown). On text surfaces a
    running split without a launch_lock is never opened: they can't see the engine's copy."""
    if not isinstance(entry, dict):
        return entry
    unlocked = is_split(entry) and status(entry) not in DECIDED + NOT_STARTED and not has_lock(entry)
    warn = setup_warning(entry)
    if not hidden(entry, today) and not unlocked:
        return dict(entry, setup=warn) if warn else entry
    out = {k: v for k, v in entry.items() if k in DESIGN_FIELDS}
    if isinstance(out.get("family_history"), list):
        out["family_history"] = public_history(entry, today)
    if status(entry) in DECIDED and hidden(entry, today):
        out["status"] = "stopped early"
    out["result"] = hidden_text(entry) if hidden(entry, today) else \
        "Result: not shown here (no launch_lock in the registry); see the Experiments tab"
    if warn:
        out["setup"] = warn
    return out


def redact_registry(reg, today=None):
    """Copy of the registry that is safe to put in a prompt, message or API answer. No-op when the switch is off."""
    if not enabled() or not isinstance(reg, dict):
        return reg
    out = dict(reg)
    exps = reg.get("experiments")
    out["experiments"] = [redact_entry(e, today) for e in exps] if isinstance(exps, list) else exps
    return out
