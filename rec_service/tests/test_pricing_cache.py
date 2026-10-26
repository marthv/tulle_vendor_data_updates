"""Cross-request pricing cache (2026-10-26 speed pass): keyed only by what ep230/ep231 depend on, never shares a
paid payload with a free or locked user, skips errors and empty panels, evicts oldest, expires, and reports
hits / misses per request; plus the per-model-call timing props."""
import pathlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import app  # noqa: E402
import xano  # noqa: E402

FOREVER = {"id": 1, "forever_access_purchased": True, "total_amount_paid": 129}
FOUR_WEEK = {"id": 2, "forever_access_purchased": False, "total_amount_paid": 59}
ONE_WEEK = {"id": 3, "forever_access_purchased": False, "total_amount_paid": 32}
NO_FIELDS = {"id": 4}


def panel(vid, reason=None, tag="x"):
    return {"vendor_id": vid, "headline": "From " + tag, "guest_minimum": 50, "verdict": None,
            "lines": [{"line": "Rental", "amount": 1000}], "source_note": None, "cost_inputs": None,
            "access_reason": reason}


@pytest.fixture
def cache_on(monkeypatch):
    monkeypatch.setattr(xano.PRICING_CACHE, "ttl", 3600)
    calls = []

    def fake(token, vid, paid):
        calls.append((token, vid, paid))
        # what ep230 would render for the token's owner, by token
        reason = {"t-forever": "forever", "t-4w": "paid", "t-1w": "below_threshold"}.get(token) if paid else None
        return panel(vid, reason, tag="%s-%s" % (token, paid))

    monkeypatch.setattr(xano, "venue_pricing", fake)
    return calls


def test_entitlement_mirrors_ep230():
    assert xano.pi_entitled(FOREVER) is True
    assert xano.pi_entitled(FOUR_WEEK) is True
    assert xano.pi_entitled({"total_amount_paid": 50}) is True      # inclusive >=, as ep230
    assert xano.pi_entitled(ONE_WEEK) is False
    assert xano.pi_entitled({"total_amount_paid": None}) is False   # Number(null) || 0
    assert xano.pi_entitled(NO_FIELDS) is None                      # unknown -> never cached
    assert xano.pi_entitled(None) is None


def test_keys_separate_free_entitled_and_locked():
    assert xano.pricing_cache_key("V1", False) == ("ep231", "V1")
    assert xano.pricing_cache_key("V1", True, FOREVER) == ("ep230", "V1", "entitled")
    assert xano.pricing_cache_key("V1", True, FOUR_WEEK) == ("ep230", "V1", "entitled")
    assert xano.pricing_cache_key("V1", True, ONE_WEEK) == ("ep230", "V1", "locked")
    assert xano.pricing_cache_key("V1", True, NO_FIELDS) is None
    assert xano.pricing_cache_key("", False) is None


def test_hit_on_second_call_and_stats(cache_on):
    s = xano.PricingStats()
    a = xano.cached_venue_pricing("t-forever", "V1", True, user=FOREVER, stats=s)
    b = xano.cached_venue_pricing("t-other", "V1", True, user=FOUR_WEEK, stats=s)   # same entitlement class
    assert a == b and len(cache_on) == 1
    assert s.as_dict() == {"hits": 1, "misses": 1, "bypass": 0}


def test_paid_payload_never_reaches_free_or_locked_user(cache_on):
    paid = xano.cached_venue_pricing("t-forever", "V1", True, user=FOREVER)
    free = xano.cached_venue_pricing(None, "V1", False, user=FOREVER)
    locked = xano.cached_venue_pricing("t-1w", "V1", True, user=ONE_WEEK)
    assert len(cache_on) == 3
    assert paid["headline"] != free["headline"] != locked["headline"]
    assert locked["access_reason"] == "below_threshold"


def test_mismatched_access_reason_is_not_stored(monkeypatch, cache_on):
    # auth/me said 1-week (locked) but they bought Forever before ep230 ran: ep230 renders the ENTITLED payload,
    # which must not be filed under the locked key.
    monkeypatch.setattr(xano, "venue_pricing", lambda t, vid, paid: panel(vid, "forever"))
    xano.cached_venue_pricing("t", "V1", True, user=ONE_WEEK)
    assert len(xano.PRICING_CACHE) == 0
    monkeypatch.setattr(xano, "venue_pricing", lambda t, vid, paid: panel(vid, None))   # no reason -> not stored
    xano.cached_venue_pricing("t", "V1", True, user=FOREVER)
    assert len(xano.PRICING_CACHE) == 0


def test_unknown_entitlement_bypasses(cache_on):
    s = xano.PricingStats()
    xano.cached_venue_pricing("t-4w", "V1", True, user=NO_FIELDS, stats=s)
    xano.cached_venue_pricing("t-4w", "V1", True, user=NO_FIELDS, stats=s)
    assert len(cache_on) == 2 and s.as_dict() == {"hits": 0, "misses": 0, "bypass": 2}


def test_errors_and_empty_panels_not_cached(monkeypatch, cache_on):
    def boom(t, vid, paid):
        raise xano.XanoError("500")
    monkeypatch.setattr(xano, "venue_pricing", boom)
    with pytest.raises(xano.XanoError):
        xano.cached_venue_pricing(None, "V1", False)
    empty = {"vendor_id": "V1", "headline": None, "guest_minimum": None, "verdict": None, "lines": [],
             "source_note": None, "cost_inputs": None, "access_reason": None}
    monkeypatch.setattr(xano, "venue_pricing", lambda t, vid, paid: dict(empty))
    xano.cached_venue_pricing(None, "V1", False)
    assert len(xano.PRICING_CACHE) == 0


def test_ttl_zero_disables(monkeypatch, cache_on):
    monkeypatch.setattr(xano.PRICING_CACHE, "ttl", 0)
    xano.cached_venue_pricing(None, "V1", False)
    xano.cached_venue_pricing(None, "V1", False)
    assert len(cache_on) == 2 and len(xano.PRICING_CACHE) == 0


def test_expiry_and_lru_eviction(monkeypatch):
    c = xano._TTLCache(10, 2)
    now = [1000.0]
    monkeypatch.setattr(xano.time, "monotonic", lambda: now[0])
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1        # a is now most recent
    c.put("c", 3)                 # evicts b (least recently used)
    assert c.get("b") is None and c.get("a") == 1 and c.get("c") == 3
    now[0] += 11
    assert c.get("a") is None and len(c) == 1


def test_values_are_copies(cache_on):
    a = xano.cached_venue_pricing(None, "V1", False)
    a["lines"].append("mutated")
    assert xano.cached_venue_pricing(None, "V1", False)["lines"] == [{"line": "Rental", "amount": 1000}]


def test_thread_safe_under_parallel_waves(cache_on):
    s = xano.PricingStats()
    vids = ["V%d" % (i % 10) for i in range(200)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        out = list(pool.map(lambda v: xano.cached_venue_pricing(None, v, False, stats=s), vids))
    d = s.as_dict()
    assert all(o["vendor_id"] == v for o, v in zip(out, vids))
    assert d["hits"] + d["misses"] == 200 and d["misses"] >= 10 and len(xano.PRICING_CACHE) == 10


def test_venue_pricing_carries_access_reason_only_for_paid(monkeypatch):
    raw = {"space": {"headline_prefix": "From", "components": [], "band": {}}, "access_reason": "paid"}
    monkeypatch.setattr(xano, "_get", lambda *a, **k: raw)
    assert xano.venue_pricing("t", "V1", True)["access_reason"] == "paid"
    assert xano.venue_pricing("t", "V1", False)["access_reason"] is None


def test_turn_props():
    usage = {"turns": [{"ms": 3000}, {"ms": 2500}], "turn_ms": [3000, 2500],
             "turn_tools": [["search_venues"], ["present_recommendations"]],
             "pricing_cache": {"hits": 9, "misses": 4, "bypass": 0}}
    p = app._turn_props(usage)
    assert p["model_turns"] == 2 and p["turn1_model_ms"] == 3000 and p["turn2_model_ms"] == 2500
    assert p["turn1_tools"] == ["search_venues"] and p["turn_ms"] == [3000, 2500]
    assert (p["pricing_cache_hits"], p["pricing_cache_misses"]) == (9, 4)
    empty = app._turn_props({})
    assert empty["turn1_model_ms"] is None and empty["model_turns"] == 0 and empty["pricing_cache_hits"] == 0


# ---------------------------------------------------------------- agent.run end to end (fake tool runner)

def _run_agent(monkeypatch, user, calls, seen):
    import copy
    import time
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import agent
    import config
    from test_progress_stop import PRICING, _fake_client
    monkeypatch.setattr(config, "EARLY_STOP", True)
    monkeypatch.setattr(config, "RAILWAY_SEARCH_PCT", 0)
    items = [{"id": i, "Vendor_ID": "V%d" % i, "Name": "Venue %d" % i, "State": "Texas"} for i in range(1, 6)]
    monkeypatch.setattr(xano, "_get", lambda url, token=None, params=None, extra_headers=None:
                        {"items": items, "itemsTotal": len(items)})

    def price(tok, vid, paid):
        calls.append(vid)
        return dict(copy.deepcopy(PRICING), vendor_id=vid, headline="From $5,000", access_reason="forever")
    monkeypatch.setattr(xano, "venue_pricing", price)
    monkeypatch.setattr(xano, "market_benchmarks", lambda s, g=0: {"state": s, "metrics": {}})
    monkeypatch.setattr(xano, "saved_vendor_ids", lambda u: [])
    monkeypatch.setattr(xano, "vendor_card", lambda vid: {"vendor_id": vid, "name": vid})
    monkeypatch.setattr(xano, "state_average_rows", lambda tok: [])
    monkeypatch.setattr(xano, "guest_modifier", lambda tok, n: 1.0)
    seen["on_model"] = lambda i: time.sleep(0.05 if i == 0 else 0.02)
    turns = [[("search_venues", {"states": ["Texas"]})],
             [("present_recommendations", {"message": "Picks.", "vendor_ids": [], "reasons": [], "chips": []})]]
    monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(turns, seen), raising=False)
    return agent.run("tok", user, True, [{"role": "user", "content": "venues in Texas"}],
                     profile_override={"Wedding_Location_Updated": ["Texas"]}, detail="full")


def test_agent_second_request_hits_cache_and_logs_turns(monkeypatch):
    monkeypatch.setattr(xano.PRICING_CACHE, "ttl", 3600)
    calls = []
    _, u1 = _run_agent(monkeypatch, dict(FOREVER), calls, {})
    n1 = len(calls)
    assert n1 > 0 and u1["pricing_cache"] == {"hits": 0, "misses": n1, "bypass": 0}
    _, u2 = _run_agent(monkeypatch, dict(FOUR_WEEK, id=9), calls, {})   # another entitled user
    assert len(calls) == n1 and u2["pricing_cache"]["hits"] == n1 and u2["pricing_cache"]["misses"] == 0
    # a locked (1-week) user gets no hit from the entitled payloads
    _, u3 = _run_agent(monkeypatch, dict(ONE_WEEK), calls, {})
    assert u3["pricing_cache"]["hits"] == 0 and len(calls) == 2 * n1
    # per-model-call timing
    assert u1["turn_tools"] == [["search_venues"], ["present_recommendations"]]
    assert len(u1["turn_ms"]) == 2 and u1["turn_ms"][0] >= 45 and u1["turn_ms"][1] >= 15
    assert u1["turn_ms_has_tools"] is False
    p = app._turn_props(u1)
    assert p["turn1_model_ms"] == u1["turn_ms"][0] and p["model_turns"] == 2
