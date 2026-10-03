"""Who may spend what. Pure functions over rec_usage rows so the rules are unit-testable offline.

FREE USERS - LIFETIME, NEVER RESETS (user decision 2026-10-03): the first opening picks are free, then
3 questions, then the plan paywall. Any LATER opening (profile edit, a return visit that asks for fresh
picks) spends one of those 3 questions. There is deliberately no daily reset: the engine's job is to get
a couple to open a pricing PDF in their FIRST session, not to be a free tool they return to.
FOREVER: 40 prompts per UTC day (openings + questions combined), shown to the user.
"""
import config


def _ok(rows):
    return [r for r in rows if r.get("status") == "ok"]


def free_used(usage_rows):
    """Free questions spent so far (lifetime)."""
    return sum(1 for r in _ok(usage_rows) if r.get("counted_as_free"))


def decide(kind, paid, usage_rows, spend_today, today):
    """Return (allowed, status, counted_as_free). status is what gets logged when blocked.

    Order matters: kill switch and global cap protect the bill first, then per-user rules.
    Only successful (status ok) requests count toward limits - an error never costs the user a question."""
    if config.KILL_SWITCH:
        return False, "killed", False
    if spend_today >= config.GLOBAL_DAILY_USD:
        return False, "blocked_global_cap", False
    ok = _ok(usage_rows)
    if paid:
        # Forever: 40 prompts per UTC day, openings and questions combined (user decision 2026-10-03).
        n = sum(1 for r in ok if r.get("usage_day") == today)
        if n >= config.PAID_DAILY_REFINES:
            return False, "blocked_user_cap", False
        return True, "ok", False
    if kind == "opening" and not any(r.get("kind") == "opening" for r in ok):
        return True, "ok", False                      # the one free opening, ever
    if free_used(usage_rows) >= config.FREE_REFINES:
        return False, "blocked_free_limit", False
    return True, "ok", True                           # spends one of the 3 lifetime questions


def free_refines_left(paid, usage_rows):
    if paid:
        return None
    return max(0, config.FREE_REFINES - free_used(usage_rows))


def cost_usd(model, usage):
    p = config.PRICES.get(model) or config.PRICES["claude-sonnet-5-5"]
    return round((usage["input_tokens"] * p["in"] + usage["cache_write_tokens"] * p["in"] * 1.25
                  + usage["cache_read_tokens"] * p["cache_read"] + usage["output_tokens"] * p["out"]) / 1e6, 6)
