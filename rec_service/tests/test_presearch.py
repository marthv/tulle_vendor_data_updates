"""Pre-search (2026-11-01 speed pass): on an opening the first search_venues call runs from the profile BEFORE the first
model call and reaches the model as a finished tool call (assistant tool_use + user tool_result), so the model starts on
what used to be turn 2. Refines and REC_PRESEARCH=0 are untouched; an empty or failed pre-search falls back to the model
searching itself. No network, no model: xano and the tool runner are stubbed."""
import copy
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import pytest  # noqa: E402

import agent  # noqa: E402
import prefs  # noqa: E402
import xano  # noqa: E402

USER = {"id": 7, "Wedding_Location_Updated": ["New York"], "Wedding_Guest_Count": 120, "Wedding_Budget": 60000}
ASK = [{"role": "user", "content": "Recommend 3 venues for us based on our profile."}]


class _Msg:
    content = []
    usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0)
    model = "stub"


def venue(vid, addr="1 Main St, Albany, NY 12207"):
    return {"vendor_id": vid, "vendor_idx": int(vid[1:]), "name": "Venue " + vid, "state": "New York", "address": addr,
            "venue_type": "Estate", "max_capacity_seated": 200, "venue_fee_range": [5000, 5000], "food_minimum": 0,
            "image": None, "description": ""}


def pricing():
    return {"guest_minimum": 0, "verdict": "", "headline": "", "source_note": "", "cost_inputs": None,
            "lines": [{"line": "Rental fee", "source": "venue", "vs_market": "typical", "amount": 5000, "pct": None,
                       "detail": ""}]}


@pytest.fixture
def world(monkeypatch):
    st = {"venues": [venue("V1"), venue("V2"), venue("V3"), venue("V4")], "calls": [], "seen": None, "by_filter": None}

    def search(token, *, states, page_size=8, **f):
        st["calls"].append(dict(f, states=states, page_size=page_size))
        vs = st["venues"]
        if st["by_filter"] is not None:
            vs = st["by_filter"](f)
        return {"total_matches": len(vs), "venues": copy.deepcopy(vs)}
    monkeypatch.setattr(xano, "search_venues", search)
    monkeypatch.setattr(xano, "venue_pricing", lambda tok, vid, paid: pricing())
    monkeypatch.setattr(xano, "market_benchmarks", lambda s, g=0: {"state": s, "metrics": {}})
    monkeypatch.setattr(xano, "saved_vendor_ids", lambda u: [])
    monkeypatch.setattr(xano, "vendor_card", lambda vid: venue(vid))

    def geocode(city, states):
        raise RuntimeError("no geocoder in tests")
    monkeypatch.setattr(xano, "geocode_city", geocode)

    class Runner:
        def __init__(self, tools, messages, **kw):
            self.tools = {(getattr(t, "name", None) or getattr(t, "__name__", None)): t for t in tools}
            st["tools"] = self.tools
            st["seen"] = {"messages": copy.deepcopy(messages), "calls_before_model": len(st["calls"])}

        def __iter__(self):
            msgs = st["seen"]["messages"]
            ids = []
            if len(msgs) > 1:
                ids = [v["vendor_id"] for v in json.loads(msgs[2]["content"][0]["content"])["venues"]][:3]
            if not ids:   # no pre-search: the model searches itself, as before
                out = json.loads(self.tools["search_venues"](**(st.get("model_call") or {"states": ["New York"], "guests": 120})))
                st["tool_out"] = out
                ids = [v["vendor_id"] for v in out["venues"]][:3]
            self.tools["present_recommendations"](message="Three picks.", vendor_ids=ids, reasons=["a", "b", "c"],
                                                  chips=["Cheaper options"])
            yield _Msg()

        def generate_tool_call_response(self):
            return None

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    monkeypatch.setattr(agent.anthropic, "Anthropic", Client, raising=False)
    return st


def test_opening_presearch_is_handed_to_the_model_as_a_finished_tool_call(world):
    res, usage = agent.run("tok", USER, True, ASK, detail="full", presearch={})
    msgs = world["seen"]["messages"]
    assert len(msgs) == 3 and msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[1]["content"][0]["name"] == "search_venues"
    assert msgs[1]["content"][0]["input"] == {"states": ["New York"], "guests": 120}
    assert msgs[2]["role"] == "user" and msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == msgs[1]["content"][0]["id"]
    assert "PRE-SEARCH" in msgs[0]["content"]
    assert world["seen"]["calls_before_model"] == 1          # the search ran before the first model call
    assert len(world["calls"]) == 1                          # ...and the model did not search again
    assert usage["presearch"] is True and usage["presearch_n"] == 4 and usage["presearch_ms"] >= 0
    assert [c["vendor_id"] for c in res["cards"]] == ["V1", "V2", "V3"]
    assert res["search_filters"]["states"] == ["New York"]   # the pre-search is the chat's primary search


def test_refine_never_presearches(world):
    agent.run("tok", USER, True, ASK, detail="full", presearch=None)
    assert len(world["seen"]["messages"]) == 1
    assert world["seen"]["calls_before_model"] == 0 and len(world["calls"]) == 1


def test_empty_presearch_falls_back_to_the_model(world):
    world["venues"] = []
    _, usage = agent.run("tok", USER, True, ASK, detail="full", presearch={})
    assert len(world["seen"]["messages"]) == 1 and "PRE-SEARCH" not in world["seen"]["messages"][0]["content"]
    assert usage["presearch"] is False and usage["presearch_n"] == 0


def test_presearch_failure_falls_back_to_the_model(world, monkeypatch):
    def boom(token, **f):
        world["calls"].append(f)
        if len(world["calls"]) == 1:
            raise RuntimeError("ep119 down")
        return {"total_matches": 3, "venues": copy.deepcopy(world["venues"][:3])}
    monkeypatch.setattr(xano, "search_venues", boom)
    res, usage = agent.run("tok", USER, True, ASK, detail="full", presearch={})
    assert usage["presearch"] is False and len(world["seen"]["messages"]) == 1
    assert len(res["cards"]) == 3


def test_preference_filters_are_tried_first_then_relaxed(world):
    world["by_filter"] = lambda f: world["venues"][:1] if f.get("venue_types") else world["venues"]
    _, usage = agent.run("tok", USER, True, ASK, detail="full", presearch={"venue_types": ["Barn / Ranch"], "vibes": []})
    assert len(world["calls"]) == 2
    assert world["calls"][0]["venue_types"] == ["Barn / Ranch"] and not world["calls"][1].get("venue_types")
    msgs = world["seen"]["messages"]
    assert "venue_types" not in msgs[1]["content"][0]["input"]
    assert "repeated without them" in msgs[0]["content"] and "venue_types" in msgs[0]["content"]
    assert usage["presearch"] is True


def test_preference_filters_kept_when_they_fit(world):
    world["by_filter"] = lambda f: world["venues"][:3] if f.get("venue_types") else world["venues"]
    agent.run("tok", USER, True, ASK, detail="full", presearch={"venue_types": ["Barn / Ranch"], "outdoor_ceremony": True})
    assert len(world["calls"]) == 1
    inp = world["seen"]["messages"][1]["content"][0]["input"]
    assert inp["venue_types"] == ["Barn / Ranch"] and inp["outdoor_ceremony"] is True
    assert "repeated without" not in world["seen"]["messages"][0]["content"]


def test_known_area_becomes_the_city(world):
    world["venues"] = [venue("V%d" % i, addr="%d Elm St, Dallas, TX 75201" % i) for i in range(1, 5)]
    agent.run("tok", {**USER, "Wedding_Location_Updated": ["Texas"]}, True, ASK, detail="full", presearch={},
              known_area="Dallas, Texas")
    inp = world["seen"]["messages"][1]["content"][0]["input"]
    assert inp["city"] == "Dallas" and inp["states"] == ["Texas"]
    assert world["calls"][0]["page_size"] == 60          # the city path reads the deep page


def test_no_state_means_no_presearch(world):
    agent.run("tok", {"id": 7, "Wedding_Guest_Count": 120}, True, ASK, detail="full", presearch={})
    assert len(world["seen"]["messages"]) == 1 and world["seen"]["calls_before_model"] == 0


def test_free_couple_presearch_uses_the_price_free_tool(world):
    _, usage = agent.run("tok", USER, False, ASK, detail="free", presearch={})
    msgs = world["seen"]["messages"]
    assert usage["presearch"] is True and len(msgs) == 3
    assert "pricing" not in json.loads(msgs[2]["content"][0]["content"])["venues"][0]


def test_search_filters_mapping():
    f = prefs.search_filters({"style": ["Barn / Ranch", "Estate / Mansion"], "setting": ["Waterfront"],
                              "catering": ["Raw Space"], "ceremony": ["outdoor"], "area": ["Dallas, Texas"],
                              "guests": ["150"], "feel": ["Rustic"], "priority": ["The location"]})
    assert f == {"city": "Dallas", "guests": 150, "venue_types": ["Barn / Ranch", "Estate / Mansion"],
                 "vibes": ["Waterfront"], "pricing_models": ["Raw Space", "Semi-Inclusive"], "outdoor_ceremony": True}
    assert prefs.search_filters({}) == {}
    assert prefs.search_filters({"catering": ["All-Inclusive"], "ceremony": ["indoor"]}) == {"pricing_models": ["All-Inclusive"]}


def test_a_named_venue_is_found_outside_the_style_filters(world):
    """2026-11-01 chat 926: 'Bear brook nj' with the Natural Light vibe + All-Inclusive active -> 'no venues listed'.
    A keyword that matches no result's name is searched again with the states only; the hit is shown first and the
    model is told which filters it falls outside of."""
    bear = dict(venue("V277", "23 Players Blvd, Fredon Township, NJ 07860, USA"), name="Bear Brook Valley",
                state="New Jersey")
    world["by_filter"] = lambda f: [] if (f.get("vibes") or f.get("pricing_models")) else [bear]
    world["model_call"] = {"states": ["New Jersey"], "keyword": "Bear Brook", "vibes": ["Natural Light / Large Windows"],
                           "pricing_models": ["All-Inclusive"]}
    agent.run("tok", USER, True, ASK, detail="full", presearch=None)
    out = world["tool_out"]
    assert [v["name"] for v in out["venues"]] == ["Bear Brook Valley"]
    assert out["named_venue_outside_filters"]["keyword"] == "Bear Brook"
    assert set(out["named_venue_outside_filters"]["filters_it_does_not_meet"]) == {"vibes", "pricing_models"}
    assert len(world["calls"]) == 2
    retry = world["calls"][-1]
    assert retry.get("keyword") == "Bear Brook" and not retry.get("vibes") and not retry.get("pricing_models")


def test_a_keyword_that_matches_a_result_name_does_not_retry(world):
    world["venues"] = [dict(venue("V5"), name="Oheka Castle")] + world["venues"][:2]
    world["model_call"] = {"states": ["New York"], "keyword": "Oheka", "vibes": ["Waterfront"]}
    agent.run("tok", USER, True, ASK, detail="full", presearch=None)
    out = world["tool_out"]
    assert out["venues"][0]["name"] == "Oheka Castle" and "named_venue_outside_filters" not in out
    assert len(world["calls"]) == 1


def test_usage_names_the_tools_and_categories_an_answer_used(world):
    """2026-11-02: the usage log and Mixpanel say WHICH tools ran and which vendor categories they touched."""
    _, usage = agent.run("tok", USER, True, ASK, detail="full", presearch=None)
    assert usage["tools_used"] == ["search_venues", "present_recommendations"]
    assert usage["categories"] == ["venue"]
    assert usage["think_chars"] == 0
