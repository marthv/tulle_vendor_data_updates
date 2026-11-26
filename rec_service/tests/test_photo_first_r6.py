"""Photo-first QA round 6 (2026-11-26): T0-53 no figure for a free couple on any photo-first quick action or price answer,
one guest count on screen, the any-category mock. No network. (The T0-54 conditional-rental tests were removed with
T0-55, owner 2026-11-26: no rental line on a member card and no conditions file on any couple-facing path.)"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
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
    # no price line at all (a rental and no estimate: T0-55 shows no rental line): the headline keeps it
    rent = [dict(c, venue_fee_range=[7000, 7000], rental_source="pdf", rental_when="peak_saturday", estimate_total=None)
            for c in cards]
    m = pf.apply(answer(cards=rent, search_filters=f), "full", {})
    assert m["text"] == "3 near New York for 300 guests" and all("price_line" not in c for c in m["cards"])


def test_canary_the_headline_check_sees_the_mismatch():
    # the check reads the price line's own count: a line "for 250 guests" under "for 300 guests" is what it must catch
    assert pf._PRICED_FOR.search("About $141,200 for 250 guests before tax and tip, an estimate").group(1) == "250"


# ---------------------------------------------------------------- the any-category mock (conftest)

def test_listed_vendor_follows_the_tests_listed_venue(monkeypatch):
    monkeypatch.setattr(xano, "listed_venue", lambda v: {"vendor_id": v, "category": "Venue"} if v == "V1" else None)
    assert xano.listed_vendor("V1") == {"vendor_id": "V1", "category": "Venue"} and xano.listed_vendor("V5") is None
