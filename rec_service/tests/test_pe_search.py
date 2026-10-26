"""Photographers + entertainment (2026-10-11): ep119 P/E parsing, keyword ranking, ep207 parsing, and the
DETAIL: LIGHT stripping the assistant must do itself (ep119 hands exact package prices to any signed-in user).
No network: ep119/ep207 responses are the synthetic fixtures in fixtures/pe_samples.json."""
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

FX = json.loads((pathlib.Path(__file__).parent / "fixtures" / "pe_samples.json").read_text(encoding="utf-8"))
MONEY = re.compile(r"\$\s?\d|\d\s?%")


def _mods():
    # Lazy, like test_card_rental: test_guards sets env vars before ITS config import.
    import agent
    import xano
    return agent, xano


def _fake_get(calls, by_category):
    def get(url, token=None, params=None):
        calls.append((url, dict(params or {})))
        if url.endswith("/pe_benchmarks"):
            key = "ep207_%s_%s" % (params["category"].lower(), params["state"].lower().replace(" ", "_"))
            return copy.deepcopy(FX.get(key) or {"overall": None})
        return copy.deepcopy(by_category[params["Category_Input"]])
    return get


# ---------------------------------------------------------------- xano.search_pe

def test_photography_params_and_parsing(monkeypatch):
    _, xano = _mods()
    calls = []
    monkeypatch.setattr(xano, "_get", _fake_get(calls, {"Photography": FX["ep119_photography"]}))
    r = xano.search_pe("tok", "Photography", states=["California"], max_price=5500,
                       types=["Wedding Day", "Banana"], offers=["Videography", "Photo Booth"],
                       hour_bands=["7-10 Hours", "8 hours"], page_size=8)
    url, p = calls[0]
    assert url.endswith("/wptp_updated_mappings_search")
    assert p["Category_Input"] == "Photography" and p["base_fee_max"] == 5500 and p["Search_Input"] == ""
    assert p["venue_types[]"] == ["Wedding Day"]            # invalid values dropped, never sent
    assert p["vibes[]"] == ["Videography"]                  # Photo Booth is an entertainment offer
    assert p["pricing_models[]"] == ["7-10 Hours"]
    assert p["fb_per_person_max"] == 0 and p["max_capacity"] == 0
    assert r["total_matches"] == 340
    v = r["vendors"][0]
    assert v["vendor_id"] == "V9001" and v["vendor_idx"] == 9001 and v["category"] == "Photography"
    assert v["state"] == "California"                       # the searched state, not the 4-state string
    assert v["package_count"] == 3 and v["price_range"] == [1200, 5200] and v["hours_range"] == [4, 10]
    assert "Videography;Drone" not in v["offers"] and "Videography" in v["offers"]
    assert v["packages"][1] == {"name": "Signature $3,800 Collection", "price": 3800, "hours": 8, "type": "Wedding Day",
                                "team": 2, "included": ["Second Shooter", "Full Gallery Rights", "Engagement Session"],
                                "addons": ["Album"]}
    empty = r["vendors"][1]                                 # a vendor with no priced package
    assert empty["package_count"] == 0 and empty["price_range"] == [None, None] and empty["hours_range"] is None


def test_entertainment_types_alias_and_no_hour_bands(monkeypatch):
    _, xano = _mods()
    calls = []
    monkeypatch.setattr(xano, "_get", _fake_get(calls, {"Entertainment": FX["ep119_entertainment"]}))
    xano.search_pe("tok", "Entertainment", states=["New York"], types=["DJ", "Other", "band"],
                   hour_bands=["7-10 Hours"], offers=["Photo Booth"])
    p = calls[0][1]
    assert p["venue_types[]"] == ["DJ", "Other Entertainment", "Live Band"]
    assert "pricing_models[]" not in p                      # unused for entertainment in ep119
    assert p["vibes[]"] == ["Photo Booth"]


def test_bad_category_refused():
    _, xano = _mods()
    try:
        xano.search_pe("tok", "Florist", states=["Texas"])
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_keyword_stems():
    _, xano = _mods()
    assert xano._stems("pianist") == ["pian"]
    assert xano._stems("string quartet for ceremony") == ["string", "quartet"]
    assert xano._stems("harpist") == ["harp"] and xano._stems("violinist") == ["violin"]
    assert xano._stems("") == []


def test_keyword_ranks_matches_first_and_counts_them(monkeypatch):
    _, xano = _mods()
    calls = []
    monkeypatch.setattr(xano, "_get", _fake_get(calls, {"Entertainment": FX["ep119_entertainment"]}))
    r = xano.search_pe("tok", "Entertainment", states=["New York"], types=["Solo Performer"], keyword="pianist")
    assert calls[0][1]["Search_Input"] == ""                # never sent: ep119's search misses packages
    assert calls[0][1]["page_size"] == xano.PE_FETCH
    assert r["keyword_matches"] == 1 and r["vendors"][0]["name"] == "Keys & Co"
    assert r["vendors"][0]["keyword_match"] is True and r["vendors"][1]["keyword_match"] is False
    assert len(r["vendors"]) == 3                           # non-matches stay as closest alternatives
    r = xano.search_pe("tok", "Entertainment", states=["New York"], keyword="string quartet")
    top = r["vendors"][0]
    assert top["name"] == "Uptown Strings" and top["packages"][0]["name"].startswith("String Quartet")


def test_keyword_fetches_a_second_page_when_few_hits(monkeypatch):
    _, xano = _mods()
    calls = []
    page = copy.deepcopy(FX["ep119_entertainment"])
    page["nextPage"] = 2
    monkeypatch.setattr(xano, "_get", _fake_get(calls, {"Entertainment": page}))
    xano.search_pe("tok", "Entertainment", states=["New York"], keyword="harp")
    assert [c[1]["page"] for c in calls] == [1, 2]


def test_multi_state_interleaves(monkeypatch):
    _, xano = _mods()
    calls = []
    monkeypatch.setattr(xano, "_get", _fake_get(calls, {"Photography": FX["ep119_photography"]}))
    r = xano.search_pe("tok", "Photography", states=["California", "Texas"], page_size=6)
    assert sorted(c[1]["State_Input"] for c in calls) == ["California", "Texas"]
    assert set(r["matches_by_state"]) == {"California", "Texas"}
    assert len({v["vendor_id"] for v in r["vendors"]}) == len(r["vendors"])     # de-duped across states


def test_search_venues_refactor_still_interleaves(monkeypatch):
    _, xano = _mods()
    def one(token, *, states, page_size=8, **f):
        s = states[0]
        return {"total_matches": 5, "venues": [{"vendor_id": "%s%d" % (s[:2], i)} for i in range(page_size)]}
    monkeypatch.setattr(xano, "_search_one", one)
    r = xano.search_venues("t", states=["Texas", "Ohio"], page_size=6)
    assert [v["vendor_id"] for v in r["venues"]][:4] == ["Te0", "Oh0", "Te1", "Oh1"]
    assert r["total_matches"] == 10 and r["matches_by_state"] == {"Texas": 5, "Ohio": 5}
    assert "keyword_matches" not in r


# ---------------------------------------------------------------- xano.pe_benchmarks

def test_pe_benchmarks_parsing(monkeypatch):
    _, xano = _mods()
    calls = []
    monkeypatch.setattr(xano, "_get", _fake_get(calls, {}))
    b = xano.pe_benchmarks("Photography", "California")
    assert calls[0][0].endswith("/pe_benchmarks") and calls[0][1]["category"] == "Photography"
    assert b["available"] and b["state"] == "California"
    assert b["headline"]["bucket"] == "Wedding Day" and b["headline"]["median"] == 4990 and b["headline"]["basis"] == "wedding_day"
    assert b["by_coverage_hours"][0]["bucket"] == "7-10 Hours"
    assert xano.pe_bucket_for(b, "Elopement")["median"] == 2213
    assert xano.pe_bucket_for(b, "Other")["bucket"] == "Wedding Day"          # no such row -> headline
    e = xano.pe_benchmarks("Entertainment", "New York")
    assert xano.pe_bucket_for(e, "DJ")["p75"] == 3500
    assert xano.pe_benchmarks("Entertainment", "Vermont") == {"category": "Entertainment", "state": "Vermont",
                                                               "available": False}


# ---------------------------------------------------------------- agent: LIGHT stripping + cards

def _vendors(monkeypatch, cat, **kw):
    _, xano = _mods()
    key = "ep119_photography" if cat == "Photography" else "ep119_entertainment"
    monkeypatch.setattr(xano, "_get", _fake_get([], {cat: FX[key]}))
    st = "California" if cat == "Photography" else "New York"
    return xano.search_pe("tok", cat, states=[st], **kw)["vendors"], xano.pe_benchmarks(cat, st)


def test_light_row_has_no_figures(monkeypatch):
    agent, _ = _mods()
    for cat in ("Photography", "Entertainment"):
        vendors, bench = _vendors(monkeypatch, cat)
        for v in vendors:
            row = agent._pe_row(v, bench, light=True)
            blob = json.dumps(row)
            assert not MONEY.search(blob), blob
            assert "price_range" not in row and all("price" not in p for p in row.get("top_packages", []))
    vendors, bench = _vendors(monkeypatch, "Photography")
    row = agent._pe_row(vendors[0], bench, light=True)
    assert row["top_packages"][1]["name"] == "Signature Collection"           # "$3,800" cut from the name
    assert "Collections start at" not in row["blurb"] and "online gallery" in row["blurb"]
    assert row["starting_price_vs_state"] == "below typical for Elopement"    # $1,200 < Elopement p25 $1,500
    assert row["top_packages"][1]["vs_state"] == "typical for Wedding Day"


def test_full_row_keeps_prices_and_compares(monkeypatch):
    agent, _ = _mods()
    vendors, bench = _vendors(monkeypatch, "Entertainment")
    dj = next(v for v in vendors if v["name"] == "Spin City DJs")
    row = agent._pe_row(dj, bench, light=False)
    assert row["price_range"] == [1800, 3200] and row["top_packages"][0]["price"] == 1800
    assert row["starting_price_vs_state"] == "below typical for DJ"           # $1,800 < DJ p25 $2,100
    assert len(row["top_packages"]) <= agent.PE_TOP_PACKAGES


def test_cards_full_vs_light(monkeypatch):
    agent, _ = _mods()
    vendors, _ = _vendors(monkeypatch, "Photography")
    v = dict(vendors[0], venue_type=agent._pe_type_label(vendors[0]))
    full = agent._pe_card(v, light=False)
    assert full["price_label"] == "From $1,200" and full["price_from"] == 1200 and full["price_to"] == 5200
    assert full["category"] == "Photography" and full["packages_label"] == "3 packages"
    assert full["hours_label"] == "4-10 hours" and full["venue_type"] == "Photography - Elopement, Wedding Day"
    assert "venue_fee_range" not in full                                      # venue card labels it "rental"
    light = agent._pe_card(v, light=True)
    assert not any(k in light for k in ("price_from", "price_to", "price_label", "venue_fee_range"))
    assert not MONEY.search(json.dumps(light))
    unpriced = agent._pe_card(dict(vendors[1], venue_type="Photography"), light=False)
    assert "price_label" not in unpriced and "packages_label" not in unpriced


def test_no_figures_keeps_plain_sentences():
    agent, _ = _mods()
    t = "Pricing starts at $15,500. Bands are 8 pieces. A 10% fee applies. Lighting minimum 5k dollars. Ceremony keys included."
    assert agent._no_figures(t) == "Bands are 8 pieces. Ceremony keys included."


# ---------------------------------------------------------------- agent.run end to end (fake model)

class _Msg:
    def __init__(self):
        self.content, self.model = [], "fake"
        self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)


def _fake_client(script):
    """A stand-in tool runner: calls the named tools with the given args, then yields one message."""
    class Runner:
        def __init__(self, tools, **kw):
            self.tools = {(getattr(t, "name", None) or t.__name__): t for t in tools}
        def __iter__(self):
            out = {}
            for name, args in script:
                if callable(args):
                    args = args(out)
                out[name] = self.tools[name](**args)
            yield _Msg()
    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    return Client


def _run(monkeypatch, detail, script, category="Entertainment", state="New York"):
    agent, xano = _mods()
    key = "ep119_photography" if category == "Photography" else "ep119_entertainment"
    monkeypatch.setattr(xano, "_get", _fake_get([], {category: FX[key]}))
    monkeypatch.setattr(xano, "market_benchmarks", lambda s: {"state": s, "metrics": {}})
    monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(script), raising=False)
    res, _ = agent.run("tok", {"id": 0}, True, [{"role": "user", "content": "DJ please"}],
                       profile_override={"Wedding_Location_Updated": [state]}, detail=detail)
    return res, None


def test_run_light_cards_and_rule1(monkeypatch):
    seen_out = {}
    def present(out):
        seen_out["search"] = json.loads(out["search_photographers_entertainment"])
        return {"message": "Two good fits.", "vendor_ids": ["V9102", "V9101", "V0000"], "reasons": ["a", "b", "c"],
                "chips": ["DJs with a photo booth"]}
    script = [("search_photographers_entertainment", {"category": "Entertainment", "states": ["New York"]}),
              ("present_recommendations", present)]
    res, _ = _run(monkeypatch, "light", script)
    assert [c["vendor_id"] for c in res["cards"]] == ["V9102", "V9101"]
    assert res["dropped_unverified_ids"] == ["V0000"]                         # never returned by a tool
    assert not MONEY.search(json.dumps(res["cards"])) and not MONEY.search(json.dumps(seen_out["search"]))
    assert "market" not in seen_out["search"]
    assert res["cards"][0]["category"] == "Entertainment" and res["cards"][0]["venue_type"] == "DJ"


def test_run_full_cards_carry_price_and_market(monkeypatch):
    seen_out = {}
    def present(out):
        seen_out["search"] = json.loads(out["search_photographers_entertainment"])
        return {"message": "m", "vendor_ids": ["V9101"], "reasons": ["r"], "chips": ["c"]}
    script = [("search_photographers_entertainment", {"category": "Entertainment", "states": ["New York"],
                                                      "types": ["Ensemble"], "keyword": "string quartet"}),
              ("present_recommendations", present)]
    res, _ = _run(monkeypatch, "full", script)
    # keyword "string quartet": the card's price is the quartet package ($2,400), not the $450 solo violin
    assert res["cards"][0]["price_label"] == "$2,400" and res["cards"][0]["price_from"] == 2400
    assert seen_out["search"]["vendors"][0]["card_price"] == "$2,400"          # model is told what the card says
    m = seen_out["search"]["market"][0]
    assert m["bucket"] == "Ensemble" and m["median"] == 1800 and m["state"] == "New York"
    assert seen_out["search"]["keyword_matches"] == 1


def test_run_show_more_uses_results_past_the_top_eight(monkeypatch):
    agent, _ = _mods()
    big = copy.deepcopy(FX["ep119_entertainment"])
    base = big["items"][1]
    big["items"] = [dict(copy.deepcopy(base), Vendor_ID="V8%03d" % i, id=8000 + i, Name="DJ %d" % i) for i in range(14)]
    FX["ep119_big"] = big
    try:
        _, xano = _mods()
        def present(out):
            return {"message": "m", "vendor_ids": ["V8000", "V8001", "V8002"], "reasons": ["", "", ""], "chips": ["c"]}
        script = [("search_photographers_entertainment", {"category": "Entertainment", "states": ["New York"]}),
                  ("present_recommendations", present)]
        monkeypatch.setattr(xano, "_get", _fake_get([], {"Entertainment": big}))
        monkeypatch.setattr(xano, "market_benchmarks", lambda s: {"state": s, "metrics": {}})
        monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(script), raising=False)
        res, _ = agent.run("tok", {"id": 0}, True, [{"role": "user", "content": "DJ"}],
                           profile_override={"Wedding_Location_Updated": ["New York"]}, detail="light")
        more = [c["vendor_id"] for c in res["more_cards"]]
        assert more == ["V8%03d" % i for i in range(3, 12)]      # MORE_MAX 9 since 2026-10-14 (was 6)
        assert not MONEY.search(json.dumps(res["more_cards"]))
    finally:
        FX.pop("ep119_big", None)
