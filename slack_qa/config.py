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


def owner_user_id() -> str:            # Vivek: never auto-answered in scan mode
    return _s("QA_OWNER_USER_ID", "U05UJMCHUV6")


def deploy_channel() -> str:           # #deployment (WeWeb staging publish notes)
    return _s("QA_DEPLOY_CHANNEL", "C0AML8KKF4Z")


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
