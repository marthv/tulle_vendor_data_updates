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
                                        lat=39.405, lng=-76.705)]
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


@pytest.mark.parametrize("q,mode", [("scan duplicates 20", "weekly"), ("scan duplicates", "weekly"),
                                    ("scan new duplicates", "nightly")])
def test_on_demand_scan_checks_everything_and_never_goes_silent(monkeypatch, q, mode):
    import scheduler
    seen = {}
    monkeypatch.setenv("DEDUPE_CHANNEL", "CDEDUPE")
    monkeypatch.setenv("QA_APPROVERS", "UKATE")
    monkeypatch.setattr(scheduler, "run_dedupe",
                        lambda bot, mode=None, max_cards=None: seen.update(mode=mode) or f"dedupe:{mode}:0")
    b, slack, store = bot()
    b.state.over_budget = lambda: False
    b.handle(mention(q, user="UKATE"))
    assert seen["mode"] == mode
    assert "No new duplicate" in slack.of("chat.postMessage")[-1]["text"]


def test_chain_in_one_state_far_apart_is_listed_not_proposed():
    rows = [dict(r) for r in ROWS] + [
        V(40, "V40", "Hilton Garden Inn", state="TX", addr="1 A St, Austin, TX", lat=30.27, lng=-97.74),
        V(41, "V41", "Hilton Garden Inn", state="TX", addr="2 B St, Dallas, TX", lat=32.78, lng=-96.80),
        V(42, "V42", "Hilton Garden Inn", state="TX", addr="3 C St, Houston, TX", lat=29.76, lng=-95.37)]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    assert b.handle(mention("find duplicates of Hilton Garden Inn")) == "merge:dupes-none"
    assert store.all() == [] and "(V41)" in slack.of("chat.postMessage")[-1]["text"]


def test_loose_name_match_never_becomes_a_group():
    rows = [dict(r) for r in ROWS] + [
        V(40, "V40", "Hilton Garden Inn", state="TX", addr="1 A St, Austin, TX", lat=30.27, lng=-97.74),
        V(41, "V41", "Hilton Garden Inn", state="TX", addr="1 A St, Austin, TX", lat=30.27, lng=-97.74)]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    out = b.handle(mention("find duplicates of Hilton"))        # nobody named "Hilton Garden Inn"
    assert out == "merge:dupes-ambiguous" and store.all() == []


def test_same_place_rules():
    a = V(1, "V1", "X", lat=39.40, lng=-76.70)
    assert mr.same_place([a, V(2, "V2", "X", lat=39.405, lng=-76.705)])
    assert not mr.same_place([a, V(3, "V3", "X", lat=39.0, lng=-77.0)])
    assert mr.same_place([V(4, "V4", "X", lat=0, lng=0, addr="1 A St, Reno, NV"),
                          V(5, "V5", "X", lat=0, lng=0, addr="9 B St, Reno, NV")])


def test_get_bot_builds_exactly_one_bot_under_concurrency(monkeypatch):
    import sys
    import threading
    import types
    import app as appmod
    built = []

    class SlowBot:
        def __init__(self):
            import time
            time.sleep(0.05)
            built.append(self)
    monkeypatch.setitem(sys.modules, "bot", types.SimpleNamespace(Bot=SlowBot))
    monkeypatch.setattr(appmod, "_bot", None)
    got = []
    ts = [threading.Thread(target=lambda: got.append(appmod.get_bot())) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(built) == 1 and all(g is built[0] for g in got)


# --- owner screenshot 11-22: Gramercy pair already had a card -----------------------------------------------------
def _gramercy_pair():
    rows = [dict(r) for r in ROWS] + [V(16506, "V16506", "Gramercy Mansion", state="MD",
                                        addr="1400 Greenspring Valley Road, Stevenson, MD", lat=39.401, lng=-76.701)]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    return b, slack, store


@pytest.mark.parametrize("status,needle", [("pending", "waiting for approval"), ("rejected", "rejected on"),
                                           ("dry_run", "dry run"), ("applied", "already merged")])
def test_existing_card_is_linked_with_its_status(status, needle):
    b, slack, store = _gramercy_pair()
    assert b.handle(mention("find duplicates of Gramercy Mansion")) == "merge:dupes:1"
    p = store.all()[0]
    if status != "pending":
        store.transition(p["id"], {"pending"}, status, "U05UJMCHUV6")
    assert b.handle(mention("find duplicates of Gramercy Mansion")) == "merge:dupes-existing"
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert needle in txt and "V16506" in txt and "different businesses" not in txt
    assert len(store.all()) == 1                                  # no second card


def test_merge_hint_never_names_the_same_id_twice():
    rows = [dict(r) for r in ROWS] + [V(16506, "V16506", "Gramercy Mansion", state="NY", addr="9 Rd, Albany, NY",
                                        lat=42.6, lng=-73.7)]
    b, slack, store = make_bot(rows=rows)
    b.xano = RowsXano()
    assert b.handle(mention("find duplicates of Gramercy Mansion")) == "merge:dupes-none"
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "Listings with the same name" in txt                          # the hint IS shown here
    import re as _re
    a, c = _re.search(r"merge (V\d+) and (V\d+)", txt).groups()
    assert a != c and {a, c} == {"V6", "V16506"}


def test_dry_run_approved_groups_are_not_proposed_again():
    b, slack, store = _gramercy_pair()
    b.handle(mention("find duplicates of Gramercy Mansion"))
    p = store.all()[0]
    store.transition(p["id"], {"pending"}, "dry_run", "UKATE")
    assert "merge:V16506|V6" in store.handled_keys("merge")
    assert b.handle(mention("merge V6 and V16506")) == "merge:approved-dry-run"
    assert len(store.all()) == 1


# --- QA 11-22: approvals made during dry run can be applied for real once dry run is off -------------------------
def _approved_in_dry_run():
    import interactions
    b, slack, store = _gramercy_pair()
    b.handle(mention("find duplicates of Gramercy Mansion"))
    p = store.all()[0]
    calls = []

    def fake_merge(payload, user, pid, mode="apply", dry_run=True):
        calls.append(dry_run)
        return {"ok": True, "dry_run": dry_run, "text": "dry" if dry_run else "Repointed 3 rows.", "result": {}}
    it = interactions.Interactions(slack=slack, store=store, apply_fns={"merge": fake_merge})
    from test_v2 import click
    import cards
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], user="U05UJMCHUV6", ts=p["ts"])) == "approved:dry_run"
    return b, slack, store, it, p, calls, click, cards


def test_dry_run_approval_stays_put_while_dry_run_is_on():
    b, slack, store, it, p, calls, click, cards = _approved_in_dry_run()
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], user="U05UJMCHUV6", ts=p["ts"])) == "noop:dry_run"
    assert calls == [True]
    assert "Apply for real" not in json.dumps(cards.render(store.get(p["id"]))[1])


def test_apply_for_real_after_dry_run_is_switched_off(monkeypatch):
    b, slack, store, it, p, calls, click, cards = _approved_in_dry_run()
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    assert "Apply for real" in json.dumps(cards.render(store.get(p["id"]))[1])
    b.handle(mention("what's waiting for approval?"))
    assert "press Apply for real" in slack.of("chat.postMessage")[-1]["text"]
    assert any(m == "chat.update" for m, _ in slack.calls)                 # card redrawn with the button
    # 2026-11-22: a real merge is never sent before the PDF list could be read and shown (stale-card guard)
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], user="U05UJMCHUV6", ts=p["ts"])) == "pdf:shown-first"
    assert calls == [True]
    store.set_pdf_check(p["id"], {"status": "ok", "visible": 0, "flags": [], "pdfs": []})
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], user="U05UJMCHUV6", ts=p["ts"])) == "approved:applied"
    assert calls == [True, False] and store.get(p["id"])["status"] == "applied"


def test_scan_skips_dry_run_approved_groups():
    from proposals import Store
    import tempfile
    from pathlib import Path
    st = Store(Path(tempfile.mkdtemp()) / "p.json")
    p = st.create("merge", "t", "w", "y", "n", {"survivor": "V1", "losers": ["V2"]}, key="merge:V1|V2")
    st.transition(p["id"], {"pending"}, "applying", "U")
    st.transition(p["id"], {"applying"}, "dry_run", "U")
    q = st.create("merge", "t", "w", "y", "n", {"survivor": "V3", "losers": ["V4"]}, key="merge:V3|V4")
    st.transition(q["id"], {"pending"}, "applying", "U")
    st.transition(q["id"], {"applying"}, "undone", "U")
    keys = st.handled_keys("merge")
    assert "merge:V1|V2" in keys and "merge:V3|V4" not in keys              # undone can be suggested again


def test_apply_for_real_is_merge_only_and_asks_to_confirm(monkeypatch):
    import cards
    import interactions
    from test_v2 import click
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    monkeypatch.setenv("VISIBILITY_DRY_RUN", "0")
    b, slack, store = _gramercy_pair()
    p = store.create("visibility", "V6: hide", "w", "y", "n", {"vendor_id": "V6", "action": "hide"}, dry_run=True)
    store.transition(p["id"], {"pending"}, "applying", "U05UJMCHUV6")
    store.transition(p["id"], {"applying"}, "dry_run", "U05UJMCHUV6")
    assert "Apply for real" not in json.dumps(cards.render(store.get(p["id"]))[1])
    calls = []
    it = interactions.Interactions(slack=slack, store=store,
                                   apply_fns={"visibility": lambda *a, **k: calls.append(1) or {"ok": True}})
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], user="U05UJMCHUV6")) == "noop:dry_run" and calls == []
    monkeypatch.delenv("DEDUPE_DRY_RUN", raising=False)                     # the merge is approved in dry run...
    b2, slack2, store2, it2, m, calls2, click2, cards2 = _approved_in_dry_run()
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")                                # ...then dry run is switched off
    blocks = json.dumps(cards2.render(store2.get(m["id"]))[1])
    assert "Apply for real" in blocks and "Merge for real?" in blocks          # confirm dialog attached
