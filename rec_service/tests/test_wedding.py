"""'My wedding' (owner 2026-11-08): one screen from the approved prototype."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import app  # noqa: E402
import wedding  # noqa: E402
import xano  # noqa: E402

USER = {"id": 7, "Wedding_Budget": 45000, "favorited_vendors": ["V1", "P1", "F9", "B1"]}
CARDS = {"V1": {"vendor_id": "V1", "name": "The Houston Club", "category": "Venue", "venue_fee_range": [12000, 15000],
                "venue_type": "Ballroom", "state": "Texas"},
         "P1": {"vendor_id": "P1", "name": "OliviaPhotos", "category": "Photography", "price_range": [2000, 4000]},
         "F9": {"vendor_id": "F9", "name": "Bloomery", "category": "Flowers"},
         "B1": {"vendor_id": "B1", "name": "Glam Co", "category": "Beauty"}}
PACK = [{"id": 1, "user_id": 7, "vendor_name": "Example 1: Venue", "total_cost": 30000, "Selected": True, "Category": "Venue"},
        {"id": 2, "user_id": 7, "vendor_name": "The Houston Club", "total_cost": 12505, "Balance_Paid": 4000,
         "Selected": True, "Category": "Venue", "Notes": "tulle:V1"},
        {"id": 3, "user_id": 7, "vendor_name": "Catering Co", "total_cost": 9000, "Selected": True, "Category": "Food and Beverage",
         "Notes": "tulle:manual"},
        {"id": 4, "user_id": 7, "vendor_name": "Old DJ", "total_cost": 900, "Selected": False, "Category": "Entertainment"}]


def test_view_budget_categories_extras():
    v = wedding.view(USER, PACK, list(CARDS.values()), show_prices=True)
    assert v["budget"] == 45000 and v["booked_total"] == 21505 and v["left"] == 23495    # example + unselected left out
    cats = {c["key"]: c for c in v["categories"]}
    assert cats["Venue"]["booked"]["name"] == "The Houston Club" and cats["Venue"]["booked"]["paid"] == 4000
    assert cats["Photography"]["saved"][0]["price"] == 2000 and cats["Entertainment"]["booked"] is None
    assert [e["name"] for e in v["extras"]] == ["Catering Co"]
    assert v["saved_other"] == [{"vendor_id": "B1", "name": "Glam Co", "category": "Beauty"}]


def test_free_couples_see_no_vendor_prices_but_their_own_numbers():
    v = wedding.view(USER, PACK, list(CARDS.values()), show_prices=False)
    assert all(s["price"] is None for c in v["categories"] for s in c["saved"])
    assert v["left"] == 23495                                                             # their own budget numbers stay


def test_table_category():
    assert wedding.table_category("Music") == "Entertainment" and wedding.table_category("Catering") == "Catering"
    assert wedding.table_category("") == "Other"


@pytest.fixture
def api(monkeypatch):
    st = {"pack": [dict(p) for p in PACK], "added": [], "mp": []}
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", dict(USER)))
    monkeypatch.setattr(xano, "has_paid_access", lambda u: True)
    monkeypatch.setattr(xano, "user_packages", lambda tok, uid: [p for p in st["pack"]])
    monkeypatch.setattr(xano, "cached_vendor_card", lambda vid, stats=None: CARDS.get(vid))

    def add(row):
        st["added"].append(row)
        st["pack"].append(dict(row, id=len(st["pack"]) + 1))
    monkeypatch.setattr(xano, "package_add", add)

    def sel(uid, item_id, selected):
        for p in st["pack"]:
            if p["id"] == item_id and p["user_id"] == uid:
                p["Selected"] = selected
                return True
        return False
    monkeypatch.setattr(xano, "package_set_selected", sel)
    monkeypatch.setattr(xano, "mp_track", lambda uid, ev, props: st["mp"].append(ev))
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)
    monkeypatch.setattr(wedding, "ENABLED", True)
    return st


def test_book_then_undo(api):
    c = TestClient(app.app)
    j = c.post("/rec/wedding/book", json={"vendor_id": "P1", "name": "OliviaPhotos", "category": "Photography",
                                          "price": 2000, "paid": 500}).json()
    assert api["added"][0]["Notes"] == "tulle:P1" and api["added"][0]["Selected"] is True
    ph = next(x for x in j["categories"] if x["key"] == "Photography")
    assert ph["booked"]["name"] == "OliviaPhotos" and j["left"] == 21495
    u = c.post("/rec/wedding/unbook", json={"item_id": ph["booked"]["id"]}).json()
    assert u["undone"] and next(x for x in u["categories"] if x["key"] == "Photography")["booked"] is None


def test_add_a_cost_needs_name_and_price(api):
    c = TestClient(app.app)
    assert c.post("/rec/wedding/book", json={"name": "Catering"}).status_code == 400
    j = c.post("/rec/wedding/book", json={"name": "Attire", "category": "Attire", "price": 1800}).json()
    assert any(e["name"] == "Attire" for e in j["extras"])


def test_rollback_flag(api, monkeypatch):
    monkeypatch.setattr(wedding, "ENABLED", False)
    assert TestClient(app.app).get("/rec/wedding").json() == {"enabled": False}
