"""Compare's pick (REC_COMPARE_DECIDE), venues near a named one (REC_NAMED_VENUE) and lookup v3 (REC_LOOKUP_V3),
2026-11-25. No network, no model."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import agent  # noqa: E402
import app  # noqa: E402
import compare  # noqa: E402
import config  # noqa: E402
import lookup  # noqa: E402
import xano  # noqa: E402

H = {"Authorization": "Bearer t"}
CARDS = {"V1": {"vendor_id": "V1", "name": "Wythe Hotel", "category": "Venue"},
         "V2": {"vendor_id": "V2", "name": "Celestine", "category": "Venue"},
         "V9": {"vendor_id": "V2", "name": "Celestine", "category": "Venue"},     # merged into V2
         "V5": {"vendor_id": "V5", "name": "Photo Co", "category": "Photography"}}


@pytest.fixture
def plan90(monkeypatch):
    rows = [{"id": 1, "user_id": 7, "slot": "Venue", "vendor_id": "V2", "vendor_name": "Celestine", "source": "planner_card",
             "status": "active"}]      # a card added in the old plan: never a pick, never touched

    def items(uid, include_removed=False, picks=False):
        return [r for r in rows if r["user_id"] == uid and (include_removed or r["status"] == "active")
                and (r["source"] == "pick") == picks]

    def add(row):
        rows.append(dict(row, id=len(rows) + 1))
        return rows[-1]

    def remove(uid, iid):
        for r in rows:
            if r["id"] == iid and r["user_id"] == uid:
                r["status"] = "removed"
                return True
        return False
    monkeypatch.setattr(xano, "plan_items", items)
    monkeypatch.setattr(xano, "plan_add", add)
    monkeypatch.setattr(xano, "plan_remove", remove)
    monkeypatch.setattr(xano, "listed_venue", lambda v: CARDS.get(v) if (CARDS.get(v) or {}).get("category") == "Venue" else None)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(config, "COMPARE_PICK", True)
    monkeypatch.setattr(config, "COMPARE_DECIDE", True)
    monkeypatch.setattr(config, "COMPARE_USER_IDS", {7})
    monkeypatch.setattr(config, "COMPARE_AB_PCT", 0)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    return rows


def post(vid):
    return TestClient(app.app).post("/rec/compare/pick", json={"vendor_id": vid}, headers=H)


def test_pick_change_clear(plan90):
    assert post("V1").json()["pick"] == {"vendor_id": "V1", "name": "Wythe Hotel"}
    assert post("V1").json()["pick"]["vendor_id"] == "V1"
    assert sum(r["source"] == "pick" for r in plan90) == 1                    # a second tap writes nothing
    assert post("V2").json()["pick"] == {"vendor_id": "V2", "name": "Celestine"}
    assert [(r["vendor_id"], r["status"]) for r in plan90 if r["source"] == "pick"] == [("V1", "removed"), ("V2", "active")]
    assert post("").json()["pick"] is None
    assert plan90[0]["status"] == "active"                                    # the old plan row is left alone
    assert app._pick_status({"id": 7}) == {"compare_decide": True, "compare_pick": None}


def test_pick_of_a_merged_duplicate_is_the_kept_venue(plan90):
    assert post("V9").json()["pick"]["vendor_id"] == "V2"


@pytest.mark.parametrize("vid", ["V5", "V404", "<script>"])
def test_only_a_listed_venue_can_be_picked(plan90, vid):
    assert post(vid).status_code == 404 and not any(r["source"] == "pick" for r in plan90)


def test_flag_off_or_control_arm(plan90, monkeypatch):
    monkeypatch.setattr(config, "COMPARE_DECIDE", False)
    assert post("V1").status_code == 404 and app._pick_status({"id": 7})["compare_decide"] is False
    monkeypatch.setattr(config, "COMPARE_DECIDE", True)
    monkeypatch.setattr(config, "COMPARE_USER_IDS", set())
    assert post("V1").status_code == 404
    assert not any(r["source"] == "pick" for r in plan90)


def test_a_failed_write_keeps_the_old_pick(plan90, monkeypatch):
    post("V1")
    monkeypatch.setattr(xano, "plan_add", lambda row: (_ for _ in ()).throw(RuntimeError("xano")))
    r = post("V2")
    assert r.status_code == 502 and r.json()["detail"]["pick"]["vendor_id"] == "V1"      # the page is told what is stored
    assert compare.pick_of(xano, 7)["vendor_id"] == "V1"


def test_a_row_that_cannot_be_removed(plan90, monkeypatch):
    post("V1")
    real = xano.plan_remove
    monkeypatch.setattr(xano, "plan_remove", lambda *a: (_ for _ in ()).throw(RuntimeError("xano")))
    assert post("V2").json()["pick"]["vendor_id"] == "V2"                    # the newest is the pick
    r = post("")
    assert r.status_code == 502 and r.json()["detail"]["pick"]["vendor_id"] == "V2"      # not cleared, and says so
    monkeypatch.setattr(xano, "plan_remove", real)
    assert post("").json()["pick"] is None
    assert TestClient(app.app).get("/rec/compare/pick", headers=H).json() == {"pick": None}


def test_going_back_to_a_leftover_older_row(plan90, monkeypatch):
    post("V1")
    monkeypatch.setattr(xano, "plan_remove", lambda *a: (_ for _ in ()).throw(RuntimeError("xano")))
    post("V2")
    assert post("V1").json()["pick"]["vendor_id"] == "V1"


def test_a_body_without_a_venue_never_clears(plan90):
    post("V1")
    assert TestClient(app.app).post("/rec/compare/pick", json={}, headers=H).status_code == 400
    assert compare.pick_of(xano, 7)["vendor_id"] == "V1"


def test_status_says_nothing_when_the_pick_cannot_be_read(plan90, monkeypatch):
    monkeypatch.setattr(xano, "plan_items", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("xano")))
    assert app._pick_status({"id": 7}) == {"compare_decide": True}


def test_the_old_plan_cannot_write_or_see_a_pick(plan90, monkeypatch):
    post("V1")
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)
    TestClient(app.app).post("/rec/plan/add", json={"vendor_id": "V1", "vendor_name": "Wythe", "source": "pick"}, headers=H)
    assert [r["source"] for r in plan90] == ["planner_card", "pick", "planner_card"]
    post("")
    assert plan90[2]["status"] == "active" and plan90[1]["status"] == "removed"


def test_listed_venue(monkeypatch):
    row = {"Vendor_ID": "V1", "id": 1, "Name": "A", "Category": "Venue", "Validated_Data": "1"}
    for change, ok in (({}, True), ({"Validated_Data": "0"}, False), ({"Category": ""}, False), ({"Category": "Flowers"}, False)):
        monkeypatch.setattr(xano, "_search_all", lambda t, s, change=change: [dict(row, **change)])
        assert bool(xano.listed_venue("V1")) is ok


def test_card_totals_need_the_same_entitlement_as_the_venue_page(monkeypatch):
    src = (pathlib.Path(app.__file__)).read_text(encoding="utf-8")
    assert '"card_totals": bool(paid and xano.pi_entitled(user)' in src


# ---------------------------------------------------------------- near venues

def _items():
    base = {"State": "New York", "Address": "", "image_1": {"url": "u"}, "flt_min_venue_fee": 31415, "Category": "Venue"}
    return [dict(base, Vendor_ID="V1", id=1, Name="Wythe Hotel", lat=40.72, lng=-73.95),
            dict(base, Vendor_ID="V3", id=3, Name="Celestine", lat=40.70, lng=-73.99),
            dict(base, Vendor_ID="V4", id=4, Name="The Wythe Hotel", lat=40.72, lng=-73.95),     # the same venue twice
            dict(base, Vendor_ID="V6", id=6, Name="Dobbin St", lat=40.725, lng=-73.955),
            dict(base, Vendor_ID="V7", id=7, Name="Buffalo Hall", lat=42.88, lng=-78.87)]


class Rail:
    def __init__(self, items):
        self.items = items

    def railway_get(self, token, params):
        return {"items": self.items}, None


def test_near_venues_are_near_distinct_and_priceless():
    got = compare.near_venues(xano, Rail(_items()), "t", {"vendor_id": "V1", "name": "Wythe Hotel", "state": "New York"}, 120)
    assert [g["name"] for g in got] == ["Dobbin St", "Celestine"]
    assert "31415" not in json.dumps(got)                                     # canary: the search row carries a price


def test_near_venues_unknown_place_is_empty():
    assert compare.near_venues(xano, Rail([]), "t", {"vendor_id": "V1", "name": "X", "state": "New York"}) == []
    assert compare.near_venues(xano, Rail(_items()), "t", {"vendor_id": "V1", "name": "X", "state": ""}) == []


# ---------------------------------------------------------------- lookup v3

def d(text):
    return lookup.detect(text, v2=True, guard=True, dym=True, v3=True)


@pytest.mark.parametrize("text", ["dessert", "pictures", "gluten free wedding cake in philadelphia", "videographer in philadelphia",
                                  "wedding photographer in dallas"])
def test_nothing_named_goes_to_the_model(text):
    assert d(text) is None


@pytest.mark.parametrize("text", ["Remove dj, I have a personal dj", "Drop the florist", "swap the band for something else"])
def test_edits_are_never_a_missing_venue(text):
    q = d(text)
    assert q is None or q.get("soft")


@pytest.mark.parametrize("text", ["ryland inn", "Villa Woodbine", "Elkin estate", "do you have the post office",
                                  "le force dj in dallas tx", "Book Cadillac pricing", "Tell me about Houston Hall"])
def test_names_stay_names(text):
    q = d(text)
    assert q and not q.get("soft")


def test_did_you_mean_needs_a_close_word():
    assert lookup.dym_score("Amway grand", "Far Far Away Ranch") > 0 and lookup.dym_score("Amway grand", "Far Far Away Ranch", True) == 0
    assert lookup.dym_score("the cape club of sharon", "Sharon Valley Ranch", True) == 0
    assert lookup.dym_score("Rhyland", "Ryland Inn", True) > 0
    assert lookup.dym_score("Elkin estate", "Elkins Estate", True) > 0
    assert lookup.dym_score("the duxbury bar maritime school", "Duxbury Bay Maritime Weddings", True) > 0


TABLE = [{"vendor_id": "V3882", "name": "Villa Woodbine", "state": "Florida", "address": "Miami, FL", "google_name": ""},
         {"vendor_id": "V957", "name": "Elkins Estate", "state": "Pennsylvania", "address": "Elkins Park, PA", "google_name": ""}]


@pytest.fixture
def names(monkeypatch):
    monkeypatch.setattr(config, "NAME_MATCH_V2", True)
    monkeypatch.setattr(xano, "name_search", lambda term, token="", **k: [dict(r) for r in TABLE if all(
        w in lookup.tokens(r["name"]) for w in term.split())])


def test_find_by_name_looks_in_every_state(names):
    r = agent.find_by_name("t", "Villa Woodbine", "Tell me about Villa Woodbine and whether it fits my wedding", ["Texas"])
    assert r["status"] == "found" and r["venue"]["vendor_id"] == "V3882"


def test_find_by_name_a_misspelling(names):
    r = agent.find_by_name("t", "Elkin estate", "Elkin estate", ["New York"])
    assert r["status"] == "found" and r["venue"]["vendor_id"] == "V957" or r["status"] == "close" and "Elkins" in r["names"][0]


@pytest.mark.parametrize("kw", ["Miami", "barn", "rustic garden", "Villa Nowhere Castle", "Melrose River Club"])
def test_find_by_name_never_invents(names, kw):
    r = agent.find_by_name("t", kw, kw, ["Texas"])
    assert r is None or r["status"] == "close"


def test_find_by_name_survives_a_search_failure(monkeypatch):
    monkeypatch.setattr(xano, "name_search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert agent.find_by_name("t", "Villa Woodbine", "", []) is None
