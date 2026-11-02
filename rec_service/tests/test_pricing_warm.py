"""Pricing-cache warmer (2026-11-02): fills the free-tier panel cache for the top venues per state, swallows failures."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import pricing_warm  # noqa: E402
import xano  # noqa: E402


def _panel(vid):
    return {"vendor_id": vid, "headline": "From", "guest_minimum": 0, "verdict": "", "lines": [{"line": "Rental fee"}],
            "source_note": "", "cost_inputs": None, "access_reason": None}


def test_warm_fills_the_free_tier_cache_and_counts_failures(monkeypatch):
    xano.PRICING_CACHE.clear()
    monkeypatch.setattr(config, "PRICING_CACHE_S", 3600)
    monkeypatch.setattr(xano.PRICING_CACHE, "ttl", 3600)
    calls = {"search": [], "pricing": []}

    def search(token, *, states, page_size=8, search_log=None, **f):
        calls["search"].append((token, tuple(states), page_size, f.get("sort_by")))
        if states == ["Ohio"]:
            raise RuntimeError("ep119 down")
        return {"total_matches": 3, "venues": [{"vendor_id": "V1"}, {"vendor_id": "V2"}, {"vendor_id": "V1"}]}

    def pricing(token, vid, paid):
        calls["pricing"].append((token, vid, paid))
        if vid == "V2":
            raise RuntimeError("ep231 500")
        return _panel(vid)
    monkeypatch.setattr(xano, "search_venues", search)
    monkeypatch.setattr(xano, "venue_pricing", pricing)
    out = pricing_warm.warm(states=["New York", "Ohio"], per_state=5, parallel=2, log=lambda *a: None)
    assert out["states"] == 1 and out["venues"] == 2 and out["cached"] == 1 and out["failed"] == 2
    assert calls["search"][0] == (None, ("New York",), 5, "popular_desc")      # no token: ep119 + ep231 are public
    assert all(tok is None and paid is False for tok, _, paid in calls["pricing"])
    assert xano.PRICING_CACHE.get(("ep231", "V1")) is not None                 # the free-tier key, nothing else
    assert xano.PRICING_CACHE.get(("ep230", "V1", "entitled")) is None
    assert pricing_warm.last["cached"] == 1


def test_start_is_a_no_op_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "PRICING_WARM", False)
    monkeypatch.setattr(pricing_warm, "_started", False)
    assert pricing_warm.start() is False


def test_states_are_title_cased_names():
    assert "New York" in pricing_warm.STATES and "District of Columbia" in pricing_warm.STATES
    assert len(pricing_warm.STATES) == 51
