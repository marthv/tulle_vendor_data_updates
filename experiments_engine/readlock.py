"""Read lock for split tests: one read, on one fixed date (FRAMEWORK.md section 14).

Standard library only, so every surface that can show a result (engine snapshot and HTTP API, Streamlit tab, Tulle
Bot, daily brief, Slack DMs) uses the same rule and the same source: the registry entry and its ``launch_lock``.

Switch: ``EXP_TAB_V2`` = 1 / on / true / yes. Unset, empty, 0, or anything else = today's behaviour exactly.

* Once a split, always a split: ``launch_lock.settings.type`` (or a left-over split rule, salt or percent) counts, so
  editing ``assignment.type`` later opens nothing.
* Only a ``launch_lock`` that validates opens anything: present, its hash matches its settings, made for this id, the
  entry still matches it, and not replaced (engine's stored copy, ``launch_locks.json``). Anything else stays hidden.
* Read date = ``launch_lock.settings.read_date``; a later date on the entry also counts, an earlier one never does.
* Hidden while not started, before the read date, and after being closed before the read date ("Stopped early, result
  not read"). Text surfaces (Bot prompt, registry-only tab) also need the lock to be in ``launch_locks.json``, because
  they can't see the engine's copy. Splits decided before locks existed (LEGACY) stay as they were.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os

_ON = ("1", "on", "true", "yes")
DECIDED = ("shipped", "reverted", "shelved", "inconclusive", "concluded", "ended", "decided")
NOT_STARTED = ("draft", "planned")
SPLIT_RULES = ("user_id_parity", "hash_pct")
STOPPED_EARLY = "Stopped early, result not read"
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch_locks.json")
# splits decided before launch locks existed: id -> fingerprint of the name
LEGACY = {"EXP-2026-10-pricing-phase2": "7a87b6acd38d", "EXP-2026-10-search-railway": "240471954fa5"}

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


def fingerprint(entry):
    return hashlib.sha256(" ".join(str((entry or {}).get("name") or "").lower().split()).encode()).hexdigest()[:12]


_ledger_cache = {"key": None, "data": {}}


def ledger():
    """{id: {"hash", "read_date", "name"}} of locks recorded at launch (validate.py --lock <id> --write). Beside the
    engine code, apart from the registry, so an edited entry can't rewrite it."""
    try:
        key = (LEDGER, os.stat(LEDGER).st_mtime_ns)
    except OSError:
        return {}
    if _ledger_cache["key"] != key:
        try:
            with open(LEDGER, encoding="utf-8") as f:
                d = json.load(f).get("locks")
        except (OSError, ValueError, AttributeError):
            d = {}
        _ledger_cache.update(key=key, data=d if isinstance(d, dict) else {})
    return _ledger_cache["data"]


def _ledger_row(entry):
    """(ledger id, row) for this entry by id, else by its name (a renamed id), else (None, None)."""
    led = ledger()
    eid = (entry or {}).get("id")
    if eid in led:
        return eid, led[eid]
    name = fingerprint(entry)
    return next(((k, v) for k, v in led.items() if isinstance(v, dict) and v.get("name") == name), (None, None))


def is_legacy(entry):
    e = entry or {}
    return status(e) in DECIDED and not e.get("launch_lock") and LEGACY.get(e.get("id")) == fingerprint(e)


def lock_problem(entry, stored=None):
    """None when the launch_lock validates, else why not. Any error counts as a problem (fail closed)."""
    e = entry if isinstance(entry, dict) else {}
    lk = e.get("launch_lock")
    if not has_lock(e):
        return "missing"
    try:
        from . import v2
        if v2.settings_hash(lk["settings"]) != lk["hash"]:
            return "edited"
        if lk["settings"].get("id") != e.get("id"):
            return "made for another test"
        diff = v2.settings_changes(e)[0]
        if diff:
            return "not matched by the entry: " + ", ".join(diff)
        lid, row = _ledger_row(e)
        if row is not None and (lid != e.get("id") or row.get("hash") != lk["hash"]):
            return "replaced or renamed"
        if isinstance(stored, dict) and stored.get("hash") and stored["hash"] != lk["hash"]:
            return "replaced after launch"
    except Exception:
        return "not checkable"
    return None


def has_lock(entry):
    lk = (entry or {}).get("launch_lock")
    return isinstance(lk, dict) and bool(lk.get("hash")) and isinstance(lk.get("settings"), dict)


def is_split(entry, stored=None):
    """True when couples are, or at launch were, divided into arms."""
    e = entry if isinstance(entry, dict) else {}
    locks = [lk.get("settings") for lk in (e.get("launch_lock"), stored) if isinstance(lk, dict)]
    if e.get("_split") or any(isinstance(s, dict) and s.get("type") == "split" for s in locks) or             _ledger_row(e)[1] is not None:
        return True
    a = e.get("assignment")
    if isinstance(a, dict) and a.get("split") is True:     # a real split the engine can't assign (e.g. by e-mail)
        return True
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
    if status(entry) not in DECIDED or not is_split(entry, _stored_lock(stored)) or is_legacy(entry):
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
    if is_legacy(entry):
        return False
    if lock_problem(entry, _stored_lock(locked)):
        return True
    rd = read_date(entry, locked)
    now = to_date(today) or utc_today()
    return rd is None or now < rd or stopped_early(entry, locked)


def hidden_text(entry, locked=None, today=None):
    rd = read_date(entry, locked)
    if stopped_early(entry, locked):
        return STOPPED_EARLY
    now = to_date(today) or utc_today()
    if status(entry) not in DECIDED and rd and now < rd:
        return f"Result: hidden until {rd.isoformat()}"
    why = None if status(entry) in NOT_STARTED else lock_problem(entry, _stored_lock(locked))
    if why:
        return f"Result: hidden, launch lock {why}"
    return f"Result: hidden until {rd.isoformat()}" if rd else "Result: hidden until a read date is set"


def text_open(entry, today=None):
    """May a surface that only has the registry show this result? Not a split, legacy, or open with a recorded lock."""
    if not is_split(entry) or is_legacy(entry):
        return True
    lid, row = _ledger_row(entry)
    return not hidden(entry, today) and row is not None and lid == entry.get("id")


def public_history(entry, today=None, locked=None):
    """family_history rows; the row for this entry's own hidden split carries no outcome."""
    rows = [dict(r) for r in (entry.get("family_history") or []) if isinstance(r, dict)]
    if hidden(entry, today, locked):
        for r in rows:
            if r.get("entry") == entry.get("id"):
                r["outcome"] = hidden_text(entry, locked)
    return rows


def setup_warning(entry):
    """"RED: launch lock ..." when a started split's lock does not validate, else None. Same check on every surface."""
    if not (isinstance(entry, dict) and is_split(entry)) or status(entry) in NOT_STARTED or is_legacy(entry):
        return None
    why = lock_problem(entry)
    return f"RED: launch lock {why}" if why else None


def redact_entry(entry, today=None):
    """Registry entry without anything that could carry a result (unchanged when it may be shown)."""
    if not isinstance(entry, dict) or text_open(entry, today):
        return entry
    warn = setup_warning(entry)
    out = {k: v for k, v in entry.items() if k in DESIGN_FIELDS}
    if isinstance(out.get("family_history"), list):
        out["family_history"] = [dict(r, outcome=None) if isinstance(r, dict) and r.get("entry") == entry.get("id") else r
                                 for r in out["family_history"]]
    if status(entry) in DECIDED:
        out["status"] = "stopped early" if stopped_early(entry) else "closed"
    out["result"] = hidden_text(entry, today=today) if hidden(entry, today) else \
        "Result: not shown here; see the Experiments tab"
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
