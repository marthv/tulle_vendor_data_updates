"""Experiment notes: a live record of what the team discussed and decided (FRAMEWORK.md "Notes").

Stored on the service volume as ``notes.jsonl`` in EXP_DATA_DIR, one JSON object per line, appended
under a lock with flush + fsync (one process, one writer). Never in the registry: notes change daily
and Tulle Bot loads the registry into every prompt.

Note: {"id", "exp_id", "ts", "author", "kind": note|decision|next_step, "text", "call", "client_id"}.
``client_id`` (optional, from the dash) makes a POST idempotent: a retry with the same client_id returns
the stored note instead of adding a duplicate.

:func:`overlay` attaches notes to a snapshot at serve time (no rescore) and marks rows whose latest
decision note is newer than the registry's ``notes_synced_through`` as ``decision_pending``; then
snapshot.recompute_views moves them out of Decide now / the queue / the rotation backlog.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import threading
from collections import deque

from . import snapshot as _snapshot
from . import spec

NOTES_FILE = "notes.jsonl"
KINDS = ("note", "decision", "next_step")
CALLS = ("ship", "revert", "shelve", "inconclusive", "keep_running", "resume")
MAX_AUTHOR = 80
MAX_TEXT = 2000
SERVE_MAX = 50
RATE_MAX = 30
RATE_WINDOW = _dt.timedelta(minutes=10)
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
OPEN_LIFECYCLES = ("live", "paused", "planned")


class NoteError(ValueError):
    """Bad note input (HTTP 422)."""


class RateLimited(Exception):
    """Too many notes (HTTP 429)."""


def _clean(s, limit, field, multiline=False):
    if not isinstance(s, str):
        raise NoteError(f"{field} must be a string")
    s = _CTRL.sub("", s)
    if not multiline:
        s = " ".join(s.split())
    s = s.strip()
    if not s:
        raise NoteError(f"{field} is required")
    if len(s) > limit:
        raise NoteError(f"{field} is longer than {limit} characters")
    return s


def validate(body):
    """Cleaned {author, kind, text, call} or NoteError."""
    if not isinstance(body, dict):
        raise NoteError("body must be a JSON object")
    author = _clean(body.get("author"), MAX_AUTHOR, "author")
    text = _clean(body.get("text"), MAX_TEXT, "text", multiline=True)
    kind = str(body.get("kind") or "note").strip().lower()
    if kind not in KINDS:
        raise NoteError(f"kind must be one of {', '.join(KINDS)}")
    call = body.get("call")
    if call in (None, ""):
        call = None
    else:
        call = str(call).strip().lower()
        if call not in CALLS:
            raise NoteError(f"call must be one of {', '.join(CALLS)}")
    if kind == "decision" and call is None:
        raise NoteError("a decision note needs a call")
    cid = body.get("client_id")
    if cid in (None, ""):
        cid = None
    elif not isinstance(cid, str) or not _CLIENT_ID.match(cid):
        raise NoteError("client_id must be 1-64 letters, digits or _.:-")
    return {"author": author, "kind": kind, "text": text, "call": call, "client_id": cid}


class NotesStore:
    def __init__(self, data_dir, clock=None):
        self.path = os.path.join(data_dir, NOTES_FILE)
        self.clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self._lock = threading.Lock()
        self._recent = deque()
        self._cache = None          # (mtime, size, by_id)

    def _read(self):
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return {}
        key = (st.st_mtime_ns, st.st_size)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        by_id = {}
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    n = json.loads(line)
                except ValueError:
                    continue            # a torn last line never breaks reads
                if isinstance(n, dict) and n.get("exp_id"):
                    by_id.setdefault(n["exp_id"], []).append(n)
        for rows in by_id.values():
            rows.sort(key=lambda n: str(n.get("ts")))
        self._cache = (key, by_id)
        return by_id

    def all_by_id(self):
        with self._lock:
            return {k: list(v) for k, v in self._read().items()}

    def load(self, exp_id, limit=SERVE_MAX):
        with self._lock:
            return list(self._read().get(exp_id, []))[-limit:]

    def add(self, exp_id, body):
        """Store a note. Returns (note, created); created is False for a repeated client_id."""
        clean = validate(body)
        now = self.clock()
        with self._lock:
            if clean.get("client_id"):
                for n in self._read().get(exp_id, []):
                    if n.get("client_id") == clean["client_id"]:
                        return n, False
            while self._recent and now - self._recent[0] > RATE_WINDOW:
                self._recent.popleft()
            if len(self._recent) >= RATE_MAX:
                raise RateLimited(f"more than {RATE_MAX} notes in 10 minutes")
            note = {"id": secrets.token_hex(6), "exp_id": exp_id,
                    "ts": now.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
            note.update(clean)
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(note, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._recent.append(now)
            self._cache = None
            return note, True


def _ts(v):
    s = str(v or "")
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if "T" in s else None
    except ValueError:
        return None


def _synced(row):
    v = row.get("notes_synced_through")
    t = _ts(v)
    if t is None and spec.parse_date(v):
        d = spec.parse_date(v)
        t = _dt.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=_dt.timezone.utc)
    return t


def pending_decision(row, notes):
    """Latest decision note newer than the registry sync mark, or None (open rows only)."""
    if row.get("lifecycle") not in OPEN_LIFECYCLES:
        return None
    mark = _synced(row)
    for n in reversed(notes or []):
        if n.get("kind") != "decision" or not n.get("call"):
            continue
        t = _ts(n.get("ts"))
        if mark is not None and t is not None and t <= mark:
            return None
        return {"call": n["call"], "author": n.get("author"), "ts": n.get("ts"), "text": n.get("text"),
                "note_id": n.get("id")}
    return None


def overlay(snap, store):
    """Return a copy of ``snap`` with notes_by_id, decision_pending and recomputed views. Idempotent."""
    if not isinstance(snap, dict):
        return snap
    by_id = store.all_by_id() if store is not None else {}
    out = dict(snap)
    ids = [r.get("id") for r in (snap.get("experiments") or []) + (snap.get("decided") or [])
           if isinstance(r, dict) and r.get("id")]
    out["notes_by_id"] = {i: [{k: n.get(k) for k in ("id", "ts", "author", "kind", "text", "call", "client_id")}
                              for n in by_id.get(i, [])[-SERVE_MAX:]] for i in ids if by_id.get(i)}
    rows = []
    for r in snap.get("experiments") or []:
        if not isinstance(r, dict):
            rows.append(r)
            continue
        r = dict(r)
        r.pop("decision_pending", None)
        dp = pending_decision(r, by_id.get(r.get("id")))
        if dp:
            r["decision_pending"] = dp
        rows.append(r)
    out["experiments"] = rows
    _snapshot.recompute_views(out)
    return out


def pending_list(snap):
    return [{"id": r.get("id"), "name": r.get("name"), "status": r.get("status"), **r["decision_pending"]}
            for r in (snap or {}).get("experiments") or []
            if isinstance(r, dict) and r.get("decision_pending")]
