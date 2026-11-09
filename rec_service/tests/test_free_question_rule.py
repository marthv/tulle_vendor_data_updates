"""Owner rule 2026-11-08: a free couple gets 3 questions for life; only after those are all used, 1 per UTC day, and
unused days never accumulate."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import guards  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _rule(monkeypatch):
    monkeypatch.setattr(config, "FREE_DAILY_MODE", True)
    monkeypatch.setattr(config, "FREE_INITIAL_REFINES", 3)
    monkeypatch.setattr(config, "FREE_DAILY_AFTER_INITIAL", 1)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)


def q(day, n=1):
    return [{"status": "ok", "kind": "refine", "counted_as_free": True, "usage_day": day} for _ in range(n)]


def left(rows, today):
    return guards.free_refines_left(False, rows, today=today)


def allowed(rows, today):
    return guards.decide("refine", False, rows, 0.0, today, True, 0.0)[0]


def test_new_couple_gets_three_then_nothing_that_day():
    assert config.FREE_DAILY_MODE and config.FREE_INITIAL_REFINES == 3 and config.FREE_DAILY_AFTER_INITIAL == 1
    rows, d = [], "2026-11-10"
    assert left(rows, d) == 3 and guards.free_phase(rows) == "initial"
    for i in range(3):
        assert allowed(rows, d)
        rows += q(d)
    assert left(rows, d) == 0 and not allowed(rows, d) and guards.free_phase(rows) == "daily"


def test_after_the_three_one_question_a_day_and_no_accumulation():
    rows = q("2026-11-10", 3)
    assert left(rows, "2026-11-11") == 1                       # next day: one
    assert left(rows, "2026-11-14") == 1                       # three idle days later: still ONE, not 4
    rows += q("2026-11-14")
    assert left(rows, "2026-11-14") == 0 and not allowed(rows, "2026-11-14")
    assert left(rows, "2026-11-15") == 1 and allowed(rows, "2026-11-15")


def test_starter_questions_can_be_spread_over_days():
    rows = q("2026-11-10")                                     # one on day 1
    assert left(rows, "2026-11-11") == 2 and guards.free_phase(rows) == "initial"
    rows += q("2026-11-11", 2)                                 # two on day 2 -> all three used
    assert left(rows, "2026-11-11") == 0 and left(rows, "2026-11-12") == 1


def test_openings_and_clarifications_do_not_spend_a_question():
    rows = [{"status": "ok", "kind": "opening", "counted_as_free": False, "usage_day": "2026-11-10"},
            {"status": "ok", "kind": "refine", "counted_as_free": True, "tools": "lookup:choices", "usage_day": "2026-11-10"},
            {"status": "error", "kind": "refine", "counted_as_free": True, "usage_day": "2026-11-10"}]
    assert left(rows, "2026-11-10") == 3


def test_paid_is_unaffected():
    assert guards.free_refines_left(True, q("2026-11-10", 9), today="2026-11-10") is None
