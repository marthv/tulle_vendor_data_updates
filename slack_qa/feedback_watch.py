"""#feedback surveillance: when a feedback post lands, propose ONE concrete fix in its thread as an approve/reject
card (same UX as dedupe). Off unless FEEDBACK_CHANNEL is set and FEEDBACK_WATCH=1.

The posts are written by Tulle Bot's own incoming webhook (rec_service/notify.py, Xano $env.slack_webhook_url_feedback),
so they arrive as bot messages; bot.py routes top-level messages in FEEDBACK_CHANNEL here before its usual
"ignore bots" rule. Our own approval cards (which carry blocks with tb_ action ids) are never re-read.

Every suggestion is checked against live data (read-only) before it is shown:
  vendor_edit -> field must be in the fn62 whitelist, vendor must resolve to exactly one table-11 row, the new value
                 must differ from the live value; the card shows live -> proposed.
  duplicate   -> both vendors must resolve; becomes a normal merge proposal (dedupe.build_proposal).
  task        -> a GitHub issue (dry run by default).
"Venue not in Tulle" posts (the Planner's own webhook) skip the model entirely (trends.py, no spend): not a venue
name -> nothing; already listed under another spelling -> a one-line note in the thread; really missing -> ONE
add-vendor card per vendor (a task, dry run by default), later requests for it -> "asked again" note with the count.
"""
from __future__ import annotations

import logging
import re
import threading
import time

import config
import dedupe
from cards import esc
from interactions import post_proposal
from llm import EDITABLE_FIELDS
from scrub import scrub_output

log = logging.getLogger("slack_qa.feedback")
VID = re.compile(r"\bV\d{1,6}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
MIN_CONF = 0.7
STRICT_CONF = 0.85
STRICT_FIELDS = {   # a wrong value here sends couples to someone else's site / inbox
    "Website": re.compile(r"(https?://)?(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(/[^\s<>]*)?", re.I),
    "Contact_Information": re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+|\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}"),
}


def _squash(t: str) -> str:
    return " ".join((t or "").lower().split())


def in_post(fragment: str, post: str) -> bool:
    """Case- and whitespace-insensitive substring check: the model may only quote the post, never paraphrase it."""
    return bool(fragment.strip()) and _squash(fragment) in _squash(post)


class VendorDirectory:
    """Table 11 rows, read-only, cached for hours (it changes slowly; a scan reads it anyway)."""

    def __init__(self, xano, ttl: float = 6 * 3600):
        self.xano, self.ttl = xano, ttl
        self._rows, self._at = None, 0.0
        self._lock = threading.Lock()

    def fresh(self) -> bool:
        """True when the cached list is loaded and within ttl (a request will be instant)."""
        return self._rows is not None and time.time() - self._at <= self.ttl

    def rows(self) -> list[dict]:
        with self._lock:
            if self._rows is None or time.time() - self._at > self.ttl:
                rows, _ = self.xano.rows(11)
                self._rows, self._at = [r for r in rows if not r.get("merged_into")], time.time()
            return self._rows

    def resolve(self, vendor_id: str = "", name: str = "", location: str = "") -> list[dict]:
        rows = self.rows()
        if vendor_id and VID.fullmatch(vendor_id.strip()):
            return [r for r in rows if str(r.get("Vendor_ID")) == vendor_id.strip()]
        n = dedupe.norm(name)
        if not n:
            return []
        hits = [r for r in rows if dedupe.norm(r.get("Name")) == n]
        if len(hits) <= 1 or not location:
            return hits
        loc = location.lower()
        narrowed = [r for r in hits if loc in str(r.get("Address") or "").lower()
                    or loc in str(r.get("State") or "").lower()]
        return narrowed or hits


def is_watchable(ev: dict, bot_uid: str | None = None) -> bool:
    """A top-level post in #feedback that isn't something this bot posted itself. Incoming-webhook posts (how
    feedback arrives) have subtype bot_message and no `user`; the bot's own chat.postMessage posts (approval cards,
    roll-ups, dedupe headers) carry user = the bot's user id, so they are skipped. People typing feedback by hand
    are watched too; other apps' messages (bot_id with a different user) are not."""
    if not config.feedback_channel() or ev.get("channel") != config.feedback_channel():
        return False
    if ev.get("type") != "message":
        return False
    if bot_uid and (ev.get("user") == bot_uid or f"<@{bot_uid}" in (ev.get("text") or "")):
        return False
    if ev.get("bot_id") and ev.get("subtype") != "bot_message":
        return False
    ut = ev.get("user_team") or ev.get("team")
    if ut and config.team_id() and ut != config.team_id():
        return False
    if ev.get("thread_ts") and ev.get("thread_ts") != ev.get("ts"):
        return False
    if ev.get("subtype") not in (None, "bot_message"):
        return False
    for b in ev.get("blocks") or []:
        if b.get("type") == "actions" and str(b.get("block_id", "")).startswith("tb:"):
            return False
    return bool(post_text(ev).strip())


def post_text(ev: dict) -> str:
    text = ev.get("text") or ""
    if not text:
        for att in ev.get("attachments") or []:
            text = att.get("text") or att.get("fallback") or ""
            if text:
                break
    return text


class FeedbackWatcher:
    def __init__(self, slack, claude, state, store, xano, directory: VendorDirectory | None = None):
        self.slack, self.claude, self.state, self.store, self.xano = slack, claude, state, store, xano
        self.directory = directory or VendorDirectory(xano)

    def handle(self, ev: dict) -> str:
        if not config.feedback_watch():
            return "feedback:watch-off"
        if config.feedback_venue_requests():
            import trends
            vr = trends.parse_venue_request(post_text(ev))
            if vr is not None:
                return self._venue_request(vr, ev)
        if self.state.over_budget():
            return "feedback:over-budget"
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if float(self.store.meta(f"fb_usd:{today}", 0) or 0) >= config.feedback_daily_usd():
            return "feedback:own-budget"            # its own slice, so #feedback can't starve Q&A
        used = self._used_today(today) + int(self.store.meta(f"fb_dup_attempts:{today}", 0) or 0)
        if used >= config.feedback_daily_cap():
            return "feedback:daily-cap"
        key = f"fb:{ev['channel']}:{ev['ts']}"
        if self.store.by_key(key) or self.store.meta(f"fb_seen:{key}"):
            return "feedback:already-proposed"
        self.store.set_meta(f"fb_seen:{key}", today)  # one look per post, even when nothing is proposed
        text = post_text(ev)
        fix, usd = self.claude.propose_fix(text)
        self.state.add_spend(usd)
        self.store.incr_meta(f"fb_usd:{today}", usd)
        kind, conf = fix.get("kind", "none"), float(fix.get("confidence") or 0)
        if kind == "none" or conf < MIN_CONF:
            return f"feedback:no-fix:{kind}:{conf:.2f}"
        raw_evidence = (fix.get("evidence") or "").strip()
        if not raw_evidence or not in_post(raw_evidence, text):
            return f"feedback:{kind}:evidence-not-in-post"
        evidence = esc(EMAIL.sub("[email]", raw_evidence)[:300])
        if kind == "vendor_edit":
            pr = self._vendor_edit(fix, evidence, text, conf)
        elif kind == "duplicate":
            self.store.incr_meta(f"fb_dup_attempts:{today}")
            pr = self._duplicate(fix, evidence)
        else:
            pr = self._task(fix, evidence, raw_evidence, ev)
        if isinstance(pr, str):
            return pr
        p = self.store.create(pr["kind"], pr["title"], pr["what"], pr["why"], pr["next_steps"], pr["payload"],
                              touches=pr.get("touches", ""), key=key if pr["kind"] != "merge" else pr["key"],
                              source=key, plain=pr.get("plain"))
        if p is None:
            return "feedback:already-proposed"
        res = post_proposal(self.slack, self.store, ev["channel"], p, thread_ts=ev["ts"])
        return f"feedback:proposed:{pr['kind']}" + ("" if res.get("ok") else f":post-failed:{res.get('error')}")

    def _used_today(self, today: str) -> int:
        """Cards proposed from #feedback today plus the bot's thread notes ("already listed", "asked again"):
        both count toward FEEDBACK_DAILY_CAP."""
        return self.store.count_source_since("fb:", today) + int(self.store.meta(f"fb_replies:{today}", 0) or 0)

    def _reply(self, ev: dict, text: str, today: str) -> bool:
        res = self.slack.post_message(ev["channel"], text, thread_ts=ev["ts"], sanitize=True)
        if res.get("ok"):
            self.store.incr_meta(f"fb_replies:{today}")
        return bool(res.get("ok"))

    # --- "Venue not in Tulle" ---------------------------------------------------------------------------
    def _venue_request(self, vr: dict, ev: dict) -> str:
        import hashlib

        import reports
        import trends
        today = time.strftime("%Y-%m-%d", time.gmtime())
        seen = f"fb_seen:fb:{ev['channel']}:{ev['ts']}"
        if self.store.meta(seen):
            return "feedback:venue-request:seen"
        self.store.set_meta(seen, today)
        if reports.is_internal(vr["user_id"], vr["email"]):
            return "feedback:venue-request:internal"
        if trends.not_a_venue(vr["name"]):
            return "feedback:venue-request:not-a-venue"
        try:
            rows = self.directory.rows()
        except Exception:  # noqa: BLE001 - no vendor list = no duplicate check = no card
            log.exception("venue request: vendor read failed")
            return "feedback:venue-request:no-directory"
        key = trends.request_key(vr["name"], vr["loc"])
        if key == "vreq:":
            return "feedback:venue-request:no-name"
        confirmed, possible, closest = trends.listed_match(vr["name"], vr["loc"], rows)
        if confirmed:
            if self._used_today(today) >= config.feedback_daily_cap():
                return "feedback:daily-cap"
            r = confirmed[0]
            where = trends.place_label(r)
            more = f" (and {len(confirmed) - 1} more with that name there)" if len(confirmed) > 1 else ""
            hidden = "; it's hidden from search right now" if str(r.get("Validated_Data")) != "1" else ""
            self._reply(ev, (f"We already list *{esc(r.get('Name'))}*{f' ({esc(where)})' if where else ''}{more}"
                             f"{hidden}. The couple typed it differently, so there's nothing to add."), today)
            return "feedback:venue-request:listed"
        if vr["user_id"]:
            who = str(vr["user_id"])
        else:                                       # never store the address itself
            who = "e" + hashlib.sha256(vr["email"].lower().encode()).hexdigest()[:10] if vr["email"] else "anon"
        stat = dict(self.store.meta(key) or {"n": 0, "couples": []})
        stat["n"] = int(stat.get("n") or 0) + 1
        stat["couples"] = list(dict.fromkeys(list(stat.get("couples") or []) + [who]))[-200:]
        self.store.set_meta(key, stat)
        n, m = stat["n"], len(stat["couples"])
        prior = self.store.by_key(key, active_only=True)
        if prior:
            p = prior[-1]
            if p.get("status") == "rejected":
                return "feedback:venue-request:rejected-before"
            if self._used_today(today) >= config.feedback_daily_cap():
                return "feedback:daily-cap"            # still counted above, just not announced
            link = (f"<https://slack.com/archives/{p['channel']}/p{str(p['ts']).replace('.', '')}|here>"
                    if p.get("channel") and p.get("ts") else "in an earlier thread")
            self._reply(ev, (f"Asked again: {n} requests from {m} couple{'s' if m != 1 else ''} so far. The add-vendor "
                             f"suggestion is {link}."), today)
            return "feedback:venue-request:repeat"
        if self._used_today(today) >= config.feedback_daily_cap():
            return "feedback:daily-cap"
        name = esc(trends.clean_request(vr["name"]))
        loc = esc(vr["loc"][:60])
        near = ", ".join(esc(f"{r.get('Name')}" + (f" ({trends.place_label(r)})" if trends.place_label(r) else ""))
                         for r in closest) or "none"
        times = "once" if n == 1 else f"{n} times"
        maybe = ""
        if possible:
            r = possible[0]
            pl = trends.place_label(r)
            maybe = (f" Possible match we already list: {esc(r.get('Name'))}{f' ({esc(pl)})' if pl else ''}; the "
                     "request doesn't say where, or says somewhere else, so check before approving.")
        p = self.store.create(
            "task", f"Add vendor: {name}"[:140],
            f"Add *{name}*{f' ({loc})' if loc else ''} to the requested-vendors list so the PDF team sources it.",
            f"A couple asked the Planner for it and we don't list it (asked {times} so far). Closest names we have: "
            f"{near}.{maybe}",
            "Approve to file the sourcing task (labelled from-feedback). Reject if it isn't a wedding vendor or we "
            "shouldn't carry it; I won't suggest it again, and later requests are only counted.",
            {"title": f"Source pricing: {trends.clean_request(vr['name'])}"
                      + (f" ({vr['loc'][:60]})" if vr["loc"] else ""),
             "body": "\n".join([
                 "Couples asked the Tulle Planner for this vendor and it isn't in table 11.",
                 f"Requested as: {trends.clean_request(vr['name'])}" + (f" / {vr['loc'][:60]}" if vr["loc"] else ""),
                 f"Requests when proposed: {n} ({m} couple{'s' if m != 1 else ''})",
                 f"Closest names we have: {', '.join(str(r.get('Name')) for r in closest) or 'none'}",
                 f"Slack: https://slack.com/archives/{ev['channel']}/p{str(ev['ts']).replace('.', '')}"])},
            key=key, source=f"fb:{ev['channel']}:{ev['ts']}")
        if p is None:
            return "feedback:venue-request:already-proposed"
        res = post_proposal(self.slack, self.store, ev["channel"], p, thread_ts=ev["ts"])
        return "feedback:venue-request:proposed" + ("" if res.get("ok") else f":post-failed:{res.get('error')}")

    # --- kinds -------------------------------------------------------------------------------------------
    def _vendor_edit(self, fix: dict, evidence: str, post: str, conf: float):
        field = next((f for f in EDITABLE_FIELDS if f.lower() == (fix.get("field") or "").strip().lower()), None)
        if not field:
            return "feedback:vendor_edit:field-not-editable"
        new = (fix.get("new_value") or "").strip()
        if not new or not evidence:
            return "feedback:vendor_edit:no-value-or-evidence"
        if not in_post(new, post):                 # the value must be written in the post, not made up
            return "feedback:vendor_edit:value-not-in-post"
        if len(new) > 1000:                        # the card must show the whole value the approver is approving
            return "feedback:vendor_edit:value-too-long"
        from sanitize import has_email
        if has_email(new):                         # emails never leave the bot or reach its store (QA round 4)
            return f"feedback:vendor_edit:{field}-email-not-supported"
        if field in STRICT_FIELDS:                 # where a wrong value sends couples somewhere else
            if conf < STRICT_CONF or not STRICT_FIELDS[field].fullmatch(new):
                return f"feedback:vendor_edit:{field}-not-convincing"
        hits = self.directory.resolve(fix.get("vendor_id", ""), fix.get("vendor_name", ""),
                                      fix.get("vendor_location", ""))
        if len(hits) != 1:
            return f"feedback:vendor_edit:resolve-{len(hits)}"
        v = hits[0]
        old = "" if v.get(field) is None else str(v.get(field))
        if old.strip() == new:
            return "feedback:vendor_edit:already-correct"
        vid = v.get("Vendor_ID")
        name, old_s, new_s = esc(v.get("Name")), esc(old[:1000]), esc(new)
        return {"kind": "vendor_edit", "title": f"{name} ({vid}): {field}",
                "what": f"Change *{field}* on *{name}* ({vid}) from “{old_s or '(empty)'}” to “{new_s}”.",
                "why": f"An anonymous feedback form post says: “{evidence}” (unverified: check the vendor's own "
                       "site before approving).",
                "next_steps": ("Approve to update the listing (it goes through the staged-edit flow, which refuses if "
                               "the value changed in the meantime and keeps the old value for rollback). Reject if "
                               "the feedback is wrong."),
                "payload": {"vendor_id": vid, "field": field, "old_value": old, "new_value": new,
                            "note": f"from #feedback: {evidence[:200]}"}}

    def _duplicate(self, fix: dict, evidence: str):
        a = self.directory.resolve(fix.get("vendor_id", ""), fix.get("vendor_name", ""), fix.get("vendor_location", ""))
        b = self.directory.resolve(fix.get("other_vendor_id", ""), fix.get("other_vendor_name", ""),
                                   fix.get("vendor_location", ""))
        if len(a) != 1 or len(b) != 1 or a[0]["id"] == b[0]["id"]:
            return "feedback:duplicate:unresolved"
        va, vb = str(a[0].get("Vendor_ID") or "").strip(), str(b[0].get("Vendor_ID") or "").strip()
        if not va or not vb or va == vb:
            return "feedback:duplicate:bad-vendor-ids"
        if str(a[0].get("Category") or "").lower() != str(b[0].get("Category") or "").lower():
            return "feedback:duplicate:different-categories"
        if dedupe.group_key([va, vb]) in self.store.open_or_rejected_keys("merge"):   # before any Xano reads
            return "feedback:duplicate:already-proposed"
        byid = {a[0]["id"]: a[0], b[0]["id"]: b[0]}
        refs = dedupe.RefIndex(self.xano, max_pages=config.feedback_page_budget(),
                               page_delay=config.dedupe_page_delay(), only={10, 36})
        refs.build({va, vb}, rowids={int(a[0]["id"]): va, int(b[0]["id"]): vb})
        pr = dedupe.build_proposal({"ids": sorted(byid, key=int), "rules": []}, byid, refs,
                                   reported=f"{evidence} (anonymous feedback form post)")
        pr["kind"] = "merge"
        return pr

    def _task(self, fix: dict, evidence: str, raw_evidence: str, ev: dict):
        raw_title = scrub_output(EMAIL.sub("[email]", (fix.get("title") or "").strip()))[:120]
        raw_body = scrub_output(EMAIL.sub("[email]", (fix.get("body") or "").strip()))[:3000]
        if not raw_title:
            return "feedback:task:no-title"
        link = f"https://slack.com/archives/{ev['channel']}/p{str(ev['ts']).replace('.', '')}"
        title, body = esc(raw_title), esc(raw_body)            # escaped for Slack only
        return {"kind": "task", "title": title,
                "what": f"Open a GitHub issue: *{title}*",
                "why": (body + (f"\nFrom an anonymous feedback form post: “{evidence}”" if evidence else ""))[:2500],
                "next_steps": "Approve to file it in the tracker (linked back to this thread). Reject if it's not worth "
                              "a task.",
                # GitHub gets plain text (appliers.defang wraps @handles in backticks at filing time)
                "payload": {"title": raw_title,
                            "body": f"{raw_body}\n\nEvidence (anonymous feedback form post): "
                                    f"{EMAIL.sub('[email]', raw_evidence)[:300]}\nSlack: {link}"}}
