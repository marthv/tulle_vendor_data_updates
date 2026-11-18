"""Event handling: ignore rules, mute/unmute/status commands, scan-mode gating, answer + post.

Every handler returns a short outcome string (logged, and asserted on in tests)."""
from __future__ import annotations

import logging
import re
import threading
import time

import config
import knowledge
from llm import Claude
from scrub import is_interpersonal, scrub_output
from slack_api import Slack
from state import RateLimiter, State

log = logging.getLogger("slack_qa.bot")

FOOTER = "_Automated answer from Tulle Bot (read-only; from the experiment registry, #deployment, commit logs and notes). Can be wrong - Vivek can confirm._"
OUT_OF_SCOPE = "I only cover product changes, experiments and their outcomes (no opinions about people or decisions that are Vivek's call) - Vivek can confirm anything else."
HELP = ("I'm Tulle Bot (automated, read-only). I answer questions about recent changes: what shipped, experiments "
        "(hypothesis, metric, status, results), deploys, what's on or off.\n"
        "`@Tulle Bot mute` - only answer when @-mentioned here.\n"
        "`@Tulle Bot unmute` - also watch this channel's questions and answer the ones I can.\n"
        "`@Tulle Bot status` - show this channel's setting.")
IGNORED_SUBTYPES_OK = {None, "thread_broadcast", "file_share"}
QUESTION_START = re.compile(
    r"^\s*(why|what|whats|what's|when|how|is|are|was|were|did|does|do|can|could|who|where|which|has|have|should|"
    r"will|would|any|anyone|anybody)\b", re.I)
REPORT_HINT = re.compile(
    r"\b(report|export|csv|spreadsheet|download|how many|count of|number of|revenue|payments?|sign-?ups?|pay rate|"
    r"nps|ratings?|planner usage|usage|venue (?:requests?|lookups?)|save offer|tt60|tt20|table \d+|pull)\b", re.I)
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
                 limiter: RateLimiter | None = None, github=None, know=knowledge, xano=None,
                 working_after: float = 5.0):
        self.slack = slack or Slack()
        self.claude = claude or Claude()
        self.state = state or State()
        self.limiter = limiter or RateLimiter()
        if github is None:
            from github_api import GitHub
            github = GitHub()
        self.github = github
        self.know = know
        if xano is None:
            from xano_ro import ReadOnlyXano
            xano = ReadOnlyXano()
        self.xano = xano
        self.working_after = working_after

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
        if is_interpersonal(question):
            self.post(channel, OUT_OF_SCOPE, reply_ts)
            return "mention:out-of-scope-rule"
        if self.state.over_budget():
            self.post(channel, "I've hit today's spending cap, so I can't look this up until tomorrow (UTC). "
                               "Vivek can confirm.", reply_ts)
            return "mention:over-budget"
        if not self.limiter.allow(channel):
            return "mention:rate-limited"
        if REPORT_HINT.search(question) and getattr(self.xano, "enabled", False):
            out = self.report(question, ev)
            if out is not None:
                return out
        return self.answer(question, ev, mode="mention")

    # --- Xano reports (@-mention only; read-only) -----------------------------------------------------------
    def report(self, question: str, ev: dict) -> str | None:
        """Route to a named report, run it read-only, post summary + CSV. None = not a report request."""
        import reports
        channel = ev["channel"]
        reply_ts = ev.get("thread_ts") or ev.get("ts")
        context = question
        if ev.get("thread_ts") and ev.get("thread_ts") != ev.get("ts"):
            prior = [m.get("text") or "" for m in self.slack.replies(channel, ev["thread_ts"])][-6:]
            context = "Earlier in this thread:\n" + "\n".join(prior) + "\n\nLatest request:\n" + question
        today = time.strftime("%Y-%m-%d", time.gmtime())
        route, usd = self.claude.route(context, today)
        self.state.add_spend(usd)
        name = route.get("report") or "none"
        if name == "none":
            return None
        if name == "clarify":
            self.post(channel, route.get("clarifying_question") or "Which report do you want: payments, sign-ups "
                      "and pay rate, Planner usage, venue requests, save offer, NPS, or an export of one table?",
                      reply_ts, footer=False)
            return "report:clarify"
        if self.state.reports_today() >= config.daily_report_cap():
            self.post(channel, f"I've run today's {config.daily_report_cap()} reports (UTC day cap). "
                               "Try again tomorrow or ask Vivek to raise QA_DAILY_REPORTS.", reply_ts, footer=False)
            return "report:cap"
        self.state.add_report()
        params = {k: route.get(k) for k in ("start_date", "end_date", "group_by", "plan", "table", "columns",
                                            "include_internal") if route.get(k) not in (None, "", [])}
        timer = threading.Timer(self.working_after, lambda: self.post(
            channel, f"Working on the {name.replace('_', ' ')} report - reading Xano (read-only)...", reply_ts,
            footer=False))
        timer.daemon = True
        timer.start()
        try:
            rep = reports.run(name, params, self.xano, int(time.time() * 1000))
        except PermissionError as e:
            timer.cancel()
            self.post(channel, f"I can't export that: {e}. Exportable tables: users (1), payment_log (16), "
                               "nps_response (77), rec_usage (79), venue_lookup_log.", reply_ts, footer=False)
            return "report:denied"
        except Exception:  # noqa: BLE001
            timer.cancel()
            log.exception("report %s failed", name)
            self.post(channel, "I couldn't read Xano just now, so no report. Try again in a few minutes.",
                      reply_ts, footer=False)
            return "report:error"
        timer.cancel()
        comment = scrub_output(f"{rep.summary}\n_Tulle Bot automated report (read-only Xano)._")
        if not rep.rows:
            self.slack.post_message(channel, comment, thread_ts=reply_ts)
            return f"report:{name}:empty"
        fname = f"tulle_{name}_{params.get('start_date', 'all')}_{params.get('end_date', today)}.csv"
        up = self.slack.upload_file(channel, reply_ts, fname, rep.csv_bytes(), title=fname, comment=comment)
        if not up.get("ok"):
            self.slack.post_message(channel, comment + f"\n(CSV upload failed: {up.get('error')} - the app may "
                                    "need the files:write scope.)", thread_ts=reply_ts)
            return f"report:{name}:upload-failed"
        return f"report:{name}"

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
        if is_interpersonal(text):
            return "scan:interpersonal"
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
            if is_interpersonal(text):
                return "scan:answer-interpersonal-blocked"
        else:
            if not in_scope or is_interpersonal(text):
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
