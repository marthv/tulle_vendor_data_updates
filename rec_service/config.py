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
# OPEN PLANNER (owner 2026-10-31): anyone signed in gets a DAILY allowance like Forever instead of the lifetime
# free limit, no required feedback (the one-tap check-in stays a suggestion), and "light" pricing - fit, whether each
# line is above or below the state, one broad state average, never a vendor's own figure; every answer points at the
# PDF. Full figures stay a plan feature. "0" restores the 10-14 lifetime rules above. Pools: REC_FREE_DAILY_USD 30,
# REC_GLOBAL_DAILY_USD 60 (owner 2026-10-31).
FREE_DAILY_MODE = os.environ.get("REC_FREE_DAILY_MODE", "1") == "1"
FREE_DAILY_REFINES = int(os.environ.get("REC_FREE_DAILY_REFINES", "40"))
FREE_PRICING = os.environ.get("REC_FREE_PRICING", "light").strip().lower()   # light | none (price-free free_mode)
# LIGHT detail gets one rounded price band per venue (2026-11-07, owner "do all 5", item 3): ~1 in 5 typed Planner
# questions asked about price and LIGHT could only say "below / typical / above average". The band is the FULL
# estimate (or the rental) rounded outward to $5K ($1K under $10K) - exact figures stay in the PDF. 0 = off, and
# LIGHT is then exactly as before.
# The Planner's first message never mentions Forever (2026-11-07, Kate 11-05). 0 = off (openings as before).
OPENING_NO_FOREVER = os.environ.get("REC_OPENING_NO_FOREVER", "1").strip() not in ("0", "false", "no", "")
# Item 5 (2026-11-07), a reason to come back: a returning couple with 2+ saved venues gets an opening built on that
# shortlist; DATE_FLEX = check cheaper (off-peak) dates before calling a venue they like a no (Des, 11-05).
RETURNING_OPENING = os.environ.get("REC_RETURNING_OPENING", "1").strip() not in ("0", "false", "no", "")
DATE_FLEX = os.environ.get("REC_DATE_FLEX", "1").strip() not in ("0", "false", "no", "")
LIGHT_PRICE_BANDS = os.environ.get("REC_LIGHT_PRICE_BANDS", "1").strip() not in ("0", "false", "no", "")
# Whole service: stop spending above this many USD per UTC day.
GLOBAL_DAILY_USD = float(os.environ.get("REC_GLOBAL_DAILY_USD", "40"))
# Cost tools in the CHAT (2026-10-25): "1" = the model gets the 4 cost tools + the COST TOOLS prompt paragraph and
# answers carry their widgets. Default OFF until the WeWeb widget component is published - the live page can't
# draw widgets, so the model must not say "see the chart below" yet. POST /rec/tool works either way.
COST_TOOLS_IN_CHAT = os.environ.get("REC_COST_TOOLS_IN_CHAT", "0") == "1"
# Thin-profile questions on the opening (owner decision 2026-10-26, profile_questions.py): "0" = the opening is
# exactly as before (starter chips, no `questions`). Works on the current page: the first question's answers are
# the opening's chips.
PROFILE_QUESTIONS = os.environ.get("REC_PROFILE_QUESTIONS", "1") == "1"
# Venue name lookup fast path (owner decision 2026-10-26, lookup.py): "oheka castle" / "Khol mansion" / "is X listed?"
# is answered from table 11 in code, without the model. 0 = off (every message goes to the model, as before).
NAME_LOOKUP = os.environ.get("REC_NAME_LOOKUP", "1") == "1"
# Missing venues ("We don't have X in Tulle yet") are posted to the feedback Slack channel for the data team.
VENUE_REQUEST_SLACK = os.environ.get("REC_VENUE_REQUEST_SLACK", "1") == "1"
# Railway search (owner decision 2026-10-26): this % of assistant users (stable per user, see railway_search.arm)
# get their venue searches from the Railway search-api instead of ep119, with ep119 as the fallback. 0 = off (the
# search-api is never called). Start: 10. Set it back to 0 to turn it off without a deploy.
def _pct(v):
    try:
        return max(0, min(100, int(float(v))))
    except (TypeError, ValueError):
        return 0


RAILWAY_SEARCH_PCT = _pct(os.environ.get("REC_RAILWAY_SEARCH_PCT", "0"))
RAILWAY_SEARCH_URL = os.environ.get("REC_RAILWAY_SEARCH_URL",
                                    "https://search-api-production-66bb.up.railway.app/search")
try:
    RAILWAY_SEARCH_TIMEOUT_S = float(os.environ.get("REC_RAILWAY_SEARCH_TIMEOUT_S", "3.0"))
except ValueError:
    RAILWAY_SEARCH_TIMEOUT_S = 3.0
# Users always on Railway search, whatever the pct (owner 2026-10-26: his own account 14998). Comma-separated ids.
RAILWAY_SEARCH_FORCE_IDS = {x.strip() for x in os.environ.get("REC_RAILWAY_SEARCH_FORCE_USER_IDS", "").split(",") if x.strip()}
# "1" = off. One env var flip on Railway, no deploy.
KILL_SWITCH = os.environ.get("REC_KILL_SWITCH", "0") == "1"
# Streamed answer text (owner 2026-10-31, Claude-style Planner chat): the final model turn is streamed and the answer
# text written so far is published on GET /rec/progress/{request_id} as `text`, so the page shows it while the model is
# still writing. Never for a couple with no plan (free mode redacts prices only on the finished answer). "0" = off: the
# model is called exactly as before and `text` stays "".
STREAM_TEXT = os.environ.get("REC_STREAM_TEXT", "1") == "1"

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

# In-process cache of venue pricing panels (2026-10-26 speed pass; xano.cached_venue_pricing). Pricing was 3.45 s of
# the 14.65 s average opening. Keyed ONLY by what ep230/ep231 depend on (vendor + endpoint + ep230 entitlement), so
# a paid payload never reaches a free or locked user. 0 disables. Prices edited in Xano show up within this long.
# 2026-11-02: 1 h -> 12 h and 5,000 -> 12,000 entries. Measured: 74% of openings fetched uncached pricing (median
# 2.7 s of the request); all-cached requests were 3-4 s faster. The process used 0.14 GB of its 8 GB. A corrected
# price can take up to 12 h to reach the Planner (the venue page itself is always live).
PRICING_CACHE_S = max(0, int(os.environ.get("REC_PRICING_CACHE_S", str(12 * 3600)) or 0))
PRICING_CACHE_MAX = max(1, int(os.environ.get("REC_PRICING_CACHE_MAX", "12000") or 12000))
# Background warmer (pricing_warm.py): the free-tier panel of the top venues per state, at start-up and every
# PRICING_WARM_EVERY_S. REC_PRICING_WARM=0 turns it off.
PRICING_WARM = os.environ.get("REC_PRICING_WARM", "1").strip() != "0"
PRICING_WARM_PER_STATE = int(os.environ.get("REC_PRICING_WARM_PER_STATE", "40") or 40)
PRICING_WARM_PARALLEL = int(os.environ.get("REC_PRICING_WARM_PARALLEL", "4") or 4)
PRICING_WARM_EVERY_S = int(os.environ.get("REC_PRICING_WARM_EVERY_S", str(6 * 3600)) or 21600)
PRICING_WARM_DELAY_S = int(os.environ.get("REC_PRICING_WARM_DELAY_S", "20") or 20)
# Prefetch caches (2026-10-27, xano.BENCH_CACHE / CARD_CACHE): table 63 state benchmarks (rebuilt by a batch job, not
# per edit) and table-11 vendor cards for the couple's Saved list. 0 disables either one.
BENCH_CACHE_S = max(0, int(os.environ.get("REC_BENCH_CACHE_S", str(6 * 3600)) or 0))
CARD_CACHE_S = max(0, int(os.environ.get("REC_CARD_CACHE_S", "3600") or 0))
# Reuse today's opening (2026-10-27, owner OK #5; opening_reuse.py): the same couple opening the Planner again the
# same ET day with nothing that shapes the answer changed gets the stored answer (~0.5 s, no model call, no usage
# row). "0" = off (every visit generates, as before). Max age caps how old the reused cards' prices can be.
OPENING_REUSE = os.environ.get("REC_OPENING_REUSE", "1") == "1"
# Pre-search (2026-11-01 speed pass): the opening's first search_venues call is run from the profile BEFORE the
# first model call and handed to the model as a finished tool call, so the model starts on what used to be
# turn 2 (turn 1 only picked search arguments: median 1.8 s of a 10.3 s opening). REC_PRESEARCH=0 restores
# the two-turn opening.
PRESEARCH = os.environ.get("REC_PRESEARCH", "1") == "1"
OPENING_REUSE_MAX_AGE_S = max(0, int(os.environ.get("REC_OPENING_REUSE_MAX_AGE_S", str(6 * 3600)) or 0))
# Prewarm (owner 2026-11-02, "time-to-value needs to decrease a lot"): POST /rec/prewarm generates the couple's
# opening in the background (e.g. the moment onboarding finishes) into the reuse store, so the Planner page gets it
# without waiting on the model. Needs OPENING_REUSE. "0" = the endpoint answers {"status": "off"} and does nothing.
PREWARM = os.environ.get("REC_PREWARM", "1") == "1"
# At most this many prewarm model runs per couple per ET day (a page that calls it on every load can't run up cost).
PREWARM_MAX_PER_DAY = max(0, int(os.environ.get("REC_PREWARM_MAX_PER_DAY", "3") or 0))
# An opening that finds a same-inputs opening already being generated (a prewarm, or the page's double mount) waits
# this long for it before generating its own (then runs its own: a stuck prewarm costs at most this much extra).
PREWARM_JOIN_S = max(0.0, float(os.environ.get("REC_PREWARM_JOIN_S", "15") or 0))   # openings p90 7.4 s (11-02); a stuck run gives up the wait
# Railway search circuit breaker (2026-10-27, owner OK #2): after a timeout / connection error / 5xx the Planner
# skips the search-api for this many seconds and goes straight to ep119. 0 = no breaker (old behaviour).
RAILWAY_BREAKER_S = max(0, int(os.environ.get("REC_RAILWAY_BREAKER_S", "60") or 0))
# Listings-only vendor types in the assistant (owner 2026-10-26: "roll in other vendors into the planner now"):
# search_vendors (florists, hair and makeup, caterers, planners - no pricing held) + the prompt scope that offers
# them. Default ON; REC_LISTINGS_IN_CHAT=0 = the tool list and SYSTEM prompt exactly as before.
LISTINGS_IN_CHAT = os.environ.get("REC_LISTINGS_IN_CHAT", "1") == "1"
# Florists stop being listings-only (2026-11-07, item 4): table 11's flt_fl_* (tier, typical total, starting prices,
# minimum spend - from the florists' PDFs, florals work 2026-10-27) go to the Planner. FULL sees figures, LIGHT the
# tier + a rounded band, free mode nothing. 0 = florists exactly as before (no pricing).
FLORIST_PRICING = os.environ.get("REC_FLORIST_PRICING", "1").strip() not in ("0", "false", "no", "")
