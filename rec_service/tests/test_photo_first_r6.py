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
    monkeypatch.setattr(xano, "budget_categories", lambda t: [{"id": 1, "Category": "Venue", "Budget_Focus_Normal": 40}])
    monkeypatch.setattr(xano, "user_packages", lambda t, uid: [])
    monkeypatch.setattr(xano, "venue_quotes", lambda t, e: [])
    monkeypatch.setattr(xano, "saved_vendor_ids", lambda u: [])
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


# Owner 2026-11-27 reversed T0-53 for the cost-tool TAPS: a free couple on a photo-first page gets exactly today's free answer
# (market averages / their own budget; compare_venues locked by free_mode). These two tests replace the T0-53 pins that said
# "upgrade line, no widget" - changed deliberately, not loosened. Typed answers keep T0-53 (tests further down).
@pytest.mark.parametrize("tool", cost_tools.TOOLS)
def test_free_couple_photo_first_cost_tool_gets_the_same_free_answer_as_switch_off(tool_env, monkeypatch, tool):
    code, j = tool_env(tool)
    assert code == 200 and j["price_locked"] is True and "upsell_prompt" not in j and j["text"] != pf.UPGRADE_LINE
    assert j["widgets"] and j["widgets"][0].get("type")
    monkeypatch.setattr(config, "PHOTO_FIRST", False)
    code2, off = tool_env(tool, pfq=False)
    assert code2 == 200 and j == off                       # byte for byte the non-photo-first free answer
    assert tool_env.stored[-1]["cards"] == [{"meta": "widget", "widget": j["widgets"][0]}]


def test_free_market_tools_are_marked_and_compare_is_not(tool_env):
    for tool in pf.MARKET_TOOLS:
        assert tool_env(tool)[1]["widgets"][0].get("market_only") is True
    assert "market_only" not in tool_env("compare_venues")[1]["widgets"][0]   # vendor pricing: never exempt from hiding


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


def test_reopened_free_tool_turns_show_as_tapped(tool_env, monkeypatch):
    """Owner 2026-11-27: a stored free cost-tool tap reads back as it was tapped (was: the upgrade line, T0-53)."""
    tool_env("estimate_cost", access=True)                    # a member's stored estimate (market averages, market_only)
    tool_env("estimate_cost")                                 # a free couple's stored estimate
    rows = [{"id": 900 + i, "chat_id": 5, "role": m["role"], "content": m["content"], "cards": m["cards"],
             "created_at": 1 + i, "chips": []} for i, m in enumerate(tool_env.stored)]
    turns = [xano.parse_message_row(r) for r in rows]
    monkeypatch.setattr(xano, "get_messages", lambda c: json.loads(json.dumps(turns)))
    monkeypatch.setattr(xano, "profile_of", lambda u: {})
    for n in ("has_paid_access", "beta_member", "pi_entitled"):
        monkeypatch.setattr(xano, n, lambda u: False)
    got = TestClient(app.app).get("/rec/chats/5?photo_first=1", headers=H).json()
    a = [t for t in got["turns"] if t["role"] == "assistant"]
    assert [[w.get("type") for w in t["widgets"]] for t in a] == [["cost_estimate"], ["cost_estimate"]]
    assert all(t["text"] != pf.UPGRADE_LINE and not t.get("upsell_prompt") for t in a)


def test_canary_an_unmarked_stored_estimate_is_still_held_back_for_free(tool_env):
    """A cost widget without market_only (a typed answer's, or anything older) keeps the T0-53 rule on reopen."""
    w = dict(TYPICAL)
    w.pop("market_only", None)
    got = pf.apply(answer(cards=[], text="A typical wedding here is about $37,400.", widgets=[w], paid=False, has_access=False,
                          price_locked=True), "free", {})
    assert got["widgets"] == [] and got["text"] == pf.UPGRADE_LINE


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


# ---------------------------------------------------------------- 2026-11-27: venue PDF prices stay locked for free couples

def _venue_priced(monkeypatch):
    """A saved venue whose PDF says rental $7,000 (CostXano's figures), reachable from every tool path."""
    from test_photo_first import CostXano
    cx = CostXano()
    monkeypatch.setattr(xano, "saved_vendor_ids", lambda u: ["V1"])
    monkeypatch.setattr(xano, "vendor_card", lambda v: cx.cached_vendor_card(v))
    monkeypatch.setattr(xano, "cached_vendor_card", lambda v, *a, **k: cx.cached_vendor_card(v))
    monkeypatch.setattr(xano, "venue_pricing", lambda t, v, paid, *a, **k: cx.cached_venue_pricing(t, v, paid))
    monkeypatch.setattr(xano, "cached_venue_pricing", lambda t, v, paid, *a, **k: cx.cached_venue_pricing(t, v, paid))
    monkeypatch.setattr(xano, "user_packages", lambda t, uid: [])


def _venue_digits(j):
    s = json.dumps(j)
    return [d for d in ("7000", "7,000", "Lily Pool Terrace") if d in s]


def _post(tool, args=None, chat=0):
    r = TestClient(app.app).post("/rec/tool?photo_first=1", json={"tool": tool, "args": args or {}, "chat_id": chat}, headers=H)
    return r.status_code, r.json()


@pytest.mark.parametrize("tool", list(cost_tools.TOOLS) + [pf.COST_TOOL])
def test_free_couple_never_gets_a_venue_pdf_price_from_any_tool(tool_env, monkeypatch, tool):
    _venue_priced(monkeypatch)
    tool_env("estimate_cost")                      # sets access False in the fixture
    code, j = _post(tool, {"vendor_id": "V1"} if tool == pf.COST_TOOL else {})
    assert code == 200, j
    assert _venue_digits(j) == [], _venue_digits(j)


def test_canary_a_member_does_get_the_venue_pdf_price(tool_env, monkeypatch):
    """Proves the digit check above can see a venue's PDF figure: a member's "What it costs" carries it."""
    _venue_priced(monkeypatch)
    tool_env("estimate_cost", access=True)         # access True from here on
    code, j = _post(pf.COST_TOOL, {"vendor_id": "V1"})
    assert code == 200 and "7000" in _venue_digits(j), j


@pytest.mark.parametrize("pfq", [True, False])
def test_rec_tool_runs_free_mode_widget_for_free_couples_only(tool_env, monkeypatch, pfq):
    """Final QA 11-27: /rec/tool must pass a free couple's widget through free_mode.widget (photo-first or not), and a
    member's not. Fails if that call is removed or skipped."""
    seen = []
    real = free_mode.widget
    monkeypatch.setattr(app.free_mode, "widget", lambda w: (seen.append(w.get("type")), dict(real(w), _free_mode=True))[1])
    for tool in cost_tools.TOOLS:
        code, j = tool_env(tool, pfq=pfq)
        assert code == 200 and j["widgets"][0].get("_free_mode") is True, (tool, j)
    assert len(seen) == len(cost_tools.TOOLS)
    code, j = tool_env("estimate_cost", access=True, pfq=pfq)
    assert code == 200 and "_free_mode" not in j["widgets"][0]
