"""Merge requests in plain words (owner 2026-11-22: "we won't know the vendor ID; we'll use names or IDs, or ask you to
search for duplicates and bring up a bunch at a time").

  @Tulle Bot merge Gramercy Mansion and Gramercy at Lakeside Manor      (names or IDs, any mix)
  @Tulle Bot merge V13433 into V105                                     ("into" = keep the second one)
  @Tulle Bot Rose Hall and Rose Hall Barn are the same venue
  @Tulle Bot find duplicates of Gramercy Mansion  /  is Rose Hall listed twice?
  @Tulle Bot scan duplicates 20                                         (batch; handled in bot.capabilities)
  @Tulle Bot merge Fresno Fields                                        (one name: find its duplicate group; owner 11-27)

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


POLITE_TAIL = re.compile(r"[\s,]+(?:please|pls|plz|thanks|thank\s+you|thx|for\s+me)\s*$", re.I)


def _strip_tail(t: str) -> str:
    """'Fresno Fields please' -> 'Fresno Fields' (owner 11-27)."""
    while True:
        u = POLITE_TAIL.sub("", t).strip(" ,.!?")
        if u == t:
            return t
        t = u


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
        rest = _strip_tail(m.group("rest").rstrip("?").strip())
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


CONFLICT_TEXT = {"state": "they are in different states", "distance": "they are more than 10 km apart",
                 "place_id": "they have different Google Place IDs",
                 "name": "their names look like two different venues"}


def warnings(vendors: list[dict]) -> list[str]:
    """Reasons the scan would never suggest these, in plain words. A person may still ask for the merge by name."""
    ps = dedupe.prepare(vendors)
    out = []
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            why = dedupe.pair_conflict(a, b)
            if why:
                out.append(CONFLICT_TEXT[why])
    if any(dedupe.is_hidden(v) for v in vendors):
        out.append("one of them is hidden from couples, so check why before merging")
    return list(dict.fromkeys(out))


def asked_why(vendors: list[dict]) -> str:
    w = warnings(vendors)
    return "Asked for in Slack." + (" *Check first*: " + "; ".join(w) + "." if w else "")


def dupes_of(rows: list[dict], vendor_ids: set[str]) -> list[dict]:
    """The scan's duplicate groups that include any of vendor_ids (same rules as the nightly scan)."""
    byvid = {str(r.get("Vendor_ID")): r["id"] for r in rows}
    want = {byvid[v] for v in vendor_ids if v in byvid}
    return [g for g in dedupe.find_groups(rows) if want & set(g["ids"])]


def held_hidden(rows: list[dict], vendor_ids: set[str]) -> list[dict]:
    """Rows the scan would group with vendor_ids but holds back because one listing is hidden (they share a held group)."""
    byid = {r["id"]: r for r in rows}
    byvid = {str(r.get("Vendor_ID")): r["id"] for r in rows}
    want = {byvid[v] for v in vendor_ids if v in byvid}
    ids = {i for h in dedupe.find_groups_held(rows)[1] if h["reason"] == "hidden" and want & set(h["ids"])
           for i in h["ids"]}
    return [byid[i] for i in sorted(ids - want, key=int)]


def _num(v):
    try:
        f = float(v)
        return f if f else None                    # 0 = unknown location in table 11
    except (TypeError, ValueError):
        return None


def same_place(vendors: list[dict], km: float = 2.0) -> bool:
    """Every pair is within `km` (when both have coordinates) or, failing coordinates, in the same city."""
    import math
    for i, a in enumerate(vendors):
        for b in vendors[i + 1:]:
            la, oa, lb, ob = _num(a.get("lat")), _num(a.get("lng")), _num(b.get("lat")), _num(b.get("lng"))
            if None not in (la, oa, lb, ob):
                d = 2 * 6371 * math.asin(math.sqrt(math.sin(math.radians(lb - la) / 2) ** 2 + math.cos(
                    math.radians(la)) * math.cos(math.radians(lb)) * math.sin(math.radians(ob - oa) / 2) ** 2))
                if d > km:
                    return False
                continue
            ca, cb = dedupe.city(str(a.get("Address") or "")), dedupe.city(str(b.get("Address") or ""))
            if not ca or ca != cb:
                return False
    return True


def same_name_others(rows: list[dict], vendor: dict) -> list[dict]:
    n = dedupe.norm(vendor.get("Name"))
    return [r for r in rows if r is not vendor and r.get("Vendor_ID") != vendor.get("Vendor_ID")
            and n and dedupe.norm(r.get("Name")) == n]


# --- "merge <one name>": find that name's duplicate group (owner 2026-11-27: "name based merges: build it") ---------
NOT_A_NAME = {"these", "those", "them", "this", "that", "it", "both", "all", "everything", "duplicates", "dupes"}


def looks_like_name(t: str) -> bool:
    """'merge Nowhere Barn' is about a venue even when nothing matches; 'can you merge these?' is not (normal Q&A)."""
    words = re.findall(r"[A-Za-z0-9'&]+", t or "")
    if not words or all(w.lower() in NOT_A_NAME | {"the", "a", "two", "venues", "listings", "cards"} for w in words):
        return False
    return any(w[0].isupper() or w[0].isdigit() for w in words if w.lower() not in ("the", "a"))


def name_seeds(rows: list[dict], target: str) -> tuple[list[dict], list[dict]]:
    """(exact, loose). exact = listings named exactly `target` (keeping 'the'; 'the X' <-> 'X' only when the exact
    spelling finds nothing). loose = every word present, used only to list close names, never for a card."""
    import pdf_list as pl
    live = [r for r in rows if not str(r.get("merged_into") or "").strip()]
    t = pl._exact(target)
    if not t:
        return [], []
    exact = [r for r in live if pl._exact(r.get("Name")) == t]
    if not exact:
        alt = t[4:] if t.startswith("the ") else "the " + t
        exact = [r for r in live if pl._exact(r.get("Name")) == alt]
    if exact:
        return exact, []
    words = dedupe.norm(target).split()
    loose = [r for r in live if words and all(w in dedupe.norm(r.get("Name")).split() for w in words)]
    return [], loose[:8]


def by_name(rows: list[dict], seeds: list[dict]) -> dict:
    """What 'merge <name>' should do with these seed listings, by the scan's own rules (dedupe.find_groups: same Google
    Place ID, same name + address, near-identical name + same website nearby). Pure, no I/O.
      {"kind": "card", "group": g, "others": [...]}   exactly one strong group of exactly 2 listings
      {"kind": "list", "groups": [...], "weak": [...]} several groups, a group of 3+, or only weak (same-name) matches
      {"kind": "none"}                                  one listing, nothing that looks like it
    `others` / `weak` = same-name listings the scan did NOT link (different place, state, category, or hidden)."""
    byid = {r["id"]: r for r in rows}
    vids = {str(s.get("Vendor_ID")) for s in seeds}
    groups = dupes_of(rows, vids)
    grouped = {i for g in groups for i in g["ids"]}
    names = {dedupe.norm(s.get("Name")) for s in seeds} - {""}
    seen, weak = set(), []
    for r in list(seeds) + held_hidden(rows, vids) + [r for r in rows if dedupe.norm(r.get("Name")) in names]:
        if r["id"] in grouped or r["id"] in seen or str(r.get("merged_into") or "").strip():
            continue
        seen.add(r["id"])
        weak.append(r)
    if len(groups) == 1 and len(groups[0]["ids"]) == 2 and not weak_sets(weak):
        return {"kind": "card", "group": groups[0], "others": weak,
                "members": [byid[i] for i in groups[0]["ids"]]}
    if groups or len(weak) >= 2:
        return {"kind": "list", "groups": [dict(g, members=[byid[i] for i in g["ids"]]) for g in groups],
                "weak": weak}
    return {"kind": "none", "weak": weak}


def _line(r: dict) -> str:
    where = ", ".join(x for x in (dedupe.city(str(r.get("Address") or "")).title(), str(r.get("State") or "").strip())
                      if x)
    clicks = int(float(r.get("Clicks_on_Venue") or 0))
    return (f"• {esc(r.get('Name'))} ({r.get('Vendor_ID')})" + (f", {esc(where)}" if where else "")
            + f", {clicks} click{'s' if clicks != 1 else ''}" + (", hidden from couples" if dedupe.is_hidden(r) else "")
            + (f", {esc(r.get('Category'))}" if r.get("Category") else ""))


def weak_sets(weak: list[dict]) -> list[list[dict]]:
    """Same-name listings that could still be one venue: same category AND same state AND same place (same city or
    within 2 km) AND no pair conflict (different Google listings, far apart). Anything else is never offered as a merge
    (owner rule: never merge across categories or locations)."""
    buckets: dict[tuple, list[dict]] = {}
    for r in weak:
        k = (str(r.get("Category") or "").strip().lower(), dedupe.norm(r.get("State")))
        buckets.setdefault(k, []).append(r)
    out = []
    for b in buckets.values():
        if not 2 <= len(b) <= MAX_VENDORS or not same_place(b):
            continue
        ps = dedupe.prepare(b)
        if any(dedupe.pair_conflict(x, y) for i, x in enumerate(ps) for y in ps[i + 1:]):
            continue
        out.append(b)
    return out


def _cmd(members: list[dict]) -> str:
    return "`@Tulle Bot merge " + " and ".join(str(m.get("Vendor_ID")) for m in members) + "`"


def candidates_reply(target: str, res: dict, handled: set[str]) -> str:
    """The 'which one?' reply for 'merge <name>' when there isn't exactly one strong pair. No card."""
    groups, weak = res.get("groups") or [], res.get("weak") or []
    lines = [f"I won't pick for you: “{esc(target)}” matches more than one possible merge, so here they are."]
    for n, g in enumerate(groups, 1):
        best = next((r for r in ("A", "B", "C") if r in g.get("rules", [])), None)
        why = dedupe.WHY_PLAIN.get(best, "The scan's rules match them.").rstrip(".").lower()
        vids = [str(m.get("Vendor_ID")) for m in g["members"]]
        had = " (already has a card)" if dedupe.group_key(vids) in handled else ""
        lines.append(f"*Group {n}* ({why}, {len(vids)} listings){had}:")
        lines += [_line(m) for m in g["members"]]
        lines.append(f"To merge them: {_cmd(g['members'])}")
    if weak:
        sets = weak_sets(weak)
        lines.append(("*Same name, not linked by the scan*" if groups else
                      "*Same name, but the scan's checks (same Google listing, same address, same website) don't link "
                      "them*") + ":")
        lines += [_line(r) for r in weak[:8]]
        for s in sets:
            lines.append(f"These are in the same place, so they might be one venue. Compare the addresses first, then: "
                         f"{_cmd(s)}")
        if not sets:
            lines.append("They're in different places or categories, so they look like different venues. I won't "
                         "suggest merging them.")
    lines.append("I keep the right listing by the usual rule (gold chip, the one couples can see, more PDFs, more "
                 "clicks); add `into V…` at the end to choose. Nothing changes until an approver presses Approve.")
    return "\n".join(lines)


SURVIVOR_WHY = {"it's the one couples can see": "it's the one couples can see",
                "it has more PDFs": "it has more PDFs", "couples click it more": "couples click it more",
                "it has the Google Place ID": "it's linked to Google", "it's the older record": "it's the older record",
                dedupe.WHY_GOLD: dedupe.WHY_GOLD}


def card_lead(target: str, payload: dict, others: list[dict]) -> str:
    """One line before the card: which listing is kept and why."""
    s, losers = payload.get("survivor"), payload.get("losers") or []
    why = SURVIVOR_WHY.get(payload.get("why_survivor"), payload.get("why_survivor") or "the usual rule")
    out = (f"Found one duplicate pair for “{esc(target)}”: keeping {s} because {why}; {', '.join(losers)} would merge "
           "into it. Nothing changes until an approver presses Approve.")
    if others:
        out += (" Not included (same name, but a different place, category, or hidden): "
                + ", ".join(f"{o.get('Vendor_ID')}" + (f" ({esc(o.get('State'))})" if o.get("State") else "")
                            for o in others[:6]) + ".")
    return out
