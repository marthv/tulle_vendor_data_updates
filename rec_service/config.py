"""Settings for the recommendation service. Everything tunable is an env var so a cap or the
kill switch can change on Railway without a deploy."""
import os

XANO_BASE = os.environ.get("XANO_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io")
XANO_META = XANO_BASE + "/api:meta/workspace/" + os.environ.get("XANO_WORKSPACE_ID", "1")
# App endpoint groups (see tulletogether/.claude/xano_backups for the endpoint sources).
API_SEARCH_GROUP = XANO_BASE + "/api:GynP5T1B"   # ep119 search, ep231/230 pricing intelligence, favorites
API_AUTH_GROUP = XANO_BASE + "/api:v1keLQ2a"     # auth/me
XANO_METADATA_TOKEN = os.environ.get("XANO_METADATA_TOKEN", "")

USAGE_TABLE_ID = int(os.environ.get("REC_USAGE_TABLE_ID", "79"))       # rec_usage, created 2026-10-03
BENCHMARK_TABLE_ID = int(os.environ.get("REC_BENCHMARK_TABLE_ID", "63"))  # venue_benchmarks

# User decision 2026-10-03: Sonnet 5.5.
MODEL = os.environ.get("REC_MODEL", "claude-sonnet-5-5")
EFFORT = os.environ.get("REC_EFFORT", "low")   # v2 speed pass 2026-10-03; was medium
MAX_TOOL_ROUNDS = int(os.environ.get("REC_MAX_TOOL_ROUNDS", "6"))

# User decision 2026-10-03: opening recs free, then 3 free refinements, then the plan paywall.
FREE_REFINES = int(os.environ.get("REC_FREE_REFINES", "3"))
# Paid users: quiet fair-use cap per UTC day.
PAID_DAILY_REFINES = int(os.environ.get("REC_PAID_DAILY_REFINES", "40"))
# Anyone: max opening generations per UTC day (each profile change regenerates).
DAILY_OPENINGS = int(os.environ.get("REC_DAILY_OPENINGS", "5"))
# Whole service: stop spending above this many USD per UTC day.
GLOBAL_DAILY_USD = float(os.environ.get("REC_GLOBAL_DAILY_USD", "40"))
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
