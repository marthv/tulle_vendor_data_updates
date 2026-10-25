"""Cost tools in the chat (owner decision 2026-10-25). The estimate and split must give exactly what the WeWeb Cost
tools tab gives (workflow 8a4f6c12 + tier texts; list 75949ea5 + row texts) - expected values below were computed
by hand from that JS and cross-checked by running the same JS in node. No network, no model."""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import cost_tools  # noqa: E402

# Live values read 2026-10-25 (GET /admin_location_adjustment, /number_of_guests with the test account's token).
STATE_ROWS = [{"State": "New York", "Cost_of_Average_Wedding": 46863}, {"State": "Texas", "Cost_of_Average_Wedding": 24063},
              {"State": "California", "Cost_of_Average_Wedding": 38369}, {"State": "Not Sure", "Cost_of_Average_Wedding": 0}]
MODIFIERS = {5: 0.35, 15: 0.4, 80: 0.86, 85: 0.92, 150: 1.3, 295: 2.44, 300: 2.5}
asked = []


def mod_at(n):
    asked.append(n)
    return MODIFIERS.get(n)


def est(state, guests, major, peak, budget):
    return cost_tools.estimate_cost(state, guests, major, peak, budget, state_rows=STATE_ROWS, modifier_at=mod_at)


def tiers(w):
    return [t["amount"] for t in w["tiers"]]


# ---------------------------------------------------------------- a. estimate parity

def test_ny_150_guests_major_city_peak():
    # 46863 x 1.3 x 1.2 x 1.1 x 1.3 = 104,541.98 -> 31,363 / 104,542 / 177,721; $45,000 is 57% under.
    w = est("New York", 150, True, True, 45000)["widget"]
    assert w["guest_lookup"] == 150
    assert tiers(w) == [31363, 104542, 177721]
    assert (w["verdict"], w["gap_pct"]) == ("under", 57)
    assert [t["label"] for t in w["tiers"]] == ["Minimalist", "Average", "Luxury"]


def test_tx_82_guests_rounds_to_80():
    # 82 -> 80 (modifier 0.86): 24063 x 0.86 x 1.1 = 22,763.60 -> 6,829 / 22,764 / 38,698; $30,000 is 32% over.
    w = est("Texas", 82, False, False, 30000)["widget"]
    assert w["guest_lookup"] == 80
    assert tiers(w) == [6829, 22764, 38698]
    assert (w["verdict"], w["gap_pct"]) == ("over", 32)


def test_350_guests_extrapolates_past_300():
    # 350 -> table row 300 (2.5) + 10 steps x 0.06 = 3.10: 38369 x 3.1 x 1.1 = 130,838.29.
    out = est("California", 350, False, False, 0)
    w = out["widget"]
    assert w["guest_lookup"] == 300 and w["note"] == "Above 300 guests this is a rough extrapolation."
    assert tiers(w) == [39251, 130838, 222425]
    assert w["verdict"] is None and out["facts"]["average"] == 130838


def test_rounding_is_javascript_math_round():
    # Math.round(12.5) = 13 -> 13/5 = 2.6 -> 15 (Python's round would give 12 -> 10).
    asked.clear()
    est("Texas", 12.5, False, False, 0)
    assert asked == [15]
    assert cost_tools.guest_lookup(83) == (85, 85) and cost_tools.guest_lookup(1) == (0, 5)


def test_budget_right_at_average_is_near():
    w = est("Texas", 82, False, False, 22764)["widget"]
    assert (w["verdict"], w["gap_pct"]) == ("near", 0)


def test_missing_inputs_are_named_not_guessed():
    assert est("", 100, False, False, 0)["widget"]["missing"] == ["state"]
    assert est("Texas", 0, False, False, 0)["widget"]["missing"] == ["guests"]
    assert est("Not Sure", 150, False, False, 0)["widget"]["missing"] == ["state_average"]
    w = est("Texas", 130, False, False, 0)["widget"]   # no table-3 row in this fixture
    assert w["missing"] == ["guest_modifier"] and w["tiers"] == []


# ---------------------------------------------------------------- b. split

CATS = [{"id": 1, "Category": "Venue", "Budget_Focus_Normal": 20, "Budget_Focus_Venue": 25},
        {"id": 2, "Category": "Food and Beverage", "Budget_Focus_Normal": 20, "Budget_Focus_Venue": 20},
        {"id": 6, "Category": "Flowers", "Budget_Focus_Normal": 9, "Budget_Focus_Venue": 8},
        {"id": 4, "Category": "Wedding Planner", "Budget_Focus_Normal": 10, "Budget_Focus_Venue": 10},
        {"id": 9, "Category": "Photography", "Budget_Focus_Normal": 10, "Budget_Focus_Venue": 8},
        {"id": 8, "Category": "Entertainment", "Budget_Focus_Normal": 10, "Budget_Focus_Venue": 9},
        {"id": 10, "Category": "Attire", "Budget_Focus_Normal": 7, "Budget_Focus_Venue": 7},
        {"id": 5, "Category": "Rentals", "Budget_Focus_Normal": 5, "Budget_Focus_Venue": 5},
        {"id": 11, "Category": "Additional Items", "Budget_Focus_Normal": 3, "Budget_Focus_Venue": 2},
        {"id": 12, "Category": "Transportation", "Budget_Focus_Normal": 2, "Budget_Focus_Venue": 2},
        {"id": 3, "Category": "Beauty", "Budget_Focus_Normal": 2, "Budget_Focus_Venue": 2},
        {"id": 7, "Category": "Day of Stationery", "Budget_Focus_Normal": 1, "Budget_Focus_Venue": 1},
        {"id": 13, "Category": "Invites", "Budget_Focus_Normal": 1, "Budget_Focus_Venue": 1}]


def by_name(w):
    return {c["name"]: c for c in w["categories"]}


def test_split_balanced_is_the_table_split():
    w = cost_tools.split_budget(50000, "normal", categories=CATS)["widget"]
    c = by_name(w)
    assert c["Venue"]["pct"] == 20 and c["Venue"]["amount"] == 10000 and c["Flowers"]["amount"] == 4500
    assert w["categories"][0]["name"] == "Venue"          # Budget_Focus_Normal descending, stable
    assert w["included_count"] == 13


def test_split_rescales_over_included_categories():
    # Planner (10) + Rentals (5) off -> 85 left: Venue 20/85 = 24%, $11,765; Flowers 9/85 = 11%, $5,294.
    w = cost_tools.split_budget(50000, "normal", ["Wedding Planner", 5], categories=CATS)["widget"]
    c = by_name(w)
    assert (c["Venue"]["pct"], c["Venue"]["amount"]) == (24, 11765)
    assert (c["Flowers"]["pct"], c["Flowers"]["amount"]) == (11, 5294)
    assert c["Wedding Planner"] == {"id": 4, "name": "Wedding Planner", "included": False, "pct": 0, "amount": 0}
    assert c["Rentals"]["included"] is False and w["included_count"] == 11
    assert abs(sum(x["amount"] for x in w["categories"]) - 50000) <= 11   # per-row rounding only


def test_split_focus_and_unknown_focus():
    w = cost_tools.split_budget(40000, "venue", categories=CATS)["widget"]
    assert w["focus"] == "venue" and by_name(w)["Venue"]["amount"] == 10000   # 25/100
    assert cost_tools.split_budget(40000, "glitter", categories=CATS)["widget"]["focus"] == "normal"


# ---------------------------------------------------------------- c. compare (free-mode lock)

FX = pathlib.Path(__file__).resolve().parent / "fixtures"
QUOTE = {"user_email": "a@b.co", "vendor_id": "V313", "venue_name": "Benbrook Stables", "base_venue_fee": 12500,
         "ceremony_fee": 2100, "other_fees": 2600, "per_person_cost": 255, "guest_count": 60, "admin_fee": 13,
         "total_admin_cost": 4387, "tax": 8, "total_tax": 2884, "final_total": 39772}


class FakeXano:
    PE_CATEGORIES = ("Photography", "Entertainment")

    def __init__(self):
        self.pricing_calls = []

    def venue_quotes(self, token, email):
        return [QUOTE]

    def saved_vendor_ids(self, user):
        return ["V1040", "P1"]

    def vendor_card(self, vid):
        return {"vendor_id": vid, "name": "Saved " + vid, "category": "Photography" if vid == "P1" else "Venue"}

    def venue_pricing(self, token, vid, paid):
        self.pricing_calls.append(vid)
        import estimate
        return {"cost_inputs": estimate.inputs_from_panel(json.loads((FX / ("pi_%s.json" % vid)).read_text()))}


USER = {"id": 7, "email": "a@b.co", "Wedding_Guest_Count": 120, "Wedding_Budget": 60000,
        "Wedding_Location_Updated": ["Texas"]}


def test_compare_paid_prices_saved_venues_from_the_pdf_and_adds_quotes():
    fx = FakeXano()
    w = cost_tools.run_tool("compare_venues", {}, "tok", USER, access=True, xano=fx)["widget"]
    assert fx.pricing_calls == ["V1040"]                    # the photographer is not a venue
    src = {r["source"]: r for r in w["rows"]}
    assert set(src) == {"pdf", "manual"} and w["locked"] is False
    # manual = 12,500 + 2,100 + 2,600 + 255 x 60 + 4,387 (before tax and tip; final_total is not used)
    assert src["manual"]["all_in"] == 36887 and src["manual"]["guests"] == 60
    assert src["pdf"]["guests"] >= 120 and src["pdf"]["all_in"] > 0
    assert [r["all_in"] for r in w["rows"]] == sorted(r["all_in"] for r in w["rows"])


def test_compare_free_shows_only_manual_quotes_and_never_fetches_pdf_pricing():
    import free_mode
    fx = FakeXano()
    out = cost_tools.run_tool("compare_venues", {}, "tok", USER, access=False, xano=fx)
    w = out["widget"]
    assert fx.pricing_calls == []                             # nothing to leak: never fetched
    assert w["locked"] is True and w["reason"] == "unlock_pdf_pricing"
    assert [r["source"] for r in w["rows"]] == ["manual"] and w["unpriced"] == []
    assert "pdf" not in json.dumps(out["facts"]).replace("pdf rows", "")  # facts carry no pdf row
    assert w["saved_venue_count"] == 1


def test_free_widget_guard_strips_pdf_rows_from_a_stored_widget():
    import free_mode
    paid = cost_tools.run_tool("compare_venues", {}, "tok", USER, access=True, xano=FakeXano())["widget"]
    pdf_totals = [r["all_in"] for r in paid["rows"] if r["source"] == "pdf"]
    g = free_mode.widget(paid)
    assert g["locked"] is True and all(r["source"] == "manual" for r in g["rows"])
    assert not any(str(t) in json.dumps(g) for t in pdf_totals)
    # the other widgets pass through unchanged
    s = cost_tools.split_budget(100, categories=CATS)["widget"]
    assert free_mode.widget(s) == s
    turns = free_mode.sanitize_turns([{"role": "assistant", "text": "x", "cards": [], "chips": [], "widgets": [paid]}])
    assert turns[0]["widgets"][0]["locked"] is True


# ---------------------------------------------------------------- d. budget status

def test_budget_status_drops_examples_and_unselected_rows():
    pk = [{"vendor_name": "Example 1: Venue", "total_cost": 30000, "Balance_Paid": 0, "Category": "Venue", "Selected": True},
          {"vendor_name": "example 2: Photography", "total_cost": 5000, "Balance_Paid": 0, "Category": "Photography", "Selected": True},
          {"vendor_name": "The Barn", "total_cost": 20000, "Balance_Paid": 5000, "Balance_Left": 99, "Category": "Venue", "Selected": True},
          {"vendor_name": "Petals", "total_cost": 4500, "Balance_Paid": 450, "Category": "Flowers", "Selected": True},
          {"vendor_name": "Maybe DJ", "total_cost": 3000, "Balance_Paid": 0, "Category": "Entertainment", "Selected": False}]
    w = cost_tools.budget_status({"Wedding_Budget": 50000}, packages=pk)["widget"]
    assert (w["added"], w["paid"], w["owed"]) == (24500, 5450, 19050)     # owed = total - paid, not Balance_Left
    assert (w["remaining"], w["pct_of_budget"], w["item_count"], w["examples_excluded"]) == (25500, 49, 2, 2)
    assert [b["category"] for b in w["by_category"]] == ["Venue", "Flowers"]


# ---------------------------------------------------------------- endpoint + response contract

T = "2026-10-25"


@pytest.fixture
def env(monkeypatch):
    import agent, app, config, xano
    st = {"logged": [], "added": []}
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", dict(USER, forever_access_purchased=False)))
    monkeypatch.setattr(xano, "has_paid_access", lambda u: False)
    monkeypatch.setattr(xano, "beta_member", lambda u: False)
    monkeypatch.setattr(xano, "has_forever", lambda u: False)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "log_usage", lambda row: st["logged"].append(row))
    monkeypatch.setattr(xano, "today", lambda: T)
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "user_id": uid, "title": "t"})
    monkeypatch.setattr(xano, "add_message", lambda *a: st["added"].append(a))
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "budget_categories", lambda tok: CATS)
    monkeypatch.setattr(xano, "state_average_rows", lambda tok: STATE_ROWS)
    monkeypatch.setattr(xano, "guest_modifier", lambda tok, n: MODIFIERS.get(n))
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    return st


def _drain():
    import app
    app._POOL.submit(lambda: None).result()   # usage rows are logged on the pool


def test_tool_endpoint_runs_without_a_model_and_is_not_a_question(env, monkeypatch):
    import agent, app, config, guards
    monkeypatch.setattr(agent, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call")))
    c = TestClient(app.app)
    r = c.post("/rec/tool", json={"tool": "split_budget", "args": {"total": 50000}, "chat_id": 9})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["widgets"][0]["type"] == "budget_split" and d["chat_id"] == 9 and d["text"]
    _drain()
    row = env["logged"][-1]
    assert row["kind"] == "tool_split_budget" and row["cost_usd"] == 0 and row["counted_as_free"] is False
    # the quick action and its widget are saved in the chat
    assert [a[1] for a in env["added"]] == ["user", "assistant"]
    assert env["added"][1][3] == [{"meta": "widget", "widget": d["widgets"][0]}]
    # ...and the logged rows never spend a free question or a paid prompt
    rows = [dict(r, status="ok", usage_day=T) for r in env["logged"]] * 50
    assert guards.free_used(rows) == 0
    assert guards.free_refines_left(False, rows) == config.FREE_REFINES
    assert guards.forever_left_today(True, rows, T) == config.PAID_DAILY_REFINES
    assert guards.decide("refine", True, rows, 0, T)[0] is True


def test_tool_endpoint_kill_switch_and_unknown_tool(env, monkeypatch):
    import app, config
    c = TestClient(app.app)
    assert c.post("/rec/tool", json={"tool": "nope"}).status_code == 400
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    assert c.post("/rec/tool", json={"tool": "split_budget"}).status_code == 503


def test_tool_endpoint_defaults_come_from_the_profile(env):
    import app
    d = TestClient(app.app).post("/rec/tool", json={"tool": "estimate_cost"}).json()
    w = d["widgets"][0]
    assert (w["state"], w["guests"], w["budget"]) == ("Texas", 120, 60000) and d["chat_id"] is None


def test_widgets_default_to_empty_on_answers_and_survive_storage():
    import app, xano
    assert app._public({"text": "x", "cards": []})["widgets"] == []
    w = {"type": "budget_status", "v": 1, "added": 1}
    stored = app._stored_cards({"cards": [{"vendor_id": "V1"}], "widgets": [w]})
    turn = xano.parse_message_row({"id": 1, "role": "assistant", "content": "t", "cards": stored})
    assert turn["widgets"] == [w] and turn["cards"] == [{"vendor_id": "V1"}]
    assert xano.parse_message_row({"id": 2, "role": "assistant", "content": "t", "cards": []})["widgets"] == []


# ---------------------------------------------------------------- the chat: the model calls the same tools

class _Msg:
    def __init__(self):
        self.content, self.model = [], "fake"
        self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)


def _fake_client(script, seen):
    class Runner:
        def __init__(self, tools, **kw):
            self.tools = {(getattr(t, "name", None) or t.__name__): t for t in tools}
            seen["tool_names"] = list(self.tools)
            seen["system"] = " ".join(b["text"] for b in kw.get("system") or [])

        def __iter__(self):
            for name, args in script:
                t = self.tools[name]
                seen.setdefault("calls", []).append((name, t.call(args) if hasattr(t, "call") else t(**args)))
            yield _Msg()

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    return Client


def _run(monkeypatch, script, detail, access, flag=True):
    import agent, config, xano
    seen = {}
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", flag)
    monkeypatch.setattr(xano, "state_average_rows", lambda tok: STATE_ROWS)
    monkeypatch.setattr(xano, "guest_modifier", lambda tok, n: MODIFIERS.get(n))
    monkeypatch.setattr(xano, "market_benchmarks", lambda s, g=0: {"state": s, "metrics": {}})
    monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(script, seen), raising=False)
    res, usage = agent.run("tok", {"id": 7}, access, [{"role": "user", "content": "What will our wedding cost?"}],
                           profile_override={"Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 82,
                                             "Wedding_Budget": 30000}, detail=detail)
    return res, usage, seen


def test_chat_tool_adds_its_widget_and_its_figures_pass_the_check(monkeypatch):
    script = [("estimate_wedding_cost", {}),
              ("present_recommendations", {"message": "A typical Texas wedding for 82 guests is about $22,764.",
                                           "vendor_ids": [], "reasons": [], "chips": []})]
    res, usage, seen = _run(monkeypatch, script, "full", True)
    assert {"estimate_wedding_cost", "split_budget", "compare_venues", "budget_status"} <= set(seen["tool_names"])
    assert [w["type"] for w in res["widgets"]] == ["cost_estimate"]
    assert [t["amount"] for t in res["widgets"][0]["tiers"]] == [6829, 22764, 38698]
    assert usage["figure_bounced"] is False and res["text"].endswith("$22,764.")


def test_chat_tool_at_free_level_shows_the_widget_but_the_model_reads_no_figures(monkeypatch):
    script = [("estimate_wedding_cost", {}),
              ("present_recommendations", {"message": "Here's a typical-cost estimate for your state and guest count.",
                                           "vendor_ids": [], "reasons": [], "chips": []})]
    res, _, seen = _run(monkeypatch, script, "free", False)
    read = seen["calls"][0][1]
    assert "22764" not in read and "facts" not in json.loads(read)
    assert res["widgets"][0]["tiers"][1]["amount"] == 22764


def test_chat_answer_without_a_cost_tool_has_empty_widgets(monkeypatch):
    res, _, _ = _run(monkeypatch, [("present_recommendations", {"message": "m", "vendor_ids": [], "reasons": [],
                                                                 "chips": []})], "full", True)
    assert res["widgets"] == []


def test_zero_means_profile_but_an_explicit_false_is_kept():
    fx = types.SimpleNamespace(state_average_rows=lambda tok: STATE_ROWS, guest_modifier=lambda tok, n: MODIFIERS.get(n))
    user = dict(USER, Wedding_Guest_Count=82, Peak_Season=True, Major_City=True)
    w = cost_tools.run_tool("estimate_cost", {"guests": 0, "peak_season": False}, "tok", user, access=True, xano=fx)["widget"]
    assert (w["guests"], w["peak_season"], w["major_city"]) == (82, False, True)



COST_NAMES = {"estimate_wedding_cost", "split_budget", "compare_venues", "budget_status"}
PRESENT = ("present_recommendations", {"message": "m", "vendor_ids": [], "reasons": [], "chips": []})


@pytest.mark.parametrize("detail,access", [("full", True), ("light", True), ("free", False)])
def test_flag_off_chat_is_exactly_as_before(monkeypatch, detail, access):
    """REC_COST_TOOLS_IN_CHAT off (default until the WeWeb widget ships): no cost tools, no COST TOOLS prompt
    text, widgets []."""
    import agent, free_mode
    res, _, seen = _run(monkeypatch, [PRESENT], detail, access, flag=False)
    assert not COST_NAMES & set(seen["tool_names"])
    assert "COST TOOLS" not in seen["system"] and agent.COST_TOOLS_RULES not in seen["system"]
    assert free_mode.COST_TOOLS_RULE not in seen["system"] and "estimate_wedding_cost" not in seen["system"]
    assert res["widgets"] == []


@pytest.mark.parametrize("detail,access", [("full", True), ("light", True), ("free", False)])
def test_flag_on_registers_the_tools_and_the_prompt(monkeypatch, detail, access):
    import agent, free_mode
    _, _, seen = _run(monkeypatch, [PRESENT], detail, access, flag=True)
    assert COST_NAMES <= set(seen["tool_names"])
    assert agent.COST_TOOLS_RULES in seen["system"]
    assert (free_mode.COST_TOOLS_RULE in seen["system"]) == (detail == "free")


def test_flag_defaults_off(monkeypatch):
    import importlib, config
    monkeypatch.delenv("REC_COST_TOOLS_IN_CHAT", raising=False)
    try:
        assert importlib.reload(config).COST_TOOLS_IN_CHAT is False
        monkeypatch.setenv("REC_COST_TOOLS_IN_CHAT", "1")
        assert importlib.reload(config).COST_TOOLS_IN_CHAT is True
    finally:
        monkeypatch.delenv("REC_COST_TOOLS_IN_CHAT", raising=False)
        importlib.reload(config)


@pytest.mark.parametrize("flag", [False, True])
def test_tool_endpoint_ignores_the_flag_and_status_reports_it(env, monkeypatch, flag):
    import app, config, xano
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", flag)
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    c = TestClient(app.app)
    assert c.post("/rec/tool", json={"tool": "split_budget", "args": {"total": 1000}}).status_code == 200
    assert c.get("/rec/status").json()["cost_tools_in_chat"] is flag
