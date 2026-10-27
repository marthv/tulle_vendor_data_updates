"""Reuse today's opening (owner OK 2026-10-27, speed pass #5).

WHY PEOPLE RE-OPEN: since 2026-10-16 (owner) the Planner page opens a FRESH chat on every visit (WeWeb page workflow
"Rec Opening (signed in only)", i4c_pick_chat), so a couple who clicks a venue card or a PDF and comes back gets a
brand-new opening: a full model run (~12 s) for picks they saw minutes ago. On 2026-10-26 17 of 56 real (non-internal)
openings were same-day repeats; about half had exactly the same inputs (same input-token count), the rest had
changed (an answered profile question, a saved venue, a used free PDF view).

RULE (conservative): the stored opening is returned only when ALL of these hold -
  - same user id, same America/New_York day, stored less than REC_OPENING_REUSE_MAX_AGE_S ago (default 6 h, so
    card prices are never older than that), in this process (a deploy or restart empties the store);
  - the fingerprint matches: every input that shapes the answer - profile fields, Saved vendor ids (in order),
    tier / entitlement fields, free PDF views left, memory notes + user_context + preference answers, the
    thin-profile questions that would be asked now, the prompt text + model + effort + thinking + feature flags,
    and VERSION below. Anything different, or anything the fingerprint can't read, = a fresh opening;
  - the stored answer was a normal one: status ok, not stopped, at least one card.
The caller still runs the full _gate first (kill switch, caps, paywall, feedback), so a reuse is never served where
a fresh opening would be refused. A reuse writes no rec_usage row (it costs nothing and spends no allowance) and is
tracked in Mixpanel as rec_server_request {kind: "opening", reused: true, model: "reuse"}.

Bump VERSION whenever the opening's prompt, cards or code change in a way the prompt hash below doesn't see."""
import copy
import datetime as dt
import hashlib
import json
import threading
import time

import config

VERSION = "2026-10-27.1"

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:      # no tz database: a fixed UTC-5 day (at worst the day flips an hour off around DST)
    _ET = dt.timezone(dt.timedelta(hours=-5))


def et_day(now_s=None):
    t = dt.datetime.fromtimestamp(time.time() if now_s is None else now_s, dt.timezone.utc)
    return t.astimezone(_ET).strftime("%Y-%m-%d")


def fingerprint(parts):
    """sha256 over the canonical JSON of parts. Unserialisable values go through str() (still deterministic)."""
    blob = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def reusable(result):
    """Only a normal answer is stored."""
    return (isinstance(result, dict) and not result.get("stopped") and bool(result.get("cards"))
            and bool((result.get("text") or "").strip()))


class Store:
    """user id -> the latest opening {day, fp, at, result}. One per user (the newest wins). Thread-safe."""

    def __init__(self, max_users=5000):
        self.max = max_users
        self._d = {}
        self._lock = threading.Lock()

    def get(self, user_id, fp, now_s=None):
        now_s = time.time() if now_s is None else now_s
        with self._lock:
            e = self._d.get(str(user_id))
        if not e or e["fp"] != fp or e["day"] != et_day(now_s):
            return None
        if now_s - e["at"] > config.OPENING_REUSE_MAX_AGE_S:
            return None
        return copy.deepcopy(e["result"])

    def put(self, user_id, fp, result, now_s=None):
        if not reusable(result):
            return False
        now_s = time.time() if now_s is None else now_s
        # notes_saved belongs to the run that saved them: a reuse saves nothing, so it must not say it did.
        stored = copy.deepcopy(dict(result, notes_saved=[]))
        with self._lock:
            self._d[str(user_id)] = {"day": et_day(now_s), "fp": fp, "at": now_s, "result": stored}
            if len(self._d) > self.max:     # drop the oldest entries
                for k in sorted(self._d, key=lambda k: self._d[k]["at"])[:len(self._d) - self.max]:
                    del self._d[k]
        return True

    def drop(self, user_id):
        with self._lock:
            self._d.pop(str(user_id), None)

    def clear(self):
        with self._lock:
            self._d.clear()

    def __len__(self):
        return len(self._d)


STORE = Store()
