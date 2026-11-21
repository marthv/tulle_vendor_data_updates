"""Block Kit "Proposed change" cards (approve / reject), modelled on the Healthie RevOps intake bot.

Layout:  Proposed change · <kind> <title>
         WHAT it will do (plain English)
         WHY (evidence)            <- long text; Slack clients collapse it behind "Show more" on their own
         WHAT IT TOUCHES (optional, e.g. the tables a merge repoints)
         NEXT STEPS
         [dry-run note]
         [Approve and apply] [Reject]          (pending)  |  ✅ Applied ... Approved by @x   (decided)
         Proposed by Tulle Bot. Only approvers can apply this.

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

FOOTER = "Proposed by Tulle Bot. Only approvers can apply this."
KIND_LABEL = {"merge": "Merge duplicate vendors", "vendor_edit": "Vendor data fix", "task": "New task",
              "visibility": "Hide or show a vendor"}
ACTION_APPROVE, ACTION_REJECT, ACTION_UNDO, ACTION_RECHECK = "tb_approve", "tb_reject", "tb_undo", "tb_recheck"
ACTION_DETAILS = "tb_details"


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


def dry_run_for(p: dict) -> bool:
    """A card is dry-run if it was created under dry run (fixed at creation) or the flag is on now."""
    from proposals import default_dry_run
    return bool(p.get("dry_run", True)) or default_dry_run(p.get("kind", ""))


def _btn(action: str, pid: str, text: str, style: str | None = None, confirm: dict | None = None) -> dict:
    b = {"type": "button", "action_id": action, "value": pid, "text": {"type": "plain_text", "text": text}}
    if style:
        b["style"] = style
    if confirm:
        b["confirm"] = confirm
    return b


UNDO_CONFIRM = {"title": {"type": "plain_text", "text": "Undo this merge?"},
                "text": {"type": "mrkdwn", "text": "Moves everything back from the backup taken before the merge. Anything "
                                                   "someone changed since (a deleted package, an edited field) stays as "
                                                   "it is and is listed as a conflict."},
                "confirm": {"type": "plain_text", "text": "Undo"}, "deny": {"type": "plain_text", "text": "Cancel"}}


def render(p: dict) -> tuple[str, list[dict]]:
    """(fallback text, blocks) for a proposal dict from proposals.Store. A merge with `plain` text gets the short card
    (3 lines + buttons, no ids or table names); the full record is behind the Details button."""
    kind = p.get("kind", "task")
    plain = p.get("plain")
    if plain:
        happens = plain["happens"]
        status0 = p.get("status", "pending")
        can_undo = ((status0 == "pending" and not dry_run_for(p)) or
                    (status0 in ("applied", "partial") and p.get("undoable", True)))
        if plain.get("hidden") and status0 in ("undone", "undoing", "rejected", "post_failed"):
            pass                                    # undone / never applied: the duplicate is not hidden
        elif plain.get("hidden"):                   # merges hide the duplicate (owner exception 2026-11-20)
            happens += " " + plain["hidden"] + (", and you can undo this from the card." if can_undo else ".")
        elif can_undo:                              # a card proposed before 2026-11-20
            happens += " You can undo this from the card."            # only when an undo is actually possible
        blocks: list[dict] = [_section(plain["headline"]), _section(plain["why"]), _section(happens)]
    else:
        head = f"*Proposed change* · {KIND_LABEL.get(kind, kind)} · {p.get('title', '')}"
        blocks = [_section(clip(head, 600)), _section(p.get("what") or "")]
        if p.get("why"):
            blocks.append(_section("*Why:* " + p["why"]))
        if p.get("touches"):
            blocks.append(_section("*What it touches:*" + chr(10) + p["touches"]))
        if p.get("next_steps"):
            blocks.append(_section("*Next steps:* " + p["next_steps"]))
    status = p.get("status", "pending")
    buttons = []
    if status == "pending":
        if dry_run_for(p):
            blocks.append(_context(":test_tube: Dry run is on: approving records the decision and shows what would "
                                   "happen. Nothing is written until dry run is switched off."))
        buttons = [_btn(ACTION_APPROVE, p["id"], "Approve and apply", "primary"),
                   _btn(ACTION_REJECT, p["id"], "Reject", "danger")]
    else:
        blocks.append(_context(outcome_line(p)))
        if status == "unknown":
            buttons.append(_btn(ACTION_RECHECK, p["id"], "Re-check"))
        if status == "partial" and kind == "merge":
            buttons.append(_btn(ACTION_APPROVE, p["id"], "Resume merge", "primary"))
        if status in ("applied", "partial") and kind == "merge" and p.get("undoable", True):
            buttons.append(_btn(ACTION_UNDO, p["id"], "Undo merge", confirm=UNDO_CONFIRM))
    if kind == "merge" and (p.get("payload") or {}).get("snapshots"):
        buttons.append(_btn(ACTION_DETAILS, p["id"], "Details"))
    if buttons:
        blocks.append({"type": "actions", "block_id": f"tb:{p['id']}", "elements": buttons})
    blocks.append(_context(FOOTER))             # owner 2026-11-20: never name people on the card
    label = plain["headline"].replace("*", "") if plain else f"{KIND_LABEL.get(kind, kind)} - {p.get('title', '')}"
    fallback = clip(f"Proposed change: {label}", MAX_FALLBACK)
    return fallback, blocks


def render_details(p: dict) -> tuple[str, list[dict]]:
    """The Details thread reply: the full technical record (both rows field by field, every row move, counter
    rules before -> after, name-keyed fields)."""
    import dedupe
    chunks = dedupe.details_text(p)
    blocks = [_section(c) for c in chunks][: MAX_BLOCKS - 1]
    blocks.append(_context("Full record, including every row id and both complete snapshots, is kept in the "
                           "proposal store and in the merge backup table, so undo is exact."))
    return clip(f"Technical details: {p.get('title', '')}", MAX_FALLBACK), blocks


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
        return f":warning: Approved by {who} but it didn't apply. {res}"
    if st == "unknown":
        return (f":warning: Approved by {who}, but I couldn't confirm the outcome. {res} Press *Re-check* before "
                "trying anything else.")
    if st == "partial":
        return (f":warning: Approved by {who}; the merge stopped part-way. {res} *Resume merge* finishes it, "
                "*Undo merge* puts back what moved.")
    if st == "post_failed":
        return "Card could not be posted."
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
