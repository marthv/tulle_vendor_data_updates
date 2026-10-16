"""POST /rec/answer-feedback: thumbs-down comments go to Slack with the question + answer (2026-10-16)."""
from fastapi.testclient import TestClient

import app as appmod
import notify
import xano


def _setup(monkeypatch, sent):
    monkeypatch.setattr(appmod, "_auth", lambda a: ("tok", {"id": 7, "first_name": "Sam"}))
    monkeypatch.setattr(xano, "plan_label", lambda u: "Free")
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid} if cid == 5 else None)
    monkeypatch.setattr(xano, "get_messages", lambda cid: [
        {"role": "user", "text": "venues in Texas"}, {"role": "assistant", "text": "Here are 3 in Austin"},
        {"role": "user", "text": "cheaper please"}, {"role": "assistant", "text": "These cost less"}])
    monkeypatch.setattr(notify, "answer_feedback", lambda *a: sent.append(a))

    class Now:   # run the background submit inline
        def submit(self, fn, *a, **k):
            return fn(*a, **k)
    monkeypatch.setattr(appmod, "_POOL", Now())


def test_posts_comment_with_question_and_answer(monkeypatch):
    sent = []
    _setup(monkeypatch, sent)
    r = TestClient(appmod.app).post("/rec/answer-feedback", json={"chat_id": 5, "answer_index": 1, "text": "too pricey"},
                                    headers={"Authorization": "Bearer x"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    user, text, question, answer, plan, chat = sent[0]
    assert (text, question, answer, chat) == ("too pricey", "cheaper please", "These cost less", 5)


def test_empty_text_rejected(monkeypatch):
    sent = []
    _setup(monkeypatch, sent)
    r = TestClient(appmod.app).post("/rec/answer-feedback", json={"chat_id": 5, "answer_index": 0, "text": " "},
                                    headers={"Authorization": "Bearer x"})
    assert r.status_code == 400 and not sent


def test_unknown_chat_still_posts(monkeypatch):
    sent = []
    _setup(monkeypatch, sent)
    r = TestClient(appmod.app).post("/rec/answer-feedback", json={"chat_id": 99, "answer_index": 0, "text": "wrong state"},
                                    headers={"Authorization": "Bearer x"})
    assert r.status_code == 200 and sent[0][2] == "" and sent[0][3] == ""


def test_slack_message_shape(monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "_post", lambda m: posted.append(m) or True)
    notify.answer_feedback({"id": 7, "first_name": "Sam"}, "line one\nline two", "q?", "a" * 500, "Free", 5)
    m = posted[0]
    assert "> line one\n> line two" in m and "*They asked:* q?" in m and m.count("a") >= 390 and "chat 5" in m
