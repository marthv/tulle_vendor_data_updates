"""Planner activity -> the team's Google Sheet (owner 2026-11-03): off without a URL, internal accounts never logged,
rows carry the agreed columns, and the page-only card actions arrive through POST /rec/event."""
import queue

from fastapi.testclient import TestClient

import app as appmod
import sheet_log
import xano


def _capture(monkeypatch):
    q = queue.Queue()
    monkeypatch.setattr(sheet_log, "_Q", q)
    monkeypatch.setattr(sheet_log, "_ensure_worker", lambda: None)
    monkeypatch.setattr(sheet_log, "URL", "https://script.example/exec")
    return q


def test_off_without_url(monkeypatch):
    q = queue.Queue()
    monkeypatch.setattr(sheet_log, "_Q", q)
    monkeypatch.setattr(sheet_log, "URL", "")
    sheet_log.event({"id": 7, "email": "a@b.com"}, "Typed a question", "hi")
    assert q.empty()


def test_internal_accounts_skipped(monkeypatch):
    q = _capture(monkeypatch)
    for u in ({"id": 14998}, {"id": 1, "email": "vivek.marthi+x@gmail.com"}, {"id": 2, "email": "kate@tulletogether.com"}):
        sheet_log.event(u, "Typed a question", "hi")
    assert q.empty()


def test_row_columns(monkeypatch):
    q = _capture(monkeypatch)
    monkeypatch.setattr(xano, "plan_label", lambda u: "free")
    sheet_log.event({"id": 35014, "email": "c@yahoo.com"}, "Typed a question", "Only gardens and nature please",
                    "Here are 3 garden venues", 1170, [{"name": "Planterra Conservatory"}, {"name": "Matthaei"}],
                    {"states": ["Michigan"], "vibes": ["Scenic / Nature Views"], "max_venue_fee": 0},
                    "", latency_ms=25000)
    row = sheet_log._row(q.get_nowait())
    assert row[1:] == ["c@yahoo.com", 35014, "free", 1170, "Typed a question", "Only gardens and nature please",
                       "Here are 3 garden venues", "Planterra Conservatory, Matthaei",
                       "states: Michigan · vibes: Scenic / Nature Views", "slow (25 s)", "", ""]


def test_flags():
    assert sheet_log.flag_of({"lookup": {"status": "not_found"}, "cards": [{}]}) == "not in Tulle"
    assert sheet_log.flag_of({"cards": [], "widgets": []}) == "no vendors shown"
    assert sheet_log.flag_of({"cards": [{"name": "x"}]}) == ""


def test_post_requires_ok(monkeypatch):
    calls = []

    class R:
        status_code = 200
        text = '{"ok": false, "error": "bad secret"}'
    monkeypatch.setattr(sheet_log.requests, "post", lambda *a, **k: calls.append(1) or R())
    monkeypatch.setattr(sheet_log.time, "sleep", lambda s: None)
    assert sheet_log._post([["x"]]) is False and len(calls) == 2


def test_event_endpoint(monkeypatch):
    q = _capture(monkeypatch)
    monkeypatch.setattr(appmod, "_auth", lambda a: ("tok", {"id": 7, "email": "c@d.com"}))
    c = TestClient(appmod.app)
    r = c.post("/rec/event", json={"kind": "vendor_click", "vendor_name": "Kohl Mansion", "chat_id": 3},
               headers={"Authorization": "Bearer x"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    e = q.get_nowait()
    assert (e["action"], e["did"], e["chat_id"], e["vendors"]) == ("Clicked View vendor", "Kohl Mansion", 3, "Kohl Mansion")
    r = c.post("/rec/event", json={"kind": "nonsense"}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 200 and q.empty()


IPHONE_INSTA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Mobile/15E148 Instagram 354.0.0.31.94 (iPhone15,2; iOS 18_1; en_US)")


def test_device_and_browser():
    cases = {
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 "
        "Mobile/15E148 Safari/604.1": ("iPhone", "Safari 18"),
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "CriOS/130.0.6723.90 Mobile/15E148 Safari/604.1": ("iPhone", "Chrome 130"),
        IPHONE_INSTA: ("iPhone", "Instagram in-app 354"),
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
        "[FBAN/FBIOS;FBAV/480.0.0.40.103;]": ("iPhone", "Facebook in-app 480"),
        "Mozilla/5.0 (Linux; Android 14; SAMSUNG SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 "
        "Chrome/122.0.0.0 Mobile Safari/537.36": ("Android phone", "Samsung Internet 26"),
        "Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
        "Safari/537.36": ("Android tablet", "Chrome 130"),
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
        "Safari/537.36 Edg/130.0.2849.68": ("Windows PC", "Edge 130"),
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 "
        "Safari/605.1.15": ("Mac", "Safari 18"),
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:132.0) Gecko/20100101 Firefox/132.0": ("Mac", "Firefox 132"),
        "": ("", ""),
    }
    for ua, want in cases.items():
        assert (sheet_log.device_of(ua), sheet_log.browser_of(ua)) == want, ua


def test_user_agent_reaches_row_inline_and_in_background(monkeypatch):
    """The middleware's User-Agent is seen by an endpoint's own sheet_log.event and by one run as a background task."""
    q = _capture(monkeypatch)
    monkeypatch.setattr(xano, "plan_label", lambda u: "free")
    monkeypatch.setattr(appmod, "_auth", lambda a: ("tok", {"id": 7, "email": "c@d.com"}))
    c = TestClient(appmod.app)
    r = c.post("/rec/event", json={"kind": "vendor_click", "vendor_name": "Kohl Mansion", "chat_id": 3},
               headers={"Authorization": "Bearer x", "User-Agent": IPHONE_INSTA})
    assert r.status_code == 200
    assert sheet_log._row(q.get_nowait())[-2:] == ["iPhone", "Instagram in-app 354"]

    from fastapi import BackgroundTasks

    @appmod.app.post("/__test_bg_sheet")
    def _bg(background: BackgroundTasks):
        background.add_task(sheet_log.event, {"id": 7, "email": "c@d.com"}, "Planner's first message")
        return {"ok": True}
    try:
        c.post("/__test_bg_sheet", headers={"User-Agent": IPHONE_INSTA})
        assert sheet_log._row(q.get_nowait())[-2:] == ["iPhone", "Instagram in-app 354"]
    finally:
        appmod.app.router.routes[:] = [rt for rt in appmod.app.router.routes if getattr(rt, "path", "") != "/__test_bg_sheet"]
    assert sheet_log.UA.get() == ""      # reset after the request
