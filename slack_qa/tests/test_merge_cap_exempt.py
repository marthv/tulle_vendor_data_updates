"""Owner ruling T0-57 (2026-11-26): merge requests from people on the approver list (config.approvers(), the same list
that may press Approve on merge cards) don't count toward, and are never refused by, the per-channel hourly cap.
Everything else keeps the cap: questions (also from approvers), lookups, PDF lists, scans, non-approver merges."""
import pytest
import requests

from fakes import FakeKnowledge, mention
from test_merge_requests import bot as _bot

CAP = 6


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network write attempted in a test")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.delenv("DEDUPE_DRY_RUN", raising=False)
    monkeypatch.setenv("QA_CHANNEL_HOURLY", str(CAP))
    monkeypatch.setenv("QA_APPROVERS", "UAPPROVER")


def bot():
    b, slack, store = _bot()
    b.know = FakeKnowledge()               # question path: no real Slack/GitHub context
    b.state.over_budget = lambda: False    # the cap is under test, not the daily model budget
    return b, slack, store


MERGES = ["merge Smith and Co and Smith & Co Events", "merge V13433 into V105",
          "merge Gramercy Mansion and Gramercy at Lakeside Manor"]


def _merges(b, user, n):
    return [b.handle(mention(MERGES[i % len(MERGES)], user=user)) for i in range(n)]


def test_approver_can_send_more_than_cap_merges_in_one_channel():
    """CANARY: fails on origin/main 9c54a49 (the 7th approver merge came back 'merge:rate-limited')."""
    b, slack, store = bot()
    outs = _merges(b, "UAPPROVER", CAP + 4)
    assert not any(o.endswith("rate-limited") for o in outs), outs
    assert all(o.startswith("merge:") for o in outs), outs
    assert not any("Too many requests" in (m.get("text") or "") for m in slack.of("chat.postMessage"))


def test_non_approver_merges_still_hit_the_cap():
    b, slack, store = bot()
    outs = _merges(b, "UKATE", CAP + 2)
    assert outs[:CAP].count("merge:rate-limited") == 0
    assert outs[CAP:] == ["merge:rate-limited"] * 2


def test_approver_question_still_counts_and_is_refused_after_cap():
    b, slack, store = bot()
    outs = [b.handle(mention("what shipped yesterday?", user="UAPPROVER")) for _ in range(CAP + 1)]
    assert "mention:rate-limited" not in outs[:CAP]
    assert outs[CAP] == "mention:rate-limited"


def test_approver_merges_do_not_use_up_question_slots():
    b, slack, store = bot()
    _merges(b, "UAPPROVER", CAP + 2)
    outs = [b.handle(mention("what shipped yesterday?", user="UKATE")) for _ in range(CAP + 1)]
    assert "mention:rate-limited" not in outs[:CAP], outs
    assert outs[CAP] == "mention:rate-limited"


def test_approver_exemption_is_per_request_not_per_channel():
    """Approver merges don't open the channel for a non-approver's merges once the cap is spent on questions."""
    b, slack, store = bot()
    [b.handle(mention("what shipped yesterday?", user="UKATE")) for _ in range(CAP)]
    assert b.handle(mention(MERGES[0], user="UKATE")) == "merge:rate-limited"
    assert b.handle(mention(MERGES[0], user="UAPPROVER")) != "merge:rate-limited"
