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
                 "rental_source": "pdf", "rental_when": "peak_saturday", "estimate_total": 38115, "estimate_guests": 100,
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
    a = answer(cards=[venue("V%d" % i, estimate_guests=None, state="Texas") for i in range(3)], search_filters={"states": ["Texas"]})
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
    ("We don't have Ben Conger Inn yet. Tell me the city.", "We don't have Ben Conger Inn."),
    ("We don't have 2027 pricing yet for this one.", None),                                     # not a name: the normal line
    ("Here's Brooklyn Botanic Garden in Brooklyn, New York. It's outside California.", "The one you asked about"),
    ("Here's Brooklyn Botanic Garden in Brooklyn, New York. It's outside California, where you're planning - tell me.",
     "Outside California, where you're planning"),
])
def test_reopened_turns_stored_before_the_switch_keep_what_cards_cant_say(text, line):
    out = pf.apply({"text": text, "cards": [venue()], "search_filters": {}}, "full", {}, reopened=True)
    assert out["text"] == (line or "One in New York")      # no search count stored, no profile count: no guests


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
    assert pf.price_line(venue(), "full") == {"text": "Rental $7,000 on a peak-season Saturday, from its PDF", "source": "pdf"}


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
    monkeypatch.setattr(pf, "short_line", lambda o, p=None, **k: "Under budget at $5,000")
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
    today = answer(cards=[{k: v for k, v in venue(vid).items() if k not in ("rental_source", "rental_when")} for vid in ("V1", "V2")])
    with_keys = answer(cards=[dict(c, setting_tags=["Waterfront"], rental_source="pdf", rental_when="peak_saturday")
                              for c in today["cards"]])
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
    assert turn["pf_meta"]["lookup"] == {"status": "elsewhere", "query": "bbg"} and len(turn["cards"]) == 1
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    assert not any(c.get("meta") for c in app._stored_cards(out) if c.get("meta") not in ("search",))


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
    want = "We don't have Lasting Impressions Event Decor. 3 florists in New Hampshire"
    base = {"text": t, "cards": cards, "search_filters": {"states": ["New Hampshire"]}}
    # live: from the structural mark only - the wording alone never decides it
    assert pf.apply(dict(base, unmatched_name="Lasting Impressions Event Decor"), "free", {})["text"] == want
    assert pf.apply(base, "free", {})["text"] == "3 florists in New Hampshire"
    # stored before the switch (no marks): the wording fallback
    assert pf.apply(base, "free", {}, reopened=True)["text"] == want
    # stored with the switch on and no mark: nothing guessed
    assert pf.apply(dict(base, pf_stored=True), "free", {}, reopened=True)["text"] == "3 florists in New Hampshire"


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


def test_headline_guests_are_the_searchs_and_the_price_line_names_its_own():
    # QA round 5 replaced round 3's "headline = the cards' priced count": a free card carries no priced count, so only the
    # search's count can make the member and free lines agree. The member's price line says which count ITS figure is for.
    cards = [venue("V%d" % i, estimate_guests=250, rental_source="estimate", state="Pennsylvania",
                   address="1 Main St, Philadelphia, PA 19107") for i in range(3)]
    f = {"states": ["Pennsylvania"], "city": "Philadelphia", "guests": 200}
    out = pf.apply(answer(cards=cards, search_filters=f), "full", {})
    assert out["text"] == "3 in Philadelphia for 200 guests" and out["understood"]["guests"] == 200
    assert "for 250 guests" in out["cards"][0]["price_line"]["text"]


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


# ---------------------------------------------------------------- QA round 4
# Names and towns below are made up for these tests (none is from the QA sample).

@pytest.mark.parametrize("name,said,want", [
    ("Willow Creek Lodge", "is willow creek lodge on here?", "Willow Creek Lodge"),
    ("Willow Creek Lodge Asbury Park", "willow creek lodge", "Willow Creek Lodge"),     # the model added the town
    ("Marigold & Fern Floral", "do you have marigold and fern floral", "Marigold & Fern Floral"),
    ("Willow Creek Lodge", "something rustic please", ""),                               # not typed by the couple
    ("Rustic Barn", "a rustic barn please", ""),                                         # a style, not a name
    ("Outdoor Garden Venue", "outdoor garden venue", ""),
    ("Finger Lakes Valley", "finger lakes valley", ""),                                  # a region
    ("Upstate New York", "upstate new york", ""),                                        # a place (never "Upstate New")
    ("Harrow", "harrow", ""),                                                            # one word: too thin for a search mark
])
def test_couple_typed_is_only_a_name_the_couple_typed(name, said, want):
    assert pf.couple_typed(name, said) == want


@pytest.mark.parametrize("text", [
    "I can't find a Willow Creek Lodge in our New Jersey data, so I can't price it.",
    "I couldn't find Willow Creek Lodge among the New Jersey venues Tulle holds pricing for.",
    "We don't list Willow Creek Lodge, so I can't open its card here.",
    "We don't have Willow Creek Lodge yet. Here are lodges nearby.",
    "Willow Creek Lodge isn't one of the venues we list. These are close by.",
    "Tulle doesn't have Willow Creek Lodge. Here are three nearby.",
])
def test_any_wording_of_we_dont_have_a_name_the_couple_typed(text):
    assert pf.typed_not_listed(text, "how much is willow creek lodge for 120", []) == "Willow Creek Lodge"
    assert pf.typed_not_listed(text, "lodges in new jersey", []) == ""          # the couple never named it
    assert pf.typed_not_listed(text, "how much is willow creek lodge", ["Willow Creek Lodge & Barn"]) == ""   # shown


def test_a_card_that_may_be_the_named_vendor_is_never_we_dont_have():
    assert pf.holds_name("Harbor's Edge Pavilion", ["Harbor Edge Pavilion"])          # a typo away
    assert pf.holds_name("Willow Creek Lodge", ["The Lodge at Willow Creek (New Jersey)"])  # words reordered, "(State)"
    assert not pf.holds_name("Willow Creek Lodge", ["Cedar Hollow Farm", "Seabright Hall"])


def test_we_dont_have_leads_however_the_model_worded_it_live(monkeypatch):
    """Live: agent.unmatched_name sets the mark from the answer's wording when the search ran without the name (a type /
    style search) - the round-4 miss - and from the search itself when it ran with it."""
    cards = [venue("V%d" % i, name=n, state="New Jersey", address="1 Main St, Red Bank, NJ 07701", estimate_guests=120)
             for i, n in enumerate(("Cedar Hollow Farm", "Seabright Hall", "Navesink House"))]
    text = "I can't find a Willow Creek Lodge in our New Jersey data. Similar lodges we hold: Cedar Hollow Farm is close."
    um = agent.unmatched_name({}, text, "does willow creek lodge fit our budget", cards)
    assert um == "Willow Creek Lodge"
    out = pf.apply(answer(cards=cards, text=text, unmatched_name=um,
                          search_filters={"states": ["New Jersey"], "guests": 120}), "full", {})
    assert out["text"] == "We don't have Willow Creek Lodge. 3 in New Jersey for 120 guests"
    # the search's own mark wins (the model added the town to the keyword)
    t = {}
    agent._note_unmatched(t, "Willow Creek Lodge Red Bank", "willow creek lodge", cards, None)
    assert agent.unmatched_name(t, "Here are three nearby.", "willow creek lodge", cards) == "Willow Creek Lodge"
    # ...never when a result is the venue, or a close name was found elsewhere
    t = {}
    agent._note_unmatched(t, "Seabright Hall", "seabright hall", cards, None)
    agent._note_unmatched(t, "Willow Creek Lodge", "willow creek lodge", [], {"status": "close", "names": ["Willow Creek Lodges (Ohio)"]})
    assert t == {}
    # a style or a place the model searched as a keyword is never a name
    t = {}
    agent._note_unmatched(t, "rustic barn", "a rustic barn near the shore", [], None)
    agent._note_unmatched(t, "Jersey Shore", "something on the jersey shore", [], None)
    assert t == {}


def _fake_model(script):
    import types

    class Msg:
        content, model = [], "fake"
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)

    class Runner:
        def __init__(self, tools, **kw):
            self.tools = {(getattr(t, "name", None) or t.__name__): t for t in tools}

        def __iter__(self):
            for name, args in script:
                self.tools[name](**args)
            yield Msg()

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    return Client


@pytest.mark.parametrize("on", [True, False])
def test_run_marks_a_named_florist_we_dont_list_only_with_the_switch_on(monkeypatch, on):
    items = [{"id": 500 + i, "Vendor_ID": "V%d" % (500 + i), "Name": n, "State": "New Jersey", "Category": "Flowers",
              "Address": "1 Main St, Red Bank, NJ", "image_1": {"url": "https://x/%d.jpg" % i}, "flt_glance": {}}
             for i, n in enumerate(("Stems Studio", "Petal & Moss"), 1)]
    monkeypatch.setattr(config, "PHOTO_FIRST", on)
    monkeypatch.setattr(config, "LISTINGS_IN_CHAT", True)
    monkeypatch.setattr(xano, "_get", lambda url, token=None, params=None: (
        {"itemsTotal": 2, "nextPage": None, "items": json.loads(json.dumps(items))}
        if url.endswith("/wptp_updated_mappings_search") else {}))
    monkeypatch.setattr(xano, "market_benchmarks", lambda s: {"state": s, "metrics": {}})
    script = [("search_vendors", {"category": "florist", "states": ["New Jersey"], "keyword": "Marigold & Fern Floral"}),
              ("present_recommendations", {"message": "Here are florists in New Jersey.", "vendor_ids": ["V501", "V502"],
                                           "reasons": ["a", "b"], "chips": []})]
    monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_model(script), raising=False)
    res, _ = agent.run("tok", {"id": 0}, True, [{"role": "user", "content": "do you have marigold & fern floral?"}],
                       profile_override={"Wedding_Location_Updated": ["New Jersey"]}, detail="full")
    assert [c["vendor_id"] for c in res["cards"]] == ["V501", "V502"]
    if on:
        assert res["unmatched_name"] == "Marigold & Fern Floral"
        line = pf.apply(dict(res, search_filters={"states": ["New Jersey"]}), "full", {})["text"]
        assert line.startswith("We don't have Marigold & Fern Floral.")
    else:
        assert "unmatched_name" not in res


def test_reopened_turn_keeps_we_dont_have_and_an_old_turn_needs_the_couples_words(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    cards = [venue("V%d" % i, name="Hall %d" % i, state="New Jersey", address="1 Main St, Red Bank, NJ") for i in range(3)]
    out = answer(cards=cards, text="Three halls nearby.", unmatched_name="Willow Creek Lodge",
                 search_filters={"states": ["New Jersey"], "guests": 100})
    stored = app._stored_cards(out)
    turn = xano.parse_message_row({"id": 1, "role": "assistant", "content": out["text"], "cards": stored})
    assert turn["pf_meta"]["unmatched_name"] == "Willow Creek Lodge"
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(xano, "get_chat", lambda u, c: {"id": c, "title": "t"})
    monkeypatch.setattr(xano, "has_paid_access", lambda u: True)
    monkeypatch.setattr(xano, "pi_entitled", lambda u: True)
    monkeypatch.setattr(xano, "beta_member", lambda u: True)
    monkeypatch.setattr(xano, "profile_of", lambda u: {})
    old = {"id": 3, "role": "assistant", "text": "We don't have Willow Creek Lodge yet. Here are three halls.",
           "cards": [dict(c) for c in cards], "filters": {"states": ["New Jersey"], "guests": 100}}
    asked = lambda q: [{"id": 2, "role": "user", "text": q, "cards": []}, json.loads(json.dumps(old))]
    rows = {"new": [{"id": 2, "role": "user", "text": "halls near red bank", "cards": []}, turn]}
    for case, turns in (("new", rows["new"]), ("old_typed", asked("what about willow creek lodge")),
                        ("old_not_typed", asked("halls near red bank"))):
        monkeypatch.setattr(xano, "get_messages", lambda c, t=turns: json.loads(json.dumps(t)))
        r = TestClient(app.app).get("/rec/chats/5?photo_first=1", headers={"Authorization": "Bearer t"}).json()
        line = [t for t in r["turns"] if t["role"] == "assistant"][0]["text"]
        if case == "old_not_typed":
            assert not line.startswith("We don't have"), line
        else:
            assert line.startswith("We don't have Willow Creek Lodge."), (case, line)


# ---- the place line names the place the search used

def test_radius_search_says_near_the_town_searched():
    cards = [venue("V1", address="10 Front St, Red Bank, NJ 07701", state="New Jersey", distance_miles=8.2),
             venue("V2", address="2 Ocean Ave, Long Branch, NJ 07740", state="New Jersey", distance_miles=5.0),
             venue("V3", address="5 Broad St, Manhattan, NY 10004", state="New York", distance_miles=29.0)]
    f = {"city": "Asbury Park", "states": ["New Jersey", "New York"], "radius_miles": 30, "guests": 150}
    out = pf.apply(answer(cards=cards, search_filters=f), "full", {})
    assert out["text"] == "3 near Asbury Park for 150 guests"        # never "in Asbury Park", never a card's state
    assert out["understood"]["place"] == "Asbury Park"
    inside = [dict(c, address="1 Cookman Ave, Asbury Park, NJ 07712") for c in cards]
    assert pf.apply(answer(cards=inside, search_filters=f), "full", {})["text"] == "3 in Asbury Park for 150 guests"


def test_a_state_is_named_only_when_every_card_is_in_it():
    cards = [venue("V1", state="Ohio", address="1 A St, Dayton, OH"), venue("V2", state="Ohio", address="2 B St, Lima, OH"),
             venue("V3", state="Kentucky", address="3 C St, Covington, KY")]
    out = pf.apply(answer(cards=cards, search_filters={"states": ["Ohio"], "guests": 100}), "full", {})
    assert out["text"] == "3 for 100 guests" and "place" not in out["understood"]
    two = [{"vendor_id": "E%d" % i, "name": "E%d" % i, "category": "Entertainment", "state": s}
           for i, s in enumerate(("Ohio", "Indiana"))]
    assert pf.apply({"text": "x", "cards": two, "search_filters": {"states": ["Ohio"]}}, "free", {})["text"] == "2 bands and DJs"


def test_city_is_matched_as_a_whole_word_and_kept_with_no_cards():
    cards = [venue("V%d" % i, state="Ohio", address="9 Palima Rd, Dayton, OH") for i in range(2)]
    out = pf.apply(answer(cards=cards, search_filters={"city": "Lima", "states": ["Ohio"], "guests": 100}), "full", {})
    assert "Lima" not in out["text"]                                      # "Palima Rd" is not Lima
    none = pf.apply(answer(cards=[], text="Nothing matched those caps in Lima yet, so try widening the rental cap.",
                           search_filters={"city": "Lima", "states": ["Ohio"]}), "full", {})
    assert none["understood"]["place"] == "Lima"

def test_switch_off_a_row_stored_with_marks_reads_as_today(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    cards = [venue("V1")]
    out = answer(cards=cards, unmatched_name="Willow Creek Lodge")
    marked = {"id": 3, "role": "assistant", "content": "Three halls.", "cards": app._stored_cards(out)}
    plain = {"id": 3, "role": "assistant", "content": "Three halls.",
             "cards": [c for c in marked["cards"] if c.get("meta") != "pf"]}
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(xano, "get_chat", lambda u, c: {"id": c, "title": "t"})
    monkeypatch.setattr(xano, "has_paid_access", lambda u: True)
    monkeypatch.setattr(xano, "pi_entitled", lambda u: True)
    got = []
    for row in (marked, plain):
        monkeypatch.setattr(xano, "get_messages", lambda c, r=row: [xano.parse_message_row(json.loads(json.dumps(r)))])
        got.append(TestClient(app.app).get("/rec/chats/5?photo_first=1", headers={"Authorization": "Bearer t"}).json())
    assert json.dumps(got[0], sort_keys=True) == json.dumps(got[1], sort_keys=True)
    assert "pf_meta" not in json.dumps(got[0]) and "Willow" not in json.dumps(got[0])


# ---------------------------------------------------------------- QA round 5

def _pe(vid, cat="Entertainment", **kw):
    return dict({"vendor_id": vid, "name": "DJ " + vid, "category": cat, "state": "New York",
                 "address": "1 Broadway, Manhattan, NY 10004", "venue_type": "DJ"}, **kw)


@pytest.mark.parametrize("level", ["full", "free"])
def test_r5_mixed_kinds_never_take_the_first_cards_noun(level):
    # msg 5388: one venue + two DJs read "3 near Manhattan for 400 guests"; a photographer first + two venues read
    # "3 photographers ...".
    f = {"city": "Manhattan", "states": ["New York"], "radius_miles": 15, "guests": 400}
    v = venue("V4347", address="170 Central Prk W, New York, NY 10024", state="New York", distance_miles=2.0)
    a = answer(cards=[v, _pe("V32", distance_miles=1.0), _pe("V1925", distance_miles=1.0)], search_filters=f)
    line = pf.apply(a, level, {})["text"]
    assert line == "3 vendors near Manhattan" and "guests" not in line
    b = answer(cards=[_pe("P1", "Photography", address="1 Main St, Philadelphia, PA", state="Pennsylvania"),
                      venue("V1", address="2 Main St, Philadelphia, PA", state="Pennsylvania"),
                      venue("V2", address="3 Main St, Philadelphia, PA", state="Pennsylvania")],
               search_filters={"city": "Philadelphia", "states": ["Pennsylvania"], "guests": 100})
    line = pf.apply(b, level, {})["text"]
    assert line == "3 vendors in Philadelphia" and "photographer" not in line
    # one kind keeps its noun (and venues their guest count)
    assert pf.apply(answer(cards=[_pe("A"), _pe("B")], search_filters=f), level, {})["text"] == "2 bands and DJs in Manhattan"
    assert pf.apply(answer(cards=[v, dict(v, vendor_id="V9")], search_filters=f), level, {})["text"] == \
        "2 near Manhattan for 400 guests"


def test_r5_member_and_free_lines_agree_on_the_guest_count():
    # msg 5337: search 200 guests, estimates stored for 100. Member line said "for 100 guests", free "for 200 guests".
    f = {"city": "Atlanta", "guests": 200, "states": ["Georgia"], "radius_miles": 50}
    full = [venue("V14604", name="The Westin Atlanta Airport", state="Georgia", rental_source="estimate",
                  venue_fee_range=[None, None], address="4736 Best Rd, College Park, GA 30337", estimate_total=28750,
                  estimate_guests=100, distance_miles=8.0),
            venue("V9331", name="Atlanta Airport Marriott Gateway", state="Georgia", rental_source="estimate",
                  venue_fee_range=[None, None], address="2020 Convention Center Concourse, Atlanta, GA 30337",
                  estimate_total=28980, estimate_guests=100, distance_miles=9.0)]
    free = [free_mode.card(c) for c in full]
    m = pf.apply(answer(cards=full, search_filters=f), "full", {"Wedding_Guest_Count": 100})
    fr = pf.apply(answer(cards=free, search_filters=f, price_locked=True, paid=False, has_access=False), "free",
                  {"Wedding_Guest_Count": 100})
    assert m["text"] == fr["text"] == "2 near Atlanta for 200 guests"
    assert m["understood"]["guests"] == fr["understood"]["guests"] == 200
    # the member's price line is honest about the count ITS figure is for
    assert all("for 100 guests" in c["price_line"]["text"] and "estimate" in c["price_line"]["text"] for c in m["cards"])
    assert not any("price_line" in c for c in fr["cards"]) and not PRICE.search(json.dumps(fr["cards"]))


@pytest.mark.parametrize("said", ["ryland inn", "RYLAND INN", "what about the ryland inn?", "The Ryland Inn pricing"])
def test_r5_we_dont_have_matches_a_name_with_or_without_the(said):
    # msg 6705: "ryland inn" typed, the answer says "I couldn't find The Ryland Inn among the New Jersey venues..."
    text = ("I couldn't find The Ryland Inn among the New Jersey venues with pricing PDFs on Tulle, so I have no figures "
            "for it. Tulle holds 425 New Jersey venues with real pricing.")
    assert pf.typed_not_listed(text, said) == "The Ryland Inn"
    out = pf.apply({"text": text, "cards": [], "last_user": said, "search_filters": {"states": ["New Jersey"]}},
                   "full", {}, reopened=True)
    assert out["text"] == "We don't have The Ryland Inn."
    # live: the search's own mark (agent._note_unmatched) reads the same name
    timing = {}
    agent._note_unmatched(timing, "The Ryland Inn", said, [], None)
    assert timing.get("unmatched_names") == ["The Ryland Inn"]


@pytest.mark.parametrize("text", [
    "I couldn't find The Ryland Inn among the New Jersey venues with pricing PDFs on Tulle, so I have no figures for it.",
    "We can't find the ryland inn among the venues in New Jersey that have pricing PDFs on Tulle today, sorry about that.",
    "Tulle doesn't list The Ryland Inn among New Jersey venues with pricing PDFs yet, so there are no figures for it.",
    "Unfortunately we do not have Ryland Inn anywhere in the New Jersey listings that we hold pricing documents for.",
])
@pytest.mark.parametrize("reopened", [False, True])
def test_r5_a_cut_off_not_found_sentence_never_becomes_the_line(text, reopened):
    # The couple did not type the name (or we can't tell): no "We don't have", and never the clipped sentence either.
    out = pf.apply({"text": text, "cards": [], "last_user": "venues in new jersey", "search_filters": {"states": ["New Jersey"]}},
                   "full", {}, reopened=reopened)
    assert out["text"] == pf.NEUTRAL_TEXT and "…" not in out["text"]


def test_r5_an_ordinary_no_card_sentence_is_still_the_line():
    out = pf.apply({"text": "Most New Jersey barns open their calendars about eighteen months ahead, so ask early.",
                    "cards": [], "search_filters": {}}, "full", {})
    assert out["text"].startswith("Most New Jersey barns open their calendars")


def test_r5_rental_line_says_which_end_of_the_range_it_is():
    # V82 Aldie Mansion: the panel's $6,800 is the peak-season Saturday; the PDF says "Fri-Sun $3,900 to $6,800".
    p = {"headline": "A peak-season Saturday dinner in the Aldie Mansion, before tax and gratuity",
         "lines": [{"line": "Rental fee", "amount": 6800, "source": "venue"}]}
    import config as cfg
    old = cfg.PHOTO_FIRST
    try:
        cfg.PHOTO_FIRST = True
        card = agent._with_rental({"vendor_id": "V82", "name": "Aldie Mansion", "category": "Venue"}, p)
        cfg.PHOTO_FIRST = False
        assert "rental_when" not in agent._with_rental({"name": "x"}, p)            # switch off: no new key
    finally:
        cfg.PHOTO_FIRST = old
    assert card["rental_when"] == "peak_saturday" and card["venue_fee_range"] == [6800, 6800]
    pl = pf.price_line(card, "full")
    assert pl == {"text": "Rental $6,800 on a peak-season Saturday, from its PDF", "source": "pdf"}
    # never the bare figure as if it were THE price: a PDF rental we can't place on a date gets no rental line
    bare = dict(card)
    bare.pop("rental_when")
    assert not (pf.price_line(bare, "full") or {}).get("text", "").startswith("Rental")
    assert pf.price_line(dict(bare, estimate_total=30000, estimate_guests=100), "full")["source"] == "estimate"
    assert "rental_when" in pf.PF_ONLY_KEYS and "rental_when" not in json.dumps(pf.strip_cards([card]))


# ---- owner ruling 2026-11-26: no "typical cost" widget for free couples in photo-first answers

TYPICAL = {"v": 1, "note": None, "type": "cost_estimate", "state": "Delaware", "budget": 40000, "guests": 130,
           "tiers": [{"label": "Minimalist", "amount": 7843, "multiplier": 0.3}, {"label": "Average", "amount": 26145,
                     "multiplier": 1}, {"label": "Luxury", "amount": 44446, "multiplier": 1.7}],
           "gap_pct": 53, "missing": [], "verdict": "over", "major_city": False, "peak_season": False, "guest_lookup": 130}
# price-like: a $ sign, a percent, a K figure, or a widget/card amount field with a number in it
PRICE_LIKE = re.compile(r"\$|\d+\s?%|\d[\d,]*\s?[kK]\b|\"(?:amount|budget|price\w*|total\w*|tiers|gap_pct|fee\w*|"
                        r"estimate_total|food_minimum|venue_fee_range)\": ?[\[\d]")


def _price_like(obj):
    return PRICE_LIKE.findall(json.dumps(obj))


def _free_answer():
    card = free_mode.card(venue("V1597", name="Juniper by Tonic", state="Delaware", address="111 W 11th St, Wilmington, DE 19801"))
    return {"text": "Typical Delaware venues for 130 guests run about $27,100.", "cards": [card], "chips": [],
            "widgets": [dict(TYPICAL)], "search_filters": {"guests": 130, "states": ["Delaware"]},
            "paid": False, "has_access": False, "price_locked": True}


def _free_response(monkeypatch, pf_on=True):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(xano, "profile_of", lambda u: {"Wedding_Guest_Count": 130})
    tok = pf.REQ.set(pf_on)
    try:
        return app._public(app._pf(_free_answer(), {"id": 1}), chat_id=9)
    finally:
        pf.REQ.reset(tok)


def test_owner_free_photo_first_response_has_no_price_like_string(monkeypatch):
    got = _free_response(monkeypatch)
    assert got["photo_first"] is True and not any(w.get("type") == "cost_estimate" for w in got["widgets"])
    assert _price_like(got) == [], _price_like(got)
    assert got["text"] != pf.NEUTRAL_WIDGET          # the line does not point at a widget that is not there


def test_owner_canary_the_check_fails_if_the_typical_cost_widget_comes_back(monkeypatch):
    monkeypatch.setattr(pf, "FREE_HIDDEN_WIDGETS", ())            # the widget comes back
    got = _free_response(monkeypatch)
    assert any(w.get("type") == "cost_estimate" for w in got["widgets"])
    assert _price_like(got), "the price-like check missed the typical cost widget: the test above could never fail"


def test_owner_members_and_switch_off_keep_the_typical_cost_widget(monkeypatch):
    # switch on, page did not ask (or switch off): today's response, widget kept
    assert any(w.get("type") == "cost_estimate" for w in _free_response(monkeypatch, pf_on=False)["widgets"])
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    assert app._pf(_free_answer(), {"id": 1})["widgets"] == [TYPICAL]
    # a member in photo-first keeps it
    m = dict(_free_answer(), cards=[venue("V1597")], paid=True, has_access=True, price_locked=False)
    assert pf.apply(m, "full", {})["widgets"] == [TYPICAL]


def test_owner_reopened_free_chat_hides_the_typical_cost_widget(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(xano, "get_chat", lambda u, c: {"id": c, "title": "t"})
    monkeypatch.setattr(xano, "has_paid_access", lambda u: False)
    monkeypatch.setattr(xano, "beta_member", lambda u: False)
    monkeypatch.setattr(xano, "profile_of", lambda u: {})
    row = {"id": 3, "role": "assistant", "content": "Here are venues in Delaware for 130 guests.",
           "cards": [{"meta": "widget", "widget": dict(TYPICAL)}, {"meta": "search", "filters": {"guests": 130, "states": ["Delaware"]}}]}
    monkeypatch.setattr(xano, "get_messages", lambda c: [xano.parse_message_row(json.loads(json.dumps(row)))])
    on = TestClient(app.app).get("/rec/chats/5?photo_first=1", headers={"Authorization": "Bearer t"}).json()
    assert not any(w.get("type") == "cost_estimate" for t in on["turns"] for w in t.get("widgets") or [])
    off = TestClient(app.app).get("/rec/chats/5", headers={"Authorization": "Bearer t"}).json()   # page did not ask
    assert any(w.get("type") == "cost_estimate" for t in off["turns"] for w in t.get("widgets") or [])
