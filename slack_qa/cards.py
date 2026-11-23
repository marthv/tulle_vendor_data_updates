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
# duplicate-PDF choices on a merge card: value = "<proposal id>|<flag number>"; what it hides is read from the store
ACTION_PDF_HIDE, ACTION_PDF_KEEP, ACTION_PDF_RECHECK = "tb_pdf_hide", "tb_pdf_keep", "tb_pdf_recheck"
ACTION_ALLOW = "tb_allow"                                      # value = "<proposal id>|hidden / unhide / gold"
ACTION_PRICE_RECHECK, ACTION_SWAP = "tb_price_recheck", "tb_swap"          # swap value = "<proposal id>|<duplicate n>"


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


APPLY_CONFIRM = {"title": {"type": "plain_text", "text": "Merge for real?"},
                 "text": {"type": "mrkdwn", "text": "This was approved during the dry run. Merging now moves the "
                                                    "duplicate's PDFs, pricing and saved lists onto the kept listing "
                                                    "and hides the duplicate (undo stays on the card). Xano re-reads "
                                                    "both listings first and refuses if they changed a lot."},
                 "confirm": {"type": "plain_text", "text": "Merge"}, "deny": {"type": "plain_text", "text": "Cancel"}}
UNDO_CONFIRM = {"title": {"type": "plain_text", "text": "Undo this merge?"},
                "text": {"type": "mrkdwn", "text": "Moves everything back from the backup taken before the merge. Anything "
                                                   "someone changed since (a deleted package, an edited field) stays as "
                                                   "it is and is listed as a conflict."},
                "confirm": {"type": "plain_text", "text": "Undo"}, "deny": {"type": "plain_text", "text": "Cancel"}}


def confirm_dialog(text: str, base: dict | None = None) -> dict:
    """A Slack confirm dialog that asks `text` first (Block Kit limit: 300 characters), then `base`'s own text."""
    text = f"{text} {base['text']['text']}" if base else text
    return {"title": base["title"] if base else {"type": "plain_text", "text": "Are you sure?"},
            "text": {"type": "mrkdwn", "text": clip(text, 300)},
            "confirm": base["confirm"] if base else {"type": "plain_text", "text": "Yes, approve"},
            "deny": {"type": "plain_text", "text": "Cancel"}}


def _pdf_blocks(p: dict, status: str) -> list[dict]:
    """The duplicate-PDF result: always on a merge card that has one (so it's seen before Approve), with one choice per
    flag while the merge hasn't run and just the outcome afterwards."""
    import pdf_dupes
    pl = p.get("payload") or {}
    c = pl.get("pdf_check")
    if c is None:
        return []
    out = [_section(pdf_dupes.summary_line(c))]
    for f in c.get("flags") or []:
        out.append(_section(pdf_dupes.flag_text(f, pl.get("survivor") or "")))
        if status in ("pending", "dry_run"):
            val = f"{p['id']}|{f['n']}"
            out.append({"type": "actions", "block_id": f"tbpdf:{p['id']}:{f['n']}", "elements": [
                _btn(ACTION_PDF_KEEP, val, "Keep both") if f.get("choice") == "hide"
                else _btn(ACTION_PDF_HIDE, val, "Hide the duplicate")]})
    return out


def _allow_blocks(p: dict, status: str) -> list[dict]:
    """Things the endpoint refuses unless a person says yes on this card (hidden duplicate, unhiding, gold chip)."""
    if status not in ("pending", "dry_run"):
        return []
    import listing_compare
    pl = p.get("payload") or {}
    have, out = pl.get("allow") or {}, []
    for key, text, label in listing_compare.allow_needed(pl):
        on = bool(have.get(key))
        out.append(_section(f":hand: *Needs your OK:* {text}\n*Answer: {'yes, OK' if on else 'not answered (the merge is refused)'}*"))
        out.append({"type": "actions", "block_id": f"tballow:{p['id']}:{key}", "elements": [
            _btn(ACTION_ALLOW, f"{p['id']}|{key}", "Take back my OK" if on else label)]})
    return out


def render(p: dict, extra_blocks: list[dict] | None = None, confirm_text: str | None = None) -> tuple[str, list[dict]]:
    """(fallback text, blocks) for a proposal dict from proposals.Store. A merge with `plain` text gets the short card
    (3 lines + buttons, no ids or table names); the full record is behind the Details button.

    Extension point (merge cards only): `extra_blocks` are shown just above the buttons and `confirm_text` makes every
    button that runs the merge ask first. Pass them here, or store them as payload["extra_blocks"] /
    payload["confirm_text"] so every re-draw of the card keeps them (an argument wins over the stored value)."""
    kind = p.get("kind", "task")
    payload = p.get("payload") or {}
    price = (payload.get("price") or {}) if kind == "merge" else {}
    if kind == "merge":
        extra_blocks = (list(price.get("extra_blocks") or []) + list(payload.get("extra_blocks") or [])
                        if extra_blocks is None else extra_blocks)
        confirm_text = (price.get("confirm_text") or payload.get("confirm_text")) if confirm_text is None else confirm_text
    else:
        extra_blocks, confirm_text = [], None
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
        blocks: list[dict] = [_section(plain["headline"])]
        if kind == "merge" and status0 in ("pending", "dry_run"):    # side by side + mismatch banner (QA 2026-11-22)
            import listing_compare
            blocks += listing_compare.blocks(payload)
        blocks += [_section(plain["why"]), _section(happens)]
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
    if kind == "merge":
        if price.get("touching") and status in ("pending", "dry_run"):
            blocks.append(_section(":no_entry: *Xano will refuse this one:* " + esc("; ".join(price["touching"])) + ". A listing "
                                   "can only be in one merge at a time. To add another duplicate, undo the earlier merge and "
                                   "merge them all in one go (up to 3 duplicates)."))
        blocks += _allow_blocks(p, status) + _pdf_blocks(p, status) + extra_blocks
    ask = confirm_dialog(confirm_text) if confirm_text else None
    buttons = []
    waiting = price.get("status") == "computing"           # no Approve until the price answer is back
    if status == "pending":
        if dry_run_for(p):
            blocks.append(_context(":test_tube: Dry run is on: approving records the decision and shows what would "
                                   "happen. Nothing is written until dry run is switched off."))
        buttons = [_btn(ACTION_APPROVE, p["id"], "Approve and apply", "primary", confirm=ask),
                   _btn(ACTION_REJECT, p["id"], "Reject", "danger")][1 if waiting else 0:]
        losers = list(payload.get("losers") or [])
        if kind == "merge" and payload.get("snapshots") and not waiting:
            buttons += [_btn(ACTION_SWAP, f"{p['id']}|{n}", "Keep the duplicate instead" if len(losers) == 1 else
                             f"Keep duplicate {n} instead") for n in range(1, min(len(losers), 3) + 1)]
    else:
        blocks.append(_context(outcome_line(p)))
        if status == "unknown":
            buttons.append(_btn(ACTION_RECHECK, p["id"], "Re-check"))
        if status == "dry_run" and kind == "merge":
            # dry run is off now: a merge approved during the dry run can run (merges re-read the live rows at
            # apply time; other kinds don't, so they get a fresh card instead - QA 11-22)
            from proposals import default_dry_run
            if not default_dry_run(kind) and not waiting:
                buttons.append(_btn(ACTION_APPROVE, p["id"], "Apply for real", "primary",
                                    confirm=confirm_dialog(confirm_text, APPLY_CONFIRM) if ask else APPLY_CONFIRM))
        if status == "partial" and kind == "merge":
            buttons.append(_btn(ACTION_APPROVE, p["id"], "Resume merge", "primary", confirm=ask))
        if status == "undone" and kind == "merge" and (p.get("result") or {}).get("warnings"):
            buttons.append(_btn(ACTION_UNDO, p["id"], "Undo again", confirm=UNDO_CONFIRM))     # ep578 v3 allows a re-run
        if status in ("applied", "partial") and kind == "merge" and p.get("undoable", True):
            buttons.append(_btn(ACTION_UNDO, p["id"], "Undo merge", confirm=UNDO_CONFIRM))
    pdf = (payload.get("pdf_check") or {}) if kind == "merge" else {}
    if pdf.get("status") == "unavailable" and status in ("pending", "dry_run"):
        buttons.append(_btn(ACTION_PDF_RECHECK, p["id"], "Run PDF check again"))
    if price and (waiting or price.get("cant")) and status in ("pending", "dry_run"):
        buttons.append(_btn(ACTION_PRICE_RECHECK, p["id"], "Check price again"))
    if kind == "merge" and payload.get("snapshots"):
        buttons.append(_btn(ACTION_DETAILS, p["id"], "Details"))
    if buttons:
        bid = f"tb:{p['id']}"
        if pdf.get("flags") or price or payload.get("allow"):      # what this card shows: a stale click is refused
            import pdf_dupes
            bid += ":" + pdf_dupes.version(payload) + (":" + str(price.get("stamp")) if price else "")
        blocks.append({"type": "actions", "block_id": bid, "elements": buttons})
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
    if st == "applied" and ((p.get("result") or {}).get("warnings")
                            or (p.get("result") or {}).get("status") == "applied_with_warnings"):
        return f":warning: Merged, but something needs a look. {res} Approved by {who}."
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
        mark = ":warning: Merge mostly undone" if (p.get("result") or {}).get("warnings") else \
            ":leftwards_arrow_with_hook: Merge undone"
        return f"{mark} by {undo} (first approved by {who}). {res}"
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
        if t == "section" and "fields" in b and (not 1 <= len(b["fields"]) <= 10
                                                 or any(len(f["text"]) > 2000 for f in b["fields"])):
            errs.append(f"block {i}: section fields")
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
