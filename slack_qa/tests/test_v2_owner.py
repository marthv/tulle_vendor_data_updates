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


@pytest.fixture(autouse=True)
def _allow_hidden_members(monkeypatch):
    """The shared fixture pair has a hidden member on purpose (visibility plan, undo, cards). The scan holds such pairs
    since 2026-11-22 (tested in test_merge_scan.py); these tests are about what a card does once it exists."""
    monkeypatch.setattr(dedupe, "HOLD_HIDDEN", False)


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
    # QA 2026-11-22: the card also carries a side-by-side section, mismatch banner and the PDF check line; the plain
    # lines themselves stay free of ids and table names
    assert any(b.get("fields") for b in blocks)
    assert main.startswith("These look like the same venue: *The Plaza* (New York) listed twice.")
    assert "Same Google listing." not in main         # QA 2026-11-22: never the only evidence
    assert "same street address" in main and "linked to the same Google listing" in main
    assert "We'll keep the visible listing, move its 3 PDFs" in main and "1 couple' plans" not in main
    assert "The duplicate is hidden from search, not deleted." in main and "add up its clicks" in main
    assert "undo" not in main.lower()                 # dry-run card: there will be nothing to undo
    ctx = " ".join(b["elements"][0]["text"] for b in blocks if b["type"] == "context")
    assert "check visibility" not in (main + ctx).lower()     # line removed 2026-11-20 (merge hides the duplicate)
    for bad in (r"\bV\d+", r"\btable\b", "Place ID", "row", "Vendor_ID", "repoint"):
        assert not re.search(bad, main, re.I), bad
    labels = [e["text"]["text"] for b in blocks if b["type"] == "actions" for e in b["elements"]]
    assert labels.pop(0) == "I checked: merge it anyway" and labels.pop(0) == "OK to merge the gold-chip listing"
    assert labels == ["Approve and apply", "Reject", "Keep the duplicate instead", "Run PDF check again",
                      "Details"]                                           # FakeX: PDFs unreadable


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
    assert "google_data_cache" not in snaps["V13433"] and "User_Email" not in snaps["V13433"]
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
    assert "snapshots" not in sent and "counters" not in sent          # the endpoint recomputes from live rows
    assert sent["survivor_vendor_id"] == "V105" and sent["loser_vendor_ids"] == ["V13433"]


# 3. counters ---------------------------------------------------------------------------------------------------
def test_counter_rules():
    s, l = dict(ROWS[0]), dict(ROWS[1])
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert plan["Clicks_on_Venue"]["after"] == 340 and plan["Clicks_on_Venue"]["before"] == {"V105": 300, "V13433": 40}
    assert "gold_chip" not in plan and "Validated_Data" not in plan     # owner-only: never auto-written
    assert plan["Clicks_on_Venue"]["losers_after"] == 0
    assert plan["Collection"]["after"] == ["Hotel", "Ballroom"]
    assert plan["Max_Capacity_Seated"]["after"] == 650
    assert plan["Time_of_Submission"]["after"] == "2024-06-01"
    assert plan["Description"]["after"] == "Iconic hotel"
    assert plan["Flags"]["after"] == "x, y"
    assert "flt_space_count" not in plan             # empty on both records: nothing to plan
    s["flt_space_count"] = 2
    assert {c["column"]: c for c in dedupe.counter_plan(s, [l])}["flt_space_count"]["after"] == "recomputed"
    assert "Website" not in plan                    # survivor has one: kept


def _empty_refs(ids):
    refs = dedupe.RefIndex(type("X", (), {"get": lambda self, path, params=None: {"items": [], "nextPage": None}})(),
                           max_pages=50)
    refs.build(set(ids))
    return refs


def test_merge_hides_the_duplicate_owner_exception_2026_11_20():
    s, l = V(1, "V1", "A", vis="1", pid="P"), V(2, "V2", "A", vis="1", pid="P")
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert "Validated_Data" not in plan and "gold_chip" not in plan     # visibility has its own plan; gold_chip never
    assert dedupe.visibility_plan(s, [l]) == [{"vendor_id": "V2", "role": "loser", "before": "1", "after": "0"}]
    pr = dedupe.build_proposal({"ids": [1, 2], "rules": ["A"]}, {1: s, 2: l}, _empty_refs({"V1", "V2"}))
    assert pr["payload"]["visibility"] == [{"vendor_id": "V2", "role": "loser", "before": "1", "after": "0"}]
    assert "check" not in pr["plain"] and pr["plain"]["hidden"] == "The duplicate is hidden from search, not deleted"
    assert "gold_chip" not in json.dumps(pr["payload"]["visibility"])


def test_visibility_plan_survivor_shown_only_when_a_duplicate_was_visible():
    hidden_s, vis_l = V(1, "V1", "A", vis="0"), V(2, "V2", "A", vis="1")
    plan = dedupe.visibility_plan(hidden_s, [vis_l])
    assert {"vendor_id": "V1", "role": "survivor", "before": "0", "after": "1"} in plan
    assert {"vendor_id": "V2", "role": "loser", "before": "1", "after": "0"} in plan
    # both hidden: survivor untouched, loser already "0" -> nothing to write
    assert dedupe.visibility_plan(V(1, "V1", "A", vis="0"), [V(2, "V2", "A", vis="0")]) == []
    # a blank loser value is still set "0" (backed up as blank); survivor not shown since no loser was visible
    assert dedupe.visibility_plan(V(1, "V1", "A", vis="0"), [V(2, "V2", "A", vis="")]) ==         [{"vendor_id": "V2", "role": "loser", "before": "", "after": "0"}]


def test_plural_hidden_wording_and_details_show_visibility():
    rows = [V(1, "V1", "A", vis="1", pid="P"), V(2, "V2", "A", vis="1", pid="P"), V(3, "V3", "A", vis="0", pid="P")]
    pr = dedupe.build_proposal({"ids": [1, 2, 3], "rules": ["A"]}, {r["id"]: r for r in rows},
                               _empty_refs({"V1", "V2", "V3"}))
    assert pr["plain"]["hidden"] == "The duplicates are hidden from search, not deleted"
    det = " ".join(dedupe.details_text(pr))
    assert "V2 (loser) 1 → 0" in det and "V3" not in det.split("Visibility")[1].split("Owner-only")[0]
    assert "gold_chip" in det and "Check visibility after merging" not in det


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
    assert hits({"note": "same as V13433 and V134330"}, "json", {"V13433"}, {}) == set()   # free text: never
    assert hits({"vendor_id": "V13433"}, "json", {"V13433"}, {}) == {"V13433"}
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
    assert '$input.mode == "status"' in doc and "`partial`" in doc and "live - merged_value + survivor_before" in doc


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
    s.update({"google_data_last_fetched": "2026-11-19"})
    l = V(2, "V2", "A", pid="P2")
    l.update({"google_data_last_fetched": "2026-01-01"})
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert plan["Place_ID"]["after"] == "P2" and plan["google_data_last_fetched"]["after"] == "2026-01-01"
    s["Place_ID"] = "P1"                              # survivor keeps its own listing -> its own fetch date stays
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert "google_data_last_fetched" not in plan


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
    for st, want in (("partial", "partial"), ("partial_undo", "applied"), ("undone", "undone"), ("none", "none")):
        monkeypatch.setattr(requests, "post", lambda *a, _st=st, **k: R({"status": _st}))
        assert appliers.merge_status({"survivor": "V1", "losers": ["V2"]}, "p")["state"] == want, st
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    monkeypatch.setattr(requests, "post", lambda *a, **k: R({"status": "partial", "repointed_total": 4}))
    r = appliers.merge({"survivor": "V1", "losers": ["V2"], "counts": {}}, OWNER, "p", dry_run=False)
    assert r.get("partial") and "part-way" in r["text"]
    monkeypatch.setattr(requests, "post", lambda *a, **k: R({"status": "partial_undo"}))
    r = appliers.merge({"survivor": "V1", "losers": ["V2"], "counts": {}}, OWNER, "p", mode="undo", dry_run=False)
    assert r["unknown"] and "undo stopped" in r["text"]



# QA round 3 ---------------------------------------------------------------------------------------------------------
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def test_r3_no_email_in_details_or_store():
    rows = [dict(r) for r in ROWS]
    rows[0].update({"User_Email": "couple.one@gmail.com", "Contact_Information": "events@theplaza.com"})
    rows[1].update({"User_Email": "bride2026@gmail.com", "Contact_Information": "",
                    "Description": "Ask sales@plaza.com", "google_data_cache": {"reviews": [{"author": "x@y.com"}]}})
    store = Store()
    p = dedupe.scan(FakeX(), store, mode="weekly", vendor_rows=rows)["proposals"][0]
    slack = RecSlack()
    interactions.post_proposal(slack, store, "C", p)
    q = store.get(p["id"])
    it = interactions.Interactions(slack=slack, store=store)
    assert it.handle(click(cards.ACTION_DETAILS, q["id"], ts=q["ts"])) == "details:posted"
    blob = json.dumps(slack.calls) + store.path.read_text(encoding="utf-8") + store.log_path.read_text(encoding="utf-8")
    assert not EMAIL.search(blob), EMAIL.search(blob).group(0)
    assert "User_Email" not in json.dumps(q["payload"]["snapshots"])


def test_r3_json_matching_is_by_key_only():
    hits = dedupe.RefIndex._hits
    assert hits({"img": "https://vault.example/V12/photo.jpg", "note": "V12 engine"}, "json", {"V12"}, {}) == set()
    assert hits('{"cards": [{"vendor_id": "V12", "vendor_idx": 5}]}', "json", {"V12"}, {}) == {"V12"}
    assert hits("V12 is great", "json", {"V12"}, {}) == set()            # not JSON at all
    assert hits({"matched": {"vendor_idx": 5}}, "json", {"V12"}, {5: "V12"}) == {"V12"}


def test_r3_time_of_submission_parses_us_format():
    s = V(1, "V1", "A", pid="P")
    s["Time_of_Submission"] = "10/2/2025, 4:05:06 PM"
    l = V(2, "V2", "A", pid="P")
    l["Time_of_Submission"] = "9/30/2025, 11:00:00 AM"           # earlier, but "9/30" > "10/2" as text
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert plan["Time_of_Submission"]["after"] == "9/30/2025, 11:00:00 AM"
    assert dedupe.parse_when("2024-06-01") < dedupe.parse_when("9/30/2025, 11:00:00 AM")


def test_r3_undo_wording_only_when_undo_possible(monkeypatch):
    store, p = proposal()
    main = card_text(cards.render(p)[1])
    assert "undo" not in main.lower()                              # dry run: nothing to undo
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    store2 = Store(store.path.with_name("p2.json"))
    p2 = dedupe.scan(FakeX(), store2, mode="weekly", vendor_rows=[dict(r) for r in ROWS])["proposals"][0]
    assert "The duplicate is hidden from search, not deleted, and you can undo this from the card." in         card_text(cards.render(p2)[1])
    p2["status"], p2["decided_by"], p2["undoable"] = "applied", OWNER, False
    assert "undo" not in card_text(cards.render(p2)[1]).lower()    # undo no longer possible: no undo promise
    p2["status"], p2["decided_by"] = "rejected", OWNER
    assert "undo" not in card_text(cards.render(p2)[1]).lower()
    assert "listed as a conflict" in json.dumps(cards.UNDO_CONFIRM)


def test_r3_partial_shows_resume_and_undo_and_resume_works():
    slack = RecSlack()
    store, p = proposal()
    interactions.post_proposal(slack, store, "C", p)
    p = store.get(p["id"])
    calls = []

    def fake(payload, user, pid, mode="apply", dry_run=True):
        calls.append(mode)
        if len(calls) == 1:
            return {"ok": False, "partial": True, "text": "stopped part-way"}
        return {"ok": True, "dry_run": False, "text": "done"}
    it = interactions.Interactions(slack=slack, store=store, apply_fns={"merge": fake})
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"])) == "approved:partial"
    labels = [e["text"]["text"] for b in slack.of("chat.update")[-1]["blocks"] if b["type"] == "actions"
              for e in b["elements"]]
    assert labels[:2] == ["Resume merge", "Undo merge"]
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"])) == "approved:applied"
    assert calls == ["apply", "apply"]


def test_r3_partial_can_be_undone():
    slack = RecSlack()
    store, p = proposal()
    interactions.post_proposal(slack, store, "C", p)
    store.transition(p["id"], {"pending"}, "partial", OWNER)
    it = interactions.Interactions(slack=slack, store=store,
                                   apply_fns={"merge": lambda *a, **k: {"ok": True, "dry_run": False, "text": "back"}})
    assert it.handle(click(cards.ACTION_UNDO, p["id"], ts=store.get(p["id"])["ts"])) == "undo:ok"
    assert store.get(p["id"])["status"] == "undone"


def test_r3_pricing_phrase_ignores_rebuilt_spaces():
    class OnlySpaces(FakeX):
        def __init__(self):
            super().__init__()
            self.tables = {62: [{"id": 1, "VENDOR_ID": "V13433"}]}
    store = Store()
    p = dedupe.scan(OnlySpaces(), store, mode="weekly", vendor_rows=[dict(r) for r in ROWS])["proposals"][0]
    assert "pricing" not in p["plain"]["happens"]


def test_r3_details_claim_is_locked():
    store, p = proposal()
    assert store.claim_details(p["id"]) is True
    assert store.claim_details(p["id"]) is False
    store.finish_details(p["id"], None)                      # failed post -> can try again
    assert store.claim_details(p["id"]) is True


def test_r3_doc_states_undo_and_resume_rules():
    doc = (Path(__file__).resolve().parent.parent / "XANO_MERGE_PROPOSAL.md").read_text(encoding="utf-8")
    for want in ("live - merged_value + survivor_before", "compares each row's LIVE value",
                 "computes `snapshots`", "gold_chip is owner-only and is never written", "Owner exception, 2026-11-20",
                 "Visibility on undo", "Later step (documented, NOT built): hard-delete", "partial_undo` is always finishable",
                 "by KEY only"):
        assert want in doc, want


def test_undone_or_rejected_merge_card_does_not_claim_hidden(monkeypatch):
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    store, p = proposal()
    assert "hidden from search" in card_text(cards.render(p)[1])
    for st in ("applied", "partial"):
        p["status"], p["decided_by"] = st, OWNER
        assert "hidden from search, not deleted, and you can undo" in card_text(cards.render(p)[1])
    for st in ("undone", "undoing", "rejected", "post_failed"):
        p["status"], p["decided_by"] = st, OWNER
        assert "hidden from search" not in card_text(cards.render(p)[1]), st


def test_parse_when_airtable_long_form_2026_11_22():
    assert dedupe.parse_when("February 8, 2025 at 4:50 PM") is not None
    assert dedupe.parse_when("February 8, 2025 at 4:50 PM") < dedupe.parse_when("2026-01-05")
    s = V(1, "V1", "A", pid="P")
    s["Time_of_Submission"] = "2026-01-05"
    l = V(2, "V2", "A", pid="P")
    l["Time_of_Submission"] = "February 8, 2025 at 4:50 PM"
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert plan["Time_of_Submission"]["after"] == "February 8, 2025 at 4:50 PM"


def test_earliest_keeps_survivor_when_a_date_is_unparseable_2026_11_22():
    s = V(1, "V1", "A", pid="P")
    s["Time_of_Submission"] = "2026-01-05"
    l = V(2, "V2", "A", pid="P")
    l["Time_of_Submission"] = "sometime last spring"
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    # survivor keeps its own value, so the column is unchanged and not planned at all
    assert plan.get("Time_of_Submission", {"after": "2026-01-05"})["after"] == "2026-01-05"
