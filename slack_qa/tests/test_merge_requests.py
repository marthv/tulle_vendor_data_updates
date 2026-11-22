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
