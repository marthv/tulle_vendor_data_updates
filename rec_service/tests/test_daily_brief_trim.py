"""Daily brief trim (owner 2026-10-31): green health lines collapse, the pricing Checks line goes, the assistant line
shows free spend against the $30 pool and the $60 cap."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import daily_brief  # noqa: E402
import rec_watch  # noqa: E402

HEALTH = """:stethoscope: *Endpoint health — last 24h*
:large_green_circle: *Search reliability* (signed in) — 0 of 3,962 failed (0.0%)
:large_yellow_circle: *Map speed* — 12% took over 3s, worst 16.08s, typical 0.074s
:large_green_circle: *Logins* — 6 users signed out by an expired token · 151 logins
_Details: Tulle Admin → 🩺 Health tab · Recorded to Xano table 78 for trend history._"""


def test_trim_health_keeps_only_non_green_lines():
    out = daily_brief._trim_health(HEALTH).split("\n")
    assert out[0].startswith(":stethoscope:") and len(out) == 3
    assert out[1].startswith(":large_yellow_circle: *Map speed*") and out[2].startswith("_Details")
    all_green = "\n".join(l for l in HEALTH.split("\n") if ":large_yellow_circle:" not in l)
    assert daily_brief._trim_health(all_green).startswith(":stethoscope: *Endpoint health — last 24h* - all green")


def test_trim_pricing_drops_checks_line():
    text = "*Pricing test* (day 30.4) - revenue: raised $209/d\nNY tier (day 24.5): $140/d\nChecks: routing OK"
    assert daily_brief._trim_pricing(text) == "*Pricing test* (day 30.4) - revenue: raised $209/d\nNY tier (day 24.5): $140/d"


def test_assistant_line_shows_free_spend_and_pools(monkeypatch):
    rows = [
        {"created_at": 9e15, "status": "ok", "kind": "opening", "user_id": 1, "cost_usd": 0.05, "counted_as_free": False},
        {"created_at": 9e15, "status": "ok", "kind": "refine", "user_id": 2, "cost_usd": 0.03, "counted_as_free": True},
        {"created_at": 9e15, "status": "blocked_user_cap", "kind": "refine", "user_id": 2, "counted_as_free": False},
    ]
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    monkeypatch.setattr(rec_watch, "_rows_for_day", lambda token, day: rows if day == today else [])   # build() asks for 2 days
    monkeypatch.setattr(rec_watch, "FREE_CAP", 30.0)
    monkeypatch.setattr(rec_watch, "CAP", 60.0)
    text = rec_watch.build("tok")
    assert "2 users (1 free), 1 opening + 1 refine · spent $0.08 (free $0.03 of $30 pool · $60/day cap)" in text
    assert "daily-cap hits: 1" in text
