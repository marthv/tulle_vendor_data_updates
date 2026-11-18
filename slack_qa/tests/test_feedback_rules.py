"""Feedback in Slack -> standing writing rules (no code change needed to teach the bot or the #deployment notes)."""
import pytest
from fastapi.testclient import TestClient

import app as app_mod
from bot import Bot
from fakes import FakeClaude, FakeKnowledge, FakeSlack, mention
from state import RateLimiter, State

DES = "these updates are a little long and kind of hard to follow can you simplify?"


@pytest.fixture
def make_bot(tmp_path):
    def _make(claude=None):
        return Bot(slack=FakeSlack(), claude=claude or FakeClaude(), state=State(tmp_path / "slack_qa_state.json"),
                   limiter=RateLimiter(), github=None, know=FakeKnowledge)
    return _make


def feedback_claude(rule="Keep it to 3 short bullets; lead with what couples will notice.", target="deploy_notes",
                    wants=True):
    c = FakeClaude()
    c.feedback_result = {"is_feedback": True, "target": target, "rule": rule, "also_wants_reply": wants}
    return c


def test_des_feedback_saves_rule_and_answers_with_it(make_bot):
    c = feedback_claude()
    b = make_bot(c)
    out = b.handle(mention(DES, channel="C0AML8KKF4Z"))
    assert out.startswith("learn:rule-1+mention:answered")
    assert b.state.rules("deploy_notes")[0]["text"].startswith("Keep it to 3 short bullets")
    assert "lead with what couples" in c.style_rules_seen[-1]          # the rewrite already follows it
    post = b.slack.posts[-1]["text"]
    assert "standing rule 1" in post and "forget 1" in post


def test_preference_only_confirms_without_answer_call(make_bot):
    c = feedback_claude(wants=False, target="bot_answers", rule="Use plain words, no jargon.")
    b = make_bot(c)
    assert b.handle(mention("from now on please use plainer words")) == "learn:rule-1"
    assert c.answer_calls == []
    assert b.state.rules("bot_answers")[0]["text"] == "Use plain words, no jargon."
    assert b.state.rules("deploy_notes") == []


def test_not_feedback_falls_through_to_normal_answer(make_bot):
    c = FakeClaude()   # classifier says not feedback
    b = make_bot(c)
    assert b.handle(mention("why is the price report so long now?")) == "mention:answered"
    assert len(c.feedback_calls) == 1 and b.state.rules() == []


def test_no_hint_skips_classifier(make_bot):
    c = FakeClaude()
    b = make_bot(c)
    b.handle(mention("what shipped yesterday?"))
    assert c.feedback_calls == []


def test_rules_list_and_forget(make_bot):
    b = make_bot(feedback_claude(wants=False))
    b.handle(mention("the staging notes are too long"))
    assert b.handle(mention("rules")) == "command:rules"
    assert "1. [the #deployment staging notes]" in b.slack.posts[-1]["text"]
    assert b.handle(mention("forget 1")) == "command:forget"
    assert b.state.rules() == []
    assert b.handle(mention("forget 7")) == "command:forget"
    assert "no rule 7" in b.slack.posts[-1]["text"]


def test_bot_answer_rules_reach_answer_prompt(make_bot):
    c = feedback_claude(wants=False, target="both", rule="Answer in 2 sentences max.")
    b = make_bot(c)
    b.handle(mention("going forward keep it shorter"))
    c.feedback_result = {"is_feedback": False, "target": "both", "rule": "", "also_wants_reply": False}
    b.handle(mention("what shipped yesterday?"))
    assert "Answer in 2 sentences max." in c.style_rules_seen[-1]


def test_rule_cap_drops_oldest(tmp_path):
    s = State(tmp_path / "s.json")
    for i in range(State.MAX_RULES + 1):
        _, dropped = s.add_rule(f"rule {i}", "both")
    assert dropped["text"] == "rule 0" and len(s.rules()) == State.MAX_RULES


def test_style_rules_endpoint(make_bot, monkeypatch):
    b = make_bot(feedback_claude(wants=False))
    b.handle(mention("the staging notes are too long"))
    client = TestClient(app_mod.app)
    assert client.get("/style-rules").status_code == 404              # off until QA_RULES_TOKEN is set
    monkeypatch.setenv("QA_RULES_TOKEN", "t0k")
    assert client.get("/style-rules", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/style-rules", params={"target": "deploy_notes"}, headers={"Authorization": "Bearer t0k"})
    assert r.status_code == 200 and r.json()["rules"][0]["id"] == 1
    r = client.get("/style-rules", params={"target": "bot_answers"}, headers={"Authorization": "Bearer t0k"})
    assert r.json()["rules"] == []
