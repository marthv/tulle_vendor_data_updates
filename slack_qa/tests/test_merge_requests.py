"""Merges by name / ID and 'find duplicates of X' (owner 2026-11-22). Same merge card as the nightly scan; nothing
moves until an approver presses Approve (dry run until the Xano merge is switched on)."""
import json

import pytest
import requests

import dedupe
import merge_requests as mr
from fakes import mention
from test_vendor_actions import make_bot


def V(i, vid, name, cat="Venue", state="CA", addr="1 Main St, Menlo Park, CA", lat=37.45, lng=-122.18, pid="",
      web="", vis="1", clicks=0):
    return {"id": i, "Vendor_ID": vid, "Name": name, "Category": cat, "State": state, "Address": addr, "lat": lat,
            "lng": lng, "Place_ID": pid, "Website": web, "Validated_Data": vis, "Clicks_on_Venue": clicks,
            "Max_Capacity_Seated": 100, "Time_of_Submission": "10/2/2026"}


ROWS = [V(105, "V105", "Allied Arts Guild", pid="P1", clicks=40),
        V(13433, "V13433", "Allied Arts Guild", pid="P1", vis="0", clicks=2),
        V(6, "V6", "Gramercy Mansion", state="MD", addr="1400 Greenspring Valley Rd, Stevenson, MD", lat=39.4,
          lng=-76.7),
        V(7, "V7", "Gramercy at Lakeside Manor", state="NJ", addr="1 Lake Rd, Sparta, NJ", lat=41.0, lng=-74.6),
        V(8, "V8", "Rose Hall", cat="Photographer", addr="2 Elm St, Austin, TX", lat=30.2, lng=-97.7),
        V(9, "V9", "Smith and Co", addr="3 Oak St, Reno, NV", lat=39.5, lng=-119.8),
        V(10, "V10", "Smith & Co Events", addr="4 Pine St, Reno, NV", lat=39.6, lng=-119.9)]


class RowsXano:
    enabled = True

    def rows(self, table_id):
        return ([], False)                    # reference tables: empty (counts 0); table 11 comes from the directory

    def get(self, path, params=None):
        return {"items": [], "nextPage": None, "itemsTotal": 0}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network write attempted in a test")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.delenv("DEDUPE_DRY_RUN", raising=False)


def bot():
    b, slack, store = make_bot(rows=[dict(r) for r in ROWS])
    b.xano = RowsXano()
    return b, slack, store


@pytest.mark.parametrize("q,kind", [
    ("merge Gramercy Mansion and Gramercy at Lakeside Manor", "merge"),
    ("merge V13433 into V105", "merge"),
    ("Rose Hall and Rose Hall Barn are the same venue", "merge"),
    ("find duplicates of Gramercy Mansion", "dupes_of"),
    ("is Rose Hall listed twice?", "dupes_of"),
    ("any duplicates for V5?", "dupes_of"),
    ("merge V12", "merge_incomplete"),
])
def test_parse(q, kind):
    assert mr.parse(q)["kind"] == kind


@pytest.mark.parametrize("q", ["why did we merge those two?", "we should merge the planner and cost tools",
                               "scan duplicates 20", "what is a duplicate?"])
def test_parse_leaves_other_text_alone(q):
    assert mr.parse(q) is None


def test_merge_by_name_posts_the_scan_card_in_thread_dry_run():
    b, slack, store = bot()
    assert b.handle(mention("merge Smith and Co and Smith & Co Events")) == "merge:proposed"
    [p] = store.all()
    assert p["kind"] == "merge" and sorted([p["payload"]["survivor"]] + p["payload"]["losers"]) == ["V10", "V9"]
    card = json.dumps(slack.of("chat.postMessage")[-1]["blocks"])
    assert "Asked for in Slack" in card and "Dry run is on" in card and "tb_approve" in card


def test_into_keeps_the_second_one():
    b, slack, store = bot()
    b.handle(mention("merge V105 into V13433"))
    p = store.all()[0]
    assert p["payload"]["survivor"] == "V13433" and p["payload"]["losers"] == ["V105"]
    assert "the listing you named to keep" in p["plain"]["happens"]


def test_without_into_the_scan_rules_choose_the_survivor():
    b, slack, store = bot()
    b.handle(mention("merge V13433 and V105"))
    assert store.all()[0]["payload"]["survivor"] == "V105"        # the visible one


def test_ambiguous_and_unknown_names_ask_first():
    b, slack, store = bot()
    assert b.handle(mention("merge Gramercy and V6")) == "merge:unresolved"
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "Which one?" in txt and "(V6)" in txt and "(V7)" in txt and store.all() == []
    assert b.handle(mention("merge Nowhere Barn and V6")) == "merge:unresolved"
    assert "can't find a vendor" in slack.of("chat.postMessage")[-1]["text"]


def test_different_categories_refused():
    b, slack, store = bot()
    assert b.handle(mention("merge V8 and V6")) == "merge:refused"
    assert "different categories" in slack.of("chat.postMessage")[-1]["text"] and store.all() == []


def test_second_request_points_at_open_card_and_rejected_can_be_asked_again():
    b, slack, store = bot()
    b.handle(mention("merge V9 and V10"))
    assert b.handle(mention("merge V10 and V9")) == "merge:duplicate"
    p = store.all()[0]
    store.transition(p["id"], {"pending"}, "rejected", "U05UJMCHUV6")
    assert b.handle(mention("merge V9 and V10")) == "merge:proposed"     # a person asking again is a new decision


def test_find_duplicates_of_a_vendor_posts_its_groups():
    b, slack, store = bot()
    assert b.handle(mention("find duplicates of V105")) == "merge:dupes:1"
    p = store.all()[0]
    assert sorted([p["payload"]["survivor"]] + p["payload"]["losers"]) == ["V105", "V13433"]
    assert "Found 1 possible duplicate group" in slack.of("chat.postMessage")[0]["text"]


def test_find_duplicates_none_lists_same_name_elsewhere():
    rows = [dict(r) for r in ROWS] + [V(11, "V11", "Gramercy Mansion", state="NY", addr="9 Rd, Albany, NY",
                                        lat=42.6, lng=-73.7)]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    assert b.handle(mention("find duplicates of V6")) == "merge:dupes-none"
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "No new duplicates" in txt and "(V11)" in txt and store.all() == []


def test_incomplete_merge_gets_a_hint():
    b, slack, store = bot()
    assert b.handle(mention("merge V12")) == "merge:incomplete"


def test_scan_accepts_a_batch_size(monkeypatch):
    import scheduler
    seen = {}
    monkeypatch.setenv("DEDUPE_CHANNEL", "CDEDUPE")
    monkeypatch.setenv("QA_APPROVERS", "UKATE")
    monkeypatch.setattr(scheduler, "run_dedupe", lambda bot, mode=None, max_cards=None: seen.update(n=max_cards) or "ok")
    b, slack, store = bot()
    b.state.over_budget = lambda: False
    b.handle(mention("scan duplicates 20", user="UKATE"))
    assert seen.get("n") == 20


class R200:
    status_code = 200

    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


def test_live_merge_reply_shows_restored_and_conflicts(monkeypatch):
    import appliers
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    monkeypatch.setenv("XANO_MERGE_URL", "https://x/api:aow91bcd/vendor/merge")
    monkeypatch.setenv("XANO_MERGE_SECRET", "s")
    sent = {}
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: sent.update(json) or R200(
        {"status": "undone", "restored_total": 5, "backup_id": 87, "undo_conflicts": [{"t": 1}]}))
    r = appliers.merge({"survivor": "V105", "losers": ["V13433"], "counts": {"t10": 1}}, "U1", "p1", mode="undo",
                       dry_run=False)
    assert r["ok"] and "Restored 5 values" in r["text"] and "1 value had been changed since" in r["text"]
    assert set(sent) == {"mode", "survivor_vendor_id", "loser_vendor_ids", "proposal_id", "approved_by",
                         "expected_counts", "secret"}                       # = ep578's inputs


# --- QA 11-22 round 2 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q", ["merge the planner and cost tools", "combine the two cards and post again",
                               "can you merge these?", "any duplicates for this week?", "is the bot listed twice?"])
def test_non_vendor_text_falls_through_to_normal_answers(q):
    b, slack, store = bot()
    out = b.handle(mention(q))
    assert not out.startswith("merge:"), (q, out)
    assert store.all() == []


def test_two_valid_readings_ask_instead_of_guessing():
    rows = [dict(r) for r in ROWS] + [V(20, "V20", "Smith", addr="5 Fir St, Reno, NV", lat=39.4, lng=-119.7),
                                      V(21, "V21", "Co and Jones", addr="6 Ash St, Reno, NV", lat=39.3, lng=-119.6),
                                      V(22, "V22", "Jones", addr="7 Elm St, Reno, NV", lat=39.2, lng=-119.5)]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    assert b.handle(mention("merge Smith and Co and Jones")) == "merge:unresolved"
    assert "more than one way" in slack.of("chat.postMessage")[-1]["text"] and store.all() == []


def test_into_must_be_last():
    b, slack, store = bot()
    assert b.handle(mention("merge V9 into V10 and V105")) == "merge:bad-into"


def test_long_lists_are_refused_quickly():
    parts = " and ".join(f"Name{i}" for i in range(30))
    vendors, err, _ = mr.split_targets(parts, lambda t: (None, []))
    assert vendors is None and "too many" in err


def test_reask_after_reject_does_not_stack_cards():
    b, slack, store = bot()
    b.handle(mention("merge V9 and V10"))
    p = store.all()[0]
    store.transition(p["id"], {"pending"}, "rejected", "U05UJMCHUV6")
    assert b.handle(mention("merge V9 and V10")) == "merge:proposed"
    assert b.handle(mention("merge V9 and V10")) == "merge:duplicate"      # the 'asked again' card is still open


def test_trailing_question_mark_and_batch_number():
    assert mr.parse("merge V12 and V13?")["rest"] == "V12 and V13"


def test_merged_vendor_cannot_be_unhidden_or_edited():
    rows = [dict(r) for r in ROWS] + [V(30, "V30", "Old Dup", vis="0")]
    rows[-1]["merged_into"] = "V105"
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    assert b.handle(mention("unhide V30")) == "vendor:visibility:merged"
    assert b.handle(mention("change V30 website to x.com")) == "vendor:vendor_edit:merged"
    assert store.all() == []


# --- owner 2026-11-22: three fixes after the first live try -------------------------------------------------------
def test_same_name_same_state_listings_become_one_card():
    rows = [dict(r) for r in ROWS] + [V(16506, "V16506", "Gramercy Mansion", state="MD",
                                        addr="1400 Greenspring Valley Road, Stevenson, MD", lat=39.41, lng=-76.71)]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    out = b.handle(mention("find duplicates of Gramercy Mansion"))
    assert out == "merge:dupes:1", out
    p = store.all()[0]
    assert sorted([p["payload"]["survivor"]] + p["payload"]["losers"]) == ["V16506", "V6"]


def test_same_name_different_states_are_listed_not_proposed():
    rows = [dict(r) for r in ROWS] + [V(16506, "V16506", "Gramercy Mansion", state="NY", addr="9 Rd, Albany, NY",
                                        lat=42.6, lng=-73.7)]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    assert b.handle(mention("find duplicates of Gramercy Mansion")) == "merge:dupes-none"
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "(V6)" in txt and "(V16506)" in txt and store.all() == []


def test_same_name_card_says_why_and_points_to_details():
    rows = [dict(r) for r in ROWS] + [V(16506, "V16506", "Gramercy Mansion", state="MD", addr="Other Rd, MD",
                                        lat=39.0, lng=-77.0)]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    b.handle(mention("find duplicates of Gramercy Mansion"))
    assert "compare the addresses under Details" in store.all()[0]["plain"]["why"]


def test_wait_note_reflects_a_cold_vendor_list():
    b, slack, store = bot()
    b.feedback.directory.fresh = lambda: False
    assert "about 3 minutes the first time" in b._wait_note()
    b.feedback.directory.fresh = lambda: True
    assert b._wait_note() == "Checking the listings..."


def test_warm_loads_the_vendor_list_and_never_raises():
    b, slack, store = bot()
    assert b.warm() == f"warm:{len(ROWS)}"

    class Boom:
        def rows(self):
            raise RuntimeError("xano down")
    b.feedback.directory = Boom()
    assert b.warm() == "warm:error"
