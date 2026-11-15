"""MCP tool calls (rec_usage kind "mcp_<tool>", 2026-11-15) are not Planner usage: the daily brief (rec_watch.build),
the weekly Planner digest (rec_watch.weekly_planner) and the Experiments block (experiments_brief) leave them out.
Each test builds the report twice - with and without the mcp_ rows - and the two must be identical. No network."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))   # repo root: rec_watch, experiments_brief

import experiments_brief as eb  # noqa: E402
import rec_watch  # noqa: E402

NOW = dt.datetime(2026, 11, 15, 13, 30, tzinfo=dt.timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
DAY = 86400000


def row(uid, kind, h_ago=2, status="ok", **k):
    ms = NOW_MS - int(h_ago * 3600 * 1000)
    return dict({"user_id": uid, "kind": kind, "status": status, "created_at": ms, "cost_usd": 0,
                 "usage_day": dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")}, **k)


PLANNER = [row(1, "opening", latency_ms=8000, cost_usd=0.05), row(2, "refine", latency_ms=9000, counted_as_free=True),
           row(1, "refine", h_ago=30, latency_ms=7000), row(3, "opening", h_ago=9 * 24, latency_ms=6000),
           row(1, "opening", h_ago=8 * 24, latency_ms=6000)]
# MCP rows: new users (50, 51), a free-tier one, a very slow one, many errors, and a "return visit" for user 3.
MCP = ([row(50, "mcp_lookup_venue", latency_ms=90000, tier="free"), row(51, "mcp_get_my_profile", h_ago=30, latency_ms=1),
        row(3, "mcp_estimate_cost", h_ago=3, latency_ms=5), row(50, "mcp_get_my_wedding", h_ago=8 * 24)]
       + [row(50, "mcp_lookup_venue", status="error") for _ in range(12)])


def _by_day(rows):
    return lambda token, day: [r for r in rows if r["usage_day"] == day]


def test_daily_brief_line_ignores_mcp_rows(monkeypatch):
    def build(rows):
        monkeypatch.setattr(rec_watch, "_rows_for_day", _by_day(rows))
        return rec_watch.build("tok", NOW)
    base = build(PLANNER)
    assert "2 users (1 free), 1 opening + 1 refine" in base and ":large_green_circle:" in base and "errors: 0" in base
    assert "median 8.5s" in base
    assert build(PLANNER + MCP) == base          # before: 3 users (2 free), median 9.0s, errors: 12 and a red light


def test_weekly_planner_ignores_mcp_rows(monkeypatch):
    monkeypatch.setattr(rec_watch, "_table_rows", lambda *a, **k: [])
    monkeypatch.setattr(rec_watch, "_themes", lambda qs, key: "")

    def build(rows):
        monkeypatch.setattr(rec_watch, "_rows_for_day", _by_day(rows))
        return rec_watch.weekly_planner("tok", NOW)
    base = build(PLANNER)
    assert "• 2 people used it (1 free) · 1 openings · 2 questions" in base
    assert "Came back: 1 of last week's 2 (50%) · 1 used it on 2+ days this week · 0 asked 0 questions" in base
    assert "• Dead ends: none" in base and "block failed" not in base
    assert build(PLANNER + MCP) == base          # before: 5 people, 3 came back, 3 asked 0 questions, dead ends: error 12


def test_experiments_brief_ignores_mcp_rows():
    first = dt.datetime(2026, 11, 10, 12, tzinfo=dt.timezone.utc)

    def at(uid, kind, days, **k):
        ms = int((first + dt.timedelta(days=days)).timestamp() * 1000)
        return {"user_id": uid, "kind": kind, "status": "ok", "created_at": ms, "usage_day": eb._day(ms), **k}
    planner = [at(60, "refine", 0, counted_as_free=True), at(61, "refine", 0, counted_as_free=True),
               at(61, "refine", 2, counted_as_free=True), at(62, "opening", 4)]
    # User 60 never came back to the Planner; an MCP call two days later must not read as a return day.
    mcp = [at(60, "mcp_get_my_profile", 2), at(60, "mcp_lookup_venue", 4), at(70, "mcp_estimate_cost", 4)]
    base = eb.free_daily(planner, NOW_MS)
    assert (base["post_n"], base["post_back"]) == (2, 1)
    assert eb.free_daily(planner + mcp, NOW_MS) == base          # before: post_back 2
    e = eb.engagement([], planner, NOW_MS)
    assert (e["couples"], e["q"]) == (3, 3)
    assert eb.engagement([], planner + mcp, NOW_MS) == e         # before: 4 couples


def test_gates_ignores_mcp_error_rows():
    """An MCP tool error must not mark a couple as having hit a Planner limit (verifier finding, 2026-11-15)."""
    import experiments_brief as eb
    now = 1_800_000_000_000
    users = [{"id": 501, "email": "a@example.com", "created_at": now - 3 * eb.DAY, "total_pdf_views": 0}]
    rows = [{"user_id": 501, "kind": "mcp_lookup_venue", "status": "error"}]
    assert eb.gates(users, rows, [], now)["a"]["chat"] == 0
    rows.append({"user_id": 501, "kind": "refine", "status": "blocked_free_limit"})
    assert eb.gates(users, rows, [], now)["a"]["chat"] == 1
