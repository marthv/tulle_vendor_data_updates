"""Tulle Bot v2: approval cards, interactivity, dedupe, #feedback watch, dry-run appliers, engine answers."""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest
import requests

import appliers
import cards
import dedupe
import engine as eng
import feedback_watch
import interactions
from fakes import FakeClaude, FakeSlack
from proposals import Store

SCRATCH = r"C:/Users/vivek/AppData/Local/Temp/claude/3d907d6bddb47d00/b1614ada-e602-409d-bacc-1819cb3f570d/scratchpad/dedupe/vendors.json"
OWNER, OTHER = "U05UJMCHUV6", "UOTHER1"


class RecSlack(FakeSlack):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []
        self._n = 0

    def _call(self, method, http="GET", **params):
        if method in ("chat.postMessage", "chat.update", "chat.postEphemeral"):
            self.calls.append((method, params))
            self._n += 1
            return {"ok": True, "ts": f"1700000000.{self._n:06d}", "channel": params.get("channel")}
        return super()._call(method, http, **params)

    def of(self, method):
        return [p for m, p in self.calls if m == method]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network write attempted in a test")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests.Session, "post", boom)
    for k in ("DEDUPE_DRY_RUN", "VENDOR_EDIT_DRY_RUN", "TASK_DRY_RUN", "QA_APPROVERS", "FEEDBACK_CHANNEL",
              "FEEDBACK_WATCH", "DEDUPE_CHANNEL", "EXP_ENGINE_URL", "EXP_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def merge_proposal(store, **kw):
    return store.create("merge", "Allied Arts Guild (V105 ← V13433)", "Merge V13433 into *V105*.", "Same Place ID.",
                        "Approve to merge.", {"survivor": "V105", "losers": ["V13433"], "counts": {"t10": 3, "t37": 2}},
                        touches="• PDF files (table 10): 3 rows → V105", key="merge:V105|V13433", **kw)


def ready_payload():
    """A merge payload that may be sent: the card's records, the PDF list, and Xano's matching preview."""
    import listing_compare
    snap = {"Name": "Oak Barn", "Address": "2 B Ln", "Category": "Venue", "Place_ID": "G1", "Validated_Data": "1"}
    pl = {"survivor": "V1", "losers": ["V2"], "counts": {}, "snapshots": {v: dict(snap, Vendor_ID=v) for v in ("V1", "V2")},
          "pdf_check": {"status": "ok", "pdfs": [], "flags": []}}
    pl["price"] = {"preview": {"current_state": listing_compare._shown_state(pl)}}
    return pl


def click(action, pid, user=OWNER, ts=None, team="T05U8AWH14Z"):
    return {"type": "block_actions", "team": {"id": team}, "user": {"id": user},
            "channel": {"id": "CDEDUPE"}, "message": {"ts": ts},
            "actions": [{"action_id": action, "value": pid}]}


def posted(slack, store, p, channel="CDEDUPE"):
    res = interactions.post_proposal(slack, store, channel, p)
    assert res["ok"]
    return store.get(p["id"])


# --- signature + route ---------------------------------------------------------------------------------------
def _signed(body: bytes, secret="test-signing-secret", ts=None):
    ts = str(int(ts or time.time()))
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
            "Content-Type": "application/x-www-form-urlencoded"}


def test_interactions_route_rejects_bad_signature_and_acks_good(monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    seen = []

    class FakeI:
        def handle_safe(self, payload):
            seen.append(payload)
    monkeypatch.setattr(appmod, "get_interactions", lambda: FakeI())
    c = TestClient(appmod.app)
    body = urlencode({"payload": json.dumps(click(cards.ACTION_APPROVE, "x"))}).encode()
    assert c.post("/slack/interactions", content=body, headers=_signed(body, secret="wrong")).status_code == 401
    old = _signed(body, ts=time.time() - 3600)
    assert c.post("/slack/interactions", content=body, headers=old).status_code == 401
    r = c.post("/slack/interactions", content=body, headers=_signed(body))
    assert r.status_code == 200 and r.content == b""
    assert seen and seen[0]["actions"][0]["value"] == "x"


def test_interactions_route_kill_switch(monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    monkeypatch.setenv("QA_ENABLED", "0")
    monkeypatch.setattr(appmod, "get_interactions", lambda: (_ for _ in ()).throw(AssertionError("ran")))
    body = urlencode({"payload": json.dumps(click(cards.ACTION_APPROVE, "x"))}).encode()
    assert TestClient(appmod.app).post("/slack/interactions", content=body, headers=_signed(body)).status_code == 200


# --- approver gating, idempotency ----------------------------------------------------------------------------
def test_non_approver_gets_ephemeral_and_nothing_changes():
    slack, store = RecSlack(), Store()
    p = posted(slack, store, merge_proposal(store))
    calls = []
    it = interactions.Interactions(slack=slack, store=store, apply_fns={"merge": lambda *a, **k: calls.append(a)})
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], user=OTHER, ts=p["ts"])) == "denied:not-approver"
    assert calls == [] and store.get(p["id"])["status"] == "pending"
    eph = slack.of("chat.postEphemeral")
    assert eph and eph[0]["user"] == OTHER and "Only approvers can apply" in eph[0]["text"]
    assert "Vivek" not in eph[0]["text"]


def test_approve_is_idempotent_and_shows_who(monkeypatch):
    slack, store = RecSlack(), Store()
    p = posted(slack, store, merge_proposal(store))
    calls = []

    def fake_merge(payload, user, pid, mode="apply", dry_run=True):
        calls.append((user, mode))
        return {"ok": True, "dry_run": False, "text": "Repointed 5 rows.", "result": {}}
    it = interactions.Interactions(slack=slack, store=store, apply_fns={"merge": fake_merge})
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"])) == "approved:applied"
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"])) == "noop:applied"
    assert it.handle(click(cards.ACTION_REJECT, p["id"], ts=p["ts"])) == "noop:applied"
    assert calls == [(OWNER, "apply")]
    last = slack.of("chat.update")[-1]
    txt = json.dumps(last["blocks"])
    assert "Approved by <@U05UJMCHUV6>" in txt and "tb_approve" not in txt and "tb_undo" in txt
    assert "already applied" in slack.of("chat.postEphemeral")[-1]["text"]


def test_reject_then_approve_is_noop():
    slack, store = RecSlack(), Store()
    p = posted(slack, store, merge_proposal(store))
    it = interactions.Interactions(slack=slack, store=store,
                                   apply_fns={"merge": lambda *a, **k: pytest.fail("applied after reject")})
    assert it.handle(click(cards.ACTION_REJECT, p["id"], ts=p["ts"])) == "rejected"
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"])) == "noop:rejected"
    assert "Rejected by <@U05UJMCHUV6>" in json.dumps(slack.of("chat.update")[-1]["blocks"])
    assert "merge:V105|V13433" in store.open_or_rejected_keys("merge")      # never suggested again


def test_click_from_another_message_or_team_is_ignored():
    slack, store = RecSlack(), Store()
    p = posted(slack, store, merge_proposal(store))
    it = interactions.Interactions(slack=slack, store=store, apply_fns={"merge": lambda *a, **k: pytest.fail("x")})
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts="999.1")) == "ignored:message-mismatch"
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"], team="TEVIL")) == "ignored:external-team"
    assert it.handle(click("other_action", p["id"], ts=p["ts"])).startswith("ignored:action")


def test_approvers_from_env(monkeypatch):
    monkeypatch.setenv("QA_APPROVERS", f"{OWNER},{OTHER}")
    monkeypatch.setenv("QA_USER_NAMES", f"{OTHER}=Des")
    slack, store = RecSlack(), Store()
    p = posted(slack, store, merge_proposal(store))
    it = interactions.Interactions(slack=slack, store=store)
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], user=OTHER, ts=p["ts"])) == "approved:dry_run"
    card = json.dumps(slack.of("chat.update")[-1]["blocks"])
    assert "Only approvers can apply this." in card and "Des" not in card and "Vivek" not in card


def test_applier_exception_marks_failed_not_applied():
    slack, store = RecSlack(), Store()
    p = posted(slack, store, merge_proposal(store))

    def bad(*a, **k):
        raise ValueError("x")
    it = interactions.Interactions(slack=slack, store=store, apply_fns={"merge": bad})
    # an exception mid-call means we can't know whether it landed: unknown + Re-check, never "failed"
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"])) == "approved:unknown"
    assert store.get(p["id"])["status"] == "unknown"
    assert "tb_recheck" in json.dumps(slack.of("chat.update")[-1]["blocks"])


# --- dry runs: no network --------------------------------------------------------------------------------------
def test_dry_run_is_default_and_never_calls_network():
    r = appliers.merge({"survivor": "V1", "losers": ["V2"], "counts": {"t10": 2, "t37": 1}}, OWNER, "p1")
    assert r["ok"] and r["dry_run"] and "would repoint about 3 rows" in r["text"]
    r = appliers.vendor_edit({"vendor_id": "V1", "field": "Website", "new_value": "x.com"}, OWNER, "p2")
    assert r["dry_run"] and "Nothing was written" in r["text"]
    r = appliers.task({"title": "Fix it"}, OWNER, "p3")
    assert r["dry_run"]


def test_live_merge_refuses_without_url(monkeypatch):
    monkeypatch.setenv("DEDUPE_DRY_RUN", "0")
    r = appliers.merge({"survivor": "V1", "losers": ["V2"], "counts": {}}, OWNER, "p1", dry_run=False)
    assert not r["ok"] and "isn't configured" in r["text"]


def test_end_to_end_dry_run_card():
    slack, store = RecSlack(), Store()
    p = posted(slack, store, merge_proposal(store))
    first = json.dumps(slack.of("chat.postMessage")[0]["blocks"])
    assert "Dry run is on" in first and "Approve and apply" in first and "Reject" in first
    it = interactions.Interactions(slack=slack, store=store)
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"])) == "approved:dry_run"
    assert "dry run, nothing written" in json.dumps(slack.of("chat.update")[-1]["blocks"])


# --- card shape ---------------------------------------------------------------------------------------------------
def test_cards_valid_and_within_limits():
    store = Store()
    long = "x" * 5000
    p = store.create("merge", "T" * 400, long, long, long, {"survivor": "V1", "losers": ["V2"], "counts": {}},
                     touches=long)
    for status in ("pending", "applying", "applied", "dry_run", "rejected", "failed", "undone"):
        p["status"], p["decided_by"] = status, OWNER
        text, blocks = cards.render(p)
        assert cards.validate(blocks) == [], status
        assert len(blocks) <= 50 and len(text) <= 3000
        assert blocks[-1]["type"] == "context" and "Proposed by Tulle Bot" in blocks[-1]["elements"][0]["text"]


def test_validator_catches_problems():
    bad = [{"type": "section", "text": {"type": "mrkdwn", "text": "x" * 3001}}] * 51
    errs = cards.validate(bad)
    assert any("blocks" in e for e in errs) and any("section text" in e for e in errs)


# --- dedupe -----------------------------------------------------------------------------------------------------
def V(i, vid, name, addr="1 Main St, Austin, TX 78701", state="Texas", pid="", web="", cat="Venue", lat=30.0,
      lng=-97.0, clicks=0, vis="1"):
    return {"id": i, "Vendor_ID": vid, "Name": name, "Address": addr, "State": state, "Place_ID": pid, "Website": web,
            "Category": cat, "lat": lat, "lng": lng, "Clicks_on_Venue": clicks, "Validated_Data": vis}


def test_find_groups_rules():
    rows = [
        V(1, "V1", "Allied Arts Guild", pid="P1", clicks=10), V(2, "V2", "Allied Arts Guild", pid="P1"),   # A+B
        V(3, "V3", "Marriott Downtown", web="marriott.com", addr="9 X St, Dallas, TX", lat=32.7, lng=-96.8),
        V(4, "V4", "Marriott Uptown", web="marriott.com", addr="8 Y St, Dallas, TX", lat=32.8, lng=-96.7),  # domain only
        V(5, "V5", "Rose Hall", pid="P9", addr="1 A, Waco, TX", lat=31.5, lng=-97.1),
        V(6, "V6", "Totally Different Caterers", pid="P9", addr="1 A, Waco, TX", lat=31.5, lng=-97.1),     # pid, diff names
        V(7, "V7", "Oak Barn", addr="2 B, Tyler, TX", lat=32.3, lng=-95.3),
        V(8, "V8", "Oak Barn", addr="2 B, Tyler, TX", lat=32.3, lng=-95.3, cat="Caterer"),                  # mixed category
    ]
    groups = dedupe.find_groups(rows)
    assert [g["ids"] for g in groups] == [[1, 2]]


def test_scan_creates_proposals_with_reference_counts_and_skips_rejected():
    rows = [V(1, "V1", "Allied Arts Guild", pid="P1", clicks=10), V(2, "V2", "Allied Arts Guild", pid="P1")]

    class FakeX:
        enabled = True
        pages = 0

        def get(self, path, params=None):
            self.pages += 1
            tid = int(path.split("/")[2])
            items = {10: [{"Vendor_ID": "V2"}, {"Vendor_ID": "V2"}, {"Vendor_ID": "V1"}],
                     37: [{"Vendor_ID": "V2"}], 90: [{"vendor_id": "V2"}]}.get(tid, [])
            return {"items": items, "nextPage": None}
    store = Store()
    res = dedupe.scan(FakeX(), store, mode="weekly", vendor_rows=rows)
    assert len(res["proposals"]) == 1
    p = res["proposals"][0]
    assert p["payload"]["survivor"] == "V2"          # more PDFs wins (2 vs 1)
    assert p["payload"]["losers"] == ["V1"] and p["payload"]["counts"]["t10"] == 1
    t10 = next(t for t in p["payload"]["tables"] if t["table"] == 10)
    assert t10["count"] == 1 and t10["rows"] and t10["rows"][0][1] == "Vendor_ID"
    assert next(t for t in p["payload"]["tables"] if t["table"] == 1)["status"] == "not_counted"
    assert p["plain"]["hidden"] == "The duplicate is hidden from search, not deleted" and "listed twice" in p["plain"]["headline"]
    details = " ".join(dedupe.details_text(p))
    assert "PDF files (table 10): 1 → survivor" in details and "counted and moved by the merge endpoint" in details
    text, blocks = cards.render(p)
    assert cards.validate(blocks) == []
    # pending -> not proposed again; rejected -> never again
    assert dedupe.scan(FakeX(), store, mode="weekly", vendor_rows=rows)["proposals"] == []
    store.transition(p["id"], {"pending"}, "rejected", OWNER)
    assert dedupe.scan(FakeX(), store, mode="weekly", vendor_rows=rows)["proposals"] == []


def test_nightly_only_looks_at_new_vendors():
    rows = [V(1, "V1", "Allied Arts Guild", pid="P1"), V(2, "V2", "Allied Arts Guild", pid="P1")]

    class FakeX:
        def get(self, path, params=None):
            return {"items": [], "nextPage": None}
    store = Store()
    store.set_meta("dedupe_max_id", 5)
    assert dedupe.scan(FakeX(), store, mode="nightly", vendor_rows=rows)["proposals"] == []
    rows.append(V(9, "V9", "Rose Hall", addr="1 A, Waco, TX", lat=31.5, lng=-97.1))
    rows.append(V(10, "V10", "Rose Hall", addr="1 A, Waco, TX", lat=31.5, lng=-97.1))
    got = dedupe.scan(FakeX(), store, mode="nightly", vendor_rows=rows)["proposals"]
    assert [p["payload"]["survivor"] for p in got] == ["V9"]


def test_page_budget_marks_tables_not_counted():
    class FakeX:
        def get(self, path, params=None):
            return {"items": [{"Vendor_ID": "V2"}] * 500, "nextPage": 2}
    refs = dedupe.RefIndex(FakeX(), max_pages=3)
    refs.build({"V2"})
    assert 10 in refs.not_counted and refs.pages_used == 3


@pytest.mark.skipif(not os.path.exists(SCRATCH), reason="scratch vendor export not present")
def test_dedupe_on_real_vendor_export():
    rows = list(json.load(open(SCRATCH, encoding="utf-8")).values())
    groups = dedupe.find_groups(rows)
    assert 300 < len(groups) < 900
    byid = {r["id"]: r for r in rows}
    for g in groups:
        assert 2 <= len(g["ids"]) <= 4
        assert len({str(byid[i].get("Category") or "").lower() for i in g["ids"]}) == 1
    # chains never merged on domain alone
    big = [g for g in groups if any("marriott" in str(byid[i].get("Website", "")).lower() for i in g["ids"])]
    for g in big:
        assert set(g["rules"]) != {"C"} or len({dedupe.place_id(byid[i]) for i in g["ids"]}) == 1


# --- #feedback watch -------------------------------------------------------------------------------------------
class FixClaude(FakeClaude):
    def __init__(self, fix):
        super().__init__()
        self.fix = fix

    def propose_fix(self, post):
        return dict(self.fix), 0.001


class FakeDir:
    def __init__(self, rows):
        self._rows = rows

    def resolve(self, vendor_id="", name="", location=""):
        return feedback_watch.VendorDirectory.resolve(self, vendor_id, name, location)

    def rows(self):
        return self._rows


def fb_event(text="Their website is now roseHall.com, the old one is dead", **kw):
    ev = {"type": "message", "subtype": "bot_message", "bot_id": "BTB", "channel": "CFEED", "ts": "1700.1", "text": text}
    ev.update(kw)
    return ev


def watcher(fix, rows=None):
    import tempfile
    from pathlib import Path
    from state import State
    slack, store = RecSlack(), Store(Path(tempfile.mkdtemp()) / "proposals.json")
    w = feedback_watch.FeedbackWatcher(slack, FixClaude(fix), State(), store, xano=None,
                                       directory=FakeDir(rows or [V(5, "V5", "Rose Hall", web="old.com")]))
    return w, slack, store


FIX = {"kind": "vendor_edit", "vendor_id": "", "vendor_name": "Rose Hall", "vendor_location": "", "field": "website",
       "new_value": "rosehall.com", "other_vendor_id": "", "other_vendor_name": "", "title": "", "body": "",
       "evidence": "Their website is now rosehall.com", "confidence": 0.9}


def test_feedback_vendor_edit_card_in_thread(monkeypatch):
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")
    monkeypatch.setenv("FEEDBACK_WATCH", "1")
    w, slack, store = watcher(FIX)
    assert feedback_watch.is_watchable(fb_event(), "UBOT")
    assert w.handle(fb_event()) == "feedback:proposed:vendor_edit"
    msg = slack.of("chat.postMessage")[0]
    assert msg["thread_ts"] == "1700.1"
    body = json.dumps(msg["blocks"])
    assert "Website" in body and "old.com" in body and "rosehall.com" in body and "Approve and apply" in body
    assert w.handle(fb_event()) == "feedback:already-proposed"


def test_feedback_guards(monkeypatch):
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")
    assert watcher(FIX)[0].handle(fb_event()) == "feedback:watch-off"
    monkeypatch.setenv("FEEDBACK_WATCH", "1")
    assert watcher(dict(FIX, field="Category"))[0].handle(fb_event()).endswith("field-not-editable")
    assert watcher(dict(FIX, confidence=0.4))[0].handle(fb_event()).startswith("feedback:no-fix")
    assert watcher(dict(FIX, new_value="old.com", evidence="site is old.com"),
                   [V(5, "V5", "Rose Hall", web="old.com")])[0].handle(fb_event(text="site is old.com")) \
        .endswith("already-correct")
    two = [V(5, "V5", "Rose Hall"), V(6, "V6", "Rose Hall", addr="x, Waco, TX")]
    assert watcher(FIX, two)[0].handle(fb_event()).endswith("resolve-2")


def test_feedback_not_watchable(monkeypatch):
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")
    own_card = fb_event(subtype=None, user="UBOT", blocks=[{"type": "actions", "block_id": "tb:x", "elements": []}])
    assert not feedback_watch.is_watchable(own_card, "UBOT")
    assert not feedback_watch.is_watchable(fb_event(subtype=None, user="UBOT"), "UBOT")     # our own roll-up/header
    assert not feedback_watch.is_watchable(fb_event(thread_ts="1600.1"), "UBOT")            # thread reply
    assert not feedback_watch.is_watchable(fb_event(channel="COTHER"), "UBOT")
    assert not feedback_watch.is_watchable(fb_event(subtype=None, bot_id="BOTHER", user="UOTHERBOT"), "UBOT")
    assert feedback_watch.is_watchable(fb_event(subtype=None, bot_id=None, user="UKATE"), "UBOT")


def test_feedback_task_scrubs_email(monkeypatch):
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")
    monkeypatch.setenv("FEEDBACK_WATCH", "1")
    fix = dict(FIX, kind="task", title="Planner quoted the wrong price for maria@x.com",
               body="Couple maria@x.com says the Planner said $5k", evidence="maria@x.com said")
    w, slack, store = watcher(fix)
    assert w.handle(fb_event(text="maria@x.com said the Planner said $5k")) == "feedback:proposed:task"
    assert "maria@x.com" not in json.dumps(slack.of("chat.postMessage")[0]) + json.dumps(store._data)


def test_bot_routes_feedback_channel(monkeypatch):
    from bot import Bot
    from state import RateLimiter, State
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")
    seen = []

    class W:
        def handle(self, ev):
            seen.append(ev)
            return "feedback:x"
    b = Bot(slack=RecSlack(), claude=FakeClaude(), state=State(), limiter=RateLimiter(), github=object(), xano=object())
    b._feedback = W()
    payload = {"event_id": "E1", "authorizations": [{"is_bot": True, "user_id": "UBOT"}], "event": fb_event()}
    assert b.handle(payload) == "feedback:x" and seen
    payload["event"] = fb_event(thread_ts="1600.1", subtype=None, user="UKATE", bot_id=None)
    assert b.handle(payload) == "ignored:feedback-channel"


# --- engine ---------------------------------------------------------------------------------------------------
SNAP = {"generated_at": "2026-11-20T06:31:00Z", "experiments": [
    {"id": "EXP-2026-11-save-offer", "name": "Save offer TT60", "status": "live", "in_rotation": True,
     "verdict": {"tldr": "From 2.1% to 3.4%; 91% chance it's better; bar is 95%.",
                 "recommend": {"call": "keep_running", "label": "Keep running", "why": "x"},
                 "how_we_call": {"now": "Ship if it reaches 95% by 12-01; otherwise call it inconclusive."},
                 "unstick": None}},
    {"id": "EXP-2026-11-price-report", "name": "Price Report", "status": "live", "in_rotation": True,
     "verdict": {"tldr": "Too early.", "recommend": None, "how_we_call": {"bar": "90%"}}}]}


class FakeEngine:
    enabled = True

    def snapshot(self):
        return SNAP

    def changes(self, limit=200):
        return {"events": [{"ts": "2026-11-18T06:47:00Z", "source": "weweb",
                            "title": "WeWeb production publish (cacheVersion 1400 -> 1401)"}], "unlogged": []}


def test_experiment_answer_quotes_engine():
    t = eng.answer_experiment("how's the save offer experiment doing?", FakeEngine(), lambda q: None)
    assert "Save offer TT60" in t and "91% chance" in t and "Keep running" in t and "Ship if it reaches 95%" in t
    assert "Price Report" not in t
    t = eng.answer_experiment("what experiments are running in rotation?", FakeEngine(), lambda q: None)
    assert "Save offer TT60" in t and "Price Report" in t


def test_experiment_answer_falls_back_when_engine_off():
    class Off:
        enabled = False
    assert eng.answer_experiment("how's the save offer test doing", Off(), lambda q: None) is None
    assert "registry" in eng.answer_experiment("how's the save offer test doing", Off(), lambda q: "Live since 11-03.")


def test_publish_answer_joins_deploy_note():
    note_ts = datetime(2026, 11, 18, 6, 30, tzinfo=timezone.utc).timestamp()
    t = eng.answer_publishes(FakeEngine(), [{"ts": str(note_ts), "text": "Price Report v2: simpler rows"}])
    assert "cacheVersion 1400 -> 1401" in t and "Price Report v2: simpler rows" in t


def test_experiment_question_regex():
    assert eng.EXPERIMENT_Q.search("how's the save offer experiment doing?")
    assert eng.EXPERIMENT_Q.search("any results on the price report test")
    assert not eng.EXPERIMENT_Q.search("what shipped yesterday")


# --- scheduler ------------------------------------------------------------------------------------------------
def test_scheduler_runs_once_per_day_and_never_without_channel(monkeypatch):
    import scheduler

    class B:
        store = Store()
    s = scheduler.Scheduler(B(), clock=lambda: datetime(2026, 11, 23, 9, tzinfo=timezone.utc))
    assert s.tick() == []                                      # nothing configured -> nothing runs
    monkeypatch.setenv("DEDUPE_CHANNEL", "CDEDUPE")
    monkeypatch.setenv("DEDUPE_SCHEDULED_SCAN", "0")
    assert s.tick() == []                                      # 2026-11-23: it can be switched off; it is on by default
    monkeypatch.delenv("DEDUPE_SCHEDULED_SCAN")
    ran = []
    monkeypatch.setattr(scheduler, "run_dedupe", lambda bot, now=None, mode=None: ran.append(now) or "dedupe:x")
    assert s.tick() == ["dedupe:x"] and s.tick() == []
    assert len(ran) == 1


def test_injected_slack_markup_is_neutralised(monkeypatch):
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")
    monkeypatch.setenv("FEEDBACK_WATCH", "1")
    fix = dict(FIX, field="Description", new_value="<!channel> <https://evil.example|rosehall.com>",
               evidence="<!here> new site")
    w, slack, store = watcher(fix)
    post = "<!here> new site: <!channel> <https://evil.example|rosehall.com>"
    assert w.handle(fb_event(text=post)) == "feedback:proposed:vendor_edit"
    body = json.dumps(slack.of("chat.postMessage")[0]["blocks"])
    assert "<!channel>" not in body and "<!here>" not in body and "<https://evil" not in body
    assert store.get(next(iter(store._data["proposals"])))["payload"]["new_value"].startswith("<!channel>")  # raw kept
    assert not feedback_watch.is_watchable(fb_event(subtype=None, bot_id=None, user="UKATE",
                                                    text="<@UBOT> what is this?"), "UBOT")
