"""Catering / hair & makeup / planner prices from vendors' own PDFs in "Plan my wedding" (service_prices.py,
REC_WW_SERVICE_PRICES, owner 2026-11-18). Pure maths first, then the proposal with the flag on and off.

Mocked sheet data (odd numbers so a leak is findable):
  B1 Glow Studio      bride hair+makeup 455, trial 135, party hair+makeup 165 x 4 assumed, 18% gratuity
                      -> (455 + 135 + 660) * 1.18 = 1475
  C1 Fork & Flame     package 87 a guest (median of 77 / 87 / 97), bar 23 a guest, 22% service, 8.25% tax
  W1 Plan Co          month-of 2350, full 9150 -> month-of first
"""
import json

import pytest

import config
import service_prices as sp
import whole_wedding as ww
from test_whole_wedding import FREE, FULL, ask, proposal_of, rows_of, w  # noqa: F401


def item(vid, cat, key, low, unit, high=None, conf="high"):
    return {"Vendor_ID": vid, "category": cat, "item_key": key, "price_low": low, "price_high": high or low,
            "pricing_unit": unit, "confidence": conf}


PAYLOAD = {
    "quotes": [
        {"Vendor_ID": "B1", "category": "beauty", "currency": "USD", "gratuity_pct": 18, "created_at": 1},
        {"Vendor_ID": "C1", "category": "catering", "currency": "USD", "service_charge_pct": 22, "tax_rate_pct": 8.25,
         "minimum_spend": 5000, "created_at": 1},
        {"Vendor_ID": "W1", "category": "planner", "currency": "USD", "created_at": 1},
        {"Vendor_ID": "W2", "category": "planner", "currency": "EUR", "created_at": 1},
        {"Vendor_ID": "W3", "category": "planner", "currency": "USD", "created_at": 1},
    ],
    "items": [
        item("B1", "beauty", "bride_hair_makeup", 455, "flat"), item("B1", "beauty", "bride_trial", 135, "flat"),
        item("B1", "beauty", "party_hair_makeup", 165, "per_service"),
        item("B1", "beauty", "party_hair_makeup", 99, "per_service", conf="low"),      # low confidence: ignored
        item("C1", "catering", "package_per_person", 77, "per_person"),
        item("C1", "catering", "package_per_person", 87, "per_person"),
        item("C1", "catering", "package_per_person", 97, "per_person"),
        item("C1", "catering", "bar_package", 23, "per_person"),
        item("C1", "catering", "cake_cutting", 3, "per_person"),
        item("W1", "planner", "full_planning", 9150, "flat"), item("W1", "planner", "month_of", 2350, "flat"),
        item("W2", "planner", "month_of", 2000, "flat"),
        item("W3", "planner", "percentage_fee", 12, "percent"),
    ],
}
IDX = sp.index(PAYLOAD)


def test_beauty_bride_trial_assumed_party_and_gratuity():
    t = sp.typical("Beauty", "B1", by_vendor=IDX)
    assert t["amount"] == round((455 + 135 + 4 * 165) * 1.18) == 1475
    assert "4 bridal-party services at $165 (an assumption" in t["based_on"] and "Not a quote" in t["based_on"]


def test_catering_per_guest_median_fees_and_minimum_bar_left_out():
    t = sp.typical("Food and Beverage", "C1", guests=120, by_vendor=IDX)
    assert t["amount"] == round(87 * 120 * 1.22 * 1.0825)
    assert "$87 a guest for food" in t["based_on"] and "22% service and 8.25% tax" in t["based_on"]
    assert "Bar, rentals" in t["based_on"]
    small = sp.typical("Food and Beverage", "C1", guests=20, by_vendor=IDX)
    assert small["amount"] == 5000 and "raised to their $5,000 minimum" in small["based_on"]
    assert sp.typical("Food and Beverage", "C1", guests=None, by_vendor=IDX) is None    # no guests -> no per-guest maths


def test_planner_tier_order_percent_fee_and_currency():
    t = sp.typical("Wedding Planner", "W1", by_vendor=IDX)
    assert t["amount"] == 2350 and "month-of coordination from $2,350" in t["based_on"]
    assert sp.typical("Wedding Planner", "W2", by_vendor=IDX) is None                    # EUR sheet: never shown as $
    assert sp.typical("Wedding Planner", "W3", target=60000, by_vendor=IDX)["amount"] == 7200
    assert sp.typical("Wedding Planner", "W3", target=None, by_vendor=IDX) is None


def test_catering_ignores_upcharges_and_buyout_minimums():
    idx = sp.index({"quotes": [
        {"Vendor_ID": "U1", "category": "catering", "currency": "USD", "created_at": 1},
        {"Vendor_ID": "R1", "category": "catering", "currency": "USD", "minimum_spend": 75000, "created_at": 1,
         "minimum_spend_conditions": "Food and beverage minimum for 1st floor/patio buyout based on 150 guests"}],
        "items": [item("U1", "catering", "entree_per_person", 5, "per_person"),
                  item("U1", "catering", "package_per_person", 9, "per_person"),
                  item("R1", "catering", "package_per_person", 60, "per_person")]})
    idx["U1"]["items"][1]["item_name"] = "Filet upgrade"
    assert sp.typical("Food and Beverage", "U1", guests=100, by_vendor=idx) is None      # $5 upcharge, upgrade line
    assert sp.typical("Food and Beverage", "R1", guests=100, by_vendor=idx)["amount"] == 6000   # buyout min ignored


def test_wrong_category_or_unknown_vendor_gives_nothing():
    assert sp.typical("Beauty", "C1", by_vendor=IDX) is None
    assert sp.typical("Beauty", "nope", by_vendor=IDX) is None
    assert sp.typical("Venue", "B1", by_vendor=IDX) is None


def test_flag_off_reads_nothing(monkeypatch):
    monkeypatch.setattr(config, "WW_SERVICE_PRICES", False)
    monkeypatch.setattr(sp, "_fetch", lambda: (_ for _ in ()).throw(AssertionError("no read with the flag off")))
    assert sp.vendors() == {}


def test_failed_read_keeps_last_copy_and_backs_off(monkeypatch):
    monkeypatch.setattr(config, "WW_SERVICE_PRICES", True)
    monkeypatch.setattr(config, "VENDOR_PRICES_SECRET", "s")
    monkeypatch.setattr(sp, "_CACHE", {"at": 0.0, "failed_at": 0.0, "by_vendor": {}})
    monkeypatch.setattr(sp, "_fetch", lambda: PAYLOAD)
    assert "B1" in sp.vendors(now=10_000_000)
    calls = []
    monkeypatch.setattr(sp, "_fetch", lambda: calls.append(1) or (_ for _ in ()).throw(OSError("down")))
    later = 10_000_000 + sp.TTL_S + 1
    assert "B1" in sp.vendors(now=later) and "B1" in sp.vendors(now=later + 5) and len(calls) == 1


# ---------------------------------------------------------------- in the proposal

@pytest.fixture
def on(w, monkeypatch):
    monkeypatch.setattr(config, "WW_SERVICE_PRICES", True)
    monkeypatch.setattr(config, "VENDOR_PRICES_SECRET", "s")
    monkeypatch.setattr(sp, "vendors", lambda now=None: IDX)
    w.listings["Wedding Planner"] = [{"vendor_id": "W1", "vendor_idx": 12, "name": "Plan Co", "state": "Texas",
                                      "category": "Wedding Planner"}]
    return w


def test_full_member_gets_worked_out_prices_labelled_estimate(on):
    rows = rows_of(ask(on, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]}))
    be, pl = rows["Beauty"], rows["Wedding Planner"]
    assert (be["name"], be["amount"], be["basis"], be["badge"], be["amount_from"]) == \
        ("Glow Studio", 1475, "estimate", ww.ESTIMATE, ww.FROM_VENDOR)
    assert "own price sheet" in be["based_on"] and "Not a quote" in be["based_on"]
    assert (pl["name"], pl["amount"], pl["why"]) == ("Plan Co", 2350, "month-of coordination from $2,350")
    # the venue's all-in already counts food and drink: catering stays inside it, never counted twice
    assert rows["Food and Beverage"]["basis"] == "included"


@pytest.mark.parametrize("pricing", ["light", "none"])
def test_free_couple_sees_locked_rows_never_the_figure(on, monkeypatch, pricing):
    monkeypatch.setattr(config, "FREE_PRICING", pricing)
    out = ask(on, FREE, "Plan my wedding", {"action": "start", "skip": ["area"]})
    rows = rows_of(out)
    for s in ("Beauty", "Wedding Planner"):
        assert rows[s]["locked"] is True and rows[s]["amount"] is None and rows[s]["badge"] == ww.PRICED_LOCKED
    blob = json.dumps(out)
    for n in ("1475", "2350", "1,475", "2,350", "455", "165"):
        assert n not in blob, n


def test_flag_off_rows_unchanged(w, monkeypatch):
    monkeypatch.setattr(config, "WW_SERVICE_PRICES", False)
    monkeypatch.setattr(sp, "vendors", lambda now=None: (_ for _ in ()).throw(AssertionError("not read when off")))
    rows = rows_of(ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]}))
    assert (rows["Beauty"]["amount"], rows["Beauty"]["amount_from"]) == (1200, ww.FROM_SPLIT)
    assert (rows["Wedding Planner"]["amount"], rows["Wedding Planner"]["name"]) == (6000, "")
