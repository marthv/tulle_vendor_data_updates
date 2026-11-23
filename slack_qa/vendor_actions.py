"""Plain-language vendor actions on @-mention (owner 2026-11-21: Tulle Bot takes over what the retired Tulle Ops app
did, in plain words instead of slash commands). Every action is a PROPOSAL card; nothing changes until an approver
(QA_APPROVERS) presses Approve.

  @Tulle Bot hide V5699 venue asked to be removed   -> visibility card (Approve -> Xano ep279, vendor hidden)
  @Tulle Bot unhide V5699                           -> visibility card (Approve -> vendor visible again)
  @Tulle Bot change V13831 website to example.com   -> vendor_edit card (existing staged-edit flow, ep280/281)
  @Tulle Bot what's waiting for approval?           -> list of open cards (read-only)
  @Tulle Bot what was applied this week?            -> list of applied / rejected cards, last 7 days (read-only)

Deterministic parsing only (no model call), so these never spend from the daily budget and never guess a vendor:
a vendor ID (V123) is required."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from cards import esc
from llm import EDITABLE_FIELDS
from sanitize import has_email

POLITE = r"^\s*(?P<polite>(?:(?:can|could|would)\s+you\s+)?(?:please\s+|pls\s+)?)"
HIDE = re.compile(POLITE + r"(hide|unhide|restore|show)\s+(?:the\s+)?(?:vendor\s+|listing\s+|venue\s+)?"
                  r"(V\d{1,6})\b(.*)$", re.I | re.S)
EDIT = re.compile(POLITE + r"(?:change|set|update|fix|correct)\s+(?:the\s+)?(V\d{1,6})(?:'s|’s)?\s+"
                  r"(.+?)\s+(?:to|=|as)\s+(?:be\s+)?(.+?)\s*$", re.I | re.S)
PENDING_Q = re.compile(r"\b(waiting|pending|awaiting)\s+(for\s+)?approvals?\b|\b(open|pending)\s+(cards|approvals)\b"
                       r"|^\s*what('?s| is)\s+(pending|waiting)\s*\??\s*$", re.I)
VID_ANY = re.compile(r"\bV\d{1,6}\b", re.I)
APPLIED_Q = re.compile(r"\b(applied|approved)\b.{0,40}\b(changes?|cards?|fixes|edits|hides?)\b"
                       r"|\bwhat (was|got|has been|have been) (applied|approved)\b", re.I)
SLACK_LINK = re.compile(r"<((?:https?://|mailto:)[^|>]+)(?:\|([^>]+))?>")

# Plain words people use -> the canonical column (only fields Tulle Bot may edit; Xano re-checks on its side).
FIELD_ALIASES = {
    "name": "Name", "vendor name": "Name", "venue name": "Name",
    "website": "Website", "site": "Website", "url": "Website", "web site": "Website",
    "description": "Description", "blurb": "Description", "about": "Description", "bio": "Description",
    "contact": "Contact_Information", "contact info": "Contact_Information",
    "contact information": "Contact_Information", "phone": "Contact_Information", "phone number": "Contact_Information",
    "capacity": "Max_Capacity_Seated", "seated capacity": "Max_Capacity_Seated", "max capacity": "Max_Capacity_Seated",
    "max seated capacity": "Max_Capacity_Seated", "guest capacity": "Max_Capacity_Seated",
    "venue type": "Venue_Type", "type of venue": "Venue_Type",
    "photography type": "Type_of_Photography", "type of photography": "Type_of_Photography",
    "entertainment type": "Type_of_Entertainment", "type of entertainment": "Type_of_Entertainment",
    "beauty type": "Type_of_Beauty", "type of beauty": "Type_of_Beauty",
}
FIELD_LIST = "name, website, description, contact (phone), seated capacity, venue type, photography / entertainment / " \
             "beauty type"
CACHE_NOTE = "Search can keep showing the old result for up to about 2 hours (search cache)."


def unslack(text: str) -> str:
    """Slack turns typed URLs into <https://x|x> and escapes & < > as entities; give back what the person typed.
    Other Slack markup (<!channel>, <@U..>, <tel:..>) is left as-is and refused by build_edit (QA 11-21)."""
    t = SLACK_LINK.sub(lambda m: m.group(2) or m.group(1), text or "")
    t = re.sub(r"<tel:[^|>]+\|([^>]+)>", r"\1", t)
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def resolve_field(words: str) -> str | None:
    w = " ".join(re.sub(r"[_*`\"'“”]", " ", words or "").lower().split())
    w = re.sub(r"^(the|its|their)\s+", "", w)
    if w in FIELD_ALIASES:
        return FIELD_ALIASES[w]
    return next((f for f in EDITABLE_FIELDS if f.lower() == w.replace(" ", "_")), None)


def parse(question: str) -> dict | None:
    """{"kind": "visibility"|"vendor_edit"|"list_pending"|"list_applied", ...} or None (not a vendor action)."""
    q = unslack(question)
    m = HIDE.match(q)
    if m:
        verb, vid, rest = m.group(2).lower(), m.group(3).upper(), m.group(4).strip()
        if m.group("polite"):                # "can you hide V12?" is a request, not a question
            rest = rest.rstrip("?").strip()
        # QA 11-21: questions and partial hides are not requests to hide the whole listing.
        if "?" in rest or re.match(r"^('|’)s\b", rest) or VID_ANY.search(rest):
            return None
        if verb != "hide" and rest:          # "show V123 pricing", "restore V12 pricing PDF": not "unhide"
            return None
        return {"kind": "visibility", "action": "hide" if verb == "hide" else "unhide", "vendor_id": vid,
                "reason": rest.lstrip(",.:;- ").strip()[:300]}
    m = EDIT.match(q)
    if m:
        new = m.group(4).strip()
        if m.group("polite"):
            new = new.rstrip("?").strip()
        return {"kind": "vendor_edit", "vendor_id": m.group(2).upper(), "field_words": m.group(3).strip(),
                "field": resolve_field(m.group(3)), "new_value": new.strip("\"'“”").strip()}
    if PENDING_Q.search(q):
        return {"kind": "list_pending"}
    if APPLIED_Q.search(q):
        return {"kind": "list_applied"}
    return None


def _link(p: dict) -> str:
    if p.get("channel") and p.get("ts"):
        # the title is already escaped when the card is built; escaping again shows &amp;amp; (QA 11-21)
        return f"<https://slack.com/archives/{p['channel']}/p{str(p['ts']).replace('.', '')}|{p.get('title', '')}>"
    return p.get("title", "")


OPEN = ("pending", "applying", "unknown", "partial", "undoing")


def open_card(store, kind: str, vendor_id: str, field: str | None = None) -> dict | None:
    """An open card for the same vendor (and field, for edits). For hide/unhide either direction counts, so opposite
    cards can't both be waiting. Rejected cards don't block a new request (QA 11-21)."""
    for p in store.all():
        pay = p.get("payload") or {}
        if p.get("kind") == kind and p.get("status") in OPEN and pay.get("vendor_id") == vendor_id \
                and (field is None or pay.get("field") == field):
            return p
    return None


def ready_to_apply(store) -> list[dict]:
    """Cards approved while their kind was in dry run, now that dry run is OFF for that kind (owner 2026-11-22):
    they show an 'Apply for real' button once their card is refreshed."""
    from proposals import default_dry_run, is_void
    return [p for p in store.all() if p.get("status") == "dry_run" and p.get("kind") == "merge"
            and not default_dry_run("merge") and not is_void(p)]


def list_pending(store) -> str:
    ready = ready_to_apply(store)
    from proposals import is_void
    rows = [p for p in store.all() if p.get("status") in ("pending", "unknown", "partial") and not is_void(p)] + ready
    if not rows:
        return "Nothing is waiting for approval."
    rows.sort(key=lambda p: p.get("created_at", ""))
    lines = [f"*Waiting for approval: {len(rows)}*"]
    for p in rows[:15]:
        tag = {"unknown": " (needs Re-check)", "partial": " (merge stopped part-way)",
               "dry_run": " (approved in dry run: press Apply for real)"}.get(p.get("status"), "")
        lines.append(f"• {_link(p)}{tag} - proposed {str(p.get('created_at', ''))[:10]}")
    if len(rows) > 15:
        lines.append(f"…and {len(rows) - 15} older ones.")
    return "\n".join(lines)


def list_applied(store, days: int = 7) -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    done = [p for p in store.all() if p.get("status") in ("applied", "rejected", "undone", "dry_run", "failed")
            and str(p.get("status_at", "")) >= since]
    if not done:
        return f"Nothing was approved, rejected or undone in the last {days} days."
    done.sort(key=lambda p: p.get("status_at", ""), reverse=True)
    word = {"applied": "applied", "rejected": "rejected", "undone": "undone", "dry_run": "approved (dry run)",
            "failed": "approved but failed"}
    lines = [f"*Decided in the last {days} days: {len(done)}*"]
    for p in done[:15]:     # no names: replies are scrubbed of @-mentions, and the card itself shows who decided
        lines.append(f"• {_link(p)} - {word[p['status']]}, {str(p.get('status_at', ''))[:10]}")
    if len(done) > 15:
        lines.append(f"…and {len(done) - 15} more.")
    return "\n".join(lines)


def build_visibility(req: dict, vendor: dict) -> dict | str:
    """Proposal fields for a hide/unhide card, or a reply string when there's nothing to propose."""
    vid, action = req["vendor_id"], req["action"]
    # No "already hidden" short-cut: the directory is cached for hours, so it can be stale right after a change.
    # Xano reports changed=false for a no-op, and the card says so.
    name = esc(vendor.get("Name") or vid)
    reason = esc(req.get("reason") or "")
    if action == "hide":
        what = f"Hide *{name}* ({vid}) from search and the map. The listing is not deleted."
        steps = (f"Approve to hide it. To bring it back later: `@Tulle Bot unhide {vid}`. {CACHE_NOTE}")
    else:
        what = f"Show *{name}* ({vid}) in search and on the map again."
        steps = f"Approve to make it visible again. {CACHE_NOTE}"
    return {"kind": "visibility", "title": f"{name} ({vid}): {'hide' if action == 'hide' else 'unhide'}",
            "what": what,
            "why": f"Asked for in Slack: “{reason}”." if reason else "Asked for in Slack (no reason given).",
            "next_steps": steps,
            "payload": {"vendor_id": vid, "action": action, "name": vendor.get("Name") or "",
                        "reason": (req.get("reason") or "")[:300]}}


def build_edit(req: dict, vendor: dict) -> dict | str:
    vid, field, new = req["vendor_id"], req.get("field"), (req.get("new_value") or "")
    if not field:
        return (f"I can't change “{esc(req.get('field_words'))}” from Slack. I can change: {FIELD_LIST}. "
                "Prices are fixed in the pricing data, not from here.")
    if not new:
        return "What should it be changed to? For example: `@Tulle Bot change V123 website to example.com`."
    if len(new) > 1000:
        return "That value is too long for a card (1,000 characters max)."
    if "<" in new or ">" in new:                 # Slack markup (<!channel>, <@U..>) never goes into a listing
        return "That value has Slack formatting in it (a mention or link). Type it as plain text."
    if has_email(new):
        return "I can't put email addresses into listings from Slack. Change that one in Xano directly."
    if field == "Max_Capacity_Seated" and not re.fullmatch(r"\d{1,5}", new.replace(",", "")):
        return "Seated capacity has to be a number, like `180`."
    if field == "Max_Capacity_Seated":
        new = new.replace(",", "")
    if field == "Website" and not re.fullmatch(r"(https?://)?(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(/[^\s<>]*)?",
                                               new, re.I):
        return f"“{esc(new)}” doesn't look like a web address."
    old = "" if vendor.get(field) is None else str(vendor.get(field))
    if old.strip() == new:
        return f"{field.replace('_', ' ')} on {esc(vendor.get('Name') or vid)} ({vid}) is already “{esc(new)}”."
    name = esc(vendor.get("Name") or vid)
    return {"kind": "vendor_edit", "title": f"{name} ({vid}): {field}",
            "what": f"Change *{field}* on *{name}* ({vid}) from “{esc(old[:1000]) or '(empty)'}” to “{esc(new)}”.",
            "why": "Asked for in Slack.",
            "next_steps": ("Approve to update the listing (it goes through the staged-edit flow, which refuses if the "
                           "value changed in the meantime and keeps the old value for rollback). Reject if it's wrong."),
            "payload": {"vendor_id": vid, "field": field, "old_value": old, "new_value": new,
                        "note": "from a Slack @-mention"}}
