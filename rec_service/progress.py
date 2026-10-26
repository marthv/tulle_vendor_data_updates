"""Live progress + Stop for one assistant request (2026-10-25).

The page generates a request_id (UUID), sends it with /rec/opening | /rec/refine | /rec/more, and meanwhile polls
GET /rec/progress/{request_id} for a friendly label, or POSTs /rec/cancel/{request_id} when the couple taps Stop.

IN MEMORY, ONE PROCESS. Railway runs ONE replica of rec_service today, so the request and its polls/cancel hit the
same process. With more than one replica (or several uvicorn workers) a poll or a cancel can land on a process that
never saw the request - it would read "unknown" and a Stop would do nothing. That needs shared storage (Redis or a
Xano table keyed by request_id) instead of this dict.

Labels are generic on purpose: no vendor ids, no tool names, no prompt text (agent.py picks them; tests check).
"""
import re
import threading
import time

MAX_ENTRIES = 2000          # bounded: oldest finished entries are evicted first
TTL_AFTER_FINISH_S = 600    # an entry reads "unknown" 10 min after its request finished
MAX_RUNNING_S = 1800        # safety net: a "running" entry older than this is treated as finished (never hit:
                            # every request finishes its entry in a finally)
MAX_ID_LEN = 64
LABEL_MAX = 40

START = "Reading your preferences…"
THINKING = "Thinking it through…"
WRITING = "Writing your answer…"
PRICING = "Checking real pricing…"
SAVED = "Looking at your saved venues…"
TOOL_LABELS = {   # static labels by tool name (search tools and batched pricing build theirs: see agent.py)
    "saved_venues": SAVED,
    "venue_pricing": PRICING, "venue_details": PRICING, "estimate_cost": PRICING,
    "market_benchmarks": PRICING, "pe_market_rates": PRICING,
    "estimate_wedding_cost": "Estimating your total cost…",
    "split_budget": "Splitting your budget…",
    "compare_venues": "Comparing your saved venues…",
    "budget_status": "Checking your budget…",
}
PRICING_TOOLS = ("venue_pricing", "venue_details", "estimate_cost")   # one venue each: "for N venues" per batch

UNKNOWN = {"state": "unknown", "label": "", "step": 0}

_lock = threading.RLock()
_entries = {}               # request_id -> entry dict (insertion order = age)


class Stopped(Exception):
    """Raised at a checkpoint once the couple tapped Stop: no further tool or model call runs."""


def _place(text):
    """A city or state name as shown in a label: letters, spaces and . - ' only (it came from model args)."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z .'\-]", "", str(text or ""))).strip()


def search_label(states=(), city=""):
    """'Searching venues in Texas…' - the city if one is searched, else the state(s); 'your area' when there is
    none, it is 'International', or the label would run past ~40 characters."""
    sts = [_place(s) for s in (states or []) if s and str(s) != "International"]
    sts = [s for s in sts if s]
    candidates = ([_place(city)] if _place(city) else []) + (
        [sts[0]] if len(sts) == 1 else [" and ".join(sts)] if len(sts) == 2 else [])
    for place in candidates:
        label = "Searching venues in %s…" % place
        if len(label) <= LABEL_MAX:
            return label
    return "Searching venues in your area…"


def pe_label(category):
    return "Searching photographers…" if str(category or "").lower().startswith("photo") else "Searching entertainment…"


_LISTING_LABELS = {"Flowers": "Searching florists…", "Beauty": "Searching hair and makeup…",
                   "Food and Beverage": "Searching caterers…", "Wedding Planner": "Searching wedding planners…"}


def listing_label(category):
    return _LISTING_LABELS.get(category, "Searching vendors…")


def pricing_label(n):
    return "Checking real pricing for %d venues…" % n if n and n > 1 else PRICING


class Tracker:
    """The request's handle. Thread-safe: tools run on the runner's thread and on pricing pools."""

    def __init__(self, entry):
        self._e = entry

    def set(self, label):
        """Show a new label; step goes up only when the label actually changes."""
        if not label:
            return
        with _lock:
            if self._e["state"] == "running" and self._e["label"] != label:
                self._e["label"] = label
                self._e["step"] += 1

    def cancelled(self):
        with _lock:
            return bool(self._e["cancel"])

    def check(self):
        """A checkpoint: raise Stopped if the couple tapped Stop."""
        if self.cancelled():
            raise Stopped()

    def finish(self, state="done"):
        with _lock:
            if self._e["state"] == "running":
                self._e["state"] = "stopped" if state == "stopped" else "done"
                self._e["finished_at"] = time.time()


class _Null:
    """No request_id: every call is a no-op, so agent.run behaves exactly as before."""
    def set(self, label):
        pass

    def cancelled(self):
        return False

    def check(self):
        pass

    def finish(self, state="done"):
        pass


NULL = _Null()


def _expired(e, now):
    if e["finished_at"] is not None:
        return now - e["finished_at"] > TTL_AFTER_FINISH_S
    return now - e["started_at"] > MAX_RUNNING_S + TTL_AFTER_FINISH_S


def _sweep(now):
    for rid in [rid for rid, e in _entries.items() if _expired(e, now)]:
        del _entries[rid]
    while len(_entries) >= MAX_ENTRIES:
        done = next((rid for rid, e in _entries.items() if e["finished_at"] is not None), None)
        del _entries[done if done is not None else next(iter(_entries))]


def _new(owner, now, state="running", cancel=False):
    return {"owner": owner, "state": state, "label": START if state == "running" else "", "step": 1 if state == "running" else 0,
            "cancel": cancel, "started_at": now, "finished_at": None if state == "running" else now,
            "precancel": state != "running"}


def clean_id(request_id):
    rid = (request_id or "").strip() if isinstance(request_id, str) else ""
    return rid[:MAX_ID_LEN] if rid else ""


def start(request_id, owner):
    """Register a request. None when there is no request_id (behaviour unchanged). A Stop that arrived before the
    request did (the page fires both at once) is kept: the request starts already cancelled. An id that belongs to
    another user is never taken over - the request runs untracked."""
    rid = clean_id(request_id)
    if not rid:
        return None
    now = time.time()
    with _lock:
        _sweep(now)
        old = _entries.get(rid)
        if old is not None and old["owner"] != owner and not _expired(old, now):
            return Tracker(_new(owner, now))          # untracked: polls by the real owner keep seeing theirs
        cancel = bool(old and old["owner"] == owner and old.get("precancel"))
        _entries.pop(rid, None)
        e = _new(owner, now, cancel=cancel)
        _entries[rid] = e
        return Tracker(e)


def get(request_id, owner):
    """The poll. Unknown/expired -> UNKNOWN. Another user's id -> None (the endpoint answers 404)."""
    rid = clean_id(request_id)
    now = time.time()
    with _lock:
        e = _entries.get(rid)
        if e is None or _expired(e, now):
            return dict(UNKNOWN)
        if e["owner"] != owner:
            return None
        state = e["state"]
        if state == "running" and now - e["started_at"] > MAX_RUNNING_S:
            state = "done"
        return {"state": state, "label": e["label"] if state == "running" else "", "step": e["step"]}


def cancel(request_id, owner):
    """Tap Stop. True = accepted (a running request stops at its next checkpoint; a finished one is unchanged; an id
    not seen yet is remembered for 10 min so the request starts cancelled). False = another user's id (404)."""
    rid = clean_id(request_id)
    if not rid:
        return True
    now = time.time()
    with _lock:
        e = _entries.get(rid)
        if e is not None and not _expired(e, now):
            if e["owner"] != owner:
                return False
            e["cancel"] = True
            return True
        _sweep(now)
        _entries[rid] = _new(owner, now, state="stopped", cancel=True)
        return True


def reset():
    """Tests only."""
    with _lock:
        _entries.clear()
