"""2026-10-14 fixes from 22 real chats: no repeats across turns (search + "Show 3 more"), stated constraints
(location, city, guests, price caps) carried into later turns and "Show 3 more", no false "new" claims, and
differentiated "Show 3 more" replies. No network, no model: xano and the tool runner are stubbed."""
import copy
import json
import pathlib
import re
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MONEY = re.compile(r"\$\s?\d|\d\s?%")


def _mods():
    import agent
    import xano
    return agent, xano


class _Msg:
    content = []
    usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0)
    model = "stub"


def _fake_client(script, log):
    """Stand-in tool runner: calls each scripted tool (args may be a function of the earlier returns), records
    every return in `log`, then ends."""
    class Runner:
        def __init__(self, tools, **kw):
            self.tools = {(getattr(t, "name", None) or getattr(t, "__name__", None)): t for t in tools}
        def __iter__(self):
            for name, args in script:
                if callable(args):
                    args = args(log)
                log.append((name, self.tools[name](**args)))
            yield _Msg()
    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    return Client


def venue(vid, name=None, addr="1 Main St, Albany, NY 12207", fee=5000, cap=200):
    return {"vendor_id": vid, "vendor_idx": int(re.sub(r"\D", "", vid) or 0), "name": name or "Venue %s" % vid,
            "state": "New York", "address": addr, "venue_type": "Estate", "max_capacity_seated": cap,
            "venue_fee_range": [fee, fee], "food_minimum": 0, "image": None, "description": ""}


def pricing(rental):
    return {"guest_minimum": 0, "verdict": "", "headline": "", "source_note": "", "cost_inputs": None,
            "lines": [{"line": "Rental fee", "source": "venue", "vs_market": "typical", "amount": rental, "pct": None,
                       "detail": ""}]}


@pytest.fixture
def world(monkeypatch):
    """xano stubs: search_venues returns st["venues"] (or per-page lists), pricing by st["rental"]."""
    agent, xano = _mods()
    st = {"venues": [], "pages": None, "calls": [], "rental": {}, "saved": [], "log": [], "geo": {}, "near": [],
          "near_calls": []}

    def near(lat, lng, radius, **f):
        st["near_calls"].append(dict(f, lat=lat, lng=lng, radius=radius))
        return {"total_matches": len(st["near"]), "venues": copy.deepcopy(st["near"])}

    def search(token, *, states, page_size=8, **f):
        st["calls"].append(dict(f, states=states, page_size=page_size))
        if st["pages"] is not None and "page" in f:
            vs = st["pages"].get(f.get("page", 1), [])
        elif st["pages"] is not None:   # one deep page (2026-10-17): pages 1.. concatenated, like ep119
            vs = [v for n in sorted(st["pages"]) for v in st["pages"][n]][:page_size]
        else:
            vs = st["venues"]
        return {"total_matches": 500, "venues": copy.deepcopy(vs)}
    monkeypatch.setattr(xano, "search_venues", search)
    monkeypatch.setattr(xano, "venue_pricing", lambda tok, vid, paid: pricing(st["rental"].get(vid, 5000)))
    monkeypatch.setattr(xano, "market_benchmarks", lambda s, g=0: {"state": s, "metrics": {}})
    monkeypatch.setattr(xano, "saved_vendor_ids", lambda u: list(st["saved"]))
    monkeypatch.setattr(xano, "vendor_card", lambda vid: venue(vid))
    monkeypatch.setattr(xano, "profile_of", lambda u: {"Wedding_Location_Updated": ["New York"]})
    monkeypatch.setattr(xano, "geocode_city", lambda city, states=(): st["geo"].get(city))
    monkeypatch.setattr(xano, "venues_near", near)

    def run(script, ask="more options", prior=(), active=None, detail="full", messages=None):
        st["log"].clear()
        monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(script, st["log"]), raising=False)
        msgs = messages or [{"role": "user", "content": ask}]
        return agent.run("tok", {"id": 0}, True, msgs, detail=detail, prior_vendor_ids=prior,
                         active_filters=active)
    st["run"] = run
    return st


def present(ids, msg="Here you go."):
    return ("present_recommendations", {"message": msg, "vendor_ids": ids, "reasons": [""] * len(ids),
                                        "chips": ["Cheaper options"]})


def _search_result(log):
    return json.loads(next(r for n, r in log if n == "search_venues"))


# ---------------------------------------------------------------- pure helpers

def test_merge_filters_inherits_hard_filters_the_model_left_empty():
    agent, _ = _mods()
    active = {"states": ["California"], "guests": 40, "max_venue_fee": 11000, "city": "San Diego"}
    got = agent.merge_filters({"states": ["California"], "guests": 0, "max_venue_fee": 0, "city": ""}, active)
    assert got["guests"] == 40 and got["max_venue_fee"] == 11000 and got["city"] == "San Diego"


def test_merge_filters_explicit_value_wins_and_relax_drops():
    agent, _ = _mods()
    active = {"states": ["California"], "guests": 40, "max_venue_fee": 11000}
    got = agent.merge_filters({"states": ["California"], "guests": 0, "max_venue_fee": 20000}, active, ["guests"])
    assert got["max_venue_fee"] == 20000 and not got.get("guests")


def test_merge_filters_new_state_drops_the_city_but_keeps_caps():
    agent, _ = _mods()
    active = {"states": ["New York"], "city": "Manhattan", "max_venue_fee": 9000}
    got = agent.merge_filters({"states": ["New Jersey"], "city": ""}, active)
    assert not got.get("city") and got["max_venue_fee"] == 9000
    assert agent.merge_filters({"states": [], "city": ""}, active)["states"] == ["New York"]


def test_in_city_nyc_by_zip_and_other_cities_by_name():
    agent, _ = _mods()
    assert agent.in_city("W 67th St, New York, NY 10023", "Manhattan")
    assert not agent.in_city("131 3rd Ave, Brooklyn, NY 11217", "Manhattan")
    assert agent.in_city("131 3rd Ave, Brooklyn, NY 11217", "New York City")
    assert not agent.in_city("23 Factory St, Montgomery, NY 12549", "NYC")
    assert not agent.in_city("640 McLean Ave, Yonkers, NY 10705, USA", "nyc")       # 107 = Westchester
    assert agent.in_city("10249 Summit Dr, San Diego, CA 92127", "San Diego")
    assert agent.in_city("anything", "")


# ---------------------------------------------------------------- shown-vendor exclusion

def test_search_leaves_out_venues_shown_earlier_in_the_chat(world):
    world["venues"] = [venue("V%d" % i) for i in range(1, 15)]
    res, _ = world["run"]([("search_venues", {"states": ["New York"]}), present(["V4", "V5", "V6"])],
                          prior=["V1", "V2", "V3"])
    r = _search_result(world["log"])
    ids = [v["vendor_id"] for v in r["venues"]]
    assert not {"V1", "V2", "V3"} & set(ids) and r["already_shown_left_out"] == 3
    assert not {"V1", "V2", "V3"} & {c["vendor_id"] for c in res["more_cards"]}


def test_saved_venues_left_out_unless_the_couple_asks_about_them(world):
    world["venues"] = [venue("V%d" % i) for i in range(1, 15)]
    world["saved"] = ["V1"]
    world["run"]([("search_venues", {"states": ["New York"]}), present(["V2"])], ask="cheaper options")
    assert "V1" not in [v["vendor_id"] for v in _search_result(world["log"])["venues"]]
    world["run"]([("search_venues", {"states": ["New York"]}), present(["V2"])], ask="which of my saved venues fit?")
    assert "V1" in [v["vendor_id"] for v in _search_result(world["log"])["venues"]]


def test_a_venue_the_couple_names_is_not_hidden(world):
    world["venues"] = [venue("V1", "Fonthill Castle Weddings"), venue("V2")]
    world["run"]([("search_venues", {"states": ["New York"], "keyword": "Fonthill"}), present(["V1"])],
                 ask="tell me about Fonthill", prior=["V1"])
    assert [v["vendor_id"] for v in _search_result(world["log"])["venues"]][0] == "V1"


def test_repeat_without_being_asked_is_bounced_once(world):
    world["venues"] = [venue("V%d" % i) for i in range(4, 14)]
    script = [("search_venues", {"states": ["New York"]}), present(["V1", "V4"], "Cheaper picks."),
              present(["V4", "V5", "V6"], "Cheaper picks.")]
    res, usage = world["run"](script, ask="Cheaper options", prior=["V1"])
    first = world["log"][1][1]
    assert first.startswith("NOT SHOWN") and "already shown" in first
    assert [c["vendor_id"] for c in res["cards"]] == ["V4", "V5", "V6"] and usage["repeat_bounced"]


def test_new_claim_on_a_repeat_is_bounced_even_when_pointing_back(world):
    world["venues"] = [venue("V%d" % i) for i in range(4, 14)]
    script = [("search_venues", {"states": ["New York"]}),
              present(["V1", "V4"], "Here are two new ones you haven't seen."), present(["V4"], "One more.")]
    res, _ = world["run"](script, ask="compare those with something else", prior=["V1"])
    assert world["log"][1][1].startswith("NOT SHOWN")


def test_breakdown_of_an_earlier_venue_is_not_a_repeat(world):
    script = [present(["V1"], "Here is the breakdown.")]
    res, usage = world["run"](script, ask="break down the first one", prior=["V1"])
    assert [c["vendor_id"] for c in res["cards"]] == ["V1"] and not usage["repeat_bounced"]


# ---------------------------------------------------------------- constraints carried across turns

def test_active_price_cap_and_guests_apply_when_the_model_omits_them(world):
    world["venues"] = [venue("V%d" % i) for i in range(1, 12)]
    world["rental"] = {"V2": 12000}           # cheapest space under the cap in ep119, the card's space over it
    res, _ = world["run"]([("search_venues", {"states": ["California"], "sort_by": "recent_desc"}),
                           present(["V1", "V3", "V4"])],
                          active={"states": ["California"], "guests": 40, "max_venue_fee": 11000})
    call = world["calls"][0]
    assert call["max_venue_fee"] == 11000 and call["guests"] == 40
    r = _search_result(world["log"])
    assert "V2" not in [v["vendor_id"] for v in r["venues"]] and r["left_out_for_minimums"]["rental_over_cap"] == 1
    assert r["filters_used"]["max_venue_fee"] == 11000
    assert res["search_filters"]["max_venue_fee"] == 11000 and res["search_filters"]["guests"] == 40
    assert all((c["venue_fee_range"][0] or 0) <= 11000 for c in res["cards"] + res["more_cards"])


def test_relax_drops_a_cap_the_couple_removed(world):
    world["venues"] = [venue("V%d" % i) for i in range(1, 6)]
    world["run"]([("search_venues", {"states": ["California"], "relax": ["max_venue_fee"]}), present(["V1"])],
                 active={"states": ["California"], "max_venue_fee": 11000})
    assert world["calls"][0]["max_venue_fee"] == 0


def test_city_filter_reads_one_deep_page_and_keeps_only_city_addresses(world):
    man = lambda i: venue("M%d" % i, addr="%d W 57th St, New York, NY 10019" % i)
    up = lambda i: venue("U%d" % i, addr="%d Main St, Tarrytown, NY 10591" % i)
    world["pages"] = {1: [man(1), up(1), man(2)], 2: [up(2), man(3), man(4)], 3: [man(5), man(6), man(7), up(3)]}
    res, _ = world["run"]([("search_venues", {"states": ["New York"], "city": "Manhattan"}),
                           present(["M1", "M2", "M3"])])
    assert [c["page_size"] for c in world["calls"]] == [60] and not any("page" in c for c in world["calls"])
    r = _search_result(world["log"])
    assert all(v["vendor_id"].startswith("M") for v in r["venues"])
    assert [c["vendor_id"] for c in res["more_cards"]] == ["M4", "M5", "M6"]
    assert res["search_filters"]["city"] == "Manhattan"


def test_show_more_pool_comes_from_the_primary_search_only(world):
    """Chat 42: picks from a New York City search, "Show 3 more" served upstate venues from a second,
    state-wide search in the same request."""
    city = [venue("C%d" % i, addr="%d Broadway, New York, NY 10001" % i) for i in range(1, 8)]
    wide = [venue("W%d" % i) for i in range(1, 12)]
    world["pages"] = {1: city + wide[:3], 2: [], 3: []}

    def search_args(log):
        return {"states": ["New York"], "city": "New York City"}
    script = [("search_venues", search_args), present(["C1", "C2", "C3"])]
    # the second, wide search runs first here and feeds nothing the couple sees
    world["venues"] = wide
    res, _ = world["run"]([("search_venues", {"states": ["New York"], "keyword": "club"})] + script)
    assert all(c["vendor_id"].startswith("C") for c in res["more_cards"]) and res["more_cards"]
    assert res["search_filters"].get("city") == "New York City"


# ---------------------------------------------------------------- storage, /rec/more, chat history

def test_filters_round_trip_through_the_cards_column_and_stay_hidden():
    import app
    _, xano = _mods()
    out = {"cards": [{"vendor_id": "a"}], "more_cards": [{"vendor_id": "b"}],
           "search_filters": {"states": ["New York"], "city": "Manhattan", "max_venue_fee": 9000}}
    stored = app._stored_cards(out)
    turn = xano.parse_message_row({"role": "assistant", "content": "x", "cards": stored, "chips": []})
    assert turn["cards"] == [{"vendor_id": "a"}] and turn["more_cards"] == [{"vendor_id": "b"}]
    assert turn["filters"]["city"] == "Manhattan"
    assert "search_filters" not in app._public(out)
    msgs, kw = app._model_inputs([turn, {"role": "user", "text": "Show 3 more", "cards": []},
                                  {"role": "assistant", "text": "3 more", "cards": [{"vendor_id": "b"}],
                                   "is_more": True}], "cheaper?")
    assert kw["active_filters"]["max_venue_fee"] == 9000 and set(kw["prior_vendor_ids"]) == {"a", "b"}


@pytest.fixture
def api(monkeypatch):
    import app
    import config
    _, xano = _mods()
    st = {"history": [], "added": [], "access": True}
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "title": "t"})
    monkeypatch.setattr(xano, "get_messages", lambda cid: copy.deepcopy(st["history"]))
    monkeypatch.setattr(xano, "log_usage", lambda row: None)
    monkeypatch.setattr(xano, "add_message", lambda *a: st["added"].append(a))
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(xano, "spend_today", lambda: (0.0, 0.0))
    monkeypatch.setattr(xano, "has_forever", lambda u: st["access"])
    monkeypatch.setattr(xano, "has_paid_access", lambda u: st["access"])
    monkeypatch.setattr(xano, "beta_member", lambda u: st["access"])
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(app, "_feedback_given", lambda uid: True)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)
    return st


def _answer(picks, more, filters=None):
    t = {"role": "assistant", "text": "picks", "cards": picks, "more_cards": more, "is_more": False, "chips": ["c"]}
    if filters:
        t["filters"] = filters
    return t


def test_show_more_reply_is_specific_and_carries_the_constraints(api):
    more = [venue("V4", "Arch Hall", fee=9000, cap=120), venue("V5", "Brook Barn", fee=4000, cap=300),
            venue("V6", "Cove Inn", fee=6500, cap=60)]
    api["history"] = [_answer([venue("V1")], more, {"states": ["New York"], "city": "Manhattan",
                                                     "guests": 100, "max_venue_fee": 11000})]
    j = TestClient(__import__("app").app).post("/rec/more", json={"chat_id": 9}).json()
    assert j["text"] != "Here are 3 more from the same search."
    assert "Manhattan" in j["text"] and "100 guests" in j["text"] and "$11K" in j["text"]
    assert "Brook Barn has the lowest rental" in j["text"]
    reasons = [c["reason"] for c in j["cards"]]
    assert all(reasons) and len(set(reasons)) == 3
    assert "lowest rental here" in reasons[1] and "largest here" in reasons[1] and "most intimate" in reasons[2]
    stored_text = api["added"][1][2]
    assert stored_text == j["text"]


def test_show_more_for_a_free_user_has_no_price_words(api):
    api["access"] = False
    more = [venue("V4", fee=9000, cap=120), venue("V5", fee=4000, cap=300), venue("V6", fee=6500, cap=60)]
    api["history"] = [_answer([venue("V1")], more, {"states": ["New York"], "max_venue_fee": 11000})]
    r = TestClient(__import__("app").app).post("/rec/more", json={"chat_id": 9})
    assert r.status_code == 200, r.text
    j = r.json()
    assert not MONEY.search(j["text"]) and not MONEY.search(json.dumps(j["cards"]))
    assert "rental" not in j["text"].lower()


def test_chat_detail_never_exposes_filters(api):
    api["history"] = [_answer([venue("V1")], [], {"states": ["New York"], "max_venue_fee": 9000})]
    turns = TestClient(__import__("app").app).get("/rec/chats/9").json()["turns"]
    assert all("filters" not in t for t in turns)


def test_peak_caveat_is_said_once_per_chat(world):
    agent, _ = _mods()
    world["venues"] = [venue("V%d" % i) for i in range(1, 6)]
    msgs = [{"role": "user", "content": "venues"},
            {"role": "assistant", "content": "Prices are for a peak-season Saturday evening. Open V1's PDF first."},
            {"role": "user", "content": "more options"}]
    res, _ = world["run"]([("search_venues", {"states": ["New York"]}),
                           present(["V2", "V3"], "V2 is the value pick. Prices are for a peak-season Saturday "
                                                 "evening, so Fridays are cheaper. V3 seats more.")],
                          messages=msgs)
    assert "peak" not in res["text"].lower() and "V2 is the value pick." in res["text"] and "V3 seats more." in res["text"]
    assert agent.strip_repeat_caveat("• Rental: $5,000 (peak Saturday)") == "• Rental: $5,000 (peak Saturday)"


def test_hiding_shown_venues_backfills_from_later_pages(world):
    """Replay of chat 33 (2026-10-14): with 13 venues shown, one 16-result page left nothing new and the model
    said the couple had seen every venue. Hiding now reads the top 60 (one page since 2026-10-17)."""
    world["pages"] = {n: [venue("V%d" % i) for i in range(5 * n - 4, 5 * n + 1)] for n in (1, 2, 3)}
    res, _ = world["run"]([("search_venues", {"states": ["New York"]}), present(["V6", "V7", "V8"])],
                          prior=["V%d" % i for i in range(1, 6)])
    assert [c["page_size"] for c in world["calls"]] == [60] and not any("page" in c for c in world["calls"])
    ids = [v["vendor_id"] for v in _search_result(world["log"])["venues"]]
    assert ids[:3] == ["V6", "V7", "V8"] and not {"V1", "V5"} & set(ids)
    assert [c["vendor_id"] for c in res["more_cards"]] == ["V9", "V10", "V11", "V12", "V13", "V14"]


# ---------------------------------------------------------------- near a town (2026-10-21 chat 247)

def test_a_town_is_searched_by_distance_not_by_address(world):
    """'Stay close to flower mound tx' -> 'no venues in Flower Mound': none had the town in its address, but 17
    seating 175+ were within 15 miles. A geocoded town reads venues_near, nearest first, with distances."""
    world["geo"]["Flower Mound"] = (33.01, -97.10, "Flower Mound, Texas")
    world["near"] = [dict(venue("N1", addr="215 E Dallas Rd, Grapevine, TX 76051"), distance_miles=5.7),
                     dict(venue("N2", addr="1213 Country Club Rd, Argyle, TX 76226"), distance_miles=9.2)]
    res, _ = world["run"]([("search_venues", {"states": ["Texas"], "city": "Flower Mound", "guests": 175,
                                              "venue_types": ["Country Club / Private Club"]}),
                           present(["N1", "N2"])])
    assert world["calls"] == []                               # ep119 not used for a geocoded town
    c = world["near_calls"][0]
    assert c["radius"] == 25 and c["guests"] == 175 and c["venue_types"] == ["Country Club / Private Club"]
    r = _search_result(world["log"])
    assert [v["vendor_id"] for v in r["venues"]] == ["N1", "N2"]
    assert r["venues"][0]["distance_miles"] == 5.7
    assert "within 25 miles of Flower Mound, Texas" in r["city_note"]
    assert res["search_filters"]["city"] == "Flower Mound"


def test_nothing_within_the_radius_tells_the_model_to_widen(world):
    world["geo"]["Marfa"] = (30.3, -104.0, "Marfa, Texas")
    world["run"]([("search_venues", {"states": ["Texas"], "city": "Marfa", "radius_miles": 30}), present([])])
    r = _search_result(world["log"])
    assert world["near_calls"][0]["radius"] == 30
    assert "bigger radius_miles (e.g. 60)" in r["city_note"]


def test_radius_is_clamped_and_sticky_but_resets_for_a_new_town(world):
    world["geo"]["Austin"] = (30.27, -97.74, "Austin, Texas")
    world["run"]([("search_venues", {"states": ["Texas"], "city": "Austin", "radius_miles": 500}), present([])])
    assert world["near_calls"][0]["radius"] == 100
    agent = _mods()[0]
    active = {"states": ["Texas"], "city": "Austin", "radius_miles": 50}
    assert agent.merge_filters({"states": ["Texas"], "city": ""}, active)["radius_miles"] == 50
    assert not agent.merge_filters({"states": ["Texas"], "city": "Dallas"}, active).get("radius_miles")
    assert not agent.merge_filters({"states": ["Texas"], "city": ""}, active, ["radius_miles"]).get("radius_miles")


def test_a_place_that_does_not_geocode_falls_back_to_the_address_match(world):
    up = lambda i: venue("U%d" % i, addr="%d Main St, Tarrytown, NY 10591" % i)
    world["pages"] = {1: [up(1), venue("A1"), up(2)]}
    world["run"]([("search_venues", {"states": ["New York"], "city": "Tarrytown"}), present(["U1"])])
    assert world["near_calls"] == [] and [c["page_size"] for c in world["calls"]] == [60]
    r = _search_result(world["log"])
    assert [v["vendor_id"] for v in r["venues"]] == ["U1", "U2"]
    assert "address is in Tarrytown" in r["city_note"]


def test_a_geocoder_error_falls_back_instead_of_failing(world, monkeypatch):
    _, xano = _mods()

    def boom(city, states=()):
        raise RuntimeError("geocoder down")
    monkeypatch.setattr(xano, "geocode_city", boom)
    world["pages"] = {1: [venue("A1")]}
    world["run"]([("search_venues", {"states": ["New York"], "city": "Albany"}), present(["A1"])])
    assert [v["vendor_id"] for v in _search_result(world["log"])["venues"]] == ["A1"]


def _row(i, lat, lng, **kw):
    r = {"id": i, "Vendor_ID": "V%d" % i, "Name": "Venue %d" % i, "State": "Texas", "Address": "x",
         "Venue_Type": "x", "Max_Capacity_Seated": 200, "lat": lat, "lng": lng, "Validated_Data": "1",
         "Category": "Venue", "flt_min_venue_fee": 5000, "flt_max_venue_fee": 6000, "flt_min_ppfb": 0,
         "flt_venue_types": ["Barn / Ranch"], "flt_venue_attributes": ["Tall / Vaulted Ceilings"],
         "flt_venue_offerings": ["Raw Space"], "flt_outdoor_ceremony": True, "flt_glance": None,
         "image_1": None, "Description": ""}
    r.update(kw)
    return r


def test_venues_near_filters_like_ep119_and_sorts_by_distance(monkeypatch):
    _, xano = _mods()
    rows = [_row(1, 33.20, -97.10),                                   # ~13 mi
            _row(2, 33.02, -97.10),                                   # ~0.5 mi
            _row(3, 34.50, -97.10),                                   # ~100 mi: outside
            _row(4, 33.03, -97.10, Max_Capacity_Seated=100),          # too small
            _row(5, 33.03, -97.10, Max_Capacity_Seated=0),            # capacity unknown: out when guests given
            _row(6, 33.03, -97.10, Validated_Data="0"),               # not validated
            _row(7, 33.03, -97.10, flt_venue_types=["Hotel / Resort"]),
            _row(8, 33.03, -97.10, flt_min_venue_fee=0)]              # fee unknown: out when capped
    seen = []

    def meta(method, path, json=None):
        seen.append(json)
        return {"items": rows, "nextPage": None}
    monkeypatch.setattr(xano, "_meta", meta)
    r = xano.venues_near(33.0146, -97.0970, 25, guests=175, venue_types=["Barn / Ranch"], max_venue_fee=9000)
    assert [v["vendor_id"] for v in r["venues"]] == ["V2", "V1"] and r["total_matches"] == 2
    assert r["venues"][0]["distance_miles"] < 1 < r["venues"][1]["distance_miles"] < 25
    box = {k: v for d in seen[0]["search"] for k, v in d.items()}
    assert box["Category"] == "Venue" and box["lat|>="] < 33.0146 < box["lat|<="]


def test_venues_near_stops_paging_if_the_box_filter_is_ignored(monkeypatch):
    _, xano = _mods()
    pages = []

    def meta(method, path, json=None):
        pages.append(json["page"])
        return {"items": [], "nextPage": json["page"] + 1}
    monkeypatch.setattr(xano, "_meta", meta)
    xano.venues_near(33.0, -97.0, 25)
    assert pages == [1, 2, 3, 4]


def test_geocode_city_wants_a_town_in_the_couples_state(monkeypatch):
    _, xano = _mods()
    xano._GEO.clear()
    hits = [{"name": "Flower Mound", "admin1": "New Jersey", "latitude": 38.9, "longitude": -74.8,
             "feature_code": "PPL", "population": None},
            {"name": "Flower Mound", "admin1": "Texas", "latitude": 33.01, "longitude": -97.09,
             "feature_code": "PPL", "population": 71253},
            {"name": "Flower Mound Airport", "admin1": "Texas", "latitude": 33.02, "longitude": -97.12,
             "feature_code": "AIRP"}]
    calls = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": hits}

    def get(url, params=None, timeout=None):
        calls.append(params["name"])
        return R()
    monkeypatch.setattr(xano.requests, "get", get)
    assert xano.geocode_city("Flower Mound, TX", ["Texas"]) == (33.01, -97.09, "Flower Mound, Texas")
    assert xano.geocode_city("flower mound", ["Texas"]) == (33.01, -97.09, "Flower Mound, Texas")
    assert calls == ["Flower Mound"]                              # cached; state suffix stripped
    assert xano.geocode_city("Flower Mound", ["Ohio"]) is None    # never a same-name town in another state
    hits[:] = [{"name": "Hudson Valley Mobile Home Park", "admin1": "New York", "latitude": 1, "longitude": 1,
                "feature_code": "PPL"},
               {"name": "Catskill Mountains", "admin1": "New York", "latitude": 1, "longitude": 1,
                "feature_code": "MTS"}]
    assert xano.geocode_city("Catskills", ["New York"]) is None   # mountains are not a populated place
    assert xano.geocode_city("Hudson Valley", ["New York"]) is None   # a mobile home park is not the region
    xano._GEO.clear()


def _first_message(monkeypatch, active=None):
    agent = _mods()[0]
    got = {}

    class Runner:
        def __init__(self, **kw):
            got["first"] = kw["messages"][0]["content"]

        def __iter__(self):
            yield _Msg()

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    monkeypatch.setattr(agent.anthropic, "Anthropic", Client, raising=False)
    agent.run("tok", {"id": 0}, True, [{"role": "user", "content": "hi"}], active_filters=active)
    return got["first"]


def test_state_only_profile_tells_the_model_to_ask_where(world, monkeypatch):
    first = _first_message(monkeypatch)
    assert "LOCATION: STATE ONLY (New York)" in first and "close to - in the message itself" in first
    assert "STATE ONLY" not in _first_message(monkeypatch, {"states": ["New York"], "city": "Albany"})
    assert "LOCATION: STATE ONLY" in _mods()[0].SYSTEM
