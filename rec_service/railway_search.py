"""Railway search for the Tulle Assistant (owner decision 2026-10-26): a stable REC_RAILWAY_SEARCH_PCT share of
assistant users get their VENUE searches (the one-ep119-call-per-state/page path in xano._search_one) from the
Railway search-api (tulle-search repo, Meilisearch, ep119-compatible params + response). ep119 is the safety net:
a Railway timeout / connection error / non-200 / response without the ep119 shape -> the same search goes to ep119
straight away. Default 0 = off: nobody is bucketed into Railway and the search-api is never called.

Unchanged (never Railway): the radius path (xano.venues_near, table 11), photographers/entertainment
(xano._search_pe_one), pricing (ep230/231).

Per request a SearchLog records every venue search (thread-safe: _interleave runs one search per state in
parallel threads) -> the rec_usage columns search_engine / search_fallback / search_ms and the Mixpanel
rec_server_request props (see SearchLog.summary)."""
import hashlib
import threading
import time

import requests

import config

RAILWAY = "railway"
EP119 = "ep119"


def arm(user_id, pct=None):
    """'railway' or 'ep119' for this user. Stable: the same user always lands in the same arm for a given pct, and
    raising pct only moves users ep119 -> railway (bucket < pct). Free and paid users are both eligible."""
    pct = config.RAILWAY_SEARCH_PCT if pct is None else pct
    if pct <= 0 or user_id in (None, ""):
        return EP119
    if pct >= 100:
        return RAILWAY
    b = int(hashlib.sha256(("railsearch:%s" % user_id).encode()).hexdigest(), 16) % 100
    return RAILWAY if b < pct else EP119


class SearchLog:
    """What one assistant request's venue searches did. `arm` is the user's bucket."""

    def __init__(self, arm_name=EP119):
        self.arm = arm_name
        self._lock = threading.Lock()
        self.calls = []        # one per HTTP search: {"engine", "ms", "fallback"} (a fallback adds 2: railway + ep119)
        self.searches = []     # one per logical search (one state / page): {"engine": final, "fallback": reason|""}
        self.wall_ms = 0       # summed wall time of xano.search_venues calls (parallel states counted once)

    @property
    def railway(self):
        return self.arm == RAILWAY

    def add_call(self, engine, ms, fallback=""):
        with self._lock:
            self.calls.append({"engine": engine, "ms": int(ms), "fallback": fallback})

    def add_search(self, engine, fallback=""):
        with self._lock:
            self.searches.append({"engine": engine, "fallback": fallback})

    def add_wall(self, ms):
        with self._lock:
            self.wall_ms += int(ms)

    def summary(self):
        """search_engine: '' (no venue search ran) | 'ep119' | 'railway' (every search answered by Railway) |
        'mixed' (Railway arm, at least one search fell back to ep119). search_fallback: the first fallback reason
        ('timeout' | 'http_<code>' | 'error' | 'bad_shape') or ''. search_ms: total venue-search wall ms.
        railway_search_ms / ep119_search_ms: summed per-call ms by engine (a failed Railway attempt counts to
        railway; parallel per-state calls are each counted, so these can exceed search_ms)."""
        with self._lock:
            searches, calls, wall = list(self.searches), list(self.calls), self.wall_ms
        fallbacks = [s["fallback"] for s in searches if s["fallback"]]
        if not searches:
            engine = ""
        elif fallbacks:
            engine = "mixed"
        elif all(s["engine"] == RAILWAY for s in searches):
            engine = RAILWAY
        else:
            engine = EP119
        return {"search_engine": engine, "search_fallback": fallbacks[0] if fallbacks else "",
                "search_ms": wall, "search_fallbacks": len(fallbacks), "search_arm": self.arm,
                "railway_search_ms": sum(c["ms"] for c in calls if c["engine"] == RAILWAY),
                "ep119_search_ms": sum(c["ms"] for c in calls if c["engine"] == EP119)}


def _ep119_shape(d):
    """ep119 answers {"items": [...], "itemsTotal": N, ...}; _search_one reads exactly those two."""
    return (isinstance(d, dict) and isinstance(d.get("items"), list)
            and isinstance(d.get("itemsTotal"), (int, float)) and not isinstance(d.get("itemsTotal"), bool))


def railway_get(token, params):
    """One search-api call. Returns (data, None) or (None, fallback_reason)."""
    t = config.RAILWAY_SEARCH_TIMEOUT_S
    try:
        r = requests.get(config.RAILWAY_SEARCH_URL, params=params, timeout=(t, t),
                         headers={"Authorization": "Bearer " + token} if token else {})
    except requests.exceptions.Timeout:
        return None, "timeout"
    except Exception:
        return None, "error"
    if r.status_code != 200:
        return None, "http_%d" % r.status_code
    try:
        d = r.json()
    except Exception:
        return None, "bad_shape"
    return (d, None) if _ep119_shape(d) else (None, "bad_shape")


def fetch(ep119_get, token, params, log=None):
    """The venue search: Railway first when the request's user is in the Railway arm, else (and on any Railway
    failure) `ep119_get(token, params)` - exactly the call origin/main makes. With no log or the ep119 arm the
    search-api is never touched."""
    if log is not None and log.railway:
        t = time.time()
        d, why = railway_get(token, params)
        log.add_call(RAILWAY, (time.time() - t) * 1000, why or "")
        if why is None:
            log.add_search(RAILWAY)
            return d
        t = time.time()
        try:
            return ep119_get(token, params)
        finally:
            log.add_call(EP119, (time.time() - t) * 1000)
            log.add_search(EP119, why)
    t = time.time()
    try:
        return ep119_get(token, params)
    finally:
        if log is not None:
            log.add_call(EP119, (time.time() - t) * 1000)
            log.add_search(EP119)
