"""Merge requests in plain words (owner 2026-11-22: "we won't know the vendor ID; we'll use names or IDs, or ask you to
search for duplicates and bring up a bunch at a time").

  @Tulle Bot merge Gramercy Mansion and Gramercy at Lakeside Manor      (names or IDs, any mix)
  @Tulle Bot merge V13433 into V105                                     ("into" = keep the second one)
  @Tulle Bot Rose Hall and Rose Hall Barn are the same venue
  @Tulle Bot find duplicates of Gramercy Mansion  /  is Rose Hall listed twice?
  @Tulle Bot scan duplicates 20                                         (batch; handled in bot.capabilities)

Every request ends in the SAME merge card the nightly scan posts (dedupe.build_proposal): nothing moves until an
approver presses Approve, and while DEDUPE_DRY_RUN is on (the Xano merge endpoint isn't built yet) Approve only
records the decision. No model call. Names resolve like the PDF list: exact name (keeping 'the') or a V-number;
several matches -> the bot asks which one."""
from __future__ import annotations

import re

import dedupe
from cards import esc

POLITE = r"^\s*(?:(?:can|could)\s+you\s+)?(?:please\s+)?"
MERGE = re.compile(POLITE + r"(?:merge|combine)\s+(?P<rest>.+?)\s*[.!]*\s*$", re.I | re.S)
SAME = re.compile(r"^\s*(?P<rest>.+?)\s+(?:are|look like)\s+(?:the\s+same(?:\s+(?:venue|vendor|place|business))?"
                  r"|duplicates?(?:\s+of each other)?)\s*[.!]*\s*$", re.I | re.S)
DUPES_OF = re.compile(POLITE + r"(?:find|check|look\s+for|search\s+for|show\s+me|are\s+there|any)?\s*(?:any\s+)?"
                      r"duplicates?\s+(?:of|for)\s+(?P<a>.+?)\s*\??\s*$", re.I | re.S)
LISTED_TWICE = re.compile(r"^\s*is\s+(?P<a>.+?)\s+(?:listed|in\s+tulle)\s+(?:twice|more\s+than\s+once|two\s+times)"
                          r"\s*\??\s*$", re.I | re.S)
SEP = re.compile(r"\s+(into|with|and|&)\s+|\s*,\s*", re.I)
MAX_VENDORS = 4          # the merge endpoint takes at most 3 duplicates per kept record
MAX_CARDS = 5


def _clean(t: str) -> str:
    t = re.sub(r"<[^|>]+\|([^>]+)>", r"\1", t or "")
    t = re.sub(r"^(?:the\s+)?(?:venues?|vendors?|listings?)\s+", "", t.strip(), flags=re.I)
    return t.strip(" \"'“”")


def parse(question: str) -> dict | None:
    """{"kind": "merge", "rest": text, "into": bool} | {"kind": "dupes_of", "target": text} | None."""
    q = re.sub(r"<[^|>]+\|([^>]+)>", r"\1", question or "").strip()
    m = DUPES_OF.match(q) or LISTED_TWICE.match(q)
    if m:
        t = _clean(m.group("a")).rstrip("?").strip()
        return {"kind": "dupes_of", "target": t} if t and len(t.split()) <= 8 else None
    m = MERGE.match(q) or SAME.match(q)
    if m:
        rest = m.group("rest").rstrip("?").strip()
        if not SEP.search(rest):                         # "merge V12" alone: nothing to merge it with
            return {"kind": "merge_incomplete", "target": _clean(rest)}
        into = [s for s in SEP.findall(rest) if s.lower() == "into"]
        if into:
            # "merge A into B" only: 'into' must be the last separator and appear once (QA 11-22: "merge V1 into V2
            # and V3" would have kept V3)
            last = [s for s in SEP.findall(rest)][-1]
            if len(into) > 1 or str(last).lower() != "into":
                return {"kind": "merge_bad_into"}
        return {"kind": "merge", "rest": rest, "into": bool(into)}
    return None


VID_LIKE = re.compile(r"^V\d{1,6}$", re.I)
MAX_PARTS = 8         # QA 11-22: the split search is 2^(parts-1); refuse long lists instead of tying up a worker


def split_targets(rest: str, resolve) -> tuple[list[dict] | None, str | None, bool]:
    """Find the split of `rest` into 2-4 vendors where every part resolves to exactly one vendor. Names can contain
    'and' ("Smith and Co"), so every way of splitting on the separators is tried. Exactly one split must resolve; if
    several do, ask which was meant (never guess). Returns (vendors, None, _) or (None, reply, anything_resolved);
    anything_resolved=False means no part looked like a vendor at all (the caller lets the text go to normal Q&A)."""
    toks = SEP.split(rest)                               # [part, sep, part, sep, part ...] (sep None for commas)
    parts, seps = toks[0::2], toks[1::2]
    n = len(parts)
    if n > MAX_PARTS:
        return None, f"That's too many names for one merge. Name 2 to {MAX_VENDORS} vendors.", True
    best_fail = None
    wins: list[list[dict]] = []
    any_resolved = False
    for mask in range(1 << max(0, n - 1)):               # which separators are real splits (rest are part of names)
        groups, cur = [], parts[0]
        for i in range(n - 1):
            if mask >> i & 1:
                groups.append(cur)
                cur = parts[i + 1]
            else:
                cur = f"{cur} {seps[i] or ','} {parts[i + 1]}"
        groups.append(cur)
        groups = [_clean(g) for g in groups if _clean(g)]
        if not 2 <= len(groups) <= MAX_VENDORS:
            continue
        found, fail = [], None
        for g in groups:
            v, hits = resolve(g)
            if v is None:
                fail = (g, hits)
                any_resolved = any_resolved or bool(hits)
                break
            any_resolved = True
            found.append(v)
        if fail is None:
            if not any(set(id(x) for x in found) == set(id(x) for x in w) and len(w) == len(found) for w in wins):
                wins.append(found)
            continue
        if best_fail is None or (fail[1] and not best_fail[1]):
            best_fail = fail
    if len(wins) == 1:
        return wins[0], None, True
    if len(wins) > 1:                                   # e.g. "Smith and Co and Jones": never guess (QA 11-22)
        opts = "\n".join("• " + " + ".join(f"{esc(v.get('Name'))} ({v.get('Vendor_ID')})" for v in w)
                         for w in wins[:5])
        return None, ("I can read that more than one way. Which did you mean? Ask again with the IDs:\n" + opts), True
    if not any_resolved:                                # the loop stops at the first miss: check every part once
        any_resolved = any(resolve(_clean(p))[0] is not None or VID_LIKE.match(_clean(p) or "")
                           for p in parts if _clean(p))
    if best_fail is None:
        return None, "Tell me 2 to 4 vendors to merge, e.g. `@Tulle Bot merge Rose Hall and Rose Hall Barn`.", False
    g, hits = best_fail
    if not hits:
        return None, f"I can't find a vendor called “{esc(g)}”. Try its exact name or ID (V1234).", any_resolved
    opts = "\n".join(f"• {esc(h.get('Name'))} ({h.get('Vendor_ID')})" + (f", {esc(h.get('State'))}" if h.get("State")
                                                                           else "") for h in hits[:8])
    return None, f"More than one vendor matches “{esc(g)}”. Which one? Ask again with the ID:\n{opts}", True


def check_group(vendors: list[dict]) -> str | None:
    """A reason the group can't be merged, or None."""
    ids = [str(v.get("Vendor_ID") or "") for v in vendors]
    if len(set(ids)) != len(ids):
        return "Those are the same listing twice."
    cats = {str(v.get("Category") or "").strip().lower() for v in vendors}
    if len(cats) > 1:
        return ("Those are in different categories (" + ", ".join(sorted(c or "none" for c in cats))
                + "), so I won't merge them.")
    return None


def dupes_of(rows: list[dict], vendor_ids: set[str]) -> list[dict]:
    """The scan's duplicate groups that include any of vendor_ids (same rules as the nightly scan)."""
    byvid = {str(r.get("Vendor_ID")): r["id"] for r in rows}
    want = {byvid[v] for v in vendor_ids if v in byvid}
    return [g for g in dedupe.find_groups(rows) if want & set(g["ids"])]


def same_name_others(rows: list[dict], vendor: dict) -> list[dict]:
    n = dedupe.norm(vendor.get("Name"))
    return [r for r in rows if r is not vendor and r.get("Vendor_ID") != vendor.get("Vendor_ID")
            and n and dedupe.norm(r.get("Name")) == n]
