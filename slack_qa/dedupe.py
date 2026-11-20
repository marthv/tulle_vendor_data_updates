"""Duplicate-vendor scan for table 11 (WPTP Updated Mappings) -> merge proposals.

Matching (ported from the 2026-11-20 analysis, HIGH tier only; ~815 groups / ~973 extra rows on 20,718 vendors):
  A  same Google Place_ID AND similar names (token-sort >= 80)  (place id alone mislabels both ways)
  B  same normalised name + same city + same state
  C  near-identical names (token-sort >= 90) within 1 km AND (same non-generic website domain OR same Place_ID)
Never on website domain alone: chains share one (Marriott 255 rows, Hilton 116). Also skipped: groups that mix
categories (a venue and its in-house caterer), groups bigger than 4 (needs a human), and anything already
proposed, applied or rejected (a human said no; we don't ask twice).

Survivor = most PDFs, then visible (Validated_Data "1"), then most clicks, then has a Place_ID, then oldest id.
The losers are RETIRED (t11.merged_into = survivor), never deleted, and every repointed row is backed up first, so a
merge can be undone (see XANO_MERGE_PROPOSAL.md). Reference counts below are read-only GETs within a page budget.
"""
from __future__ import annotations

import ast
import collections
import logging
import math
import re
import time
from urllib.parse import urlparse

try:
    from rapidfuzz import fuzz

    def ratio(a: str, b: str) -> float:
        return fuzz.token_sort_ratio(a, b)
except ImportError:                                  # pragma: no cover - rapidfuzz is in requirements
    import difflib

    def ratio(a: str, b: str) -> float:
        return 100 * difflib.SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()

log = logging.getLogger("slack_qa.dedupe")


def _esc(t) -> str:
    return str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

GENERIC = {"facebook.com", "instagram.com", "google.com", "weddingwire.com", "theknot.com", "linktr.ee",
           "sites.google.com", "yelp.com", "business.site", "squarespace.com", "wixsite.com", "wix.com", "tiktok.com",
           "youtube.com", "gigsalad.com", "thebash.com", "zola.com", "peerspace.com", "eventective.com",
           "herecomestheguide.com", "bit.ly", "godaddysites.com", "square.site", "honeybook.com", "canva.site",
           "myportfolio.com", "vagaro.com", "glossgenius.com", "pixieset.com", "format.com", "smugmug.com",
           "shootproof.com", "carrd.co", "mailchimpsites.com", "x.com", "twitter.com", "pinterest.com", "linkedin.com",
           "tripadvisor.com", "opentable.com", "resy.com", "tock.com", "eventbrite.com", "apple.com", "wordpress.com",
           "blogspot.com"}
CHAIN_DOMAIN_MIN = 6        # a domain on this many vendors is a chain / platform: never evidence on its own
MAX_GROUP = 4

# Every table that points at a vendor (workspace 1, checked 2026-11-20). action: repoint = the merge endpoint moves
# rows to the survivor; keep = history/derived (rebuilt nightly or a log), left as-is and noted on the card.
REF_TABLES = [
    (10, "PDF files (WPTP PDFs)", ["Vendor_ID"], "repoint"),
    (36, "Extracted PDF data", ["VENDOR_ID"], "repoint"),
    (37, "Venue pricing", ["Vendor_ID"], "repoint"),
    (62, "Venue spaces", ["VENDOR_ID"], "repoint"),
    (87, "Venue PDF facts", ["Vendor_ID"], "repoint"),
    (92, "Service price quotes", ["Vendor_ID"], "repoint"),
    (93, "Service price items", ["Vendor_ID"], "repoint"),
    (84, "Floral quotes", ["Vendor_ID"], "repoint"),
    (85, "Floral items", ["Vendor_ID"], "repoint"),
    (71, "Venue preferred vendors", ["VENDOR_ID", "matched_vendor_id"], "repoint"),
    (86, "Preferred vendors named on PDFs", ["venue_vendor_id", "matched_vendor_id"], "repoint"),
    (90, "Couples' Planner plans", ["vendor_id"], "repoint"),
    (65, "Vendor portal claims", ["vendor_id"], "repoint"),
    (66, "Vendor portal submissions", ["vendor_id"], "repoint"),
    (67, "Vendor portal messages", ["vendor_id"], "repoint"),
    (70, "Vendor portal calendars", ["vendor_id"], "repoint"),
    (73, "Feedback triage", ["vendor_id"], "repoint"),
    (74, "Staged vendor edits", ["vendor_id"], "repoint"),
    (33, "Packages", ["vendor_id"], "repoint"),
    (31, "Venue calculator entries", ["vendor_id"], "repoint"),
    (1, "Couples' saved vendors (users)", ["favorited_vendors", "last_viewed_vendor_id"], "repoint"),
    (91, "Planner venue lookup log", ["best_vendor_id"], "keep"),
]
SKIP_BY_DEFAULT = {1}       # 60k+ user rows: counted by the merge endpoint at preview/apply time, not by paging


def _states(s) -> frozenset:
    if isinstance(s, list):
        return frozenset(str(x).strip().lower() for x in s if str(x).strip())
    s = (s or "").strip()
    if s.startswith("["):
        try:
            return frozenset(x.strip().lower() for x in ast.literal_eval(s))
        except (ValueError, SyntaxError):
            pass
    return frozenset(x.strip().lower() for x in re.split(r"[;,]", s) if x.strip())


def norm(n: str) -> str:
    n = (n or "").lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return " ".join(t for t in n.split() if t not in {"the", "llc", "inc", "co"})


def city(addr: str) -> str:
    p = [x.strip() for x in (addr or "").split(",") if x.strip()]
    if p and p[-1].upper() in ("USA", "US", "UNITED STATES"):
        p = p[:-1]
    if len(p) >= 2 and re.match(r"^[A-Za-z]{2}(\s+\d{5}(-\d{4})?)?$", p[-1]):
        return p[-2].lower()
    if len(p) >= 2:
        return re.sub(r"\d", "", p[-2]).strip().lower()
    return ""


def domain(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    try:
        h = urlparse(u).netloc.lower().split(":")[0]
    except ValueError:
        return ""
    if h.startswith("www."):
        h = h[4:]
    parts = h.split(".")
    if len(parts) > 2 and parts[-2] not in ("co", "com"):
        h = ".".join(parts[-2:])
    return h


def place_id(r: dict) -> str | None:
    v = str(r.get("Place_ID") or "").strip()
    return v if v and v != "None" else None


def _km(a, b) -> float:
    dl = math.radians(b["_lat"] - a["_lat"])
    dg = math.radians(b["_lng"] - a["_lng"])
    x = math.sin(dl / 2) ** 2 + math.cos(math.radians(a["_lat"])) * math.cos(math.radians(b["_lat"])) * math.sin(dg / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


class _UF:
    def __init__(self):
        self.p = {}

    def f(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def u(self, a, b):
        self.p[self.f(a)] = self.f(b)


def prepare(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r.get("merged_into"):                    # already retired by an earlier merge
            continue
        r = dict(r)
        r["_st"], r["_n"], r["_c"], r["_d"] = _states(r.get("State")), norm(r.get("Name")), city(r.get("Address")), \
            domain(r.get("Website"))
        try:
            r["_lat"], r["_lng"] = float(r.get("lat")), float(r.get("lng"))
            if not r["_lat"] or not r["_lng"]:
                r["_lat"] = r["_lng"] = None
        except (TypeError, ValueError):
            r["_lat"] = r["_lng"] = None
        out.append(r)
    return out


def find_groups(rows: list[dict]) -> list[dict]:
    """High-tier duplicate groups -> [{"ids": [row ids], "rules": {"A","B","C"}}], biggest impact first."""
    V = prepare(rows)
    byid = {r["id"]: r for r in V}
    dom_count = collections.Counter(r["_d"] for r in V if r["_d"])
    pairs: dict[frozenset, set] = collections.defaultdict(set)

    def add(a, b, rule):
        if a != b:
            pairs[frozenset((a, b))].add(rule)

    # A: same place id + similar names
    by_pid = collections.defaultdict(list)
    for r in V:
        if place_id(r):
            by_pid[place_id(r)].append(r)
    for g in by_pid.values():
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                if a["_n"] and b["_n"] and ratio(a["_n"], b["_n"]) >= 80:
                    add(a["id"], b["id"], "A")
    # B: name + city + state
    by_ncs = collections.defaultdict(list)
    for r in V:
        if r["_n"] and r["_c"]:
            by_ncs[(r["_n"], r["_c"], r["_st"])].append(r)
    for g in by_ncs.values():
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                add(a["id"], b["id"], "B")
    # C: fuzzy >= 90 within 1 km AND (same non-generic, non-chain domain OR same place id)
    grid = collections.defaultdict(list)
    for r in V:
        if r["_lat"] is not None and r["_n"]:
            grid[(int(r["_lat"] // 0.01), int(r["_lng"] // 0.01))].append(r)
    for (i, j), cell in grid.items():
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for b in grid.get((i + di, j + dj), []):
                    for a in cell:
                        if not int(a["id"]) < int(b["id"]):
                            continue
                        if ratio(a["_n"], b["_n"]) < 90 or _km(a, b) > 1.0:
                            continue
                        same_dom = (a["_d"] and a["_d"] == b["_d"] and "." in a["_d"] and a["_d"] not in GENERIC
                                    and dom_count[a["_d"]] < CHAIN_DOMAIN_MIN)
                        same_pid = place_id(a) and place_id(a) == place_id(b)
                        if same_dom or same_pid:
                            add(a["id"], b["id"], "C")
    uf = _UF()
    for p in pairs:
        a, b = tuple(p)
        uf.u(a, b)
    groups = collections.defaultdict(list)
    for x in uf.p:
        groups[uf.f(x)].append(x)
    out = []
    for ids in groups.values():
        if len(ids) < 2 or len(ids) > MAX_GROUP:
            continue
        cats = {str(byid[i].get("Category") or "").strip().lower() for i in ids}
        if len(cats) > 1:
            continue
        rules = set()
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                rules |= pairs.get(frozenset((a, b)), set())
        ids = sorted(ids, key=int)
        clicks = sum(int(byid[i].get("Clicks_on_Venue") or 0) for i in ids)
        out.append({"ids": ids, "rules": sorted(rules), "clicks": clicks})
    out.sort(key=lambda g: (-g["clicks"], g["ids"][0]))
    return out


def group_key(vendor_ids: list[str]) -> str:
    return "merge:" + "|".join(sorted(vendor_ids))


def choose_survivor(members: list[dict], pdf_counts: dict[str, int]) -> tuple[dict, str]:
    def score(r):
        return (pdf_counts.get(r.get("Vendor_ID"), 0), str(r.get("Validated_Data")) == "1",
                int(r.get("Clicks_on_Venue") or 0), bool(place_id(r)), -int(r["id"]))
    ranked = sorted(members, key=score, reverse=True)
    s, runner = ranked[0], ranked[1]
    reasons = []
    if pdf_counts.get(s.get("Vendor_ID"), 0) > pdf_counts.get(runner.get("Vendor_ID"), 0):
        reasons.append("it has more PDFs")
    elif str(s.get("Validated_Data")) == "1" and str(runner.get("Validated_Data")) != "1":
        reasons.append("it's the one couples can see")
    elif int(s.get("Clicks_on_Venue") or 0) > int(runner.get("Clicks_on_Venue") or 0):
        reasons.append("couples click it more")
    elif place_id(s) and not place_id(runner):
        reasons.append("it has the Google Place ID")
    else:
        reasons.append("it's the older record")
    return s, reasons[0]


# --- reference counts (read-only) -------------------------------------------------------------------------
class RefIndex:
    """{table_id: {vendor_id: count}} built from read-only pages, within a page budget."""

    def __init__(self, xano, max_pages: int, page_delay: float = 0.0, skip: set[int] | None = None):
        self.xano, self.max_pages, self.page_delay = xano, max_pages, page_delay
        self.skip = SKIP_BY_DEFAULT if skip is None else skip
        self.counts: dict[int, collections.Counter] = {}
        self.not_counted: set[int] = set()
        self.pages_used = 0

    def build(self, wanted: set[str]) -> None:
        for tid, _label, cols, _action in REF_TABLES:
            if tid in self.skip:
                self.not_counted.add(tid)
                continue
            c = collections.Counter()
            page, ok = 1, True
            while True:
                if self.pages_used >= self.max_pages:
                    ok = False
                    break
                d = self.xano.get(f"/table/{tid}/content", {"page": page, "per_page": 500})
                self.pages_used += 1
                items = d.get("items", []) if isinstance(d, dict) else (d or [])
                for row in items:
                    for col in cols:
                        v = row.get(col)
                        vals = v if isinstance(v, list) else [v]
                        for x in vals:
                            x = str(x or "").strip()
                            if x in wanted:
                                c[x] += 1
                nxt = d.get("nextPage") if isinstance(d, dict) else None
                if not items or len(items) < 500 or (isinstance(d, dict) and "nextPage" in d and not nxt):
                    break
                page = int(nxt) if nxt else page + 1
                if self.page_delay:
                    time.sleep(self.page_delay)
            if ok:
                self.counts[tid] = c
            else:
                self.not_counted.add(tid)

    def pdfs(self) -> dict[str, int]:
        return dict(self.counts.get(10, {}))


# --- proposal content -------------------------------------------------------------------------------------
def _loc(r: dict) -> str:
    st = r.get("State")
    st = ", ".join(sorted(_states(st))).title() if st else ""
    c = city(r.get("Address")).title()
    return ", ".join(x for x in (c, st) if x) or "location unknown"


RULE_TEXT = {"A": "same Google Place ID and near-identical names",
             "B": "same name in the same city and state",
             "C": "near-identical names within 1 km with the same website or Place ID"}


def build_proposal(group: dict, byid: dict, refs: RefIndex) -> dict:
    members = [byid[i] for i in group["ids"]]
    pdfs = refs.pdfs()
    survivor, why_s = choose_survivor(members, pdfs)
    losers = [m for m in members if m is not survivor]
    sv, lv = survivor.get("Vendor_ID"), [m.get("Vendor_ID") for m in losers]

    def line(r):
        pid = place_id(r)
        return (f"• *{r.get('Vendor_ID')}* {_esc(r.get('Name'))} · {_esc(_loc(r))} · {_esc(r.get('Category') or '?')} · "
                f"{pdfs.get(r.get('Vendor_ID'), 0) if 10 in refs.counts else '?'} PDFs · "
                f"{int(r.get('Clicks_on_Venue') or 0)} clicks · "
                f"{'visible' if str(r.get('Validated_Data')) == '1' else 'hidden'} · "
                f"Place ID {('…' + pid[-6:]) if pid else 'none'}")

    counts, touch_lines, kept = {}, [], []
    for tid, label, cols, action in REF_TABLES:
        if tid in refs.not_counted:
            if action == "repoint":
                touch_lines.append(f"• {label} (table {tid}): counted by the merge endpoint when it runs")
            continue
        n = sum(refs.counts.get(tid, {}).get(v, 0) for v in lv)
        if action == "keep":
            if n:
                kept.append(f"{label} ({n} rows, history)")
            continue
        counts[f"t{tid}"] = n
        if n:
            touch_lines.append(f"• {label} (table {tid}): {n} row{'s' if n != 1 else ''} → {sv}")
    total = sum(counts.values())
    if total == 0:
        touch_lines.insert(0, "• No linked rows found on the retired record(s) in the tables I counted.")
    touch_lines.append(f"• Vendor table (11): {', '.join(lv)} marked merged into {sv} (hidden from search, not deleted)")
    if kept:
        touch_lines.append("• Left as-is: " + "; ".join(kept))

    what = (f"Merge {', '.join(lv)} into *{sv}* ({_esc(survivor.get('Name'))}). Every PDF, price and saved link on "
            f"{'them' if len(lv) > 1 else 'it'} moves to {sv}; {', '.join(lv)} "
            f"{'are' if len(lv) > 1 else 'is'} retired, not deleted.")
    why = (f"These look like the same business: {'; '.join(RULE_TEXT[r] for r in group['rules'])}.\n"
           + "\n".join(line(m) for m in [survivor] + losers)
           + f"\n{sv} stays because {why_s}.")
    nxt = ("Approve to merge. Every row is backed up before it moves and the card gets an *Undo merge* button. "
           "Reject if they're different businesses (e.g. two venues on one estate) and I won't suggest this pair again.")
    title = f"{_esc(survivor.get('Name'))} ({sv} ← {', '.join(lv)})"
    return {"title": title[:140], "what": what, "why": why, "touches": "\n".join(touch_lines), "next_steps": nxt,
            "payload": {"survivor": sv, "losers": lv, "counts": counts, "total": total,
                        "row_ids": {m.get("Vendor_ID"): m["id"] for m in members}},
            "key": group_key([sv] + lv)}


# --- the scan -----------------------------------------------------------------------------------------------
def scan(xano, store, mode: str = "nightly", max_cards: int = 10, max_pages: int = 300, page_delay: float = 0.0,
         vendor_rows: list[dict] | None = None) -> dict:
    """mode weekly = every group; nightly = groups touching a vendor created since the last scan.
    Returns {"proposals": [...created...], "groups": n, "pages": n, "note": str}. Creates proposals in `store`;
    posting is the caller's job."""
    rows = vendor_rows
    if rows is None:
        rows, truncated = xano.rows(11)
        if truncated:
            return {"proposals": [], "groups": 0, "pages": 0, "note": "vendor table read was truncated; skipped"}
    byid = {r["id"]: r for r in rows}
    groups = find_groups(rows)
    last_max = int(store.meta("dedupe_max_id", 0) or 0)
    if mode == "nightly" and last_max:
        groups = [g for g in groups if any(int(i) > last_max for i in g["ids"])]
    skip = store.open_or_rejected_keys("merge")
    fresh = [g for g in groups
             if group_key([str(byid[i].get("Vendor_ID")) for i in g["ids"]]) not in skip][:max_cards]
    created = []
    if fresh:
        wanted = {str(byid[i].get("Vendor_ID")) for g in fresh for i in g["ids"]}
        refs = RefIndex(xano, max_pages=max_pages, page_delay=page_delay)
        refs.build(wanted)
        for g in fresh:
            pr = build_proposal(g, byid, refs)
            created.append(store.create("merge", pr["title"], pr["what"], pr["why"], pr["next_steps"], pr["payload"],
                                        touches=pr["touches"], key=pr["key"], source=f"dedupe:{mode}"))
        pages = refs.pages_used
    else:
        pages = 0
    store.set_meta("dedupe_max_id", max([int(r["id"]) for r in rows] + [last_max]))
    store.set_meta(f"dedupe_last_{mode}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return {"proposals": created, "groups": len(groups), "pages": pages,
            "note": f"{len(groups)} candidate groups ({mode}); {len(created)} new suggestions"}
