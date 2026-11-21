"""Customer lookup (took over Tulle Ops' /tulle view-as, 2026-11-21): allowlist, ephemeral only, read-only, no logging
of customer data."""
import json
import logging

import pytest
import requests

import customer_lookup as cl
from fakes import mention
from test_vendor_actions import KATE, OWNER, make_bot

SNAP = {"ok": True, "user": {"id": 34819, "name": "Jane Doe", "email": "jane@example.com", "tier": "timed",
                             "days_left": 3, "access_until": "2026-11-24", "pricing_intelligence_unlocked": False,
                             "total_paid": 35, "pdf_views_left": 0, "pdfs_viewed": 4, "vendors_viewed": 9,
                             "favourites": 2, "payments": 1, "last_active_at": None, "last_vendor": "Rose Hall",
                             "location": ["NY"], "guests": 120, "budget": 40000},
        "upgrade": {"eligible": True, "credit": 35, "final_price": 64, "forever_price": 99}}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted in a test")
    monkeypatch.setattr(requests, "post", boom)
    for k in ("LOOKUP_USER_IDS", "CUSTOMER_LOOKUP_SECRET"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("q,want", [
    ("look up jane@example.com", {"email": "jane@example.com"}),
    ("lookup <mailto:Jane@Example.com|Jane@Example.com>", {"email": "jane@example.com"}),
    ("can you look up user 34819?", {"user_id": 34819}),
    ("check customer #34819", {"user_id": 34819}),
    ("what does user 34819 see?", {"user_id": 34819}),
    ("what does jane@example.com see on the pricing page", {"email": "jane@example.com"}),
])
def test_parse(q, want):
    assert cl.parse(q) == want


@pytest.mark.parametrize("q", ["check 5", "look up the payments for last week", "what does the planner see?",
                               "hide V12", "check the venue pricing"])
def test_parse_ignores_other_requests(q):
    assert cl.parse(q) is None


def test_not_on_list_is_denied_ephemerally_and_nothing_fetched(monkeypatch):
    monkeypatch.setenv("CUSTOMER_LOOKUP_SECRET", "k")
    monkeypatch.setenv("LOOKUP_USER_IDS", OWNER)
    b, slack, store = make_bot()
    assert b.handle(mention("look up user 34819", user=KATE)) == "lookup:denied"
    eph = slack.of("chat.postEphemeral")
    assert eph and eph[-1]["user"] == KATE and "not on the customer-lookup list" in eph[-1]["text"]
    assert slack.of("chat.postMessage") == []


def test_empty_list_denies_everyone(monkeypatch):
    monkeypatch.setenv("CUSTOMER_LOOKUP_SECRET", "k")
    b, slack, store = make_bot()
    assert b.handle(mention("look up user 34819", user=OWNER)) == "lookup:denied"


def test_lookup_is_ephemeral_readonly_and_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("CUSTOMER_LOOKUP_SECRET", "k")
    monkeypatch.setenv("LOOKUP_USER_IDS", f"{OWNER},{KATE}")
    sent = {}

    class R:
        status_code = 200

        def json(self):
            return SNAP
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: sent.update(url=url, body=json) or R())
    b, slack, store = make_bot()
    with caplog.at_level(logging.INFO):
        assert b.handle(mention("look up <mailto:jane@example.com|jane@example.com>", user=KATE)) == "lookup:ok"
    assert sent["url"].endswith("/api:aow91bcd/admin/customer_snapshot")
    assert sent["body"] == {"actor_slack_id": KATE, "secret": "k", "email": "jane@example.com"}
    eph = slack.of("chat.postEphemeral")[-1]
    assert eph["user"] == KATE and "Timed*, 3 days left" in eph["text"] and "Forever for *$64*" in eph["text"]
    assert "Only you can see this" in eph["text"]
    assert slack.of("chat.postMessage") == []                     # nothing visible in the channel
    assert store.all() == []                                      # nothing stored
    assert "jane@example.com" not in caplog.text and "Jane Doe" not in caplog.text


def test_user_id_target_sends_only_user_id(monkeypatch):
    monkeypatch.setenv("CUSTOMER_LOOKUP_SECRET", "k")
    seen = {}
    cl.fetch({"user_id": 34819}, OWNER, post=lambda url, body: seen.update(body) or {"ok": False, "error": "x"})
    assert seen == {"actor_slack_id": OWNER, "secret": "k", "user_id": 34819}


def test_not_configured_and_failure_messages(monkeypatch):
    monkeypatch.setenv("LOOKUP_USER_IDS", OWNER)
    b, slack, store = make_bot()
    assert b.handle(mention("look up user 1", user=OWNER)) == "lookup:not-configured"
    monkeypatch.setenv("CUSTOMER_LOOKUP_SECRET", "k")

    class R:
        status_code = 404

        def json(self):
            return {"message": "No user found"}
    monkeypatch.setattr(requests, "post", lambda *a, **k: R())
    assert b.handle(mention("look up user 999999", user=OWNER)) == "lookup:failed"
    assert "No user found" in slack.of("chat.postEphemeral")[-1]["text"]


def test_render_free_and_forever():
    free = dict(SNAP, user=dict(SNAP["user"], tier=None, pdf_views_left=2), upgrade={"eligible": False})
    t = cl.render(free)
    assert "*Free*, never paid" in t and "Free PDF views left: 2" in t and "not showing" in t
    fv = dict(SNAP, user=dict(SNAP["user"], tier="forever", pricing_intelligence_unlocked=True),
              upgrade={"already_forever": True})
    t = cl.render(fv)
    assert "*Forever*" in t and "unlocked" in t and "already Forever" in t


def test_markup_in_customer_fields_is_escaped():
    t = cl.render(dict(SNAP, user=dict(SNAP["user"], name="<!channel> Jane", last_vendor="<@U1>")))
    assert "<!channel>" not in t and "<@U1>" not in t
