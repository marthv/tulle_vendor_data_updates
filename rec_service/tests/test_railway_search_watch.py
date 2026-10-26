"""Daily brief "Railway search (assistant)" block (rec_watch.railway_section, 2026-10-26): formatting, verdict rules,
crash safety, and the daily_brief wiring. No network."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))   # repo root: rec_watch, daily_brief

import rec_watch as rw  # noqa: E402

NOW = dt.datetime(2026, 10, 27, 13, 32, tzinfo=dt.timezone.utc)
MS = lambda h_ago: int((NOW - dt.timedelta(hours=h_ago)).timestamp() * 1000)   # noqa: E731
HEALTH = {"ok": True, "search_enabled": True,
          "sync": {"last_sync_at": "2026-10-27T08:34:42Z", "last_sync_status": "ok"}}


def row(engine, search_ms=150, latency=9000, status="ok", fallback="", uid=1, h_ago=2):
    return {"search_engine": engine, "search_ms": search_ms, "latency_ms": latency, "status": status,
            "search_fallback": fallback, "user_id": uid, "created_at": MS(h_ago)}


def rows(n_rail, n_ep, rail_ms=150, ep_ms=1800, fallbacks=0, rail_errors=0, ep_errors=0, h_ago=2):
    out = []
    for i in range(n_rail):
        fb = i < fallbacks
        out.append(row("mixed" if fb else "railway", rail_ms, status="error" if i < rail_errors else "ok",
                       fallback="timeout" if fb else "", uid=i % 7, h_ago=h_ago))
    for i in range(n_ep):
        out.append(row("ep119", ep_ms, status="error" if i < ep_errors else "ok", uid=100 + i % 40, h_ago=h_ago))
    return out


def verdict_of(rs):
    return rw.railway_block(rs, HEALTH, NOW)[0][-1]


def test_collecting_data_under_20():
    assert "collecting data (19/20" in verdict_of(rows(19, 200))


def test_hold_on_fallback_rate():
    v = verdict_of(rows(40, 200, fallbacks=7))            # 17.5% > 15%
    assert v.startswith(":red_circle:") and "REC_RAILWAY_SEARCH_PCT=0" in v


def test_hold_on_error_rate_over_ep119_plus_3pp():
    assert verdict_of(rows(40, 200, rail_errors=3, ep_errors=4)).startswith(":red_circle:")   # 7.5% vs 2% + 3pp
    assert not verdict_of(rows(40, 200, rail_errors=1, ep_errors=4)).startswith(":red_circle:")  # 2.5% vs 2%


def test_green_needs_50_requests_low_fallback_and_30pct_faster():
    assert verdict_of(rows(60, 200, rail_ms=150, ep_ms=1800, fallbacks=2)).startswith(":large_green_circle:")
    assert verdict_of(rows(49, 200, rail_ms=150, ep_ms=1800)).startswith(":large_yellow_circle:")   # < 50 req
    assert verdict_of(rows(60, 200, rail_ms=1300, ep_ms=1800)).startswith(":large_yellow_circle:")  # only 28% faster
    assert verdict_of(rows(60, 200, rail_ms=150, ep_ms=1800, fallbacks=4)).startswith(":large_yellow_circle:")  # 6.7%
    assert verdict_of(rows(60, 0)).startswith(":large_yellow_circle:")     # no ep119 baseline to compare


def test_block_format_counts_mixed_with_railway_and_lists_it():
    rs = rows(30, 120, fallbacks=2) + rows(5, 10, h_ago=50)   # some older than 24h
    lines, tldr, light = rw.railway_block(rs, HEALTH, NOW)
    text = "\n".join(lines)
    assert lines[0] == "*Railway search (assistant)*"
    assert "_Last 24h_" in text and "_Since start (Oct 25 11:32 UTC)_" in text
    assert "Railway: 30 req (2 mixed), 7 users" in text and "fallbacks 2 (timeout 2)" in text
    assert "Railway: 35 req (2 mixed)" in text                    # since start includes the older rows
    assert "Index freshness: last sync 297 min ago" in text
    assert tldr.startswith(light) and "Railway search:" in tldr and "35 Railway req since start" in tldr


def test_ep119_rows_before_rollout_start_are_left_out():
    rs = rows(25, 0, h_ago=2) + [row("ep119", h_ago=100 + i) for i in range(30)]
    text = "\n".join(rw.railway_block(rs, HEALTH, NOW)[0])
    assert "ep119: no requests" in text


def test_no_railway_rows_yet():
    lines, tldr, light = rw.railway_block([row("ep119")], HEALTH, NOW)
    assert "No Railway-arm venue searches yet" in lines[1] and "collecting data" in tldr


def test_freshness_variants():
    assert "unavailable" in rw.freshness(None, NOW)
    stale = {"sync": {"last_sync_at": "2026-10-25T08:00:00Z", "last_sync_status": "failed"}, "search_enabled": False}
    f = rw.freshness(stale, NOW)
    assert "over 26h" in f and "last sync failed" in f and "SEARCH_ENABLED=0" in f


def test_section_never_raises():
    def boom():
        raise RuntimeError("xano down")
    lines, tldr, light = rw.railway_section("t", NOW, get_rows=boom, get_health=lambda: HEALTH)
    assert lines == ["*Railway search (assistant)* - unavailable (xano down)"] and light is None
    lines, _, _ = rw.railway_section("t", NOW, get_rows=lambda: rows(25, 50), get_health=boom)
    assert "Index freshness: unavailable" in "\n".join(lines)
    # empty / junk columns
    junk = [{"search_engine": "railway", "created_at": None, "search_ms": None, "latency_ms": None}, {}]
    lines, _, _ = rw.railway_section("t", NOW, get_rows=lambda: junk, get_health=lambda: {"weird": 1})
    assert lines[0].startswith("*Railway search (assistant)*")
    lines, _, _ = rw.railway_section("t", NOW, get_rows=lambda: [], get_health=lambda: None)
    assert "No Railway-arm venue searches yet" in lines[1]


def test_search_rows_scans_back_to_the_deploy():
    days = {"2026-10-27": [row("railway")], "2026-10-26": [row("ep119"), {"status": "ok"}],
            "2026-10-25": [{"status": "ok"}], "2026-10-24": [row("railway")]}
    asked = []
    got = rw.search_rows("t", NOW, fetch=lambda d: asked.append(d) or days.get(d, []))
    assert len(got) == 2 and asked == ["2026-10-27", "2026-10-26", "2026-10-25"]   # stops at the untagged day


def test_daily_brief_carries_block_and_one_tldr(monkeypatch):
    import daily_brief
    monkeypatch.setattr(daily_brief, "payments", lambda t, n: {"rev24": 400, "n24": 9, "rev7": 380, "forever24": 2})
    monkeypatch.setattr(daily_brief, "signups", lambda t, n: (100, 95))
    monkeypatch.setattr(daily_brief, "nps", lambda t, n: {"n24": 1, "avg7": 4.5, "n7": 6, "comments24": 0})
    monkeypatch.setattr(daily_brief, "rec_line", lambda t: ("*Tulle Assistant* - 3 users, 2 opening + 4 refine", []))
    import bnpl_watch
    monkeypatch.setattr(bnpl_watch, "build", lambda *a, **k: "pricing test")
    monkeypatch.setattr(rw, "railway_section",
                        lambda t: rw.railway_block(rows(40, 200, fallbacks=7), HEALTH, NOW))
    out = daily_brief.compose("t", "health ok", now_ms=MS(0))
    tl = out.split("*TL;DR*")[1].split("*Decisions")[0]
    assert tl.count("Railway search:") == 1 and "*Railway search (assistant)*" in out
    assert "consider REC_RAILWAY_SEARCH_PCT=0 on rec-engine" in out                    # red -> a decision
    assert out.index("*Tulle Assistant*") < out.index("*Railway search (assistant)*")

    def boom(t):
        raise RuntimeError("x")
    monkeypatch.setattr(rw, "railway_section", boom)
    out = daily_brief.compose("t", "health ok", now_ms=MS(0))
    assert "Railway search section failed" in out and "*Tulle Assistant*" in out
