"""Event handling: ignore rules, mute/unmute/status commands, scan-mode gating, answer + post.

Every handler returns a short outcome string (logged, and asserted on in tests)."""
from __future__ import annotations

import logging
import re

import config
import knowledge
from llm import Claude
from scrub import is_personal, scrub_output
from slack_api import Slack
from state import RateLimiter, State

log = logging.getLogger("slack_qa.bot")

FOOTER = "_Automated answer from Tulle Bot (read-only; from the experiment registry, #deployment, commit logs and notes). Can be wrong - Vivek can confirm._"
OUT_OF_SCOPE = "I only cover product changes, experiments and their outcomes - Vivek can confirm anything else."
HELP = ("I'm Tulle Bot (automated, read-only). I answer questions about recent changes: what shipped, experiments "
        "(hypothesis, metric, status, results), deploys, what's on or off.\n"
        "`@Tulle Bot mute` - only answer when @-mentioned here.\n"
        "`@Tulle Bot unmute` - also watch this channel's questions and answer the ones I can.\n"
        "`@Tulle Bot status` - show this channel's setting.")
IGNORED_SUBTYPES_OK = {None, "thread_broadcast", "file_share"}
QUESTION_START = re.compile(
    r"^\s*(why|what|whats|what's|when|how|is|are|was|were|did|does|do|can|could|who|where|which|has|have|should|"
    r"will|would|any|anyone|anybody)\b", re.I)
MENTION = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")
MAX_WORDS = 160


def bot_user_id(payload: dict, slack: Slack | None = None) -> str | None:
    for a in payload.get("authorizations") or []:
        if a.get("is_bot") and a.get("user_id"):
            return a["user_id"]
    return config.bot_user_id() or (slack.bot_user_id() if slack is not None else None)


def should_ignore(ev: dict, bot_uid: str | None) -> str | None:
    """Reason to ignore the event, or None to handle it."""
    if ev.get("bot_id") or ev.get("bot_profile"):
        return "bot-message"
    if ev.get("subtype") not in IGNORED_SUBTYPES_OK:
        return f"subtype:{ev.get('subtype')}"
    if not ev.get("user"):
        return "no-user"
    if bot_uid and ev.get("user") == bot_uid:
        return "own-message"
    if ev.get("channel_type") in ("im", "mpim"):
        return "dm"
    if not ev.get("channel"):
        return "no-channel"
    return None


def looks_like_question(text: str) -> bool:
    t = MENTION.sub("", text or "").strip()
    if len(t) < 12:
        return False
    return "?" in t or bool(QUESTION_START.match(t))


def cap_words(text: str, n: int = MAX_WORDS) -> str:
    words = text.split()
    return text if len(words) <= n else " ".join(words[:n]) + " ..."


class Bot:
    def __init__(self, slack: Slack | None = None, claude: Claude | None = None, state: State | None = None,
                 limiter: RateLimiter | None = None, github=None, know=knowledge):
        self.slack = slack or Slack()
        self.claude = claude or Claude()
        self.state = state or State()
        self.limiter = limiter or RateLimiter()
        if github is None:
            from github_api import GitHub
            github = GitHub()
        self.github = github
        self.know = know

    # --- entry point -------------------------------------------------------------------------------------
    def handle_safe(self, payload: dict) -> str:
        try:
            out = self.handle(payload)
        except Exception:  # noqa: BLE001 - background task: log, never raise
            log.exception("handler failed")
            out = "error"
        log.info("event %s -> %s", payload.get("event_id"), out)
        return out

    def handle(self, payload: dict) -> str:
        if not config.enabled():
            return "disabled"
        ev = payload.get("event") or {}
        bot_uid = bot_user_id(payload, self.slack)
        why = should_ignore(ev, bot_uid)
        if why:
            return f"ignored:{why}"
        etype = ev.get("type")
        if etype == "app_mention":
            return self.on_mention(ev, bot_uid)
        if etype == "message":
            if bot_uid and f"<@{bot_uid}" in (ev.get("text") or ""):
                return "ignored:mention-handled-by-app_mention"
            return self.on_scan(ev)
        return f"ignored:type:{etype}"

    # --- @-mention ---------------------------------------------------------------------------------------
    def on_mention(self, ev: dict, bot_uid: str | None) -> str:
        channel, text = ev["channel"], ev.get("text") or ""
        reply_ts = ev.get("thread_ts") or ev.get("ts")
        cmd = re.sub(r"[^a-z ]", "", MENTION.sub("", text).lower()).strip()
        if cmd in ("mute", "unmute", "status", "help", ""):
            return self.command(cmd or "help", channel, ev.get("user", ""), reply_ts)
        question = MENTION.sub("", text).strip()
        if is_personal(question):
            self.post(channel, OUT_OF_SCOPE, reply_ts)
            return "mention:out-of-scope-rule"
        if self.state.over_budget():
            self.post(channel, "I've hit today's spending cap, so I can't look this up until tomorrow (UTC). "
                               "Vivek can confirm.", reply_ts)
            return "mention:over-budget"
        if not self.limiter.allow(channel):
            return "mention:rate-limited"
        return self.answer(question, ev, mode="mention")

    def command(self, cmd: str, channel: str, user: str, reply_ts: str | None) -> str:
        if cmd == "mute":
            self.state.set_muted(channel, True, by=user)
            self.post(channel, "Muted here: I'll only answer when someone @-mentions me. `@Tulle Bot unmute` to undo.",
                      reply_ts, footer=False)
        elif cmd == "unmute":
            self.state.set_muted(channel, False, by=user)
            self.post(channel, "Unmuted here: I'll watch questions in this channel and answer the ones about recent "
                               "changes I'm confident about (I stay quiet otherwise). `@Tulle Bot mute` to stop.",
                      reply_ts, footer=False)
        elif cmd == "status":
            muted = self.state.is_muted(channel)
            src = "set in this channel" if self.state.explicit(channel) else "the default"
            self.post(channel, f"This channel is {'MUTED (answers @-mentions only)' if muted else 'UNMUTED (watching questions)'}"
                               f" - {src}. Today's estimated spend: ${self.state.spent_today():.2f} of "
                               f"${config.daily_usd_cap():.2f}.", reply_ts, footer=False)
        else:
            self.post(channel, HELP, reply_ts, footer=False)
        return f"command:{cmd}"

    # --- scan mode ---------------------------------------------------------------------------------------
    def on_scan(self, ev: dict) -> str:
        channel, text = ev["channel"], ev.get("text") or ""
        if self.state.is_muted(channel):
            return "scan:muted"
        if ev.get("user") == config.owner_user_id():
            return "scan:owner"
        if not looks_like_question(text):
            return "scan:not-a-question"
        if is_personal(text):
            return "scan:personal"
        if self.state.over_budget():
            return "scan:over-budget"
        tri, usd = self.claude.triage(text, self.know.headlines())
        self.state.add_spend(usd)
        conf = float(tri.get("confidence") or 0)
        if tri.get("category") != "change_question" or not tri.get("is_question") or not tri.get("relevant"):
            return f"scan:triage-skip:{tri.get('category')}"
        if conf < config.scan_threshold():
            return f"scan:below-threshold:{conf:.2f}"
        if self.state.over_budget():
            return "scan:over-budget"
        if not self.limiter.allow(channel):
            return "scan:rate-limited"
        return self.answer(text, ev, mode="scan")

    # --- answer ------------------------------------------------------------------------------------------
    def answer(self, question: str, ev: dict, mode: str) -> str:
        channel = ev["channel"]
        reply_ts = ev.get("thread_ts") or ev.get("ts")
        dynamic = self.know.build_dynamic(question, channel, ev.get("thread_ts"), self.slack, self.github,
                                          current_ts=ev.get("ts"))
        data, usd = self.claude.answer(self.know.static_context(), dynamic, question, mode)
        self.state.add_spend(usd)
        text = (data.get("answer") or "").strip()
        conf = float(data.get("confidence") or 0)
        in_scope = data.get("in_scope", True)
        answerable = bool(data.get("answerable"))

        if mode == "scan":
            if not in_scope or not answerable or not text or conf < config.scan_threshold():
                return f"scan:answer-withheld:{conf:.2f}"
            if is_personal(text):
                return "scan:answer-personal-blocked"
        else:
            if not in_scope or is_personal(text):
                self.post(channel, OUT_OF_SCOPE, reply_ts)
                return "mention:out-of-scope"
            if not text:
                text = "I don't have that in the change history I can see. Vivek can confirm."
            elif not answerable and "Vivek can confirm" not in text:
                text = text.rstrip() + " Vivek can confirm."

        sources = [s for s in (data.get("sources") or []) if isinstance(s, str) and s.strip()][:5]
        body = cap_words(text)
        if sources:
            body += "\n_Sources: " + "; ".join(s.strip() for s in sources) + "_"
        self.post(channel, body, reply_ts)
        return f"{mode}:answered"

    def post(self, channel: str, text: str, thread_ts: str | None, footer: bool = True) -> dict:
        msg = scrub_output(text)
        msg = msg + "\n" + FOOTER if footer else "_Tulle Bot (automated):_ " + msg
        return self.slack.post_message(channel, msg, thread_ts=thread_ts)
