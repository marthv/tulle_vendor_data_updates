"""Price alert on the merge card, re-check on Approve, swap which listing is kept."""
import threading
import time

import pytest
import requests

import cards
import interactions
import listing_compare as lc
import merge_checks as mc
import price_impact
import pdf_dupes as pd
from proposals import Store
from test_pdf_dupes import A, B, EXACT_ROWS, FakeXano, Sent, approve_block, live, press, row  # noqa: F401
from test_v2 import OWNER, RecSlack, click

REAL_PRICE = mc.price


def rec(total=100, alert=False, cant=(), stamp="s1"):
    return {"survivor": "V1", "losers": ["V2"], "now": {}, "after": {"total": total}, "rooms": [], "filters": None,
            "notes": [], "assumptions": [], "cant": list(cant), "reasons": ["it moved"] if alert else [], "alert": alert,
            "stamp": stamp, "seconds": 1}


@pytest.fixture
def world(monkeypatch, live):                                            # noqa: F811
    """A live merge card whose price answer the test controls through w.answer (dict or callable)."""
    class W:
        answer, seen, preview_ok = rec(), [], True
    w = W()

    def compute(xano, survivor, losers, *, preview=None, hidden_pdfs=(), deadline=0, live_check=True):
        w.seen.append((survivor, tuple(hidden_pdfs)))
        a = w.answer(survivor, hidden_pdfs) if callable(w.answer) else w.answer
        return dict(a, survivor=survivor, losers=list(losers))
    monkeypatch.setattr(price_impact, "compute", compute)
    monkeypatch.setattr(price_impact, "blocks", lambda r: {
        "alert": r["alert"], "confirm_text": "Price moves. Merge anyway?" if r["alert"] else None,
        "extra_blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"• `V1` predicted AFTER {(r['after'] or {}).get('total')}"}}]})
    monkeypatch.setattr(mc, "preview", lambda payload, hide: {
        "status": "preview", "survivor": payload["survivor"], "losers": payload["losers"], "hide_pdf_ids": hide, "refusals": [],
        "current_state": lc._shown_state(payload) if w.preview_ok else None})
    w.x = FakeXano({10: [dict(r) for r in EXACT_ROWS], 11: [dict(A), dict(B)]})
    w.store, w.slack = Store(), RecSlack()
    pl = {"survivor": "V1", "losers": ["V2"], "counts": {}, "rules": ["A"],
          "snapshots": {"V1": dict(A), "V2": dict(B)}, "pdf_check": pd.check(w.x, "V1", ["V2"])}
    pl["price"] = mc.price(w.x, pl)
    w.p = w.store.create("merge", "t", "w", "why", "n", pl, key="k",
                         plain={"headline": "h", "why": "Asked for in Slack.", "happens": "z."})
    interactions.post_proposal(w.slack, w.store, "C1", w.p)
    w.it = interactions.Interactions(slack=w.slack, store=w.store, xano=w.x)
    w.get = lambda: w.store.get(w.p["id"])
    w.sent = Sent(monkeypatch, {"status": "applied", "repointed_total": 1, "backup_id": 1, "hide_pdf_ids": ["P2"]})
    w.approve = lambda: press(w.it, w.get(), cards.ACTION_APPROVE, block_id=approve_block(w.get()))
    return w


def labels(p):
    return [e["text"]["text"] for b in cards.render(p)[1] if b["type"] == "actions" for e in b["elements"]]


def test_no_change_approves_once_and_shows_the_price(world):
    assert "AFTER 100" in str(cards.render(world.get())[1]) and cards.validate(cards.render(world.get())[1]) == []
    assert world.approve() == "approved:applied" and world.sent.body["hide_pdf_ids"] == ["P2"]
    assert world.seen == [("V1", ("P2",))] * 2                              # at posting, and again on Approve


def test_price_moved_since_the_card_reshows_it_and_sends_nothing(world):
    world.answer = rec(200, alert=True, stamp="s2")
    assert world.approve() == "price:moved" and world.sent.body is None
    q = world.get()
    ap = next(e for b in cards.render(q)[1] if b["type"] == "actions" for e in b["elements"]
              if e["action_id"] == cards.ACTION_APPROVE)
    assert q["status"] == "pending" and "Merge anyway" in ap["confirm"]["text"]["text"]
    assert "AFTER 200" in str(cards.render(q)[1])
    assert world.approve() == "approved:applied"                           # seen, confirmed, unchanged: goes ahead


def test_click_on_a_card_showing_the_old_price_is_refused(world):
    old = approve_block(world.get())
    world.answer = rec(200, alert=True, stamp="s2")
    press(world.it, world.get(), cards.ACTION_PRICE_RECHECK)
    assert press(world.it, world.get(), cards.ACTION_APPROVE, block_id=old) == "price:stale" and world.sent.body is None


def test_failure_is_an_alert_with_confirm_never_a_pass(world):
    def boom(*a):
        raise RuntimeError("x")
    world.answer = boom
    assert world.approve() == "price:moved" and world.sent.body is None
    q = world.get()
    assert q["payload"]["price"]["status"] == "failed" and "couldn't compute" in str(cards.render(q)[1])
    assert q["payload"]["price"]["confirm_text"] and "Check price again" in labels(q)
    # a failed check holds no preview, and nothing is ever sent without Xano's preview of this merge
    assert world.approve() == "price:no-preview" and world.sent.body is None


def test_cant_compute_with_a_good_preview_can_be_confirmed(world):
    world.answer = dict(rec(None), cant=["x"], alert=True, reasons=["can't compute: x"], after=None)
    press(world.it, world.get(), cards.ACTION_PRICE_RECHECK)
    assert world.get()["payload"]["price"]["cant"] and world.approve() == "approved:applied"


def test_no_preview_no_merge(world):
    world.preview_ok = False
    press(world.it, world.get(), cards.ACTION_PRICE_RECHECK)
    assert world.approve() == "price:no-preview" and world.sent.body is None
    world.preview_ok = True
    assert world.approve() == "price:no-preview" and world.approve() == "approved:applied"     # asked again, seen, approved


def test_preview_that_disagrees_with_the_card_stops_it(world):
    pl = world.store._data["proposals"][world.p["id"]]["payload"]
    pl["price"]["preview"]["current_state"]["V2"]["Address"] = "moved"
    assert world.approve() == "price:no-preview" and world.sent.body is None
    import appliers
    out = appliers.merge(world.get()["payload"], OWNER, "p", dry_run=False)
    assert not out["ok"] and "Nothing was changed" in out["text"] and world.sent.body is None


def test_cant_compute_with_a_different_after_price_is_reshown():
    assert not mc.moved({"cant": True, "after_total": None, "stamp": "a"}, {"cant": True, "after_total": None, "stamp": "b"})
    assert mc.moved({"cant": True, "after_total": 1}, {"cant": True, "after_total": 2})
    assert mc.moved({"cant": False, "stamp": "a"}, {"cant": True, "stamp": "failed"})
    assert mc.moved({"cant": True, "stamp": "failed"}, {"cant": False, "stamp": "a"})


def test_choice_change_recomputes_with_the_new_hide_list(world):
    world.answer = lambda s, hide: rec(100 if hide else 130, alert=not hide, stamp="h" if hide else "k")
    press(world.it, world.get(), cards.ACTION_PRICE_RECHECK)
    assert press(world.it, world.get(), cards.ACTION_PDF_KEEP, f"{world.p['id']}|1") == "pdf:keep"
    assert world.seen[-1] == ("V1", ()) and "AFTER 130" in str(cards.render(world.get())[1])
    assert world.get()["payload"]["price"]["hide"] == []


def test_while_computing_there_is_no_approve_and_an_old_answer_never_wins(world):
    pid = world.p["id"]
    world.store.set_price(pid, mc.computing(world.get()["payload"]))
    ls = labels(world.get())
    assert "Approve and apply" not in ls and "Reject" in ls and "Check price again" in ls
    assert not mc.current(world.get()["payload"])
    stale = dict(world.p["payload"]["price"], hide=["P9"])
    _, why = world.store.set_price(pid, stale, only_if=lambda now: mc.current(dict(now, price=stale)))
    assert why == "superseded" and world.get()["payload"]["price"]["status"] == "computing"
    assert press(world.it, world.get(), cards.ACTION_APPROVE, block_id=f"tb:{pid}:h:computing") == "price:shown-first"
    assert world.sent.body is None


def test_card_without_a_price_is_checked_before_it_can_run(world):
    world.store._data["proposals"][world.p["id"]]["payload"].pop("price")
    assert world.approve() == "price:shown-first" and world.sent.body is None
    assert world.approve() == "approved:applied"


def test_swap_rebuilds_everything_for_the_other_listing(world):
    q = world.get()
    assert "Keep the duplicate instead" in labels(q)
    world.answer = lambda s, hide: rec(100 if s == "V1" else 55, stamp=s)
    assert press(world.it, q, cards.ACTION_SWAP, f"{q['id']}|1") == "swapped"
    q = world.get()
    pl = q["payload"]
    assert (pl["survivor"], pl["losers"]) == ("V2", ["V1"]) and pl["price"]["stamp"] == "V2"
    assert pl["pdf_check"]["flags"][0]["keep_pdf_id"] == "P2" and pl["price"]["hide"] == ["P1"]      # new Primary kept
    assert q["plain"]["why"] == "Asked for in Slack." and q["key"] == "k"
    assert world.approve() == "approved:applied"
    assert world.sent.body["survivor_vendor_id"] == "V2" and world.sent.body["loser_vendor_ids"] == ["V1"]
    assert set(world.sent.body["expected_state"]) == {"V1", "V2"}
    assert press(world.it, world.get(), cards.ACTION_SWAP, f"{q['id']}|1") == "noop:swap"            # decided


def test_swap_needs_an_approver_and_live_listings(world):
    q = world.get()
    assert world.it.handle(click(cards.ACTION_SWAP, f"{q['id']}|1", user="USOMEONE", ts=q["ts"])) == "denied:not-approver"
    world.x.tables[11][1]["merged_into"] = "V9"
    assert press(world.it, q, cards.ACTION_SWAP, f"{q['id']}|1") == "swap:failed"
    assert world.get()["payload"]["survivor"] == "V1"
    _, why = world.store.replace_merge(q["id"], {"payload": {"survivor": "V2"}}, OWNER, "V7")        # swapped meanwhile
    assert why


def test_one_price_check_at_a_time_and_waiting_fails_to_the_alert(monkeypatch):
    running, peak, lock = [0], [0], threading.Lock()

    def slow(xano, payload, hide, pv):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.1)
        with lock:
            running[0] -= 1
        return {"status": "ok"}
    monkeypatch.setattr(mc, "_compute", slow)
    ts = [threading.Thread(target=REAL_PRICE, args=(FakeXano({}), {"survivor": "V1", "losers": ["V2"]})) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] == 1
    hold = threading.Thread(target=REAL_PRICE, args=(FakeXano({}), {"survivor": "V1", "losers": ["V2"]}))
    hold.start()
    time.sleep(0.03)
    got = REAL_PRICE(FakeXano({}), {"survivor": "V1", "losers": ["V2"]}, wait=0.01)
    hold.join()
    assert got["status"] == "failed" and got["alert"] and got["confirm_text"]


def test_hang_becomes_the_alert(monkeypatch):
    monkeypatch.setattr(mc, "PREVIEW_TIMEOUT", 0)
    ev = threading.Event()
    monkeypatch.setattr(mc, "_compute", lambda *a: ev.wait(30))
    t = threading.Thread(target=lambda: None)
    real_join = threading.Thread.join
    monkeypatch.setattr(threading.Thread, "join", lambda self, timeout=None: real_join(self, 0.1))
    got = REAL_PRICE(FakeXano({}), {"survivor": "V1", "losers": ["V2"]})
    monkeypatch.setattr(threading.Thread, "join", real_join)
    ev.set()
    time.sleep(0.1)
    assert got["status"] == "failed" and "too long" in got["confirm_text"]


def test_without_quickjs_or_function_access_it_is_the_alert():
    """The real price_impact against a Xano that answers 403 to everything (no function-read access on Railway)."""
    class Deny(FakeXano):
        def get(self, url, **kw):
            return type("R", (), {"status_code": 403, "json": lambda s: {}})()
    got = REAL_PRICE(Deny({}, status=403), {"survivor": "V1", "losers": ["V2"]})
    assert got["alert"] and got["cant"] and got["confirm_text"] and "can't compute" in str(got["extra_blocks"])


def test_preview_is_read_only_and_bad_previews_are_ignored(monkeypatch, live):                        # noqa: F811
    sent = Sent(monkeypatch, {"status": "preview", "survivor": "V1", "losers": ["V2"], "refusals": ["gold chip"]})
    assert mc.preview({"survivor": "V1", "losers": ["V2"]}, ["P2"])["status"] == "preview"
    assert sent.body["mode"] == "preview" and sent.body["hide_pdf_ids"] == ["P2"]
    Sent(monkeypatch, {"status": "applied"})
    assert mc.preview({"survivor": "V1", "losers": ["V2"]}, []) is None
    monkeypatch.delenv("XANO_MERGE_URL")
    assert mc.preview({"survivor": "V1", "losers": ["V2"]}, []) is None


def test_preview_refusals_are_shown(monkeypatch):
    monkeypatch.setattr(mc, "preview", lambda p, h: {"refusals": [
        "a duplicate (V2) holds the gold chip, which a merge never moves; pass allow_gold_loser to merge anyway",
        "florists with 2 priced quotes after the merge are not merged yet: x", "stale card: x"]})
    monkeypatch.setattr(price_impact, "compute", lambda *a, **k: rec())
    monkeypatch.setattr(price_impact, "blocks", lambda r: {"extra_blocks": [], "confirm_text": None, "alert": False})
    got = REAL_PRICE(FakeXano({}), {"survivor": "V1", "losers": ["V2"]})
    assert "Xano would refuse" in str(got["extra_blocks"]) and "Florists with" in str(got["extra_blocks"])
    assert "gold chip" not in str(got["extra_blocks"])          # answered by its own button on the card
