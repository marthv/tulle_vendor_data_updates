"""Scan conflict rules (2026-11-22, QA plan review items 4, 6, 9 + PASS_BAR amendment 2): conflicting State, >10 km or
conflicting Place_IDs never pair; a 3+ group is all-pairs; hidden members are held; an undone merge is final."""
import pytest

import dedupe
import merge_requests as mr
from fakes import mention
from test_merge_requests import RowsXano
from test_v2 import V
from test_vendor_actions import make_bot


def ids(rows):
    return [g["ids"] for g in dedupe.find_groups(rows)]


def reasons(rows):
    return {(tuple(h["ids"]), h["reason"]) for h in dedupe.find_groups_held(rows)[1]}


# the live rows behind the canary, 2026-11-22: V1913 (Boston) carries New York's Place_ID
NYC_PID = "ChIJL83lHfZYwokRnd7wA8PLD1E"
BOSTON = V(1913, "V1913", "Mandarin Oriental", addr="776 Boylston St, Boston, MA 02199", state="Massachusetts",
           pid=NYC_PID, lat=42.3486759, lng=-71.0816572)
NYC = V(9876, "V9876", "Mandarin Oriental NYC", addr="80 Columbus Circle, New York, NY 10023", state="New York",
        pid=NYC_PID, web="MANDARINORIENTAL.COM/NEWYORK", lat=40.7689575, lng=-73.9830488)


def test_canary_mandarin_oriental_boston_vs_nyc():
    assert ids([BOSTON, NYC]) == []
    assert ((1913, 9876), "state") in reasons([BOSTON, NYC])
    # each rule alone is enough: state only, address state only, distance only
    assert ids([dict(BOSTON, Address="", lat=None, lng=None), NYC]) == []
    assert ids([dict(BOSTON, State="", lat=None, lng=None), NYC]) == []
    assert ids([dict(BOSTON, State="", Address=""), NYC]) == []
    assert ((1913, 9876), "distance") in reasons([dict(BOSTON, State="", Address=""), NYC])


def test_the_canary_would_have_been_suggested_before(monkeypatch):
    monkeypatch.setattr(dedupe, "pair_conflict", lambda a, b: None)
    assert ids([BOSTON, NYC]) == [[1913, 9876]]                     # proves the test can fail


@pytest.mark.parametrize("change,why", [
    ({"State": "Oklahoma", "Address": "1 Main St, Austin"}, "state"),
    ({"State": "", "Address": "1 Main St, Austin, OK 73301"}, "state"),
    ({"lat": 30.2}, "distance"),                                                         # 22 km
    ({"Place_ID": "P2"}, "place_id"),
])
def test_each_conflict_blocks_a_pair_that_matches_otherwise(change, why):
    a, b = V(1, "V1", "Oak Barn", pid="P1"), V(2, "V2", "Oak Barn", pid="P1")
    assert ids([a, b]) == [[1, 2]]
    b.update(change)
    assert ids([a, b]) == [] and dedupe.pair_conflict(*dedupe.prepare([a, b])) == why


@pytest.mark.parametrize("change", [
    {"State": ""}, {"State": "TX"}, {"State": "['Texas', 'Oklahoma']"}, {"State": "Texas; Oklahoma"},
    {"Place_ID": ""}, {"Place_ID": None}, {"lat": None, "lng": None}, {"lat": 0, "lng": 0}, {"lat": 30.05},    # 5.6 km
    {"Address": "1 Main St, Austin"},
])
def test_blank_or_equivalent_values_are_not_conflicts(change):
    a, b = V(1, "V1", "Oak Barn", pid="P1"), dict(V(2, "V2", "Oak Barn", pid="P1"), **change)
    assert dedupe.pair_conflict(*dedupe.prepare([a, b])) is None
    if change.get("Place_ID", "P1"):
        assert ids([a, b]) == [[1, 2]]


def test_dc_spellings_and_addr_state():
    a = V(1, "V1", "Oak Barn", addr="1 K St NW, Washington, DC 20001", state="District of Columbia", pid="P")
    assert ids([a, dict(a, id=2, Vendor_ID="V2", State="Washington DC")]) == [[1, 2]]
    for addr, want in (("776 Boylston St, Boston, MA 02199", "ma"), ("1 A, Boston, MA 02199-1234, USA", "ma"),
                       ("1 A, Boston, ma", "ma"), ("Via Roma 1, 50123 Firenze FI, Italy", ""), ("", ""),
                       ("12 Rue de X, Paris, France", ""), ("1325 Quincy St NE", ""),
                       ("500 Main St, Omaha, NE 68102", "ne"), ("500 Main St Omaha NE 68102", "ne")):
        assert dedupe.addr_state(addr) == want, addr


@pytest.mark.parametrize("pid", ["N/A", "n/a", "NA", '"NA"', "X", "None", "Heritage Hall", "https://citycreekevents.com/",
                                 "305 Bloomfield Ave, Nutley, NJ 07110, USA", "Place ID: ChIJH-fNkE5BjoAR0mlZD_aWU8o"])
def test_a_placeholder_is_not_a_place_id(pid):
    """Live 2026-11-22: 'N/A' was matched as a Place_ID and paired photographers with similar names."""
    a = V(12037, "V12037", "Katie Salerno Photography", addr="Los Angeles", state="California", pid=pid,
          cat="Photography", lat=34.05, lng=-118.24)
    b = dict(a, id=12038, Vendor_ID="V12038", Name="Katie Shuler Photography")
    assert dedupe.ratio(dedupe.norm(a["Name"]), dedupe.norm(b["Name"])) >= 80
    assert dedupe.place_id(a) is None and ids([a, b]) == []


def test_every_pair_in_a_group_of_three_must_match():
    a = V(1, "V1", "Rose Hall", pid="P1")
    b = V(2, "V2", "Rose Hall", pid="P1")
    c = V(3, "V3", "Rose Hall", pid="P1")
    assert ids([a, b, c]) == [[1, 2, 3]]
    # A~B (same name+street, B has no Place_ID) and B~C, but A and C have different Place_IDs: none is suggested
    b2 = dict(b, Place_ID="")
    c2 = dict(c, Place_ID="P2")
    assert ids([a, b2, c2]) == []
    got = reasons([a, b2, c2])
    assert ((1, 2, 3), "not_all_match") in got and ((1, 3), "place_id") in got


@pytest.mark.parametrize("vis", ["0", "\n", ""])
def test_hidden_member_holds_the_whole_group(vis):
    a, b = V(1, "V1", "Rose Hall", pid="P1"), V(2, "V2", "Rose Hall", pid="P1", vis=vis)
    assert ids([a, b]) == [] and ((1, 2), "hidden") in reasons([a, b])
    c = V(3, "V3", "Rose Hall", pid="P1")
    assert ids([a, b, c]) == []                                     # not even the two visible ones, until checked


def test_held_reports_mixed_category_and_too_big():
    rows = [V(i, f"V{i}", "Rose Hall", pid="P1") for i in range(1, 6)]
    assert ids(rows) == [] and reasons(rows) == {((1, 2, 3, 4, 5), "too_big")}
    rows = [V(1, "V1", "Rose Hall", pid="P1"), V(2, "V2", "Rose Hall", pid="P1", cat="Food and Beverage")]
    assert reasons(rows) == {((1, 2), "mixed_category")}


# --- undone = final for the scan, a person can still ask ---------------------------------------------------------
def _undone(store, key="merge:V1|V2"):
    p = store.create("merge", "t", "w", "y", "n", {"survivor": "V1", "losers": ["V2"]}, key=key)
    store.transition(p["id"], {"pending"}, "applying", "U")
    store.transition(p["id"], {"applying"}, "applied", "U")
    store.transition(p["id"], {"applied"}, "undoing", "U")
    store.transition(p["id"], {"undoing"}, "undone", "U")
    return p


class EmptyX:
    def get(self, path, params=None):
        return {"items": [], "nextPage": None}


def test_scan_never_resuggests_an_undone_merge_but_a_person_can():
    rows = [V(1, "V1", "Rose Hall", pid="P1"), V(2, "V2", "Rose Hall", pid="P1")]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    _undone(store)
    assert dedupe.scan(EmptyX(), store, mode="weekly", vendor_rows=rows)["proposals"] == []
    assert "merge:V1|V2" in store.handled_keys("merge")
    assert store.create("merge", "t", "w", "y", "n", {}, key="merge:V1|V2") is None        # nor by a racing caller
    assert b.handle(mention("find duplicates of V1")) == "merge:dupes-existing"
    assert "merged, then undone" in slack.of("chat.postMessage")[-1]["text"]
    assert b.handle(mention("merge V1 and V2")) == "merge:proposed"
    assert b.handle(mention("merge V1 and V2")) == "merge:duplicate"                       # one open card only


def test_undone_still_allowed_for_other_kinds():
    from proposals import Store
    import tempfile
    from pathlib import Path
    st = Store(Path(tempfile.mkdtemp()) / "p.json")
    p = st.create("vendor_edit", "t", "w", "y", "n", {}, key="edit:V1:Website")
    st.transition(p["id"], {"pending"}, "undone", "U")
    assert st.create("vendor_edit", "t", "w", "y", "n", {}, key="edit:V1:Website") is not None


# --- asked by name ------------------------------------------------------------------------------------------------
def test_asked_merge_warns_on_conflict_or_hidden():
    assert mr.asked_why([dict(V(1, "V1", "A")), dict(V(2, "V2", "A"))]) == "Asked for in Slack."
    w = mr.asked_why([BOSTON, NYC])
    assert "different states" in w
    assert "hidden" in mr.asked_why([V(1, "V1", "A"), V(2, "V2", "A", vis="0")])


def test_find_duplicates_names_the_hidden_one_without_a_card():
    rows = [V(1, "V1", "Rose Hall", pid="P1"), V(2, "V2", "Rose Hall", pid="P1", vis="0")]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    assert b.handle(mention("find duplicates of V1")) == "merge:dupes-hidden"
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "(V2), hidden" in txt and "merge V1 and V2" in txt and store.all() == []
