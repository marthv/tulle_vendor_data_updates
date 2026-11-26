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
    assert "detail_text" not in out and "99 Scott" not in json.dumps(out)   # today's paragraph is not sent
    assert out["photo_first"] is True


def test_apply_never_changes_what_is_stored():
    a = answer()
    before = json.dumps(a, sort_keys=True)
    pf.apply(a, "full", {})
    assert json.dumps(a, sort_keys=True) == before


def test_guests_fall_back_to_the_profile_and_date_is_understood():
    a = answer(cards=[venue("V%d" % i, estimate_guests=None) for i in range(3)], search_filters={"states": ["Texas"]})
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


@pytest.mark.parametrize("text", [
    "The Foundry has the lowest rental of the three, with food at a typical rate for New York venues.",
    "Wythe fits best.", "That one is expensive.", "A bargain.", "Good value for a Saturday."])
def test_no_card_line_never_judges_price(text):
    line = pf.apply(answer(cards=[], text=text), "full", {})["text"]
    assert not QUALITY.search(line) and line in (pf.NEUTRAL_TEXT, pf.NEUTRAL_WIDGET)


def test_a_long_no_card_answer_is_clipped_not_replaced():
    text = ("Most DJs in Texas play for four to five hours and bring their own sound system and lights for the dance floor. "
            "Ask about travel.")
    line = pf.apply(answer(cards=[], text=text), "full", {})["text"]
    assert line == "Most DJs in Texas play for four to five hours and bring their own sound…"
    assert len(line.split()) == pf.MAX_WORDS


def test_international_is_never_a_place():
    a = answer(cards=[venue(state="International", estimate_guests=110)] * 3, search_filters={"states": ["International"], "guests": 110})
    assert pf.apply(a, "full", {})["text"] == "3 for 110 guests"


def test_member_estimate_keeps_the_minimum_caveat():
    c = venue(rental_source="estimate", estimate_label="About $38,115 for 100 guests before tax and gratuity, with food at its "
                                                       "minimum spend (it may cost more)")
    t = pf.price_line(c, "full")["text"]
    assert t == "About $38,100 for 100 guests before tax and tip, an estimate; food at its minimum spend, may cost more"


def test_member_estimate_says_before_tax_and_tip():
    assert pf.price_line(venue(rental_source="estimate"), "full")["text"] == "About $38,100 for 100 guests before tax and tip, an estimate"


def test_one_of_a_kind_is_singular():
    c = [{"vendor_id": "P1", "name": "Snap", "category": "Photography", "state": "Texas"}]
    assert pf.apply(answer(cards=c, search_filters={"states": ["Texas"]}), "free", {})["text"] == "1 photographer in Texas"


@pytest.mark.parametrize("text,line", [
    ("We don't have Ben Conger Inn yet. Tell me the city.", "We don't have Ben Conger Inn yet."),
    ("Here's Brooklyn Botanic Garden in Brooklyn, New York. It's outside California.", None),   # no "where you're planning"
    ("Here's Brooklyn Botanic Garden in Brooklyn, New York. It's outside California, where you're planning - tell me.",
     "Outside California, where you're planning"),
])
def test_reopened_turns_stored_before_the_switch_keep_what_cards_cant_say(text, line):
    out = pf.apply({"text": text, "cards": [venue()], "search_filters": {}}, "full", {}, reopened=True)
    assert out["text"] == (line or "One in New York for 100 guests")


def test_show_more_line_uses_the_search_guests(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(app, "_chat_used", lambda *a: None)
    monkeypatch.setattr(xano, "get_chat", lambda u, c: {"id": c})
    hist = [{"role": "assistant", "text": "x", "cards": [], "filters": {"states": ["New York"], "city": "Brooklyn", "guests": 150},
             "more_cards": [venue("V%d" % i, estimate_guests=150) for i in range(5)]}]
    monkeypatch.setattr(xano, "get_messages", lambda c: hist)
    monkeypatch.setattr(app, "_gate", lambda *a, **k: (True, True, [], True, False, {}))
    monkeypatch.setattr(xano, "log_usage", lambda *a, **k: None)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "profile_of", lambda u: {"Wedding_Guest_Count": 90})
    monkeypatch.setattr(xano, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(xano, "touch_chat", lambda *a, **k: None)
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)
    h = {"Authorization": "Bearer t"}
    r = TestClient(app.app).post("/rec/more?photo_first=1", json={"chat_id": 3}, headers=h).json()
    assert r["text"] == "3 in Brooklyn for 150 guests" and "search_filters" not in r
    r0 = TestClient(app.app).post("/rec/more", json={"chat_id": 3}, headers=h).json()
    assert "photo_first" not in r0 and "search_filters" not in r0


def test_main_search_reads_the_live_ep119_item_shape(monkeypatch):
    """Live ep119 v24 unsets flt_glance and lifts venue_types / venue_attributes onto the item."""
    item = {"id": 4081, "Vendor_ID": "V4081", "Name": "Wythe Hotel", "State": "New York", "Address": "80 Wythe Ave, Brooklyn, NY 11249",
            "Venue_Type": "Hotel", "Max_Capacity_Seated": 150, "image_1": "https://i/w.jpg", "Description": "",
            "flt_min_venue_fee": 0, "flt_max_venue_fee": 0, "space_count": 4, "pricing_row_count": 20,
            "venue_types": ["Hotel / Resort"], "venue_offerings": ["All-Inclusive"],
            "venue_attributes": ["Rooftop / Skyline Views; Natural Light / Large Windows", "Rooftop / Skyline Views"],
            "min_fb_min_peak_sat": 43000}
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    v = xano._venue_result({"items": [item], "itemsTotal": 1})["venues"][0]
    assert v["setting_tags"] == ["Hotel / Resort", "Rooftop / Skyline Views", "Natural Light / Large Windows"]
    assert [f["label"] for f in pf.facts(v)] == ["Seats 150", "Rooftop", "Hotel"]
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    assert "setting_tags" not in xano._venue_result({"items": [item], "itemsTotal": 1})["venues"][0]


def test_free_facts_drop_digit_words_but_keep_seats():
    c = {"vendor_id": "P1", "name": "Snap", "category": "Photography", "venue_type": "Documentary", "packages_label": "3 packages"}
    assert [f["label"] for f in pf.facts(c, free=True)] == ["Documentary"]
    assert pf.facts(venue(), free=True)[0]["label"] == "Seats 220"


def test_other_vendors_never_get_a_price_line():
    c = {"vendor_id": "P1", "name": "Abby Leigh", "category": "Photography", "price_range": [800, 9500], "estimate_total": 9000}
    for lvl in ("full", "light"):
        assert pf.price_line(c, lvl) is None


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
    pf.REQ.set(True)
    assert app._pf(answer(), {"id": 1})["cards"][0]["price_line"]["source"] == "pdf"
    free = app._pf(answer(has_access=False, paid=False), {"id": 1})
    assert all("price_line" not in c for c in free["cards"])
    pf.REQ.set(False)


def test_a_page_that_did_not_ask_gets_todays_answer_with_the_switch_on(monkeypatch):
    """The vendor-page bubble (no photoFirst prop, so no ?photo_first=1) with REC_PHOTO_FIRST=1: today's full answer."""
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(xano, "profile_of", lambda u: {})
    today = answer(cards=[{k: v for k, v in venue(vid).items() if k != "rental_source"} for vid in ("V1", "V2")])
    with_keys = answer(cards=[dict(c, setting_tags=["Waterfront"], rental_source="pdf") for c in today["cards"]])
    pf.REQ.set(False)
    got = app._pf(with_keys, {"id": 1})
    assert json.dumps(got, sort_keys=True) == json.dumps(today, sort_keys=True)
    assert got["text"].startswith("99 Scott") and "photo_first" not in got


def test_the_request_flag_comes_from_the_query(monkeypatch):
    if not any(getattr(r, "path", "") == "/_pf_probe" for r in app.app.routes):
        app.app.get("/_pf_probe")(lambda: {"pf": pf.REQ.get()})
    c = TestClient(app.app)
    assert c.get("/_pf_probe").json() == {"pf": False}
    assert c.get("/_pf_probe?surface=bubble&photo_first=1").json() == {"pf": True}
    assert c.get("/_pf_probe?photo_first=0").json() == {"pf": False}
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    pf.REQ.set(True)
    assert not pf.on()
    pf.REQ.set(False)


def test_whole_wedding_answers_are_never_shaped(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    pf.REQ.set(True)
    a = answer(widgets=[{"type": "wedding_proposal", "total": 50862}], text="About $50,862 in all.")
    assert app._pf(a, {"id": 1})["text"] == "About $50,862 in all."
    pf.REQ.set(False)


def test_reopened_chat_keeps_the_lookup(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    out = answer(cards=[venue()], lookup={"status": "elsewhere", "query": "bbg"}, venue_request=None)
    stored = app._stored_cards(out)
    turn = xano.parse_message_row({"id": 1, "role": "assistant", "content": "x", "cards": stored})
    assert turn["lookup"] == {"status": "elsewhere", "query": "bbg"} and len(turn["cards"]) == 1
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    assert not any(c.get("meta") == "lookup" for c in app._stored_cards(out))


def test_glance_tags_are_whole_venue_settings_never_offerings():
    g = {"venue_types": ["Hotel / Resort"], "venue_offerings": ["All-Inclusive"],
         "venue_attributes": ["Rooftop / Skyline Views; Waterfront", "Natural Light / Large Windows"], "min_guests": 150}
    tags = xano.glance_tags(g)
    assert tags == ["Hotel / Resort", "Rooftop / Skyline Views", "Waterfront", "Natural Light / Large Windows"]
    assert [f["label"] for f in pf.facts(venue(venue_type="", setting_tags=tags))] == ["Seats 220", "Waterfront", "Rooftop"]


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


def test_photographers_compare_without_a_price(monkeypatch):
    monkeypatch.setattr(config, "COMPARE_ANY_CATEGORY", True)
    w = compare.build(AnyXano(), "t", {"id": 1}, ["P1", "P2"], access=True)
    assert w["category"] == "Photography" and [v["name"] for v in w["venues"]] == ["Snap", "Lens"]
    assert "price_from" not in json.dumps(w) and "$" not in json.dumps(w) and "3200" not in json.dumps(w)
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


# ---------------------------------------------------------------- QA round 3

def test_outside_is_found_anywhere_and_bare_outside_is_not():
    t = ("Here's Lodge on the Desert in Tucson, Arizona. It's outside Pennsylvania, where you're planning - tell me if "
         "you meant a different one.")
    assert pf.reopen_line(t) == "Outside Pennsylvania, where you're planning"
    assert pf.reopen_line("Outside catering is not allowed here. The rental includes tables.") is None


@pytest.mark.parametrize("text,name", [
    ("Lasting Impressions Event Decor isn\u2019t in Tulle\u2019s listings, but here are florists in New Hampshire.", "Lasting Impressions Event Decor"),
    ("We don't have Ben Conger Inn yet. Here are venues near Long Beach.", "Ben Conger Inn"),
    ("We don't have Saturday availability in the PDF.", ""),
    ("We don't have pricing for that room.", ""),
])
def test_a_named_vendor_we_dont_list(text, name):
    assert pf.not_listed(text) == name


def test_not_listed_leads_the_line_live_and_reopened():
    cards = [{"vendor_id": "F%d" % i, "name": "F", "category": "Flowers", "state": "New Hampshire"} for i in range(3)]
    t = "Lasting Impressions Event Decor isn't in Tulle's listings. Here are florists in New Hampshire."
    for reopened in (False, True):
        out = pf.apply({"text": t, "cards": cards, "search_filters": {"states": ["New Hampshire"]}}, "free", {}, reopened=reopened)
        assert out["text"] == "We don't have Lasting Impressions Event Decor. 3 florists in New Hampshire"


def test_clip_never_cuts_inside_a_price_or_parenthesis():
    assert pf.clip("The Foundry comes to about $38,913 (Tulle's state benchmark puts it near the middle) for 100 guests.") == ""
    line = pf.apply(answer(cards=[], text="The Foundry comes to about $38,913 (Tulle's state benchmark puts it near the "
                                         "middle) for 100 guests."), "full", {})["text"]
    assert line == pf.NEUTRAL_TEXT
    assert pf.clip("Most venues in the area ask for a deposit, about $5,000 from the PDF, when you sign the contract.") \
        == "Most venues in the area ask for a deposit\u2026"


def test_free_fallback_with_nothing_shown_is_neutral():
    res = free_mode.sanitize_result(answer(cards=[], text=free_mode.FALLBACK_TEXT))
    assert pf.apply(res, "free", {})["text"] == pf.NEUTRAL_TEXT


def test_headline_guests_match_the_cards_price_line():
    cards = [venue("V%d" % i, estimate_guests=250) for i in range(3)]
    out = pf.apply(answer(cards=cards, search_filters={"states": ["Pennsylvania"], "city": "Philadelphia", "guests": 200}), "full", {})
    assert out["text"] == "3 in Philadelphia for 250 guests"
    est = pf.apply(answer(cards=[venue("V1", estimate_guests=250, rental_source="estimate")] * 3,
                          search_filters={"city": "Philadelphia", "guests": 200}), "full", {})
    assert est["text"] == "3 in Philadelphia for 250 guests" and "for 250 guests" in est["cards"][0]["price_line"]["text"]


def test_a_show_more_batch_stores_its_search_with_the_switch_on(monkeypatch):
    saved = []
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(app, "_chat_used", lambda *a: None)
    monkeypatch.setattr(xano, "get_chat", lambda u, c: {"id": c})
    f = {"states": ["Pennsylvania"], "city": "Philadelphia", "guests": 250}
    monkeypatch.setattr(xano, "get_messages", lambda c: [{"role": "assistant", "text": "x", "cards": [], "filters": f,
                                                          "more_cards": [venue("V%d" % i) for i in range(5)]}])
    monkeypatch.setattr(app, "_gate", lambda *a, **k: (True, True, [], True, False, {}))
    for n in ("log_usage", "mp_track", "touch_chat"):
        monkeypatch.setattr(xano, n, lambda *a, **k: None)
    monkeypatch.setattr(xano, "add_message", lambda cid, role, text, cards=None, chips=None: saved.append(cards))
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)
    TestClient(app.app).post("/rec/more", json={"chat_id": 3}, headers={"Authorization": "Bearer t"})
    assert {"meta": "search", "filters": f} in saved[-1]
    turn = xano.parse_message_row({"id": 1, "role": "assistant", "content": "x", "cards": saved[-1]})
    assert turn["filters"] == f and len(turn["cards"]) == 3
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    TestClient(app.app).post("/rec/more", json={"chat_id": 3}, headers={"Authorization": "Bearer t"})
    assert not any(c.get("meta") for c in saved[-1])
