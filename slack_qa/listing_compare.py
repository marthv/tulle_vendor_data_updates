"""The "are these really the same place?" half of a merge card (QA plan review + PASS_BAR Amendment 2, 2026-11-22).

A person has to be able to decide in about 30 seconds, so the card shows the listings side by side (name, street address,
website, Google listing same/different, distance, visible/hidden, gold chip, PDFs and their years, room-or-venue guess,
links), a banner on every mismatch, and a newer-PDF alert. Everything is worked out from payload["snapshots"] (read live
when the card is made) and payload["pdf_check"]["pdfs"], so any re-draw of the card shows the same thing. Read-only.

expected_state(payload) is what the approver saw, sent with apply so the merge endpoint can refuse if it changed since.
"""
from __future__ import annotations

import logging
import re

import dedupe
import pdf_dupes
import xano_search

log = logging.getLogger("slack_qa.listing_compare")

VENDOR_PAGE = "https://www.tulletogether.app/vendors/"
FAR_KM = 1.0
ROOM_WORDS = set("""ballroom room rooms hall terrace garden gardens lawn rooftop barn chapel pavilion courtyard loft suite
library patio gallery tent deck salon atrium foyer lounge conservatory boathouse carriage stable veranda""".split())
STATE_FIELDS = ("Name", "Address", "Category", "Place_ID", "Validated_Data")


def fresh(xano, members: list[dict]) -> list[dict]:
    """The same listings read live from table 11 (the bot's own list can be hours old). Falls back to what was given."""
    vids = [str(m.get("Vendor_ID") or "") for m in members]
    try:
        rows = {str(r.get("Vendor_ID")): r for r in xano_search.search(xano, 11, "Vendor_ID", vids)}
        if set(rows) != set(vids):
            return members
        return [{k: v for k, v in rows[v_].items() if k not in dedupe.PERSONAL_FIELDS} for v_ in vids]
    except Exception as e:  # noqa: BLE001 - the card still works from the cached rows
        log.info("live listing read failed: %s", type(e).__name__)
        return members


def _order(payload: dict) -> list[str]:
    return [str(v) for v in [payload.get("survivor")] + list(payload.get("losers") or []) if v]


def rows_of(payload: dict) -> list[dict]:
    snaps = payload.get("snapshots") or {}
    return [snaps[v] for v in _order(payload) if snaps.get(v)]


def pdfs_of(payload: dict, vid: str) -> list[dict] | None:
    """This listing's PDFs as the check read them; None = not read."""
    c = payload.get("pdf_check") or {}
    return [p for p in c["pdfs"] if p.get("vendor_id") == vid] if c.get("pdfs") else (
        [] if c.get("status") == "ok" else None)


def _pin(r: dict):
    try:
        la, ln = float(r.get("lat")), float(r.get("lng"))
    except (TypeError, ValueError):
        return None
    return {"_lat": la, "_lng": ln} if la and ln else None


def km(a: dict, b: dict) -> float | None:
    pa, pb = _pin(a), _pin(b)
    return dedupe._km(pa, pb) if pa and pb else None


def visible(r: dict) -> bool:
    return str(r.get("Validated_Data")) == "1"


def gold(r: dict) -> bool:
    return dedupe.has_gold(r)


def room_label(r: dict, others: list[dict]) -> str:
    """"room or space" only when this name is another listing's name plus a room word; else "whole venue"."""
    mine = set(dedupe.norm(r.get("Name")).split())
    for o in others:
        theirs = set(dedupe.norm(o.get("Name")).split())
        extra = mine - theirs
        if theirs and len(theirs & mine) >= max(1, len(theirs) - 1) and extra & ROOM_WORDS:
            return "room or space"
    return "whole venue"


def _is_venue(r: dict) -> bool:
    return str(r.get("Category") or "").strip().lower() in ("", "venue")


def pair_facts(a: dict, b: dict) -> dict:
    """Each fact is "same" | "different" | "" (missing on one, so it proves nothing)."""
    def cmp(x, y):
        return "" if not x or not y else "same" if x == y else "different"

    d = km(a, b)
    sa, sb = dedupe._states(a.get("State")), dedupe._states(b.get("State"))
    return {"google": cmp(dedupe.place_id(a), dedupe.place_id(b)),
            "street": cmp(dedupe.street(a.get("Address")), dedupe.street(b.get("Address"))),
            "website": cmp(dedupe.domain(a.get("Website")), dedupe.domain(b.get("Website"))),
            "state": "" if not sa or not sb else "same" if sa & sb else "different",
            "category": cmp(str(a.get("Category") or "").strip().lower(), str(b.get("Category") or "").strip().lower()),
            "km": d, "distance": "" if d is None else "same" if d <= FAR_KM else "different"}


def _km_text(d: float) -> str:
    return f"{d * 1000:.0f} m" if d < 1 else f"{d:,.1f} km"


def evidence(survivor: dict, losers: list[dict]) -> str:
    """What the records agree on, in words. A shared Google listing is never given as the only reason."""
    facts = [pair_facts(survivor, m) for m in losers]
    ok = [w for w, k in (("same street address", "street"), ("same website", "website"), ("within 1 km", "distance"))
          if all(f[k] == "same" for f in facts)]
    if all(f["google"] == "same" for f in facts):
        ok.append("linked to the same Google listing")
    if not ok:
        return "Near-identical names."
    if ok == ["linked to the same Google listing"]:
        return ("Near-identical names and linked to the same Google listing, but no matching address, website or map "
                "position: a Google link can be on the wrong record, so check both pages.")
    return "Near-identical names, " + ", ".join(ok) + "."


def mismatches(payload: dict) -> list[str]:
    rows = rows_of(payload)
    out: list[str] = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            f = pair_facts(a, b)
            if f["state"] == "different":
                out.append("they're in different states")
            if f["distance"] == "different":
                out.append(f"they're {_km_text(f['km'])} apart on the map")
            if f["street"] == "different":
                out.append("the street addresses differ")
            if f["google"] == "different":
                out.append("they're linked to different Google listings")
            if f["google"] == "same" and "different" in (f["state"], f["distance"], f["street"]):
                out.append("they share a Google listing even though the places differ, so one link is probably wrong")
            if f["website"] == "different":
                out.append("the websites differ")
            if f["category"] == "different":
                out.append("the categories differ")
    if rows and _is_venue(rows[0]):
        labs = {room_label(r, [o for o in rows if o is not r]) for r in rows}
        if len(labs) > 1:
            out.append("one looks like a room or space and the other like the whole venue")
    hid = [r for r in rows if not visible(r)]
    if hid:
        who = "the kept listing" if hid == rows[:1] else "every listing" if len(hid) == len(rows) else "a duplicate"
        out.append(f"{who} is hidden from search: someone may have hidden it on purpose, so find out why first")
    if rows and not gold(rows[0]) and any(gold(r) for r in rows[1:]):
        out.append("only the listing that would be hidden has the gold chip")
    return list(dict.fromkeys(out))


def _is_primary(label) -> bool:
    return str(label or "").strip().lower() == "primary"


def primary_after(payload: dict) -> dict:
    """Which PDF is Primary after the merge. {"expected": PDF or None by the owner's rule (newer year wins), "planned": PDF,
    None, or "unknown" (no preview yet) by ep578's own plan, "labels": {row_id: planned new label}, "primaries": [PDFs that
    are Primary today, kept listing first]}."""
    pdfs = list((payload.get("pdf_check") or {}).get("pdfs") or [])
    order = _order(payload)
    pdfs.sort(key=lambda p: (order.index(p["vendor_id"]) if p.get("vendor_id") in order else 9, p.get("row_id") or 0))
    hidden = {f["hide_pdf_id"] for f in (payload.get("pdf_check") or {}).get("flags") or [] if f.get("choice") == "hide"}
    exp = pdf_dupes.primary_rule([
        {"key": p["row_id"], "vid": p["vendor_id"], "year": pdf_dupes.top_year(pdf_dupes.years(p.get("year")))}
        for p in pdfs if _is_primary(p.get("label")) and p.get("visible") and p.get("pdf_id") not in hidden], order)
    pv = (payload.get("price") or {}).get("preview") or {}
    labels = {c["row_id"]: c.get("new") for c in pv.get("t10_plan") or [] if c.get("column") == "Current_Assignment"}
    planned: object = "unknown"
    if pv.get("current_state") is not None and sorted(pv.get("hide_pdf_ids") or []) == sorted(hidden):
        left = [p for p in pdfs if _is_primary(labels.get(p["row_id"], p.get("label"))) and p.get("visible")
                and p.get("pdf_id") not in hidden]
        planned = left[0] if left else None
        for p in left:                      # a kept listing may already have had two Primaries; ep578 leaves those alone
            if p["row_id"] == exp and all(q["vendor_id"] == p["vendor_id"] for q in left):
                planned = p
    return {"expected": next((p for p in pdfs if p["row_id"] == exp), None), "planned": planned, "labels": labels,
            "primaries": [p for p in pdfs if _is_primary(p.get("label")) and p.get("visible")]}


def _year_words(p: dict) -> str:
    y = "-".join(sorted(pdf_dupes.years(p.get("year")))) or "no year"
    printed = p.get("printed_years")
    seen = ("printed in it: " + ", ".join(printed) if printed else "no year printed in its text") \
        if printed is not None else "PDF not read, so the label isn't checked"
    return f"{y} ({seen})"


def primary_statement(payload: dict) -> str:
    """One plain sentence when more than one listing has a Primary PDF; "" otherwise."""
    r = primary_after(payload)
    if len({p["vendor_id"] for p in r["primaries"]}) < 2:
        return ""
    to = r["planned"] if r["planned"] not in ("unknown", None) else r["expected"]
    if to is None:
        return ""
    order = _order(payload)
    who = lambda p: "kept listing" if p["vendor_id"] == order[0] else "duplicate"  # noqa: E731
    out = (f":page_with_curl: *Main PDF after the merge:* the {who(to)}'s, labelled {_year_words(to)}. ")
    rest = [f"the {who(p)}'s, labelled {_year_words(p)}, " + (
        f"becomes {r['labels'][p['row_id']]}" if r["labels"].get(p["row_id"]) else "stays listed, but not as the main PDF")
        for p in r["primaries"] if p["row_id"] != to["row_id"]]
    return out + "; ".join(rest)[:1].upper() + "; ".join(rest)[1:] + "."


def primary_mismatch(payload: dict) -> str:
    """Why the card's Primary statement can't be trusted: ep578's plan differs from the owner's rule. "" = fine."""
    r = primary_after(payload)
    if r["planned"] == "unknown" or len({p["vendor_id"] for p in r["primaries"]}) < 2:
        return ""
    a, b = (r["planned"] or {}).get("row_id"), (r["expected"] or {}).get("row_id")
    if a == b:
        return ""
    say = lambda p: f"{'-'.join(sorted(pdf_dupes.years(p.get('year')))) or 'no year'} PDF" if p else "no PDF"  # noqa: E731
    return (f"Xano's plan would make the {say(r['planned'])} the main PDF, but the rule (newer pricing year wins) says the "
            f"{say(r['expected'])}. Ask the owner; don't approve this one.")


def gold_block(payload: dict) -> str:
    """A merge that would retire a gold-chip listing while the kept one has none is never sent."""
    return dedupe.gold_refusal(rows_of(payload), _order(payload)[0]) if rows_of(payload) else ""


def allow_needed(payload: dict) -> list[tuple[str, str, str]]:
    """(key, what the card says, button) for each thing the merge endpoint refuses unless a person says yes. Never on by
    default; keys match payload["allow"] (hidden / unhide / gold)."""
    rows = rows_of(payload)
    if len(rows) < 2:
        return []
    s, losers = rows[0], rows[1:]
    out = []
    if visible(s) and not all(visible(r) for r in losers):
        out.append(("hidden", "A duplicate is hidden from search, and merging would put its PDFs and prices on a visible "
                    "page. Find out why it was hidden (wrong PDFs? the venue asked?) first.", "I checked: merge it anyway"))
    if not visible(s) and any(visible(r) for r in losers):
        out.append(("unhide", "The kept listing is hidden from search, and this merge would make it visible. Find out why "
                    "it was hidden first, or keep the other listing.", "I checked: make it visible"))
    if gold(s) and any(gold(r) for r in losers):       # only the duplicate has it = gold_block, not a question
        out.append(("gold", "The duplicate has the gold chip, and so does the kept listing, so the page keeps its chip.",
                    "OK to merge the gold-chip listing"))
    return out


def allow_missing(payload: dict) -> list[str]:
    have = payload.get("allow") or {}
    return [k for k, _t, _b in allow_needed(payload) if not have.get(k)]


def esc(t) -> str:
    return str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clip(t, n: int) -> str:
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _field(payload: dict, i: int, r: dict, rows: list[dict]) -> str:
    vid = str(r.get("Vendor_ID") or _order(payload)[i])
    lines = [f"*{'Keep' if i == 0 else 'Duplicate'}: <{VENDOR_PAGE}{vid}|{esc(_clip(r.get('Name'), 80)).replace('|', '/')}>*"
             if re.fullmatch(r"V\d+", vid) else f"*{'Keep' if i == 0 else 'Duplicate'}: {esc(_clip(r.get('Name'), 80))}*"]
    lines.append(f"Address: {esc(_clip(r.get('Address'), 140)) or '—'}")
    lines.append(f"Website: {esc(dedupe.domain(r.get('Website'))) or '—'}")
    if i:
        f = pair_facts(rows[0], r)
        lines.append("Google listing: " + {"same": "same", "different": "*different*"}.get(f["google"], "missing on one"))
        lines.append("Distance: " + ("unknown" if f["km"] is None else _km_text(f["km"])))
    else:
        lines.append("Google listing: " + ("linked" if dedupe.place_id(r) else "not linked"))
    lines.append(("Visible in search" if visible(r) else "*Hidden from search*") + (", gold chip" if gold(r) else ""))
    if _is_venue(r):
        lines.append(f"Looks like: {room_label(r, [o for o in rows if o is not r])} (guess from the name)")
    ps = pdfs_of(payload, vid)
    if ps is None:
        lines.append("PDFs: couldn't read")
    else:
        shown = [p for p in ps if p.get("visible")]
        parts = []
        for p in sorted(shown, key=lambda p: (str(p.get("label") or "").lower() != "primary", p.get("row_id") or 0))[:4]:
            y = "-".join(sorted(pdf_dupes.years(p.get("year")))) or "no year"
            t = f"{esc(_clip(p.get('label'), 24)).replace('|', '/')} {y}"
            parts.append(f"<{p['link']}|{t}>" if re.fullmatch(r"https?://[^\s<>|]{1,300}", str(p.get("link") or "")) else t)
        hid = len(ps) - len(shown)
        lines.append(f"PDFs: {len(shown)}" + (f" (+{hid} hidden)" if hid else "")
                     + (": " + ", ".join(parts) if parts else "") + (", …" if len(shown) > 4 else ""))
    return "\n".join(lines)[:1990]


def blocks(payload: dict) -> list[dict]:
    """Banner(s) + the side-by-side section. Empty when the card has no stored records."""
    rows = rows_of(payload)
    if len(rows) < 2:
        return []
    out = []
    bad = mismatches(payload)
    if bad:
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": (
            ":rotating_light: *Check before approving:* " + "; ".join(esc(b) for b in bad) + ".")[:2990]}})
    stop = gold_block(payload) or primary_mismatch(payload)
    if stop:
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": (":no_entry: *Can't be merged as it stands:* " + stop)}})
    st = primary_statement(payload)
    if st:
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": st[:2990]}})
    out.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Side by side*"},
                "fields": [{"type": "mrkdwn", "text": _field(payload, i, r, rows)} for i, r in enumerate(rows[:4])]})
    return out


def expected_state(payload: dict) -> dict:
    """What the card showed for every listing, for the endpoint's stale-card refusal (ep578 v2 shape):
    {Vendor_ID: {Name, Address, Category, Place_ID, Validated_Data, pdf_ids}}, text trimmed, blank = "". pdf_ids covers
    hidden PDFs too ("#<row id>" when a row has no PDF_ID). {} when the PDF list couldn't be read: Approve then runs the
    check again instead of sending (interactions._pdf_gate), so a merge never goes out without this."""
    mine = _shown_state(payload)
    # the endpoint's own copy from the preview the card was built with, when it says the same as the card; if it doesn't,
    # the card is already out of date and sending what the card showed makes the endpoint refuse
    pv = ((payload.get("price") or {}).get("preview") or {}).get("current_state")
    out = dict(pv) if mine and pv == mine else mine
    if out and (payload.get("allow") or {}).get("unhide"):
        out = dict(out, unhide_survivor=True)
    return out


def preview_state(payload: dict) -> bool:
    """True when ep578's own preview was made for this card and says exactly what the card shows."""
    pv = ((payload.get("price") or {}).get("preview") or {}).get("current_state")
    return bool(pv) and pv == _shown_state(payload)


def _shown_state(payload: dict) -> dict:
    out = {}
    snaps = payload.get("snapshots") or {}
    for vid in _order(payload):
        s, ps = snaps.get(vid), pdfs_of(payload, vid)
        if not s or ps is None:
            return {}
        out[vid] = {k: "" if s.get(k) is None else str(s.get(k)).strip() for k in STATE_FIELDS}
        out[vid]["pdf_ids"] = sorted(p["pdf_id"] or f"#{p['row_id']}" for p in ps)
    return out
