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
    xano.PRICING_CACHE.clear()
    monkeypatch.setattr(xano.PRICING_CACHE, "ttl", 0)
    yield
    xano.PRICING_CACHE.clear()
