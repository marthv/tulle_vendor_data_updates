"""Fixes for the independent bot QA (qa_bot/report.md, 2026-11-23) and ep578 v5. QA's own tests are test_qa_bot.py."""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import appliers
import cards
import config
import interactions
import listing_compare as lc
import merge_checks as mc
import pdf_dupes as pd
import proposals
import scheduler
from proposals import Store
from test_merge_checks import world  # noqa: F401
from test_merge_requests import bot, mention  # noqa: F401
from test_pdf_dupes import A, B, EXACT_ROWS, Sent, approve_block, live, press, row  # noqa: F401
from test_v2 import OWNER, RecSlack, click

REAL_SUBMIT = mc.submit


def said(world):
    return [p for m, p in world.slack.calls if m == "chat.postEphemeral"][-1]["text"]


# 1. the approver's card, under the lock -----------------------------------------------------------------------------
def test_every_part_of_the_card_is_compared_under_the_lock():
    s = Store()
    base = {"survivor": "V1", "losers": ["V2"], "allow": {}, "price": {"status": "ok", "stamp": "a", "hide": []},
            "pdf_check": {"flags": [{"hide_pdf_id": "P2", "keep_pdf_id": "P1", "choice": "hide"}]}}
    import copy
    for change in (lambda p: p.update(survivor="V2", losers=["V1"]), lambda p: p.update(losers=["V2", "V3"]),
                   lambda p: p["pdf_check"]["flags"][0].update(choice="keep"), lambda p: p["allow"].update(hidden=True),
                   lambda p: p["price"].update(stamp="b"), lambda p: p["price"].update(hide=["P2"]),
                   lambda p: p["price"].update(status="computing")):
        p = s.create("merge", "t", "w", "y", "n", copy.deepcopy(base))
        want = proposals.fingerprint(p["payload"])
        change(s._data["proposals"][p["id"]]["payload"])
        q, why = s.transition(p["id"], {"pending"}, "applying", "U", expect=want)
        assert why == s.CHANGED and q["status"] == "pending" and s.get(p["id"])["status"] == "pending"
    p = s.create("merge", "t", "w", "y", "n", copy.deepcopy(base))
    assert s.transition(p["id"], {"pending"}, "applying", "U", expect=proposals.fingerprint(p["payload"]))[1] == ""


def test_refused_click_reshows_the_card_and_says_so(world, monkeypatch):                            # noqa: F811
    real = mc.price

    def slow(x, pl, *a, **k):
        if "pv" in k:
            world.store.set_pdf_choice(world.p["id"], 1, "keep", "UB")
        return real(x, pl, *a, **k)
    monkeypatch.setattr(mc, "price", slow)
    assert world.approve() == "stale:changed" and world.sent.body is None and world.get()["status"] == "pending"
    assert "Nothing was sent" in said(world)
    assert "keep both" in str([p for m, p in world.slack.calls if m == "chat.update"][-1])


def test_stale_message_with_the_same_stamp_but_the_other_direction_is_refused(world):                # noqa: F811
    old = approve_block(world.get())
    q = world.store._data["proposals"][world.p["id"]]["payload"]
    q["survivor"], q["losers"] = "V2", ["V1"]
    q["price"]["survivor"] = "V2"
    q["price"]["preview"]["current_state"] = lc._shown_state(q)
    assert press(world.it, world.get(), cards.ACTION_APPROVE, block_id=old).startswith(("stale", "price", "pdf"))
    assert world.sent.body is None


# 2. old cards and the scan's rules --------------------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["pending", "dry_run"])
def test_card_from_an_older_build_is_voided(world, status):                                         # noqa: F811
    st = world.store._data["proposals"][world.p["id"]]
    st.pop("build")
    st["status"] = status
    assert "can't be approved" in str(cards.render(world.get())[1]) and "tb_approve" not in str(cards.render(world.get())[1])
    assert press(world.it, world.get(), cards.ACTION_APPROVE, block_id=f"tb:{st['id']}") == "void"
    assert world.get()["status"] == "failed" and world.sent.body is None and "fresh" in said(world)


def test_old_card_does_not_block_a_fresh_one():
    s = Store()
    p = s.create("merge", "t", "w", "y", "n", {"survivor": "V1", "losers": ["V2"]}, key="merge:V1|V2")
    assert s.create("merge", "t", "w", "y", "n", {}, key="merge:V1|V2") is None and "merge:V1|V2" in s.handled_keys("merge")
    for status in ("pending", "dry_run"):
        s._data["proposals"][p["id"]].update(status=status)
        s._data["proposals"][p["id"]].pop("build", None)
        assert "merge:V1|V2" not in s.handled_keys("merge")
    assert s.create("merge", "t", "w", "y", "n", {}, key="merge:V1|V2")
    s._data["proposals"][p["id"]]["status"] = "rejected"                    # a human's no still counts, whatever the build
    assert "merge:V1|V2" in s.handled_keys("merge")


BAD = [({"State": "Ohio"}, "different states"), ({"lat": 34.0}, "km apart"), ({"Place_ID": "G9"}, "different Google listings"),
       ({"merged_into": "V9"}, "already merged"), ({"gold_chip": True}, "gold chip"), ({"Validated_Data": "0"}, "hidden")]


@pytest.mark.parametrize("origin", ["named", "suggested"])
@pytest.mark.parametrize("change,word", BAD)
def test_hard_checks_run_on_live_rows_for_every_card(world, origin, change, word):                  # noqa: F811
    world.store._data["proposals"][world.p["id"]]["payload"]["origin"] = origin
    world.x.tables[11] = [dict(A), dict(B, **change)]
    assert world.approve() in ("rules:refused", "gold:refused") and world.sent.body is None
    assert word in said(world) and world.get()["status"] == "pending"


def test_name_conflict_is_a_warning_for_a_named_card_and_a_stop_for_a_suggested_one(world):         # noqa: F811
    pl = world.store._data["proposals"][world.p["id"]]["payload"]
    pl["snapshots"]["V2"]["Name"] = "Pine Mill"
    pl["price"]["preview"]["current_state"] = lc._shown_state(pl)
    world.x.tables[11] = [dict(A), dict(B, Name="Pine Mill")]
    assert world.approve() == "rules:refused" and "ask for it by name" in said(world)
    pl["origin"] = "named"
    assert world.approve() == "approved:applied"


def test_live_rows_that_cant_be_read_send_nothing(world):                                           # noqa: F811
    world.x.status = 500
    assert world.approve() == "rules:refused" and "couldn't re-read" in said(world) and world.sent.body is None


def test_origin_is_set_where_the_card_is_made():
    import dedupe

    class Refs:
        counts, rows, not_counted, xano, pdf_names = {}, {}, {t for t, *_ in dedupe.REF_TABLES}, None, {}

        def pdfs(self):
            return {}
    by = {1: dict(A), 2: dict(B)}
    for sug in (True, False):
        p = dedupe.build_proposal({"ids": [1, 2], "rules": []}, by, Refs(), suggested=sug)
        assert p["payload"]["origin"] == ("suggested" if sug else "named")


# 3. replies we don't recognise: QA's tests; plus the exact words -------------------------------------------------------------
def test_unrecognised_4xx_never_says_nothing_changed(world, monkeypatch):                           # noqa: F811
    Sent(monkeypatch, {"message": "Error parsing JSON"}, 400)
    assert world.approve() == "approved:unknown"
    t = world.get()["result_text"]
    assert "can't tell" in t and "othing was changed" not in t
    ls = [e["text"]["text"] for b in cards.render(world.get())[1] if b["type"] == "actions" for e in b["elements"]]
    assert "Re-check" in ls


# 4. stuck cards ------------------------------------------------------------------------------------------------------------------
def test_sweep_threshold_is_longer_than_a_merge(monkeypatch):
    for v in ("1", "0", "5", "30"):
        monkeypatch.setenv("QA_STALE_APPLY_MIN", v)
        assert config.stale_apply_min() * 60 > config.merge_timeout()


def test_sweep_runs_in_the_background_at_start_up_and_not_on_the_request(monkeypatch):
    import app
    from fastapi.testclient import TestClient
    swept, where = threading.Event(), []

    class B:
        slack, store = RecSlack(), Store()

        def warm(self):
            return ""
    monkeypatch.setenv("QA_WARM_ON_START", "0")
    monkeypatch.setattr(interactions.Interactions, "sweep", lambda self: (where.append(threading.current_thread().name),
                                                                           swept.set()) and [])
    app.set_bot(B())
    try:
        with TestClient(app.app):
            assert swept.wait(5)
    finally:
        app.set_bot(None)
        mc._STOP.clear()
    assert set(where) == {"sweep"}


@pytest.mark.parametrize("status", ["applying", "undoing"])
def test_stuck_card_is_recovered_by_the_next_sweep_then_rechecked(world, status):                   # noqa: F811
    st = world.store._data["proposals"][world.p["id"]]
    st["status"], st["status_at"] = status, (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    assert world.it.sweep() == []                                              # may still be running
    st["status_at"] = (datetime.now(timezone.utc) - timedelta(minutes=config.stale_apply_min() + 1)).isoformat()
    assert world.it.sweep() == [world.p["id"]] and world.get()["status"] == "unknown"
    assert press(world.it, world.get(), cards.ACTION_RECHECK) == "recheck:applied"


# 6. shutdown and visible errors ---------------------------------------------------------------------------------------------------
def test_queued_checks_stop_at_shutdown(monkeypatch):
    ran, gate = [], threading.Event()
    monkeypatch.setattr(mc, "_STOP", threading.Event())
    monkeypatch.setattr(mc, "_WORKER", [])
    REAL_SUBMIT(lambda: (ran.append(1), gate.wait(5)))
    time.sleep(0.3)
    for _ in range(5):
        REAL_SUBMIT(lambda: ran.append(2))
    mc.stop()
    gate.set()
    mc._WORKER[0].join(5)
    assert ran == [1] and not mc._WORKER[0].is_alive() and mc._JOBS.empty() and mc._WORKER[0].daemon
    REAL_SUBMIT(lambda: ran.append(3))
    assert mc._JOBS.empty()


def test_a_broken_check_is_visible_to_the_approver(world, monkeypatch):                             # noqa: F811
    monkeypatch.setattr(mc, "current", lambda *a: 1 / 0)
    assert world.approve() == "gate:error" and world.sent.body is None and world.get()["status"] == "pending"
    assert "Could not check this merge, so nothing was sent" in said(world)


def test_a_broken_click_is_visible(world, monkeypatch):                                             # noqa: F811
    monkeypatch.setattr(world.store, "set_pdf_choice", lambda *a: 1 / 0)
    ev = click(cards.ACTION_PDF_KEEP, f"{world.p['id']}|1", user=OWNER, ts=world.get()["ts"])
    assert world.it.handle_safe(ev) == "error" and "Something went wrong" in said(world)


# 7. pilot limiter ---------------------------------------------------------------------------------------------------------------------
class ScanBot:
    xano = type("X", (), {"enabled": True})()

    def __init__(self):
        self.store, self.slack = Store(), RecSlack()


def test_max_cards_zero_means_none(monkeypatch):
    monkeypatch.setenv("DEDUPE_CHANNEL", "C1")
    monkeypatch.setenv("DEDUPE_MAX_CARDS", "0")
    assert config.dedupe_max_cards() == 0
    import dedupe
    monkeypatch.setattr(dedupe, "scan", lambda *a, **k: 1 / 0)
    assert scheduler.run_dedupe(ScanBot()) == "dedupe:off"
    b, slack, store = bot()
    monkeypatch.setenv("QA_APPROVERS", "UKATE")
    b.state.over_budget = lambda: False
    b.handle(mention("scan duplicates 20", user="UKATE"))
    assert "switched off" in slack.of("chat.postMessage")[-1]["text"]


def test_scheduled_scan_is_off_by_default(monkeypatch):
    assert config.dedupe_scheduled_scan() is False


def test_scan_without_a_number_only_counts(monkeypatch):
    import dedupe
    rows = [dict(A), dict(B)]
    s = Store()
    out = dedupe.scan(ScanBot.xano, s, mode="weekly", max_cards=0, vendor_rows=rows)
    assert out["fresh"] == 1 and out["proposals"] == [] and s.all() == [] and s.meta("dedupe_max_id") is None
    monkeypatch.setenv("DEDUPE_CHANNEL", "C1")
    monkeypatch.setenv("QA_APPROVERS", "UKATE")
    seen = []
    monkeypatch.setattr(scheduler, "run_dedupe", lambda bot, mode=None, max_cards=None: seen.append(max_cards) or
                        (f"dedupe:{mode}:count:7" if max_cards == 0 else "dedupe:ok"))
    b, slack, store = bot()
    b.state.over_budget = lambda: False
    b.handle(mention("scan duplicates", user="UKATE"))
    assert seen == [0] and "7 possible duplicate groups" in slack.of("chat.postMessage")[-1]["text"]
    b.handle(mention("scan duplicates 90", user="UKATE"))
    assert seen == [0, 25]


# ep578 v5 ------------------------------------------------------------------------------------------------------------------------------
def test_md5s_are_sent_with_preview_and_apply_when_drive_read_them(world, monkeypatch):             # noqa: F811
    m1, m2 = "a" * 32, "b" * 32
    pdfs = world.store._data["proposals"][world.p["id"]]["payload"]["pdf_check"]["pdfs"]
    for p, m in zip(pdfs, (m1, m2, pd.EMPTY_MD5)):
        p["md5"] = m
    assert world.approve() == "approved:applied"
    assert world.sent.body["pdf_md5"] == {"P1": m1, "P2": m2}                 # never the md5 of an empty file
    pl = world.get()["payload"]
    for p in pl["pdf_check"]["pdfs"]:
        p["md5"] = ""
    assert pd.md5_map(pl) == {}


def test_preview_sends_md5(monkeypatch, live):                                                      # noqa: F811
    sent = Sent(monkeypatch, {"status": "preview", "survivor": "V1", "losers": ["V2"]})
    mc.preview.__wrapped__ if hasattr(mc.preview, "__wrapped__") else None
    pl = {"survivor": "V1", "losers": ["V2"], "pdf_check": {"pdfs": [{"pdf_id": "P1", "md5": "c" * 32}]}}
    assert mc.preview(pl, []) and sent.body["pdf_md5"] == {"P1": "c" * 32} and "allow_gold_loser" not in sent.body


def test_kept_newer_is_information(world, monkeypatch):                                             # noqa: F811
    world.store._data["proposals"][world.p["id"]]["status"] = "applied"
    Sent(monkeypatch, {"status": "undone", "restored_total": 3, "kept_newer": [{"row_id": 1}, {"row_id": 2}]})
    press(world.it, world.get(), cards.ACTION_UNDO)
    q = world.get()
    assert "2 newer last-viewed values were left as they are" in q["result_text"]
    assert ":warning:" not in cards.outcome_line(q) and "Undo again" not in str(cards.render(q)[1])


@pytest.mark.parametrize("v,y", [("2026", 2026), ("2025-2026", 2026), ("2025, 2026", 2026), ("2025-26", 0), ("2025/26", 0),
                                 ("2,025", 0), ("No date listed", 0), ("", 0), ("12026", 0), ("1999", 0), ("2101", 0)])
def test_year_labels_read_the_way_v5_reads_them(v, y):
    import price_impact
    assert pd.label_year(v) == y and price_impact._label_year({"Year_of_Pricing": v}) == y


def test_same_content_under_another_drive_file_does_not_take_over():
    rows = [row(1, year="2023"), row(2, "V2", year="2026")]
    pl = {"survivor": "V1", "losers": ["V2"], "pdf_check": {"pdfs": [dict(pd.Pdf(r).public(), md5="d" * 32) for r in rows]}}
    assert lc.primary_after(pl)["expected"]["row_id"] == 1
    sup = [row(1, year="2023"), row(3, year="2024", assign="Supplemental 1"), row(2, "V2", year="2026")]
    ps = [pd.Pdf(r).public() for r in sup]
    ps[1]["md5"] = ps[2]["md5"] = "e" * 32                     # the duplicate's Primary is the kept listing's Supplemental
    assert lc.primary_after({"survivor": "V1", "losers": ["V2"], "pdf_check": {"pdfs": ps}})["expected"]["row_id"] == 1
