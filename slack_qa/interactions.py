"""Slack interactivity (button clicks on proposal cards).

app.py verifies the Slack signature and acks within 3 s; handle() runs in the background:
  1. only tb_approve / tb_reject / tb_undo from this workspace are considered,
  2. the clicker must be in QA_APPROVERS (else an ephemeral "only X can approve", nothing changes),
  3. a compare-and-set on the stored status makes double clicks and Slack retries no-ops,
  4. the card is updated to "Applying…", the applier runs, the card shows the outcome + who approved.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs

import appliers
import cards
import config
from proposals import Store

log = logging.getLogger("slack_qa.interactions")


def parse_body(body: bytes) -> dict | None:
    try:
        form = parse_qs(body.decode("utf-8"), strict_parsing=False)
        raw = (form.get("payload") or [""])[0]
        return json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        return None


class Interactions:
    def __init__(self, slack=None, store: Store | None = None, apply_fns: dict | None = None):
        if slack is None:
            from slack_api import Slack
            slack = Slack()
        self.slack = slack
        self.store = store or Store()
        self.apply_fns = apply_fns or appliers.APPLIERS

    def handle_safe(self, payload: dict) -> str:
        try:
            out = self.handle(payload)
        except Exception:  # noqa: BLE001 - background task
            log.exception("interaction failed")
            out = "error"
        log.info("interaction -> %s", out)
        return out

    def handle(self, payload: dict) -> str:
        if payload.get("type") != "block_actions":
            return f"ignored:type:{payload.get('type')}"
        team = (payload.get("team") or {}).get("id") or (payload.get("user") or {}).get("team_id")
        if team and config.team_id() and team != config.team_id():
            return "ignored:external-team"
        acts = payload.get("actions") or []
        if not acts:
            return "ignored:no-action"
        act = acts[0]
        aid, pid = act.get("action_id"), act.get("value") or ""
        if aid not in (cards.ACTION_APPROVE, cards.ACTION_REJECT, cards.ACTION_UNDO):
            return f"ignored:action:{aid}"
        user = (payload.get("user") or {}).get("id") or ""
        chan = (payload.get("channel") or {}).get("id") or (payload.get("container") or {}).get("channel_id")
        msg_ts = (payload.get("message") or {}).get("ts") or (payload.get("container") or {}).get("message_ts")
        thread = (payload.get("message") or {}).get("thread_ts")
        if user not in config.approvers():
            if chan:
                self.slack.post_ephemeral(chan, user, f"Only {cards.approver_names()} can approve or reject Tulle "
                                                      "Bot changes, so nothing happened.", thread_ts=thread)
            return "denied:not-approver"
        p = self.store.get(pid)
        if not p:
            if chan:
                self.slack.post_ephemeral(chan, user, "I can't find that proposal any more.", thread_ts=thread)
            return "missing"
        # The card the click came from must be the card we posted for this proposal (no replaying an id elsewhere).
        if p.get("ts") and msg_ts and p.get("ts") != msg_ts:
            return "ignored:message-mismatch"

        if aid == cards.ACTION_REJECT:
            p, why = self.store.transition(pid, {"pending"}, "rejected", user)
            if why:
                self._ephemeral(chan, user, why, thread)
                return "noop:" + (p or {}).get("status", "missing")
            self._refresh(p)
            return "rejected"

        if aid == cards.ACTION_UNDO:
            p, why = self.store.transition(pid, {"applied"}, "undoing", user)
            if why:
                self._ephemeral(chan, user, why, thread)
                return "noop:" + (p or {}).get("status", "missing")
            res = self.apply_fns["merge"](p["payload"], user, pid, mode="undo")
            if res.get("ok") and not res.get("dry_run"):
                p, _ = self.store.transition(pid, {"undoing"}, "undone", user, result_text=res["text"])
            else:
                p, _ = self.store.transition(pid, {"undoing"}, "applied", user,
                                             result_text=(p.get("result_text") or "") + f" (Undo: {res['text']})")
            self._refresh(p)
            return "undo:" + ("ok" if res.get("ok") else "failed")

        # approve
        p, why = self.store.transition(pid, {"pending"}, "applying", user)
        if why:
            self._ephemeral(chan, user, why, thread)
            return "noop:" + (p or {}).get("status", "missing")
        self._refresh(p)
        fn = self.apply_fns.get(p["kind"])
        if fn is None:
            res = {"ok": False, "dry_run": False, "text": f"no applier for {p['kind']}"}
        else:
            try:
                res = fn(p["payload"], user, pid)
            except Exception as e:  # noqa: BLE001
                log.exception("applier %s failed", p["kind"])
                res = {"ok": False, "dry_run": False, "text": f"error ({type(e).__name__})"}
        to = "failed" if not res.get("ok") else ("dry_run" if res.get("dry_run") else "applied")
        p, _ = self.store.transition(pid, {"applying"}, to, user, result_text=res.get("text", ""),
                                     result=res.get("result") or {})
        self._refresh(p)
        return f"approved:{to}"

    def _ephemeral(self, chan, user, text, thread):
        if chan:
            self.slack.post_ephemeral(chan, user, text, thread_ts=thread)

    def _refresh(self, p: dict | None) -> None:
        if not p or not p.get("channel") or not p.get("ts"):
            return
        text, blocks = cards.render(p)
        self.slack.update_message(p["channel"], p["ts"], text, blocks)


def post_proposal(slack, store: Store, channel: str, p: dict, thread_ts: str | None = None) -> dict:
    """Post a freshly created proposal's card and remember where it lives."""
    text, blocks = cards.render(p)
    problems = cards.validate(blocks)
    if problems:
        log.error("card %s invalid: %s", p["id"], problems)
        return {"ok": False, "error": "invalid_blocks"}
    res = slack.post_blocks(channel, text, blocks, thread_ts=thread_ts)
    if res.get("ok"):
        store.set_message(p["id"], res.get("channel") or channel, res.get("ts"))
    return res
