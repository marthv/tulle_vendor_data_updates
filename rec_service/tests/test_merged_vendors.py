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


# ---------------------------------------------------------------- agent.run (fake model): tools given an old id

def _agent(monkeypatch, calls, prior=(), user=None):
    """Run agent.run with a fake model that makes `calls` [(tool, args)], then presents; -> (tool results, result)."""
    import json
    import types

    import agent
    got = []

    class Msg:
        content, model, usage = [], "fake", types.SimpleNamespace(input_tokens=1, output_tokens=1)

    class Runner:
        def __init__(self, tools, **k):
            self.tools = {(getattr(t, "name", None) or getattr(t, "__name__", None)): t for t in tools}

        def __iter__(self):
            for name, args in calls:
                t = self.tools[name]
                out = t.call(args) if hasattr(t, "call") else t(**args)
                if name != "present_recommendations":
                    got.append(json.loads(out))
                yield Msg()

    monkeypatch.setattr(agent.anthropic, "Anthropic", lambda: types.SimpleNamespace(beta=types.SimpleNamespace(
        messages=types.SimpleNamespace(tool_runner=lambda **k: Runner(**k)))), raising=False)
    monkeypatch.setattr(xano, "market_benchmarks", lambda *a, **k: {})
    res, _ = agent.run("tok", user or {"id": 3, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 100,
                                       "favorited_vendors": []}, True, [{"role": "user", "content": "hi"}],
                       prior_vendor_ids=prior)
    return got, res


@pytest.fixture
def priced(db, monkeypatch):
    """Each vendor's peak rental is 1000 x its number; each off-peak rental 500 x its number."""
    asked = []

    def pricing(token, vid, paid):
        asked.append(vid)
        n = int(vid[1:])
        return {"vendor_id": vid, "headline": "H", "guest_minimum": 0, "verdict": "", "lines": [], "source_note": "",
                "cost_inputs": {"who": vid, "space": "Ballroom", "rental": 1000 * n}, "access_reason": "forever"}

    import estimate
    monkeypatch.setattr(xano, "venue_pricing", pricing)
    monkeypatch.setattr(estimate, "off_peak_override", lambda d, space: {"who": d["vendor_id"]})
    monkeypatch.setattr(estimate, "compute", lambda inp, g, o=None: {
        "total_before_tax_and_gratuity": inp["rental"], "per_guest": 10, "guests": g, "estimated_lines": [], "peak_of": inp["who"], "off_peak_of": (o or {}).get("who")})
    return asked


PRESENT = ("present_recommendations", {"message": "m", "vendor_ids": [], "reasons": [], "chips": []})


def _present(ids):
    return ("present_recommendations", {"message": "m", "vendor_ids": ids, "reasons": [""] * len(ids), "chips": []})


def test_estimate_cost_never_mixes_the_two_listings(priced, monkeypatch):
    got, _ = _agent(monkeypatch, [("estimate_cost", {"vendor_id": "V2", "off_peak": True}),
                                  ("estimate_cost", {"vendor_id": "V2"}), PRESENT], prior=["V2"])
    assert (got[0]["peak_of"], got[0]["off_peak_of"]) == ("V1", "V1")
    assert got[1]["peak_of"] == "V1" and "V2" not in priced


def test_estimate_cost_stale_search_result_still_not_mixed(priced, monkeypatch):
    # the search index still lists the duplicate (sync lag), so it is in `seen`, not `prior`
    monkeypatch.setattr(xano, "search_venues", lambda *a, **k: {"total_matches": 1, "venues": [
        dict(xano.card_from_row(_row(2, "Dupe Hall")), food_minimum=0, at_a_glance=None)]})
    got, _ = _agent(monkeypatch, [("search_venues", {"states": ["Texas"]}),
                                  ("estimate_cost", {"vendor_id": "V2", "off_peak": True}), PRESENT])
    assert (got[-1]["peak_of"], got[-1]["off_peak_of"]) == ("V1", "V1")


def test_estimate_cost_unmerged_unchanged(priced, monkeypatch):
    got, _ = _agent(monkeypatch, [("estimate_cost", {"vendor_id": "V3", "off_peak": True}), PRESENT], prior=["V3"])
    assert (got[0]["peak_of"], got[0]["off_peak_of"]) == ("V3", "V3") and set(priced) == {"V3"}


def test_duplicate_and_kept_vendor_present_as_one_card(priced, monkeypatch):
    # presented twice: the first is handed back as a repeat (guard), the second stands
    for ids in (["V2", "V1", "V3"], ["V1", "V2", "V3"]):
        _, res = _agent(monkeypatch, [_present(ids)] * 2, prior=["V1", "V2", "V3"])
        assert sorted(c["vendor_id"] for c in res["cards"]) == ["V1", "V3"], ids


def test_saved_list_naming_both_gives_one(priced, monkeypatch):
    user = {"id": 3, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 100,
            "favorited_vendors": ["V2", "V3", "V1"]}
    got, _ = _agent(monkeypatch, [("saved_venues", {}), PRESENT], user=user)
    assert [r["vendor_id"] for r in got[0]["saved"]] == ["V1", "V3"]


def test_unique_cards_leaves_distinct_and_idless_cards_alone():
    cards = [{"vendor_id": "V1"}, {"vendor_id": "V3"}, {}, {}]
    assert xano.unique_cards(cards) == cards and xano.unique_cards(cards + [{"vendor_id": "V1", "n": 2}]) == cards
    import cost_tools
    assert cost_tools._unique(cards + [{"vendor_id": "V1", "n": 2}]) == cards
