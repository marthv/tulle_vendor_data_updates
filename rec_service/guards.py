"""Who may spend what. Pure functions over rec_usage rows so the rules are unit-testable offline."""
import config


def decide(kind, paid, usage_rows, spend_today, today):
    """Return (allowed, status, counted_as_free). status is what gets logged when blocked.

    Order matters: kill switch and global cap protect the bill first, then per-user rules.
    Only successful (status ok) requests count toward limits - an error never costs the user a refine."""
    if config.KILL_SWITCH:
        return False, "killed", False
    if spend_today >= config.GLOBAL_DAILY_USD:
        return False, "blocked_global_cap", False
    ok = [r for r in usage_rows if r.get("status") == "ok"]
    if kind == "opening":
        n = sum(1 for r in ok if r.get("kind") == "opening" and r.get("usage_day") == today)
        if n >= config.DAILY_OPENINGS:
            return False, "blocked_user_cap", False
        return True, "ok", False
    if paid:
        n = sum(1 for r in ok if r.get("kind") == "refine" and r.get("usage_day") == today)
        if n >= config.PAID_DAILY_REFINES:
            return False, "blocked_user_cap", False
        return True, "ok", False
    used_free = sum(1 for r in ok if r.get("kind") == "refine" and r.get("counted_as_free"))
    if used_free >= config.FREE_REFINES:
        return False, "blocked_free_limit", False
    return True, "ok", True


def free_refines_left(paid, usage_rows):
    if paid:
        return None
    used = sum(1 for r in usage_rows if r.get("status") == "ok" and r.get("kind") == "refine" and r.get("counted_as_free"))
    return max(0, config.FREE_REFINES - used)


def cost_usd(model, usage):
    p = config.PRICES.get(model) or config.PRICES["claude-sonnet-5-5"]
    return round((usage["input_tokens"] * p["in"] + usage["cache_write_tokens"] * p["in"] * 1.25
                  + usage["cache_read_tokens"] * p["cache_read"] + usage["output_tokens"] * p["out"]) / 1e6, 6)
