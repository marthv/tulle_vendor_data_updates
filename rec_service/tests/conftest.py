"""Shared test setup. The cross-request pricing cache (xano.PRICING_CACHE, 2026-10-26) is OFF in every test unless
the test turns it on, so one test's stubbed pricing can never be served to another."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xano  # noqa: E402


@pytest.fixture(autouse=True)
def _pricing_cache_off(monkeypatch):
    xano.PRICING_CACHE.clear()
    monkeypatch.setattr(xano.PRICING_CACHE, "ttl", 0)
    yield
    xano.PRICING_CACHE.clear()
