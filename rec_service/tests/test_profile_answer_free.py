"""A free user's answer to one of OUR thin-profile questions must not spend a free question (2026-10-26)."""
import pytest
from fastapi import HTTPException

import app
import guards
import xano


@pytest.fixture
def free_user_no_questions_left(monkeypatch):
    monkeypatch.setattr(xano, "beta_member", lambda u: False)
    monkeypatch.setattr(xano, "has_paid_access", lambda u: False)
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(xano, "spend_today", lambda: (0.0, 0.0))
    monkeypatch.setattr(xano, "log_usage", lambda row: None)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(app, "_feedback_given", lambda uid: True)
    monkeypatch.setattr(guards, "decide", lambda *a, **k: (False, "blocked_free_limit", False))


def test_normal_question_is_refused_when_out_of_free_questions(free_user_no_questions_left):
    with pytest.raises(HTTPException) as e:
        app._gate("refine", {"id": 1})
    assert e.value.status_code == 402


def test_profile_answer_is_allowed_and_not_counted(free_user_no_questions_left):
    paid, access, rows, fb, counted_free, base = app._gate("refine", {"id": 1}, waive_free=True)
    assert counted_free is False and paid is False


def test_waive_never_lifts_spend_caps(free_user_no_questions_left, monkeypatch):
    monkeypatch.setattr(guards, "decide", lambda *a, **k: (False, "blocked_free_cap", False))
    with pytest.raises(HTTPException) as e:
        app._gate("refine", {"id": 1}, waive_free=True)
    assert e.value.status_code == 503


def test_counted_question_flag_is_cleared_for_answers(free_user_no_questions_left, monkeypatch):
    monkeypatch.setattr(guards, "decide", lambda *a, **k: (True, "ok", True))
    assert app._gate("refine", {"id": 1}, waive_free=True)[4] is False
    assert app._gate("refine", {"id": 1})[4] is True
