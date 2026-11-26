"""Photo-first QA round 6 (2026-11-26): T0-53 no figure for a free couple on any photo-first quick action or price answer,
T0-54 a conditional rental rate shows its condition or no line, one guest count on screen, the any-category mock. No network."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import build_conditional_rates as bcr  # noqa: E402
import config  # noqa: E402
import cost_tools  # noqa: E402
import free_mode  # noqa: E402
import photo_first as pf  # noqa: E402
import upsell  # noqa: E402
import xano  # noqa: E402
from test_photo_first import TYPICAL, _price_like, answer, venue  # noqa: E402

H = {"Authorization": "Bearer t"}


# ---------------------------------------------------------------- T0-53: /rec/tool on a photo-first page

@pytest.fixture
def tool_env(monkeypatch):
    """The real /rec/tool code; Xano reads replaced by fixed values (QA's t052 set-up: Pennsylvania, 130 guests, $30,000)."""
    stored = []
    user = {"id": 99999, "Wedding_Location_Updated": ["Pennsylvania"], "Wedding_Guest_Count": 130, "Wedding_Budget": 30000}
    state = {"access": False}
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", user))
    monkeypatch.setattr(app, "_chat_used", lambda *a: None)
    monkeypatch.setattr(app, "_submit_usage", lambda *a, **k: None)
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)
    for n in ("has_paid_access", "beta_member", "pi_entitled"):
        monkeypatch.setattr(xano, n, lambda u: state["access"])
    monkeypatch.setattr(xano, "has_forever", lambda u: False)
    monkeypatch.setattr(xano, "state_average_rows", lambda t: [{"State": "Pennsylvania", "Cost_of_Average_Wedding": 34000}])
    monkeypatch.setattr(xano, "guest_modifier", lambda t, n: 1.0)
    monkeypatch.setattr(xano, "get_chat", lambda u, c: {"id": c, "title": "t"})
    monkeypatch.setattr(xano, "add_message", lambda cid, role, content, cards=None, chips=None: stored.append(
        {"role": role, "content": content, "cards": cards or [], "chips": chips or []}))
    for n in ("mp_track", "touch_chat", "log_usage"):
        monkeypatch.setattr(xano, n, lambda *a, **k: None)

    def run(tool, access=False, pfq=True, chat=5):
        state["access"] = access
        r = TestClient(app.app).post("/rec/tool" + ("?photo_first=1" if pfq else ""),
                                     json={"tool": tool, "args": {}, "chat_id": chat}, headers=H)
        return r.status_code, r.json()
    run.stored = stored
    return run


@pytest.mark.parametrize("tool", cost_tools.TOOLS)
def test_free_couple_photo_first_cost_tool_response_has_no_price_like_string(tool_env, tool):
    code, j = tool_env(tool)
    assert code == 200
    assert _price_like(j) == [], _price_like(j)                      # the WHOLE response JSON
    assert j["text"] == pf.UPGRADE_LINE and j["widgets"] == [] and j["price_locked"] is True
    assert j["upsell_prompt"] == upsell.prompt(has_plan=False)       # the product's existing upgrade card and wording
    assert j["upsell_prompt"]["cta"] == "See plans"
    # what is stored is the same words: a reopened chat cannot bring the figures back
    assert tool_env.stored[-1]["content"] == pf.UPGRADE_LINE and tool_env.stored[-1]["cards"] == []


def test_canary_the_check_fails_on_todays_free_estimate_cost(tool_env):
    # Without the fix the tile returned the cost_estimate widget ("$37,400 ... Minimalist $11,220, Luxury $63,580 ... 20%
    # under"): today's response (a page that did not ask for photo-first) must fail the same check.
    code, j = tool_env("estimate_cost", pfq=False)
    assert code == 200 and any(w.get("type") == "cost_estimate" for w in j["widgets"])
    assert _price_like(j), "the price-like check missed today's typical-cost answer: the test above could never fail"


def test_members_and_switch_off_keep_the_cost_tools(tool_env, monkeypatch):
    code, j = tool_env("estimate_cost", access=True)
    assert code == 200 and [w.get("type") for w in j["widgets"]] == ["cost_estimate"] and "upsell_prompt" not in j
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    code, j = tool_env("estimate_cost")                                  # switch off: today's free answer, byte for byte
    assert [w.get("type") for w in j["widgets"]] == ["cost_estimate"] and "upsell_prompt" not in j


def test_reopened_upgrade_turn_and_old_estimate_turn_read_as_the_upgrade(tool_env, monkeypatch):
    tool_env("estimate_cost", access=True)                    # a member's stored estimate (the figures are in the store)
    tool_env("estimate_cost")                                 # the free upgrade turn
    rows = [{"id": 900 + i, "chat_id": 5, "role": m["role"], "content": m["content"], "cards": m["cards"],
             "created_at": 1 + i, "chips": []} for i, m in enumerate(tool_env.stored)]
    turns = [xano.parse_message_row(r) for r in rows]
    monkeypatch.setattr(xano, "get_messages", lambda c: json.loads(json.dumps(turns)))
    monkeypatch.setattr(xano, "profile_of", lambda u: {})
    for n in ("has_paid_access", "beta_member", "pi_entitled"):
        monkeypatch.setattr(xano, n, lambda u: False)
    got = TestClient(app.app).get("/rec/chats/5?photo_first=1", headers=H).json()
    a = [t for t in got["turns"] if t["role"] == "assistant"]
    assert [t["text"] for t in a] == [pf.UPGRADE_LINE, pf.UPGRADE_LINE]
    assert all(t.get("upsell_prompt", {}).get("cta") == "See plans" for t in a)
    assert _price_like(a) == [], _price_like(a)


# ---------------------------------------------------------------- T0-53: a typed price question (no cards)

@pytest.mark.parametrize("text,card", [
    (upsell.apply_text("A typical wedding in Pennsylvania comes to about $37,400."), True),  # the price-question rule
    (free_mode.FALLBACK_TEXT, False),               # free_mode removed every price sentence (card already shown in chat)
    ("Typical Pennsylvania weddings cost more in the city.", False)])
def test_free_typed_typical_cost_question_gets_the_upgrade_line(text, card):
    out = dict(answer(cards=[], text=free_mode.sanitize_text(text)[0], paid=False, has_access=False, price_locked=True),
               upsell_prompt=upsell.prompt() if card else None)
    got = pf.apply(out, "free", {"Wedding_Guest_Count": 130})
    assert got["text"] == pf.UPGRADE_LINE and got["upsell_prompt"]["cta"] == "See plans"
    assert _price_like({"text": got["text"], "widgets": got.get("widgets")}) == []


def test_a_typed_price_question_for_a_member_or_with_cards_is_unchanged():
    m = pf.apply(answer(cards=[], text="A typical wedding in Pennsylvania comes to about $37,400.",
                        widgets=[dict(TYPICAL)]), "full", {})
    assert m["text"] != pf.UPGRADE_LINE and m["widgets"] == [TYPICAL]
    c = free_mode.card(venue("V1"))
    f = pf.apply(answer(cards=[c], text="Typical venues cost more here.", paid=False, has_access=False, price_locked=True),
                 "free", {})
    assert f["text"] != pf.UPGRADE_LINE and not f.get("upsell_prompt")


def test_free_budget_split_widget_is_held_back_too():
    w = {"type": "budget_split", "budget": 30000, "rows": [{"label": "Venue", "amount": 12000}]}
    got = pf.apply(answer(cards=[], text="Here is a split of your $30,000.", widgets=[w], paid=False, has_access=False,
                          price_locked=True), "free", {})
    assert got["widgets"] == [] and got["text"] == pf.UPGRADE_LINE and _price_like(got) == []


# ---------------------------------------------------------------- T0-54: conditional rental rates

def test_classify_reads_the_stored_condition():
    c = bcr.classify
    assert c("Bi-County Rate for Saturday all day (9AM-1AM) booking", 7480) == {"kind": "conditional",
                                                                              "condition": "county residents"}
    assert c("In-County rate $35/day; Out-of-County rate $75/day", 35) == {"kind": "both", "condition": "county residents",
                                                                          "other": 75}
    assert c("Easttown Township Resident rate for 5 hours; Non-residents $1700", 1450)["other"] == 1700
    for notes, fee in (("Non-resident Saturday rate with 40% discount applied", 2820),       # the everyone-else rate
                       ("Prime Time Saturday 8-hour block rate, Non-Resident price. Resident rate is $1,835.", 2250),
                       ("All day flat fee. A.V. Resident Rate is $6000.", 6500),            # residents' figure is another
                       ("Mon-Thu rate $80/hr (resident $55/hr)", 80),
                       ("5% discount if paid in full; 10% military discount", 3850),         # a discount, not this rate
                       ("Same pricing for both non-profit and regular rates.", 300),
                       ("Package includes exclusive access to Outdoor Members Lounges", 3000),
                       ("Peak season Saturday, 8 hours", 9000), ("", 9000)):
        assert c(notes, fee) is None, notes
    assert c("Trustees of Reservations Membership required: $140", 7500)["kind"] == "conditional"
    assert c("Commercial Rate; includes architectural lighting", 2800)["kind"] == "not_private"
    # a note that also prices businesses never pairs two figures (V8994: "Commercial Non-Resident: $550/hr")
    assert c("Private Resident rate: $93/hr. Commercial Non-Resident: $550/hr.", 93)["kind"] == "conditional"


def test_the_data_file_holds_newton_white_mansion():
    assert pf.conditional_rate("V2118", 7480) == {"kind": "conditional", "condition": "county residents"}
    assert pf.conditional_rate("V2118", 9724) is None and pf.conditional_rate("V0", 7480) is None
    assert pf.conditional_rate("V2118", None) is None
    d = json.load(open(pathlib.Path(pf.__file__).with_name("conditional_rates.json"), encoding="utf-8"))
    assert all(set(e) <= {"kind", "condition", "other"} and e["kind"] in ("both", "conditional", "not_private", "unclear")
               for v in d.values() for e in v.values())


def _member(vid, fee):
    return venue(vid, venue_fee_range=[fee, fee], estimate_total=29317, estimate_guests=100)


def test_member_line_for_a_conditional_rate(monkeypatch):
    monkeypatch.setattr(pf, "_COND", {"V2118": {"7480": {"kind": "conditional", "condition": "county residents"}},
                                      "V9": {"7480": {"kind": "both", "condition": "county residents", "other": 9724}},
                                      "V8": {"500": {"kind": "unclear"}}})
    assert pf.price_line(_member("V2118", 7480), "full") is None        # only the residents' rate stored: no line at all
    assert pf.price_line(_member("V9", 7480), "full")["text"] == \
        "Rental $7,480 on a peak-season Saturday for county residents ($9,724 otherwise), from its PDF"
    assert pf.price_line(_member("V8", 500), "full") is None
    assert pf.price_line(_member("V8", 6000), "full")["text"] == "Rental $6,000 on a peak-season Saturday, from its PDF"
    assert pf.price_line(_member("V1", 7000), "full")["text"] == "Rental $7,000 on a peak-season Saturday, from its PDF"


def test_canary_without_the_data_newton_white_mansion_shows_the_residents_rate_as_everyones(monkeypatch):
    monkeypatch.setattr(pf, "_COND", {})
    assert pf.price_line(_member("V2118", 7480), "full")["text"] == "Rental $7,480 on a peak-season Saturday, from its PDF"


class _CostXano:
    def __init__(self, rental):
        self.rental = rental

    def cached_vendor_card(self, vid):
        return {"vendor_id": vid, "vendor_idx": 1, "name": "Newton White Mansion", "category": "Venue", "state": "Maryland"}

    def pi_entitled(self, user):
        return True

    def cached_venue_pricing(self, token, vid, full, user):
        inp = {"space": "Newton White Mansion", "rental": self.rental, "rental_source": "venue", "pp_fb": 160,
               "pp_fb_source": "market", "service_pct": 22, "service_source": "market"}
        return {"lines": [{"line": "Rental fee", "source": "venue", "amount": self.rental}], "cost_inputs": inp}


def test_cost_lines_conditional_rate(monkeypatch):
    import estimate
    monkeypatch.setattr(estimate, "compute", lambda inp, g: {
        "space": "Newton White Mansion", "guests": g, "total_before_tax_and_gratuity": 29317, "per_guest": 293,
        "lines": [{"item": "Rental", "amount": 7480, "source": "venue"}]})
    import compare
    monkeypatch.setattr(compare, "_cost", lambda e, inp: {"total": 29317, "lines": [
        {"key": "rental", "amount": 7480, "source": "pdf"}]})
    monkeypatch.setattr(pf, "_COND", {"V2118": {"7480": {"kind": "conditional", "condition": "county residents"}}})
    w = pf.build_cost_lines(_CostXano(7480), "t", {}, "V2118", access=True)
    assert w["level"] == "words" and "total" not in w and not any("amount" in r for r in w["lines"])
    monkeypatch.setattr(pf, "_COND", {"V2118": {"7480": {"kind": "both", "condition": "county residents", "other": 9724}}})
    w = pf.build_cost_lines(_CostXano(7480), "t", {}, "V2118", access=True)
    rent = [r for r in w["lines"] if r["key"] == "rental"][0]
    assert w["level"] == "full" and rent["note"] == "For county residents ($9,724 otherwise), from the PDF"
    assert w["total_note"].endswith("with the rental for county residents")
    monkeypatch.setattr(pf, "_COND", {})
    w = pf.build_cost_lines(_CostXano(7480), "t", {}, "V2118", access=True)
    assert [r for r in w["lines"] if r["key"] == "rental"][0]["note"] == "From the PDF"


# ---------------------------------------------------------------- one guest count on screen (row 4562 / msg 5068)

def test_show_more_headline_drops_a_count_the_prices_do_not_use():
    f = {"city": "New York", "guests": 300, "states": ["New York"], "radius_miles": 30}
    cards = [venue("V%d" % i, venue_fee_range=[None, None], rental_source="estimate", estimate_total=141200,
                   estimate_guests=250, address="1 Main St, Brooklyn, NY 11201", distance_miles=4.0) for i in range(3)]
    m = pf.apply(answer(cards=cards, search_filters=f), "full", {})
    assert m["text"] == "3 near New York"
    assert all(c["price_line"]["text"] == "About $141,200 for 250 guests before tax and tip, an estimate" for c in m["cards"])
    assert m["understood"]["guests"] == 300
    # the same batch priced at the headline's count keeps it
    same = [dict(c, estimate_guests=300) for c in cards]
    assert pf.apply(answer(cards=same, search_filters=f), "full", {})["text"] == "3 near New York for 300 guests"
    # rental lines name no count: the headline keeps it
    rent = [dict(c, venue_fee_range=[7000, 7000], rental_source="pdf", rental_when="peak_saturday") for c in cards]
    assert pf.apply(answer(cards=rent, search_filters=f), "full", {})["text"] == "3 near New York for 300 guests"


def test_canary_the_headline_check_sees_the_mismatch():
    # the check reads the price line's own count: a line "for 250 guests" under "for 300 guests" is what it must catch
    assert pf._PRICED_FOR.search("About $141,200 for 250 guests before tax and tip, an estimate").group(1) == "250"


# ---------------------------------------------------------------- the any-category mock (conftest)

def test_listed_vendor_follows_the_tests_listed_venue(monkeypatch):
    monkeypatch.setattr(xano, "listed_venue", lambda v: {"vendor_id": v, "category": "Venue"} if v == "V1" else None)
    assert xano.listed_vendor("V1") == {"vendor_id": "V1", "category": "Venue"} and xano.listed_vendor("V5") is None
