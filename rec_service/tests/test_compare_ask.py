"""REC_COMPARE_ASK (owner 2026-11-27): "compare venues" asks WHICH venues (a picker), then the side by side.
The old ranked "Your venues, compared" table (venue_compare) must not come back from these entry points.
No network, no model: xano and the agent are faked. Pass bar written before the build: typed/chip/tool compare ->
widget type compare_picker, no venue_compare, no model call, no venue price digit for a free couple."""
import json
import pathlib
import re
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import pytest  # noqa: E402

FIGURE = re.compile(r"[$€£]\s?\d|\b\d[\d,.]*\s?[kK]\b|\d\s?%")
# Prices planted on the saved venues' cards: none of them may reach a free couple (or anyone: the picker has no price).
CARDS = {
    "V1": {"vendor_id": "V1", "vendor_idx": 1, "name": "Alpha Hall", "state": "New York", "category": "Venue",
           "address": "1 A St, Brooklyn, NY 11201, USA", "image": "https://img/a.jpg", "venue_fee_range": [38500, 41250],
           "estimated_total": 77777, "reason": "Cheapest at $38,500"},
    "V2": {"vendor_id": "V2", "vendor_idx": 2, "name": "Beta Loft", "state": "New York", "category": "Venue",
           "image": {"url": "https://img/b.jpg"}, "venue_fee_range": [12345, 12345], "food_minimum": 54321},
    "P1": {"vendor_id": "P1", "name": "Photo Co", "category": "Photography", "venue_fee_range": [999, 999]},
}
PLANTED = ("38500", "38,500", "41250", "77777", "12345", "54321", "999")


def _mods():
    import agent, app, compare, config, cost_tools, free_mode, xano
    return agent, app, compare, config, cost_tools, free_mode, xano


@pytest.fixture
def api(monkeypatch):
    agent, app, compare, config, cost_tools, free_mode, xano = _mods()
    from fastapi.testclient import TestClient
    st = {"access": False, "user": {"id": 7, "favorited_vendors": ["V1", "P1", "V2"]}, "model_calls": 0,
          "history": [], "old_tool": 0, "added": []}
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "COMPARE_PICK", True)
    monkeypatch.setattr(config, "COMPARE_ASK", True, raising=False)
    monkeypatch.setattr(config, "COMPARE_USER_IDS", {7})
    monkeypatch.setattr(config, "COMPARE_AB_PCT", 0)
    monkeypatch.setattr(config, "PROFILE_QUESTIONS", False)

    def guarded(*a, **k):
        st["model_calls"] += 1
        return {"text": "model answer", "cards": [], "chips": [], "widgets": [{"type": "venue_compare", "rows": []}],
                "price_locked": not st["access"], "has_access": st["access"], "paid": False}
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", st["user"]))
    monkeypatch.setattr(app, "_run_guarded", guarded)
    monkeypatch.setattr(app, "_lookup_fast", lambda *a, **k: None)
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    monkeypatch.setattr(app, "_submit_usage", lambda row: None)
    monkeypatch.setattr(xano, "cached_vendor_card", lambda vid: dict(CARDS[vid]) if vid in CARDS else None)
    monkeypatch.setattr(xano, "create_chat", lambda uid, title: {"id": 51, "title": title})
    monkeypatch.setattr(xano, "list_chats", lambda uid: [])
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "title": "t"})
    monkeypatch.setattr(xano, "get_messages", lambda cid: list(st["history"]))
    monkeypatch.setattr(xano, "add_message", lambda *a: st["added"].append(a))
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "has_forever", lambda u: False)
    monkeypatch.setattr(xano, "beta_member", lambda u: st["access"])
    monkeypatch.setattr(xano, "has_paid_access", lambda u: st["access"])
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)
    real = cost_tools.run_tool

    def run_tool(name, *a, **k):
        if name == "compare_venues" and not compare.ask_on(st["user"]):
            st["old_tool"] += 1
            return {"widget": {"type": "venue_compare", "rows": [], "unpriced": []}, "facts": {}}
        return real(name, *a, **k)
    monkeypatch.setattr(cost_tools, "run_tool", run_tool)
    st["c"] = TestClient(app.app)
    return st


def _types(j):
    return [w.get("type") for w in j.get("widgets") or []]


@pytest.mark.parametrize("text", ["compare venues", "Compare my saved venues", "compare my venues and quotes",
                                  "Yes, compare my saved venues", "Compare my last saved venues", "compare",
                                  "can you compare our favorite venues?", "Compare saved"])
def test_typed_compare_asks_with_a_picker_not_the_old_table(api, text):
    r = api["c"].post("/rec/refine", json={"chat_id": 9, "message": text})
    assert r.status_code == 200, r.text
    j = r.json()
    assert _types(j) == ["compare_picker"] and "venue_compare" not in r.text
    assert api["model_calls"] == 0                     # no model, no question used
    assert j["text"] == _mods()[2].ASK_TEXT
    w = j["widgets"][0]
    assert [v["vendor_id"] for v in w["venues"]] == ["V2", "V1"]      # newest saved first; the photographer left out
    assert w["max"] == 3 and w["min"] == 2 and w["enough"] is True


@pytest.mark.parametrize("text", ["compare Alpha Hall and Beta Loft", "which is cheaper, Alpha or Beta?",
                                  "compare the food at Oheka Castle", "find venues in Brooklyn"])
def test_a_named_compare_or_other_question_still_goes_to_the_model(api, text):
    r = api["c"].post("/rec/refine", json={"chat_id": 9, "message": text})
    assert r.status_code == 200 and api["model_calls"] == 1 and "compare_picker" not in r.text


@pytest.mark.parametrize("access", [False, True])
def test_no_venue_price_digit_in_the_picker_for_anyone(api, access):
    api["access"] = access
    j = api["c"].post("/rec/refine", json={"chat_id": 9, "message": "compare venues"}).json()
    blob = json.dumps(j)
    assert not FIGURE.search(json.dumps({k: j.get(k) for k in ("text", "widgets", "chips")})), blob
    for p in PLANTED:
        assert p not in blob
    keys = set().union(*[set(v) for v in j["widgets"][0]["venues"]])
    assert keys <= {"vendor_id", "vendor_idx", "name", "place", "image", "category", "saved"}
    assert j["price_locked"] is (not access)


def test_chat_venues_are_offered_too(api):
    api["user"] = {"id": 7, "favorited_vendors": ["V1"]}
    api["history"] = [{"role": "user", "text": "venues in brooklyn"},
                      {"role": "assistant", "text": "Here.", "cards": [dict(CARDS["V2"]), dict(CARDS["V1"]),
                                                                        {"vendor_id": "V9", "name": "Gamma", "category": "Venue"}]}]
    j = api["c"].post("/rec/refine", json={"chat_id": 9, "message": "compare venues"}).json()
    vs = j["widgets"][0]["venues"]
    assert [(v["vendor_id"], v["saved"]) for v in vs] == [("V1", True), ("V2", False), ("V9", False)]


def test_fewer_than_two_says_so_and_offers_find_or_look_up(api):
    api["user"] = {"id": 7, "favorited_vendors": ["V1", "P1"]}
    j = api["c"].post("/rec/tool", json={"tool": "compare_venues", "args": {}}).json()
    w = j["widgets"][0]
    assert w["type"] == "compare_picker" and w["enough"] is False and len(w["venues"]) == 1
    assert j["text"] == _mods()[2].FEW_TEXT
    assert _mods()[2].picker_chips(w) == ["Find venues", "Look up a venue"]


def test_tools_menu_compare_venues_returns_the_picker(api):
    r = api["c"].post("/rec/tool", json={"tool": "compare_venues", "args": {}, "chat_id": 9})
    assert r.status_code == 200 and _types(r.json()) == ["compare_picker"] and api["old_tool"] == 0
    assert "venue_compare" not in r.text
    assert api["added"][-1][1] == "assistant" and api["added"][-1][3][0]["widget"]["type"] == "compare_picker"   # stored


def test_new_conversation_typed_compare_gets_the_picker(api):
    j = api["c"].post("/rec/refine", json={"messages": [{"role": "user", "content": "compare venues"}],
                                           "message": "compare venues"}).json()
    assert _types(j) == ["compare_picker"] and j["chat_id"] == 51 and api["model_calls"] == 0


def test_model_tool_compare_venues_gets_the_picker_too(api):
    _, _, compare, _, cost_tools, _, xano = _mods()
    out = cost_tools.run_tool("compare_venues", {}, "tok", api["user"], access=False, xano=xano)
    assert out["widget"]["type"] == "compare_picker" and "venues_to_pick" in out["facts"]


def test_switch_off_or_control_arm_is_exactly_as_before(api, monkeypatch):
    config = _mods()[3]
    monkeypatch.setattr(config, "COMPARE_ASK", False, raising=False)
    r = api["c"].post("/rec/refine", json={"chat_id": 9, "message": "compare venues"})
    assert api["model_calls"] == 1 and "compare_picker" not in r.text
    r = api["c"].post("/rec/tool", json={"tool": "compare_venues", "args": {}})
    assert _types(r.json()) == ["venue_compare"] and api["old_tool"] == 1
    monkeypatch.setattr(config, "COMPARE_ASK", True, raising=False)
    monkeypatch.setattr(config, "COMPARE_USER_IDS", set())       # not in the compare arm: compare_picked is refused
    r = api["c"].post("/rec/refine", json={"chat_id": 9, "message": "compare venues"})
    assert api["model_calls"] == 2 and "compare_picker" not in r.text


def test_free_mode_widget_rebuilds_the_picker_from_a_whitelist():
    free_mode = _mods()[5]
    w = {"type": "compare_picker", "v": 1, "venues": [{"vendor_id": "V1", "name": "A $38,500 hall", "place": "NY",
                                                        "venue_fee_range": [1, 2], "cost": 5}], "enough": False}
    out = free_mode.widget(w)
    assert out["type"] == "compare_picker" and set(out["venues"][0]) == {"vendor_id", "vendor_idx", "name", "place", "image",
                                                                          "category", "saved"}
    assert "38" not in json.dumps(out)


def test_photo_first_keeps_the_question(monkeypatch):
    import photo_first
    out = {"text": "Which venues do you want to compare? Pick 2 or 3.", "cards": [], "widgets": [{"type": "compare_picker", "venues": []}]}
    res = photo_first.apply(out, "free")
    assert res["text"] == out["text"] and res["widgets"] == out["widgets"]
