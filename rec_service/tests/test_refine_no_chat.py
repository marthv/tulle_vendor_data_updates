"""POST /rec/refine without a chat_id is saved now (2026-10-12, user 827: 3 rated questions were lost and each
started from scratch, so the state named in one was forgotten by the next)."""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import agent  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402
import xano  # noqa: E402

TODAY = "2026-10-12"


@pytest.fixture
def env(monkeypatch):
    st = {"logged": [], "added": [], "created": [], "chats": [], "history": {}, "model_msgs": []}

    def run(token, user, access, messages, **kw):
        st["model_msgs"].append([dict(m) for m in messages])
        return ({"text": "picks", "cards": [{"vendor_id": "V1", "name": "A"}], "more_cards": [], "chips": ["x"]},
                {"model": "m", "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "output_tokens": 1, "tool_calls": 0, "latency_ms": 1})

    def create_chat(uid, title):
        st["created"].append(title)
        return {"id": 50 + len(st["created"]), "user_id": uid, "title": title}

    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    monkeypatch.setattr(xano, "create_chat", create_chat)
    monkeypatch.setattr(xano, "list_chats", lambda uid: list(st["chats"]))
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "title": "t"})
    monkeypatch.setattr(xano, "get_messages", lambda cid: [dict(t) for t in st["history"].get(cid, [])])
    monkeypatch.setattr(xano, "add_message", lambda *a: st["added"].append(a))
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "log_usage", lambda row: st["logged"].append(row))
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(xano, "spend_today", lambda: (0.0, 0.0))
    monkeypatch.setattr(xano, "has_forever", lambda u: True)
    monkeypatch.setattr(xano, "beta_member", lambda u: True)
    monkeypatch.setattr(xano, "has_paid_access", lambda u: True)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "today", lambda: TODAY)
    monkeypatch.setattr(app, "_feedback_given", lambda uid: True)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    return st


def test_first_question_creates_a_chat_and_saves_both_turns(env):
    r = TestClient(app.app).post("/rec/refine", json={"messages": [{"role": "user", "content": "New York venues"}]})
    assert r.status_code == 200, r.text
    assert r.json()["chat_id"] == 51 and env["created"] == ["New York venues"]
    assert [a[1:3] for a in env["added"]] == [("user", "New York venues"), ("assistant", "picks")]


def test_next_question_continues_the_recent_chat_with_server_history(env):
    now = int(time.time() * 1000)
    env["chats"] = [{"id": 51, "title": "New York venues", "updated_at": now - 60_000}]
    env["history"][51] = [{"role": "user", "text": "New York venues", "cards": []},
                          {"role": "assistant", "text": "picks", "cards": [{"vendor_id": "V1", "name": "A"}]}]
    r = TestClient(app.app).post("/rec/refine", json={"messages": [{"role": "user", "content": "cheaper ones"}]})
    assert r.status_code == 200 and r.json()["chat_id"] == 51 and env["created"] == []
    sent = env["model_msgs"][-1]
    assert sent[0] == {"role": "user", "content": "New York venues"}      # no OPENING_ASK before a user turn
    assert sent[-1] == {"role": "user", "content": "cheaper ones"}


def test_stale_chat_is_not_reused(env):
    env["chats"] = [{"id": 40, "title": "old", "updated_at": int(time.time() * 1000) - 2 * 3600_000}]
    r = TestClient(app.app).post("/rec/refine", json={"message": "barns in Texas"})
    assert r.status_code == 200 and r.json()["chat_id"] == 51 and env["created"] == ["barns in Texas"]


def test_blocked_question_creates_no_chat(env, monkeypatch):
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    r = TestClient(app.app).post("/rec/refine", json={"message": "barns"})
    assert r.status_code == 503 and env["created"] == [] and env["added"] == []


def test_bad_shape_still_400(env):
    r = TestClient(app.app).post("/rec/refine", json={"messages": [{"role": "assistant", "content": "hi"}]})
    assert r.status_code == 400
