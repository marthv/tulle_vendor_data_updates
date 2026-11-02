"""Pricing-cache warmer (2026-11-02 speed pass).

WHY. Measured on 1,000 Planner requests (Mixpanel rec_server_request, 2026-10-24..11-01): 74% of openings and 71% of
typed questions fetched at least one venue's pricing panel that was not in the in-process cache, costing a median
2.7 s / 2.2 s per request (p90 5.6 s / 4.6 s). Requests whose venues were all cached were 3-4 s faster end to end
(opening 9.4 s -> 6.4 s, question 10.9 s -> 7.0 s). The per-venue hit rate was 37% (openings) and 18% (questions):
a one-hour TTL plus a cache that starts empty on every deploy (several a day lately).

WHAT. (1) config.PRICING_CACHE_S default 1 h -> 12 h. (2) This module fills the FREE-tier panel (ep231, public, no
token, Xano-response-cached) for the most popular venues of every state at start-up and every PRICING_WARM_EVERY_S,
in a daemon thread with a small pool so live requests are never starved. The paid panel (ep230) needs the member's
own token, so it is not warmed; the longer TTL is what helps there. Never raises; failures are counted and logged.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import config
import lookup
import xano

STATES = sorted(s.title().replace(" Of ", " of ") for s in lookup.US_STATES)
_lock = threading.Lock()
_started = False
last = {"at": 0, "states": 0, "venues": 0, "cached": 0, "failed": 0, "ms": 0}


def warm(states=None, per_state=None, parallel=None, log=print):
    """Price the top `per_state` venues (most clicked) of each state through the free-tier cache. Returns the counts.
    Every failure is counted, never raised. `states` is kept for the signature only: the list is national, capped
    per state."""
    per_state = int(per_state or config.PRICING_WARM_PER_STATE)
    parallel = max(1, int(parallel or config.PRICING_WARM_PARALLEL))
    t0 = time.time()
    stats = {"states": 0, "venues": 0, "cached": 0, "failed": 0}
    # ep119 is auth=user (the first warm-up, 2026-11-02 01:22 UTC, failed on all 51 states with no token), so the
    # venue list comes from table 11 through the metadata token instead: most clicked first, `per_state` per state.
    try:
        ids = xano.top_venues_for_warm(per_state=per_state)
        stats["states"] = len({s for s in states or []}) if states else 1
    except Exception as e:
        ids = []
        stats["failed"] += 1
        log("pricing warm: venue list failed: %s" % repr(e)[:160])
    stats["venues"] = len(ids)

    def one(vid):
        try:
            p = xano.cached_venue_pricing(None, vid, False)
            return 1 if p else 0
        except Exception:
            return -1
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        for got in ex.map(one, ids):
            if got > 0:
                stats["cached"] += 1
            elif got < 0:
                stats["failed"] += 1
    stats["ms"] = int((time.time() - t0) * 1000)
    with _lock:
        last.update(stats, at=int(time.time()))
    log("pricing warm: %d states, %d venues, %d cached, %d failed, %.1f s"
        % (stats["states"], stats["venues"], stats["cached"], stats["failed"], stats["ms"] / 1000))
    return stats


def _loop():
    time.sleep(config.PRICING_WARM_DELAY_S)   # let the service answer its first requests before the warm-up starts
    while True:
        try:
            warm()
        except Exception as e:   # belt and braces: warm() already swallows everything
            print("pricing warm: loop error", repr(e)[:200])
        time.sleep(max(300, config.PRICING_WARM_EVERY_S))


def start():
    """Start the background warmer once (no-op when disabled or already running). Called from app start-up."""
    global _started
    with _lock:
        if _started or not config.PRICING_WARM or config.PRICING_CACHE_S <= 0:
            return False
        _started = True
    threading.Thread(target=_loop, name="pricing-warm", daemon=True).start()
    return True
