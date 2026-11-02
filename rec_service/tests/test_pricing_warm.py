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
    calls = {"list": [], "pricing": []}

    def top(per_state=40, max_pages=4):
        calls["list"].append(per_state)
        return ["V1", "V2"]

    def pricing(token, vid, paid):
        calls["pricing"].append((token, vid, paid))
        if vid == "V2":
            raise RuntimeError("ep231 500")
        return _panel(vid)
    monkeypatch.setattr(xano, "top_venues_for_warm", top)
    monkeypatch.setattr(xano, "venue_pricing", pricing)
    out = pricing_warm.warm(per_state=5, parallel=2, log=lambda *a: None)
    assert out["venues"] == 2 and out["cached"] == 1 and out["failed"] == 1
    assert calls["list"] == [5]
    assert all(tok is None and paid is False for tok, _, paid in calls["pricing"])   # ep231 is public
    assert xano.PRICING_CACHE.get(("ep231", "V1")) is not None                 # the free-tier key, nothing else
    assert xano.PRICING_CACHE.get(("ep230", "V1", "entitled")) is None
    assert pricing_warm.last["cached"] == 1


def test_warm_survives_a_failed_venue_list(monkeypatch):
    def boom(per_state=40, max_pages=4):
        raise RuntimeError("meta 500")
    monkeypatch.setattr(xano, "top_venues_for_warm", boom)
    out = pricing_warm.warm(log=lambda *a: None)
    assert out["venues"] == 0 and out["failed"] == 1


def test_top_venues_for_warm_caps_per_state_and_skips_unvalidated(monkeypatch):
    rows = [{"Vendor_ID": "V%d" % i, "State": "Texas" if i % 2 else ["New Jersey"], "Validated_Data": "1",
             "Clicks_on_Venue": 100 - i} for i in range(10)]
    rows.append({"Vendor_ID": "V99", "State": "Texas", "Validated_Data": "0", "Clicks_on_Venue": 999})
    monkeypatch.setattr(xano, "_meta", lambda m, p, **kw: {"items": rows, "nextPage": None})
    ids = xano.top_venues_for_warm(per_state=2)
    assert ids == ["V0", "V1", "V2", "V3"] and "V99" not in ids


def test_start_is_a_no_op_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "PRICING_WARM", False)
    monkeypatch.setattr(pricing_warm, "_started", False)
    assert pricing_warm.start() is False


def test_states_are_title_cased_names():
    assert "New York" in pricing_warm.STATES and "District of Columbia" in pricing_warm.STATES
    assert len(pricing_warm.STATES) == 51
