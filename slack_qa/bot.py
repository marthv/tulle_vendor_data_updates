"""Event handling: ignore rules, mute/unmute/status commands, scan-mode gating, answer + post.

Every handler returns a short outcome string (logged, and asserted on in tests)."""
from __future__ import annotations

import logging
import re
import threading
import time

import config
import knowledge
from cards import esc as esc_text
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
        "`@Tulle Bot status` - show this channel's setting.\n"
        "Tell me how you'd like things written (e.g. `@Tulle Bot the staging notes are too long`) and I'll keep to it "
        "from then on. `@Tulle Bot rules` lists what I've learned; `@Tulle Bot forget 2` removes one.\n"
        "*Vendor fixes* (I post a card; nothing changes until an approver presses Approve):\n"
        "`@Tulle Bot hide V1234 venue asked to be removed` / `@Tulle Bot unhide V1234`\n"
        "`@Tulle Bot change V1234 website to example.com` (also name, description, contact, seated capacity, type)\n"
        "`@Tulle Bot what's waiting for approval?` / `@Tulle Bot what was applied this week?`\n"
        "*Pricing PDFs*: `@Tulle Bot PDFs for V1234` or `@Tulle Bot give me all the pdfs for Gramercy Mansion`\n"
        "*Duplicates* (I post a merge card; nothing moves until an approver presses Approve): "
        "`@Tulle Bot merge Rose Hall and Rose Hall Barn` · `@Tulle Bot merge V13433 into V105` (keeps the second) · "
        "`@Tulle Bot find duplicates of Gramercy Mansion` · `@Tulle Bot scan duplicates 20`\n"
        "*Customer lookup* (team only; read-only): `@Tulle Bot look up user 34819` or "
        "`@Tulle Bot what does user 34819 see?`")
IGNORED_SUBTYPES_OK = {None, "thread_broadcast", "file_share"}
QUESTION_START = re.compile(
    r"^\s*(why|what|whats|what's|when|how|is|are|was|were|did|does|do|can|could|who|where|which|has|have|should|"
    r"will|would|any|anyone|anybody)\b", re.I)
REPORT_HINT = re.compile(
    r"\b(report|export|csv|spreadsheet|download|how many|count of|number of|revenue|payments?|sign-?ups?|pay rate|"
    r"nps|ratings?|planner usage|usage|venue (?:requests?|lookups?)|save offer|tt60|tt20|table \d+|pull)\b", re.I)
MENTION = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")
MAX_WORDS = 160
# Cheap gate before the feedback classifier (Haiku): only messages that sound like a writing preference.
FEEDBACK_HINT = re.compile(
    r"\b(too (?:long|short|much|technical|wordy|detailed)|long|wordy|verbose|shorter|simpler|simplify|plain(?:er)?|"
    r"jargon|technical|hard to (?:follow|read|understand)|confusing|easier to read|less detail|more detail|"
    r"(?:fewer|less|more) bullets?|tl;?dr|summar(?:y|ise|ize)|from now on|going forward|in future|next time|"
    r"please (?:always|don'?t|stop|keep|use|say)|(?:always|never|stop|don'?t) (?:include|mention|list|say|use|write)|"
    r"(?:i|we)'?d (?:prefer|rather|like)|prefer)\b", re.I)
FORGET = re.compile(r"^\s*(?:forget|remove|delete|drop|undo)\s+(?:rule\s*)?#?(\d+)\s*$", re.I)
TARGET_LABEL = {"deploy_notes": "the #deployment staging notes", "bot_answers": "my answers",
                "both": "the #deployment notes and my answers"}


def format_rules(rules: list[dict]) -> str:
    return "\n".join(f"{r['id']}. {r['text']}" for r in rules)


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
    if ev.get("channel") in config.blocked_channels():
        return "blocked-channel"
    ut = ev.get("user_team") or ev.get("team")
    if ut and config.team_id() and ut != config.team_id():
        return "external-user"
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
                 working_after: float = 5.0, store=None, engine=None):
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
        if store is None:
            from proposals import Store
            store = Store()
        self.store = store
        if engine is None:
            from engine import Engine
            engine = Engine()
        self.engine = engine
        self._feedback = None
        self._pdf_index = None

    @property
    def feedback(self):
        if self._feedback is None:
            from feedback_watch import FeedbackWatcher
            self._feedback = FeedbackWatcher(self.slack, self.claude, self.state, self.store, self.xano)
        return self._feedback

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
        if config.feedback_channel() and ev.get("channel") == config.feedback_channel():
            import feedback_watch
            if feedback_watch.is_watchable(ev, bot_uid):
                return self.feedback.handle(ev)
            if ev.get("type") == "message":
                return "ignored:feedback-channel"
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
        if cmd in ("mute", "unmute", "status", "help", "rules", ""):
            return self.command(cmd or "help", channel, ev.get("user", ""), reply_ts)
        question = MENTION.sub("", text).strip()
        fm = FORGET.match(question)
        if fm:
            gone = self.state.remove_rule(int(fm.group(1)))
            self.post(channel, f'Removed rule {gone["id"]}: "{gone["text"]}"' if gone else
                      f"There's no rule {fm.group(1)}. `@Tulle Bot rules` lists them.", reply_ts, footer=False)
            return "command:forget"
        out = self.customer_lookup(question, ev)
        if out is not None:
            return out
        out = self.pdf_list(question, ev)
        if out is not None:
            return out
        out = self.merge_request(question, ev)
        if out is not None:
            return out
        out = self.vendor_action(question, ev)
        if out is not None:
            return out
        if is_interpersonal(question):
            self.post(channel, OUT_OF_SCOPE, reply_ts)
            return "mention:out-of-scope-rule"
        if self.state.over_budget():
            self.post(channel, "I've hit today's spending cap, so I can't look this up until tomorrow (UTC). "
                               "Vivek can confirm.", reply_ts)
            return "mention:over-budget"
        if not self.limiter.allow(channel):
            return "mention:rate-limited"
        if FEEDBACK_HINT.search(question):
            out = self.learn(question, ev)
            if out is not None:
                return out
        out = self.capabilities(question, ev)
        if out is not None:
            return out
        if REPORT_HINT.search(question) and getattr(self.xano, "enabled", False):
            out = self.report(question, ev)
            if out is not None:
                return out
        return self.answer(question, ev, mode="mention")

    # --- customer lookup (took over Tulle Ops' /tulle view-as, 2026-11-21) -----------------------------------
    def customer_lookup(self, question: str, ev: dict) -> str | None:
        """'look up jane@x.com' / 'what does user 123 see' -> an EPHEMERAL snapshot for the asker only. None = not a
        lookup. Never posts customer data to the channel, never stores or logs it."""
        import customer_lookup as cl
        target = cl.parse(question)
        channel, user = ev["channel"], ev.get("user", "")
        # private replies go into the thread only when the mention was in one; a top-level mention's thread has no
        # replies to open, so the answer could go unseen (QA 11-21)
        thread = ev.get("thread_ts") if ev.get("thread_ts") and ev.get("thread_ts") != ev.get("ts") else None

        def tell(text):
            self.slack.post_ephemeral(channel, user, text, thread_ts=thread)
        if target is None:
            if cl.near_miss(question):          # never pass a customer's email on to the model or a public reply
                tell(cl.USAGE)
                return "lookup:usage"
            return None
        if target.get("invalid"):
            tell("That user ID isn't valid. " + cl.USAGE)
            return "lookup:invalid"
        if not cl.allowed(user):
            tell("You're not on the customer-lookup list. That list is separate from vendor fixes, on purpose. "
                 "Vivek can add you.")
            log.info("lookup denied for %s", user)
            return "lookup:denied"
        if not cl.secret():
            tell("Customer lookup isn't switched on yet (its Xano key isn't set).")
            return "lookup:not-configured"
        if not self.limiter.allow(channel):
            tell("Too many requests in this channel just now. Try again in a minute.")
            return "lookup:rate-limited"
        res = cl.fetch(target, user)
        if not res.get("ok"):
            tell("Couldn't look that up: " + ("Xano didn't answer, try again in a minute." if res.get("unknown")
                                              else str(res.get("error"))[:200]))
            log.info("lookup by %s failed (%s)", user, "unknown" if res.get("unknown") else "refused")
            return "lookup:failed"
        if cl.public():                            # owner 2026-11-22: the team can all see the answer
            self.slack.post_message(channel, cl.render(res, shared=True), thread_ts=ev.get("thread_ts") or ev.get("ts"),
                                    sanitize=True)
        else:
            tell(cl.render(res))
        log.info("lookup by %s ok (%s)", user, "channel" if cl.public() else "private")   # never the email/answer
        return "lookup:ok"

    # --- a vendor's pricing PDFs (owner 2026-11-22) --------------------------------------------------------------
    def pdf_list(self, question: str, ev: dict) -> str | None:
        """'PDFs for V1234' / 'give me all the pdfs for Gramercy Mansion' -> a list with Drive links. Read-only, no
        model call. None = not a PDF-list request."""
        import pdf_list as pl
        target = pl.parse(question)
        if target is None:
            return None
        channel = ev["channel"]
        reply_ts = ev.get("thread_ts") or ev.get("ts")

        def say(text):
            self.slack.post_message(channel, "_Tulle Bot (automated, read-only):_ " + text, thread_ts=reply_ts,
                                    sanitize=True)       # email-only filter: keeps Drive links intact
        import customer_lookup as cl
        user = ev.get("user", "")
        if not cl.allowed(user):                 # paid PDF links: team list only (QA 11-22), same list as lookup
            self.slack.post_ephemeral(channel, user, "PDF links are for the Tulle team only.",
                                      thread_ts=ev.get("thread_ts"))
            return "pdfs:denied"
        if not self.limiter.allow(channel):
            say("Too many requests in this channel just now. Try again in a minute.")
            return "pdfs:rate-limited"
        if not getattr(self.xano, "enabled", False):
            say("I can't read the PDF list from here right now.")
            return "pdfs:no-xano"
        try:
            vendor, hits = pl.resolve_vendor(self.feedback.directory, target)
        except Exception:  # noqa: BLE001
            log.exception("pdf list: vendor lookup failed")
            say("I couldn't read the vendor list just now. Try again in a few minutes.")
            return "pdfs:lookup-error"
        if vendor is None:
            if not hits and not pl.is_id(target):
                return None                     # "pdfs for venues in Ohio" is a question, not a vendor (QA 11-22)
            say(pl.candidates_text(target, hits))
            return "pdfs:ambiguous" if hits else "pdfs:not-found"
        if self._pdf_index is None:
            self._pdf_index = pl.PdfIndex(self.xano)
        timer = None
        if not self._pdf_index.fresh():
            timer = threading.Timer(self.working_after, lambda: say(
                "Reading the PDF list (about a minute the first time, instant after that)..."))
            timer.daemon = True
            timer.start()
        try:
            rows, built_at, truncated = self._pdf_index.get(str(vendor.get("Vendor_ID")))
        except Exception:  # noqa: BLE001
            log.exception("pdf list: table 10 read failed")
            say("I couldn't read the PDF list just now. Try again in a few minutes.")
            return "pdfs:read-error"
        finally:
            if timer:
                timer.cancel()
        say(pl.render(vendor, rows, built_at, truncated))
        return f"pdfs:{len(rows)}"

    # --- start-up warm (owner 2026-11-22: first request after a restart waited ~3 min) ---------------------------
    def warm(self) -> str:
        """Load the vendor list in the background at start-up. Never raises."""
        if not getattr(self.xano, "enabled", False):
            return "warm:no-xano"
        try:
            t0 = time.time()
            n = len(self.feedback.directory.rows())
            log.info("warm: vendor list loaded (%d rows) in %.0f s", n, time.time() - t0)
            return f"warm:{n}"
        except Exception:  # noqa: BLE001
            log.exception("warm: vendor list load failed (requests will load it on demand)")
            return "warm:error"

    def _wait_note(self) -> str:
        try:
            cold = not self.feedback.directory.fresh()
        except Exception:  # noqa: BLE001
            cold = True
        return ("Loading the vendor list (about 3 minutes the first time after a restart, instant after that)..."
                if cold else "Checking the listings...")

    # --- merges by name / ID, and "find duplicates of X" (owner 2026-11-22) ---------------------------------------
    def merge_request(self, question: str, ev: dict) -> str | None:
        """'merge A and B' / 'merge A into B' / 'A and B are the same venue' -> the scan's own merge card in this thread;
        'find duplicates of X' / 'is X listed twice?' -> cards for X's duplicate groups. None = not a merge request.
        Nothing moves until an approver presses Approve (and Approve is a dry run until the Xano merge is built)."""
        import dedupe
        import merge_requests as mr
        import pdf_list as pl
        from interactions import post_proposal
        req = mr.parse(question)
        if req is None:
            return None
        channel = ev["channel"]
        reply_ts = ev.get("thread_ts") or ev.get("ts")

        def say(text):
            self.post(channel, text, reply_ts, footer=False)
        if req["kind"] == "merge_bad_into":
            say("Use \"into\" once, at the end: `@Tulle Bot merge V1 and V2 into V3` keeps V3.")
            return "merge:bad-into"
        if req["kind"] == "merge_incomplete":
            # "merge V12" -> hint; "can you merge these?" / "merge the planner" -> not about a vendor: normal Q&A
            t = req.get("target") or ""
            try:
                v, hits = pl.resolve_vendor(self.feedback.directory, t) if t else (None, [])
            except Exception:  # noqa: BLE001
                v, hits = None, []
            if v is None and not hits and not pl.is_id(t):
                return None
            say("Which two (or more) should I merge? e.g. `@Tulle Bot merge Rose Hall and Rose Hall Barn` or "
                "`@Tulle Bot merge V13433 into V105` (\"into\" keeps the second one).")
            return "merge:incomplete"
        if not self.limiter.allow(channel):
            say("Too many requests in this channel just now. Try again in a minute.")
            return "merge:rate-limited"
        if not getattr(self.xano, "enabled", False):
            say("I can't read the vendor list from here right now.")
            return "merge:no-xano"
        directory = self.feedback.directory
        resolve = lambda t: pl.resolve_vendor(directory, t)      # noqa: E731
        note = self._wait_note()               # decided now, before this request starts the load
        timer = threading.Timer(self.working_after, lambda: say(note))
        timer.daemon = True
        timer.start()
        try:
            if req["kind"] == "dupes_of":
                return self._dupes_of(req["target"], channel, reply_ts, say, resolve)
            vendors, err, looked_like_vendors = mr.split_targets(req["rest"], resolve)
            if err:
                if not looked_like_vendors:     # "merge the planner and cost tools": not vendors -> normal Q&A
                    return None
                say(err)
                return "merge:unresolved"
            gone = [v for v in vendors if str(v.get("merged_into") or "").strip()]
            if gone:                            # cached list can be a few hours old
                say(f"{esc_text(gone[0].get('Name'))} ({gone[0].get('Vendor_ID')}) was already merged into "
                    f"{gone[0].get('merged_into')}. Merge into that one instead.")
                return "merge:already-merged"
            why_not = mr.check_group(vendors)
            if why_not:
                say(why_not)
                return "merge:refused"
            vids = [str(v.get("Vendor_ID")) for v in vendors]
            no = dedupe.gold_refusal(vendors, vids[-1]) if req["into"] else ""
            if no:
                say(f"{no} Ask `@Tulle Bot merge {' and '.join(vids)}` and I'll keep that one.")
                return "merge:gold-refused"
            key = dedupe.group_key(vids)
            # the plain key AND any 'asked again' keys (QA 11-22: otherwise every re-ask made another card)
            from proposals import is_void
            prior = [p for p in self.store.all() if p.get("kind") == "merge" and not is_void(p)
                     and (p.get("key") == key or str(p.get("key", "")).startswith(key + ":asked:"))]
            open_p = next((p for p in prior if p.get("status") in ("pending", "applying", "unknown", "partial")), None)
            if open_p:
                say(f"There's already a card for these: {self._link(open_p)}.")
                return "merge:duplicate"
            if any(p.get("status") == "applied" for p in prior):
                say("These were already merged.")
                return "merge:already-merged"
            dry = next((p for p in prior if p.get("status") == "dry_run"), None)
            if dry:
                if config.merge_dry_run():
                    say(f"These were already approved ({self._link(dry)}) in a dry run, and merges are still in dry run, so "
                        "nothing has moved yet. Once merges are switched on, that card gets an *Apply for real* button "
                        "(`@Tulle Bot what's waiting for approval?` lists them).")
                else:                               # merges are on: redraw that card so its button is there now
                    import cards
                    if dry.get("channel") and dry.get("ts"):
                        text, blocks = cards.render(dry)
                        self.slack.update_message(dry["channel"], dry["ts"], text, blocks)
                    say(f"These were approved in a dry run, so nothing has moved yet. Merges are on now: press *Apply for "
                        f"real* on {self._link(dry)}.")
                return "merge:approved-dry-run"
            byid = {v["id"]: v for v in vendors}
            refs = dedupe.RefIndex(self.xano, max_pages=config.feedback_page_budget(),
                                   page_delay=config.dedupe_page_delay(), only={10, 36})
            fast = config.merge_card_fast() and refs.build_named_fast(set(vids))     # checks the page budget itself
            if not fast:
                refs.build(set(vids), rowids={int(v["id"]): str(v.get("Vendor_ID")) for v in vendors})
            pr = dedupe.build_proposal({"ids": sorted(byid, key=int), "rules": []}, byid, refs,
                                       survivor_vid=vids[-1] if req["into"] else "",
                                       why_text=mr.asked_why(vendors) + self._others_note(mr, vids))
            # a person asking again after an earlier Reject or Undo is a new decision: give it its own key
            k = pr["key"] + (f":asked:{ev.get('ts')}"
                             if any(p.get("status") in ("rejected", "undone") for p in prior) else "")
            p = self.store.create("merge", pr["title"], pr["what"], pr["why"], pr["next_steps"], pr["payload"],
                                  key=k, source=f"mention:{channel}:{ev.get('ts')}", plain=pr["plain"])
            if p is None:
                say("There's already a card for these.")
                return "merge:duplicate"
            res = post_proposal(self.slack, self.store, channel, p, thread_ts=reply_ts)
            return "merge:proposed" + ("" if res.get("ok") else f":post-failed:{res.get('error')}")
        except Exception:  # noqa: BLE001
            log.exception("merge request failed")
            say("I couldn't read the listings just now. Try again in a few minutes.")
            return "merge:error"
        finally:
            timer.cancel()

    def _others_note(self, mr, vids) -> str:
        """Another listing the scan would group with these: say so now, so all the duplicates go in one merge (one card, one
        undo). A later merge into the same listing also works (ep578 v4)."""
        try:
            rows = self.feedback.directory.rows()
            byid = {r["id"]: r for r in rows}
            more = sorted({str(byid[i].get("Vendor_ID")) for g in mr.dupes_of(rows, set(vids)) for i in g["ids"]} - set(vids))
        except Exception:  # noqa: BLE001 - a hint only
            log.exception("others note failed")
            return ""
        if not more:
            return ""
        return (f" *Also looks the same*: {', '.join(more)}. You can merge them together in one request (up to 3 "
                "duplicates).")

    def _dupes_of(self, target, channel, reply_ts, say, resolve) -> str:
        import dedupe
        import merge_requests as mr
        import pdf_list as pl
        from interactions import post_proposal
        vendor, hits = resolve(target)
        if vendor is not None:
            seeds = [vendor]
        elif (hits and 2 <= len(hits) <= mr.MAX_VENDORS
              and all(pl._exact(h.get("Name")) == pl._exact(target) for h in hits)):
            # exact name only: the loose "every word appears" fallback must never pick a group nobody named (QA 11-22)
            # owner 2026-11-22: "find duplicates of Gramercy Mansion" found two listings with exactly that name and
            # asked "which one?" - those listings ARE the question. Check them as a group.
            seeds = hits
        else:
            if not hits and not pl.is_id(target):   # "any duplicates for this week?" -> normal Q&A (QA 11-22)
                return None
            say(pl.candidates_text(target, hits))
            return "merge:dupes-ambiguous" if hits else "merge:dupes-not-found"
        vendor = seeds[0]
        rows = self.feedback.directory.rows()
        vids = {str(s.get("Vendor_ID")) for s in seeds}
        vid = ", ".join(sorted(vids))
        name = esc_text(vendor.get("Name"))
        groups = mr.dupes_of(rows, vids)
        byid = {r["id"]: r for r in rows}
        skip = self.store.handled_keys("merge")
        fresh = [g for g in groups
                 if dedupe.group_key([str(byid[i].get("Vendor_ID")) for i in g["ids"]]) not in skip][:mr.MAX_CARDS]
        why_text = ""
        if not fresh and len(seeds) > 1:
            # same exact name, but the scan's stricter rules (same Google listing / same address / nearby + same
            # website) didn't link them: propose them only when they are in the same state and category, and say so
            states = {dedupe.norm(s.get("State")) for s in seeds}
            cats = {str(s.get("Category") or "").strip().lower() for s in seeds}
            key = dedupe.group_key(sorted(vids))
            # and in the same PLACE (same city, or within 2 km): a chain like "Hilton Garden Inn" x3 in one state
            # is three hotels, not one (QA 11-22)
            if len(states) == 1 and len(cats) == 1 and key not in skip and mr.same_place(seeds) \
                    and not mr.warnings(seeds):
                fresh = [{"ids": sorted((s["id"] for s in seeds), key=int), "rules": []}]
                where = esc_text(seeds[0].get("State")) or "the same state"
                both = "both" if len(seeds) == 2 else f"all {len(seeds)}"
                why_text = (f"Same name, {both} in the same place in {where}. The scan's stricter checks (same Google "
                            "listing or address) didn't link them, so compare the addresses under Details before "
                            "approving.")
        others = [o for o in mr.same_name_others(rows, vendor) if str(o.get("Vendor_ID")) not in vids]
        if len(seeds) > 1:                     # the same-name listings themselves, when they weren't proposed
            others = [s for s in seeds] + others
        vid = str(vendor.get("Vendor_ID")) if len(seeds) == 1 else vid
        # owner 2026-11-22: when the group already has a card, link it and say where it stands (the old reply said
        # "no duplicates ... probably different businesses" about the very pair that had a card)
        gkeys = [dedupe.group_key([str(byid[i].get("Vendor_ID")) for i in g["ids"]]) for g in groups]
        if len(seeds) > 1:
            gkeys.append(dedupe.group_key(sorted(vids)))
        existing = []
        for k in dict.fromkeys(gkeys):
            from proposals import is_void
            ps = [p for p in self.store.all() if p.get("kind") == "merge" and not is_void(p)
                  and (p.get("key") == k or str(p.get("key", "")).startswith(k + ":asked:"))]
            if ps:
                # the card that explains the group best: open > approved in dry run > merged > rejected > others
                rank = {"pending": 0, "applying": 0, "unknown": 0, "partial": 0, "undoing": 0, "dry_run": 1,
                        "applied": 2, "rejected": 3, "undone": 4, "failed": 5, "post_failed": 6}
                existing.append(min(ps, key=lambda p: (rank.get(p.get("status"), 9),
                                                       "~" + str(p.get("created_at", "")))))
        if not fresh and existing:
            lines = [f"*{name}* ({vid}): there's already a card for "
                     + ("this group:" if len(existing) == 1 else f"these {len(existing)} groups:")]
            for p in existing[:mr.MAX_CARDS]:
                pay = p.get("payload") or {}
                ids = [str(x) for x in [pay.get("survivor")] + list(pay.get("losers") or []) if x]
                st = p.get("status")
                when = str(p.get("status_at") or p.get("created_at") or "")[:10]
                state = {"pending": "waiting for approval", "unknown": "needs a Re-check on the card",
                         "partial": "stopped part-way (Resume or Undo on the card)",
                         "applied": "already merged",
                         "undone": (f"merged, then undone. If it should be merged after all, `@Tulle Bot merge "
                                    f"{' and '.join(ids)}` makes a new card"),
                         "dry_run": f"approved on {when} in a dry run, so nothing has moved yet" + (
                             "; merges are still in dry run" if config.merge_dry_run() else
                             ". Merges are on now: press *Apply for real* on the card"),
                         "applying": "being applied right now", "undoing": "being undone right now",
                         "failed": "approved but it didn't apply (see the card)",
                         "post_failed": "the card couldn't be posted",
                         "rejected": (f"rejected on {when}. If that was wrong, `@Tulle Bot merge "
                                      f"{' and '.join(ids)}` makes a new card")}.get(st, st or "unknown")
                lines.append(f"• {self._link(p)} ({', '.join(ids)}): {state}.")
            say("\n".join(lines))
            return "merge:dupes-existing"
        if not fresh:
            held = mr.held_hidden(rows, vids)
            if held:                            # 2026-11-22: hidden can mean a takedown or wrong PDFs; a person decides
                lines = [f"*{name}* ({vid}) looks the same as these, but at least one of the listings is hidden from "
                         "couples, so I don't suggest the merge on my own:"]
                lines += [f"• {esc_text(o.get('Name'))} ({o.get('Vendor_ID')})"
                          + (", hidden" if dedupe.is_hidden(o) else "") for o in held[:8]]
                lines.append("Once you've checked why it is hidden, say `@Tulle Bot merge "
                             + " and ".join(sorted(vids | {str(o.get('Vendor_ID')) for o in held})) + "`.")
                say("\n".join(lines))
                return "merge:dupes-hidden"
            lines = [f"No new duplicates of *{name}* ({vid}) by the scan's rules (same Google listing, or same name in "
                     "the same place, or a near-identical name with the same website nearby)."]
            if others:
                lines.append("Listings with the same name in different places (probably different businesses):")
                lines += [f"• {esc_text(o.get('Name'))} ({o.get('Vendor_ID')})"
                          + (f", {esc_text(o.get('State'))}" if o.get("State") else "") for o in others[:8]]
                a = str(others[0].get("Vendor_ID"))
                b = str(vendor.get("Vendor_ID")) if a != str(vendor.get("Vendor_ID")) else (
                    str(others[1].get("Vendor_ID")) if len(others) > 1 else "V1234")
                lines.append(f"If two are really the same, say `@Tulle Bot merge {a} and {b}`.")
            say("\n".join(lines))
            return "merge:dupes-none"
        wanted = {str(byid[i].get("Vendor_ID")) for g in fresh for i in g["ids"]}
        refs = dedupe.RefIndex(self.xano, max_pages=config.feedback_page_budget(),
                               page_delay=config.dedupe_page_delay(), only={10, 36})
        refs.build(wanted, rowids={int(i): str(byid[i].get("Vendor_ID")) for g in fresh for i in g["ids"]})
        say(f"Found {len(fresh)} possible duplicate group{'s' if len(fresh) != 1 else ''} for *{name}* ({vid}). "
            "Nothing changes until someone approves a card.")
        posted = 0
        for g in fresh:
            try:
                pr = dedupe.build_proposal(g, byid, refs, why_text=why_text, suggested=True)
            except dedupe.Held as e:
                log.info("find duplicates: held on live rows: %s", e)
                ids = " and ".join(str(byid[i].get("Vendor_ID")) for i in g["ids"])
                say(f"I held back one group ({ids}): when I re-read the listings just now they no longer pass my checks "
                    f"({esc_text(str(e))}). If they really are the same, `@Tulle Bot merge {ids}` makes a card.")
                continue
            p = self.store.create("merge", pr["title"], pr["what"], pr["why"], pr["next_steps"], pr["payload"],
                                  key=pr["key"], source=f"mention:{channel}", plain=pr["plain"])
            if p is not None and post_proposal(self.slack, self.store, channel, p, thread_ts=reply_ts).get("ok"):
                posted += 1
        return f"merge:dupes:{posted}"

    @staticmethod
    def _link(p: dict) -> str:
        import vendor_actions as va
        return va._link(p)

    # --- vendor actions in plain words (took over from the retired Tulle Ops app, 2026-11-21) -------------------
    def vendor_action(self, question: str, ev: dict) -> str | None:
        """hide / unhide / change a field -> a proposal card (nothing changes until an approver presses Approve);
        'what's waiting' / 'what was applied' -> read-only lists. None = not a vendor action. No model call."""
        import vendor_actions as va
        req = va.parse(question)
        if req is None:
            return None
        channel = ev["channel"]
        reply_ts = ev.get("thread_ts") or ev.get("ts")
        if req["kind"] == "list_pending":
            # dry run switched off since these were approved: redraw their cards so the Apply button appears
            import cards
            ready = [p for p in va.ready_to_apply(self.store) if p.get("channel") and p.get("ts")]
            for p in sorted(ready, key=lambda p: str(p.get("created_at", "")))[:15]:   # = the 15 the list shows
                text, blocks = cards.render(p)
                self.slack.update_message(p["channel"], p["ts"], text, blocks)
            self.post(channel, va.list_pending(self.store), reply_ts, footer=False)
            return "vendor:list-pending"
        if req["kind"] == "list_applied":
            self.post(channel, va.list_applied(self.store), reply_ts, footer=False)
            return "vendor:list-applied"
        dup = None if (req["kind"] == "vendor_edit" and not req.get("field")) else va.open_card(
            self.store, req["kind"], req["vendor_id"], req.get("field") if req["kind"] == "vendor_edit" else None)
        if dup:
            self.post(channel, f"There's already a card waiting for this: {va._link(dup)}. Approve or reject that one "
                               "first.", reply_ts, footer=False)
            return f"vendor:{req['kind']}:duplicate"
        if not self.limiter.allow(channel):        # same per-channel cap as questions (QA 11-21)
            return "vendor:rate-limited"
        if not getattr(self.xano, "enabled", False):
            self.post(channel, "I can't read the vendor list from here right now, so I can't propose that.", reply_ts,
                      footer=False)
            return "vendor:no-xano"
        try:
            hits = self.feedback.directory.resolve(req["vendor_id"])
        except Exception:  # noqa: BLE001
            log.exception("vendor lookup failed")
            self.post(channel, "I couldn't read the vendor list just now. Try again in a few minutes.", reply_ts,
                      footer=False)
            return "vendor:lookup-error"
        if len(hits) != 1:
            self.post(channel, f"I can't find vendor {req['vendor_id']}. Check the ID (it looks like V1234).",
                      reply_ts, footer=False)
            return "vendor:not-found"
        if str(hits[0].get("merged_into") or "").strip():
            # a merged duplicate stays hidden: unhide/edit would bring back a listing whose PDFs and pricing moved
            # (merge audit 11-22). The cached list can be hours old, so check the row itself.
            self.post(channel, f"{req['vendor_id']} was merged into {hits[0].get('merged_into')}. Make the change on "
                               "that listing instead.", reply_ts, footer=False)
            return f"vendor:{req['kind']}:merged"
        pr = va.build_visibility(req, hits[0]) if req["kind"] == "visibility" else va.build_edit(req, hits[0])
        if isinstance(pr, str):
            self.post(channel, pr, reply_ts, footer=False)
            return f"vendor:{req['kind']}:declined"
        from interactions import post_proposal
        p = self.store.create(pr["kind"], pr["title"], pr["what"], pr["why"], pr["next_steps"], pr["payload"],
                              source=f"mention:{channel}:{ev.get('ts')}")
        res = post_proposal(self.slack, self.store, channel, p, thread_ts=reply_ts)
        log.info("vendor action %s proposed by %s: %s", pr["kind"], ev.get("user"), pr["title"])
        return f"vendor:{pr['kind']}:proposed" + ("" if res.get("ok") else f":post-failed:{res.get('error')}")

    # --- feedback -> standing rule -------------------------------------------------------------------------
    def learn(self, question: str, ev: dict) -> str | None:
        """Save a writing preference as a standing rule. None = not feedback (carry on as a normal question)."""
        channel = ev["channel"]
        reply_ts = ev.get("thread_ts") or ev.get("ts")
        if ev.get("thread_ts") and ev.get("thread_ts") != ev.get("ts"):
            ctx = self.slack.render(self.slack.replies(channel, ev["thread_ts"]))[-6:]
        else:
            ctx = self.slack.render(self.slack.history(channel, limit=5, days=7))[-5:]
        where = "#deployment (where the staging notes are posted)" if channel == config.deploy_channel() else "a channel"
        fb, usd = self.claude.feedback(question, f"Posted in {where}. Recent messages:\n" + "\n".join(ctx))
        self.state.add_spend(usd)
        rule_text = (fb.get("rule") or "").strip()
        if not fb.get("is_feedback") or not rule_text or is_interpersonal(rule_text):
            return None
        target = fb.get("target") if fb.get("target") in TARGET_LABEL else "both"
        rule, dropped = self.state.add_rule(rule_text, target, by=ev.get("user", ""), source=f"{channel}/{ev.get('ts')}")
        when = "from the next staging note" if target == "deploy_notes" else "from now on"
        note = (f'Got it - saved as standing rule {rule["id"]} for {TARGET_LABEL[target]}, {when}: "{rule["text"]}"\n'
                f"`@Tulle Bot rules` lists them; `@Tulle Bot forget {rule['id']}` undoes this one.")
        if dropped:
            note += f' (Oldest rule {dropped["id"]} dropped to stay under {self.state.MAX_RULES}.)'
        log.info("rule %s added by %s target=%s: %s", rule["id"], ev.get("user"), target, rule["text"])
        if not fb.get("also_wants_reply"):
            self.post(channel, note, reply_ts, footer=False)
            return f"learn:rule-{rule['id']}"
        ask = (question + "\n(Their writing preference is already saved as a standing rule and the notifier will "
               "follow it. Don't discuss whether formats can change; just do what they asked, following the rules.)")
        out = self.answer(ask, ev, mode="mention", prefix=note + "\n\n",
                          extra_target="deploy_notes" if target != "bot_answers" else None)
        return f"learn:rule-{rule['id']}+{out}"

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

    # --- v2 capabilities: experiments engine, WeWeb publishes, on-demand duplicate scan --------------------------
    def capabilities(self, question: str, ev: dict) -> str | None:
        import engine as eng
        channel = ev["channel"]
        reply_ts = ev.get("thread_ts") or ev.get("ts")
        if re.search(r"\b(scan|check|find)\b.*\bduplicates?\b|\bdedupe\b", question, re.I):
            if ev.get("user") not in config.approvers():
                self.post(channel, "Only approvers can start a duplicate scan.", reply_ts, footer=False)
                return "dedupe:denied"
            if not config.dedupe_channel():
                self.post(channel, "The duplicate scan isn't switched on yet (DEDUPE_CHANNEL isn't set).", reply_ts,
                          footer=False)
                return "dedupe:no-channel"
            # 'scan duplicates 20' posts up to 20 cards (max 25); without a number it only counts (QA 2026-11-23)
            n = re.search(r"\bduplicates?\s+(\d{1,3})\b|\b(\d{1,3})\s+(?:more\s+)?duplicates?\b", question, re.I)
            n = (n.group(1) or n.group(2)) if n else None
            n = min(25, int(n)) if n else 0
            if n and config.dedupe_max_cards() == 0:
                self.post(channel, "Scan cards are switched off (DEDUPE_MAX_CARDS=0). Name the listings instead: `@Tulle Bot "
                                   "merge V1 and V2`.", reply_ts, footer=False)
                return "dedupe:off"
            import time as _t
            last = float(self.store.meta("dedupe_ondemand_at", 0) or 0)
            wait = config.ondemand_scan_cooldown_min() * 60 - (_t.time() - last)
            if wait > 0 and n:
                self.post(channel, f"A scan ran a few minutes ago; try again in {int(wait // 60) + 1} min.", reply_ts,
                          footer=False)
                return "dedupe:cooldown"
            if n:
                self.store.set_meta("dedupe_ondemand_at", _t.time())
            self.post(channel, f"Starting a duplicate scan; suggestions will land in <#{config.dedupe_channel()}>." if n else
                      "Counting possible duplicates (no cards will be posted)…", reply_ts, footer=False)
            from scheduler import run_dedupe
            # owner 2026-11-22: an on-demand scan looks at EVERY vendor (the nightly job's "only vendors added since
            # the last scan" mode found 0 and said nothing). 'scan new duplicates' keeps the nightly behaviour.
            mode = "nightly" if re.search(r"\b(new|recent|latest)\b", question, re.I) else "weekly"
            out = run_dedupe(self, mode=mode, max_cards=n)
            if ":count:" in out:
                self.post(channel, f"{out.rsplit(':', 1)[1]} possible duplicate groups don't have a card yet. Say `@Tulle Bot "
                                   "scan duplicates 10` to post 10 of them (max 25).", reply_ts, footer=False)
                return out
            if out.endswith(":0") or out.endswith(":busy"):     # never leave the asker with silence
                self.post(channel, "A scan is already running; its cards will appear when it finishes."
                          if out.endswith(":busy") else
                          "No new duplicate suggestions: every group the scan finds already has a card or was "
                          "rejected." if mode == "weekly" else
                          "No new duplicates among vendors added since the last scan. Say `@Tulle Bot scan "
                          "duplicates 20` to check every vendor.", reply_ts, footer=False)
            return out
        if eng.PUBLISH_Q.search(question) and re.search(r"\b(what|which|changed?|last|latest|recent)\b", question, re.I):
            deploy = self.slack.history(config.deploy_channel(), limit=100, days=14)
            text = eng.answer_publishes(self.engine, deploy)
            if text:
                self.post(channel, text, reply_ts, footer=False)
                return "engine:publishes"
            return None
        if eng.EXPERIMENT_Q.search(question):
            text = eng.answer_experiment(question, self.engine, lambda q: None)
            if text:
                self.post(channel, text, reply_ts, footer=False)
                return "engine:experiment"
        return None

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
        elif cmd == "rules":
            rs = self.state.rules()
            if not rs:
                self.post(channel, "No standing rules yet. Tell me how you'd like the #deployment notes or my answers "
                                   "written (e.g. `@Tulle Bot the staging notes are too long`) and I'll keep to it.",
                          reply_ts, footer=False)
            else:
                lines = [f'{r["id"]}. [{TARGET_LABEL.get(r.get("target"), r.get("target"))}] {r["text"]}' for r in rs]
                self.post(channel, "Standing rules I follow (from team feedback):\n" + "\n".join(lines)
                          + "\n`@Tulle Bot forget <number>` removes one.", reply_ts, footer=False)
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
    def answer(self, question: str, ev: dict, mode: str, prefix: str = "", extra_target: str | None = None) -> str:
        channel = ev["channel"]
        reply_ts = ev.get("thread_ts") or ev.get("ts")
        dynamic = self.know.build_dynamic(question, channel, ev.get("thread_ts"), self.slack, self.github,
                                          current_ts=ev.get("ts"))
        rules = {r["id"]: r for r in self.state.rules("bot_answers")}
        if extra_target:   # e.g. "rewrite that note simpler" -> also follow the deploy-note rules
            rules.update({r["id"]: r for r in self.state.rules(extra_target)})
        data, usd = self.claude.answer(self.know.static_context(), dynamic, question, mode,
                                       style_rules=format_rules(sorted(rules.values(), key=lambda r: r["id"])))
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
        body = prefix + cap_words(text)
        if sources:
            body += "\n_Sources: " + "; ".join(s.strip() for s in sources) + "_"
        self.post(channel, body, reply_ts)
        return f"{mode}:answered"

    def post(self, channel: str, text: str, thread_ts: str | None, footer: bool = True) -> dict:
        msg = scrub_output(text)
        msg = msg + "\n" + FOOTER if footer else "_Tulle Bot (automated):_ " + msg
        return self.slack.post_message(channel, msg, thread_ts=thread_ts)
