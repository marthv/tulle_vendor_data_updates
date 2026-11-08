"""Owner 2026-11-07: "which one did you mean?" and "we don't have it" lookup answers do not spend a free couple's
question, but still count toward when the one-time rating card is asked."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
import config  # noqa: E402
import guards  # noqa: E402
from test_name_lookup import env, ask, settle  # noqa: E402,F401


def row(tools="search_venues,present_recommendations"):
    return {"status": "ok", "kind": "refine", "counted_as_free": True, "tools": tools, "usage_day": "2026-11-07"}


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(config, "FREE_DAILY_MODE", False)
    monkeypatch.setattr(config, "FREE_REFINES", 2)
    monkeypatch.setattr(config, "FREE_FEEDBACK_BONUS", 1)
    monkeypatch.setattr(config, "FREE_FEEDBACK_REQUIRED", True)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)


def test_clarifications_are_not_spent_but_are_asked(gate):
    rows = [row(), row("lookup:choices"), row("lookup:not_found"), row("lookup:found")]
    assert guards.free_used(rows) == 2 and guards.free_asked(rows) == 4


def test_rating_card_comes_after_two_asks_even_if_one_was_a_clarification(gate):
    rows = [row(), row("lookup:choices")]
    assert guards.free_feedback_required(False, rows, False) is True
    assert guards.decide("refine", False, rows, 0, "2026-11-07", feedback_given=False)[1] == "feedback_required"


def test_after_the_rating_a_clarification_did_not_use_up_a_question(gate):
    rows = [row(), row("lookup:not_found")]
    assert guards.free_refines_left(False, rows, feedback_given=True) == 2      # limit 3, 1 spent
    assert guards.decide("refine", False, rows, 0, "2026-11-07", feedback_given=True)[0] is True
    rows += [row(), row()]
    assert guards.decide("refine", False, rows, 0, "2026-11-07", feedback_given=True)[1] == "blocked_free_limit"


def test_free_lookup_miss_shows_the_question_still_left(env, gate):
    env["forever"], env["access"] = False, False
    env["search"] = lambda t: []
    j = ask("cresthollow pricing")
    assert j["lookup"]["status"] == "not_found"
    assert j["free_refines_left"] == 3            # limit 3 after feedback (env: feedback given), nothing spent
    settle(env, "logged")
    assert [r for r in env["logged"] if r["status"] == "ok"][0]["tools"] == "lookup:not_found"


def test_daily_mode_counts_questions_not_openings(gate, monkeypatch):
    # owner 2026-11-07: free couples get 1 question a day; a home-screen starter (an opening) must not use it up.
    monkeypatch.setattr(config, "FREE_DAILY_MODE", True)
    monkeypatch.setattr(config, "FREE_DAILY_REFINES", 1)
    opening = {"status": "ok", "kind": "opening", "counted_as_free": False, "usage_day": "2026-11-07"}
    assert guards.decide("refine", False, [opening], 0, "2026-11-07")[0] is True
    asked = [opening, row()]
    assert guards.decide("refine", False, asked, 0, "2026-11-07")[1] == "blocked_user_cap"
    assert guards.free_refines_left(False, asked, today="2026-11-07") == 0
    assert guards.decide("refine", False, asked, 0, "2026-11-08")[0] is True            # tomorrow it's back
    assert guards.decide("refine", False, [row("lookup:choices")], 0, "2026-11-07")[0] is True   # clarifying = free
