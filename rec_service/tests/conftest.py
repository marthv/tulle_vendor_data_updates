"""Shared test setup.

1. Stub the anthropic SDK for the whole suite, before any test module imports app/agent. pytest collects
   test_answer_feedback.py first and it imports app with the REAL SDK installed, so later per-file stubs were
   no-ops and the fake tool runners saw BetaFunctionTool objects (no __name__): 20 order-dependent failures on a
   machine with anthropic installed. test_free_mode checks the real beta_tool path in a fresh subprocess.
2. The cross-request pricing cache (xano.PRICING_CACHE, 2026-10-26) is OFF in every test unless the test turns
   it on, so one test's stubbed pricing can never be served to another."""
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import xano  # noqa: E402


@pytest.fixture(autouse=True)
def _pricing_cache_off(monkeypatch):
    # Same for the prefetch caches (2026-10-27) and the opening reuse store: off unless a test turns them on.
    caches = (xano.PRICING_CACHE, xano.BENCH_CACHE, xano.CARD_CACHE)
    for c in caches:
        c.clear()
        monkeypatch.setattr(c, "ttl", 0)
    import config
    import opening_reuse
    import railway_search
    opening_reuse.STORE.clear()
    monkeypatch.setattr(config, "OPENING_REUSE", False)
    # Streamed answer text (2026-10-31) is off unless a test turns it on: the fake tool runners yield finished
    # messages, not event streams.
    monkeypatch.setattr(config, "STREAM_TEXT", False)
    # Open planner (2026-10-31) is off in the suite unless a test turns it on: the free-tier tests below cover
    # the lifetime rules; tests/test_free_daily.py covers the daily allowance.
    monkeypatch.setattr(config, "FREE_DAILY_MODE", False)
    railway_search.BREAKER.reset()
    yield
    for c in caches:
        c.clear()
    opening_reuse.STORE.clear()
    railway_search.BREAKER.reset()


@pytest.fixture(autouse=True)
def _recent_opening_clear():
    # app._RECENT_OPENING / _USED_CHATS (one chat per opening, 2026-11-07) are module-level: never let one test's
    # opening be "the same opening" in another.
    import app
    app._RECENT_OPENING.clear()
    app._USED_CHATS.clear()
    yield
