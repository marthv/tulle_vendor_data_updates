"""POST /rec/notes: a dead-end line becomes the couple's memory note + a quiet Slack line, never a thumbs down (2026-11-01)."""
from fastapi.testclient import TestClient

import app as appmod
import notify
import xano


def _setup(monkeypatch, saved, slack, thumbs):
    monkeypatch.setattr(appmod, "_auth", lambda a: ("tok", {"id": 7, "first_name": "Sam"}))
    monkeypatch.setattr(xano, "plan_label", lambda u: "Forever")
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "add_memory_note", lambda uid, note, cap=20: saved.append((uid, note)))
    monkeypatch.setattr(appmod, "_memory", lambda uid: ([n for _, n in saved], ""))
    monkeypatch.setattr(notify, "still_looking", lambda *a: slack.append(a))
    monkeypatch.setattr(notify, "answer_feedback", lambda *a: thumbs.append(a))

    class Now:   # run the background submit inline
        def submit(self, fn, *a, **k):
            return fn(*a, **k)
    monkeypatch.setattr(appmod, "_POOL", Now())


def test_note_is_saved_and_team_gets_quiet_line(monkeypatch):
    saved, slack, thumbs = [], [], []
    _setup(monkeypatch, saved, slack, thumbs)
    r = TestClient(appmod.app).post("/rec/notes", json={"note": "  a barn   near Austin under $8k ", "source": "dead_end:no_results", "chat_id": 5},
                                    headers={"Authorization": "Bearer x"})
    assert r.status_code == 200
    assert r.json()["note"] == "Still looking for: a barn near Austin under $8k"
    assert saved == [(7, "Still looking for: a barn near Austin under $8k")]
    assert r.json()["notes"] == ["Still looking for: a barn near Austin under $8k"]
    user, text, plan, source, chat = slack[0]
    assert (text, plan, source, chat) == ("a barn near Austin under $8k", "Forever", "dead_end:no_results", 5)
    assert thumbs == []   # never the thumbs-down notice


def test_too_short_is_rejected(monkeypatch):
    saved, slack, thumbs = [], [], []
    _setup(monkeypatch, saved, slack, thumbs)
    r = TestClient(appmod.app).post("/rec/notes", json={"note": " x "}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 400 and saved == [] and slack == []


def test_long_note_is_capped(monkeypatch):
    saved, slack, thumbs = [], [], []
    _setup(monkeypatch, saved, slack, thumbs)
    r = TestClient(appmod.app).post("/rec/notes", json={"note": "y" * 500}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 200 and r.json()["note"] == "Still looking for: " + "y" * 170
