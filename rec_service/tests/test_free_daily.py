"""Open planner (owner 2026-10-31): signed-in users without a plan get a DAILY allowance (40, like Forever), no required
feedback, light pricing (fit + above/below the state, one broad average, no vendor figures) and are capped (429), never
paywalled, when the day's allowance is used. REC_FREE_DAILY_MODE=0 restores the lifetime rules (test_guards.py)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402
import guards  # noqa: E402

T = "2026-10-31"


def ok(kind, free=False, day=T):
    return {"status": "ok", "kind": kind, "counted_as_free": free, "usage_day": day}


@pytest.fixture(autouse=True)
def _daily(monkeypatch):
    monkeypatch.setattr(config, "FREE_DAILY_MODE", True)
    monkeypatch.setattr(config, "FREE_DAILY_REFINES", 40)
    monkeypatch.setattr(config, "FREE_PRICING", "light")
    monkeypatch.setattr(config, "FOREVER_ONLY", False)


def test_free_user_gets_forty_prompts_a_day_then_a_cap_not_a_paywall():
    rows = [ok("opening")]          # 2026-11-07: openings no longer count - only questions do
    for i in range(40):
        allowed, status, free = guards.decide("refine", False, rows, 0, T, feedback_given=False)
        assert (allowed, status, free) == (True, "ok", True), i
        rows.append(ok("refine", True))
    assert guards.free_questions_today(rows, T) == 40
    assert guards.decide("refine", False, rows, 0, T, feedback_given=False) == (False, "blocked_user_cap", False)
    assert guards.decide("opening", False, rows, 0, T, feedback_given=False) == (True, "ok", False)   # openings never blocked
    assert guards.free_refines_left(False, rows, False, today=T) == 0


def test_allowance_resets_each_day_and_feedback_is_never_required():
    yesterday = [ok("refine", True, day="2026-10-30")] * 60
    assert guards.decide("refine", False, yesterday, 0, T, feedback_given=False) == (True, "ok", True)
    assert guards.free_refines_left(False, yesterday, False, today=T) == 40
    assert guards.free_limit(False) == 40 and guards.free_limit(True) == 40
    assert guards.free_feedback_required(False, [ok("refine", True)] * 10, False) is False


def test_spend_pools_and_kill_switch_still_apply(monkeypatch):
    assert guards.decide("refine", False, [], 0, T, free_spend_today=config.FREE_DAILY_USD) == (False, "blocked_free_cap", False)
    assert guards.decide("refine", False, [], config.GLOBAL_DAILY_USD, T) == (False, "blocked_global_cap", False)
    monkeypatch.setattr(config, "FOREVER_ONLY", True)
    assert guards.decide("refine", False, [], 0, T) == (False, "forever_only", False)


def test_tier_fields_and_detail_level():
    import app
    f = app._tier_fields(False, False, False, None, None, 0, False)
    assert f["tier"] == "free" and f["price_locked"] is True and f["questions_left"] == 0
    assert f["paywall"] is False and f["feedback_required"] is False
    assert f["free_daily_mode"] is True and f["free_daily_limit"] == 40
    assert app._detail(True, True) == "full"
    assert app._detail(False, True) == "light"      # a plan without full figures
    assert app._detail(False, False) == "light"     # no plan: light pricing in open-planner mode
    config.FREE_PRICING = "none"
    assert app._detail(False, False) == "free"
    config.FREE_DAILY_MODE = False
    config.FREE_PRICING = "light"
    assert app._detail(False, False) == "free"
    assert app._tier_fields(False, False, False, None, None, 0, False)["paywall"] is True


def test_prompt_rules_light_average_and_closing_question():
    import agent
    text = agent.system_prompt(True) + agent.system_prompt(False)
    assert "EVERY message ends with exactly one specific next question" in text
    assert "broad state-level average" in text and "You get no vendor figures" in text
