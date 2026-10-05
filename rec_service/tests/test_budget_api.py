"""/budget/plan endpoints against an in-memory fake of Xano - no network, no AI."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as app_mod  # noqa: E402
import budget  # noqa: E402
import config  # noqa: E402
import xano  # noqa: E402
from test_budget import SHARES  # noqa: E402

USERS = {"tokA": {"id": 1, "Wedding_Budget": 50000, "forever": True},
         "tokB": {"id": 2, "Wedding_Budget": 30000, "forever": True},
         "tokFree": {"id": 3, "Wedding_Budget": 30000, "forever": False},
         "tokNoBudget": {"id": 4, "Wedding_Budget": 0, "forever": True}}


@pytest.fixture
def client(monkeypatch):
    store = {}
    monkeypatch.setattr(config, "BUDGET_PLAN_ON", True)
    monkeypatch.setattr(config, "FOREVER_ONLY", True)
    monkeypatch.setattr(config, "BUDGET_RIPPLE", {})
    monkeypatch.setattr(xano, "verify_user", lambda t: USERS.get(t))
    monkeypatch.setattr(xano, "has_forever", lambda u: u["forever"])
    monkeypatch.setattr(xano, "get_budget_plan", lambda uid: store.get(uid))
    monkeypatch.setattr(xano, "set_budget_plan", lambda uid, s: store.__setitem__(uid, s) or s)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(budget, "load_shares", lambda ttl=3600: SHARES)
    c = TestClient(app_mod.app)
    c.store = store
    return c


def H(tok):
    return {"Authorization": "Bearer " + tok}


def test_default_plan_is_typical_and_whole_budget(client):
    p = client.get("/budget/plan", headers=H("tokA")).json()
    assert p["focus"] == "typical" and p["total"] == 50000 and p["over_by"] == 0


def test_save_then_read_back(client):
    r = client.post("/budget/plan", headers=H("tokA"),
                    json={"focus": "venue", "excluded": ["Wedding Planner"], "edits": {"Venue": 20000}})
    assert r.status_code == 200 and r.json()["over_by"] > 0
    again = client.get("/budget/plan", headers=H("tokA")).json()
    assert again["focus"] == "venue" and again["excluded"] == ["Wedding Planner"]
    assert again["edits"] == {"Venue": 20000}


def test_plans_are_per_user(client):
    client.post("/budget/plan", headers=H("tokA"), json={"focus": "flowers", "excluded": ["Beauty"]})
    b = client.get("/budget/plan", headers=H("tokB")).json()
    assert b["focus"] == "typical" and b["excluded"] == [] and b["total"] == 30000
    assert 2 not in client.store


def test_off_switch_returns_503(client, monkeypatch):
    monkeypatch.setattr(config, "BUDGET_PLAN_ON", False)
    assert client.get("/budget/plan", headers=H("tokA")).status_code == 503


def test_not_signed_in_and_not_forever(client):
    assert client.get("/budget/plan", headers=H("nope")).status_code == 401
    assert client.get("/budget/plan", headers=H("tokFree")).status_code == 402


def test_no_budget_asks_for_one(client):
    r = client.get("/budget/plan", headers=H("tokNoBudget"))
    assert r.status_code == 400 and r.json()["detail"]["status"] == "budget_required"


def test_bad_input_is_400_and_not_saved(client):
    r = client.post("/budget/plan", headers=H("tokA"), json={"focus": "luxury"})
    assert r.status_code == 400 and 1 not in client.store
