"""Railway search for the Tulle Assistant (owner decision 2026-10-26): a stable REC_RAILWAY_SEARCH_PCT share of
assistant users get their VENUE searches (the one-ep119-call-per-state/page path in xano._search_one) from the
Railway search-api (tulle-search repo, Meilisearch, ep119-compatible params + response). ep119 is the safety net:
a Railway timeout / connection error / non-200 / response without the ep119 shape -> the same search goes to ep119
straight away. Default 0 = off: nobody is bucketed into Railway and the search-api is never called.

Also on this path since 2026-10-26: the listings-only categories (xano._search_listing_one - Flowers, Beauty, Food and
Beverage, Wedding Planner), same params shape with Category_Input set; their searches count in the same SearchLog.
Unchanged (never Railway): the radius path (xano.venues_near, table 11), photographers/entertainment
(xano._search_pe_one), pricing (ep230/231).

Per request a SearchLog records every venue search (thread-safe: _interleave runs one search per state in
parallel threads) -> the rec_usage columns search_engine / search_fallback / search_ms and the Mixpanel
rec_server_request props (see SearchLog.summary).

CIRCUIT BREAKER (2026-10-27, owner OK with the move to 100%): the search-api still freezes every 2-3 h. Without a
breaker every search during a freeze paid the full timeout (connect 1.0 s / read REC_RAILWAY_SEARCH_TIMEOUT_S) before
ep119. Now a timeout, connection error or 5xx opens the breaker for config.RAILWAY_BREAKER_S
(60 s): searches in that window go straight to ep119 (fallback reason 'breaker_open'), and the first search after it
tries Railway again. 4xx and bad_shape answers do not trip it (a request problem, not a freeze). One process-wide
breaker: Railway runs one rec-engine replica."""
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
    if user_id not in (None, "") and str(user_id) in config.RAILWAY_SEARCH_FORCE_IDS:
        return RAILWAY   # forced users (REC_RAILWAY_SEARCH_FORCE_USER_IDS) - ep119 is still their fallback
    if pct <= 0 or user_id in (None, ""):
        return EP119
    if pct >= 100:
        return RAILWAY
    b = int(hashlib.sha256(("railsearch:%s" % user_id).encode()).hexdigest(), 16) % 100
    return RAILWAY if b < pct else EP119


class Breaker:
    """Process-wide circuit breaker for the search-api. Thread-safe. open_until is time.monotonic() seconds."""

    def __init__(self):
        self._lock = threading.Lock()
        self.open_until = 0.0
        self.trips = 0          # since process start (for logs / tests)

    def is_open(self, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            return now < self.open_until

    def trip(self, now=None):
        if config.RAILWAY_BREAKER_S <= 0:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            self.open_until = now + config.RAILWAY_BREAKER_S
            self.trips += 1

    def reset(self):
        with self._lock:
            self.open_until = 0.0
            self.trips = 0


BREAKER = Breaker()
BREAKER_SKIP = "breaker_open"


def trips_breaker(why):
    """Freeze-like failures open the breaker: timeout, connection error, 5xx."""
    return why in ("timeout", "error") or (isinstance(why, str) and why.startswith("http_5"))


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
                # Breaker (2026-10-27): searches this request sent straight to ep119 because it was open, and its
                # state when the request's searches were summarised.
                "breaker_skips": sum(1 for f in fallbacks if f == BREAKER_SKIP),
                "breaker_state": "open" if BREAKER.is_open() else "closed",
                "railway_search_ms": sum(c["ms"] for c in calls if c["engine"] == RAILWAY),
                "ep119_search_ms": sum(c["ms"] for c in calls if c["engine"] == EP119)}


def _ep119_shape(d):
    """ep119 answers {"items": [...], "itemsTotal": N, ...}; _search_one reads exactly those two."""
    return (isinstance(d, dict) and isinstance(d.get("items"), list)
            and isinstance(d.get("itemsTotal"), (int, float)) and not isinstance(d.get("itemsTotal"), bool))


def railway_get(token, params):
    """One search-api call. Returns (data, None) or (None, fallback_reason)."""
    # connect 1.0s, read REC_RAILWAY_SEARCH_TIMEOUT_S: a stalled Railway falls back to ep119 within ~4s worst case.
    try:
        r = requests.get(config.RAILWAY_SEARCH_URL, params=params, timeout=(1.0, config.RAILWAY_SEARCH_TIMEOUT_S),
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
        if BREAKER.is_open():
            why = BREAKER_SKIP           # search-api failed in the last RAILWAY_BREAKER_S: don't wait on it again
        else:
            t = time.time()
            d, why = railway_get(token, params)
            log.add_call(RAILWAY, (time.time() - t) * 1000, why or "")
            if why is None:
                log.add_search(RAILWAY)
                return d
            if trips_breaker(why):
                BREAKER.trip()
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
