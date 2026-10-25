"""Settings for the recommendation service. Everything tunable is an env var so a cap or the
kill switch can change on Railway without a deploy."""
import os

XANO_BASE = os.environ.get("XANO_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io")
XANO_META = XANO_BASE + "/api:meta/workspace/" + os.environ.get("XANO_WORKSPACE_ID", "1")
# App endpoint groups (see tulletogether/.claude/xano_backups for the endpoint sources).
API_SEARCH_GROUP = XANO_BASE + "/api:GynP5T1B"   # ep119 search, ep231/230 pricing intelligence, favorites
API_AUTH_GROUP = XANO_BASE + "/api:v1keLQ2a"     # auth/me
# The WeWeb app's own group (Cost tools / Budget read these with the user's token, 2026-10-25): /number_of_guests,
# /admin_location_adjustment, /budget_breakdown_current_budget_breakdown, /packages, /venue_calculator_entries_v1.
API_APP_GROUP = XANO_BASE + "/api:WGW_G49d"
XANO_METADATA_TOKEN = os.environ.get("XANO_METADATA_TOKEN", "")

USAGE_TABLE_ID = int(os.environ.get("REC_USAGE_TABLE_ID", "79"))       # rec_usage, created 2026-10-03
BENCHMARK_TABLE_ID = int(os.environ.get("REC_BENCHMARK_TABLE_ID", "63"))  # venue_benchmarks

# User decision 2026-10-03: Sonnet 5.5.
MODEL = os.environ.get("REC_MODEL", "claude-sonnet-5-5")
EFFORT = os.environ.get("REC_EFFORT", "low")   # v2 speed pass 2026-10-03; was medium
MAX_TOOL_ROUNDS = int(os.environ.get("REC_MAX_TOOL_ROUNDS", "6"))
# Thinking (speed pass 2026-10-16). Empty = API default (unchanged behaviour). "disabled", "adaptive",
# "between_tools", or "budget:N" (N >= 1024, a hard cap on thinking tokens per turn).
THINKING = os.environ.get("REC_THINKING", "").strip()
# Stop as soon as present_recommendations is accepted (skips a throwaway model turn). "0" = old behaviour.
EARLY_STOP = os.environ.get("REC_EARLY_STOP", "1").strip() != "0"


def thinking_kwargs():
    """Extra tool_runner kwargs for REC_THINKING; {} keeps the API default."""
    t = THINKING.lower()
    if not t:
        return {}
    if t.startswith("budget:"):
        return {"thinking": {"type": "enabled", "budget_tokens": max(1024, int(t.split(":", 1)[1]))}}
    if t in ("disabled", "adaptive", "between_tools"):
        return {"thinking": {"type": t}}
    return {}

# User decision 2026-10-03: opening recs free, then 3 free refinements, then the plan paywall.
FREE_REFINES = int(os.environ.get("REC_FREE_REFINES", "1"))   # 2026-10-14: owner set free users to 1 question (was 3 since 10-03). Railway pins REC_FREE_REFINES - set it to 1 at launch.
# Forever: prompts per UTC day, openings + questions combined (user decision 2026-10-03: 40).
PAID_DAILY_REFINES = int(os.environ.get("REC_PAID_DAILY_REFINES", "40"))
# Anyone: max opening generations per UTC day (each profile change regenerates).
DAILY_OPENINGS = int(os.environ.get("REC_DAILY_OPENINGS", "5"))
# FREE MODE (owner decisions 2026-10-14; supersedes the 10-08 "feedback earns 3 more" plan): after the
# FREE_REFINES lifetime questions a free user must leave feedback once (403 feedback_required), and that
# feedback earns NOTHING (bonus 0) - the next question or opening is the plan paywall (402).
# REC_FREE_FEEDBACK_REQUIRED=0 skips the feedback step (straight to the paywall).
FREE_FEEDBACK_REQUIRED = os.environ.get("REC_FREE_FEEDBACK_REQUIRED", "1") == "1"
FREE_FEEDBACK_BONUS = int(os.environ.get("REC_FREE_FEEDBACK_BONUS", "0"))
# Free users' share of the daily budget ($15, user decision 2026-10-08) so free traffic can't use up the
# global cap and lock paying members out -> 503 blocked_free_cap. Counted from rec_usage rows with tier "free"
# or counted_as_free (table 79 has no tier column yet, so each free user's one free opening is NOT counted).
FREE_DAILY_USD = float(os.environ.get("REC_FREE_DAILY_USD", "15"))
# Whole service: stop spending above this many USD per UTC day.
GLOBAL_DAILY_USD = float(os.environ.get("REC_GLOBAL_DAILY_USD", "40"))
# Cost tools in the CHAT (2026-10-25): "1" = the model gets the 4 cost tools + the COST TOOLS prompt paragraph and
# answers carry their widgets. Default OFF until the WeWeb widget component is published - the live page can't
# draw widgets, so the model must not say "see the chart below" yet. POST /rec/tool works either way.
COST_TOOLS_IN_CHAT = os.environ.get("REC_COST_TOOLS_IN_CHAT", "0") == "1"
# "1" = off. One env var flip on Railway, no deploy.
KILL_SWITCH = os.environ.get("REC_KILL_SWITCH", "0") == "1"

ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "REC_ALLOWED_ORIGINS", "https://www.tulletogether.app,https://tulletogether.app").split(",") if o.strip()]

# USD per million tokens. claude-api skill table, cached 2026-09-25. Cache writes bill at 1.25x input
# (5-minute TTL), cache reads at the listed rate.
PRICES = {
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.20},
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00, "cache_read": 0.10},
}

# Server-side Mixpanel event per AI request (rec_server_request). Public project token (Tulle - WeWeb,
# 3963130) - the same value the client snippet and ep30 use. Empty string disables.
MIXPANEL_TOKEN = os.environ.get("REC_MIXPANEL_TOKEN", "41be4c76431ca31c2a389e0f4bd1a290")

# Forever: quiet monthly ceiling per user (calendar month, UTC), on top of the 40/day visible limit.
# User decision 2026-10-03: 300/month caps worst-case AI cost at ~$10/user/month.
# Beta is FOREVER-ONLY end to end (user 2026-10-03 late): everyone else is refused with forever_only (402,
# upsell). The free-tier rules below stay in code for when the assistant opens up. "0" restores them.
FOREVER_ONLY = os.environ.get("REC_FOREVER_ONLY", "1") == "1"
# Who counts as a full beta member (unlimited-tier caps, full figures, feedback check-in). User 2026-10-08:
# "paid" = every buyer with active access (1 week / 4 weeks too), same experience as Forever. "forever" =
# the 10-03 rule. Free users stay refused while FOREVER_ONLY is on.
BETA_AUDIENCE = os.environ.get("REC_BETA_AUDIENCE", "forever").strip().lower()
# Forever beta check-in (Kate 2026-10-03, user: "the point is for beta testing"): after this many questions
# a Forever user must leave feedback once to keep going. Whether they bother is the stickiness signal.
# 0 turns it off. Feedback rows: Xano table 81 rec_feedback.
BETA_FEEDBACK_AFTER = int(os.environ.get("REC_BETA_FEEDBACK_AFTER", "5"))
# Beta feedback is also posted to Slack #feedback (private; Tulle Bot must be a member). Railway sets
# REC_SLACK_BOT_TOKEN as a reference to health-report-cron's HEALTH_SLACK_BOT_TOKEN.
SLACK_BOT_TOKEN = os.environ.get("REC_SLACK_BOT_TOKEN", "")
FEEDBACK_SLACK_WEBHOOK = os.environ.get("REC_FEEDBACK_SLACK_WEBHOOK", "")   # Tulle Bot, preferred
FEEDBACK_SLACK_CHANNEL = os.environ.get("REC_FEEDBACK_SLACK_CHANNEL", "C07JML6NMLM")
FEEDBACK_TABLE_ID = int(os.environ.get("REC_FEEDBACK_TABLE_ID", "81"))
FOREVER_MONTHLY_CAP = int(os.environ.get("REC_FOREVER_MONTHLY_CAP", "300"))
