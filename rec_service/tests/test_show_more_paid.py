"""POST /rec/more spends a question exactly like a typed one (user decision 2026-10-07)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402
import xano  # noqa: E402

TODAY = "2026-10-07"


def card(v):
    return {"vendor_id": v, "name": v.upper()}


def answer(picks, more, **kw):
    return dict({"role": "assistant", "text": "picks", "cards": [card(v) for v in picks],
                 "more_cards": [card(v) for v in more], "is_more": False, "chips": []}, **kw)


@pytest.fixture
def env(monkeypatch):
    st = {"usage": [], "logged": [], "added": [], "history": [], "forever": True, "feedback": True, "rows": []}
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "title": "t"} if cid == 9 else None)
    monkeypatch.setattr(xano, "get_messages", lambda cid: [dict(t) for t in st["history"]])
    monkeypatch.setattr(xano, "log_usage", lambda row: st["logged"].append(row))
    monkeypatch.setattr(xano, "add_message", lambda *a: st["added"].append(a))
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "user_usage", lambda uid: list(st["rows"]))
    monkeypatch.setattr(xano, "spend_today_usd", lambda: 0.0)
    monkeypatch.setattr(xano, "has_forever", lambda u: st["forever"])
    monkeypatch.setattr(xano, "has_paid_access", lambda u: st["forever"])
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "today", lambda: TODAY)
    monkeypatch.setattr(app, "_feedback_given", lambda uid: st["feedback"])
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    return st


def test_reveals_next_three_and_spends_one_question(env):
    env["history"] = [answer("abc", "defghi")]
    r = TestClient(app.app).post("/rec/more", json={"chat_id": 9})
    assert r.status_code == 200, r.text
    j = r.json()
    assert [c["vendor_id"] for c in j["cards"]] == list("def") and j["more_available"] == 3
    ok = [x for x in env["logged"] if x["status"] == "ok"]
    assert len(ok) == 1 and ok[0]["kind"] == "refine"          # counted exactly like a typed question
    assert j["forever_left_today"] == config.PAID_DAILY_REFINES - 1
    user_turn, asst_turn = env["added"][0], env["added"][1]
    assert user_turn[1:] == ("user", "Show 3 more")
    assert all(c["via_more"] for c in asst_turn[3])


def test_second_press_continues_then_runs_out(env):
    env["history"] = [answer("abc", "defghi"), {"role": "user", "text": "Show 3 more", "cards": [], "is_more": False},
                      answer("def", [], is_more=True)]
    j = TestClient(app.app).post("/rec/more", json={"chat_id": 9}).json()
    assert [c["vendor_id"] for c in j["cards"]] == list("ghi") and j["more_available"] == 0


def test_nothing_left_is_409_and_costs_nothing(env):
    env["history"] = [answer("abc", "de")]
    r = TestClient(app.app).post("/rec/more", json={"chat_id": 9})
    assert r.status_code == 409 and env["logged"] == [] and env["added"] == []


def test_a_new_typed_answer_replaces_the_pool(env):
    env["history"] = [answer("abc", "defghi"), {"role": "user", "text": "barns", "cards": []}, answer("xyz", [])]
    assert TestClient(app.app).post("/rec/more", json={"chat_id": 9}).status_code == 409


def test_blocked_exactly_like_a_typed_question(env, monkeypatch):
    env["history"] = [answer("abc", "defghi")]
    monkeypatch.setattr(config, "BETA_FEEDBACK_AFTER", 5)
    env["feedback"] = False
    env["rows"] = [{"status": "ok", "kind": "refine", "usage_day": TODAY}] * 5
    r = TestClient(app.app).post("/rec/more", json={"chat_id": 9})
    assert r.status_code == 403 and r.json()["detail"]["status"] == "feedback_required"
    assert env["added"] == [] and not [x for x in env["logged"] if x["status"] == "ok"]


def test_daily_cap_blocks(env):
    env["history"] = [answer("abc", "defghi")]
    env["rows"] = [{"status": "ok", "kind": "refine", "usage_day": TODAY}] * config.PAID_DAILY_REFINES
    env["feedback"] = True
    assert TestClient(app.app).post("/rec/more", json={"chat_id": 9}).status_code == 429


def test_other_users_chat_is_404(env):
    assert TestClient(app.app).post("/rec/more", json={"chat_id": 123}).status_code == 404


def test_chat_history_never_exposes_unrevealed_venues(env):
    env["history"] = [answer("abc", "defghi"), {"role": "user", "text": "Show 3 more", "cards": []},
                      answer("def", [], is_more=True)]
    turns = TestClient(app.app).get("/rec/chats/9").json()["turns"]
    assert all("more_cards" not in t for t in turns)
    assert [t["more_available"] for t in turns] == [0, 0, 3]   # button only under the latest answer


def test_public_response_hides_extras():
    out = {"text": "x", "cards": [card("a")], "more_cards": [card(v) for v in "bcdefg"]}
    pub = app._public(out, chat_id=1)
    assert "more_cards" not in pub and pub["more_available"] == 6 and "more_cards" in out
