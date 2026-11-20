"""Block Kit "Proposed change" cards (approve / reject), modelled on the Healthie RevOps intake bot.

Layout:  Proposed change · <kind> <title>
         WHAT it will do (plain English)
         WHY (evidence)            <- long text; Slack clients collapse it behind "Show more" on their own
         WHAT IT TOUCHES (optional, e.g. the tables a merge repoints)
         NEXT STEPS
         [dry-run note]
         [Approve and apply] [Reject]          (pending)  |  ✅ Applied ... Approved by @x   (decided)
         Proposed by Tulle Bot. Only Vivek, Des can approve.

Buttons carry ONLY the proposal id. What an approval does is read from the server-side proposal store, so a
forged button value can never change the action (interactions.py re-checks the approver list too).
"""
from __future__ import annotations

import config

MAX_BLOCKS = 50
MAX_SECTION = 3000
MAX_HEADER = 150
MAX_BUTTON = 75
MAX_FALLBACK = 3000

KIND_LABEL = {"merge": "Merge duplicate vendors", "vendor_edit": "Vendor data fix", "task": "New task"}
ACTION_APPROVE, ACTION_REJECT, ACTION_UNDO = "tb_approve", "tb_reject", "tb_undo"


def esc(text) -> str:
    """Neutralise Slack control sequences in text that came from data or a couple's post (names, PDF text, LLM
    output): no <!channel>/<!here> pings, no <@U...> mentions, no disguised <url|label> links."""
    return str(text if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 2].rstrip() + " …"


def approver_names() -> str:
    names = config.user_names()
    out = [names.get(u, f"<@{u}>") for u in config.approvers()]
    if len(out) <= 1:
        return "".join(out)
    return ", ".join(out[:-1]) + " and " + out[-1]


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": clip(text, MAX_SECTION)}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": clip(text, MAX_SECTION)}]}


def dry_run_for(kind: str) -> bool:
    return {"merge": config.merge_dry_run, "vendor_edit": config.vendor_edit_dry_run,
            "task": config.task_dry_run}.get(kind, lambda: True)()


def render(p: dict) -> tuple[str, list[dict]]:
    """(fallback text, blocks) for a proposal dict from proposals.Store."""
    kind = p.get("kind", "task")
    head = f"*Proposed change* · {KIND_LABEL.get(kind, kind)} · {p.get('title', '')}"
    blocks: list[dict] = [_section(clip(head, 600)), _section(p.get("what") or "")]
    if p.get("why"):
        blocks.append(_section("*Why:* " + p["why"]))
    if p.get("touches"):
        blocks.append(_section("*What it touches:*\n" + p["touches"]))
    if p.get("next_steps"):
        blocks.append(_section("*Next steps:* " + p["next_steps"]))
    status = p.get("status", "pending")
    if status == "pending":
        if dry_run_for(kind):
            blocks.append(_context(":test_tube: Dry run is on: approving records the decision and shows what would "
                                   "happen. Nothing is written until dry run is switched off."))
        blocks.append({"type": "actions", "block_id": f"tb:{p['id']}", "elements": [
            {"type": "button", "action_id": ACTION_APPROVE, "value": p["id"], "style": "primary",
             "text": {"type": "plain_text", "text": "Approve and apply"}},
            {"type": "button", "action_id": ACTION_REJECT, "value": p["id"], "style": "danger",
             "text": {"type": "plain_text", "text": "Reject"}},
        ]})
    else:
        blocks.append(_context(outcome_line(p)))
        if status == "applied" and kind == "merge" and p.get("undoable", True):
            blocks.append({"type": "actions", "block_id": f"tb:{p['id']}", "elements": [
                {"type": "button", "action_id": ACTION_UNDO, "value": p["id"],
                 "text": {"type": "plain_text", "text": "Undo merge"},
                 "confirm": {"title": {"type": "plain_text", "text": "Undo this merge?"},
                             "text": {"type": "mrkdwn", "text": "Restores every repointed row from the backup "
                                                                 "taken before the merge."},
                             "confirm": {"type": "plain_text", "text": "Undo"},
                             "deny": {"type": "plain_text", "text": "Cancel"}}}]})
    blocks.append(_context(f"Proposed by Tulle Bot. Only {approver_names()} can approve."))
    fallback = clip(f"Proposed change: {KIND_LABEL.get(kind, kind)} - {p.get('title', '')}", MAX_FALLBACK)
    return fallback, blocks


def outcome_line(p: dict) -> str:
    who = f"<@{p['decided_by']}>" if p.get("decided_by") else "someone"
    res = p.get("result_text") or ""
    st = p.get("status")
    if st == "applied":
        return f":white_check_mark: Applied. {res} Approved by {who}."
    if st == "dry_run":
        return f":test_tube: Approved by {who} (dry run, nothing written). {res}"
    if st == "rejected":
        return f":x: Rejected by {who}." + (f" {res}" if res else "")
    if st == "applying":
        return f":hourglass_flowing_sand: Applying… approved by {who}."
    if st == "undone":
        undo = f"<@{p['undo_by']}>" if p.get("undo_by") else who
        return f":leftwards_arrow_with_hook: Merge undone by {undo} (first approved by {who}). {res}"
    if st == "failed":
        return f":warning: Approved by {who} but it didn't apply: {res} Nothing was changed."
    return st or ""


def validate(blocks: list[dict]) -> list[str]:
    """Block Kit limits we rely on. Returns a list of problems (empty = fine)."""
    errs = []
    if len(blocks) > MAX_BLOCKS:
        errs.append(f"{len(blocks)} blocks > {MAX_BLOCKS}")
    for i, b in enumerate(blocks):
        t = b.get("type")
        if t not in ("section", "context", "actions", "header", "divider"):
            errs.append(f"block {i}: type {t}")
        if t == "section" and len(b["text"]["text"]) > MAX_SECTION:
            errs.append(f"block {i}: section text > {MAX_SECTION}")
        if t == "header" and len(b["text"]["text"]) > MAX_HEADER:
            errs.append(f"block {i}: header > {MAX_HEADER}")
        if t == "context":
            if not 1 <= len(b["elements"]) <= 10:
                errs.append(f"block {i}: context elements")
            for e in b["elements"]:
                if len(e.get("text", "")) > MAX_SECTION:
                    errs.append(f"block {i}: context text > {MAX_SECTION}")
        if t == "actions":
            if not 1 <= len(b["elements"]) <= 25:
                errs.append(f"block {i}: actions elements")
            for e in b["elements"]:
                if len(e["text"]["text"]) > MAX_BUTTON:
                    errs.append(f"block {i}: button text > {MAX_BUTTON}")
                if len(e.get("value", "")) > 2000 or len(e.get("action_id", "")) > 255:
                    errs.append(f"block {i}: value/action_id too long")
                if e.get("style") not in (None, "primary", "danger"):
                    errs.append(f"block {i}: style")
        if len(b.get("block_id", "")) > 255:
            errs.append(f"block {i}: block_id too long")
    return errs
