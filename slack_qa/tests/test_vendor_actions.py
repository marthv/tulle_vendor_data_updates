"""Vendor actions on @-mention (hide / unhide / change a field / approval lists), 2026-11-21: Tulle Bot takes over the
retired Tulle Ops app's useful commands in plain words. Every write is a card an approver must press."""
import json
import tempfile
from pathlib import Path

import pytest
import requests

import appliers
import cards
import interactions
import vendor_actions as va
from fakes import FakeClaude, mention
from proposals import Store
from test_v2 import FakeDir, RecSlack, click

OWNER, KATE = "U05UJMCHUV6", "UKATE"
ROSE = {"id": 5, "Vendor_ID": "V5", "Name": "Rose Hall", "Website": "old.com", "Validated_Data": "1",
        "Max_Capacity_Seated": "150", "Description": "Barn"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network write attempted in a test")
    monkeypatch.setattr(requests, "post", boom)
    for k in ("VISIBILITY_DRY_RUN", "VENDOR_EDIT_DRY_RUN", "VENDOR_EDIT_SECRET", "QA_APPROVERS", "FEEDBACK_CHANNEL"):
        monkeypatch.delenv(k, raising=False)


class XanoOn:
    enabled = True


def make_bot(rows=None):
    from bot import Bot
    from state import RateLimiter, State
    slack = RecSlack()
    store = Store(Path(tempfile.mkdtemp()) / "proposals.json")
    b = Bot(slack=slack, claude=FakeClaude(), state=State(), limiter=RateLimiter(), github=object(), xano=XanoOn(),
            store=store)

    class W:
        directory = FakeDir(rows if rows is not None else [dict(ROSE)])
    b._feedback = W()
    return b, slack, store


# --- parsing ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q,kind,extra", [
    ("hide V5699 venue asked to be removed", "visibility", {"action": "hide", "reason": "venue asked to be removed"}),
    ("please hide vendor V5699", "visibility", {"action": "hide"}),
    ("unhide V5699", "visibility", {"action": "unhide"}),
    ("restore V5699", "visibility", {"action": "unhide"}),
    ("show V5699", "visibility", {"action": "unhide"}),
    ("change V13831 website to <https://example.com|example.com>", "vendor_edit",
     {"field": "Website", "new_value": "example.com"}),
    ("set V462's seated capacity to 1,200", "vendor_edit", {"field": "Max_Capacity_Seated"}),
    ("fix V9 description = Lovely barn close to the lake", "vendor_edit",
     {"field": "Description", "new_value": "Lovely barn close to the lake"}),
    ("what's waiting for approval?", "list_pending", {}),
    ("what was applied this week?", "list_applied", {}),
])
def test_parse(q, kind, extra):
    r = va.parse(q)
    assert r and r["kind"] == kind
    for k, v in extra.items():
        assert r[k] == v


@pytest.mark.parametrize("q", ["show V5699 pricing", "hide the pricing card for free users",
                               "what's the payment rate today?", "why is V5699 so expensive?"])
def test_parse_leaves_questions_alone(q):
    assert va.parse(q) is None


@pytest.mark.parametrize("q,needle", [
    ("change V5 price to $5", "Prices are fixed in the pricing data"),
    ("change V5 state to Ohio", "I can't change"),
    ("change V5 contact to rose@hall.com", "email addresses"),
    ("change V5 seated capacity to lots", "has to be a number"),
    ("change V5 website to not a site", "doesn't look like a web address"),
    ("change V5 website to old.com", "already"),
])
def test_edit_refusals_post_a_reply_and_no_card(q, needle):
    b, slack, store = make_bot()
    out = b.handle(mention(q, user=KATE))
    assert out == "vendor:vendor_edit:declined"
    assert store.all() == []
    assert needle in slack.of("chat.postMessage")[-1]["text"]


# --- cards -----------------------------------------------------------------------------------------------------
def test_hide_mention_posts_card_with_approve_and_no_write():
    b, slack, store = make_bot()
    assert b.handle(mention("hide V5 venue asked to be removed", user=KATE)) == "vendor:visibility:proposed"
    [p] = store.all()
    assert p["kind"] == "visibility" and p["payload"] == {"vendor_id": "V5", "action": "hide", "name": "Rose Hall",
                                                          "reason": "venue asked to be removed"}
    card = json.dumps(slack.of("chat.postMessage")[-1]["blocks"])
    assert "Hide or show a vendor" in card and "Rose Hall" in card and "unhide V5" in card
    assert "tb_approve" in card and "Dry run is on" in card            # dry run by default (fails closed)


def test_unknown_vendor_is_refused():
    b, slack, store = make_bot(rows=[])
    assert b.handle(mention("hide V999 spam")) == "vendor:not-found"
    assert store.all() == []


def test_edit_mention_posts_vendor_edit_card_with_old_value():
    b, slack, store = make_bot()
    assert b.handle(mention("change V5 website to rosehall.com")) == "vendor:vendor_edit:proposed"
    [p] = store.all()
    assert p["payload"] == {"vendor_id": "V5", "field": "Website", "old_value": "old.com",
                            "new_value": "rosehall.com", "note": "from a Slack @-mention"}


def test_vendor_actions_spend_nothing():
    b, slack, store = make_bot()
    b.state.add_spend(10_000)                       # over the daily budget: actions still work (no model call)
    assert b.handle(mention("hide V5 asked by venue")) == "vendor:visibility:proposed"


def test_lists():
    b, slack, store = make_bot()
    b.handle(mention("hide V5 asked by venue"))
    b.handle(mention("what's waiting for approval?"))
    assert "Waiting for approval: 1" in slack.of("chat.postMessage")[-1]["text"]
    p = store.all()[0]
    store.transition(p["id"], {"pending"}, "applying", OWNER)
    store.transition(p["id"], {"applying"}, "applied", OWNER)
    b.handle(mention("what was applied this week?"))
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "Decided in the last 7 days: 1" in txt and "Rose Hall (V5): hide" in txt and "- applied," in txt
    b.handle(mention("what's waiting for approval?"))
    assert "Nothing is waiting" in slack.of("chat.postMessage")[-1]["text"]


# --- applier ---------------------------------------------------------------------------------------------------
class Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


def test_visibility_dry_run_writes_nothing():
    r = appliers.visibility({"vendor_id": "V5", "action": "hide"}, OWNER, "p1", dry_run=True)
    assert r["ok"] and r["dry_run"] and "Nothing was written" in r["text"]


def test_visibility_live_calls_ep279(monkeypatch):
    monkeypatch.setenv("VISIBILITY_DRY_RUN", "0")
    monkeypatch.setenv("VENDOR_EDIT_SECRET", "s3")
    sent = {}
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: sent.update(url=url, body=json) or
                        Resp(200, {"vendor_id": "V5", "vendor_name": "Rose Hall", "previous_visibility": 1,
                                   "new_visibility": 0, "changed": True}))
    r = appliers.visibility({"vendor_id": "V5", "action": "hide"}, OWNER, "p1", dry_run=False)
    assert r["ok"] and not r["dry_run"] and "hidden from search" in r["text"] and "2 hours" in r["text"]
    assert r["result"] == {"previous_visibility": 1, "new_visibility": 0}
    assert sent["url"].endswith("/api:aow91bcd/vendor/visibility")
    assert sent["body"] == {"vendor_id": "V5", "action": "hide", "secret": "s3", "actor_slack_id": OWNER,
                            "source": "tulle-bot"}


def test_visibility_noop_refusal_and_unknown(monkeypatch):
    monkeypatch.setenv("VISIBILITY_DRY_RUN", "0")
    monkeypatch.setenv("VENDOR_EDIT_SECRET", "s3")
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(200, {"changed": False}))
    assert "already hidden" in appliers.visibility({"vendor_id": "V5", "action": "hide"}, OWNER, "p", False)["text"]
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(403, {"message": "bad secret"}))
    r = appliers.visibility({"vendor_id": "V5", "action": "hide"}, OWNER, "p", False)
    assert not r["ok"] and not r["unknown"] and "bad secret" in r["text"]

    def timeout(*a, **k):
        raise requests.Timeout()
    monkeypatch.setattr(requests, "post", timeout)
    r = appliers.visibility({"vendor_id": "V5", "action": "hide"}, OWNER, "p", False)
    assert not r["ok"] and r["unknown"]


def test_visibility_without_secret_sends_nothing(monkeypatch):
    monkeypatch.setenv("VISIBILITY_DRY_RUN", "0")
    r = appliers.visibility({"vendor_id": "V5", "action": "hide"}, OWNER, "p", False)
    assert not r["ok"] and "VENDOR_EDIT_SECRET" in r["text"]


def test_visibility_status_reads_row():
    class X:
        enabled = True

        def rows(self, t):
            return [dict(ROSE, Validated_Data="0")], False
    assert appliers.visibility_status({"vendor_id": "V5", "action": "hide"}, X())["state"] == "applied"
    assert appliers.visibility_status({"vendor_id": "V5", "action": "unhide"}, X())["state"] == "none"


# --- approve gating end to end ---------------------------------------------------------------------------------
def test_only_approvers_can_apply_a_hide(monkeypatch):
    b, slack, store = make_bot()
    b.handle(mention("hide V5 asked by venue", user=KATE))
    p = store.all()[0]
    calls = []
    it = interactions.Interactions(slack=slack, store=store,
                                   apply_fns={"visibility": lambda *a, **k: calls.append(a) or
                                              {"ok": True, "dry_run": False, "text": "V5 is now hidden.", "result": {}}})
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], user=KATE, ts=p.get("ts"))) == "denied:not-approver"
    assert calls == []
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], user=OWNER, ts=p.get("ts"))) == "approved:applied"
    assert len(calls) == 1 and store.get(p["id"])["status"] == "applied"


def test_help_lists_vendor_actions():
    b, slack, store = make_bot()
    b.handle(mention("help"))
    txt = slack.of("chat.postMessage")[-1]["text"]
    assert "hide V1234" in txt and "what's waiting for approval" in txt
