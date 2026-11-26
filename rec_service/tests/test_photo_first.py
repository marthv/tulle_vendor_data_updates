"""Photo-first Assistant (REC_PHOTO_FIRST) and compare in any category (REC_COMPARE_ANY_CATEGORY), 2026-11-26.
No network, no model."""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import agent  # noqa: E402
import app  # noqa: E402
import compare  # noqa: E402
import config  # noqa: E402
import free_mode  # noqa: E402
import photo_first as pf  # noqa: E402
import xano  # noqa: E402

PRICE = re.compile(r"\$|\d+\s?%|\d[\d,]*\s?[kK]\b")
QUALITY = re.compile(r"\b(best|lowest|cheapest|bargain|expensive|good value|great value|top)\b", re.I)


def venue(vid="V1", **kw):
    return dict({"vendor_id": vid, "vendor_idx": 1, "name": "Brooklyn Botanic Garden", "state": "New York",
                 "address": "990 Washington Ave, Brooklyn, NY 11225", "venue_type": "Garden / Botanical Garden",
                 "max_capacity_seated": 220, "image": "https://x/1.jpg", "venue_fee_range": [7000, 7000],
                 "rental_source": "pdf", "estimate_total": 38115, "estimate_guests": 100,
                 "estimate_label": "About $38,115 for 100 guests"}, **kw)


def answer(cards=None, **kw):
    return dict({"text": "99 Scott is the best Brooklyn fit: about $36,900 for 100 guests. Prospect Park follows. "
                         "Open the PDF.", "cards": cards if cards is not None else [venue("V1"), venue("V2"), venue("V3")],
                 "chips": [], "search_filters": {"states": ["New York"], "city": "Brooklyn", "guests": 100},
                 "paid": True, "has_access": True}, **kw)


# ---------------------------------------------------------------- the short line

def test_results_line_is_short_and_names_place_and_guests():
    out = pf.apply(answer(), "full", {})
    assert out["text"] == "3 in Brooklyn for 100 guests"
    assert len(out["text"].split()) <= pf.MAX_WORDS
    assert out["detail_text"].startswith("99 Scott")          # the full answer is kept, not lost
    assert out["photo_first"] is True


def test_apply_never_changes_what_is_stored():
    a = answer()
    before = json.dumps(a, sort_keys=True)
    pf.apply(a, "full", {})
    assert json.dumps(a, sort_keys=True) == before


def test_guests_fall_back_to_the_profile_and_date_is_understood():
    a = answer(search_filters={"states": ["Texas"]})
    out = pf.apply(a, "full", {"Wedding_Guest_Count": 120, "Wedding_Date": "2027-09-18"})
    assert out["text"] == "3 in Texas for 120 guests"
    assert out["understood"] == {"place": "Texas", "guests": 120, "date": "Sep 2027"}


def test_other_categories_name_the_kind():
    cards = [{"vendor_id": "P1", "name": "Snap", "category": "Photography", "state": "Texas", "venue_type": "Documentary"}] * 2
    out = pf.apply(answer(cards=cards, search_filters={"states": ["Texas"]}), "free", {})
    assert out["text"] == "2 photographers in Texas"


def test_no_cards_keeps_only_the_first_sentence():
    out = pf.apply(answer(cards=[], text="A DJ usually plays four hours. Most add a ceremony set. Ask about travel."), "full", {})
    assert out["text"] == "A DJ usually plays four hours."


def test_outside_their_state_is_the_one_line():
    a = answer(cards=[venue()], lookup={"status": "elsewhere", "query": "brooklyn botanic garden"},
               text="Here's Brooklyn Botanic Garden in Brooklyn, New York. It's outside California, where you're planning - "
                    "tell me if you meant a different one. Open its pricing PDF.")
    assert pf.apply(a, "full", {})["text"] == "Outside California, where you're planning"


def test_not_in_tulle_says_no_first_then_one_question():
    a = answer(cards=[venue("V7")], lookup={"status": "not_found", "query": "Ben Conger Inn"},
               venue_request={"name": "Ben Conger Inn", "location": ""}, text="We don't have Ben Conger Inn yet. Here are 1.")
    out = pf.apply(a, "free", {})
    assert out["text"] == "We don't have Ben Conger Inn."
    assert out["not_found"] == {"name": "Ben Conger Inn", "question": "Which town is it near?",
                                "chip": "Check their quote or pricing PDF"}


def test_found_has_no_not_found_block():
    a = answer(cards=[venue()], lookup={"status": "found", "query": "x"}, venue_request=None)
    assert "not_found" not in pf.apply(a, "full", {})


# ---------------------------------------------------------------- card facts

def test_facts_are_seats_and_whole_venue_settings():
    f = pf.facts(venue(setting_tags=["Natural Light / Large Windows", "Greenhouse"]))
    assert f[0] == {"icon": "seat", "label": "Seats 220"}
    assert [x["label"] for x in f] == ["Seats 220", "Garden", "Greenhouse"]


@pytest.mark.parametrize("vt", ["All-Inclusive", "All Inclusive Package", "In-house catering"])
def test_a_pricing_style_is_never_a_setting(vt):
    labels = [x["label"].lower() for x in pf.facts(venue(venue_type=vt, setting_tags=[]))]
    assert not any("inclusive" in l or "catering" in l for l in labels)


def test_no_guest_minimum_fact():
    f = pf.facts(venue(guest_minimum=175, min_guests=175))
    assert not any("175" in x["label"] or "minimum" in x["label"].lower() for x in f)


# ---------------------------------------------------------------- price line by level

def test_member_price_line_names_the_pdf():
    assert pf.price_line(venue(), "full") == {"text": "Rental $7,000, from its PDF", "source": "pdf"}


def test_member_estimated_rental_is_not_called_pdf():
    pl = pf.price_line(venue(rental_source="estimate"), "full")
    assert pl["source"] == "estimate" and "PDF" not in pl["text"] and "estimate" in pl["text"]


def test_light_band_is_marked_estimate():
    c = agent._light_card(dict(venue(), price_band={"label": "about $35K-40K all-in for 100 guests, before tax and tip"}))
    assert pf.price_line(c, "light")["text"].endswith(", an estimate")


def test_free_answer_has_no_price_digit_anywhere():
    res = free_mode.sanitize_result(answer())
    out = pf.apply(res, "free", {"Wedding_Guest_Count": 100})
    blob = json.dumps({k: out[k] for k in ("text", "cards", "understood")})
    assert not PRICE.search(blob), PRICE.search(blob)
    assert all("price_line" not in c for c in out["cards"])


def test_free_line_never_carries_price_talk(monkeypatch):
    monkeypatch.setattr(pf, "short_line", lambda o, p=None: "Under budget at $5,000")
    out = pf.apply(free_mode.sanitize_result(answer()), "free", {})
    assert not PRICE.search(out["text"])


def test_added_words_never_judge_price():
    for lvl in ("full", "light", "free"):
        out = pf.apply(answer(), lvl, {})
        added = [out["text"]] + [f["label"] for c in out["cards"] for f in c["facts"]] + \
                [c.get("price_line", {}).get("text", "") for c in out["cards"]]
        assert not any(QUALITY.search(t) for t in added)


# ---------------------------------------------------------------- rental source + vibe tags only with the flag

def test_rental_source_only_with_flag(monkeypatch):
    p = {"lines": [{"line": "Rental fee", "amount": 5000, "source": "venue"}]}
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    assert "rental_source" not in agent._with_rental({"name": "x"}, p)
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    assert agent._with_rental({"name": "x"}, p)["rental_source"] == "pdf"
    p["lines"][0]["source"] = "market"
    assert agent._with_rental({"name": "x"}, p)["rental_source"] == "estimate"


def test_card_from_row_setting_tags_only_with_flag(monkeypatch):
    row = {"Vendor_ID": "V1", "id": 1, "Name": "A", "Category": "Venue", "flt_venue_attributes": ["Waterfront"]}
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    assert "setting_tags" not in xano.card_from_row(row)
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    assert xano.card_from_row(row)["setting_tags"] == ["Waterfront"]
    assert free_mode.card(xano.card_from_row(row))["setting_tags"] == ["Waterfront"]


# ---------------------------------------------------------------- app wiring

def test_pf_off_returns_the_same_object(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    a = answer()
    assert app._pf(a, {"id": 1}) is a


def test_pf_on_level_from_tier(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(xano, "profile_of", lambda u: {})
    assert app._pf(answer(), {"id": 1})["cards"][0]["price_line"]["source"] == "pdf"
    free = app._pf(answer(has_access=False, paid=False), {"id": 1})
    assert all("price_line" not in c for c in free["cards"])


# ---------------------------------------------------------------- What it costs

class CostXano:
    def __init__(self, entitled=True):
        self.entitled = entitled

    def pi_entitled(self, user):
        return self.entitled

    def cached_vendor_card(self, vid):
        return {"V1": venue("V1", category="Venue"), "P1": {"vendor_id": "P1", "name": "Snap", "category": "Photography"}}.get(vid)

    def cached_venue_pricing(self, token, vid, paid, user=None):
        lines = [{"line": "Rental fee", "amount": 7000, "source": "venue"},
                 {"line": "Food and drink minimum", "source": "venue"},
                 {"line": "Ceremony on site", "source": "market"},
                 {"line": "Service charge", "source": "venue"}]
        inp = {"space": "Lily Pool Terrace", "guest_floor": 0, "rental": 7000, "ceremony": None, "pp_fb": 200,
               "fb_min": None, "fb_min_per_person": False, "service_pct": 22, "tax_pct": None, "estimates": {"ceremony": 1500}}
        return {"lines": lines, "cost_inputs": inp if paid else None}


def test_cost_lines_member_names_room_guests_and_sources():
    w = pf.build_cost_lines(CostXano(), "t", {"id": 1}, "V1", access=True, guests=100)
    assert w["level"] == "full" and w["room"] == "Lily Pool Terrace" and w["guests"] == 100
    by = {l["key"]: l for l in w["lines"]}
    assert by["rental"]["note"] == "From the PDF" and by["rental"]["amount"] == 7000
    assert by["food"]["note"] == "From the PDF"
    assert all(l["note"] in ("From the PDF", "Estimate", "Not in this PDF") or l["note"].startswith("Included")
               for l in w["lines"])
    assert "100 guests" in w["total_note"] and "Lily Pool Terrace" in w["total_note"]


@pytest.mark.parametrize("access,entitled", [(False, False), (True, False), (False, True)])
def test_cost_lines_without_figures_have_no_digits(access, entitled):
    w = pf.build_cost_lines(CostXano(entitled), "t", {"id": 1}, "V1", access=access, guests=100)
    assert w["level"] == "words"
    rows = {l["key"]: l["where"] for l in w["lines"]}
    assert rows == {"rental": "In the PDF", "food": "In the PDF", "ceremony": "Not in this PDF", "service": "In the PDF"}
    assert not PRICE.search(json.dumps(w["lines"])) and "amount" not in json.dumps(w)


def test_cost_lines_stored_full_widget_is_locked_for_free():
    w = pf.build_cost_lines(CostXano(), "t", {"id": 1}, "V1", access=True, guests=100)
    w["lines"][0]["secret"] = "$9,999"
    locked = free_mode.widget(w)
    assert locked["level"] == "words" and "$" not in json.dumps(locked) and "7000" not in json.dumps(locked)


def test_cost_lines_only_for_a_venue():
    assert pf.build_cost_lines(CostXano(), "t", {"id": 1}, "P1", access=True) is None


def test_cost_tool_is_unknown_with_the_flag_off(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    r = TestClient(app.app).post("/rec/tool", json={"tool": "cost_lines", "args": {"vendor_id": "V1"}},
                                 headers={"Authorization": "Bearer t"})
    assert r.status_code == 400


def test_cost_tool_respects_the_kill_switch(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    r = TestClient(app.app).post("/rec/tool", json={"tool": "cost_lines", "args": {"vendor_id": "V1"}},
                                 headers={"Authorization": "Bearer t"})
    assert r.status_code == 503


# ---------------------------------------------------------------- compare in any category

class AnyXano:
    def __init__(self):
        self.cards = {"P1": {"vendor_id": "P1", "name": "Snap", "category": "Photography", "venue_type": "Documentary",
                             "price_range": [3200, 6000], "state": "Texas"},
                      "P2": {"vendor_id": "P2", "name": "Lens", "category": "Photography", "venue_type": "Fine art",
                             "price_range": [2500, 5000], "state": "Texas"},
                      "F1": {"vendor_id": "F1", "name": "Bloom", "category": "Flowers", "venue_type": "Florist"},
                      "F2": {"vendor_id": "F2", "name": "Petal", "category": "Flowers", "venue_type": "Florist"},
                      "V1": venue("V1", category="Venue")}

    def pi_entitled(self, user):
        return True

    def cached_vendor_card(self, vid):
        return self.cards.get(vid)

    def pdf_rows(self, token, vid):
        return []

    def pdf_summary(self, rows):
        return {"count": 1, "years": [2026]}


def test_any_category_off_needs_venues(monkeypatch):
    monkeypatch.setattr(config, "COMPARE_ANY_CATEGORY", False)
    assert compare.build(AnyXano(), "t", {"id": 1}, ["P1", "P2"], access=True) is None


def test_photographers_compare_with_sourced_price_for_members(monkeypatch):
    monkeypatch.setattr(config, "COMPARE_ANY_CATEGORY", True)
    w = compare.build(AnyXano(), "t", {"id": 1}, ["P1", "P2"], access=True)
    assert w["category"] == "Photography" and [v["name"] for v in w["venues"]] == ["Snap", "Lens"]
    assert w["venues"][0]["price_from"]["label"] == "Packages from $3,200, from its PDF"
    assert w["venues"][0]["style"] == ["Documentary"] and w["venues"][0]["seats"] is None
    assert "guests" not in compare.summary_text(w)


def test_any_category_words_level_has_no_price(monkeypatch):
    monkeypatch.setattr(config, "COMPARE_ANY_CATEGORY", True)
    w = compare.build(AnyXano(), "t", {"id": 1}, ["P1", "P2"], access=False)
    assert w["level"] == "words" and "$" not in json.dumps(w) and "price_from" not in json.dumps(w)
    assert w["category"] == "Photography"


def test_never_mixed_categories(monkeypatch):
    monkeypatch.setattr(config, "COMPARE_ANY_CATEGORY", True)
    w = compare.build(AnyXano(), "t", {"id": 1}, ["F1", "P1", "F2"], access=True)
    assert {v["category"] for v in w["venues"]} == {"Flowers"} and len(w["venues"]) == 2
    assert compare.build(AnyXano(), "t", {"id": 1}, ["F1", "P1"], access=True) is None


def test_saved_ids_one_category(monkeypatch):
    monkeypatch.setattr(config, "COMPARE_ANY_CATEGORY", True)
    user = {"favorited_vendors": ["P1", "F1", "P2", "V1"]}        # newest last
    assert compare.saved_ids(AnyXano(), user) == ["P2", "P1"]     # V1 is alone in its category
    assert compare.saved_ids(AnyXano(), user, "Flowers") == ["F1"]
    assert compare.saved_by_category(AnyXano(), user) == {"Venue": 1, "Photography": 2, "Flowers": 1}


@pytest.fixture
def picks(monkeypatch):
    rows = []

    def items(uid, include_removed=False, picks=False):
        return [r for r in rows if r["user_id"] == uid and r["status"] == "active" and (r["source"] == "pick") == picks]

    def add(row):
        rows.append(dict(row, id=len(rows) + 1))
        return rows[-1]

    def remove(uid, iid):
        for r in rows:
            if r["id"] == iid:
                r["status"] = "removed"
                return True
        return False
    x = AnyXano()
    monkeypatch.setattr(xano, "plan_items", items)
    monkeypatch.setattr(xano, "plan_add", add)
    monkeypatch.setattr(xano, "plan_remove", remove)
    monkeypatch.setattr(xano, "listed_vendor", lambda v: x.cards.get(v))
    monkeypatch.setattr(xano, "listed_venue", lambda v: x.cards.get(v) if (x.cards.get(v) or {}).get("category") == "Venue" else None)
    return rows


def test_one_pick_per_category(picks, monkeypatch):
    monkeypatch.setattr(config, "COMPARE_ANY_CATEGORY", True)
    assert compare.set_pick(xano, 7, "P1")[0]
    assert compare.set_pick(xano, 7, "V1")[0]
    assert compare.set_pick(xano, 7, "P2")[0]
    got = compare.picks_of(xano, 7)
    assert got["Photography"]["vendor_id"] == "P2" and got["Venue"]["vendor_id"] == "V1"
    ok, now = compare.set_pick(xano, 7, "", category="Photography", expect="P2")
    assert ok and now is None and compare.picks_of(xano, 7) == {"Venue": {"vendor_id": "V1", "name": "Brooklyn Botanic Garden"}}


def test_pick_off_is_venues_only(picks, monkeypatch):
    monkeypatch.setattr(config, "COMPARE_ANY_CATEGORY", False)
    with pytest.raises(LookupError):
        compare.set_pick(xano, 7, "P1")


def test_get_pick_by_category(picks, monkeypatch):
    monkeypatch.setattr(config, "COMPARE_ANY_CATEGORY", True)
    monkeypatch.setattr(config, "COMPARE_PICK", True)
    monkeypatch.setattr(config, "COMPARE_DECIDE", True)
    monkeypatch.setattr(config, "COMPARE_USER_IDS", {7})
    monkeypatch.setattr(config, "COMPARE_AB_PCT", 0)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    c = TestClient(app.app)
    h = {"Authorization": "Bearer t"}
    assert c.post("/rec/compare/pick", json={"vendor_id": "P1"}, headers=h).json()["pick"]["vendor_id"] == "P1"
    assert c.get("/rec/compare/pick?category=Photography", headers=h).json()["pick"]["vendor_id"] == "P1"
    assert c.get("/rec/compare/pick", headers=h).json()["pick"] is None      # the venue pick is its own
    r = c.post("/rec/compare/pick", json={"vendor_id": "", "expect": "P1", "category": "Photography"}, headers=h)
    assert r.json()["ok"] and r.json()["pick"] is None
