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
        if self.state.over_budget():
            return "feedback:over-budget"
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if float(self.store.meta(f"fb_usd:{today}", 0) or 0) >= config.feedback_daily_usd():
            return "feedback:own-budget"            # its own slice, so #feedback can't starve Q&A
        used = self.store.count_source_since("fb:", today) + int(self.store.meta(f"fb_dup_attempts:{today}", 0) or 0)
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
                              source=key)
        if p is None:
            return "feedback:already-proposed"
        res = post_proposal(self.slack, self.store, ev["channel"], p, thread_ts=ev["ts"])
        return f"feedback:proposed:{pr['kind']}" + ("" if res.get("ok") else f":post-failed:{res.get('error')}")

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
        refs.build({va, vb})
        pr = dedupe.build_proposal({"ids": sorted(byid, key=int), "rules": []}, byid, refs)
        pr["why"] = f"An anonymous feedback form post says: “{evidence}”\n" + pr["why"].split("\n", 1)[-1]
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
