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
from datetime import datetime, timezone
from urllib.parse import parse_qs

import appliers
import cards
import config
from proposals import Store

log = logging.getLogger("slack_qa.interactions")
ACTIONS = (cards.ACTION_APPROVE, cards.ACTION_REJECT, cards.ACTION_UNDO, cards.ACTION_RECHECK, cards.ACTION_DETAILS,
           cards.ACTION_PDF_HIDE, cards.ACTION_PDF_KEEP, cards.ACTION_PDF_RECHECK, cards.ACTION_PRICE_RECHECK,
           cards.ACTION_SWAP, cards.ACTION_ALLOW)


def _age(iso) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(iso))).total_seconds()
    except (TypeError, ValueError):
        return 0.0


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
                 status_fns: dict | None = None, sweep: bool = True, xano=None):
        if slack is None:
            from slack_api import Slack
            slack = Slack()
        self.slack = slack
        self._xano = xano
        self.store = store or Store()
        self.apply_fns = apply_fns or appliers.APPLIERS
        self.status_fns = status_fns or _status_fns()
        if sweep:
            self.sweep()

    def sweep(self) -> list[str]:
        """Cards left applying / undoing by a restart become 'unknown' (Re-check). app.py runs this in the background at
        start-up and every minute, never on the request that has to answer Slack within 3 s."""
        hit = self.store.sweep_stale(config.stale_apply_min())
        for pid in hit:
            self._refresh(self.store.get(pid))
        return hit

    def handle_safe(self, payload: dict) -> str:
        try:
            out = self.handle(payload)
        except Exception:  # noqa: BLE001 - background task
            log.exception("interaction failed")
            out = "error"
            try:
                self._ephemeral((payload.get("channel") or {}).get("id"), (payload.get("user") or {}).get("id") or "",
                                "Something went wrong handling that click. Look at the card before trying again.",
                                (payload.get("message") or {}).get("thread_ts"))
            except Exception:  # noqa: BLE001
                log.exception("could not tell the approver")
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
        pid, _, flag_n = pid.partition("|")             # "<id>|<n>" only on the duplicate-PDF buttons
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
        if p.get("status") in ("applying", "undoing") and _age(p.get("status_at")) > config.stale_apply_min() * 60:
            self.sweep()                                          # a click always recovers a stuck card
            p = self.store.get(pid)
        if aid in (cards.ACTION_PDF_HIDE, cards.ACTION_PDF_KEEP):
            return self._pdf_choice(p, flag_n, "hide" if aid == cards.ACTION_PDF_HIDE else "keep", user, chan, thread)
        if aid == cards.ACTION_PDF_RECHECK:
            return self._pdf_recheck(p, user, chan, thread)
        if aid == cards.ACTION_PRICE_RECHECK:
            return self._price(p["id"], user, chan, thread) if p.get("kind") == "merge" else "ignored:price"
        if aid == cards.ACTION_ALLOW:
            p2, why = self.store.set_allow(p["id"], flag_n, user)
            if why:
                self._ephemeral(chan, user, why, thread)
                return "noop:allow"
            self._refresh(p2)
            return "allow:" + ("on" if p2["payload"]["allow"].get(flag_n) else "off")
        if aid == cards.ACTION_SWAP:
            return self._swap(p, flag_n, user, chan, thread)
        if aid == cards.ACTION_REJECT:
            return self._reject(p, user, chan, thread)
        if aid == cards.ACTION_UNDO:
            return self._undo(p, user, chan, thread)
        if aid == cards.ACTION_RECHECK:
            return self._recheck(p, user, chan, thread)
        return self._approve(p, user, chan, thread, act.get("block_id") or "")

    # --- duplicate PDFs --------------------------------------------------------------------------------------
    @property
    def xano(self):
        if self._xano is None:
            from xano_ro import ReadOnlyXano
            self._xano = ReadOnlyXano(cache_ttl=0)
        return self._xano

    def _pdf_choice(self, p, flag_n, choice, user, chan, thread) -> str:
        p2, why = self.store.set_pdf_choice(p["id"], int(flag_n) if flag_n.isdigit() else -1, choice, user)
        if why:
            self._ephemeral(chan, user, why, thread)
            return "noop:pdf"
        self._price(p["id"], user, chan, thread)        # hiding can change which PDF stays Primary, so the price too
        return f"pdf:{choice}"

    # --- price -------------------------------------------------------------------------------------------------
    def _price(self, pid, user, chan, thread) -> str:
        """Recompute the price and redraw. While it runs the card has no Approve button."""
        import merge_checks
        p = self.store.get(pid)
        p, why = self.store.set_price(pid, merge_checks.computing((p or {}).get("payload") or {}))
        if why:
            self._ephemeral(chan, user, why, thread)
            return "noop:price"
        self._refresh(p)
        pl = p["payload"]
        ans = merge_checks.price(self.xano, pl)
        ans["touching"] = merge_checks.touching(self.store, pid, pl)
        p, why = self.store.set_price(pid, ans, only_if=lambda now: merge_checks.current(dict(now, price=ans)))
        if why:                                 # another click changed the choices: its own recompute redraws the card
            return "price:superseded"
        self._refresh(p)
        return "price:" + ("alert" if ans.get("alert") else "ok")

    def _rules(self, pl) -> str:
        """The scan's rules again, on what the card shows AND on rows read live just now ("" = fine). A suggested pair (scan,
        'find duplicates', #feedback) must pass all of them; a pair someone named must pass the hard ones (state, place,
        hidden without an OK, gold)."""
        import dedupe
        import xano_search
        snaps = pl.get("snapshots") or {}
        vids = [str(v) for v in [pl.get("survivor")] + list(pl.get("losers") or []) if v]
        if not snaps:
            return ""                                  # appliers.merge refuses a card without its records
        try:
            rows = {str(r.get("Vendor_ID")): r for r in xano_search.search(self.xano, 11, "Vendor_ID", vids)}
        except Exception as e:  # noqa: BLE001
            log.warning("live listings not read: %s", type(e).__name__)
            return "I couldn't re-read the listings just now. Try again in a few minutes."
        if set(rows) != set(vids):
            return "one of these listings doesn't exist any more. Ask for a fresh card."
        refs = type("R", (), {"xano": self.xano, "pdf_names": {}})()
        for members in ([snaps[v] for v in vids if v in snaps], [rows[v] for v in vids]):
            why = dedupe.hard_conflict(members, vids[0], pl.get("allow") or {})
            if why:
                return why
            if pl.get("origin") != "named":
                try:
                    dedupe.check_suggestion(members, refs)
                except dedupe.Held as e:
                    return (f"this pair no longer passes the scan's checks ({e}). If they really are the same, ask for it by "
                            "name: that makes a card with the warnings on it.")
        return ""

    def _price_gate(self, p, user, chan, thread, block_id) -> str | None:
        """The approver must have seen the current price answer, and it must still be true right now."""
        import listing_compare
        import merge_checks
        pl = p.get("payload") or {}
        if not pl.get("snapshots"):
            return None
        if not merge_checks.current(pl):
            self._price(p["id"], user, chan, thread)
            self._ephemeral(chan, user, "I've worked out the price couples would see and added it to this card. Have a "
                                        "look, then press Approve again.", thread)
            return "price:shown-first"
        if not listing_compare.preview_state(pl):
            had = bool(((pl["price"].get("preview") or {}).get("current_state")))
            if not had:
                self._price(p["id"], user, chan, thread)          # try Xano's preview once more; the card is redrawn
            self._ephemeral(chan, user, "Nothing was merged: one of these listings changed while the card was being made. "
                                        "Ask for a fresh card." if had else
                            "Nothing was merged: I didn't have Xano's preview of this merge. I've asked for it again; have a "
                            "look at the card, then press Approve again.", thread)
            return "price:no-preview"
        wrong = listing_compare.primary_mismatch(pl)
        if wrong:
            self._ephemeral(chan, user, f"Nothing was merged: {wrong}", thread)
            return "primary:mismatch"
        hit = merge_checks.touching(self.store, p["id"], pl)
        if hit:
            self._ephemeral(chan, user, "Nothing was merged: " + "; ".join(hit) + ". Finish or undo that merge first.", thread)
            return "price:touching"
        old = pl["price"]
        shown = block_id.split(":")[3] if block_id.count(":") >= 3 else ""
        if block_id.startswith("tb:") and shown != str(old.get("stamp")):
            self._refresh(p)
            self._ephemeral(chan, user, "The price on this card was updated, so nothing happened. Check it and press "
                                        "Approve again.", thread)
            return "price:stale"
        from proposals import short_fingerprint
        if block_id.count(":") >= 4 and block_id.split(":")[4] != short_fingerprint(pl):
            self._refresh(p)
            self._ephemeral(chan, user, "This card was changed, so nothing happened. Check it and press Approve again.", thread)
            return "stale:changed"
        self._ephemeral(chan, user, "Checking the price once more, then merging (a few minutes; the card updates)…", thread)
        new = merge_checks.price(self.xano, pl, pv=old.get("preview"))
        new["touching"] = hit
        if not merge_checks.moved(old, new):
            return None
        p2, why = self.store.set_price(p["id"], new, only_if=lambda now: merge_checks.current(dict(now, price=new)))
        self._refresh(p2)
        self._ephemeral(chan, user, "Nothing was merged: the price couples would see changed since this card was shown. "
                                    "Check it and press Approve again.", thread)
        return "price:moved"

    # --- swap which listing is kept --------------------------------------------------------------------------------
    def _swap(self, p, n, user, chan, thread) -> str:
        import dedupe
        pl = p.get("payload") or {}
        losers = list(pl.get("losers") or [])
        if p.get("kind") != "merge" or p.get("status") != "pending" or not n.isdigit() or not 1 <= int(n) <= len(losers):
            self._ephemeral(chan, user, "That can't be swapped any more.", thread)
            return "noop:swap"
        keep, vids = losers[int(n) - 1], [pl.get("survivor")] + losers
        try:
            pr = dedupe.rebuild_for_swap(self.xano, vids, keep, pl, p.get("plain") or {})
        except dedupe.Held as e:                # the gold-chip listing can't be the one that's retired
            self._ephemeral(chan, user, f"I can't swap these: {e}", thread)
            return "swap:refused"
        except Exception as e:  # noqa: BLE001 - nothing has changed yet
            log.warning("swap failed: %s", type(e).__name__)
            self._ephemeral(chan, user, "I couldn't re-read the listings just now, so nothing changed. Try again in a "
                                        "few minutes.", thread)
            return "swap:failed"
        p2, why = self.store.replace_merge(p["id"], pr, user, pl.get("survivor"))
        if why:
            self._ephemeral(chan, user, why, thread)
            return "noop:swap"
        self._price(p["id"], user, chan, thread)
        return "swapped"

    def _pdf_recheck(self, p, user, chan, thread) -> str:
        import pdf_dupes
        pl = p.get("payload") or {}
        if p.get("kind") != "merge" or (pl.get("pdf_check") or {}).get("status") == "ok":
            return "noop:pdf-recheck"          # a finished check (and its choices) is never replaced by a click
        p2, why = self.store.set_pdf_check(p["id"], pdf_dupes.safe_check(self.xano, pl.get("survivor"),
                                                                         pl.get("losers") or []))
        if why:
            self._ephemeral(chan, user, why, thread)
            return "noop:pdf"
        if pl.get("snapshots"):
            self._price(p["id"], user, chan, thread)      # the new check may pre-select a PDF to hide
        else:
            self._refresh(p2)
        return "pdf:rechecked"

    def _pdf_gate(self, p, user, chan, thread, block_id) -> str | None:
        """Before a merge runs: the approver must have seen the PDF result and the current choices, and the PDFs to hide
        must still be as shown. None = go ahead."""
        if p.get("kind") != "merge" or p.get("status") not in ("pending", "dry_run"):
            return None                        # Resume: the choices were fixed when it was first approved
        import pdf_dupes
        from proposals import default_dry_run
        pl = p.get("payload") or {}
        live = not default_dry_run("merge") and (p.get("status") == "dry_run" or not p.get("dry_run", True))
        if not live:
            return None                        # a dry run hides nothing
        if pl.get("snapshots") and (pl.get("pdf_check") or {}).get("status") != "ok":
            # a card from before this check existed, or one whose check failed: the merge is never sent without the
            # PDF list the approver saw (the endpoint compares it, Amendment 2)
            self._pdf_recheck(p, user, chan, thread)
            now = ((self.store.get(p["id"]) or {}).get("payload") or {}).get("pdf_check") or {}
            self._ephemeral(chan, user, "I've run the duplicate-PDF check and added it to this card. Have a look, then "
                                        "press Approve again." if now.get("status") == "ok" else
                            "I still can't read the PDFs on these listings, so nothing was merged. Try again in a few "
                            "minutes.", thread)
            return "pdf:shown-first"
        shown = block_id.split(":")[2] if block_id.count(":") >= 2 else ""
        if block_id.startswith("tb:") and shown != pdf_dupes.version(pl):
            self._refresh(p)
            self._ephemeral(chan, user, "Someone changed an answer on this card, so nothing happened. Check it and "
                                        "press Approve again.", thread)
            return "pdf:stale"
        import listing_compare
        if listing_compare.gold_block(pl):
            self._ephemeral(chan, user, f"Nothing was merged: {listing_compare.gold_block(pl)}", thread)
            return "gold:refused"
        if listing_compare.allow_missing(pl):
            self._ephemeral(chan, user, "Nothing was merged: this card has a *Needs your OK* question that hasn't been "
                                        "answered.", thread)
            return "allow:missing"
        why = self._rules(pl)
        if why:
            self._ephemeral(chan, user, f"Nothing was merged: {why}", thread)
            return "rules:refused"
        why = pdf_dupes.recheck_live(self.xano, pl) if pdf_dupes._chosen(pl) else None
        if why:
            self._ephemeral(chan, user, f"Nothing was merged: {why}. Choose *Keep both* for those PDFs, or try again.",
                            thread)
            return "pdf:changed"
        return self._price_gate(p, user, chan, thread, block_id)

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

    def _approve(self, p, user, chan, thread, block_id: str = "") -> str:
        was = p.get("status")
        from proposals import fingerprint, is_void
        if is_void(p):
            p2, why = self.store.transition(p["id"], {"pending", "dry_run"}, "failed", user, result_text=(
                "This card was made before the current merge checks, so it can't be approved. Ask for a fresh card. Nothing "
                "was changed."))
            self._refresh(p2)
            self._ephemeral(chan, user, why or "That card is from before the current checks. Ask for a fresh one.", thread)
            return "void"
        expect = fingerprint(p.get("payload") or {}) if p.get("kind") == "merge" and was in ("pending", "dry_run") else None
        try:
            stop = self._pdf_gate(p, user, chan, thread, block_id)
        except Exception:  # noqa: BLE001 - a check that breaks must be visible, and nothing is sent
            log.exception("approve checks failed")
            self._ephemeral(chan, user, "Could not check this merge, so nothing was sent. Try again in a few minutes; if it "
                                        "keeps happening, tell the owner.", thread)
            return "gate:error"
        if stop:
            return stop
        from proposals import default_dry_run
        if was == "dry_run" and p.get("kind") == "merge":
            # owner 2026-11-22: a card approved while dry run was on can be applied for real once dry run is off
            # ("Apply for real" button). While dry run is still on there is nothing more to do.
            if default_dry_run(p.get("kind", "")):
                self._ephemeral(chan, user, "This was approved while changes are in dry run. It can be applied for "
                                            "real once dry run is switched off.", thread)
                return "noop:dry_run"
            p, why = self.store.transition(p["id"], {"dry_run"}, "applying", user, expect=expect, dry_run=False)
        elif was == "dry_run":                 # only merges re-read live data at apply time (QA 11-22)
            self._ephemeral(chan, user, "This was approved during the dry run. Ask again to make a fresh card.", thread)
            return "noop:dry_run"
        else:
            p, why = self.store.transition(p["id"], {"pending", "partial"}, "applying", user, expect=expect)
        if why == self.store.CHANGED:
            self._refresh(p)
            self._ephemeral(chan, user, "Nothing was sent: someone changed this card while your click was being checked. Look "
                                        "at it again, then press Approve.", thread)
            return "stale:changed"
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
        again = was == "undone" and bool((p.get("result") or {}).get("warnings"))      # ep578 v3: undo may be run again
        p, why = self.store.transition(p["id"], {"undone"} if again else {"applied", "partial"}, "undoing", user)
        if why:
            self._ephemeral(chan, user, why, thread)
            return "noop:" + (p or {}).get("status", "missing")
        res = self._run(self.apply_fns["merge"], p, user, mode="undo")
        if res.get("unknown"):
            p, _ = self.store.transition(p["id"], {"undoing"}, "unknown", user, result_text=res["text"],
                                         unknown_from="undoing")
        elif res.get("ok") and not res.get("dry_run"):
            p, _ = self.store.transition(p["id"], {"undoing"}, "undone", user, result_text=res["text"],
                                         result=res.get("result") or {})
        else:                              # refused / dry run: back to where it was (applied or partial)
            p, _ = self.store.transition(p["id"], {"undoing"}, was if was in ("applied", "partial", "undone") else "applied",
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
        if state == "none" and p.get("kind") == "merge" and not was_undo and _age(p.get("decided_at")) < \
                config.merge_recheck_grace():
            # the endpoint plans for up to a minute before it records anything, so "none" this soon proves nothing
            st = {"text": "No merge is recorded yet, but the request may still be starting. Press Re-check in a few "
                          "minutes."}
            state = "unknown"
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
        extra = {"result": st["result"]} if "result" in st else {}
        p, _ = self.store.transition(p["id"], {"unknown"}, to, user, result_text=st.get("text", ""),
                                     rechecked_by=user, **extra)
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


def post_proposal(slack, store: Store, channel: str, p: dict, thread_ts: str | None = None, xano=None) -> dict:
    """Post a freshly created proposal's card and remember where it lives. A card that fails to post becomes
    post_failed: never shown, so it neither blocks a later re-proposal nor counts against a daily cap."""
    if p is None:
        return {"ok": False, "error": "duplicate"}
    pl = p.get("payload") or {}
    price_later = p.get("kind") == "merge" and bool(pl.get("snapshots")) and not pl.get("price")
    if price_later:                    # the card goes up without Approve; the price (and ep578's slow preview) follow
        import merge_checks
        p = store.set_price(p["id"], merge_checks.computing(pl))[0] or p
    text, blocks = cards.render(p)
    problems = cards.validate(blocks)
    res = {"ok": False, "error": "invalid_blocks"} if problems else \
        slack.post_blocks(channel, text, blocks, thread_ts=thread_ts)
    if problems:
        log.error("card %s invalid: %s", p["id"], problems)
    if res.get("ok"):
        store.set_message(p["id"], res.get("channel") or channel, res.get("ts"))
        if price_later:
            merge_checks.submit(lambda: merge_checks.finish(slack, store, p["id"], xano))
    else:
        store.transition(p["id"], {"pending"}, "post_failed", "bot", result_text=str(res.get("error")))
    return res
