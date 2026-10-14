import json, os, pathlib, sys, types
os.environ.setdefault("REC_FOREVER_ONLY", "0")   # config reads it at import; test_guards needs the free-tier rules
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)
import agent  # noqa: E402
import estimate  # noqa: E402
import xano  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
# Live ep230 payloads saved 2026-10-13 (venues from user 1437's chats + the assistant's later picks).
VENUES = ["V1040", "V3913", "V3358", "V2574", "V84", "V2010", "V1201"]


def panel(vid):
    return json.loads((FIX / ("pi_%s.json" % vid)).read_text())


def test_matches_the_venue_page_total_on_every_live_venue():
    """The page's own all_in.at_default is the ground truth: same guests -> same total, to the dollar."""
    for vid in VENUES:
        d = panel(vid)
        want = d["space"]["all_in"]["at_default"]
        got = estimate.compute(estimate.inputs_from_panel(d), d["default_guests"])
        assert got["guests"] == want["guests"], vid
        assert got["total_before_tax_and_gratuity"] == want["total"], vid
        assert got["per_guest"] == want["per_guest"], vid


def test_wadsworth_at_160_guests():
    # 7,000 rental (PDF) + 750 ceremony (est) + 146 x 160 = 23,360 food (est) = 31,110; +20% service (est)
    e = estimate.compute(estimate.inputs_from_panel(panel("V3913")), 160)
    assert e["total_before_tax_and_gratuity"] == 37332
    assert e["per_guest"] == 233
    assert e["estimated_lines"] == ["Ceremony on site", "Food and drink", "Service charge"]
    assert e["tax"] == {"source": "not stated by this venue - added on top"}


def test_food_minimum_applies_below_breakeven():
    # Saint Clements: $160 a head but a $27,500 minimum -> at 160 guests (25,600) the minimum is what you pay
    e = estimate.compute(estimate.inputs_from_panel(panel("V2574")), 160)
    food = [l for l in e["lines"] if l["item"] == "Food and drink"][0]
    assert food["amount"] == 27500 and food.get("minimum_applies")
    assert e["tax"]["pct"] == 7.35 and e["total_with_tax"] > e["total_before_tax_and_gratuity"]


def test_guest_floor_is_priced_and_said():
    d = panel("V1040")          # Farmington Gardens: 150-guest minimum
    e = estimate.compute(estimate.inputs_from_panel(d), 120)
    assert e["guests"] == 150 and "150 guests" in e["priced_at_guest_minimum"]


def test_off_peak_override_uses_the_matching_space():
    inp = estimate.inputs_from_panel(panel("V3358"))
    details = {"spaces": [{"space": "Other room", "Venue_Fee_on_Lowest_Saturday": 1},
                          {"space": inp["space"], "Venue_Fee_on_Lowest_Saturday": 2500,
                           "Per_Person_Food_and_Beverage_on_Lowest_Saturday": 99,
                           "Months__Lowest_Pricing": "Jan-Mar"}]}
    o = estimate.off_peak_override(details, inp["space"])
    assert o["rental"] == 2500 and o["pp_fb"] == 99 and o["months"] == "Jan-Mar"
    peak = estimate.compute(inp, 160)["total_before_tax_and_gratuity"]
    off = estimate.compute(inp, 160, o)["total_before_tax_and_gratuity"]
    assert off < peak
    assert estimate.off_peak_override({"spaces": [{"space": "x"}, {"space": "y"}]}, "z") is None


def test_no_inputs_no_estimate():
    assert estimate.compute(None, 100) is None
    assert estimate.inputs_from_panel({"space": {"components": [], "all_in": {}}}) is None
    assert estimate.label(None) is None


def test_label_with_budget_share():
    e = estimate.compute(estimate.inputs_from_panel(panel("V3913")), 160)
    assert estimate.label(e, 60000) == "About $37,332 for 160 guests before tax and gratuity (62% of your budget)"


# ---------------------------------------------------------------- what the model sees

def _pricing(vid, paid=True):
    d = panel(vid)
    sp = d["space"]
    rows = [{"line": c.get("title"), "detail": c.get("sentence"), "source": c.get("source"),
             "vs_market": c.get("badge_label"), "amount": c.get("amount"), "pct": c.get("pct")}
            for c in sp["components"] if c.get("key") != "total"]
    return {"vendor_id": vid, "guest_minimum": sp.get("guest_floor"), "verdict": sp["band"].get("sentence"),
            "lines": rows, "cost_inputs": estimate.inputs_from_panel(d) if paid else None}


def test_market_sentences_never_reach_the_model():
    """The per-venue-band "Comparable Connecticut venues charge about $146 a head" is what contradicted
    Farmington's "$205" in 1437's chat. Lines the PDF doesn't give carry no figure at all now."""
    for vid in ("V3913", "V1040"):
        c = agent._compact_pricing(_pricing(vid), 160, 60000)
        blob = json.dumps(c["lines"])
        assert "146" not in blob and "205" not in blob, vid
        assert "{guests}" not in blob
        assert c["estimate"]["guests"] in (160, 150)


def test_guests_placeholder_filled():
    c = agent._compact_pricing(_pricing("V3358"), 160)
    food = [l for l in c["lines"] if l["line"] == "Food and drink"][0]
    assert food["detail"] == "At 160 guests that is $127 a head."


def test_card_rental_not_disclosed_is_not_zero():
    card = agent._with_rental({"vendor_id": "V1040", "venue_fee_range": [0, 0]}, _pricing("V1040"), 160, 60000)
    assert card["venue_fee_range"] == [None, None]
    assert card["estimate_label"].startswith("About $")
    assert "estimate_label" not in agent._light_card(card)


def test_light_gets_no_estimate():
    card = agent._with_rental({"vendor_id": "V3913"}, _pricing("V3913", paid=False), 160, 60000)
    assert "estimate_label" not in card
    assert "estimate" not in agent._compact_pricing(_pricing("V3913", paid=False), 160)


# ---------------------------------------------------------------- the figure check

def test_figure_check_catches_the_1437_contradiction():
    allowed = agent.numbers_in(json.dumps({"pp_fb": {"median": 146, "scope": "Connecticut venues that seat 151-250"}}))
    assert agent.unsupported_figures("about $205 a head", allowed) == ["$205"]
    assert agent.unsupported_figures("about $146 a head", allowed) == []


def test_figure_check_tolerates_rounding_but_not_drift():
    allowed = {37332.0, 7000.0}
    assert agent.unsupported_figures("about $37,300, or $37K, with a $7,000 rental", allowed) == []
    assert agent.unsupported_figures("about $39,900", allowed) == ["$39,900"]
    assert agent.unsupported_figures("a $15 fee", allowed) == []          # under MIN_CHECKED


def test_derived_budget_shares_and_savings():
    d = agent.derived_numbers(160, 60000, totals=[37332, 21000])
    assert agent.unsupported_figures("$24,000-$30,000, about $150-$190 a guest, saves $16,332", d) == []


# ---------------------------------------------------------------- benchmarks for the couple's size

def test_capacity_band_and_label():
    assert xano.capacity_band(160)[0] == "cap_151_250"
    assert xano.capacity_band(0) is None
    assert xano.band_label("Connecticut", xano.capacity_band(160)) == "Connecticut venues that seat 151-250"
    assert xano.band_label("Connecticut", xano.capacity_band(40)) == "Connecticut venues that seat up to 75"
    assert xano.band_label("Connecticut", xano.capacity_band(500)) == "Connecticut venues that seat 401 or more"


def test_market_benchmarks_use_the_couples_band(monkeypatch):
    rows = [  # live table 63 values, 2026-10-13
        {"State": "Connecticut", "Dimension": "overall", "Bucket": "all", "Metric": "pp_fb", "median": 159, "n_spaces": 112},
        {"State": "Connecticut", "Dimension": "capacity_band", "Bucket": "cap_151_250", "Metric": "pp_fb", "median": 146, "n_spaces": 25},
        {"State": "Connecticut", "Dimension": "capacity_band", "Bucket": "cap_251_400", "Metric": "pp_fb", "median": 205, "n_spaces": 10},
        {"State": "Connecticut", "Dimension": "overall", "Bucket": "all", "Metric": "fb_min", "median": 14500, "n_spaces": 42},
        {"State": "Connecticut", "Dimension": "capacity_band", "Bucket": "cap_151_250", "Metric": "fb_min", "median": 200, "n_spaces": 7},
    ]
    monkeypatch.setattr(xano, "_search_all", lambda t, s: rows)
    m = xano.market_benchmarks("Connecticut", 160)["metrics"]
    assert m["pp_fb"]["median"] == 146 and m["pp_fb"]["scope"] == "Connecticut venues that seat 151-250"
    assert m["fb_min"]["median"] == 14500 and m["fb_min"]["scope"] == "all Connecticut venues"   # band too thin
    m0 = xano.market_benchmarks("Connecticut")["metrics"]
    assert m0["pp_fb"]["median"] == 159


# ---------------------------------------------------------------- "how much should we budget?" in code

def _bench(p200, p125, p75, n=27, scope="Connecticut venues that seat 151-250"):
    pts = {200: p200, 125: p125, 75: p75}
    return {"metrics": {"all_in_pg_%d" % k: {"p25": v[0], "median": v[1], "p75": v[2], "n_spaces": n, "scope": scope}
                        for k, v in pts.items()}}


def test_budget_check_matches_the_1437_profile():
    """Live table 63 (2026-10-13). Replay run 2 called $180-210 x 160 'well above' $60k's 40-50% guide; CT's
    typical venue at 160 guests is 49% of $60k - within it. PA is a stretch."""
    ct = agent.budget_check(_bench((165.92, 181.2, 228), (165.92, 183.92, 259.2), (183.33, 219.6, 272)), 160, 60000)
    assert ct["per_guest"] == 183 and ct["typical_venue_and_food_total"] == 29200
    assert ct["share_of_their_budget_pct"] == 49 and ct["verdict"] == "within the 40-50% guideline"
    assert ct["guideline_for_their_budget"] == [24000, 30000]
    assert ct["whole_wedding_budget_for_a_typical_venue"] == [58400, 73000]
    pa = agent.budget_check(_bench((136, 209.31, 254.1), (158.36, 227, 295), (183.33, 263.93, 433.33)), 160, 60000)
    assert pa["typical_venue_and_food_total"] == 35000 and pa["verdict"].startswith("a little above")


def test_budget_check_edges():
    b = _bench((100, 100, 100), (120, 120, 120), (150, 150, 150))
    assert agent.budget_check(b, 0, 60000) is None
    assert agent.budget_check(b, 40, 0)["per_guest"] == 150          # flat below 75
    assert agent.budget_check(b, 300, 0)["per_guest"] == 100         # flat above 200
    assert "verdict" not in agent.budget_check(b, 300, 0)
    assert agent.budget_check({"metrics": {}}, 160, 60000) is None
