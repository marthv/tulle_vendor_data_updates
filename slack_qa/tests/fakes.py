import time

from slack_api import Slack


class FakeSlack(Slack):
    """Real Slack class with the HTTP layer replaced: records posts, serves canned history."""

    def __init__(self, history=None, replies=None, channels=None):
        super().__init__(token="xoxb-test")
        self.posts = []
        self._history = history or {}
        self._replies = replies or {}
        self._channels = channels or []

    def _call(self, method, http="GET", **params):
        if method == "chat.postMessage":
            self.posts.append(params)
            return {"ok": True}
        if method == "conversations.history":
            return {"ok": True, "messages": list(reversed(self._history.get(params["channel"], [])))}
        if method == "conversations.replies":
            return {"ok": True, "messages": self._replies.get(params["ts"], [])}
        if method == "users.conversations":
            return {"ok": True, "channels": self._channels}
        if method == "users.info":
            return {"ok": True, "user": {"profile": {"display_name": "Kate"}}}
        if method == "auth.test":
            return {"ok": True, "user_id": "UBOT"}
        return {"ok": False, "error": "unknown"}


class FakeClaude:
    def __init__(self, triage=None, answer=None, triage_usd=0.001, answer_usd=0.05):
        self.triage_result = triage or {"is_question": True, "category": "change_question", "relevant": True,
                                        "confidence": 0.9, "reason": "x"}
        self.answer_result = answer or {"in_scope": True, "answerable": True, "confidence": 0.9,
                                        "answer": "The price report replaced the PI card on 2026-11-17.",
                                        "sources": ["EXP-2026-11-price-report-vs-pi-card"]}
        self.triage_usd, self.answer_usd = triage_usd, answer_usd
        self.triage_calls, self.answer_calls = [], []

    def triage(self, message, headlines):
        self.triage_calls.append(message)
        return dict(self.triage_result), self.triage_usd

    def answer(self, static, dynamic, question, mode):
        self.answer_calls.append((question, mode, dynamic))
        return dict(self.answer_result), self.answer_usd


class FakeKnowledge:
    @staticmethod
    def headlines():
        return "- change 2026-11-17: price report live"

    @staticmethod
    def static_context():
        return "STATIC"

    @staticmethod
    def build_dynamic(question, channel, thread_ts, slack, github, current_ts=None):
        return "DYNAMIC"


def msg(text, user="UKATE", channel="C065B5YEG1G", **extra):
    ev = {"type": "message", "text": text, "user": user, "channel": channel, "channel_type": "channel",
          "ts": f"{time.time():.6f}"}
    ev.update(extra)
    return {"type": "event_callback", "event_id": f"Ev{time.time_ns()}", "event": ev,
            "authorizations": [{"user_id": "UBOT", "is_bot": True}]}


def mention(text, **extra):
    p = msg(f"<@UBOT> {text}", **extra)
    p["event"]["type"] = "app_mention"
    p["event"].pop("channel_type", None)
    return p
