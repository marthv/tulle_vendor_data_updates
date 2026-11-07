"""Item 4 of the owner's 2026-11-07 Planner list: florists carry their real pricing; empty categories offer the
nearest state that has them."""
from concurrent.futures import ThreadPoolExecutor

import agent
import config
import free_mode
import neighbors
import xano

ROW = {"Vendor_ID": "F1", "id": 1, "Name": "Bloomery Flower Studio", "Category": "Flowers", "State": "New Jersey",
       "flt_fl_tier": "Mid", "flt_fl_tiers": ["Mid", "Luxury"], "flt_fl_est": 6600, "flt_fl_bouquet_from": 325,
       "flt_fl_centerpiece_from": 200, "flt_fl_min": 0, "flt_fl_no_min": False, "flt_fl_bridesmaid_from": 0,
       "flt_fl_boutonniere_from": 20, "flt_fl_min_eff": 0}


def test_florist_pricing_reads_the_flt_fl_fields():
    fp = xano.florist_pricing(ROW)
    assert fp["tier"] == "Mid" and fp["typical_total"] == 6600 and "min_spend" not in fp
    assert fp["from"] == {"bouquet": 325, "centerpiece": 200, "boutonniere": 20}
    assert xano.florist_pricing({"flt_fl_min_eff": 1})["no_minimum"] is True
    assert xano.florist_pricing({"Name": "x"}) is None


def test_listing_item_carries_it_only_for_florists_and_only_with_the_flag(monkeypatch):
    monkeypatch.setattr(config, "FLORIST_PRICING", True)
    assert "florist_pricing" in xano._listing_item(ROW, "Flowers", "New Jersey", [])
    assert "florist_pricing" not in xano._listing_item(ROW, "Beauty", "New Jersey", [])
    monkeypatch.setattr(config, "FLORIST_PRICING", False)
    assert "florist_pricing" not in xano._listing_item(ROW, "Flowers", "New Jersey", [])


def test_saved_florist_cards_stay_figure_free(monkeypatch):
    monkeypatch.setattr(config, "FLORIST_PRICING", True)
    assert "florist_pricing" not in xano.card_from_row(ROW)


def test_levels_full_light_free(monkeypatch):
    monkeypatch.setattr(config, "FLORIST_PRICING", True)
    v = xano._listing_item(ROW, "Flowers", "New Jersey", [])
    full = agent._listing_card(v, "full")["price_note"]
    assert full == "Typical florals about $6,600 · bouquets from $325"
    light = agent._listing_card(v, "light")["price_note"]
    assert light == "Typical wedding florals about $6K-$7K · Mid tier"
    assert "price_note" not in agent._listing_card(v, "free")
    assert agent._listing_row(v, "light")["pricing"]["price_band"]["low"] == 6000
    assert "typical_total" not in agent._listing_row(v, "light")["pricing"]
    assert "pricing" not in agent._listing_row(v, "free")
    assert "price_note" not in free_mode.card(agent._listing_card(v, "full"))


def test_nearby_states_with_matches_most_first_and_skips_errors():
    counts = {"Kansas": 40, "Illinois": 12, "Arkansas": 0}

    def count(s):
        if s == "Iowa":
            raise RuntimeError("blip")
        return counts.get(s, 0)
    with ThreadPoolExecutor(4) as pool:
        got = neighbors.nearby_with_matches("Missouri", count, pool)
    assert got == [{"state": "Kansas", "count": 40}, {"state": "Illinois", "count": 12}]
    assert neighbors.nearby_with_matches("Hawaii", count, None) == []
    assert all(s in neighbors.NEIGHBORS for ns in neighbors.NEIGHBORS.values() for s in ns)
