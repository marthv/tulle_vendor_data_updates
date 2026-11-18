import pytest

from bot import FOOTER, OUT_OF_SCOPE, Bot, looks_like_question, should_ignore
from state import RateLimiter, State
from fakes import FakeClaude, FakeKnowledge, FakeSlack, mention, msg


@pytest.fixture
def make_bot(tmp_path):
    def _make(claude=None, slack=None):
        b = Bot(slack=slack or FakeSlack(), claude=claude or FakeClaude(), state=State(tmp_path / "s.json"),
                limiter=RateLimiter(), github=None, know=FakeKnowledge)
        return b
    return _make


# --- ignore rules ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ev,reason", [
    ({"bot_id": "B1", "user": "U1", "channel": "C1"}, "bot-message"),
    ({"subtype": "bot_message", "channel": "C1"}, "bot-message"),     # notifier posts carry bot_id too
    ({"subtype": "message_changed", "user": "U1", "channel": "C1"}, "subtype:message_changed"),
    ({"subtype": "message_deleted", "channel": "C1"}, "subtype:message_deleted"),
    ({"subtype": "channel_join", "user": "U1", "channel": "C1"}, "subtype:channel_join"),
    ({"user": "UBOT", "channel": "C1"}, "own-message"),
    ({"user": "U1", "channel": "D1", "channel_type": "im"}, "dm"),
    ({"channel": "C1"}, "no-user"),
])
def test_should_ignore(ev, reason):
    if ev.get("subtype") == "bot_message":
        ev["bot_id"] = "B2"
    assert should_ignore(ev, "UBOT") == reason


def test_should_not_ignore_plain_and_broadcast():
    assert should_ignore({"user": "U1", "channel": "C1"}, "UBOT") is None
    assert should_ignore({"user": "U1", "channel": "C1", "subtype": "thread_broadcast"}, "UBOT") is None


def test_bot_messages_never_answered(make_bot):
    b = make_bot()
    p = msg("why did the price report change?")
    p["event"]["bot_id"] = "B0TULLE"
    assert b.handle(p) == "ignored:bot-message"
    assert b.slack.posts == [] and b.claude.triage_calls == []


def test_message_with_mention_left_to_app_mention(make_bot):
    b = make_bot()
    b.state.set_muted("C065B5YEG1G", False)
    assert b.handle(msg("<@UBOT> what shipped yesterday?")) == "ignored:mention-handled-by-app_mention"
    assert b.claude.triage_calls == []


def test_looks_like_question():
    assert looks_like_question("why did the price report change?")
    assert looks_like_question("is the free first vendor still on")
    assert not looks_like_question("ok thanks")
    assert not looks_like_question("Shipped the new LP copy today.")


# --- commands ----------------------------------------------------------------------------------------------
def test_mute_unmute_status(make_bot, monkeypatch):
    b = make_bot()
    ch = "C065B5YEG1G"
    assert b.state.is_muted(ch) is True                      # QA_DEFAULT_MUTED defaults to 1
    assert b.handle(mention("unmute")) == "command:unmute"
    assert b.state.is_muted(ch) is False
    assert b.handle(mention("status")) == "command:status"
    assert "UNMUTED" in b.slack.posts[-1]["text"] and "set in this channel" in b.slack.posts[-1]["text"]
    assert b.handle(mention("Mute!")) == "command:mute"
    assert b.state.is_muted(ch) is True
    assert all(p["thread_ts"] for p in b.slack.posts)        # replies go in thread
    assert b.claude.answer_calls == [] and b.claude.triage_calls == []


def test_mute_persists_across_restart(make_bot, tmp_path):
    b = make_bot()
    b.handle(mention("unmute"))
    assert State(tmp_path / "s.json").is_muted("C065B5YEG1G") is False


def test_default_unmuted_env(make_bot, monkeypatch):
    monkeypatch.setenv("QA_DEFAULT_MUTED", "0")
    assert make_bot().state.is_muted("CNEW") is False


def test_help_on_bare_mention(make_bot):
    b = make_bot()
    assert b.handle(mention("")) == "command:help"


# --- scan mode ---------------------------------------------------------------------------------------------
def test_muted_channel_not_scanned(make_bot):
    b = make_bot()
    assert b.handle(msg("why did the price report change?")) == "scan:muted"
    assert b.claude.triage_calls == []


def test_scan_answers_above_threshold(make_bot):
    b = make_bot()
    b.state.set_muted("C065B5YEG1G", False)
    p = msg("why did the price report change?")
    assert b.handle(p) == "scan:answered"
    post = b.slack.posts[-1]
    assert post["thread_ts"] == p["event"]["ts"]
    assert FOOTER in post["text"] and "Sources: EXP-2026-11-price-report-vs-pi-card" in post["text"]


def test_scan_silent_below_threshold(make_bot):
    claude = FakeClaude(triage={"is_question": True, "category": "change_question", "relevant": True,
                                "confidence": 0.5, "reason": ""})
    b = make_bot(claude=claude)
    b.state.set_muted("C065B5YEG1G", False)
    assert b.handle(msg("why did the price report change?")).startswith("scan:below-threshold")
    assert b.slack.posts == [] and claude.answer_calls == []


@pytest.mark.parametrize("category", ["personal", "interpersonal", "owner_decision", "other"])
def test_scan_silent_out_of_scope_category(make_bot, category):
    claude = FakeClaude(triage={"is_question": True, "category": category, "relevant": True,
                                "confidence": 0.99, "reason": ""})
    b = make_bot(claude=claude)
    b.state.set_muted("C065B5YEG1G", False)
    assert b.handle(msg("should we raise prices to $99?")) == f"scan:triage-skip:{category}"
    assert b.slack.posts == []


def test_scan_silent_when_answer_not_confident(make_bot):
    claude = FakeClaude(answer={"in_scope": True, "answerable": False, "confidence": 0.2,
                                "answer": "I don't know. Vivek can confirm.", "sources": []})
    b = make_bot(claude=claude)
    b.state.set_muted("C065B5YEG1G", False)
    assert b.handle(msg("what shipped yesterday?")).startswith("scan:answer-withheld")
    assert b.slack.posts == []


def test_scan_skips_owner(make_bot):
    b = make_bot()
    b.state.set_muted("C065B5YEG1G", False)
    assert b.handle(msg("why did the price report change?", user="U05UJMCHUV6")) == "scan:owner"
    assert b.claude.triage_calls == []


def test_owner_mention_is_answered(make_bot):
    b = make_bot()
    assert b.handle(mention("what shipped yesterday?", user="U05UJMCHUV6")) == "mention:answered"


def test_scan_skips_non_questions_without_claude(make_bot):
    b = make_bot()
    b.state.set_muted("C065B5YEG1G", False)
    assert b.handle(msg("Published the new LP.")) == "scan:not-a-question"
    assert b.claude.triage_calls == []


def test_scan_skips_personal_questions(make_bot):
    b = make_bot()
    b.state.set_muted("C065B5YEG1G", False)
    assert b.handle(msg("was Des frustrated about the copy change?")) == "scan:personal"
    assert b.claude.triage_calls == []


def test_scan_blocks_personal_answer(make_bot):
    claude = FakeClaude(answer={"in_scope": True, "answerable": True, "confidence": 0.95,
                                "answer": "Kate was frustrated so the copy changed.", "sources": ["x"]})
    b = make_bot(claude=claude)
    b.state.set_muted("C065B5YEG1G", False)
    assert b.handle(msg("why did the copy change?")) == "scan:answer-personal-blocked"
    assert b.slack.posts == []


# --- mentions ----------------------------------------------------------------------------------------------
def test_mention_always_replies_even_if_unanswerable(make_bot):
    claude = FakeClaude(answer={"in_scope": True, "answerable": False, "confidence": 0.1,
                                "answer": "I don't have that in the change history.", "sources": []})
    b = make_bot(claude=claude)
    assert b.handle(mention("what is the Q3 roadmap?")) == "mention:answered"
    assert "Vivek can confirm" in b.slack.posts[-1]["text"]


def test_mention_out_of_scope(make_bot):
    b = make_bot()
    assert b.handle(mention("why is Kate upset with Des?")) == "mention:out-of-scope-rule"
    assert OUT_OF_SCOPE in b.slack.posts[-1]["text"] and b.claude.answer_calls == []
    claude = FakeClaude(answer={"in_scope": False, "answerable": False, "confidence": 0.9,
                                "answer": "x", "sources": []})
    b2 = make_bot(claude=claude)
    assert b2.handle(mention("should we drop the price to $20?")) == "mention:out-of-scope"


def test_output_scrubbed_before_posting(make_bot):
    claude = FakeClaude(answer={"in_scope": True, "answerable": True, "confidence": 0.95,
                                "answer": "Webhook https://hooks.sl" "ack.com/services/T1/B2/abcdef; user 31469 "
                                          "jane@x.com token xo" "xb-1234567890-abcdefghij",
                                "sources": ["note"]})
    b = make_bot(claude=claude)
    b.handle(mention("what changed?"))
    text = b.slack.posts[-1]["text"]
    for bad in ("hooks.slack.com", "31469", "jane@x.com", "xo" "xb-"):
        assert bad not in text


def test_long_answer_capped(make_bot):
    claude = FakeClaude(answer={"in_scope": True, "answerable": True, "confidence": 0.95,
                                "answer": "word " * 400, "sources": []})
    b = make_bot(claude=claude)
    b.handle(mention("what changed?"))
    assert len(b.slack.posts[-1]["text"].split()) < 200


# --- cost cap / rate limit / kill switch -------------------------------------------------------------------
def test_cost_cap_blocks_scan_and_mention(make_bot, monkeypatch):
    monkeypatch.setenv("QA_DAILY_USD", "0.15")
    claude = FakeClaude(answer_usd=0.08, triage_usd=0.01)
    b = make_bot(claude=claude)
    b.state.set_muted("C065B5YEG1G", False)
    assert b.handle(msg("what shipped yesterday?")) == "scan:answered"           # spend 0.09
    assert b.handle(msg("why did the price report change?")) == "scan:answered"  # 0.18 after this one
    assert b.state.spent_today() >= 0.15
    assert b.handle(msg("is the free first vendor still on?")) == "scan:over-budget"
    assert b.handle(mention("is the free first vendor still on?")) == "mention:over-budget"
    assert "spending cap" in b.slack.posts[-1]["text"]
    assert len(claude.answer_calls) == 2


def test_spend_persists(make_bot, tmp_path):
    b = make_bot()
    b.state.add_spend(0.5)
    assert State(tmp_path / "s.json").spent_today() == pytest.approx(0.5)


def test_rate_limit_per_channel(make_bot, monkeypatch):
    monkeypatch.setenv("QA_CHANNEL_HOURLY", "2")
    b = make_bot()
    b.state.set_muted("C065B5YEG1G", False)
    out = [b.handle(msg("what shipped yesterday?")) for _ in range(3)]
    assert out == ["scan:answered", "scan:answered", "scan:rate-limited"]
    assert b.handle(msg("what shipped yesterday?", channel="C0AML8KKF4Z")) == "scan:muted"


def test_kill_switch_in_bot(make_bot, monkeypatch):
    monkeypatch.setenv("QA_ENABLED", "0")
    b = make_bot()
    assert b.handle(mention("status")) == "disabled"
    assert b.slack.posts == []


def test_handle_safe_swallows_errors(make_bot):
    b = make_bot()
    b.handle = lambda p: 1 / 0
    assert b.handle_safe({"event_id": "x"}) == "error"
