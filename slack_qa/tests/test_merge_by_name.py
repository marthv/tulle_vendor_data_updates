"""'@Tulle Bot merge <venue name>' (owner 2026-11-27: "name based merges: build it"). One strong duplicate group of 2 ->
the normal merge card (same checks, same Approve click); anything less clear -> the candidates and the exact command, no
card; nothing -> say so. Planted bad cases: same name in different states / categories must never become a card."""
import json

import pytest
import requests

import merge_requests as mr
from fakes import mention
from test_merge_requests import ROWS, V, RowsXano
from test_vendor_actions import make_bot

FRESNO = [V(200, "V200", "Fresno Fields", state="CA", addr="10 Field Rd, Fresno, CA", lat=36.74, lng=-119.78,
            pid="PF", clicks=50),
          V(201, "V201", "Fresno Fields", state="CA", addr="10 Field Road, Fresno, CA", lat=36.7401, lng=-119.7801,
            pid="PF", clicks=3)]
MAKOY = [V(300, "V300", "The Makoy", state="OH", addr="5462 Center St, Hilliard, OH", lat=40.03, lng=-83.16, clicks=9),
         V(301, "V301", "The Makoy", state="OH", addr="5462 Center St, Hilliard, OH", lat=40.03, lng=-83.16, clicks=1)]
# two different venues with one name, two states (planted: must NOT produce a card)
TWIN = [V(400, "V400", "Twin Oaks", state="CA", addr="1 Oak Ln, Napa, CA", lat=38.3, lng=-122.3, clicks=20),
        V(401, "V401", "Twin Oaks", state="TX", addr="9 Oak Ln, Waco, TX", lat=31.5, lng=-97.1, clicks=20)]
# same name, same address, different categories (planted: must NOT produce a card or a command)
BLOOM = [V(500, "V500", "Bloom House", cat="Venue", addr="7 Rose St, Austin, TX", state="TX", lat=30.2, lng=-97.7),
         V(501, "V501", "Bloom House", cat="Florist", addr="7 Rose St, Austin, TX", state="TX", lat=30.2, lng=-97.7)]
# one name, two separate strong pairs (CA and TX): ambiguous
ROSE = [V(600, "V600", "Rose Barn", state="CA", addr="1 A St, Chico, CA", lat=39.7, lng=-121.8, pid="R1", clicks=5),
        V(601, "V601", "Rose Barn", state="CA", addr="1 A St, Chico, CA", lat=39.7, lng=-121.8, pid="R1"),
        V(602, "V602", "Rose Barn", state="TX", addr="2 B St, Tyler, TX", lat=32.3, lng=-95.3, pid="R2", clicks=7),
        V(603, "V603", "Rose Barn", state="TX", addr="2 B St, Tyler, TX", lat=32.3, lng=-95.3, pid="R2")]
# three listings of one venue: a 3-way merge is listed, not carded
TRIPLE = [V(700 + i, f"V{700 + i}", "Hall Seven", state="NV", addr="7 Main St, Reno, NV", lat=39.5, lng=-119.8,
            pid="H7") for i in range(3)]
SINGLE = [V(800, "V800", "Lonely Lodge", state="MT", addr="1 Pine Rd, Bozeman, MT", lat=45.7, lng=-111.0)]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network write attempted in a test")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.delenv("DEDUPE_DRY_RUN", raising=False)


def bot(*extra):
    rows = [dict(r) for r in ROWS] + [dict(r) for group in extra for r in group]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    return b, slack, store


def texts(slack):
    return [m.get("text") or "" for m in slack.of("chat.postMessage")]


# --- parsing --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q,target", [("merge Fresno Fields", "Fresno Fields"), ("merge the Makoy", "the Makoy"),
                                      ("merge Fresno Fields please", "Fresno Fields"),
                                      ("please merge Fresno Fields, thanks", "Fresno Fields"),
                                      ("can you merge Fresno Fields?", "Fresno Fields")])
def test_parse_one_name(q, target):
    r = mr.parse(q)
    assert r == {"kind": "merge_incomplete", "target": target}


def test_polite_tail_does_not_break_two_name_merges():
    assert mr.parse("merge V12 and V13 please")["rest"] == "V12 and V13"


@pytest.mark.parametrize("t,ok", [("Fresno Fields", True), ("the Makoy", True), ("these", False), ("them", False),
                                  ("the two", False), ("both", False), ("V12", True)])
def test_looks_like_name(t, ok):
    assert mr.looks_like_name(t) is ok


# --- one strong pair -> the normal card ----------------------------------------------------------------------------
def test_one_strong_pair_posts_the_normal_card_and_says_who_is_kept():
    b, slack, store = bot(FRESNO)
    assert b.handle(mention("merge Fresno Fields")) == "merge:proposed"
    [p] = store.all()
    assert p["kind"] == "merge" and p["status"] == "pending"           # never auto-approved
    assert p["payload"]["survivor"] == "V200" and p["payload"]["losers"] == ["V201"]
    assert p["payload"]["origin"] == "suggested"                         # the scan's live re-check ran
    lead = next(t for t in texts(slack) if "Found one duplicate pair" in t)
    assert "keeping V200 because couples click it more" in lead and "V201 would merge into it" in lead
    card = json.dumps(slack.of("chat.update")[-1]["blocks"])
    assert "tb_approve" in card and "Dry run is on" in card               # same card, same Approve click


@pytest.mark.parametrize("q", ["merge the Makoy", "merge The Makoy please", "merge Makoy"])
def test_the_makoy_by_name(q):
    b, slack, store = bot(MAKOY)
    assert b.handle(mention(q)) == "merge:proposed", q
    [p] = store.all()
    assert p["payload"]["survivor"] == "V300" and p["payload"]["losers"] == ["V301"]


def test_asking_again_links_the_open_card():
    b, slack, store = bot(FRESNO)
    b.handle(mention("merge Fresno Fields"))
    assert b.handle(mention("merge Fresno Fields")) == "merge:duplicate"
    assert len(store.all()) == 1


def test_a_rejected_pair_is_not_reproposed_by_name():
    b, slack, store = bot(FRESNO)
    b.handle(mention("merge Fresno Fields"))
    p = store.all()[0]
    store.transition(p["id"], {"pending"}, "rejected", "U05UJMCHUV6")
    assert b.handle(mention("merge Fresno Fields")) == "merge:name-turned-down"
    assert "`@Tulle Bot merge V200 and V201`" in texts(slack)[-1] and len(store.all()) == 1


def test_by_id_alone_finds_its_pair():
    b, slack, store = bot(FRESNO)
    assert b.handle(mention("merge V201")) == "merge:proposed"
    assert store.all()[0]["payload"]["survivor"] == "V200"


def test_pair_with_a_same_name_listing_elsewhere_still_cards_and_names_the_other():
    other = [V(202, "V202", "Fresno Fields", state="NY", addr="1 Rd, Albany, NY", lat=42.6, lng=-73.7)]
    b, slack, store = bot(FRESNO, other)
    assert b.handle(mention("merge Fresno Fields")) == "merge:proposed"
    lead = next(t for t in texts(slack) if "Found one duplicate pair" in t)
    assert "Not included" in lead and "V202 (NY)" in lead
    assert sorted([store.all()[0]["payload"]["survivor"]] + store.all()[0]["payload"]["losers"]) == ["V200", "V201"]


# --- ambiguous -> candidates + exact commands, no card --------------------------------------------------------------
def test_two_strong_groups_list_candidates_with_commands():
    b, slack, store = bot(ROSE)
    assert b.handle(mention("merge Rose Barn")) == "merge:name-candidates"
    t = texts(slack)[-1]
    assert store.all() == []
    for vid in ("V600", "V601", "V602", "V603"):
        assert f"({vid})" in t
    assert "`@Tulle Bot merge V600 and V601`" in t and "`@Tulle Bot merge V602 and V603`" in t
    assert "Chico, CA" in t and "7 clicks" in t


def test_three_listings_are_listed_not_carded():
    b, slack, store = bot(TRIPLE)
    assert b.handle(mention("merge Hall Seven")) == "merge:name-candidates"
    assert store.all() == [] and "`@Tulle Bot merge V700 and V701 and V702`" in texts(slack)[-1]


def test_weak_same_place_match_lists_a_command_but_no_card():
    weak = [V(900, "V900", "Gramercy Mansion", state="MD", addr="Other Rd, Stevenson, MD", lat=39.41, lng=-76.71)]
    b, slack, store = bot(weak)
    assert b.handle(mention("merge Gramercy Mansion")) == "merge:name-candidates"
    t = texts(slack)[-1]
    assert store.all() == [] and "`@Tulle Bot merge V6 and V900`" in t and "Compare the addresses" in t


def test_hidden_duplicate_is_listed_not_carded():
    b, slack, store = bot()                     # ROWS: Allied Arts Guild V13433 is hidden
    assert b.handle(mention("merge Allied Arts Guild")) == "merge:name-candidates"
    t = texts(slack)[-1]
    assert store.all() == [] and "hidden from couples" in t and "(V13433)" in t


# --- planted bad cases: must NOT produce a card ---------------------------------------------------------------------
def test_same_name_different_states_never_cards_or_offers_a_command():
    b, slack, store = bot(TWIN)
    assert b.handle(mention("merge Twin Oaks")) == "merge:name-candidates"
    t = texts(slack)[-1]
    assert store.all() == [] and slack.of("chat.update") == []
    assert "(V400)" in t and "(V401)" in t and "different venues" in t
    assert "merge V400 and V401" not in t and "merge V401 and V400" not in t


def test_same_name_same_address_different_categories_never_cards():
    b, slack, store = bot(BLOOM)
    assert b.handle(mention("merge Bloom House")) == "merge:name-candidates"
    t = texts(slack)[-1]
    assert store.all() == [] and "merge V500 and V501" not in t and "different venues" in t


def test_loose_name_never_cards():
    b, slack, store = bot(FRESNO)
    assert b.handle(mention("merge Fresno")) == "merge:name-close"
    assert store.all() == [] and "No listing is called exactly" in texts(slack)[-1]


def test_by_name_pure_rules():
    rows = [dict(r) for r in ROWS + TWIN + FRESNO]
    seeds, _ = mr.name_seeds(rows, "Twin Oaks")
    assert mr.by_name(rows, seeds)["kind"] == "list" and mr.weak_sets(mr.by_name(rows, seeds)["weak"]) == []
    seeds, _ = mr.name_seeds(rows, "Fresno Fields")
    assert mr.by_name(rows, seeds)["kind"] == "card"


# --- nothing --------------------------------------------------------------------------------------------------------
def test_unknown_name_says_so():
    b, slack, store = bot()
    assert b.handle(mention("merge Nowhere Barn")) == "merge:name-not-found"
    assert "can't find a listing called" in texts(slack)[-1] and store.all() == []


def test_single_listing_with_no_duplicates_says_so():
    b, slack, store = bot(SINGLE)
    assert b.handle(mention("merge Lonely Lodge")) == "merge:name-none"
    assert "Nothing to merge" in texts(slack)[-1] and store.all() == []


@pytest.mark.parametrize("q", ["can you merge these?", "merge them", "merge both please"])
def test_non_names_still_go_to_normal_answers(q):
    b, slack, store = bot()
    out = b.handle(mention(q))
    assert not out.startswith("merge:"), (q, out)
