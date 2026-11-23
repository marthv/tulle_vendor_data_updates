"""The sign-up flow's one free question (REC_ONBOARDING, 2026-11-23). No network, no model."""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import pytest  # noqa: E402

import app  # noqa: E402
import config  # noqa: E402
import xano  # noqa: E402


def body(**kw):
    return app.RefineBody(message="what is included?", **kw)


@pytest.mark.parametrize("flag,onb,rows,free", [
    (True, True, [], True),
    (True, True, [{"kind": "opening"}, {"kind": "tool_estimate_cost"}], True),
    (True, True, [{"kind": "refine", "status": "ok"}], False),       # once per account
    (True, True, [{"kind": "refine", "status": "error"}], False),
    (True, False, [], False),                                         # an ordinary question is counted
    (False, True, [], False),                                         # flag off
])
def test_free_once(monkeypatch, flag, onb, rows, free):
    monkeypatch.setattr(config, "ONBOARDING", flag)
    monkeypatch.setattr(xano, "user_usage", lambda uid: rows)
    assert app._onboarding_free({"id": 5}, body(onboarding=onb)) is free


def test_a_failed_read_is_never_free(monkeypatch):
    monkeypatch.setattr(config, "ONBOARDING", True)

    def boom(uid):
        raise xano.XanoError("down")
    monkeypatch.setattr(xano, "user_usage", boom)
    assert app._onboarding_free({"id": 5}, body(onboarding=True)) is False


def test_the_gate_gets_it(monkeypatch):
    seen = {}

    def guarded(kind, token, user, msgs, **kw):
        seen.update(kw, msgs=msgs)
        raise RuntimeError("stop here")
    monkeypatch.setattr(config, "ONBOARDING", True)
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(app, "_lookup_fast", lambda *a, **k: None)
    monkeypatch.setattr(app, "_recent_chat", lambda uid: None)
    monkeypatch.setattr(app, "_run_guarded", guarded)
    b = body(onboarding=True, messages=[{"role": "user", "content": "Show me venues"}, {"role": "assistant", "content": "Alpha, Beta"}])
    with pytest.raises(RuntimeError):
        app._refine("t", {"id": 5}, b, None, None)
    assert seen["waive_free"] is True and [m["role"] for m in seen["msgs"]] == ["user", "assistant", "user"]
