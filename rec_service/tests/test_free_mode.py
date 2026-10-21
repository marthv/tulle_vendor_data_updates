"""FREE MODE (owner decisions 2026-10-14): signed-in users with no plan get 3 questions, one required feedback
that earns nothing, then the paywall - and NO price of any kind in text, cards, chips or what the model reads.
No network, no model: xano and the Anthropic client are faked."""
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

FX = json.loads((pathlib.Path(__file__).parent / "fixtures" / "pe_samples.json").read_text(encoding="utf-8"))
# Any figure a free user must never see: currency, 38K, percentages.
FIGURE = re.compile(r"[$€£]\s?\d|\b\d[\d,.]*\s?[kK]\b|\d\s?%")
PRICE_KEYS = {"venue_fee_range", "food_minimum", "at_a_glance", "price_from", "price_to", "price_label",
              "price_range", "estimate_label", "pricing", "card_price", "market", "starting_price_vs_state",
              "vs_state", "from_price"}
T = "2026-10-14"


def _mods():
    import agent, app, config, free_mode, guards, xano   # lazy: test_guards sets env before ITS config import
    return agent, app, config, free_mode, guards, xano


def _keys(obj):
    if isinstance(obj, dict):
        return set(obj) | set().union(*[_keys(v) for v in obj.values()]) if obj else set()
    if isinstance(obj, list):
        return set().union(*[_keys(v) for v in obj]) if obj else set()
    return set()


# ---------------------------------------------------------------- guards: the free path

def ok(kind, free=False, day=T):
    return {"status": "ok", "kind": kind, "counted_as_free": free, "usage_day": day}


@pytest.fixture
def free_on(monkeypatch):
    _, _, config, _, _, _ = _mods()
    monkeypatch.setattr(config, "FOREVER_ONLY", False)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "FREE_REFINES", 3)
    monkeypatch.setattr(config, "FREE_FEEDBACK_BONUS", 0)
    monkeypatch.setattr(config, "FREE_FEEDBACK_REQUIRED", True)
    monkeypatch.setattr(config, "FREE_DAILY_USD", 15.0)
    monkeypatch.setattr(config, "GLOBAL_DAILY_USD", 40.0)
    monkeypatch.setattr(config, "BETA_FEEDBACK_AFTER", 3)
    return config


def test_free_three_questions_then_feedback_then_paywall_no_bonus(free_on):
    guards = _mods()[4]
    rows = []
    assert guards.decide("opening", False, rows, 0, T, feedback_given=False) == (True, "ok", False)  # first opening free
    rows.append(ok("opening"))
    for i in range(3):
        assert guards.decide("refine", False, rows, 0, T, feedback_given=False) == (True, "ok", True), i
        rows.append(ok("refine", True))
    # 4th question: feedback first (403); a new opening is always free (2026-10-16: every chat starts free)
    assert guards.decide("refine", False, rows, 0, T, feedback_given=False)[1] == "feedback_required"
    assert guards.decide("opening", False, rows, 0, T, feedback_given=False) == (True, "ok", False)
    # feedback given -> it earns nothing: paywall (402) for questions; openings still free
    assert guards.free_refines_left(False, rows, True) == 0
    assert guards.decide("refine", False, rows, 0, T, feedback_given=True)[1] == "blocked_free_limit"
    assert guards.decide("opening", False, rows, 0, T, feedback_given=True) == (True, "ok", False)


def test_free_one_question_then_feedback_then_paywall(free_on, monkeypatch):
    # 2026-10-14: owner cut free users to ONE question (opening + 1, then feedback, then paywall).
    monkeypatch.setattr(free_on, "FREE_REFINES", 1)
    guards = _mods()[4]
    rows = []
    assert guards.decide("opening", False, rows, 0, T, feedback_given=False) == (True, "ok", False)
    rows.append(ok("opening"))
    assert guards.free_refines_left(False, rows, False) == 1
    assert guards.decide("refine", False, rows, 0, T, feedback_given=False) == (True, "ok", True)
    rows.append(ok("refine", True))
    assert guards.free_refines_left(False, rows, False) == 0
    assert guards.decide("refine", False, rows, 0, T, feedback_given=False)[1] == "feedback_required"
    assert guards.decide("opening", False, rows, 0, T, feedback_given=False) == (True, "ok", False)
    assert guards.decide("refine", False, rows, 0, T, feedback_given=True)[1] == "blocked_free_limit"


def test_free_refines_default_is_one(monkeypatch):
    import importlib
    monkeypatch.delenv("REC_FREE_REFINES", raising=False)
    config = importlib.reload(_mods()[2])
    try:
        assert config.FREE_REFINES == 1
    finally:
        importlib.reload(config)


def test_feedback_step_can_be_switched_off(free_on, monkeypatch):
    guards = _mods()[4]
    monkeypatch.setattr(free_on, "FREE_FEEDBACK_REQUIRED", False)
    rows = [ok("opening")] + [ok("refine", True)] * 3
    assert guards.decide("refine", False, rows, 0, T, feedback_given=False)[1] == "blocked_free_limit"


def test_free_cap_blocks_free_only_and_global_cap_still_first(free_on):
    guards = _mods()[4]
    assert guards.decide("opening", False, [], 0, T, free_spend_today=15.0)[1] == "blocked_free_cap"
    assert guards.decide("refine", False, [ok("opening")], 0, T, free_spend_today=15.0)[1] == "blocked_free_cap"
    assert guards.decide("refine", True, [], 0, T, free_spend_today=15.0) == (True, "ok", False)   # paid unaffected
    assert guards.decide("refine", False, [], 40.0, T, free_spend_today=15.0)[1] == "blocked_global_cap"


def test_paid_feedback_is_optional(free_on):
    # 2026-10-16 (owner): paid members are never blocked for feedback - the check-in is a suggestion.
    guards = _mods()[4]
    rows = [ok("refine")] * 3
    assert guards.decide("refine", True, rows, 0, T, feedback_given=False) == (True, "ok", False)
    assert guards.decide("opening", True, rows, 0, T, feedback_given=False) == (True, "ok", False)
    assert guards.decide("refine", True, rows, 0, T, feedback_given=True) == (True, "ok", False)
    assert guards.free_refines_left(True, rows, True) is None


def test_forever_only_still_refuses_free_users(free_on, monkeypatch):
    guards = _mods()[4]
    monkeypatch.setattr(free_on, "FOREVER_ONLY", True)
    assert guards.decide("opening", False, [], 0, T, free_spend_today=99)[1] == "forever_only"


# ---------------------------------------------------------------- the price guard

LEAKS = ["Rental is $12,500 for a Saturday.", "Expect about 38K all in.", "The service charge is 22%.",
         "It's one of the more affordable options in Connecticut.", "A budget-friendly pick.",
         "This one fits your budget.", "It's well within your budget.", "Food runs below typical for the state.",
         "Pricier than the others, but worth it.", "Cheapest of the three.", "Great value for a ballroom.",
         "There's a food and drink minimum on Saturdays.", "Per head costs are lower here.",
         "Roughly 5,000 dollars.", "The rental fee covers the ballroom.", "Prices are lower off-peak rates.",
         "Ask about their 10 percent discount."]
CLEAN = ["Seats 200, so your 150 guests fit comfortably.", "Rated 4.8 on Google from 312 reviews.",
         "It has an outdoor ceremony lawn and a ballroom.", "Open Rosewood's pricing PDF to see exactly what they charge.",
         "You have 2 free PDF views - use one on The Barn's PDF.", "Their package includes 8 hours and a second shooter.",
         "Exact pricing lives in each vendor's PDF and on Tulle plans."]


@pytest.mark.parametrize("s", LEAKS)
def test_price_talk_detected(s):
    assert _mods()[3].has_price_talk(s), s


@pytest.mark.parametrize("s", CLEAN)
def test_fit_talk_allowed(s):
    assert not _mods()[3].has_price_talk(s), s


def test_sanitize_text_drops_price_sentences_and_steers_to_pdf():
    fm = _mods()[3]
    text = ("The Grand Hall seats 220 and has a ballroom with tall ceilings. Its rental is $9,000 on a peak Saturday. "
            "It is also the most affordable of the three.\nRosewood has a waterfront ceremony lawn.")
    clean, n = fm.sanitize_text(text)
    assert n == 2 and not fm.has_price_talk(clean)
    assert "seats 220" in clean and "waterfront" in clean and "pdf" in clean.lower()


def test_sanitize_text_all_price_falls_back():
    fm = _mods()[3]
    clean, n = fm.sanitize_text("It's $5,000. Service charge 22%. Tax is 8.875%.")
    assert clean == fm.FALLBACK_TEXT and n == 3 and not fm.has_price_talk(clean)


def test_free_card_is_a_whitelist():
    fm = _mods()[3]
    c = {"vendor_id": "V1", "vendor_idx": 1, "name": "Grand Hall", "state": "New York", "venue_type": "Ballroom",
         "max_capacity_seated": 220, "image": "x.jpg", "venue_fee_range": [9000, 9000], "food_minimum": 12000,
         "at_a_glance": "Rental $9,000", "price_from": 4000, "price_label": "From $4,000", "estimate_label": "~$40k",
         "description": "A grand ballroom downtown. Packages start at $95 per person.",
         "reason": "Under your budget at $9,000"}
    out = fm.card(c)
    assert not (set(out) & PRICE_KEYS) and out["price_locked"] is True
    assert out["description"] == "A grand ballroom downtown." and out["reason"] == ""
    assert not FIGURE.search(json.dumps(out))


def test_free_chips_drop_money_and_top_up():
    fm = _mods()[3]
    out = fm.chips(["Show me cheaper options", "Is this a good price for Texas?", "Cheaper dates at The Barn?",
                    "Change my guests or budget", "Barn / Ranch venues"])
    assert out[0] == "Barn / Ranch venues" and len(out) >= 2
    assert not any(re.search(r"cheap|price|budget|cost", c, re.I) for c in out)
    assert all(not re.search(r"cheap|price|budget|cost|realistic", c, re.I) for c in fm.STARTER_CHIPS + fm.TOPUPS)


# ---------------------------------------------------------------- what the model reads (agent.run, fake model)

class _Msg:
    def __init__(self):
        self.content, self.model = [], "fake"
        self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)


def _fake_client(script, captured):
    class Runner:
        def __init__(self, tools, **kw):
            captured.update(kw, tool_names=[getattr(t, "name", None) or t.__name__ for t in tools], tools=tools)
            self.tools = {(getattr(t, "name", None) or t.__name__): t for t in tools}

        def __iter__(self):
            out = {}
            for name, args in script:
                if callable(args):
                    args = args(out)
                t = self.tools[name]
                out[name] = (t.call(args) if hasattr(t, "call") else t(**args))
            yield _Msg()

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    return Client


VENUES = [{"vendor_id": "V%d" % i, "vendor_idx": i, "name": "Hall %d" % i, "state": "New York", "address": "1 Main St",
           "venue_type": "Ballroom", "max_capacity_seated": 250, "venue_fee_range": [8000, 12000],
           "food_minimum": 9000 if i == 3 else 0, "at_a_glance": "Rental $8,000",
           "image": "i.jpg", "description": "Elegant ballroom. Rental from $8,000 on Saturdays."} for i in range(1, 12)]
PI = {"vendor_id": "V1", "guest_minimum": 100, "verdict": "Below typical for New York at $8,000",
      "lines": [{"line": "Rental fee", "amount": 8000, "vs_market": "Below average", "source": "pdf", "pct": None,
                 "detail": "The rental is $8,000"}]}


def _run_free(monkeypatch, script, detail="free", user=None):
    agent, _, _, _, _, xano = _mods()
    captured = {}
    monkeypatch.setattr(xano, "search_venues", lambda token, **kw: {"total_matches": 11, "venues": copy.deepcopy(VENUES)})
    monkeypatch.setattr(xano, "venue_pricing", lambda token, vid, paid: dict(copy.deepcopy(PI), vendor_id=vid))
    monkeypatch.setattr(xano, "market_benchmarks", lambda s: {"state": s, "rental": {"median": 9000}})
    monkeypatch.setattr(xano, "venue_details", lambda vid, with_amounts: {
        "vendor_id": vid, "spaces": [{"space": "Ballroom", "Months__Highest_Pricing": "June", "FB_Spend_Min_Type": "Minimum",
                                      "Max_Capacity_Seated": 250, "Outside_Ceremony_Space": "Lawn",
                                      "Venue_Fee_on_a_Peak_Season_Saturday": 8000}],
        "google": {"rating": 4.7, "review_count": 120, "note": "n",
                   "reviews": [{"text": "Beautiful wedding venue"}, {"text": "Worth every $ of the $9k"}]}})
    monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(script, captured), raising=False)
    res, _ = agent.run("tok", user or {"id": 0, "FreeViewsRemaining": 2}, False,
                       [{"role": "user", "content": "How much is a ballroom in NYC?"}],
                       profile_override={"Wedding_Location_Updated": ["New York"], "Wedding_Guest_Count": 150,
                                         "Wedding_Budget": 60000}, detail=detail)
    return res, captured


def test_free_model_gets_no_price_tools_no_price_inputs_no_budget(monkeypatch):
    seen = {}

    def present(out):
        seen.update({k: json.loads(v) for k, v in out.items()})
        return {"message": "Hall 1 is $8,000 and the cheapest. Hall 2 seats 250 with a ballroom.",
                "vendor_ids": ["V1", "V2"], "reasons": ["Cheapest at $8,000", "Seats 250"],
                "chips": ["Cheaper options", "Waterfront venues"]}
    script = [("search_venues", {"states": ["New York"], "guests": 150}), ("venue_details", {"vendor_id": "V1"}),
              ("present_recommendations", present)]
    res, cap = _run_free(monkeypatch, script)
    names = cap["tool_names"]
    assert not {"venue_pricing", "market_benchmarks", "pe_market_rates"} & set(names)
    assert {"search_venues", "search_photographers_entertainment", "present_recommendations"} <= set(names)
    for t in cap["tools"]:   # no price inputs offered (real SDK: input_schema; stub: the signature)
        params = (t.input_schema.get("properties", {}) if hasattr(t, "input_schema")
                  else t.__code__.co_varnames[:t.__code__.co_argcount])
        assert not {"max_venue_fee", "max_food_per_person", "max_price"} & set(params), t
    # what the model READ: no figures, no price keys, guest-minimum fit only
    assert not FIGURE.search(json.dumps(seen)) and not (_keys(seen) & PRICE_KEYS)
    assert seen["search_venues"]["venues"][0]["guest_minimum_fits"] is True
    assert "Months__Highest_Pricing" not in json.dumps(seen) and seen["venue_details"]["google"]["rating"] == 4.7
    assert len(seen["venue_details"]["google"]["reviews"]) == 1
    # prompt: free rules appended, budget hidden, free views mentioned
    sys_text = " ".join(b["text"] for b in cap["system"])
    first = cap["messages"][0]["content"]
    assert "FREE MODE" in sys_text and "DETAIL: FREE" in first and "60000" not in first and "2 free PDF views" in first


def test_paid_model_still_gets_full_tools(monkeypatch):
    script = [("present_recommendations", {"message": "m", "vendor_ids": [], "reasons": [], "chips": []})]
    _, cap = _run_free(monkeypatch, script, detail="full")
    assert {"venue_pricing", "market_benchmarks", "pe_market_rates"} <= set(cap["tool_names"])
    assert len(cap["system"]) == 1 and "60000" in cap["messages"][0]["content"]


def test_free_result_after_guard_has_no_price_anywhere(monkeypatch):
    fm = _mods()[3]

    def present(out):
        return {"message": "Hall 1 rents for $8,000 and is budget-friendly. Hall 2 seats 250 and has a grand ballroom.",
                "vendor_ids": ["V1", "V2"], "reasons": ["Cheapest at $8,000", "Seats 250"],
                "chips": ["Cheaper options", "Is this a good price for New York?"]}
    res, _ = _run_free(monkeypatch, [("search_venues", {"states": ["New York"]}), ("present_recommendations", present)])
    out = fm.sanitize_result(res)
    blob = json.dumps({k: out[k] for k in ("text", "cards", "more_cards", "chips")})
    assert not FIGURE.search(blob) and not (_keys(out["cards"] + out["more_cards"]) & PRICE_KEYS)
    assert all(c["price_locked"] for c in out["cards"] + out["more_cards"]) and out["price_locked"] is True
    assert out["price_redactions"] == 2 and "seats 250" in out["text"] and "PDF" in out["text"]
    assert not any(re.search(r"cheap|price", c, re.I) for c in out["chips"])


def test_free_pe_search_has_no_prices(monkeypatch):
    agent, _, _, _, _, xano = _mods()
    seen = {}

    def present(out):
        seen["s"] = json.loads(out["search_photographers_entertainment"])
        return {"message": "m", "vendor_ids": [], "reasons": [], "chips": []}
    get_calls = []

    def fake_get(url, token=None, params=None):
        get_calls.append(url)
        if url.endswith("/pe_benchmarks"):
            return copy.deepcopy(FX.get("ep207_entertainment_new_york") or {"overall": None})
        return copy.deepcopy(FX["ep119_entertainment"])
    monkeypatch.setattr(xano, "_get", fake_get)
    captured = {}
    monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(
        [("search_photographers_entertainment", {"category": "Entertainment", "states": ["New York"]}),
         ("present_recommendations", present)], captured), raising=False)
    agent.run("tok", {"id": 0}, False, [{"role": "user", "content": "DJ"}],
              profile_override={"Wedding_Location_Updated": ["New York"]}, detail="free")
    assert seen["s"]["vendors"] and not FIGURE.search(json.dumps(seen["s"])) and not (_keys(seen["s"]) & PRICE_KEYS)


# ---------------------------------------------------------------- endpoints

@pytest.fixture
def api(monkeypatch, free_on):
    agent, app, config, _, _, xano = _mods()
    from fastapi.testclient import TestClient
    st = {"rows": [], "logged": [], "feedback": False, "access": False, "beta": False, "spend": (0.0, 0.0),
          "details": [], "fb_rows": []}

    def run(token, user, access, messages, **kw):
        st["details"].append(kw.get("detail"))
        return ({"text": "The Grand Hall is $9,000 and the most affordable. It seats 220 with a ballroom.",
                 "cards": [{"vendor_id": "V1", "name": "Grand Hall", "venue_fee_range": [9000, 9000],
                            "reason": "Cheapest at $9,000", "max_capacity_seated": 220}],
                 "more_cards": [{"vendor_id": "V%d" % i, "venue_fee_range": [1, 2]} for i in range(2, 5)],
                 "chips": ["Cheaper options", "Waterfront venues"]},
                {"model": "m", "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "output_tokens": 1, "tool_calls": 0, "latency_ms": 1})

    def log_usage(row):
        st["logged"].append(row)
        if row.get("status") == "ok":
            st["rows"].append(dict(row, usage_day=T))

    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    monkeypatch.setattr(xano, "create_chat", lambda uid, title: {"id": 51, "title": title})
    monkeypatch.setattr(xano, "list_chats", lambda uid: [])
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "title": "t"})
    monkeypatch.setattr(xano, "get_messages", lambda cid: [])
    monkeypatch.setattr(xano, "add_message", lambda *a: None)
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "log_usage", log_usage)
    monkeypatch.setattr(xano, "user_usage", lambda uid: list(st["rows"]))
    monkeypatch.setattr(xano, "spend_today", lambda: st["spend"])
    monkeypatch.setattr(xano, "has_forever", lambda u: st["beta"])
    monkeypatch.setattr(xano, "beta_member", lambda u: st["beta"])
    monkeypatch.setattr(xano, "has_paid_access", lambda u: st["access"])
    monkeypatch.setattr(xano, "plan_label", lambda u: "free")
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "today", lambda: T)
    monkeypatch.setattr(xano, "add_beta_feedback", lambda row: st["fb_rows"].append(row))
    monkeypatch.setattr(app.notify, "beta_feedback", lambda *a, **k: None)
    monkeypatch.setattr(app, "_feedback_given", lambda uid: st["feedback"])
    monkeypatch.setattr(app, "_memory_notes", lambda uid: [])
    st["c"] = TestClient(app.app)
    return st


def _assert_no_price(j):
    blob = json.dumps({k: j.get(k) for k in ("text", "cards", "chips")})
    assert not FIGURE.search(blob), blob
    assert not (_keys(j.get("cards") or []) & PRICE_KEYS) and "more_cards" not in j
    assert j["price_locked"] is True and all(c["price_locked"] for c in j["cards"])


def test_free_flow_end_to_end(api):
    c = api["c"]
    s = c.get("/rec/status").json()
    assert (s["tier"], s["price_locked"], s["questions_left"], s["feedback_required"], s["feedback_done"],
            s["paywall"], s["free_mode"]) == ("free", True, 3, False, False, False, True)
    j = c.post("/rec/opening").json()
    _assert_no_price(j)
    assert j["questions_left"] == 3 and j["tier"] == "free" and j["chips"] == _mods()[3].STARTER_CHIPS
    for left in (2, 1, 0):
        r = c.post("/rec/refine", json={"chat_id": 9, "message": "what do these cost?"})
        assert r.status_code == 200, r.text
        j = r.json()
        _assert_no_price(j)
        assert j["questions_left"] == left
    assert j["feedback_required"] is True and j["paywall"] is False       # the 3rd answer already says so
    assert api["details"] == ["free"] * 4                                   # model never ran in a price mode
    r = c.post("/rec/refine", json={"chat_id": 9, "message": "more?"})
    assert r.status_code == 403 and r.json()["detail"]["status"] == "feedback_required", r.text
    assert r.json()["detail"]["free_feedback_bonus"] == 0 and r.json()["detail"]["questions_used"] == 3
    assert c.post("/rec/opening").status_code == 200          # every chat starts free (2026-10-16)
    assert c.get("/rec/status").json()["feedback_required"] is True
    r = c.post("/rec/feedback", json={"rating": 4, "would_use": "maybe", "text": "helpful picks so far"})
    assert r.status_code == 200 and r.json()["questions_left"] == 0 and r.json()["paywall"] is True
    assert api["fb_rows"][0]["questions_used"] == 3
    api["feedback"] = True
    r = c.post("/rec/refine", json={"chat_id": 9, "message": "more?"})
    assert r.status_code == 402 and r.json()["detail"]["status"] == "blocked_free_limit", r.text
    assert r.json()["detail"]["paywall"] is True and r.json()["detail"]["tier"] == "free"
    j = c.post("/rec/opening").json()                          # a fresh chat after the paywall is still free
    _assert_no_price(j)
    assert j["paywall"] is True and j["questions_left"] == 0
    s = c.get("/rec/status").json()
    assert (s["questions_left"], s["feedback_required"], s["feedback_done"], s["paywall"]) == (0, False, True, True)


def test_free_show_more_cards_have_no_price(api, monkeypatch):
    _, _, _, _, _, xano = _mods()
    hist = [{"role": "assistant", "text": "x", "cards": [{"vendor_id": "V1"}], "is_more": False, "chips": ["Cheaper options"],
             "more_cards": [{"vendor_id": "V%d" % i, "name": "N", "venue_fee_range": [5000, 6000],
                             "at_a_glance": "$5,000"} for i in range(2, 8)]}]
    monkeypatch.setattr(xano, "get_messages", lambda cid: copy.deepcopy(hist))
    api["rows"] = [ok("opening")]
    j = api["c"].post("/rec/more", json={"chat_id": 9}).json()
    _assert_no_price(j)
    assert j["questions_left"] == 2


def test_free_chat_detail_strips_old_prices(api, monkeypatch):
    _, _, _, _, _, xano = _mods()
    monkeypatch.setattr(xano, "get_messages", lambda cid: [
        {"role": "assistant", "text": "Rental is $9,000. It seats 200 guests in a ballroom.", "chips": ["Cheaper options"],
         "cards": [{"vendor_id": "V1", "venue_fee_range": [9000, 9000]}], "more_cards": []}])
    t = api["c"].get("/rec/chats/9").json()["turns"][0]
    assert not FIGURE.search(json.dumps(t)) and "venue_fee_range" not in json.dumps(t) and t["price_locked"]


def test_free_cap_returns_503(api):
    api["spend"] = (16.0, 15.0)
    r = api["c"].post("/rec/opening")
    assert r.status_code == 503 and r.json()["detail"]["status"] == "blocked_free_cap"
    assert api["logged"][-1]["tier"] == "free"


def test_paid_member_unchanged(api):
    api.update(access=True, beta=True, feedback=True)
    j = api["c"].post("/rec/refine", json={"chat_id": 9, "message": "what does it cost?"}).json()
    assert api["details"] == ["full"]
    assert "$9,000" in j["text"] and j["cards"][0]["venue_fee_range"] == [9000, 9000]   # money talk as today
    assert j["cards"][0]["reason"] == "Cheapest at $9,000" and "Cheaper options" in j["chips"]
    assert (j["tier"], j["price_locked"], j["paywall"]) == ("paid", False, False)
    _, _, config, _, _, _ = _mods()
    assert j["questions_left"] == config.PAID_DAILY_REFINES - 1 and "price_locked" not in j["cards"][0]


def test_forever_only_on_refuses_free_with_402(api, monkeypatch):
    _, _, config, _, _, _ = _mods()
    monkeypatch.setattr(config, "FOREVER_ONLY", True)
    r = api["c"].post("/rec/opening")
    assert r.status_code == 402 and r.json()["detail"]["status"] == "forever_only"
    s = api["c"].get("/rec/status").json()
    assert s["free_mode"] is False and s["paywall"] is True and s["tier"] == "free"


def test_real_sdk_tool_names_and_schemas():
    """The suite stubs the anthropic SDK (first test file wins), so check the REAL beta_tool path in a fresh
    interpreter: same tool names as the full set, no price inputs, wrappers call through to the full tools."""
    import subprocess
    code = r'''
import json, sys
sys.path.insert(0, ".")
from anthropic import beta_tool
import free_mode
calls = []
@beta_tool
def search_venues(states: list[str], guests: int = 0, max_venue_fee: int = 0, venue_types: list[str] = None,
                  vibes: list[str] = None, pricing_models: list[str] = None, keyword: str = "",
                  sort_by: str = "popular_desc", outdoor_ceremony: bool = False, city: str = "",
                  radius_miles: int = 0, relax: list[str] = None) -> str:
    """s

    Args:
        states: s
        guests: g
        max_venue_fee: m
        venue_types: v
        vibes: v
        pricing_models: p
        keyword: k
        sort_by: s
        outdoor_ceremony: o
        city: c
        radius_miles: r
        relax: r
    """
    calls.append("sv")
    return json.dumps({"total_matches": 1, "venues": [{"vendor_id": "V1", "venue_fee_range": [1, 2],
                       "pricing": {"guest_minimum_fits": True, "lines": [{"line": "Rental fee"}]}}]})
@beta_tool
def other(x: str = "") -> str:
    """o

    Args:
        x: x
    """
    return "{}"
ts = free_mode.tools(search_venues, other, other, other, other, other)
print(json.dumps({"names": [t.name for t in ts[:4]],
                  "props": {t.name: sorted(t.input_schema["properties"]) for t in ts[:4]},
                  "out": json.loads(ts[0].call({"states": ["New York"]})), "calls": calls}))
'''
    r = subprocess.run([sys.executable, "-c", code], cwd=str(pathlib.Path(__file__).resolve().parent.parent),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout.strip().splitlines()[-1])
    assert d["names"] == ["search_venues", "search_photographers_entertainment", "saved_venues", "venue_details"]
    flat = {p for ps in d["props"].values() for p in ps}
    assert not {"max_venue_fee", "max_food_per_person", "max_price"} & flat and "guests" in d["props"]["search_venues"]
    assert d["calls"] == ["sv"] and d["out"]["venues"] == [{"vendor_id": "V1", "guest_minimum_fits": True}]
