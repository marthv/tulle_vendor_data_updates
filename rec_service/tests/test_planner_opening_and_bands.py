"""Items 2 + 3 of the owner's 2026-11-07 Planner list: no Forever in the first message; LIGHT price bands."""
import pytest

import agent
import config
import free_mode
import upsell


# ---------------------------------------------------------------- item 2: the first message

def test_forever_sentence_is_dropped_and_the_useful_clause_kept():
    t = ("Since your budget is tight, I'd start with The Lakeview Room. Full prices are in each venue's PDF, and "
         "Forever unlocks them here. Open The Lakeview Room's PDF first. Which city or area?")
    out = upsell.strip_forever(t)
    assert "Forever" not in out and "Full prices are in each venue's PDF." in out and out.endswith("Which city or area?")


def test_strip_forever_never_returns_empty():
    assert upsell.strip_forever("Forever unlocks it.") == "Forever unlocks it."
    assert upsell.strip_forever("") == ""


# ---------------------------------------------------------------- item 3: price bands

@pytest.mark.parametrize("n,lo,hi", [(31100, 30000, 35000), (3600, 3000, 4000), (30000, 30000, 35000),
                                     (9999, 9000, 10000), (850, 0, 1000)])
def test_band_edges_round_outward(n, lo, hi):
    assert agent._band_edges(n) == (lo, hi)


def test_light_card_carries_a_band_from_the_estimate_else_the_rental(monkeypatch):
    monkeypatch.setattr(config, "LIGHT_PRICE_BANDS", True)
    c = agent._light_card({"vendor_id": "V1", "estimate_total": 31100, "estimate_guests": 125,
                           "venue_fee_range": [5000, 5000], "estimate_label": "About $31,100"})
    assert "estimate_total" not in c and "venue_fee_range" not in c and "estimate_label" not in c
    assert c["price_band"]["label"].startswith("about $30K-$35K all-in for 125 guests")
    r = agent._light_card({"vendor_id": "V2", "venue_fee_range": [3600, 3600]})
    assert r["price_band"]["label"] == "rental about $3K-$4K"
    assert "price_band" not in agent._light_card({"vendor_id": "V3"})


def test_bands_off_means_light_cards_exactly_as_before(monkeypatch):
    monkeypatch.setattr(config, "LIGHT_PRICE_BANDS", False)
    c = agent._light_card({"vendor_id": "V1", "estimate_total": 31100, "estimate_guests": 125})
    assert c == {"vendor_id": "V1"}


def test_free_mode_never_shows_a_band(monkeypatch):
    monkeypatch.setattr(config, "LIGHT_PRICE_BANDS", True)
    c = agent._light_card({"vendor_id": "V1", "name": "X", "estimate_total": 31100, "estimate_guests": 125})
    assert "price_band" not in free_mode.card(c)
