"""Listings-only vendor types in the assistant (owner 2026-10-26: "roll in other vendors into the planner now").
search_vendors -> xano.search_listings (ep119 / Railway with ep119 fallback, Category_Input = Flowers | Beauty |
Food and Beverage | Wedding Planner). Tulle holds NO pricing for these, so cards and model rows carry no figure at
any DETAIL level, and the prompt says so precisely. No network: ep119 responses are synthetic."""
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

MONEY = re.compile(r"\$\s?\d|\d\s?%")
PRICE_KEYS = {"price_from", "price_to", "price_label", "price_range", "venue_fee_range", "food_minimum",
              "estimate_label", "card_price", "market"}


def _mods():
    import agent
    import railway_search
    import xano
    return agent, xano, railway_search


def _item(i, name, state="New York", desc="", beauty="", glance_desc=""):
    return {"id": 500 + i, "Vendor_ID": "V%d" % (500 + i), "Name": name, "State": state, "Category": "Flowers",
            "Address": "1 Main St, Brooklyn, NY", "Description": desc, "Type_of_Beauty": beauty,
            "image_1": {"url": "https://x/%d.jpg" % i},
            "flt_glance": {"pdf_description": glance_desc, "pricing_row_count": 0}}


EP119_FLOWERS = {"itemsTotal": 41, "nextPage": None, "items": [
    _item(1, "Stems Studio", state="New York, New Jersey",
          desc="Garden-style florals. Centerpieces start at $85 each. 10% off weekdays."),
    _item(2, "Wild Rose Floral", desc="Lush garden roses and installations for lofts."),
    _item(3, "Petal & Moss", glance_desc="Minimal, sculptural arrangements."),
]}


def _fake_get(calls, data):
    def get(url, token=None, params=None):
        calls.append((url, dict(params or {})))
        if url.endswith("/wptp_updated_mappings_search"):
            return copy.deepcopy(data)
        return {}
    return get


# ---------------------------------------------------------------- category words -> category

def test_resolve_category_maps_couples_words():
    agent, _, _ = _mods()
    cases = {"Flowers": "Flowers", "florist": "Flowers", "economical flowers": "Flowers", "bouquets": "Flowers",
             "makeup artist": "Beauty", "hair and make-up": "Beauty", "Beauty": "Beauty", "HMU": "Beauty",
             "catering": "Food and Beverage", "Food and Beverage": "Food and Beverage", "caterer": "Food and Beverage",
             "wedding planner nyc": "Wedding Planner", "day-of coordinator": "Wedding Planner",
             "photographer": "Photography", "videographer": "Photography", "DJ": "Entertainment",
             "photo booth": "Entertainment", "string quartet": "Entertainment",
             "tent services": None, "hotels": None, "cake": None, "officiant": None, "dress": None}
    for words, want in cases.items():
        assert agent.resolve_category(words) == want, words


# ---------------------------------------------------------------- xano.search_listings

def test_listing_params_parsing_and_state(monkeypatch):
    _, xano, _ = _mods()
    calls = []
    monkeypatch.setattr(xano, "_get", _fake_get(calls, EP119_FLOWERS))
    r = xano.search_listings("tok", "Flowers", states=["New York"], page_size=8)
    url, p = calls[0]
    assert url.endswith("/wptp_updated_mappings_search")
    assert p["Category_Input"] == "Flowers" and p["states[]"] == ["New York"] and p["State_Input"] == "New York"
    assert p["base_fee_max"] == 0 and p["fb_per_person_max"] == 0 and p["max_capacity"] == 0
    assert p["Search_Input"] == "" and p["fallback_all"] == "false"
    assert not any(k in p for k in ("venue_types[]", "vibes[]", "pricing_models[]"))
    assert r["total_matches"] == 41
    v = r["vendors"][0]
    assert v["vendor_id"] == "V501" and v["vendor_idx"] == 501 and v["category"] == "Flowers"
    assert v["state"] == "New York"                       # the searched state, not "New York, New Jersey"
    assert r["vendors"][2]["description"] == "Minimal, sculptural arrangements."   # PDF description preferred


def test_listing_keyword_ranks_not_filters(monkeypatch):
    _, xano, _ = _mods()
    calls = []
    monkeypatch.setattr(xano, "_get", _fake_get(calls, EP119_FLOWERS))
    r = xano.search_listings("tok", "Flowers", states=["New York"], keyword="garden roses", page_size=8)
    assert calls[0][1]["Search_Input"] == "" and calls[0][1]["page_size"] == xano.LISTING_FETCH
    assert [v["name"] for v in r["vendors"]][:2] == ["Stems Studio", "Wild Rose Floral"]
    assert r["keyword_matches"] == 2 and len(r["vendors"]) == 3      # the non-match stays as an alternative


def test_listing_bad_category_refused():
    _, xano, _ = _mods()
    for bad in ("Venue", "Photography", "Rentals", ""):
        try:
            xano.search_listings("tok", bad, states=["Texas"])
        except ValueError:
            continue
        raise AssertionError("accepted %r" % bad)


def test_listing_multi_state_interleaves(monkeypatch):
    _, xano, _ = _mods()
    calls = []
    monkeypatch.setattr(xano, "_get", _fake_get(calls, EP119_FLOWERS))
    r = xano.search_listings("tok", "Beauty", states=["New York", "New Jersey"], page_size=6)
    assert sorted(c[1]["State_Input"] for c in calls) == ["New Jersey", "New York"]
    assert all(c[1]["Category_Input"] == "Beauty" for c in calls)
    assert r["matches_by_state"] == {"New York": 41, "New Jersey": 41}


def test_listing_railway_arm_then_ep119_fallback(monkeypatch):
    _, xano, rs = _mods()
    calls = []
    monkeypatch.setattr(xano, "_get", _fake_get(calls, EP119_FLOWERS))
    sent = []
    monkeypatch.setattr(rs, "railway_get", lambda tok, p: (sent.append(dict(p)) or (copy.deepcopy(EP119_FLOWERS), None)))
    log = rs.SearchLog(rs.RAILWAY)
    r = xano.search_listings("tok", "Wedding Planner", states=["New York"], search_log=log)
    assert sent and sent[0]["Category_Input"] == "Wedding Planner" and not calls      # Railway answered
    assert r["total_matches"] == 41 and log.summary()["search_engine"] == "railway"
    monkeypatch.setattr(rs, "railway_get", lambda tok, p: (None, "timeout"))
    log = rs.SearchLog(rs.RAILWAY)
    xano.search_listings("tok", "Wedding Planner", states=["New York"], search_log=log)
    assert len(calls) == 1 and calls[0][1]["Category_Input"] == "Wedding Planner"     # ep119 safety net
    assert log.summary()["search_fallback"] == "timeout"
    log = rs.SearchLog(rs.EP119)                                                        # ep119 arm: Railway untouched
    monkeypatch.setattr(rs, "railway_get", lambda tok, p: (_ for _ in ()).throw(AssertionError("called")))
    xano.search_listings("tok", "Flowers", states=["New York"], search_log=log)


def test_vendor_card_for_listing_row(monkeypatch):
    _, xano, _ = _mods()
    row = dict(_item(9, "Stems Studio", desc="Bouquets from $150."), Category="Flowers",
               flt_min_venue_fee=0, flt_max_venue_fee=0)
    monkeypatch.setattr(xano, "_search_all", lambda table, search: [row])
    c = xano.vendor_card("V509")
    assert c["category"] == "Flowers" and c["vendor_id"] == "V509" and "venue_fee_range" not in c


# ---------------------------------------------------------------- cards and model rows carry no figure

def test_listing_card_and_row_have_no_figures():
    agent, xano, _ = _mods()
    v = xano._listing_item(EP119_FLOWERS["items"][0], "Flowers", "New York", [])
    card, row = agent._listing_card(v), agent._listing_row(v)
    assert not PRICE_KEYS & set(card) and not PRICE_KEYS & set(row)
    assert not MONEY.search(json.dumps(card)) and not MONEY.search(json.dumps(row))
    assert card["description"] == "Garden-style florals."            # money sentences dropped, the rest kept
    assert card["venue_type"] == "Florist" and row["kind"] == "Florist"
    b = agent._listing_card(dict(v, category="Beauty", subtype="Airbrush"))
    assert b["venue_type"] == "Hair & makeup - Airbrush"
    assert agent._listing_say("Flowers", 41, ["New York"]) == \
        "We list 41 florists in New York; pricing for florists is still being added."
    assert agent._listing_say("Beauty", 3, ["International"]).startswith("We list 3 hair and makeup artists outside")


# ---------------------------------------------------------------- prompt scope

def test_prompt_scope_listings_on_and_off():
    agent, _, _ = _mods()
    on, off = agent.system_prompt(True), agent.system_prompt(False)
    assert off == agent.SYSTEM
    for old, new in agent.LISTINGS_PROMPT_EDITS:
        assert agent.SYSTEM.count(old) == 1 and new in on
    assert "search_vendors" in on and "search_vendors" not in off
    assert "pricing for florists is still being added" in on and "say_this" in on
    scope = on.split("SCOPE - ")[1].split("YOUR ONE JOB")[0]
    unsupported = scope.split("- Anything else")[1]
    for t in ("florist", "hair and makeup", "planner", "catering"):
        assert t not in unsupported                                   # no longer told to refuse these
    for t in ("rentals", "attire", "cakes", "stationery", "transportation", "hotel room blocks", "Officiants"):
        assert t in unsupported
    never = on.split("Never offer a chip about something none of these covers")[1].split("\n")[0]
    assert "florists" not in never and "rentals" in never and "officiants" in never
    assert "never a price, budget, cheaper or package chip" in on


def test_prompt_edits_fail_loudly_when_system_drifts(monkeypatch):
    agent, _, _ = _mods()
    monkeypatch.setattr(agent, "SYSTEM", agent.SYSTEM.replace("SCOPE - three vendor types:", "SCOPE:"))
    try:
        agent.system_prompt(True)
    except RuntimeError:
        return
    raise AssertionError("drift not detected")


# ---------------------------------------------------------------- agent.run end to end (fake model)

class _Msg:
    def __init__(self):
        self.content, self.model = [], "fake"
        self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                           cache_creation_input_tokens=0)


def _fake_client(script, captured):
    class Runner:
        def __init__(self, tools, **kw):
            captured["system"] = kw.get("system")
            self.tools = {(getattr(t, "name", None) or t.__name__): t for t in tools}
            captured["tools"] = list(self.tools)
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


def _run(monkeypatch, detail, script, listings=True):
    agent, xano, _ = _mods()
    import config
    calls, captured = [], {}
    monkeypatch.setattr(config, "LISTINGS_IN_CHAT", listings)
    monkeypatch.setattr(xano, "_get", _fake_get(calls, EP119_FLOWERS))
    monkeypatch.setattr(xano, "market_benchmarks", lambda s: {"state": s, "metrics": {}})
    monkeypatch.setattr(xano, "venue_pricing", lambda *a, **k: (_ for _ in ()).throw(AssertionError("priced")))
    monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(script, captured), raising=False)
    res, _ = agent.run("tok", {"id": 0}, detail == "full", [{"role": "user", "content": "economical flowers"}],
                       profile_override={"Wedding_Location_Updated": ["New York"]}, detail=detail)
    return res, calls, captured


def _script(seen_out, category="florist"):
    def present(out):
        seen_out["search"] = json.loads(out["search_vendors"])
        return {"message": "Three florists to look at.", "vendor_ids": ["V501", "V502", "V999"],
                "reasons": ["a", "b", "c"], "chips": ["Florists in New Jersey"]}
    return [("search_vendors", {"category": category, "states": ["New York"]}), ("present_recommendations", present)]


def test_run_listing_cards_at_every_detail_level(monkeypatch):
    for detail in ("full", "light", "free"):
        seen_out = {}
        res, calls, captured = _run(monkeypatch, detail, _script(seen_out))
        assert "search_vendors" in captured["tools"], detail
        assert calls[0][1]["Category_Input"] == "Flowers"
        assert [c["vendor_id"] for c in res["cards"]] == ["V501", "V502"], detail
        assert res["dropped_unverified_ids"] == ["V999"]                       # never returned by a tool
        blob = json.dumps(res["cards"]) + json.dumps(seen_out["search"])
        assert not MONEY.search(blob), detail
        assert not any(PRICE_KEYS & set(c) for c in res["cards"])
        s = seen_out["search"]
        assert s["pricing_held"] is False and s["total_matches"] == 41
        assert s["say_this"] == "We list 41 florists in New York; pricing for florists is still being added."
        assert res["cards"][0]["venue_type"] == "Florist"
        assert "search_vendors" in captured["system"][0]["text"]


def test_run_unsupported_and_pe_redirect(monkeypatch):
    out = {}
    def present(o):
        out.update({k: json.loads(v) for k, v in o.items()})
        return {"message": "m", "vendor_ids": [], "reasons": [], "chips": []}
    script = [("search_vendors", {"category": "tent services", "states": ["New York"]}),
              ("search_vendors", {"category": "photographers", "states": ["New York"]}),
              ("present_recommendations", present)]
    res, calls, _ = _run(monkeypatch, "light", script)
    assert not calls                                                    # neither one searched
    assert res["cards"] == []
    # the runner keeps the last result per tool name: the redirect
    assert out["search_vendors"]["error"] == "use search_photographers_entertainment"


def test_run_unsupported_hotel_note(monkeypatch):
    out = {}
    def present(o):
        out.update(json.loads(o["search_vendors"]))
        return {"message": "m", "vendor_ids": [], "reasons": [], "chips": []}
    res, calls, _ = _run(monkeypatch, "full", [("search_vendors", {"category": "hotels", "states": ["Illinois"]}),
                                               ("present_recommendations", present)])
    assert out["supported"] is False and "Hotel / Resort" in out["say_this"] and not calls


def test_run_flag_off_no_tool_and_main_prompt(monkeypatch):
    agent, _, _ = _mods()
    script = [("present_recommendations", {"message": "m", "vendor_ids": [], "reasons": [], "chips": []})]
    _, _, captured = _run(monkeypatch, "light", script, listings=False)
    assert "search_vendors" not in captured["tools"]
    assert captured["system"][0]["text"].startswith(agent.SYSTEM)
    assert "search_vendors" not in captured["system"][0]["text"]
