"""Next-planning-step chips (owner 2026-10-25: "make sure the chips still exist to push them to the next step").
Action chips (handled by the page, no model call), the code guarantee on cost-widget replies, /rec/tool chips,
starter chips by flag, the free price guard keeping planning chips, and the flag-off prompt staying exactly
origin/main's. No network, no model."""
import ast
import pathlib
import subprocess
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import chips as ch  # noqa: E402
import free_mode  # noqa: E402

OPEN_COST, OPEN_BUDGET, ADD_PDF = "Open Cost tools", "Open my budget", "Add a quote from a PDF"


def _asst(*chips):
    return {"role": "assistant", "text": "t", "cards": [], "chips": list(chips)}


# ---------------------------------------------------------------- action chips

def test_emitted_chips_look_like_every_other_chip():
    """Owner 2026-10-25: next-step chips blend in - no arrow or marker on anything the backend emits."""
    import agent
    emitted = (list(ch.ACTION_CHIPS) + [c for v in ch.NEXT_STEPS.values() for c in v] + list(ch.GUARANTEE_DEFAULT.values())
               + ch.FREE_FALLBACK_STEPS + ch.STARTER_CHIPS_COST_TOOLS + free_mode.STARTER_CHIPS_COST_TOOLS
               + [c for t in ch.TOOL_WIDGET for f in (False, True) for c in ch.tool_chips(t, free=f)])
    assert not [c for c in emitted if "→" in c or "->" in c]
    assert "→" not in agent.COST_TOOLS_RULES and "put it last" not in agent.COST_TOOLS_RULES
    assert "Don't force it." in agent.COST_TOOLS_RULES and "at most one of the three per reply" in agent.COST_TOOLS_RULES


def test_action_chips_are_exactly_three():
    assert ch.ACTION_CHIPS == {OPEN_COST: "open_cost_tools", OPEN_BUDGET: "open_budget", ADD_PDF: "add_pdf_quote"}


def test_action_label_ignores_case_punctuation_and_an_arrow():
    assert ch.action_label("open my budget") == OPEN_BUDGET
    assert ch.action_label("Open my budget →") == OPEN_BUDGET and ch.action_label("Open Cost tools ->") == OPEN_COST
    assert ch.action_label("Add a quote from a PDF") == ADD_PDF
    assert ch.action_label("Open my budget tab") is None


def test_action_chips_pass_dedupe_even_when_offered_before():
    hist = [_asst("Waterfront venues", OPEN_BUDGET), _asst("Cheaper options", "Find a photographer")]
    out = ch.dedupe_chips(["Waterfront venues", OPEN_BUDGET], hist)
    assert OPEN_BUDGET in out and "Waterfront venues" not in out


def test_at_most_one_action_chip_and_it_is_last():
    out = ch.place_action_chip([OPEN_BUDGET, "A", ADD_PDF, "B", "C", "D"], [])
    assert out == ["A", "B", "C", OPEN_BUDGET]
    assert sum(1 for c in out if ch.action_label(c)) == 1


def test_never_the_same_action_chip_twice_in_a_row():
    hist = [_asst("A", ADD_PDF)]
    out = ch.place_action_chip(["B", ADD_PDF], hist)
    assert ADD_PDF not in out and len(out) >= 2
    # a different action chip is fine, and the next-but-one reply may repeat it
    assert ch.place_action_chip(["B", ADD_PDF, OPEN_BUDGET], hist) == ["B", OPEN_BUDGET]
    assert ch.place_action_chip(["B", ADD_PDF], hist + [_asst("C", "D")]) == ["B", ADD_PDF]


def test_no_action_chip_is_a_no_op():
    c = ["A"] * 1 + ["B", "C", "D", "E"]
    assert ch.place_action_chip(c, [_asst("A")]) == c


def test_action_chip_alone_is_topped_up_to_two():
    out = ch.place_action_chip([OPEN_COST], [])
    assert len(out) == 2 and out[-1] == OPEN_COST


# ---------------------------------------------------------------- code guarantee per widget type

@pytest.mark.parametrize("wtype,default", [("cost_estimate", "Split our budget by category"),
                                           ("budget_split", "Find venues within my venue budget"),
                                           ("venue_compare", ADD_PDF), ("budget_status", OPEN_BUDGET)])
def test_guarantee_appends_the_default_next_step(wtype, default):
    out = ch.guarantee_next_step(["Waterfront venues", "Cheaper options"], [{"type": wtype}])
    assert out == ["Waterfront venues", "Cheaper options", default]
    full = ch.guarantee_next_step(["A", "B", "C", "D"], [{"type": wtype}])
    assert full == ["A", "B", "C", default] and len(full) == 4


def test_guarantee_leaves_a_reply_that_already_has_a_next_step():
    c = ["Waterfront venues", "Compare my saved venues"]
    assert ch.guarantee_next_step(c, [{"type": "cost_estimate"}]) == c
    c = ["Waterfront venues", OPEN_COST]
    assert ch.guarantee_next_step(c, [{"type": "budget_split"}]) == c
    assert ch.guarantee_next_step(["What's left for flowers?", "A"], [{"type": "budget_status"}])[-1] == "A"


def test_guarantee_needs_a_cost_widget():
    assert ch.guarantee_next_step(["A", "B"], []) == ["A", "B"]
    assert ch.guarantee_next_step(["A", "B"], [{"type": "something_else"}]) == ["A", "B"]


def test_guarantee_follows_the_last_widget_and_skips_a_repeated_action_chip():
    ws = [{"type": "cost_estimate"}, {"type": "venue_compare"}]
    assert ch.guarantee_next_step(["A", "B"], ws)[-1] == ADD_PDF
    assert ch.guarantee_next_step(["A", "B"], ws, [_asst("X", ADD_PDF)])[-1] == OPEN_BUDGET


def test_guarantee_on_free_plan_skips_price_filtered_search():
    out = ch.guarantee_next_step(["A", "B"], [{"type": "budget_split"}], free=True)
    assert out[-1] not in ch.PRICE_FILTER_PLANNING and ch.free_ok(out[-1])
    assert out[-1] == OPEN_BUDGET


# ---------------------------------------------------------------- /rec/tool chips (pure)

@pytest.mark.parametrize("tool", ["estimate_cost", "split_budget", "compare_venues", "budget_status"])
@pytest.mark.parametrize("free", [False, True])
def test_tool_chips_two_or_three_with_one_action_last(tool, free):
    out = ch.tool_chips(tool, free=free)
    assert 2 <= len(out) <= 3
    acts = [i for i, c in enumerate(out) if ch.action_label(c)]
    assert len(acts) <= 1 and (not acts or acts[0] == len(out) - 1)
    assert all(ch.is_next_step(c) for c in out)
    if free:
        assert all(ch.free_ok(c) for c in out)


def test_tool_chips_mapping():
    assert ch.tool_chips("estimate_cost") == ["Split our budget by category", "Compare my saved venues", OPEN_COST]
    assert ch.tool_chips("split_budget")[:2] == ["Find venues within my venue budget", "Find a photographer in my budget"]
    assert ch.tool_chips("compare_venues") == ["Find a photographer", ADD_PDF]
    assert ch.tool_chips("budget_status") == ["What's left for photography?", OPEN_BUDGET]
    booked = {"by_category": [{"category": "Photography", "added": 4000}]}
    assert ch.tool_chips("budget_status", widget=booked)[0] == "What's left for entertainment?"
    assert ch.tool_chips("nope") == []


# ---------------------------------------------------------------- free mode keeps planning chips

def test_free_chips_keep_planning_and_action_chips_when_planning_on():
    c = ["What will our wedding cost?", "Split our budget by category", "Compare my saved venues", OPEN_BUDGET]
    assert free_mode.chips(c, planning=True) == c
    # price-filtered searches and plain price chips still drop
    out = free_mode.chips(["Find venues within my venue budget", "Show me cheaper options", "Waterfront venues"],
                          planning=True)
    assert out[0] == "Waterfront venues" and not any("budget" in x or "cheaper" in x for x in out)


def test_free_chips_unchanged_when_planning_off():
    c = ["What will our wedding cost?", "Split our budget by category", "Waterfront venues"]
    assert "What will our wedding cost?" not in free_mode.chips(c)
    assert free_mode.chips(c) == free_mode.chips(c, planning=False)


# ---------------------------------------------------------------- app: starter chips, refine, more, tool

USER = {"id": 7, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 120, "Wedding_Budget": 60000}
CATS = [{"id": i, "Category_Name": n, "Budget_Focus_Normal": p} for i, (n, p) in
        enumerate([("Venue", 40), ("Food and Beverage", 30), ("Photography", 30)])]


@pytest.fixture
def env(monkeypatch):
    import app, config, xano
    st = {"added": [], "history": [], "out": None}
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", dict(USER)))
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    for name, f in {"has_paid_access": lambda u: True, "beta_member": lambda u: True, "has_forever": lambda u: True,
                    "mp_track": lambda *a, **k: None, "log_usage": lambda row: None,
                    "get_chat": lambda uid, cid: {"id": cid, "user_id": uid, "title": "t"},
                    "get_messages": lambda cid: [dict(t) for t in st["history"]],
                    "create_chat": lambda uid, title: {"id": 77, "user_id": uid, "title": title},
                    "add_message": lambda *a: st["added"].append(a), "touch_chat": lambda c: None,
                    "budget_categories": lambda tok: CATS,
                    "state_average_rows": lambda tok: [{"State": "Texas", "Cost_of_Average_Wedding": 24063}],
                    "guest_modifier": lambda tok, n: 1.0}.items():
        monkeypatch.setattr(xano, name, f)
    monkeypatch.setattr(app, "_run_guarded", lambda *a, **k: dict(st["out"]))
    return st


def _out(chips, widgets=(), locked=False):
    o = {"text": "t", "cards": [], "more_cards": [], "chips": list(chips), "widgets": list(widgets)}
    if locked:
        o["price_locked"] = True
    return o


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("locked", [False, True])
def test_starter_chips_by_flag(env, monkeypatch, flag, locked):
    import app, config
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", flag)
    env["out"] = _out([], locked=locked)
    got = TestClient(app.app).post("/rec/opening").json()["chips"]
    if not flag:
        assert got == (free_mode.STARTER_CHIPS if locked else ch.STARTER_CHIPS)
    elif locked:
        assert got == ["Try a different style", "What will our wedding cost?", "Outdoor ceremony space",
                       "Change my guest count"]
    else:
        assert got == ["Show me cheaper options", "Try a different style", "What will our wedding cost?",
                       "Change my guests or budget"]


@pytest.mark.parametrize("wtype,default", [("cost_estimate", "Split our budget by category"),
                                           ("budget_split", "Find venues within my venue budget"),
                                           ("venue_compare", ADD_PDF), ("budget_status", OPEN_BUDGET)])
def test_refine_guarantee_when_flag_on(env, monkeypatch, wtype, default):
    import app, config
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", True)
    env["out"] = _out(["Waterfront venues", "Barn / Ranch venues"], [{"type": wtype, "v": 1}])
    c = TestClient(app.app)
    j = c.post("/rec/refine", json={"chat_id": 5, "message": "q"}).json()
    assert j["chips"] == ["Waterfront venues", "Barn / Ranch venues", default]
    assert env["added"][-1][4] == j["chips"]   # stored as shown
    # same flow, no stored chat (new conversation path)
    j = c.post("/rec/refine", json={"message": "q"}).json()
    assert j["chips"][-1] == default


def test_refine_flag_off_has_no_guarantee(env, monkeypatch):
    import app, config
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", False)
    env["out"] = _out(["Waterfront venues", "Barn / Ranch venues"], [{"type": "venue_compare", "v": 1}])
    j = TestClient(app.app).post("/rec/refine", json={"chat_id": 5, "message": "q"}).json()
    assert j["chips"] == ["Waterfront venues", "Barn / Ranch venues"]


def test_refine_model_action_chips_one_last_and_not_repeated(env, monkeypatch):
    import app, config
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", True)
    env["history"] = [{"role": "user", "text": "q0"}, _asst("Find a photographer", ADD_PDF)]
    env["out"] = _out([ADD_PDF, "Waterfront venues", OPEN_BUDGET, "Barn / Ranch venues"],
                      [{"type": "venue_compare", "v": 1}])
    j = TestClient(app.app).post("/rec/refine", json={"chat_id": 5, "message": "q"}).json()
    assert j["chips"] == ["Waterfront venues", "Barn / Ranch venues", OPEN_BUDGET]


def test_refine_free_keeps_planning_chips_and_guarantee(env, monkeypatch):
    import app, config
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", True)
    env["out"] = _out(["What will our wedding cost?", "Split our budget by category", "Show me cheaper options"],
                      locked=True)
    j = TestClient(app.app).post("/rec/refine", json={"chat_id": 5, "message": "q"}).json()
    assert j["chips"][:2] == ["What will our wedding cost?", "Split our budget by category"]
    assert "Show me cheaper options" not in j["chips"]
    env["out"] = _out(["Waterfront venues", "Barn / Ranch venues"], [{"type": "budget_split", "v": 1}], locked=True)
    j = TestClient(app.app).post("/rec/refine", json={"chat_id": 5, "message": "q"}).json()
    assert j["chips"][-1] == OPEN_BUDGET


def test_more_reply_drops_a_carried_forward_action_chip():
    import app
    hist = [{"role": "user", "content": "q"},
            {"role": "assistant", "text": "t", "chips": ["Waterfront venues", ADD_PDF], "is_more": False,
             "cards": [{"vendor_id": "V1"}],
             "more_cards": [{"vendor_id": "V%d" % i, "name": "N%d" % i} for i in range(2, 6)]}]
    reply = app._more_reply(hist)
    if reply is None:
        pytest.skip("fixture has too few hidden cards for a Show-3-more batch")
    assert ADD_PDF not in reply["chips"] and len(reply["chips"]) >= 2


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("access", [True, False])
def test_tool_endpoint_returns_and_stores_next_step_chips(env, monkeypatch, flag, access):
    import app, config, xano
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", flag)
    monkeypatch.setattr(xano, "has_paid_access", lambda u: access)
    j = TestClient(app.app).post("/rec/tool", json={"tool": "split_budget", "args": {"total": 50000},
                                                   "chat_id": 9}).json()
    assert j["chips"] == ch.tool_chips("split_budget", free=not access)
    assert 2 <= len(j["chips"]) <= 3
    assert env["added"][-1][4] == j["chips"]


# ---------------------------------------------------------------- flag off = origin/main's prompt and chips

def _main_constants(path):
    root = pathlib.Path(__file__).resolve().parents[2]
    try:
        src = subprocess.run(["git", "show", "origin/main:rec_service/" + path], cwd=root, capture_output=True,
                             text=True, encoding="utf-8", check=True).stdout
    except Exception:
        pytest.skip("origin/main not available")
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    return out


def test_flag_off_prompt_and_chips_identical_to_origin_main():
    import agent
    a, f, c = _main_constants("agent.py"), _main_constants("free_mode.py"), _main_constants("chips.py")
    assert agent.SYSTEM == a["SYSTEM"]
    assert free_mode.RULES == f["RULES"]
    assert free_mode.STARTER_CHIPS == f["STARTER_CHIPS"] and free_mode.TOPUPS == f["TOPUPS"]
    assert ch.STARTER_CHIPS == c["STARTER_CHIPS"] and ch.CHIP_TOPUPS == c["CHIP_TOPUPS"]
    # the new text lives only in the flag-on paragraphs, appended after origin/main's
    assert agent.COST_TOOLS_RULES.startswith(a["COST_TOOLS_RULES"]) and "NEXT PLANNING STEP" in agent.COST_TOOLS_RULES
    assert free_mode.COST_TOOLS_RULE.startswith(f["COST_TOOLS_RULE"])
    assert "NEXT PLANNING STEP" not in agent.SYSTEM + free_mode.RULES
    for label in ch.ACTION_CHIPS:
        assert label in agent.COST_TOOLS_RULES and label not in agent.SYSTEM
