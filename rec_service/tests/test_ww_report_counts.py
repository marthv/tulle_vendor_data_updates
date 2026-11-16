"""Whole-wedding proposals (rec_usage kind "ww_proposal", 2026-11-15) must not move any existing report number: a plan
assembled in code is not a typed question. Each test builds a report twice - with and without the ww rows - and every
line that existed before must be identical; the proposals show up only on their own new line. A third build runs the
SAME rows through origin/main's report code (old vs new) to prove the new code changes nothing when there are no ww
rows. No network."""
import datetime as dt
import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))   # repo root: rec_watch, experiments_brief, ab_report

import ab_report  # noqa: E402
import experiments_brief as eb  # noqa: E402
import rec_watch  # noqa: E402

NOW = dt.datetime(2026, 11, 15, 13, 30, tzinfo=dt.timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def row(uid, kind, h_ago=2, status="ok", **k):
    ms = NOW_MS - int(h_ago * 3600 * 1000)
    return dict({"user_id": uid, "kind": kind, "status": status, "created_at": ms, "cost_usd": 0,
                 "usage_day": dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")}, **k)


PLANNER = [row(1, "opening", latency_ms=8000, cost_usd=0.05), row(2, "refine", latency_ms=9000, counted_as_free=True, tool_calls=4),
           row(1, "refine", h_ago=30, latency_ms=7000, categories="venue"), row(3, "opening", h_ago=9 * 24, latency_ms=6000),
           row(1, "opening", h_ago=8 * 24, latency_ms=6000), row(2, "refine", latency_ms=300, model="lookup", tools="lookup:found"),
           row(4, "tool_estimate_cost", h_ago=5), row(2, "pdf", status="free", h_ago=1)]
# Proposals: a new couple (60), a free one (61, tier free + counted), a very slow one, a paid one with categories, an
# old one that would read as a "return visit" for couple 3, an error, and one from the Ask Tulle bubble.
WW = [row(60, "ww_proposal", latency_ms=41000, model="whole_wedding", tools="ww:start,arm:ww", tool_calls=11,
          categories="venue,photography,flowers"),
      row(61, "ww_proposal", latency_ms=38000, model="whole_wedding", counted_as_free=True, tier="free", tool_calls=11),
      row(1, "ww_proposal", latency_ms=35000, model="whole_wedding", tools="ww:cheaper,arm:ww", tool_calls=11, categories="venue"),
      row(3, "ww_proposal", h_ago=3, latency_ms=30000, model="whole_wedding", tool_calls=11),
      row(60, "ww_proposal", h_ago=8 * 24, latency_ms=30000, model="whole_wedding"),
      row(62, "ww_proposal", status="error", model="whole_wedding"),
      row(63, "ww_proposal", latency_ms=20000, model="whole_wedding", surface="bubble:/vendor", counted_as_free=True)]


def _by_day(rows):
    return lambda token, day: [r for r in rows if r["usage_day"] == day]


def _without_ww_line(text):
    return "\n".join(l for l in text.split("\n") if not l.startswith("• Whole-wedding plans"))


def _origin(name):
    """origin/main's copy of a repo-root report module, loaded under another name (skip when git can't give it)."""
    try:
        src = subprocess.run(["git", "show", "origin/main:%s.py" % name], cwd=ROOT, capture_output=True, text=True,
                             encoding="utf-8", timeout=20)
    except Exception:
        pytest.skip("git not available")
    if src.returncode != 0 or not src.stdout:
        pytest.skip("origin/main not available")
    spec = importlib.util.spec_from_loader("_origin_" + name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(ROOT / (name + ".py"))
    exec(compile(src.stdout, name + "@origin/main", "exec"), mod.__dict__)
    return mod


def test_daily_brief_line_unchanged(monkeypatch):
    def build(mod, rows):
        monkeypatch.setattr(mod, "_rows_for_day", _by_day(rows))
        return mod.build("tok", NOW)
    base = build(rec_watch, PLANNER)
    assert "users (1 free), 1 opening + 2 refine" in base and "errors: 0" in base
    with_ww = build(rec_watch, PLANNER + WW)
    # every existing number untouched (without the filter: more users, a free one, a slower median, an error) ...
    assert _without_ww_line(with_ww) == base and with_ww != base
    # ... and the flow's own rows of the last 24h - its ERRORS included - on a line of their own (fix 8, 2026-11-16)
    assert with_ww.split("\n")[-1] == "• Whole-wedding plans: 5 by 5 couples (2 used a free question) · p50 35.0s · errors 1"
    assert build(_origin("rec_watch"), PLANNER) == base    # old code, same rows: the new code changed nothing


def test_watch_24h_view_surfaces_whole_wedding_errors(monkeypatch):
    """The 24h watch line used to drop every ww_ row, so a failing new flow was invisible there."""
    def build(rows):
        monkeypatch.setattr(rec_watch, "_rows_for_day", _by_day(rows))
        return rec_watch.build("tok", NOW)
    errs = [row(70 + i, "ww_proposal", status="error", model="whole_wedding", h_ago=1 + i) for i in range(4)]
    out = build(PLANNER + errs)
    assert "errors: 0" in out.split("\n")[1]                                    # the Planner's own error count: unmoved
    assert out.split("\n")[-1] == "• Whole-wedding plans: 0 by 0 couples (0 used a free question) · p50 0.0s · errors 4"
    assert build(PLANNER) == _without_ww_line(out) and "Whole-wedding" not in build(PLANNER)
    # nothing but the new flow in the window: still not silent about its errors
    only = build(errs + [row(80, "ww_proposal", h_ago=30, status="error")])     # the 30h-old one is outside the window
    assert only == ("*Recommendation engine* (last 24h) - no usage yet.\n"
                    "• Whole-wedding plans: 0 by 0 couples (0 used a free question) · p50 0.0s · errors 4")
    assert build([]) == "*Recommendation engine* (last 24h) - no usage yet."


def test_weekly_planner_unchanged_plus_its_own_line(monkeypatch):
    def build(mod, rows):
        monkeypatch.setattr(mod, "_table_rows", lambda *a, **k: [])
        monkeypatch.setattr(mod, "_themes", lambda qs, key: "")
        monkeypatch.setattr(mod, "_rows_for_day", _by_day(rows))
        return mod.weekly_planner("tok", NOW)
    base = build(rec_watch, PLANNER)
    assert "· 1 openings · 3 questions · 1 cost-tool runs" in base and "block failed" not in base
    with_ww = build(rec_watch, PLANNER + WW)
    assert _without_ww_line(with_ww) == base and with_ww != base
    assert "• Whole-wedding plans: 5 by 5 couples (2 used a free question) · p50 35.0s · errors 1" in with_ww
    assert build(_origin("rec_watch"), PLANNER) == base


def test_planner_daily_unchanged_plus_its_own_line(monkeypatch):
    def build(mod, rows):
        monkeypatch.setattr(mod, "_table_rows", lambda *a, **k: [])
        monkeypatch.setattr(mod, "_rows_for_day", _by_day(rows))
        return mod.planner_daily("tok", NOW)
    base = build(rec_watch, PLANNER)
    assert "1 openings · 2 questions" in base and "Typed question (model): 1 · p50 9.0s" in base
    assert "Questions needing 3+ tool calls: 1 of 1" in base and "block failed" not in base
    with_ww = build(rec_watch, PLANNER + WW)
    assert _without_ww_line(with_ww) == base and with_ww != base      # speed lines, 3+ tool calls, categories: untouched
    assert "• Whole-wedding plans: 5 by 5 couples (2 used a free question) · p50 35.0s · errors 1" in with_ww
    assert build(_origin("rec_watch"), PLANNER) == base


def test_experiments_brief_unchanged():
    first = dt.datetime(2026, 11, 10, 12, tzinfo=dt.timezone.utc)

    def at(uid, kind, days, **k):
        ms = int((first + dt.timedelta(days=days)).timestamp() * 1000)
        return {"user_id": uid, "kind": kind, "status": "ok", "created_at": ms, "usage_day": eb._day(ms), **k}
    planner = [at(60, "refine", 0, counted_as_free=True), at(61, "refine", 0, counted_as_free=True),
               at(61, "refine", 2, counted_as_free=True), at(62, "opening", 4), at(63, "refine", 3, surface="bubble:/x"),
               at(64, "refine", 4, surface="planner")]
    # Couple 60 never came back to ask; a proposal two days later must not read as a return day, a free question, a
    # bubble couple or a blocked couple.
    wws = [at(60, "ww_proposal", 2, counted_as_free=True), at(70, "ww_proposal", 4, counted_as_free=True),
           at(71, "ww_proposal", 4, surface="bubble:/x"), dict(at(72, "ww_proposal", 4), status="error"),
           at(65, "ww_proposal", 4, counted_as_free=True)]
    old = _origin("experiments_brief")
    for fn, args in (("free_daily", (NOW_MS,)), ("bubble", (NOW_MS,))):
        base = getattr(eb, fn)(planner, *args)
        assert getattr(eb, fn)(planner + wws, *args) == base, fn
        assert getattr(old, fn)(planner, *args) == base, fn
    base = eb.engagement([], planner, NOW_MS)
    assert eb.engagement([], planner + wws, NOW_MS) == base == old.engagement([], planner, NOW_MS)
    users = [{"id": 72, "email": "a@example.com", "created_at": NOW_MS - 3 * eb.DAY, "total_pdf_views": 0}]
    base = eb.gates(users, planner, [], NOW_MS)
    assert eb.gates(users, planner + wws, [], NOW_MS) == base == old.gates(users, planner, [], NOW_MS)


def test_ab_report_unchanged():
    rows = PLANNER + [row(5, "refine"), row(5, "pdf", status="paid")]
    base = ab_report.compute(rows, [], [])
    assert ab_report.compute(rows + WW, [], []) == base
    assert _origin("ab_report").compute(rows, [], []) == base
