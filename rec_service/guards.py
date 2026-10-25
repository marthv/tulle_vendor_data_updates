"""Who may spend what. Pure functions over rec_usage rows so the rules are unit-testable offline.

FREE USERS - LIFETIME, NEVER RESETS (user decision 2026-10-03): FREE_REFINES questions (3 until 2026-10-14, now 1),
then the plan paywall. OPENINGS are always free (2026-10-16: every chat starts free, so the page can open a fresh
chat on each visit); before that only the first opening was free and later ones spent a question. There is deliberately no daily reset: the engine's job is to get
a couple to open a pricing PDF in their FIRST session, not to be a free tool they return to.
FREE MODE (owner decisions 2026-10-14): after those 3, ONE required feedback (feedback_required, 403) that
earns no more questions (FREE_FEEDBACK_BONUS = 0); after it, the paywall (402) for any question or opening.
Free traffic has its own daily spend cap (blocked_free_cap, 503) so it can't trip the global cap.
FOREVER: 40 prompts per UTC day (openings + questions combined), shown to the user, plus a quiet
300/calendar-month ceiling (blocked_monthly_cap) that bounds worst-case cost at ~$10/user/month.
"""
import config


def _ok(rows):
    return [r for r in rows if r.get("status") == "ok"]


def _prompts(rows):
    """ok rows that count as a prompt. Quick-action cost tools (kind "tool_<name>", POST /rec/tool, 2026-10-25)
    make no model call and never count toward any allowance (owner decision)."""
    return [r for r in _ok(rows) if not str(r.get("kind") or "").startswith("tool_")]


def free_used(usage_rows):
    """Free questions spent so far (lifetime)."""
    return sum(1 for r in _ok(usage_rows) if r.get("counted_as_free"))


def forever_questions(usage_rows):
    """Questions asked on Forever, lifetime: refines that did NOT spend a free question (those were asked
    before upgrading, or on a 1-week / 4-week plan)."""
    return sum(1 for r in _ok(usage_rows) if r.get("kind") == "refine" and not r.get("counted_as_free"))


def beta_questions_left(paid, usage_rows, feedback_given):
    """Forever questions left before the one-time feedback check-in; None when it doesn't apply."""
    if not paid or feedback_given or config.BETA_FEEDBACK_AFTER <= 0:
        return None
    return max(0, config.BETA_FEEDBACK_AFTER - forever_questions(usage_rows))


def free_limit(feedback_given):
    """Lifetime free questions: FREE_REFINES, plus FREE_FEEDBACK_BONUS (0 since 2026-10-14) after feedback."""
    return config.FREE_REFINES + (config.FREE_FEEDBACK_BONUS if feedback_given else 0)


def free_feedback_required(paid, usage_rows, feedback_given):
    """True when a free user has used the FREE_REFINES questions and still owes the one-time feedback."""
    return (not paid and not feedback_given and config.FREE_FEEDBACK_REQUIRED
            and free_used(usage_rows) >= config.FREE_REFINES)


def decide(kind, paid, usage_rows, spend_today, today, feedback_given=True, free_spend_today=0.0):
    """Return (allowed, status, counted_as_free). status is what gets logged when blocked.

    Order matters: kill switch and global cap protect the bill first, then per-user rules.
    Only successful (status ok) requests count toward limits - an error never costs the user a question."""
    if config.KILL_SWITCH:
        return False, "killed", False
    if spend_today >= config.GLOBAL_DAILY_USD:
        return False, "blocked_global_cap", False
    ok = _prompts(usage_rows)
    if paid:
        # Forever: quiet monthly ceiling first (calendar month, UTC), then 40 prompts per UTC day,
        # openings and questions combined (user decisions 2026-10-03).
        month = sum(1 for r in ok if (r.get("usage_day") or "")[:7] == today[:7])
        if month >= config.FOREVER_MONTHLY_CAP:
            return False, "blocked_monthly_cap", False
        # Beta check-in: since 2026-10-16 (owner) it is OPTIONAL for paid members - the page shows a feedback
        # strip (feedback_suggested) but never blocks a paid question.
        n = sum(1 for r in ok if r.get("usage_day") == today)
        if n >= config.PAID_DAILY_REFINES:
            return False, "blocked_user_cap", False
        return True, "ok", False
    if config.FOREVER_ONLY:
        return False, "forever_only", False          # beta: Forever members only
    if free_spend_today >= config.FREE_DAILY_USD:
        return False, "blocked_free_cap", False       # free share of today's budget is used up
    if kind == "opening":
        return True, "ok", False                      # 2026-10-16 (owner): every chat starts free - openings never
                                                      # spend a question, before or after the paywall
    if free_feedback_required(paid, usage_rows, feedback_given):
        return False, "feedback_required", False      # the one-time feedback, before the paywall
    if free_used(usage_rows) >= free_limit(feedback_given):
        return False, "blocked_free_limit", False
    return True, "ok", True                           # spends one of the lifetime free questions


def forever_left_today(paid, usage_rows, today):
    """Forever prompts left today (None for non-Forever users)."""
    if not paid:
        return None
    return max(0, config.PAID_DAILY_REFINES - sum(1 for r in _prompts(usage_rows) if r.get("usage_day") == today))


def free_refines_left(paid, usage_rows, feedback_given=False):
    if paid:
        return None
    return max(0, free_limit(feedback_given) - free_used(usage_rows))


def cost_usd(model, usage):
    p = config.PRICES.get(model) or config.PRICES["claude-sonnet-5-5"]
    return round((usage["input_tokens"] * p["in"] + usage["cache_write_tokens"] * p["in"] * 1.25
                  + usage["cache_read_tokens"] * p["cache_read"] + usage["output_tokens"] * p["out"]) / 1e6, 6)
