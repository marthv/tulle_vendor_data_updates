"""Slack interactivity (button clicks on proposal cards).

app.py verifies the Slack signature and acks within 3 s; handle() runs in the background:
  1. only tb_approve / tb_reject / tb_undo / tb_recheck from this workspace are considered,
  2. the clicker must be in QA_APPROVERS (else an ephemeral "only approvers can apply this", nothing changes),
  3. a compare-and-set on the stored status makes double clicks and Slack retries no-ops,
  4. the card is updated to "Applying…", the applier runs (dry run if the proposal was created under dry run), the
     card shows the outcome + who approved. A timeout / 5xx is "unknown": the card says so and offers Re-check.
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
ACTIONS = (cards.ACTION_APPROVE, cards.ACTION_REJECT, cards.ACTION_UNDO, cards.ACTION_RECHECK, cards.ACTION_DETAILS)


def parse_body(body: bytes) -> dict | None:
    try:
        form = parse_qs(body.decode("utf-8"), strict_parsing=False)
        raw = (form.get("payload") or [""])[0]
        return json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        return None


def _status_fns():
    return {"merge": lambda p: appliers.merge_status(p["payload"], p["id"]),
            "vendor_edit": lambda p: appliers.vendor_edit_status(p["payload"]),
            "task": lambda p: appliers.task_status(p["payload"], p["id"]),
            "visibility": lambda p: appliers.visibility_status(p["payload"])}


class Interactions:
    def __init__(self, slack=None, store: Store | None = None, apply_fns: dict | None = None,
                 status_fns: dict | None = None, sweep: bool = True):
        if slack is None:
            from slack_api import Slack
            slack = Slack()
        self.slack = slack
        self.store = store or Store()
        self.apply_fns = apply_fns or appliers.APPLIERS
        self.status_fns = status_fns or _status_fns()
        if sweep:
            for pid in self.store.sweep_stale(config.stale_apply_min()):
                self._refresh(self.store.get(pid))

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
        if aid not in ACTIONS:
            return f"ignored:action:{aid}"
        user = (payload.get("user") or {}).get("id") or ""
        chan = (payload.get("channel") or {}).get("id") or (payload.get("container") or {}).get("channel_id")
        msg_ts = (payload.get("message") or {}).get("ts") or (payload.get("container") or {}).get("message_ts")
        thread = (payload.get("message") or {}).get("thread_ts")
        if aid == cards.ACTION_DETAILS:                 # read-only: anyone in the workspace may open it
            return self._details(pid, user, chan, msg_ts, thread)
        if user not in config.approvers():
            self._ephemeral(chan, user, "Only approvers can apply or reject Tulle Bot changes, so nothing happened.",
                            thread)
            return "denied:not-approver"
        p = self.store.get(pid)
        if not p:
            self._ephemeral(chan, user, "I can't find that proposal any more.", thread)
            return "missing"
        if p.get("ts") and msg_ts and p.get("ts") != msg_ts:     # a card's id can't be replayed from another message
            return "ignored:message-mismatch"
        if aid == cards.ACTION_REJECT:
            return self._reject(p, user, chan, thread)
        if aid == cards.ACTION_UNDO:
            return self._undo(p, user, chan, thread)
        if aid == cards.ACTION_RECHECK:
            return self._recheck(p, user, chan, thread)
        return self._approve(p, user, chan, thread)

    # --- actions -------------------------------------------------------------------------------------------
    def _reject(self, p, user, chan, thread) -> str:
        p, why = self.store.transition(p["id"], {"pending"}, "rejected", user)
        if why:
            self._ephemeral(chan, user, why, thread)
            return "noop:" + (p or {}).get("status", "missing")
        self._refresh(p)
        return "rejected"

    def _run(self, fn, p, user, **kw) -> dict:
        try:
            return fn(p["payload"], user, p["id"], dry_run=p.get("dry_run", True), **kw)
        except Exception as e:  # noqa: BLE001 - an exception mid-call: we can't know whether it landed
            log.exception("applier %s failed", p["kind"])
            return {"ok": False, "dry_run": False, "unknown": True,
                    "text": f"Something broke mid-way ({type(e).__name__}), so I can't tell whether it applied."}

    def _approve(self, p, user, chan, thread) -> str:
        was = p.get("status")
        from proposals import default_dry_run
        if was == "dry_run":
            # owner 2026-11-22: a card approved while dry run was on can be applied for real once dry run is off
            # ("Apply for real" button). While dry run is still on there is nothing more to do.
            if default_dry_run(p.get("kind", "")):
                self._ephemeral(chan, user, "This was approved while changes are in dry run. It can be applied for "
                                            "real once dry run is switched off.", thread)
                return "noop:dry_run"
            p, why = self.store.transition(p["id"], {"dry_run"}, "applying", user, dry_run=False)
        else:
            p, why = self.store.transition(p["id"], {"pending", "partial"}, "applying", user)
        if why:
            self._ephemeral(chan, user, why, thread)
            return "noop:" + (p or {}).get("status", "missing")
        self._refresh(p)
        fn = self.apply_fns.get(p["kind"])
        res = self._run(fn, p, user) if fn else {"ok": False, "text": f"no applier for {p['kind']}"}
        if res.get("partial"):
            to = "partial"
        elif res.get("unknown"):
            to = "unknown"
        elif not res.get("ok"):
            to = "failed"
        else:
            to = "dry_run" if res.get("dry_run") else "applied"
        if was == "partial" and to in ("failed", "dry_run"):
            to = "partial"                 # a failed / dry-run Resume leaves the half-done merge resumable + undoable
        p, _ = self.store.transition(p["id"], {"applying"}, to, user, result_text=res.get("text", ""),
                                     result=res.get("result") or {}, unknown_from="applying" if to == "unknown" else None)
        self._refresh(p)
        return f"approved:{to}"

    def _undo(self, p, user, chan, thread) -> str:
        if p.get("kind") != "merge":
            return "ignored:undo-not-merge"
        was = p.get("status")
        p, why = self.store.transition(p["id"], {"applied", "partial"}, "undoing", user)
        if why:
            self._ephemeral(chan, user, why, thread)
            return "noop:" + (p or {}).get("status", "missing")
        res = self._run(self.apply_fns["merge"], p, user, mode="undo")
        if res.get("unknown"):
            p, _ = self.store.transition(p["id"], {"undoing"}, "unknown", user, result_text=res["text"],
                                         unknown_from="undoing")
        elif res.get("ok") and not res.get("dry_run"):
            p, _ = self.store.transition(p["id"], {"undoing"}, "undone", user, result_text=res["text"])
        else:                              # refused / dry run: back to where it was (applied or partial)
            p, _ = self.store.transition(p["id"], {"undoing"}, was if was in ("applied", "partial") else "applied",
                                         user, result_text=(p.get("result_text") or "") + f" (Undo: {res['text']})")
        self._refresh(p)
        return "undo:" + ("unknown" if res.get("unknown") else "ok" if res.get("ok") else "failed")

    def _recheck(self, p, user, chan, thread) -> str:
        if p.get("status") != "unknown":
            self._ephemeral(chan, user, f"That one is {p.get('status')}, nothing to re-check.", thread)
            return "noop:" + str(p.get("status"))
        fn = self.status_fns.get(p["kind"])
        try:
            st = fn(p) if fn else {"state": "unknown", "text": "no status check for this kind"}
        except Exception as e:  # noqa: BLE001
            st = {"state": "unknown", "text": f"The check failed ({type(e).__name__})."}
        was_undo = p.get("unknown_from") == "undoing"
        state = st.get("state")
        if state == "applied":
            to = "applied"
        elif state == "partial":
            to = "partial"
        elif state == "undone":
            to = "undone"
        elif state == "none":
            to = "applied" if was_undo else "pending"     # a confirmed no-op undo leaves it applied; else re-approvable
        else:
            to = "unknown"
        p, _ = self.store.transition(p["id"], {"unknown"}, to, user, result_text=st.get("text", ""),
                                     rechecked_by=user)
        self._refresh(p)
        return f"recheck:{to}"

    def _details(self, pid, user, chan, msg_ts, thread) -> str:
        p = self.store.get(pid)
        if not p or p.get("kind") != "merge":
            return "ignored:details"
        if p.get("ts") and msg_ts and p.get("ts") != msg_ts:
            return "ignored:message-mismatch"
        if not self.store.claim_details(pid):          # under the store lock: two quick clicks post once
            self._ephemeral(chan, user, "The details are in this card's thread.", thread)
            return "details:already"
        text, blocks = cards.render_details(p)
        res = self.slack.post_blocks(p.get("channel") or chan, text, blocks, thread_ts=thread or p.get("ts"))
        self.store.finish_details(pid, res.get("ts") if res.get("ok") else None)
        return "details:posted" if res.get("ok") else f"details:failed:{res.get('error')}"

    def _ephemeral(self, chan, user, text, thread):
        if chan:
            self.slack.post_ephemeral(chan, user, text, thread_ts=thread)

    def _refresh(self, p: dict | None) -> None:
        if not p or not p.get("channel") or not p.get("ts"):
            return
        text, blocks = cards.render(p)
        self.slack.update_message(p["channel"], p["ts"], text, blocks)


def post_proposal(slack, store: Store, channel: str, p: dict, thread_ts: str | None = None) -> dict:
    """Post a freshly created proposal's card and remember where it lives. A card that fails to post becomes
    post_failed: never shown, so it neither blocks a later re-proposal nor counts against a daily cap."""
    if p is None:
        return {"ok": False, "error": "duplicate"}
    text, blocks = cards.render(p)
    problems = cards.validate(blocks)
    res = {"ok": False, "error": "invalid_blocks"} if problems else \
        slack.post_blocks(channel, text, blocks, thread_ts=thread_ts)
    if problems:
        log.error("card %s invalid: %s", p["id"], problems)
    if res.get("ok"):
        store.set_message(p["id"], res.get("channel") or channel, res.get("ts"))
    else:
        store.transition(p["id"], {"pending"}, "post_failed", "bot", result_text=str(res.get("error")))
    return res
