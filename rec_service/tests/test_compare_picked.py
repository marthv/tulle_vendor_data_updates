"""Side-by-side compare (compare.py, 2026-11-22). No network, no model."""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import pytest  # noqa: E402

import compare  # noqa: E402
import config  # noqa: E402
import estimate  # noqa: E402
import free_mode  # noqa: E402

PRICE_KEYS = {"cost", "off_peak", "amount", "total", "per_guest", "tax_pct", "pct", "lines", "venue_fee_range", "room"}


def inputs(**kw):
    return dict({"space": "Hall", "guest_floor": 0, "rental": 10000, "ceremony": None, "pp_fb": 200, "fb_min": None,
                 "fb_min_per_person": False, "service_pct": 20, "tax_pct": None, "estimates": {}}, **kw)


class FakeXano:
    def __init__(self, entitled=True):
        self.entitled, self.full_calls, self.peak = entitled, 0, 10000
        self.inp = {"V1": inputs(), "V2": inputs(rental=20000, pp_fb=None, estimates={"pp_fb": 150}, guest_floor=175)}
        self.cards = {"V1": {"vendor_id": "V1", "vendor_idx": 1, "name": "Alpha", "state": "New York", "category": "Venue",
                             "address": "1 A St, Brooklyn, NY 11201, USA", "max_capacity_seated": 80, "venue_fee_range": [10000, 10000]},
                      "V2": {"vendor_id": "V2", "name": "Beta", "state": "New York", "category": "Venue", "max_capacity_seated": 250},
                      "V3": {"vendor_id": "V3", "name": "Photo Co", "category": "Photography"}}

    def pi_entitled(self, user):
        return self.entitled

    def cached_vendor_card(self, vid):
        return self.cards.get(vid)

    def cached_venue_pricing(self, token, vid, paid, user=None):
        self.full_calls += bool(paid)
        lines = [{"line": "Rental fee", "vs_market": "Above average in New York", "amount": 10000, "detail": "$10,000", "source": "venue"},
                 {"line": "Food and drink", "vs_market": "20% below average", "source": "venue"},
                 {"line": "Ceremony on site", "vs_market": "Below average in New York", "source": "venue"},
                 {"line": "Service charge", "vs_market": "Typical in New York", "source": "market"}]
        return {"lines": lines, "guest_minimum": 175 if vid == "V2" else None, "cost_inputs": self.inp[vid] if paid else None}

    def cached_venue_details(self, vid, amounts):
        if vid != "V1":
            return {"spaces": []}
        return {"spaces": [{"space": "Hall", "Max_Capacity_Seated": 120, "Months__Lowest_Pricing": "January, February",
                            "Venue_Fee_on_Lowest_Saturday": 6000,
                            "Venue_Fee_on_a_Peak_Season_Saturday": self.peak}],
                "about": {"venue_type": "Loft, Hotel", "vibes": "Modern"}, "google": {"rating": 4.6, "review_count": 90}}

    def pdf_rows(self, token, vid):
        return [1]

    def pdf_summary(self, rows):
        return {"count": 2, "years": [2026]}


def build(x, access=True, guests=100, ids=("V1", "V2")):
    return compare.build(x, "t", {"id": 5}, list(ids), access=access, guests=guests, budget=60000)


def keys(o):
    if isinstance(o, dict):
        return set(o) | set().union(*[keys(v) for v in o.values()] or [set()])
    if isinstance(o, list):
        return set().union(*[keys(v) for v in o] or [set()])
    return set()


def test_full_totals_equal_the_venue_page_formula():
    x = FakeXano()
    w = build(x)
    a, b = w["venues"]
    assert a["cost"]["total"] == estimate.compute(x.inp["V1"], 100)["total_before_tax_and_gratuity"] == 36000
    assert b["cost"]["guests"] == 175 and b["cost"]["at_minimum"] and b["fits"]["minimum"] is False
    food = next(l for l in b["cost"]["lines"] if l["key"] == "food")
    assert food["source"] == "estimate" and "Food and drink" in b["cost"]["estimated"]
    assert a["seats"] == 120 and a["seats_is_room"] and a["room"] == "Hall"
    assert a["off_peak"]["total"] == 31200 and a["off_peak"]["months"] == "January, February"
    assert [v["name"] for v in w["venues"]] == ["Alpha", "Beta"]   # picked order, never a ranking


def test_totals_at_different_guest_counts_are_never_subtracted():
    assert not any("comes to about" in d for d in build(FakeXano())["differences"])
    x = FakeXano()
    x.inp["V2"]["rental"] = 26000
    assert "Beta comes to about $7,200 more than Alpha for 200 guests (some lines estimated)." in build(x, guests=200)["differences"]


@pytest.mark.parametrize("access,entitled", [(False, False), (False, True), (True, False)])
def test_no_price_without_both_plan_and_entitlement(access, entitled):
    x = FakeXano(entitled)
    w = build(x, access=access)
    assert w["level"] == "words" and x.full_calls == 0          # ep230 is never called
    assert not (keys(w) & PRICE_KEYS), keys(w) & PRICE_KEYS
    assert "$" not in json.dumps(w) and w["budget"] is None
    assert w["venues"][0]["words"][0] == {"key": "rental", "label": "Rental", "word": "above typical"}
    # food carries a figure, ceremony is per-person-unsafe, service is a market stand-in: all dropped
    assert [r["key"] for r in w["venues"][0]["words"]] == ["rental"]
    assert w["reason"] == ("member" if access else "plan")


def test_stored_full_widget_is_locked_for_a_lapsed_member():
    w = free_mode.widget(build(FakeXano()))
    assert w["level"] == "words" and not (keys(w) & PRICE_KEYS) and "$" not in json.dumps(w)


def test_planted_price_in_an_unknown_key_does_not_survive():
    w = build(FakeXano(), access=False)
    w["venues"][0]["secret"] = "$9,999"
    w["venues"][0]["words"].append({"key": "rental", "label": "Rental", "word": "$9,999"})
    w["differences"].append("It costs $9,999.")
    assert "9,999" not in json.dumps(compare.lock(w))


def test_unknown_widget_types_are_closed_to_free_couples():
    assert free_mode.widget({"type": "new_thing", "rows": [{"price": 1}]}) == {"type": "new_thing", "locked": True}
    for t in ("cost_estimate", "budget_split", "budget_status", "ww_context"):
        assert free_mode.widget({"type": t, "x": 1})["x"] == 1


def test_needs_two_real_venues():
    assert build(FakeXano(), ids=("V1", "V3")) is None      # a photographer is not a venue
    assert build(FakeXano(), ids=("V1", "V1")) is None
    assert build(FakeXano(), ids=("V1", "V99999")) is None
    assert compare.clean_ids(["V1", "V1", "x y", "V2", "V3", "V4"]) == ["V1", "V2", "V3"]


def test_daily_cap_counts_distinct_venues(monkeypatch):
    monkeypatch.setattr(config, "COMPARE_VENUES_PER_DAY", 3)
    compare._USED.clear()
    assert compare.spend(9, ["V1", "V2"]) and all(compare.spend(9, ["V1", "V2"]) for _ in range(20))
    assert compare.spend(9, ["V3"]) and not compare.spend(9, ["V4"])


def test_arm(monkeypatch):
    monkeypatch.setattr(config, "COMPARE_PICK", False)
    assert compare.arm(14998) == "off"
    monkeypatch.setattr(config, "COMPARE_PICK", True)
    monkeypatch.setattr(config, "COMPARE_USER_IDS", {14998})
    monkeypatch.setattr(config, "COMPARE_AB_PCT", 0)
    assert compare.arm(14998) == "compare" and compare.arm(7) == "off" and compare.arm(None) == "off"
    monkeypatch.setattr(config, "COMPARE_AB_PCT", 50)
    arms = [compare.arm(i) for i in range(1, 2001)]
    assert 900 < arms.count("compare") < 1100 and set(arms) == {"compare", "control"}
    assert compare.arm(123) == compare.arm(123)


def test_no_price_is_called_good_or_bad():
    text = json.dumps([build(FakeXano()), build(FakeXano(), guests=200), build(FakeXano(), access=False)]).lower()
    for w in ("best", "winner", "cheap", "bargain", "good value", "expensive", "affordable", "score"):
        assert w not in text


def test_off_peak_needs_both_sources_to_agree_on_the_peak():
    x = FakeXano()
    x.peak = 22000        # table 36 says another peak rental than the panel (Field Museum): not comparable
    assert build(x)["venues"][0]["off_peak"] is None


def test_per_person_ceremony(monkeypatch):
    panel = {"space": {"components": [{"key": "ceremony", "amount": 20, "fee_type": "per_person", "source": "venue"},
                                      {"key": "venue_fee", "amount": 8500, "source": "venue"},
                                      {"key": "food_and_drink", "source": "venue", "pp_fb": 390, "fb_min": 40000},
                                      {"key": "service_charge", "source": "venue", "pct": 7}], "all_in": {"estimates": {}}}}
    inp = estimate.inputs_from_panel(panel)
    monkeypatch.setattr(config, "CEREMONY_PER_PERSON", True)
    assert estimate.compute(inp, 100)["total_before_tax_and_gratuity"] == 54035   # ep230 at_default, read live 2026-11-22
    monkeypatch.setattr(config, "CEREMONY_PER_PERSON", False)
    assert estimate.compute(inp, 100)["total_before_tax_and_gratuity"] < 54035


def test_saved_takes_the_latest_venues_only():
    assert compare.saved_venue_ids(FakeXano(), {"favorited_vendors": ["V1", "V3", "V2"]}) == ["V2", "V1"]
    assert compare.saved_venue_ids(FakeXano(), {}) == []


def test_place():
    assert compare._place({"address": "1 A St, Brooklyn, NY 11201, USA", "state": "New York"}) == "Brooklyn, New York"
    assert compare._place({"address": "80 Wythe Ave, Brooklyn, NY 11249", "state": "New York"}) == "Brooklyn, New York"
    assert compare._place({"address": "Somewhere in Tuscany", "state": "International"}) == "International"


def test_minimum_only_food(monkeypatch):
    inp = inputs(pp_fb=None, fb_min=15000, estimates={"pp_fb": 150}, rental=3000)
    monkeypatch.setattr(config, "MIN_FOOD_ONLY", True)
    e = estimate.compute(inp, 100)
    assert e["total_before_tax_and_gratuity"] == round((3000 + 15000) * 1.2) and "Food and drink" not in e["estimated_lines"]
    row = next(l for l in compare._cost(e, inp)["lines"] if l["key"] == "food")
    assert row["source"] == "pdf" and row["minimum_only"]
    monkeypatch.setattr(config, "MIN_FOOD_ONLY", False)
    assert estimate.compute(inp, 200)["total_before_tax_and_gratuity"] == round((3000 + 30000) * 1.2)   # as before


def test_a_pdf_minimum_is_never_tagged_estimate():
    inp = inputs(pp_fb=None, fb_min=60000, estimates={"pp_fb": 150})
    c = compare._cost(estimate.compute(inp, 100), inp)
    assert next(l for l in c["lines"] if l["key"] == "food")["source"] == "pdf" and c["estimated"] == []


def test_stored_full_widget_for_a_member_without_entitlement(monkeypatch):
    import app
    from fastapi.testclient import TestClient
    w = build(FakeXano())
    user = {"id": 5, "total_amount_paid": 30}
    monkeypatch.setattr(app, "_auth", lambda a: ("t", user))
    monkeypatch.setattr(app.xano, "get_chat", lambda u, c: {"id": c, "title": "x"})
    monkeypatch.setattr(app.xano, "get_messages", lambda c: [{"role": "assistant", "text": "Here.", "cards": [], "chips": [], "widgets": [w]}])
    monkeypatch.setattr(app.xano, "has_paid_access", lambda u: True)
    monkeypatch.setattr(app.xano, "beta_member", lambda u: False)
    r = TestClient(app.app).get("/rec/chats/7", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200 and "venue_side_by_side" in r.text and "36000" not in r.text and "$" not in r.text
    monkeypatch.setattr(app.xano, "pi_entitled", lambda u: True)
    monkeypatch.setattr(app.xano, "beta_member", lambda u: True)
    assert "36000" in TestClient(app.app).get("/rec/chats/7", headers={"Authorization": "Bearer t"}).text


def test_infinite_guests():
    assert build(FakeXano(), guests=float("inf"))["guests"] == 100


def test_minimum_only_is_said_everywhere(monkeypatch):
    monkeypatch.setattr(config, "MIN_FOOD_ONLY", True)
    inp = inputs(pp_fb=None, fb_min=15000, estimates={"pp_fb": 150}, rental=3000)
    e = estimate.compute(inp, 100)
    assert "may cost more" in estimate.label(e, 40000) and "food_is_only_the_minimum" in estimate.summary(e)
    plain = estimate.compute(inputs(), 100)
    assert "may cost more" not in estimate.label(plain) and "food_is_only_the_minimum" not in estimate.summary(plain)
    x = FakeXano()
    x.inp = {"V1": inputs(), "V2": inp}
    d = build(x, guests=100)["differences"]
    assert "Alpha comes to about $14,400 more than Beta for 100 guests (food at Beta is only its minimum and may cost more)." in d


def test_room_is_named_in_the_seating_line():
    d = build(FakeXano(), guests=200)["differences"]
    assert "Alpha (Hall) lists seating for 120, below your 200." in d
