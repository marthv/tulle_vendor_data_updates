import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("REC_FOREVER_ONLY", "0")   # free-tier rules are tested with the beta gate off
import config  # noqa: E402
import guards  # noqa: E402

T = "2026-10-03"


def ok(kind, free=False, day=T):
    return {"status": "ok", "kind": kind, "counted_as_free": free, "usage_day": day}


def test_free_user_gets_one_free_opening_then_exactly_N_questions_lifetime():
    rows = []
    assert guards.decide("opening", False, rows, 0, T) == (True, "ok", False)
    rows.append(ok("opening"))
    for i in range(config.FREE_REFINES):
        allowed, status, free = guards.decide("refine", False, rows, 0, T)
        assert allowed and free, i
        rows.append(ok("refine", True))
    assert guards.decide("refine", False, rows, 0, T) == (False, "blocked_free_limit", False)
    assert guards.free_refines_left(False, rows) == 0


def test_no_daily_reset_for_free_users():
    rows = [ok("opening", day="2026-09-01")] + [ok("refine", True, day="2026-09-01")] * config.FREE_REFINES
    assert guards.decide("refine", False, rows, 0, T)[1] == "blocked_free_limit"
    assert guards.decide("opening", False, rows, 0, T)[1] == "blocked_free_limit"


def test_second_opening_spends_a_question():
    rows = [ok("opening")]
    assert guards.decide("opening", False, rows, 0, T) == (True, "ok", True)
    rows.append(ok("opening", True))
    assert guards.free_refines_left(False, rows) == config.FREE_REFINES - 1


def test_errors_do_not_use_up_free_questions():
    rows = [ok("opening")] + [{"status": "error", "kind": "refine", "counted_as_free": False}] * 5
    assert guards.decide("refine", False, rows, 0, T) == (True, "ok", True)


def test_paid_user_daily_caps_reset_each_day():
    rows = [ok("refine")] * config.PAID_DAILY_REFINES
    assert guards.decide("refine", True, rows, 0, T)[1] == "blocked_user_cap"
    old = [ok("refine", day="2026-10-02")] * config.PAID_DAILY_REFINES
    assert guards.decide("refine", True, old, 0, T) == (True, "ok", False)
    mixed = [ok("opening")] * 10 + [ok("refine")] * (config.PAID_DAILY_REFINES - 10)
    assert guards.decide("opening", True, mixed, 0, T)[1] == "blocked_user_cap"   # 40/day combined
    assert guards.decide("refine", True, mixed[:-1], 0, T) == (True, "ok", False)


def test_global_cap_and_kill_switch_win():
    assert guards.decide("refine", True, [], config.GLOBAL_DAILY_USD, T)[1] == "blocked_global_cap"
    os.environ["REC_KILL_SWITCH"] = "1"
    importlib.reload(config)
    try:
        assert guards.decide("opening", True, [], 0, T)[1] == "killed"
    finally:
        os.environ["REC_KILL_SWITCH"] = "0"
        importlib.reload(config)


def test_cost():
    u = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_tokens": 0, "cache_write_tokens": 0}
    assert guards.cost_usd("claude-sonnet-5-5", u) == 3.0


def test_forever_monthly_cap_is_quiet_and_resets_next_month():
    days = ["2026-10-%02d" % d for d in range(1, 31)]
    rows = [ok("refine", day=days[i % 30]) for i in range(config.FOREVER_MONTHLY_CAP)]   # spread, never 40 in a day
    assert guards.decide("refine", True, rows, 0, "2026-10-31") == (False, "blocked_monthly_cap", False)
    assert guards.decide("refine", True, rows, 0, "2026-11-01") == (True, "ok", False)   # new month


def test_forever_beta_feedback_checkin():
    n = config.BETA_FEEDBACK_AFTER
    five = [ok("refine")] * n
    assert guards.decide("refine", True, five[:-1], 0, T, feedback_given=False) == (True, "ok", False)
    assert guards.decide("refine", True, five, 0, T, feedback_given=False)[1] == "feedback_required"
    assert guards.decide("opening", True, five, 0, T, feedback_given=False) == (True, "ok", False)   # picks never gated
    assert guards.decide("refine", True, five, 0, T, feedback_given=True) == (True, "ok", False)    # unlocked
    assert guards.beta_questions_left(True, five[:2], False) == n - 2
    assert guards.beta_questions_left(True, five, True) is None
    assert guards.beta_questions_left(False, five, False) is None
    # questions asked on the free tier (counted_as_free) don't count toward the Forever check-in
    free_era = [dict(ok("refine"), counted_as_free=True)] * 10
    assert guards.decide("refine", True, free_era, 0, T, feedback_given=False) == (True, "ok", False)


def test_forever_only_beta_refuses_everyone_else():
    os.environ["REC_FOREVER_ONLY"] = "1"
    importlib.reload(config)
    try:
        assert guards.decide("opening", False, [], 0, T) == (False, "forever_only", False)
        assert guards.decide("refine", False, [], 0, T) == (False, "forever_only", False)
        assert guards.decide("refine", True, [], 0, T) == (True, "ok", False)      # Forever unaffected
    finally:
        os.environ["REC_FOREVER_ONLY"] = "0"
        importlib.reload(config)
