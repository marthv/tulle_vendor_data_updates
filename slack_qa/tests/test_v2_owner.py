"""Owner feedback 2026-11-20: plain-English dedupe card, Details view with the full record, counter aggregation,
user-side vendor-id fields."""
import json
import re
from pathlib import Path

import pytest
import requests

import appliers
import cards
import dedupe
import interactions
from proposals import Store
from test_v2 import OWNER, RecSlack, V, click

ROWS = [V(1, "V105", "The Plaza", addr="768 5th Ave, New York, NY 10019", state="New York", pid="P1", clicks=300,
          web="theplaza.com", lat=40.7646, lng=-73.9744),
        V(2, "V13433", "The Plaza", addr="768 5th Ave, New York, NY 10019", state="New York", pid="P1", clicks=40,
          vis="0", lat=40.7646, lng=-73.9744)]
ROWS[0].update({"gold_chip": False, "Collection": ["Hotel"], "Max_Capacity_Seated": 500, "Description": "",
                "Time_of_Submission": "2025-03-01", "Flags": "x"})
ROWS[1].update({"gold_chip": True, "Collection": ["Hotel", "Ballroom"], "Max_Capacity_Seated": 650,
                "Description": "Iconic hotel", "Time_of_Submission": "2024-06-01", "Flags": "y; x",
                "google_data_cache": {"rating": 4.6}})


class FakeX:
    def __init__(self):
        self.tables = {
            10: [{"id": 501, "Vendor_ID": "V13433"}, {"id": 502, "Vendor_ID": "V13433"}, {"id": 503, "Vendor_ID": "V13433"},
                 {"id": 504, "Vendor_ID": "V105"}],
            37: [{"id": 9, "Vendor_ID": "V13433"}],
            90: [{"id": 77, "vendor_id": "V13433"}],
            66: [{"id": 3, "vendor_id": "", "payload": {"note": "same as V13433 and V134330"}}],
            71: [{"id": 4, "VENDOR_ID": "V1", "matched_vendor_id": "", "matched_vendor_idx": 2}],
        }

    def get(self, path, params=None):
        return {"items": self.tables.get(int(path.split("/")[2]), []), "nextPage": None}


def proposal():
    store = Store()
    res = dedupe.scan(FakeX(), store, mode="weekly", vendor_rows=[dict(r) for r in ROWS])
    return store, res["proposals"][0]


def card_text(blocks):
    return " ".join(b["text"]["text"] for b in blocks if b["type"] == "section")


# 1. plain-English main card ------------------------------------------------------------------------------------
def test_main_card_is_plain_english():
    store, p = proposal()
    text, blocks = cards.render(p)
    assert cards.validate(blocks) == []
    main = card_text(blocks)
    assert sum(1 for b in blocks if b["type"] == "section") == 3
    assert main.startswith("These look like the same venue: *The Plaza* (New York) listed twice.")
    assert "Same Google listing." in main
    assert "We'll keep the visible listing, move its 3 PDFs" in main and "1 couple' plans" not in main
    assert "hidden, not deleted, and this can be undone" in main and "add up its clicks" in main
    for bad in (r"\bV\d+", r"\btable\b", "Place ID", "row", "Vendor_ID", "repoint"):
        assert not re.search(bad, main, re.I), bad
    labels = [e["text"]["text"] for b in blocks if b["type"] == "actions" for e in b["elements"]]
    assert labels == ["Approve and apply", "Reject", "Details"]


def test_why_line_matches_strongest_rule():
    rows = [V(1, "V1", "Oak Barn", addr="2 B Ln, Tyler, TX 75701", lat=32.35, lng=-95.3),
            V(2, "V2", "Oak Barn", addr="2 B Lane, Tyler, TX 75701", lat=32.3515, lng=-95.3005)]
    store = Store()
    p = dedupe.scan(FakeX(), store, mode="weekly", vendor_rows=rows)["proposals"][0]
    assert p["plain"]["why"] == "Same name at the same address."


def test_saved_by_couples_count_when_users_counted():
    refs = dedupe.RefIndex(type("X", (), {"get": lambda self, path, params=None: {
        "items": [{"id": 1, "favorited_vendors": ["V2", "V9"]}, {"id": 2, "favorited_vendors": ["V2"]}]
        if "/table/1/" in path else [], "nextPage": None}})(), max_pages=50, skip=set())
    refs.build({"V2"}, rowids={2: "V2"})
    byid = {1: V(1, "V1", "Rose Hall", pid="P"), 2: V(2, "V2", "Rose Hall", pid="P")}
    pr = dedupe.build_proposal({"ids": [1, 2], "rules": ["A"]}, byid, refs)
    assert "2 saved-by couples" in pr["plain"]["happens"]


# 2. full technical record: Details + persisted snapshots -------------------------------------------------------------
def test_snapshots_rows_and_counters_persisted():
    store, p = proposal()
    q = store.get(p["id"])
    snaps = q["payload"]["snapshots"]
    assert set(snaps) == {"V105", "V13433"} and snaps["V13433"]["Description"] == "Iconic hotel"
    assert snaps["V13433"]["google_data_cache"] == {"rating": 4.6}
    assert all(not k.startswith("_") for s in snaps.values() for k in s)
    t10 = next(t for t in q["payload"]["tables"] if t["table"] == 10)
    assert [r[0] for r in t10["rows"]] == [501, 502, 503]
    assert any(c["column"] == "Clicks_on_Venue" for c in q["payload"]["counters"])


def test_details_button_posts_full_record_once_in_thread():
    slack = RecSlack()
    store, p = proposal()
    interactions.post_proposal(slack, store, "CDEDUPE", p)
    p = store.get(p["id"])
    it = interactions.Interactions(slack=slack, store=store)
    payload = click(cards.ACTION_DETAILS, p["id"], user="USOMEONE", ts=p["ts"])     # not an approver: still allowed
    assert it.handle(payload) == "details:posted"
    reply = slack.of("chat.postMessage")[-1]
    assert reply["thread_ts"] == p["ts"]
    assert cards.validate(reply["blocks"]) == []
    body = " ".join(b["text"]["text"] if b["type"] == "section" else b["elements"][0]["text"] for b in reply["blocks"])
    for want in ("V105", "V13433", "Description", "Iconic hotel", "501, 502, 503", "Clicks_on_Venue · sum",
                 "V105=300, V13433=40 → 340", "Couples", "partner_key"):
        assert want in body, want
    assert it.handle(payload) == "details:already"
    assert store.get(p["id"])["status"] == "pending"


def test_merge_endpoint_receives_snapshots(monkeypatch):
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    monkeypatch.setenv("XANO_MERGE_URL", "https://x.example/m")
    monkeypatch.setenv("XANO_MERGE_SECRET", "s")
    store, p = proposal()
    sent = {}

    class R:
        status_code = 200

        def json(self):
            return {"repointed_total": 5, "backup_id": 1}
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: sent.update(json) or R())
    appliers.merge(p["payload"], OWNER, p["id"], dry_run=False)
    assert set(sent["snapshots"]) == {"V105", "V13433"} and sent["counters"]


# 3. counters ---------------------------------------------------------------------------------------------------
def test_counter_rules():
    s, l = dict(ROWS[0]), dict(ROWS[1])
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert plan["Clicks_on_Venue"]["after"] == 340 and plan["Clicks_on_Venue"]["before"] == {"V105": 300, "V13433": 40}
    assert plan["gold_chip"]["after"] is True
    assert plan["Collection"]["after"] == ["Hotel", "Ballroom"]
    assert plan["Max_Capacity_Seated"]["after"] == 650
    assert plan["Time_of_Submission"]["after"] == "2024-06-01"
    assert plan["Description"]["after"] == "Iconic hotel"
    assert plan["Flags"]["after"] == "x, y"
    assert plan["flt_space_count"]["after"] == "recomputed"
    assert "Validated_Data" not in plan            # survivor already visible: unchanged columns are omitted
    assert "Website" not in plan                    # survivor has one: kept


def test_any_visible_rule():
    s, l = V(1, "V1", "A", vis="0"), V(2, "V2", "A", vis="1")
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert plan["Validated_Data"]["after"] == "1"


def test_every_counter_rule_documented():
    doc = (Path(__file__).resolve().parent.parent / "XANO_MERGE_PROPOSAL.md").read_text(encoding="utf-8")
    for col in dedupe.COUNTER_RULES:
        assert col in doc, col


# 4. user-side vendor ids -------------------------------------------------------------------------------------------
def test_user_side_fields_mapped_and_documented():
    doc = (Path(__file__).resolve().parent.parent / "XANO_MERGE_PROPOSAL.md").read_text(encoding="utf-8")
    cols = {(t, c) for t, _l, cs, _a, _u in dedupe.REF_TABLES for c, _k in cs}
    for want in ((1, "favorited_vendors"), (1, "last_viewed_vendor_id"), (1, "pricing_viewed_ctx"), (90, "vendor_id"),
                 (33, "vendor_id"), (31, "vendor_id"), (43, "cards"), (66, "payload"), (71, "matched_vendor_idx")):
        assert want in cols, want
    for t, _l, cs, _a, _u in dedupe.REF_TABLES:
        for c, _k in cs:
            assert c in doc, (t, c)
    for _t, f, _w in dedupe.NAME_KEYED:
        assert f.split(" / ")[0] in doc, f


def test_json_and_rowid_matching():
    hits = dedupe.RefIndex._hits
    assert hits({"note": "same as V13433 and V134330"}, "json", {"V13433"}, {}) == {"V13433"}
    assert hits('{"vendor_id": "V1343"}', "json", {"V13433"}, {}) == set()
    assert hits(["V2", "V9"], "ids", {"V2"}, {}) == {"V2"}
    assert hits(2, "rowid", {"V13433"}, {2: "V13433"}) == {"V13433"}


def test_preview_cards_valid_for_all_states():
    store, p = proposal()
    for st in ("pending", "applied", "dry_run", "unknown", "rejected"):
        p["status"], p["decided_by"] = st, OWNER
        assert cards.validate(cards.render(p)[1]) == []
    assert cards.validate(cards.render_details(p)[1]) == []


# risk review B1 -------------------------------------------------------------------------------------------------------
def test_proposal_doc_matches_code_exactly():
    import gen_merge_doc
    doc = (Path(__file__).resolve().parent.parent / "XANO_MERGE_PROPOSAL.md").read_text(encoding="utf-8")
    assert gen_merge_doc.render(doc) == doc, "run: cd slack_qa && python gen_merge_doc.py"
    assert '$input.mode == "status"' in doc and "`partial`" in doc and "never by subtraction" in doc


def test_json_vendor_idx_ints_are_matched():
    hits = dedupe.RefIndex._hits
    card = [{"vendor_id": "V9", "vendor_idx": 2, "name": "The Plaza"}]
    assert hits(card, "json", {"V13433"}, {2: "V13433"}) == {"V13433"}
    payload = {"venue": {"vendor_id": "V1"}, "matched": {"key": "x", "vendor_idx": "2"}}
    assert hits(json.dumps(payload), "json", {"V13433"}, {2: "V13433"}) == {"V13433"}
    assert hits({"vendor_idx": 3, "x": 2}, "json", {"V13433"}, {2: "V13433"}) == set()


def test_keyed_tables_are_rebuilt_not_repointed():
    acts = {(t, c): a for t, _l, cs, a, _u in dedupe.REF_TABLES for c, _k in cs}
    assert acts[(62, "VENDOR_ID")] == "rebuild" and acts[(71, "VENDOR_ID")] == "rebuild"
    assert acts[(71, "matched_vendor_id")] == "repoint" and acts[(71, "matched_vendor_idx")] == "repoint"
    store, p = proposal()
    assert "t62" not in p["payload"]["counts"]               # venue side of t71 isn't counted as a move either
    t71 = [t for t in p["payload"]["tables"] if t["table"] == 71]
    assert [t["action"] for t in t71] == ["rebuild", "repoint"]


def test_google_cache_follows_kept_place_id():
    s = V(1, "V1", "A", pid="")
    s.update({"google_data_cache": {"rating": 3.0}, "google_data_last_fetched": "2026-11-19"})
    l = V(2, "V2", "A", pid="P2")
    l.update({"google_data_cache": {"rating": 4.8}, "google_data_last_fetched": "2026-01-01"})
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert plan["Place_ID"]["after"] == "P2" and plan["google_data_cache"]["after"] == {"rating": 4.8}
    assert plan["google_data_last_fetched"]["after"] == "2026-01-01"
    s["Place_ID"] = "P1"                              # survivor keeps its own listing -> its own cache stays
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert "google_data_cache" not in plan


def test_partial_merge_rechecks_to_undoable(monkeypatch):
    slack = RecSlack()
    store, p = proposal()
    interactions.post_proposal(slack, store, "C", p)
    p = store.get(p["id"])
    it = interactions.Interactions(
        slack=slack, store=store,
        apply_fns={"merge": lambda *a, **k: {"ok": False, "unknown": True, "text": "stopped part-way"}},
        status_fns={"merge": lambda q: appliers.merge_status.__wrapped__(q) if hasattr(appliers.merge_status, "__wrapped__")
                    else {"state": "applied", "text": "The merge stopped part-way. Everything that moved is backed up"}})
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"])) == "approved:unknown"
    assert it.handle(click(cards.ACTION_RECHECK, p["id"], ts=p["ts"])) == "recheck:applied"
    assert "Undo merge" in json.dumps(slack.of("chat.update")[-1]["blocks"])


def test_partial_status_mapping(monkeypatch):
    monkeypatch.setenv("XANO_MERGE_URL", "https://x.example/m")
    monkeypatch.setenv("XANO_MERGE_SECRET", "s")

    class R:
        status_code = 200

        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d
    for st, want in (("partial", "applied"), ("partial_undo", "applied"), ("undone", "undone"), ("none", "none")):
        monkeypatch.setattr(requests, "post", lambda *a, _st=st, **k: R({"status": _st}))
        assert appliers.merge_status({"survivor": "V1", "losers": ["V2"]}, "p")["state"] == want, st
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    monkeypatch.setattr(requests, "post", lambda *a, **k: R({"status": "partial", "repointed_total": 4}))
    r = appliers.merge({"survivor": "V1", "losers": ["V2"], "counts": {}}, OWNER, "p", dry_run=False)
    assert r["unknown"] and "part-way" in r["text"]
    monkeypatch.setattr(requests, "post", lambda *a, **k: R({"status": "partial_undo"}))
    r = appliers.merge({"survivor": "V1", "losers": ["V2"], "counts": {}}, OWNER, "p", mode="undo", dry_run=False)
    assert r["unknown"] and "undo stopped" in r["text"]
