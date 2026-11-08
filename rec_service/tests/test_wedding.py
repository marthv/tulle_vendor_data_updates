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
    assert api["added"][0]["Package_Name"].endswith("tulle:P1") and api["added"][0]["Notes"] == "" and api["added"][0]["Selected"] is True
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


def test_v2_notes_file_slices_and_marker():
    pk = PACK + [{"id": 5, "user_id": 7, "vendor_name": "Bloomery", "total_cost": 3000, "Selected": True,
                  "Category": "Flowers", "Notes": "Peonies, deposit due Jan", "Package_Name": "Booked in My wedding · tulle:F9",
                  "Files": {"path": "/vault/abc/contract.pdf", "name": "contract.pdf", "mime": "application/pdf"}}]
    v = wedding.view(USER, pk, list(CARDS.values()), show_prices=True)
    cats = {c["key"]: c for c in v["categories"]}
    fl = cats["Flowers"]["booked"]
    assert fl["vendor_id"] == "F9" and fl["notes"] == "Peonies, deposit due Jan"
    assert fl["file"]["url"].endswith("/vault/abc/contract.pdf") and fl["file"]["name"] == "contract.pdf"
    assert cats["Venue"]["booked"]["vendor_id"] == "V1" and cats["Venue"]["booked"]["notes"] == ""   # v1 marker hidden
    assert v["slices"][0] == {"label": "Venue", "amount": 12505} and {"label": "Flowers", "amount": 3000} in v["slices"]
    assert v["paid_total"] == 4000


def test_clean_attachment():
    assert wedding.clean_attachment({"path": "https://evil/x.pdf"}) is None
    assert wedding.clean_attachment({"path": "/vault/../x"}) is None
    a = wedding.clean_attachment({"path": "/vault/a/b.pdf", "name": "b.pdf", "size": 9, "mime": "application/pdf", "url": "https://evil"})
    assert a["path"] == "/vault/a/b.pdf" and "url" not in a and a["access"] == "public"


def test_item_edit_endpoint(monkeypatch):
    calls = {}
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", USER))
    monkeypatch.setattr(app, "_wedding_view", lambda t, u, packages=None: {"enabled": True, "categories": []})
    monkeypatch.setattr(xano, "package_update", lambda uid, iid, ch, price, paid: calls.update(uid=uid, iid=iid, ch=ch) or iid == 2)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    c = TestClient(app.app)
    assert c.post("/rec/wedding/item", json={"item_id": 2, "notes": " hi "}).status_code == 200 and calls["ch"] == {"Notes": "hi"}
    assert c.post("/rec/wedding/item", json={"item_id": 2, "file": {"path": "nope"}}).status_code == 400
    assert c.post("/rec/wedding/item", json={"item_id": 9, "notes": "x"}).status_code == 404
    assert c.post("/rec/wedding/item", json={"item_id": 2, "clear_file": True}).status_code == 200 and calls["ch"] == {"Files": None}
