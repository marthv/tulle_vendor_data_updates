"""Environment settings. Read at call time (not import time) so a Railway variable change or a test's
monkeypatch takes effect without a restart of the module."""
from __future__ import annotations

import os
from pathlib import Path


def _s(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _dry(name: str) -> bool:
    """Dry-run flags FAIL CLOSED: only an explicit 0/false/no/off turns dry run off. A typo ("O", "flase") stays dry."""
    v = os.environ.get(name)
    return not (v is not None and v.strip().lower() in ("0", "false", "no", "off"))


def _f(name: str, default: float) -> float:
    try:
        return float(_s(name, str(default)))
    except ValueError:
        return default


def enabled() -> bool:                 # QA_ENABLED=0 -> kill switch: ack Slack, do nothing
    return _flag("QA_ENABLED", True)


def default_muted() -> bool:           # QA_DEFAULT_MUTED=1 (default): new channels answer @-mentions only
    return _flag("QA_DEFAULT_MUTED", True)


def daily_usd_cap() -> float:          # QA_DAILY_USD: estimated Claude spend cap per UTC day
    return _f("QA_DAILY_USD", 2.0)


def scan_threshold() -> float:         # QA_SCAN_THRESHOLD: min confidence to answer unprompted
    return _f("QA_SCAN_THRESHOLD", 0.75)


def channel_hourly_cap() -> int:       # QA_CHANNEL_HOURLY: max bot replies per channel per rolling hour
    return int(_f("QA_CHANNEL_HOURLY", 6))


def daily_report_cap() -> int:         # QA_DAILY_REPORTS: Xano report runs per UTC day
    return int(_f("QA_DAILY_REPORTS", 30))


def signing_secret() -> str:
    return _s("SLACK_SIGNING_SECRET")


def bot_token() -> str:
    return _s("SLACK_BOT_TOKEN")


def bot_user_id() -> str:              # optional; normally read from the event's authorizations[]
    return _s("SLACK_BOT_USER_ID")


def rules_token() -> str:              # QA_RULES_TOKEN: shared with the #deployment notifier (GET /style-rules)
    return _s("QA_RULES_TOKEN")


def owner_user_id() -> str:           # Vivek: never auto-answered in scan mode
    return _s("QA_OWNER_USER_ID", "U05UJMCHUV6")


def deploy_channel() -> str:           # #deployment (WeWeb staging publish notes)
    return _s("QA_DEPLOY_CHANNEL", "C0AML8KKF4Z")


def blocked_channels() -> set[str]:    # never answer or read here (outside people). Default: #ext-rimal-tulle
    return {c.strip() for c in _s("QA_BLOCKED_CHANNELS", "C0AM6UJHA5A").split(",") if c.strip()}


def team_id() -> str:                  # messages from another workspace (Slack Connect / external users) are ignored
    return _s("QA_TEAM_ID", "T05U8AWH14Z")


def answer_model() -> str:
    return _s("QA_MODEL", "claude-sonnet-5-5")


def triage_model() -> str:
    return _s("QA_TRIAGE_MODEL", "claude-haiku-5-5")


def answer_effort() -> str:
    return _s("QA_EFFORT", "low")


def github_token() -> str:
    return _s("GITHUB_TOKEN")


def github_repos() -> list[str]:
    raw = _s("QA_GITHUB_REPOS", "marthv/tulle_vendor_data_updates,marthv/ww-price-report,marthv/ww-planner-chat")
    return [r.strip() for r in raw.split(",") if r.strip()]


def user_names() -> dict[str, str]:    # QA_USER_NAMES="U05UJMCHUV6=Vivek,U0XXXX=Des" (fallback if users:read missing)
    out = {"U05UJMCHUV6": "Vivek"}
    for pair in _s("QA_USER_NAMES").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def state_dir() -> Path:
    """STATE_DIR (a Railway volume, e.g. /data) -> /data if it exists and is writable -> slack_qa/.state."""
    for cand in (_s("STATE_DIR"), "/data"):
        if cand:
            p = Path(cand)
            try:
                p.mkdir(parents=True, exist_ok=True)
                probe = p / ".slack_qa_probe"
                probe.write_text("ok")
                probe.unlink()
                return p
            except OSError:
                continue
    p = Path(__file__).resolve().parent / ".state"
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- v2 (2026-11-20): approvals, dedupe, #feedback watch, roll-up, engine ----------------------------------
# Every capability that can post or write is OFF until its variable is set. Every apply path is a DRY RUN until
# its *_DRY_RUN variable is explicitly 0 (dry run = log + say what would happen; no network write).

def approvers() -> list[str]:          # QA_APPROVERS="U05UJMCHUV6,U0...": who may press Approve / Reject / Undo
    raw = _s("QA_APPROVERS", owner_user_id())
    return [u.strip() for u in raw.split(",") if u.strip()]


def merge_dry_run() -> bool:           # DEDUPE_DRY_RUN=0 -> Approve calls the Xano merge endpoint for real
    return _dry("DEDUPE_DRY_RUN")


def merge_url() -> str:                # XANO_MERGE_URL: Xano ep578 POST /vendor/merge (api group 3)
    return _s("XANO_MERGE_URL")


def merge_secret() -> str:             # XANO_MERGE_SECRET: that endpoint's `secret` body gate
    return _s("XANO_MERGE_SECRET")


def dedupe_channel() -> str:           # DEDUPE_CHANNEL: where merge suggestions are posted. Empty = scan never posts
    return _s("DEDUPE_CHANNEL")


def dedupe_max_cards() -> int:         # DEDUPE_MAX_CARDS: suggestions per post (hard max 10). 0 = no scan cards at all
    return max(0, min(10, int(_f("DEDUPE_MAX_CARDS", 10))))


def dedupe_scheduled_scan() -> bool:   # DEDUPE_SCHEDULED_SCAN=0 -> no nightly / weekly scan. ON by default (owner 2026-11-23)
    return _flag("DEDUPE_SCHEDULED_SCAN", True)


def dedupe_max_pages() -> int:         # DEDUPE_MAX_PAGES: Xano read budget (500-row pages) per scan for reference counts
    return int(_f("DEDUPE_MAX_PAGES", 300))


def dedupe_page_delay() -> float:      # seconds between Xano pages during a scan (keeps load off the live app)
    return _f("DEDUPE_PAGE_DELAY", 0.3)


def dedupe_weekly_dow() -> int:        # 0=Mon .. 6=Sun, full scan day (UTC)
    return int(_f("DEDUPE_WEEKLY_DOW", 0))


def dedupe_hour_utc() -> int:          # hour (UTC) the nightly / weekly scan runs; 08 UTC = 3-4am US Eastern
    return int(_f("DEDUPE_HOUR_UTC", 8))


def feedback_channel() -> str:         # FEEDBACK_CHANNEL: #feedback id. Empty = no watching, no roll-up
    return _s("FEEDBACK_CHANNEL")


def feedback_watch() -> bool:          # FEEDBACK_WATCH=1 -> propose fixes in #feedback threads
    return _flag("FEEDBACK_WATCH", False)


def feedback_daily_cap() -> int:       # FEEDBACK_DAILY_CAP: max proposed-fix cards per UTC day
    return int(_f("FEEDBACK_DAILY_CAP", 15))


def vendor_edit_dry_run() -> bool:     # VENDOR_EDIT_DRY_RUN=0 -> Approve stages + applies via Xano ep280/ep281
    return _dry("VENDOR_EDIT_DRY_RUN")


def visibility_dry_run() -> bool:     # VISIBILITY_DRY_RUN=0 -> Approve on a hide/unhide card calls Xano ep279
    return _dry("VISIBILITY_DRY_RUN")


def vendor_edit_base() -> str:        # Xano api group 3 (the Tulle Ops bot's staged-edit endpoints)
    return _s("VENDOR_EDIT_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:aow91bcd")


def vendor_edit_secret() -> str:       # VENDOR_EDIT_SECRET: same `secret` the Tulle Ops bot sends (XANO_API_KEY there)
    return _s("VENDOR_EDIT_SECRET")


def task_dry_run() -> bool:            # TASK_DRY_RUN=0 -> Approve files a GitHub issue
    return _dry("TASK_DRY_RUN")


def task_repo() -> str:
    return _s("TASK_REPO", "marthv/tulle_vendor_data_updates")


def task_github_token() -> str:        # TASK_GITHUB_TOKEN: issues:write only. Kept apart from the read-only GITHUB_TOKEN
    return _s("TASK_GITHUB_TOKEN")


def rollup_enabled() -> bool:          # ROLLUP_ENABLED=1 -> daily #feedback roll-up by venue
    return _flag("ROLLUP_ENABLED", False)


def rollup_target() -> str:            # ROLLUP_CHANNEL: default = the owner's DM with the bot (a draft for review)
    return _s("ROLLUP_CHANNEL", owner_user_id())


def rollup_hour_utc() -> int:
    return int(_f("ROLLUP_HOUR_UTC", 13))


def scheduler_enabled() -> bool:       # QA_SCHEDULER=1 -> run the in-process scheduler (dedupe + roll-up)
    return _flag("QA_SCHEDULER", False)


def engine_url() -> str:               # EXP_ENGINE_URL: http://experiments-engine.railway.internal:8080
    return _s("EXP_ENGINE_URL")


def engine_key() -> str:               # EXP_API_KEY (reference the engine service's variable)
    return _s("EXP_API_KEY")


def feedback_daily_usd() -> float:     # FEEDBACK_DAILY_USD: #feedback fix calls' own budget, inside QA_DAILY_USD
    return _f("FEEDBACK_DAILY_USD", 0.5)


def feedback_page_budget() -> int:     # pages read to count references on a duplicate reported in #feedback
    return int(_f("FEEDBACK_PAGE_BUDGET", 60))


def ondemand_scan_cooldown_min() -> int:
    return int(_f("DEDUPE_ONDEMAND_COOLDOWN_MIN", 30))


def stale_apply_min() -> int:          # an apply/undo still "in progress" after this long is marked unknown at start-up
    # never shorter than a merge can take (its 300 s timeout) plus two minutes
    return max(int(_f("QA_STALE_APPLY_MIN", 10)), int(merge_timeout() // 60) + 2)


def merge_timeout() -> float:
    return _f("XANO_MERGE_TIMEOUT", 300)     # a merge plans, locks, writes and rebuilds; measured previews alone 14-65 s


def merge_recheck_grace() -> float:    # seconds after Approve during which "no merge recorded" proves nothing (the
    return _f("XANO_MERGE_RECHECK_GRACE", 120)  # request may not have reached Xano yet; ep578 v3 takes its lock first)


def feedback_venue_requests() -> bool:  # FEEDBACK_VENUE_REQUESTS=0 turns off the "Venue not in Tulle" triage (needs FEEDBACK_WATCH=1)
    return _flag("FEEDBACK_VENUE_REQUESTS", True)


def trend_digest_channel() -> str:     # TREND_DIGEST_CHANNEL: where the weekly #feedback trend digest goes. Empty = off
    return _s("TREND_DIGEST_CHANNEL")


def trend_digest_dow() -> int:         # 0=Mon .. 6=Sun (UTC)
    return int(_f("TREND_DIGEST_DOW", 0))


def trend_digest_hour_utc() -> int:    # 14 UTC = 9-10am US Eastern
    return int(_f("TREND_DIGEST_HOUR_UTC", 14))


def trend_digest_max_pages() -> int:   # conversations.history pages (200 messages each) read per digest
    return int(_f("TREND_DIGEST_MAX_PAGES", 10))
