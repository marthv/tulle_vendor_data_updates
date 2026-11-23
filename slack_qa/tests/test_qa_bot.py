"""QA's own tests (merge-bot branch, 68e42d9). Run from <checkout>/slack_qa with PYTHONPATH=.;tests"""
import itertools, json, threading, time
import pytest, requests
import appliers, cards, config, interactions, listing_compare as lc, merge_checks as mc, pdf_dupes as pd, price_impact
from proposals import Store
from test_merge_checks import world, rec  # noqa
from test_pdf_dupes import A, B, EXACT_ROWS, FakeXano, Sent, approve_block, live, press, row  # noqa
from test_v2 import OWNER, RecSlack, click

@pytest.fixture(autouse=True)
def _c(tmp_path, monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "x"); monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("XANO_MERGE_RECHECK_GRACE", "0"); monkeypatch.setattr(mc, "submit", lambda fn: fn())

class Net:
    """records every request to the merge endpoint"""
    def __init__(s, mp, reply=None, status=200):
        s.modes = []; s.bodies = []
        class R:
            status_code = status
            def json(self): return reply or {"status": "applied", "repointed_total": 1}
        def post(url, json=None, headers=None, timeout=None):
            s.modes.append((json or {}).get("mode")); s.bodies.append(json); return R()
        mp.setattr(requests, "post", post)

ALL = [cards.ACTION_APPROVE, cards.ACTION_UNDO, cards.ACTION_RECHECK, cards.ACTION_SWAP, cards.ACTION_ALLOW,
       cards.ACTION_PDF_HIDE, cards.ACTION_PDF_KEEP, cards.ACTION_PDF_RECHECK, cards.ACTION_PRICE_RECHECK, cards.ACTION_DETAILS]
STATUSES = ["pending","dry_run","applying","applied","undoing","undone","unknown","partial","failed","rejected","post_failed"]

@pytest.mark.parametrize("flag", [None, "", "1", "true", "O", "flase", " 1 "])
@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("pdry", [True, False])
def test_dry_run_never_sends_apply_or_undo(world, monkeypatch, flag, status, pdry):
    monkeypatch.delenv("DEDUPE_DRY_RUN") if flag is None else monkeypatch.setenv("DEDUPE_DRY_RUN", flag)
    net = Net(monkeypatch, {"status": "applied"})
    for a in ALL:
        st = world.store._data["proposals"][world.p["id"]]
        st["status"], st["dry_run"] = status, pdry
        st["result"] = {"warnings": ["w"]}
        v = world.p["id"] + ("|1" if a in (cards.ACTION_SWAP, cards.ACTION_PDF_HIDE, cards.ACTION_PDF_KEEP) else "|hidden" if a == cards.ACTION_ALLOW else "")
        try: press(world.it, world.get(), a, value=v, block_id=approve_block(world.get()) if a==cards.ACTION_APPROVE and status=="pending" else "")
        except Exception: pass
    assert not {"apply", "undo"} & set(net.modes), net.modes

@pytest.mark.parametrize("a", [x for x in ALL if x != cards.ACTION_DETAILS])
@pytest.mark.parametrize("status", ["pending", "dry_run", "applied", "partial", "unknown", "undone"])
def test_non_approver_nothing(world, monkeypatch, a, status):
    net = Net(monkeypatch); st = world.store._data["proposals"][world.p["id"]]; st["status"] = status
    before = json.dumps(st, sort_keys=True)
    out = world.it.handle(click(a, world.p["id"] + "|1", user="UEVIL", ts=world.get()["ts"]))
    assert out == "denied:not-approver" and net.modes == [] and json.dumps(st, sort_keys=True) == before

def _race(world, monkeypatch, other):
    """Approver A clicks Approve; while A's price re-check runs, approver B's click lands AND finishes first."""
    bid = approve_block(world.get()); net = Net(monkeypatch, {"status": "applied", "repointed_total": 1})
    real, done = mc.price, []
    def slow(x, pl, *a, **k):
        if "pv" in k and not done:          # A's re-check on Approve
            done.append(1); other()
        return real(x, pl, *a, **k)
    monkeypatch.setattr(mc, "price", slow)
    out = press(world.it, world.get(), cards.ACTION_APPROVE, block_id=bid)
    return out, net

def test_race_keep_both_during_approve(world, monkeypatch):
    out, net = _race(world, monkeypatch, lambda: press(world.it, world.get(), cards.ACTION_PDF_KEEP, value=world.p["id"] + "|1"))
    sent = [b for b in net.bodies if b["mode"] == "apply"]
    print(out, sent)
    assert not sent, "apply went out with PDF choices the approver never saw"

def test_race_price_changed_for_new_choice(world, monkeypatch):
    """worse: the new choice makes a PRICE ALERT the approver never confirmed"""
    world.answer = lambda s, hid: rec(100) if hid else rec(500, alert=True, stamp="zz")
    out, net = _race(world, monkeypatch, lambda: press(world.it, world.get(), cards.ACTION_PDF_KEEP, value=world.p["id"] + "|1"))
    sent = [b for b in net.bodies if b["mode"] == "apply"]
    assert not sent, "apply went out with an unconfirmed price alert"

def test_race_allow_toggle_during_approve(world, monkeypatch):
    for d in (world.store._data["proposals"][world.p["id"]]["payload"],):
        d["allow"] = {}
    def other():
        world.store._data["proposals"][world.p["id"]]["payload"]["allow"] = {"hidden": True}
    out, net = _race(world, monkeypatch, other)
    sent = [b for b in net.bodies if b["mode"] == "apply"]
    assert not sent or "allow_hidden_loser" not in sent[0]

def test_race_swap_during_approve(world, monkeypatch):
    import dedupe
    def other():
        q = json.loads(json.dumps(world.get()["payload"])); q.update(survivor="V2", losers=["V1"])
        monkeypatch.setattr(dedupe, "rebuild_for_swap", lambda *a, **k: {"payload": {**q, "pdf_check": pd.check(world.x, "V2", ["V1"]), "price": None}, "title": "t"})
        print(press(world.it, world.get(), cards.ACTION_SWAP, value=world.p["id"] + "|1"))
    out, net = _race(world, monkeypatch, other)
    sent = [(b["survivor_vendor_id"], b["loser_vendor_ids"]) for b in net.bodies if b["mode"] == "apply"]
    assert not sent, f"merge went out in the direction the approver did not see: {sent}"

def test_old_card_flip_needs_all_gates(world, monkeypatch):
    """card from the OLD bot: no price, no pdf_check, plain block id, approved in dry run, then flag off"""
    st = world.store._data["proposals"][world.p["id"]]
    st["status"], st["dry_run"] = "dry_run", True
    st["payload"].pop("price"); st["payload"].pop("pdf_check")
    net = Net(monkeypatch)
    outs = [press(world.it, world.get(), cards.ACTION_APPROVE, block_id="tb:" + st["id"])]
    assert outs == ["pdf:shown-first"] and "apply" not in net.modes
    assert press(world.it, world.get(), cards.ACTION_APPROVE, block_id="tb:" + st["id"]) in ("price:stale", "pdf:stale")
    assert "apply" not in net.modes

def test_old_rule_pair_different_states_is_stopped_at_apply(world, monkeypatch):
    """V1913 Boston / V9876 NYC canary: a card the OLD scan made and someone approved in dry run. Flag goes off."""
    st = world.store._data["proposals"][world.p["id"]]
    st["status"], st["dry_run"], st["source"] = "dry_run", True, "dedupe:weekly"
    a, b = st["payload"]["snapshots"]["V1"], st["payload"]["snapshots"]["V2"]
    a.update(State="Massachusetts", Address="776 Boylston St, Boston, MA 02199, USA", lat=42.348, lng=-71.08)
    b.update(State="New York", Address="80 Columbus Cir, New York, NY 10023, USA", lat=40.769, lng=-73.983)
    st["payload"]["price"] = mc.price(world.x, st["payload"])
    net = Net(monkeypatch)
    bl = next(x for x in cards.render(world.get())[1] if x["type"] == "actions" and x["elements"][0]["action_id"] == cards.ACTION_APPROVE)["block_id"]
    press(world.it, world.get(), cards.ACTION_APPROVE, block_id=bl)
    assert "apply" not in net.modes, "banner only; nothing in code stops a two-state pair"

def test_no_snapshots_card(world, monkeypatch):
    st = world.store._data["proposals"][world.p["id"]]; st["payload"].pop("snapshots"); st["payload"].pop("price")
    net = Net(monkeypatch); out = press(world.it, world.get(), cards.ACTION_APPROVE, block_id="tb:" + st["id"])
    assert "apply" not in net.modes, "sent with no price, no pdf check, no expected_state"

@pytest.mark.parametrize("reply,status,want", [({}, 200, "unknown"), ({"status": ""}, 200, "unknown"), ({"status": "weird"}, 200, "unknown"),
     ({"message": "Error parsing JSON / db error mid-run", "code": "ERROR_CODE_INPUT_ERROR"}, 400, "unknown"), ({"message":"Too many"}, 429, "failed"), ({}, 502, "unknown")])
def test_unrecognised_replies(world, monkeypatch, reply, status, want):
    Net(monkeypatch, reply, status)
    if reply == {}:
        class R:
            status_code = status
            def json(self): return {}
        monkeypatch.setattr(requests, "post", lambda *a, **k: R())
    out = world.approve()
    assert out == f"approved:{want}", world.get()["result_text"]

def test_timeout_unknown_no_resend(world, monkeypatch):
    n = []
    def post(*a, **k): n.append(k["json"]["mode"]); raise requests.Timeout()
    monkeypatch.setattr(requests, "post", post)
    assert world.approve() == "approved:unknown"
    for _ in range(3): press(world.it, world.get(), cards.ACTION_APPROVE); press(world.it, world.get(), cards.ACTION_RECHECK); press(world.it, world.get(), cards.ACTION_UNDO)
    assert n.count("apply") == 1 and n.count("undo") == 0

def test_double_click_two_people(world, monkeypatch):
    monkeypatch.setenv("QA_APPROVERS", f"{OWNER},UKATE"); net = Net(monkeypatch)
    bid, ts = approve_block(world.get()), world.get()["ts"]
    def go(u):
        ev = click(cards.ACTION_APPROVE, world.p["id"], user=u, ts=ts); ev["actions"][0]["block_id"] = bid; world.it.handle(ev)
    T = [threading.Thread(target=go, args=(u,)) for u in (OWNER, "UKATE", OWNER, "UKATE")]
    [t.start() for t in T]; [t.join() for t in T]
    assert net.modes.count("apply") == 1

def test_stuck_applying_after_restart_within_15min(world):
    """restart mid-merge, first click comes 2 min later -> sweep skips it; a click at 20 min must still recover it"""
    from datetime import datetime, timezone, timedelta
    st = world.store._data["proposals"][world.p["id"]]; st["status"] = "applying"
    st["status_at"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    it = interactions.Interactions(slack=world.slack, store=world.store, xano=world.x)     # lazily, on first click
    st["status_at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    press(it, world.get(), cards.ACTION_RECHECK)
    assert world.get()["status"] != "applying"

def test_alert_exceptions_never_send(world, monkeypatch):
    for target, name in ((mc, "current"), (mc, "moved"), (lc, "preview_state"), (lc, "primary_mismatch"), (lc, "gold_block"), (lc, "allow_missing"), (pd, "recheck_live"), (pd, "version")):
        with monkeypatch.context() as m:
            net = Net(m); bid = approve_block(world.get())
            def boom(*a, **k): raise ValueError("x")
            m.setattr(target, name, boom)
            ev = click(cards.ACTION_APPROVE, world.p["id"], user=OWNER, ts=world.get()["ts"]); ev["actions"][0]["block_id"] = bid
            world.it.handle_safe(ev)
            assert "apply" not in net.modes, name
            assert world.get()["status"] == "pending", name

def _limits(blocks):
    errs = cards.validate(blocks)
    if len(blocks) > 50: errs.append(f"{len(blocks)} blocks")
    for b in blocks:
        if len(b.get("block_id", "")) > 255: errs.append("block_id")
        if b["type"] == "actions":
            if len(b["elements"]) > 25: errs.append("elements")
            for e in b["elements"]:
                if len(e["text"]["text"]) > 75: errs.append("button text")
                c = e.get("confirm")
                if c and (len(c["title"]["text"]) > 100 or len(c["text"]["text"]) > 300 or len(c["confirm"]["text"]) > 30): errs.append("confirm")
        if b["type"] == "section":
            if not 1 <= len((b.get("text") or {}).get("text", "x")) <= 3000: errs.append("sec")
            for f in b.get("fields") or []:
                if not 1 <= len(f["text"]) <= 2000: errs.append("field")
        if b["type"] == "context":
            for e in b["elements"]:
                if not 1 <= len(e["text"]) <= 3000: errs.append(f"context {len(e['text'])}")
    ids = [b["block_id"] for b in blocks if b.get("block_id")]
    if len(ids) != len(set(ids)): errs.append("dup block_id")
    return errs

@pytest.mark.parametrize("status", STATUSES)
def test_worst_case_card(world, status):
    st = world.store._data["proposals"][world.p["id"]]; pl = st["payload"]
    long = "Ünïcode & <Ballroom> | " * 30
    pl["losers"] = ["V2", "V3", "V4"]
    for v in ("V1", "V2", "V3", "V4"):
        pl["snapshots"][v] = dict(A, Vendor_ID=v, Name=long, Address=long * 3, Website="https://" + "a" * 900 + ".com", gold_chip=(v in ("V1", "V2")), Validated_Data="1" if v != "V3" else "0", State=v, lat=float(v[1]) * 10, lng=20)
    f0 = pl["pdf_check"]["flags"][0]
    pl["pdf_check"]["flags"] = [dict(f0, n=i, what_matched=long * 2, what_differed=long * 2, stored_diff=[long] * 3) for i in range(1, 9)]
    pl["pdf_check"]["notes"] = [long] * 3
    r = dict(rec(1, alert=True), reasons=[long] * 9, kinds=["opening price"], cant=["x"])
    b = mc.short(r, {"extra_blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "predicted\n" + "\n".join("• `V1` " + long for _ in range(9))}}], "confirm_text": long})
    pl["price"].update(extra_blocks=b["extra_blocks"], confirm_text=b["confirm_text"], cant=True, touching=[long] * 4, detail=b["detail"])
    st["status"], st["result_text"], st["result"] = status, long * 20, {"warnings": [long]}
    st["plain"] = {"headline": long * 3, "why": long * 3, "happens": long * 3}
    text, blocks = cards.render(world.get())
    print(status, len(blocks), len(json.dumps(blocks)))
    assert _limits(blocks) == [], len(blocks)
    assert _limits(cards.render_details(world.get())[1]) == []

def test_canary_flag_off_does_send(world, monkeypatch):
    net = Net(monkeypatch); assert world.approve() == "approved:applied" and net.modes == ["apply"]
