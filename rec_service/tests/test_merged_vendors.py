"""Vendor merges (2026-11-22): a merged duplicate's row (merged_into = kept Vendor_ID) becomes the KEPT vendor's card
everywhere the Planner looks a vendor up by id; a row with an empty / missing merged_into behaves exactly as before and
costs no extra read. One hop only (ep578 refuses chained merges)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import xano  # noqa: E402


def _row(i, name, merged="", **kw):
    r = {"id": i, "Vendor_ID": "V%d" % i, "Name": name, "Category": "Venue", "State": "Texas", "Validated_Data": "1",
         "flt_min_venue_fee": 100 * i, "flt_max_venue_fee": 200 * i, "merged_into": merged}
    r.update(kw)
    return r


@pytest.fixture
def db(monkeypatch):
    t11 = {r["Vendor_ID"]: r for r in (
        _row(1, "Kept Hall", google_data_cache={"rating": 4.5, "reviews": []}),
        _row(2, "Dupe Hall", merged="V1", Validated_Data="0", google_data_cache={"rating": 1.0, "reviews": []}),
        _row(3, "Plain Hall"),
        _row(4, "Orphan Hall", merged="V999"),
        _row(5, "Null Hall", merged=None),
        _row(6, "Chain Hall", merged="V2"))}
    no_col = _row(7, "Old Row")
    del no_col["merged_into"]
    t11["V7"] = no_col
    t36 = [{"VENDOR_ID": "V1", "PDF_ID": "P1", "Venue_Space_Name": "Ballroom", "VENUE_NAME": "Kept Hall"}]
    reads = []

    def search(table, search):
        reads.append((table, search[0]))
        if table == 11:
            r = t11.get(search[0]["Vendor_ID"])
            return [r] if r else []
        return [r for r in t36 if r["VENDOR_ID"] == search[0]["VENDOR_ID"]]

    def meta(method, path, **kw):
        i = int(path.rsplit("/", 1)[1])
        return next((r for r in t11.values() if r["id"] == i), None)

    monkeypatch.setattr(xano, "_search_all", search)
    monkeypatch.setattr(xano, "_meta", meta)
    monkeypatch.setattr(xano, "_IDX_CACHE", type(xano._IDX_CACHE)())
    return reads


def test_merged_vendor_gives_the_kept_vendors_card(db):
    c = xano.vendor_card("V2")
    assert (c["vendor_id"], c["vendor_idx"], c["name"], c["venue_fee_range"]) == ("V1", 1, "Kept Hall", [100, 200])


@pytest.mark.parametrize("vid,name", [("V1", "Kept Hall"), ("V3", "Plain Hall"), ("V5", "Null Hall"), ("V7", "Old Row")])
def test_unmerged_vendor_is_unchanged_and_costs_one_read(db, vid, name):
    c = xano.vendor_card(vid)
    assert c["vendor_id"] == vid and c["name"] == name and len(db) == 1


def test_missing_vendor_and_missing_kept_vendor(db):
    assert xano.vendor_card("V404") is None
    assert xano.vendor_card("V4")["vendor_id"] == "V4"      # kept row gone: never an empty card


def test_one_hop_only(db):
    assert xano.vendor_card("V6")["vendor_id"] == "V2"


def test_card_by_idx_follows_the_merge(db):
    assert xano.vendor_card_by_idx(2)["vendor_id"] == "V1"
    assert xano.vendor_card_by_idx(3)["vendor_id"] == "V3"
    assert len(db) == 1                                      # only the merged row needed a second read


def test_card_by_idx_failed_second_read_is_none_not_an_error(db, monkeypatch):
    def boom(table, search):
        raise xano.XanoError("down")
    monkeypatch.setattr(xano, "_search_all", boom)
    assert xano.vendor_card_by_idx(2) is None
    assert xano.vendor_card_by_idx(3)["name"] == "Plain Hall"


def test_venue_details_reads_the_kept_vendors_pdfs_and_google(db):
    d = xano.venue_details("V2", with_amounts=True)
    assert d["vendor_id"] == "V1" and d["spaces"][0]["space"] == "Ballroom" and d["google"]["rating"] == 4.5
    db.clear()
    d = xano.venue_details("V3", with_amounts=True)
    assert d["vendor_id"] == "V3" and d["spaces"] == [] and len(db) == 2     # same two reads as before


def test_canary_without_follow_merge_the_duplicate_would_show(db, monkeypatch):
    monkeypatch.setattr(xano, "follow_merge", lambda r: r)   # the test must be able to fail
    assert xano.vendor_card("V2")["vendor_id"] == "V2"
