"""Owner 2026-11-27: "make sure it does show and anyone is allowed to use it whenever (estimate my wedding, check my
budget)". A signed-in couple with no plan who has used today's free question (free daily mode, the first 3 starter
questions long gone) still gets both free cost tools from POST /rec/tool, and a tap never changes questions_left.
Photo-first free couples get the same free widget as outside photo-first (owner 2026-11-27, reverses T0-53 for taps). The kill switch still stops the tools. Canary: a question-limit gate planted in
the tool path must make the check fail."""
import pytest
from fastapi.testclient import TestClient

from test_cost_tools import CATS, MODIFIERS, STATE_ROWS, USER  # noqa: F401  (also sets up the anthropic stub)

T = "2026-11-27"
TOOLS = ("estimate_cost", "budget_status")


def _row(kind, free=False, day=T):
    return {"status": "ok", "kind": kind, "counted_as_free": free, "usage_day": day, "model": "claude-sonnet-5-5"}


# 3 starter questions on earlier days + today's one free question = out of questions until tomorrow
SPENT = [_row("refine", True, "2026-11-20")] * 3 + [_row("refine", True)]


@pytest.fixture
def env(monkeypatch):
    import app, config, guards, photo_first, xano
    st = {"logged": [], "added": []}
    monkeypatch.setattr(config, "FREE_DAILY_MODE", True)
    monkeypatch.setattr(config, "FREE_INITIAL_REFINES", 3)
    monkeypatch.setattr(config, "FREE_DAILY_AFTER_INITIAL", 1)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(guards, "_today", lambda: T)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", dict(USER, forever_access_purchased=False)))
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    monkeypatch.setattr(xano, "has_paid_access", lambda u: False)
    monkeypatch.setattr(xano, "beta_member", lambda u: False)
    monkeypatch.setattr(xano, "has_forever", lambda u: False)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "log_usage", lambda row: st["logged"].append(row))
    monkeypatch.setattr(xano, "today", lambda: T)
    monkeypatch.setattr(xano, "user_usage", lambda uid: SPENT + [dict(r, usage_day=T) for r in st["logged"]])
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "user_id": uid, "title": "t"})
    monkeypatch.setattr(xano, "add_message", lambda *a: st["added"].append(a))
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "budget_categories", lambda tok: CATS)
    monkeypatch.setattr(xano, "state_average_rows", lambda tok: STATE_ROWS)
    monkeypatch.setattr(xano, "guest_modifier", lambda tok, n: MODIFIERS.get(n))
    monkeypatch.setattr(xano, "user_packages", lambda tok, uid: [])
    monkeypatch.setattr(photo_first, "on", lambda user: False)
    return st


def _drain():
    import app
    app._POOL.submit(lambda: None).result()


def _questions_left(c):
    d = c.get("/rec/status").json()
    return d.get("questions_left", d.get("free_refines_left"))


def check_tools_free_at_limit(c, env):
    """The assertion under test: at the limit both tools answer 200 and the count is the same before and after."""
    import guards
    assert guards.decide("refine", False, SPENT, 0, T)[:2] == (False, "blocked_user_cap")   # really out of questions
    before = _questions_left(c)
    assert before == 0, before
    for tool in TOOLS:
        for _ in range(3):   # repeated taps too
            r = c.post("/rec/tool", json={"tool": tool, "chat_id": 9})
            assert r.status_code == 200, (tool, r.status_code, r.text)
            d = r.json()
            assert d["text"] and d["price_locked"] is True
    _drain()
    assert _questions_left(c) == before == 0
    assert all(row["counted_as_free"] is False and row["cost_usd"] == 0 and row["kind"].startswith("tool_")
               for row in env["logged"])
    rows = SPENT + [dict(r, usage_day=T) for r in env["logged"]]
    assert guards.free_refines_left(False, rows, today=T) == 0 and guards.free_questions_today(rows, T) == 1


def test_free_couple_at_the_limit_can_use_both_tools_and_nothing_is_counted(env):
    import app
    check_tools_free_at_limit(TestClient(app.app), env)


def test_widget_still_comes_back_outside_photo_first(env):
    import app
    c = TestClient(app.app)
    for tool, typ in (("estimate_cost", "cost_estimate"), ("budget_status", "budget_status")):
        w = c.post("/rec/tool", json={"tool": tool}).json()["widgets"][0]
        assert w.get("type") == typ, w


def test_photo_first_free_couple_at_the_limit_gets_the_free_widget(env, monkeypatch):
    """Owner 2026-11-27 (reverses T0-53 for taps): photo-first free = the same market-average widget as anywhere else."""
    import app, photo_first
    monkeypatch.setattr(photo_first, "on", lambda user: True)
    c = TestClient(app.app)
    for tool, typ in (("estimate_cost", "cost_estimate"), ("budget_status", "budget_status")):
        r = c.post("/rec/tool", json={"tool": tool})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["price_locked"] is True and d["text"] and d["widgets"][0]["type"] == typ and not d.get("upsell_prompt")
    _drain()
    assert _questions_left(c) == 0


def test_kill_switch_still_stops_the_tools_at_the_limit(env, monkeypatch):
    import app, config
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    assert TestClient(app.app).post("/rec/tool", json={"tool": "estimate_cost"}).status_code == 503


def test_canary_a_question_limit_gate_in_the_tool_path_fails_the_check(env, monkeypatch):
    """Plant the bug the owner described (the tool refused once the free questions are used): the check must fail."""
    import app, guards
    from fastapi import HTTPException
    real = app.cost_tools.run_tool

    def gated(name, args, token, user, **kw):
        if guards.free_refines_left(False, app.xano.user_usage(user["id"]), today=T) <= 0:
            raise HTTPException(429, {"status": "blocked_user_cap"})
        return real(name, args, token, user, **kw)
    monkeypatch.setattr(app.cost_tools, "run_tool", gated)
    with pytest.raises(AssertionError):
        check_tools_free_at_limit(TestClient(app.app), env)


def test_canary_a_tool_tap_that_spends_a_question_fails_the_check(env, monkeypatch):
    import app
    real = app._submit_usage
    monkeypatch.setattr(app, "_submit_usage", lambda row: real(dict(row, kind="refine", counted_as_free=True)))
    with pytest.raises(AssertionError):
        check_tools_free_at_limit(TestClient(app.app), env)
