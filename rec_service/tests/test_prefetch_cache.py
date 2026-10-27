"""Prefetch caches (2026-10-27): state benchmarks by (state, capacity band) and Saved-vendor cards by id, shared
across requests; failures / empty results never cached; deep copies in and out; the per-part prefetch timing and
hit / miss counters reach usage and the Mixpanel props."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import app  # noqa: E402
import xano  # noqa: E402


@pytest.fixture
def caches_on(monkeypatch):
    monkeypatch.setattr(xano.BENCH_CACHE, "ttl", 3600)
    monkeypatch.setattr(xano.CARD_CACHE, "ttl", 3600)
    calls = {"bench": [], "card": []}

    def bench(state, guests=0):
        calls["bench"].append((state, guests))
        return {"state": state, "metrics": {"venue_fee": {"median": 9000}} if state != "Nowhere" else {}}

    def card(vid):
        calls["card"].append(vid)
        return None if vid == "MISSING" else {"vendor_id": vid, "name": "Venue " + vid, "state": "Texas"}

    monkeypatch.setattr(xano, "market_benchmarks", bench)
    monkeypatch.setattr(xano, "vendor_card", card)
    return calls


def test_bench_cached_by_state_and_band(caches_on):
    st = xano.CacheStats()
    xano.cached_market_benchmarks("Texas", 120, st)
    xano.cached_market_benchmarks("Texas", 140, st)    # same band (76-150): hit
    xano.cached_market_benchmarks("Texas", 200, st)    # other band: miss
    xano.cached_market_benchmarks("Ohio", 120, st)     # other state: miss
    xano.cached_market_benchmarks("Texas", 0, st)      # no guest count = its own key
    assert caches_on["bench"] == [("Texas", 120), ("Texas", 200), ("Ohio", 120), ("Texas", 0)]
    assert st.as_dict()["bench_hits"] == 1 and st.as_dict()["bench_misses"] == 4


def test_bench_without_metrics_is_not_cached(caches_on):
    xano.cached_market_benchmarks("Nowhere", 100)
    xano.cached_market_benchmarks("Nowhere", 100)
    assert len(caches_on["bench"]) == 2


def test_card_cached_by_id_none_not_cached_and_copies(caches_on):
    st = xano.CacheStats()
    a = xano.cached_vendor_card("V1", st)
    a["name"] = "mutated by a caller"
    b = xano.cached_vendor_card("V1", st)
    assert b["name"] == "Venue V1"                      # deep copy: a caller can't poison the cache
    xano.cached_vendor_card("MISSING", st)
    xano.cached_vendor_card("MISSING", st)
    assert caches_on["card"] == ["V1", "MISSING", "MISSING"]
    assert st.as_dict() == {"bench_hits": 0, "bench_misses": 0, "card_hits": 1, "card_misses": 3}


def test_ttl_zero_is_off(caches_on, monkeypatch):
    monkeypatch.setattr(xano.BENCH_CACHE, "ttl", 0)
    monkeypatch.setattr(xano.CARD_CACHE, "ttl", 0)
    xano.cached_market_benchmarks("Texas", 120)
    xano.cached_market_benchmarks("Texas", 120)
    xano.cached_vendor_card("V1")
    xano.cached_vendor_card("V1")
    assert len(caches_on["bench"]) == 2 and caches_on["card"] == ["V1", "V1"]


def test_keys_carry_no_user_so_every_tier_gets_the_same_rows(caches_on):
    # Neither read uses a token or entitlement (table 63 / table 11 via the metadata API) - one key per state/band
    # or vendor is correct for every tier. The tier rules (LIGHT/FREE get no benchmarks, figure stripping) are
    # applied per request in agent.run, after the cache.
    xano.cached_vendor_card("V9")
    assert list(k for k, _ in xano.CARD_CACHE._d.items()) == [("card", "V9")]


def test_prefetch_props():
    u = {"prefetch_bench_ms": 12, "prefetch_saved_ms": 340, "prefetch_saved_n": 4,
         "prefetch_cache": {"bench_hits": 1, "bench_misses": 0, "card_hits": 3, "card_misses": 1}}
    p = app._turn_props(u)
    assert p["prefetch_bench_ms"] == 12 and p["prefetch_saved_ms"] == 340 and p["prefetch_saved_n"] == 4
    assert (p["bench_cache_hits"], p["bench_cache_misses"], p["card_cache_hits"], p["card_cache_misses"]) == (1, 0, 3, 1)
    assert app._turn_props({})["card_cache_hits"] == 0


# ---------------------------------------------------------------- agent.run end to end (fake model)

class _Msg:
    def __init__(self):
        import types
        self.content, self.model = [], "fake"
        self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)


def _run(monkeypatch, user, detail="full"):
    import types
    import agent

    class Runner:
        def __init__(self, tools, **k):
            self.tools = {(getattr(t, "name", None) or getattr(t, "__name__", None)): t for t in tools}

        def __iter__(self):
            t = self.tools["present_recommendations"]
            args = {"message": "m", "vendor_ids": [], "reasons": [], "chips": []}
            t.call(args) if hasattr(t, "call") else t(**args)
            yield _Msg()

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **k: Runner(**k)))
    monkeypatch.setattr(agent.anthropic, "Anthropic", Client, raising=False)
    return agent.run("tok", user, True, [{"role": "user", "content": "hi"}], detail=detail)[1]


def test_agent_prefetch_uses_caches_and_reports_parts(caches_on, monkeypatch):
    user = {"id": 3, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 120,
            "favorited_vendors": ["V1", "V2"]}
    u1 = _run(monkeypatch, user)
    assert u1["prefetch_cache"] == {"bench_hits": 0, "bench_misses": 1, "card_hits": 0, "card_misses": 2}
    assert u1["prefetch_saved_n"] == 2 and u1["prefetch_bench_ms"] >= 0 and u1["prefetch_saved_ms"] >= 0
    u2 = _run(monkeypatch, user)
    assert u2["prefetch_cache"] == {"bench_hits": 1, "bench_misses": 0, "card_hits": 2, "card_misses": 0}
    assert len(caches_on["bench"]) == 1 and caches_on["card"] == ["V1", "V2"]


def test_light_and_free_still_get_no_benchmarks(caches_on, monkeypatch):
    user = {"id": 3, "Wedding_Location_Updated": ["Texas"], "favorited_vendors": []}
    for detail in ("light", "free"):
        u = _run(monkeypatch, user, detail)
        assert u["prefetch_cache"]["bench_misses"] == 0 and u["prefetch_cache"]["bench_hits"] == 0
    assert caches_on["bench"] == []
