"""'My wedding' plan + A/B arm (owner 2026-11-07)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import app  # noqa: E402
import plan  # noqa: E402
import xano  # noqa: E402


def test_arm_is_stable_and_splits_near_half(monkeypatch):
    monkeypatch.setattr(plan, "PLAN_AB_PCT", 50)
    arms = [plan.arm(u) for u in range(1, 4001)]
    assert arms == [plan.arm(u) for u in range(1, 4001)]                 # same couple, same arm
    assert 0.46 < arms.count("plan") / len(arms) < 0.54
    monkeypatch.setattr(plan, "PLAN_AB_PCT", 0)
    assert plan.arm(123) == "control"
    monkeypatch.setattr(plan, "ALWAYS_PLAN", {14998})
    assert plan.arm(14998) == "plan"                                      # internal accounts always see it


ROWS = [{"id": 1, "slot": "Venue", "vendor_id": "V1675", "vendor_name": "Kohl Mansion", "est_cost": 46018,
         "status": "active", "created_at": 1},
        {"id": 2, "slot": "Photography", "vendor_id": "P1", "vendor_name": "Jane Doe Photography", "est_cost": 0,
         "status": "removed", "created_at": 2}]


def test_summary_has_every_slot_and_hides_prices_for_free():
    s = plan.summary(ROWS, budget=60000)
    assert [x["slot"] for x in s["slots"]][:2] == ["Venue", "Photography"] and s["filled"] == 1 and s["count"] == 1
    assert s["total_est"] == 46018 and s["budget"] == 60000 and s["slots"][1]["items"] == []   # removed row hidden
    free = plan.summary(ROWS, budget=60000, show_prices=False)
    assert free["total_est"] is None and free["slots"][0]["items"][0]["est_cost"] is None


def test_next_step_only_once_engaged_and_skips_filled_slots():
    assert plan.next_step([], 1) is None
    assert plan.next_step([], 3)["slot"] == "Venue"
    n = plan.next_step(ROWS, 0)
    assert n["slot"] == "Photography" and "photographer" in n["ask"].lower()


def test_context_line_names_what_is_in_the_plan():
    line = plan.context_line(ROWS)
    assert "Venue: Kohl Mansion" in line and "Photographer: open" in line


@pytest.fixture
def api(monkeypatch):
    store = {"rows": [dict(r) for r in ROWS], "mp": []}
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7, "Wedding_Budget": 60000}))
    monkeypatch.setattr(plan, "PLAN_AB_PCT", 100)
    monkeypatch.setattr(xano, "has_paid_access", lambda u: True)
    monkeypatch.setattr(xano, "plan_items", lambda uid: [r for r in store["rows"] if r["status"] == "active"])

    def add(row):
        row = dict(row, id=len(store["rows"]) + 1, created_at=99)
        store["rows"].append(row)
        return row
    monkeypatch.setattr(xano, "plan_add", add)

    def remove(uid, item_id):
        for r in store["rows"]:
            if r["id"] == item_id:
                r["status"] = "removed"
                return True
        return False
    monkeypatch.setattr(xano, "plan_remove", remove)
    monkeypatch.setattr(xano, "mp_track", lambda uid, ev, props: store["mp"].append((ev, props)))
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)
    return store


def test_add_then_remove(api):
    c = TestClient(app.app)
    j = c.post("/rec/plan/add", json={"vendor_id": "P9", "vendor_name": "Bloom", "category": "Flowers",
                                      "est_cost": 3000}).json()
    assert j["added"] and j["enabled"] and j["count"] == 2
    assert any(s["slot"] == "Flowers" and s["items"][0]["name"] == "Bloom" for s in j["slots"])
    again = c.post("/rec/plan/add", json={"vendor_id": "P9", "vendor_name": "Bloom", "category": "Flowers"}).json()
    assert again["already"] and again["count"] == 2
    item = next(s for s in j["slots"] if s["slot"] == "Flowers")["items"][0]["id"]
    r = c.post("/rec/plan/remove", json={"item_id": item}).json()
    assert r["removed"] and r["count"] == 1
    assert [e for e, _ in api["mp"]][:1] == ["rec_plan_add"]


def test_add_needs_something(api):
    assert TestClient(app.app).post("/rec/plan/add", json={}).status_code == 400


def test_everyone_gets_the_plan_by_default():
    import importlib, os
    src = (pathlib.Path(__file__).resolve().parent.parent / "plan.py").read_text(encoding="utf-8")
    assert 'os.environ.get("REC_PLAN_AB_PCT", "100")' in src


def test_compact_brief_shows_adoption_when_everyone_has_the_plan():
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
    import ab_report
    s = {"control": {"n": 0, "q": 0, "pdf_couples": 0, "bought": 0},
         "plan": {"n": 50, "q": 120, "pdf_couples": 20, "bought": 2, "adds": 30, "adders": 15, "two_slots": 6}}
    out = ab_report.format_block(s, 3)
    assert "My wedding plan" in out and "15 added to their plan (30%)" in out and "A/B" not in out
