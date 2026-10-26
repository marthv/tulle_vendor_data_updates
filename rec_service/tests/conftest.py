"""Stub the anthropic SDK for the whole suite, before any test module imports app/agent.

Most test files already do this with sys.modules.setdefault, but pytest collects test_answer_feedback.py
first and it imports app with the REAL SDK installed, so the later stubs were no-ops and the fake tool
runners saw BetaFunctionTool objects (no __name__): 20 order-dependent failures on a machine with
anthropic installed. test_free_mode checks the real beta_tool path in a fresh subprocess, unaffected."""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)
