import json
import types

import pytest

import knowledge
import llm
from fakes import FakeSlack


def test_static_context_has_registry_and_is_stable():
    a = knowledge.static_context()
    b = knowledge.static_context()
    assert a is b or a == b                                   # byte-identical -> cacheable
    assert "<experiment_registry" in a and "EXP-2026-11" in a
    assert "PICK UP HERE" in a


def test_bm25_finds_relevant_note(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("## PICK UP HERE\n- x\n", encoding="utf-8")
    (mem / "project_price_report.md").write_text("# Price report\nThe price report replaced the PI card.", encoding="utf-8")
    (mem / "project_florals.md").write_text("# Florals\nFloral category pricing extraction.", encoding="utf-8")
    hits = knowledge.corpus(tmp_path).search("why did the price report change")
    assert hits and hits[0][0] == "memory note project_price_report"


def test_real_corpus_search_returns_bounded_text():
    hits = knowledge.corpus().search("price report pricing intelligence card", budget_chars=24000)
    assert hits and sum(len(t) for _, t, _ in hits) <= 24000


def test_build_dynamic_uses_thread_deploy_and_other_channels():
    slack = FakeSlack(
        history={"C0AML8KKF4Z": [{"ts": "1763448000.0", "bot_id": "B1", "username": "Tulle Bot",
                                  "text": "WeWeb staging publish 1401: price report rows simplified"}],
                 "COTHER": [{"ts": "1763448100.0", "user": "UKATE", "text": "LP copy updated for NY"},
                            {"ts": "1763448200.0", "user": "UKATE", "text": "Des was frustrated about it"}]},
        replies={"111.1": [{"ts": "111.1", "user": "UKATE", "text": "why did the price report change?"},
                           {"ts": "111.2", "user": "UDES", "text": "and when?"}]},
        channels=[{"id": "COTHER", "name": "growth"}, {"id": "C0AML8KKF4Z", "name": "deployment"}])
    text = knowledge.build_dynamic("why did the price report change?", "C065B5YEG1G", "111.1", slack, None,
                                   current_ts="111.2")
    assert "<current_conversation" in text and "and when?" in text
    assert "WeWeb staging publish 1401" in text
    assert "LP copy updated for NY" in text
    assert "<recent_commits" in text                                         # synced fallback without token


def test_build_dynamic_live_github():
    class FakeGH:
        enabled = True

        def recent_commits(self, days=14):
            return [{"repo": "ww-price-report", "full": "marthv/ww-price-report", "sha": "2201271",
                     "date": "2026-11-18 06:00", "subject": "locked view shows one unlock card"}]

        def commit_summary(self, full, sha):
            return {"repo": "ww-price-report", "sha": sha, "stats": {"total": 40},
                    "files": [{"file": "src/wwElement.vue", "+": 30, "-": 10}]}
    text = knowledge.build_dynamic("what did 2201271 change?", "C1", None, FakeSlack(), FakeGH())
    assert "GitHub live" in text and "src/wwElement.vue (+30/-10)" in text


def test_cost_usd():
    u = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_creation_input_tokens": 0,
         "cache_read_input_tokens": 1_000_000}
    assert llm.cost_usd("claude-sonnet-5-5", u) == pytest.approx(2.0 + 1.0 + 0.10)
    assert llm.cost_usd("unknown-model", {"output_tokens": 1_000_000}) == pytest.approx(25.0)


class _Resp:
    def __init__(self, data, model="claude-sonnet-5-5", stop="end_turn"):
        self.content = [types.SimpleNamespace(type="text", text=json.dumps(data))]
        self.usage = types.SimpleNamespace(input_tokens=1000, output_tokens=200,
                                           cache_creation_input_tokens=0, cache_read_input_tokens=30000)
        self.model, self.stop_reason = model, stop


class BadRequestError(Exception):
    pass


def test_answer_call_shape_and_fallback_retry():
    calls = []
    good = {"in_scope": True, "answerable": True, "confidence": 0.9, "answer": "x", "sources": ["s"]}

    def beta_create(**kw):
        calls.append(("beta", kw))
        raise BadRequestError("fallbacks not allowed")

    def create(**kw):
        calls.append(("plain", kw))
        return _Resp(good)
    client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=beta_create)),
                                   messages=types.SimpleNamespace(create=create))
    data, usd = llm.Claude(client).answer("STATIC", "DYN", "q?", "scan")
    assert data == good and usd > 0
    beta_kw = calls[0][1]
    assert beta_kw["fallbacks"] == "default" and beta_kw["model"] == "claude-sonnet-5-5"
    assert beta_kw["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert beta_kw["system"][1]["text"] == "STATIC"
    assert "DYN" in beta_kw["messages"][0]["content"]
    assert calls[1][0] == "plain" and "fallbacks" not in calls[1][1]


def test_triage_refusal_is_low_confidence():
    client = types.SimpleNamespace(messages=types.SimpleNamespace(
        create=lambda **kw: _Resp({}, model="claude-haiku-5-5", stop="refusal")))
    data, _ = llm.Claude(client).triage("q?", "")
    assert data["confidence"] == 0.0
