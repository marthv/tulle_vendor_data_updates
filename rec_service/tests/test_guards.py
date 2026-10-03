import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402
import guards  # noqa: E402

T = "2026-10-03"


def ok(kind, free=False, day=T):
    return {"status": "ok", "kind": kind, "counted_as_free": free, "usage_day": day}


def test_free_user_gets_exactly_three_refines():
    rows = []
    for i in range(3):
        allowed, status, free = guards.decide("refine", False, rows, 0, T)
        assert allowed and free, i
        rows.append(ok("refine", True))
    assert guards.decide("refine", False, rows, 0, T) == (False, "blocked_free_limit", False)
    assert guards.free_refines_left(False, rows) == 0


def test_errors_do_not_use_up_free_refines():
    rows = [{"status": "error", "kind": "refine", "counted_as_free": False}] * 5
    assert guards.decide("refine", False, rows, 0, T)[0] is True


def test_opening_is_free_but_capped_per_day():
    rows = [ok("opening")] * config.DAILY_OPENINGS
    assert guards.decide("opening", False, rows, 0, T) == (False, "blocked_user_cap", False)
    assert guards.decide("opening", False, [ok("opening", day="2026-10-02")] * 9, 0, T)[0] is True


def test_paid_user_daily_cap_resets_each_day():
    rows = [ok("refine")] * config.PAID_DAILY_REFINES
    assert guards.decide("refine", True, rows, 0, T)[1] == "blocked_user_cap"
    old = [ok("refine", day="2026-10-02")] * config.PAID_DAILY_REFINES
    assert guards.decide("refine", True, old, 0, T) == (True, "ok", False)


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
