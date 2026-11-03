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
                       "states: Michigan · vibes: Scenic / Nature Views", "slow (25 s)"]


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
