"""Duplicate-vendor scan for table 11 (WPTP Updated Mappings) -> merge proposals.

Matching (HIGH tier only; on 20,718 vendors, 2026-11-22: 521 groups before the conflict rules below, see the branch
commit for the count after):
  A  same Google Place_ID AND similar names (token-sort >= 80)  (place id alone mislabels both ways)
  B  same normalised name + same city + same state
  C  near-identical names (token-sort >= 90) within 1 km AND (same non-generic website domain OR same Place_ID)
Never on website domain alone: chains share one (Marriott 255 rows, Hilton 116).
Never a pair, whatever else matches (2026-11-22, see pair_conflict): different states, more than 10 km apart, two
different Place_IDs, or names that each keep a distinctive word the other lacks (name_conflict). A Place_ID or address
shared by 3+ differently named listings is an operator's office: no evidence. The scan also drops a group whose PDFs
name different venues (pdf_name_conflict). In a group of 3+, EVERY pair must match on its own (no A~B~C chains).
Also skipped: groups that mix categories (a venue and its in-house caterer), groups bigger than 4 (needs a human),
groups with a hidden member (held until a human has checked why it was hidden), and anything already proposed, applied,
undone or rejected (a human said no; we don't ask twice).

Survivor = visible (Validated_Data "1") first, then most PDFs, most clicks, has a Place_ID, oldest id.
The losers are RETIRED (t11.merged_into = survivor) and HIDDEN (Validated_Data "0"; owner exception 2026-11-20), never
deleted, and every repointed row and every changed column is backed up first, so a merge can be undone (see XANO_MERGE_PROPOSAL.md). Reference counts below are read-only GETs within a page budget.
"""
from __future__ import annotations

import ast
import collections
import json
import logging
import math
import re
import threading
import time
import unicodedata
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
BRAND_NAME_MIN = 3          # a normalised name on this many vendors is a brand ("hilton garden inn", "first baptist church")
MAX_GROUP = 4
MAX_KM = 10.0               # two records further apart than this are never one business, whatever else matches
NOT_A_PLACE_ID = {"na", "none", "null", "nan", "x", "tbd", "unknown"}      # plus anything with "/", spaces, quotes, ":"
HOLD_HIDDEN = True          # groups with a hidden member are not suggested (a person can still ask for them by name)

# Every place a vendor is referenced (workspace 1, all 58 tables' schemas read 2026-11-20, read-only).
# Column kinds: id = text Vendor_ID ("V123"); ids = text[] of Vendor_IDs; json = JSON/JSON-string that embeds Vendor_IDs
# (exact-token replace); rowid = int table-11 row id. action: repoint = the merge endpoint moves it to the survivor;
# keep = history/derived (a log, or rebuilt nightly), left as-is and listed on the details view.
# rebuild = NOT repointed: the rows carry a unique key that embeds the venue's Vendor_ID (t62 space_key "V3794|grandhall",
# t71 pv_key "V1|lulucakeboutique"), so a repoint would leave keys naming the old record or collide with the survivor's
# rows. The nightly denorm regenerates them for the survivor from the moved 36/37 rows (risk review 2026-11-20).
# user = the couple's own data (saved lists, plans, packages, chats), shown separately on the card.
REF_TABLES = [
    (10, "PDF files", [("Vendor_ID", "id")], "repoint", False),
    (36, "Extracted PDF data", [("VENDOR_ID", "id")], "repoint", False),
    (37, "Venue pricing", [("Vendor_ID", "id")], "repoint", False),
    (62, "Venue spaces", [("VENDOR_ID", "id")], "rebuild", False),
    (87, "Venue PDF facts", [("Vendor_ID", "id")], "repoint", False),
    (92, "Service price quotes", [("Vendor_ID", "id")], "repoint", False),
    (93, "Service price items", [("Vendor_ID", "id")], "repoint", False),
    (84, "Floral quotes", [("Vendor_ID", "id")], "repoint", False),
    (85, "Floral items", [("Vendor_ID", "id")], "repoint", False),
    (71, "Venue preferred vendors (venue side)", [("VENDOR_ID", "id")], "rebuild", False),
    (71, "Venue preferred vendors (as a named partner)", [("matched_vendor_id", "id"), ("matched_vendor_idx", "rowid")],
     "repoint", False),
    (86, "Preferred vendors named on PDFs", [("venue_vendor_id", "id"), ("matched_vendor_id", "id")], "repoint", False),
    (65, "Vendor portal claims", [("vendor_id", "id")], "repoint", False),
    (66, "Vendor portal submissions", [("vendor_id", "id"), ("payload", "json")], "repoint", False),
    (67, "Vendor portal messages", [("vendor_id", "id")], "repoint", False),
    (70, "Vendor portal calendars", [("vendor_id", "id")], "repoint", False),
    (73, "Feedback triage", [("vendor_id", "id")], "repoint", False),
    (74, "Staged vendor edits", [("vendor_id", "id")], "repoint", False),
    (1, "Couples' saved lists, last viewed, pricing-page context (users)",
     [("favorited_vendors", "ids"), ("last_viewed_vendor_id", "id"), ("pricing_viewed_ctx", "json")], "repoint", True),
    (90, "Couples' Planner plans", [("vendor_id", "id")], "repoint", True),
    (33, "Couples' budget packages", [("vendor_id", "id")], "repoint", True),
    (31, "Couples' venue calculator entries", [("vendor_id", "id")], "repoint", True),
    (43, "Planner chat cards", [("cards", "json")], "repoint", True),
    (91, "Planner venue lookup log", [("best_vendor_id", "id"), ("alternatives", "json")], "keep", False),
    (72, "Admin audit log", [("target_id", "id")], "keep", False),
    (57, "Hub stats (top venue)", [("top_vendor_id", "rowid")], "keep", False),
]
SKIP_BY_DEFAULT = {1, 43, 72}   # users (60k+), every Planner message, the audit log: counted by the merge endpoint

# Fields that hold a vendor NAME as the key (no Vendor_ID), so an id repoint can't reach them. Listed on every
# details view and in XANO_MERGE_PROPOSAL.md; none is rewritten automatically.
NAME_KEYED = [
    ("1 user", "last_viewed_vendor_name", "updated to the survivor's name when last_viewed_vendor_id is repointed"),
    ("90 rec_wedding_plan", "vendor_name", "updated with vendor_id; free-text items (vendor_id empty) are name-only"),
    ("33 Packages", "vendor_name", "manual packages have no vendor_id: name-only, left as typed"),
    ("31 Venue Calculator Entries", "venue_name", "updated with vendor_id when present"),
    ("65 vendor_claim / 74 pending_edit", "venue_name / vendor_name", "denormalised; updated with vendor_id"),
    ("36 Extracted PDF Data / 37 venue_pricing", "VENUE_NAME / Venue_Name", "the name printed on the PDF; left as-is"),
    ("71 venue_preferred_vendors", "partner_key / partner_name / matched_vendor_name / venue_name",
     "partner_key is a NAME/domain-derived identity: rows that collide after the repoint are combined"),
    ("86 venue_pdf_preferred_vendors", "vendor_name_raw / vendor_name_norm", "as printed on the PDF; left as-is"),
    ("45 WPTP_Upload_Record", "Vendor_Name", "upload log with NO vendor id at all: unreachable by id"),
    ("6 WPTP - Pending Uploads", "Name", "pre-ingest submissions, name only"),
    ("43 rec_messages / 80 rec_memory", "content / notes", "free text written by the Planner/couple: left as-is"),
    ("57 hub_stats", "top_vendor_name", "recomputed nightly"),
    ("11 WPTP Updated Mappings", "mk_name_key", "recomputed for the survivor after the merge"),
]

# Per-column rules for table 11 when records merge into the survivor (S = survivor, L = losers). Every column the
# rule changes is backed up first, so undo restores the exact old values.
COUNTER_RULES = {
    "Clicks_on_Venue": ("sum", "couples' clicks add up; each loser's own count is set to 0 after backup (no "
                                "double count), restored on undo"),
    "Collection": ("union", "every collection tag, survivor's order first"),
    "Flags": ("union_tokens", "every flag, de-duplicated"),
    "Max_Capacity_Seated": ("max", "largest stated capacity"),
    "Time_of_Submission": ("earliest", "first time this venue was submitted"),
    "google_data_last_fetched": ("with_place_id", "taken from the record whose Place_ID is kept"),
    "scrape_last_attempted_at": ("most_recent", ""),
    "scrape_cost_usd": ("sum", "money already spent scraping both; each loser's own value set to 0 after backup, "
                                "restored on undo"),
    "Website": ("fill_if_empty", ""), "Description": ("fill_if_empty", ""), "Contact_Information": ("fill_if_empty", ""),
    "Place_ID": ("fill_if_empty", ""), "Address": ("fill_if_empty", ""), "Venue_Type": ("fill_if_empty", ""),
    "Type_of_Photography": ("fill_if_empty", ""), "Type_of_Entertainment": ("fill_if_empty", ""),
    "Type_of_Beauty": ("fill_if_empty", ""), "image": ("fill_if_empty", ""), "image_1": ("fill_if_empty", ""),
    "image_2": ("fill_if_empty", ""), "image_3": ("fill_if_empty", ""),
    "flt_space_count": ("recompute", "re-derived from the merged pricing rows by the nightly denorm"),
}
# OWNER-ONLY, never auto-written by a merge: gold_chip (curated).
OWNER_ONLY = ("gold_chip",)
# Visibility on merge (owner exception 2026-11-20 to "Validated_Data is owner-only": "merge hides the duplicate,
# ideally merge deletes duplicates"). On apply each loser's Validated_Data is backed up then set "0"; if a loser was
# visible and the survivor hidden, the survivor is backed up then set "1" so the venue stays in search. Undo restores
# both from the backup. Hard delete is a later, separately approved step (XANO_MERGE_PROPOSAL.md section 5).
VISIBILITY_COL = "Validated_Data"
HIDDEN, VISIBLE = "0", "1"


def visibility_plan(survivor: dict, losers: list[dict]) -> list[dict]:
    """The Validated_Data writes a merge makes: [{"vendor_id", "role": survivor|loser, "before", "after"}] for the
    records whose value actually changes. Every one is backed up first and restored on undo."""
    out = []
    any_loser_visible = any(str(m.get(VISIBILITY_COL)) == VISIBLE for m in losers)
    if any_loser_visible and str(survivor.get(VISIBILITY_COL)) != VISIBLE:
        out.append({"vendor_id": survivor.get("Vendor_ID"), "role": "survivor",
                    "before": survivor.get(VISIBILITY_COL), "after": VISIBLE})
    for m in losers:
        if str(m.get(VISIBILITY_COL)) != HIDDEN:
            out.append({"vendor_id": m.get("Vendor_ID"), "role": "loser", "before": m.get(VISIBILITY_COL),
                        "after": HIDDEN})
    return out
# Never stored, shown or sent: the submitting couple's email (User_Email holds their gmail) and the raw Google cache.
PERSONAL_FIELDS = ("User_Email", "google_data_cache")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def redact(v):
    """Email-looking strings -> [email], recursively. Snapshots are for people and undo; nobody needs an address."""
    if isinstance(v, str):
        return EMAIL_RE.sub("[email]", v)
    if isinstance(v, list):
        return [redact(x) for x in v]
    if isinstance(v, dict):
        return {k: redact(x) for k, x in v.items()}
    return v


def safe_snapshot(row: dict) -> dict:
    return {k: redact(v) for k, v in row.items() if not str(k).startswith("_") and k not in PERSONAL_FIELDS}


def parse_when(v):
    """Table-11 dates come as ISO ("2025-03-01", "2025-03-01T10:00:00Z") or Airtable-style "3/1/2025, 4:05:06 PM".
    Returns a sortable datetime, or None."""
    from datetime import datetime
    t = str(v or "").strip()
    if not t:
        return None
    # 2026-11-22: also Airtable's long form "February 8, 2025 at 4:50 PM" (it fell through to fromisoformat -> None,
    # so 'earliest' picked a later ISO date). Mirrors endpoint 578 (vendor/merge).
    for fmt in ("%m/%d/%Y, %I:%M:%S %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y, %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
                "%B %d, %Y at %I:%M %p", "%B %d, %Y at %I:%M:%S %p", "%b %d, %Y at %I:%M %p", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is not None:                   # compare in UTC: "10:00-05:00" is later than "14:00Z"
        from datetime import timezone as _tz
        d = d.astimezone(_tz.utc)
    return d.replace(tzinfo=None)


# Everything else on table 11: Name, Category, State, Country, lat, lng, User_Email, scrape_status, scrape_last_error
# keep the survivor's value; all other flt_* / mk_* denorm columns are recomputed for the survivor after the merge.


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or v == [] or v == {}


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def counter_plan(survivor: dict, losers: list[dict]) -> list[dict]:
    """What each rule column becomes on the survivor: [{"column", "rule", "before": {vid: v}, "after"}] for the columns
    whose value actually changes (or is recomputed)."""
    out = []
    allr = [survivor] + losers
    for col, (rule, _note) in COUNTER_RULES.items():
        vals = [r.get(col) for r in allr]
        sv = survivor.get(col)
        if all(_blank(v) for v in vals):           # nothing on any record: never plan a write (e.g. a 0)
            continue
        if rule == "sum":
            after = sum(_num(v) for v in vals)
            after = int(after) if all(isinstance(v, int) or v in (None, "") for v in vals) else round(after, 4)
        elif rule == "union":
            after = list(dict.fromkeys(x for v in vals for x in (v or []) if x))
        elif rule == "union_tokens":
            toks = [t.strip() for v in vals for t in re.split(r"[;,]", str(v or "")) if t.strip()]
            after = ", ".join(dict.fromkeys(toks))
        elif rule == "max":
            after = max((_num(v) for v in vals), default=0)
            after = int(after) if float(after).is_integer() else after
        elif rule in ("earliest", "most_recent"):
            present = [v for v in vals if v not in (None, "", 0)]
            ds = [(parse_when(v), v) for v in present if parse_when(v) is not None]
            # 2026-11-22: if any present value can't be parsed, keep the survivor's value (same rule as endpoint 578),
            # rather than picking from the parseable subset and showing a plan the endpoint won't apply.
            if len(ds) != len(present):
                ds = []
            pick = (min(ds, key=lambda t: t[0]) if rule == "earliest" else max(ds, key=lambda t: t[0])) if ds else None
            after = pick[1] if pick else sv
        elif rule == "with_place_id":
            src = survivor if not _blank(survivor.get("Place_ID")) else                 next((r for r in losers if not _blank(r.get("Place_ID"))), survivor)
            after = src.get(col)
        elif rule == "fill_if_empty":
            after = sv if not _blank(sv) else next((v for v in vals[1:] if not _blank(v)), sv)
        else:                                          # recompute
            out.append({"column": col, "rule": rule, "before": {r.get("Vendor_ID"): redact(r.get(col)) for r in allr},
                        "after": "recomputed"})
            continue
        if after != sv and not (_blank(after) and _blank(sv)):
            e = {"column": col, "rule": rule, "before": {r.get("Vendor_ID"): redact(r.get(col)) for r in allr},
                 "after": redact(after)}
            if rule == "sum":
                e["losers_after"] = 0
            out.append(e)
    return out


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


STREET_WORD = re.compile(r"\b(st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|hwy|highway|pkwy|parkway|"
                         r"ct|court|way|suite|ste|pl|place|cir|circle|trl|trail|route|rte|sq|square|fm|unit|floor|"
                         r"n|s|e|w|ne|nw|se|sw)\b\.?", re.I)
STREET_ABBR = {"street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "drive": "dr", "lane": "ln",
               "highway": "hwy", "parkway": "pkwy", "court": "ct", "place": "pl", "circle": "cir", "trail": "trl",
               "north": "n", "south": "s", "east": "e", "west": "w", "suite": "ste", "route": "rte"}


def _street_like(v: str) -> bool:
    return bool(re.search(r"\d", v)) or bool(STREET_WORD.search(v))


def city(addr: str) -> str:
    """City from a Google-style address. Returns "" rather than guess: "123 Main St Houston TX 77002" (no comma before
    the state) used to yield the street as the "city", which merged two Houston hotels 35 km apart (QA 2026-11-20)."""
    p = [x.strip() for x in (addr or "").split(",") if x.strip()]
    if p and p[-1].upper() in ("USA", "US", "UNITED STATES"):
        p = p[:-1]
    if len(p) >= 2 and re.match(r"^[A-Za-z]{2}(\s+\d{5}(-\d{4})?)?$", p[-1]):
        cand = p[-2]
    elif len(p) >= 2:
        cand = re.sub(r"\s*\d{5}(-\d{4})?$", "", p[-2])
    else:
        return ""
    cand = cand.strip().lower()
    return "" if not cand or _street_like(cand) else cand


def street(addr: str) -> str:
    """Normalised street line ("60 furman st"), only when it starts with a house number; else ""."""
    first = (addr or "").split(",")[0].strip().lower()
    if not re.match(r"^\d+[a-z]?\s+\S", first):
        return ""
    words = re.sub(r"[^a-z0-9 ]+", " ", first).split()
    return " ".join(STREET_ABBR.get(w, w) for w in words)


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
    """A real Google Place ID, else None. Table 11 also holds "N/A" etc.: matched as an id, "N/A" paired unrelated
    photographers with similar names (found 2026-11-22)."""
    v = str(r.get("Place_ID") or "").strip()
    return v if re.fullmatch(r"[A-Za-z0-9_-]+", v) and v.lower() not in NOT_A_PLACE_ID else None


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
        r["_s"] = street(r.get("Address"))
        try:
            r["_lat"], r["_lng"] = float(r.get("lat")), float(r.get("lng"))
            if not r["_lat"] or not r["_lng"]:
                r["_lat"] = r["_lng"] = None
        except (TypeError, ValueError):
            r["_lat"] = r["_lng"] = None
        out.append(r)
    return out


US_STATES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co",
    "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky", "louisiana": "la", "maine": "me",
    "maryland": "md", "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms", "missouri": "mo",
    "montana": "mt", "nebraska": "ne", "nevada": "nv", "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm",
    "new york": "ny", "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or",
    "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc", "south dakota": "sd", "tennessee": "tn",
    "texas": "tx", "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
    "wisconsin": "wi", "wyoming": "wy", "district of columbia": "dc", "washington dc": "dc", "washington d.c.": "dc",
    "washington, d.c.": "dc"}
_US_CODES = frozenset(US_STATES.values())


def state_keys(v) -> frozenset:
    """State field -> comparable keys: US states as two-letter codes, anything else lower-cased as written."""
    return frozenset(US_STATES.get(x, x) for x in _states(v))


def addr_state(addr: str) -> str:
    """US state code at the end of an address ("..., Boston, MA 02199, USA" -> "ma"); "" when it isn't clearly one."""
    # a ZIP after the code, or a comma before it: "1325 Quincy St NE" is a street, not Nebraska
    tail = r"\s*(?:,\s*(?:USA?|United States))?\s*$"
    m = (re.search(r"[,\s]([A-Za-z]{2})\.?\s+\d{5}(?:-\d{4})?" + tail, addr or "", re.I)
         or re.search(r",\s*([A-Za-z]{2})\.?" + tail, addr or "", re.I))
    c = m.group(1).lower() if m else ""
    return c if c in _US_CODES else ""


# Words that don't tell one venue from another. Removed (with hotel brands) before names are compared.
GENERIC_WORDS = frozenset(
    "a an the and at in of on by for to with room rooms hall halls terrace venue venues house ballroom ballrooms estate "
    "estates event events wedding weddings center centre space spaces place garden gardens barn barns lodge inn hotel "
    "hotels resort resorts spa club country golf course manor mansion restaurant banquet banquets farm farms ranch winery "
    "vineyard vineyards pavilion pavillion rooftop loft lofts chapel theater theatre museum gallery villa suites suite "
    "studio studios photography photo photos photographer photographers video films film music band dj entertainment "
    "florist floral florals flowers catering caterers company co llc inc llp ltd collection group conference facility "
    "facilities grill tavern cafe bar lounge patio courtyard lawn private dining".split())
PDF_TITLE_WORDS = frozenset("package packages pricing price prices menu menus guide brochure info information rates rate "
                            "list packet proposal contract quote weddings not specified unknown none".split())
BRAND_WORDS = frozenset(
    "marriott hilton hyatt westin sheraton ritz carlton doubletree renaissance kimpton wyndham omni fairmont "
    "intercontinental radisson hampton autograph curio tapestry marriot jw regency regis waldorf astoria wedgewood".split())
NUMBER_WORDS = dict(zip("one two three four five six seven eight nine ten eleven twelve".split(), map(str, range(1, 13))))
OPERATOR = re.compile(r"\bby\b(.+)$")


def _fold(n: str) -> str:
    """norm() that keeps accented letters as plain ones and joins possessives ("Manzo's" = "manzos")."""
    n = unicodedata.normalize("NFKD", n or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return norm(re.sub(r"['`’‘]", "", n))


def _split_operator(n: str) -> tuple[str, str]:
    """("ivory room", "cameron mitchell premier events") for "The Ivory Room by Cameron Mitchell Premier Events"."""
    m = OPERATOR.search(n)
    return (n[:m.start()].strip(), m.group(1).strip()) if m and n[:m.start()].strip() else (n, "")


def _words(n: str) -> list[str]:
    return [NUMBER_WORDS.get(t, t) for t in n.split() if len(t) > 1 or t.isdigit()]


def distinctive(name: str) -> list[str]:
    """The words that tell this listing apart: no operator suffix, generic words or brand words. A name that is nothing
    but generic words before its "by ..." ("The Terrace by X", "Flowers by Joan") keeps the words after it."""
    head, op = _split_operator(_fold(name))
    for part in (head, op):
        out = [t for t in _words(part) if t not in GENERIC_WORDS and t not in BRAND_WORDS]
        if out:
            return out
    return []


def _similar(a: str, b: str, cut: float) -> bool:
    return a == b or ratio(a, b) >= cut


def _leftover(xs: list[str], ys: list[str]) -> list[str]:
    return [x for x in xs if not any(x == y or (min(len(x), len(y)) >= 4 and ratio(x, y) >= 85) for y in ys)]


def name_conflict(a: str, b: str) -> bool:
    """True when the names point at two different venues: each keeps a distinctive word the other lacks. Not a conflict:
    one name contained in the other, or only a spelling difference. "X by <operator>" names must also match before the
    "by" (2026-11-22 held-out miss: The Ivory Room vs The Terrace, both "by Cameron Mitchell Premier Events")."""
    fa, fb = _fold(a), _fold(b)
    (ha, oa), (hb, ob) = _split_operator(fa), _split_operator(fb)
    if oa or ob:
        wa, wb = set(_words(ha)) - {"the"}, set(_words(hb)) - {"the"}
        da, db = (w - GENERIC_WORDS - BRAND_WORDS for w in (wa, wb))
        if not (_similar(ha, hb, 90) or _similar("".join(ha.split()), "".join(hb.split()), 90)
                or (da and db and (da <= db or db <= da))):
            return True
    if not (oa and ob):                              # containment; two operator names were compared head to head above
        ta, tb = set(_words(fa)), set(_words(fb))
        ca, cb = "".join(fa.split()), "".join(fb.split())
        if ta <= tb or tb <= ta or ca in cb or cb in ca:
            return False
    xa, xb = distinctive(a), distinctive(b)
    if not xa or not xb or _similar("".join(xa), "".join(xb), 88):
        return False
    la, lb = _leftover(xa, xb), _leftover(xb, xa)
    return bool(la and lb) and not _similar("".join(la), "".join(lb), 80)


def pdf_name_conflict(names_a, names_b) -> bool:
    """The venue names read from each listing's PDFs (table 36) share no distinctive word: two venues filed under one
    listing name (2026-11-22: Cleveland Restoration Society V11220 = Dunham Tavern, V11221 = Sarah Benedict House)."""
    na, nb = ([d for d in ([t for t in distinctive(n) if t not in PDF_TITLE_WORDS] for n in names) if d]
              for names in (names_a, names_b))
    if not na or not nb:
        return False
    for dx in na:
        for dy in nb:
            if (len(_leftover(dx, dy)) < len(dx) or _similar("".join(dx), "".join(dy), 80)
                    or "".join(dx) in "".join(dy) or "".join(dy) in "".join(dx)):
                return False
    return True


def office_keys(rows: list[dict]) -> set:
    """Place_IDs ("pid", x) and street addresses ("street", s, city) shared by 3+ listings with different distinctive
    names: an operator's office (or a multi-business building), so sharing one is no evidence of being one venue."""
    by = collections.defaultdict(list)
    for r in rows:
        if place_id(r):
            by[("pid", place_id(r))].append(r)
        if r["_s"] and r["_c"]:
            by[("street", r["_s"], r["_c"])].append(r)
    out = set()
    for k, g in by.items():
        kept = []                                    # pairwise-different names
        for r in g if len(g) >= 3 else ():
            if all(name_conflict(r.get("Name"), x) for x in kept):
                kept.append(r.get("Name"))
        if len(kept) >= 3:
            out.add(k)
    return out


def pair_conflict(a: dict, b: dict) -> str | None:
    """Why two PREPARED records can't be one business ("state" | "distance" | "place_id" | "name"), or None. Blank values never
    conflict. Added 2026-11-22: rule A (same Place_ID + similar name) paired Mandarin Oriental Boston (V1913, carrying
    New York's Place_ID) with Mandarin Oriental NYC (V9876)."""
    sa, sb = state_keys(a.get("State")), state_keys(b.get("State"))
    if sa and sb and not sa & sb:
        return "state"
    xa, xb = addr_state(str(a.get("Address") or "")), addr_state(str(b.get("Address") or ""))
    if xa and xb and xa != xb:
        return "state"
    if a["_lat"] is not None and b["_lat"] is not None and _km(a, b) > MAX_KM:
        return "distance"
    pa, pb = place_id(a), place_id(b)
    if pa and pb and pa != pb:
        return "place_id"
    if name_conflict(a.get("Name"), b.get("Name")):
        return "name"
    return None


def is_hidden(r: dict) -> bool:
    """Not visible to couples: their search only shows Validated_Data "1"."""
    return str(r.get(VISIBILITY_COL) or "").strip() != VISIBLE


def find_groups(rows: list[dict]) -> list[dict]:
    """High-tier duplicate groups -> [{"ids": [row ids], "rules": ["A","B","C"]}], biggest impact first."""
    return find_groups_held(rows)[0]


def find_groups_held(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(groups, held): `held` = what matched but was NOT suggested, [{"ids", "reason"}]. Pair reasons: state, distance,
    place_id, name, office. Group reasons: too_big, mixed_category, bad_ids, not_all_match, hidden."""
    V = prepare(rows)
    byid = {r["id"]: r for r in V}
    dom_count = collections.Counter(r["_d"] for r in V if r["_d"])
    pairs: dict[frozenset, set] = collections.defaultdict(set)

    conflicts: dict[frozenset, str] = {}

    def add(a, b, rule):
        if a == b:
            return
        key = frozenset((a, b))
        why = conflicts.get(key) or pair_conflict(byid[a], byid[b])
        if why:
            conflicts[key] = why
        else:
            pairs[key].add(rule)

    office = office_keys(V)
    off_pairs: set[frozenset] = set()

    def off_pid(r):
        return ("pid", place_id(r)) in office

    def off_street(r):
        return ("street", r["_s"], r["_c"]) in office

    def share_office(a, b):
        return ((off_pid(a) and place_id(a) == place_id(b))
                or (off_street(a) and (a["_s"], a["_c"]) == (b["_s"], b["_c"])))

    # A: same place id + similar names
    by_pid = collections.defaultdict(list)
    for r in V:
        if place_id(r):
            by_pid[place_id(r)].append(r)
    for g in by_pid.values():
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                if a["_n"] and b["_n"] and ratio(a["_n"], b["_n"]) >= 80:
                    if off_pid(a):
                        off_pairs.add(frozenset((a["id"], b["id"])))
                    else:
                        add(a["id"], b["id"], "A")
    # B: name + city + state, AND physically the same place: within 1 km, same Place_ID or same street address.
    # Brands (a name on 3+ vendors, or a chain website) need the same Place_ID or street address: two Hiltons
    # 800 m apart downtown are different hotels.
    name_count = collections.Counter(r["_n"] for r in V if r["_n"])
    by_ncs = collections.defaultdict(list)
    for r in V:
        if r["_n"] and r["_c"]:
            by_ncs[(r["_n"], r["_c"], r["_st"])].append(r)
    for g in by_ncs.values():
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                same_pid = bool(place_id(a)) and place_id(a) == place_id(b)
                same_street = bool(a["_s"]) and a["_s"] == b["_s"]
                near = a["_lat"] is not None and b["_lat"] is not None and _km(a, b) <= 1.0
                brand = name_count[a["_n"]] >= BRAND_NAME_MIN or (a["_d"] and dom_count[a["_d"]] >= CHAIN_DOMAIN_MIN)
                if not (same_pid or same_street or (near and not brand)):
                    continue
                if share_office(a, b):               # address, Place_ID and map pin are all the office's
                    same_pid, same_street, near = (same_pid and not off_pid(a)), (same_street and not off_street(a)), False
                if same_pid or same_street or (near and not brand):
                    add(a["id"], b["id"], "B")
                else:
                    off_pairs.add(frozenset((a["id"], b["id"])))
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
                        if not (same_dom or same_pid):
                            continue
                        if share_office(a, b) and not (same_pid and not off_pid(a)):
                            off_pairs.add(frozenset((a["id"], b["id"])))     # "nearby" is only the office
                        else:
                            add(a["id"], b["id"], "C")
    uf = _UF()
    for p in pairs:
        a, b = tuple(p)
        uf.u(a, b)
    groups = collections.defaultdict(list)
    for x in uf.p:
        groups[uf.f(x)].append(x)
    out = []
    held = [{"ids": sorted(k, key=int), "reason": why} for k, why in conflicts.items()]
    held += [{"ids": sorted(k, key=int), "reason": "office"} for k in off_pairs if k not in pairs and k not in conflicts]

    def hold(ids, reason):
        held.append({"ids": sorted(ids, key=int), "reason": reason})

    for ids in groups.values():
        if len(ids) < 2:
            continue
        if len(ids) > MAX_GROUP:
            hold(ids, "too_big")
            continue
        cats = {str(byid[i].get("Category") or "").strip().lower() for i in ids}
        if len(cats) > 1:
            hold(ids, "mixed_category")
            continue
        vids = [str(byid[i].get("Vendor_ID") or "").strip() for i in ids]
        if not all(vids) or len(set(vids)) != len(vids):      # a merge needs distinct, real Vendor_IDs
            hold(ids, "bad_ids")
            continue
        if any(frozenset((a, b)) not in pairs for i, a in enumerate(ids) for b in ids[i + 1:]):
            hold(ids, "not_all_match")            # A~B and B~C says nothing about A~C
            continue
        if HOLD_HIDDEN and any(is_hidden(byid[i]) for i in ids):
            hold(ids, "hidden")
            continue
        rules = set()
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                rules |= pairs.get(frozenset((a, b)), set())
        ids = sorted(ids, key=int)
        clicks = sum(int(byid[i].get("Clicks_on_Venue") or 0) for i in ids)
        out.append({"ids": ids, "rules": sorted(rules), "clicks": clicks})
    out.sort(key=lambda g: (-g["clicks"], g["ids"][0]))
    return out, held


def group_key(vendor_ids: list) -> str:
    return "merge:" + "|".join(sorted(str(v or "").strip() for v in vendor_ids))


def choose_survivor(members: list[dict], pdf_counts: dict[str, int]) -> tuple[dict, str]:
    """Visible first (couples' search only shows Validated_Data "1"; keeping a hidden record would hide the venue),
    then most PDFs, most clicks, has a Place ID, oldest."""
    def score(r):
        return (str(r.get("Validated_Data")) == "1", pdf_counts.get(r.get("Vendor_ID"), 0),
                int(r.get("Clicks_on_Venue") or 0), bool(place_id(r)), -int(r["id"]))
    ranked = sorted(members, key=score, reverse=True)
    s, runner = ranked[0], ranked[1]
    reasons = []
    if str(s.get("Validated_Data")) == "1" and str(runner.get("Validated_Data")) != "1":
        reasons.append("it's the one couples can see")
    elif pdf_counts.get(s.get("Vendor_ID"), 0) > pdf_counts.get(runner.get("Vendor_ID"), 0):
        reasons.append("it has more PDFs")
    elif int(s.get("Clicks_on_Venue") or 0) > int(runner.get("Clicks_on_Venue") or 0):
        reasons.append("couples click it more")
    elif place_id(s) and not place_id(runner):
        reasons.append("it has the Google Place ID")
    else:
        reasons.append("it's the older record")
    return s, reasons[0]


# --- reference counts (read-only) -------------------------------------------------------------------------
# JSON keys that hold a Vendor_ID (exact value match) and keys whose int holds a table-11 row id. Matching is by KEY
# only, never by searching text: "V12" inside an image URL or "V12 engine" in a note must not match (QA round 3).
JSON_ID_KEYS = {"vendor_id", "best_vendor_id", "matched_vendor_id", "venue_vendor_id"}


def _json_hits(obj, wanted: set[str], rowids: dict) -> set[str]:
    out, stack = set(), [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            for k, v in o.items():
                kl = str(k).lower()
                if kl in JSON_ID_KEYS and isinstance(v, str) and v.strip() in wanted:
                    out.add(v.strip())
                elif kl.endswith("vendor_idx") and rowids:
                    try:
                        if int(v) in rowids:
                            out.add(rowids[int(v)])
                    except (TypeError, ValueError):
                        pass
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(o, list):
            stack.extend(o)
    return out


def _idx_values(obj) -> set[int]:
    """Every int under a key ending in 'vendor_idx' anywhere in a JSON value."""
    out, stack = set(), [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            for k, v in o.items():
                if str(k).lower().endswith("vendor_idx"):
                    try:
                        out.add(int(v))
                    except (TypeError, ValueError):
                        pass
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(o, list):
            stack.extend(o)
    return out


class RefIndex:
    """Read-only reference index for the vendors in play: counts[table][vendor_id] and rows[table][vendor_id] =
    [(row id, column)], built from 500-row pages within a page budget. JSON columns match the exact Vendor_ID token;
    rowid columns match the vendors' table-11 row ids."""

    MAX_ROW_IDS = 200        # per table per vendor kept in the proposal (the endpoint backs up every row anyway)

    def __init__(self, xano, max_pages: int, page_delay: float = 0.0, skip: set[int] | None = None,
                 only: set[int] | None = None):
        self.xano, self.max_pages, self.page_delay = xano, max_pages, page_delay
        self.skip = SKIP_BY_DEFAULT if skip is None else skip
        if only is not None:                 # e.g. {10, 36} for a cheap count; the rest -> "counted at merge time"
            self.skip = self.skip | {t for t, *_ in REF_TABLES if t not in only}
        self.counts: dict[int, collections.Counter] = {}
        self.rows: dict[int, dict[str, list]] = {}
        self.pdf_names: dict[str, set] = collections.defaultdict(set)      # vendor id -> venue names read from its PDFs
        self.not_counted: set[int] = set()
        self.pages_used = 0

    @staticmethod
    def _hits(v, kind: str, wanted: set[str], rowids: dict) -> set[str]:
        if v is None or v == "":
            return set()
        if kind == "id":
            x = str(v).strip()
            return {x} if x in wanted else set()
        if kind == "ids":
            return {str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip() in wanted}
        if kind == "rowid":
            try:
                return {rowids[int(v)]} if int(v) in rowids else set()
            except (TypeError, ValueError):
                return set()
        obj = v
        if isinstance(v, str):
            try:
                obj = json.loads(v)
            except ValueError:
                return set()              # not JSON: never matched by searching free text
        return _json_hits(obj, wanted, rowids)

    def build(self, wanted: set[str], rowids: dict[int, str] | None = None) -> None:
        rowids = rowids or {}
        for tid, _label, cols, _action, _user in REF_TABLES:
            if tid in self.skip:
                self.not_counted.add(tid)
                continue
            c, rws = collections.Counter(), collections.defaultdict(list)
            page, ok = 1, True
            while True:
                if self.pages_used >= self.max_pages:
                    ok = False
                    break
                d = self.xano.get(f"/table/{tid}/content", {"page": page, "per_page": 500})
                self.pages_used += 1
                items = d.get("items", []) if isinstance(d, dict) else (d or [])
                for row in items:
                    for col, kind in cols:
                        for vid in self._hits(row.get(col), kind, wanted, rowids):
                            c[vid] += 1
                            if tid == 36 and str(row.get("VENUE_NAME") or "").strip():
                                self.pdf_names[vid].add(str(row["VENUE_NAME"]).strip())
                            if len(rws[vid]) < self.MAX_ROW_IDS:
                                rws[vid].append([row.get("id"), col])
                nxt = d.get("nextPage") if isinstance(d, dict) else None
                if not items or len(items) < 500 or (isinstance(d, dict) and "nextPage" in d and not nxt):
                    break
                page = int(nxt) if nxt else page + 1
                if self.page_delay:
                    time.sleep(self.page_delay)
            if ok:
                self.counts[tid], self.rows[tid] = c, dict(rws)
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


def _place(r: dict) -> str:
    c = city(r.get("Address")).title()
    st = ", ".join(sorted(_states(r.get("State")))).title()
    return c or st or ""


RULE_TEXT = {"A": "same Google Place ID and near-identical names",
             "B": "same name at the same place (same address, Google listing or within 1 km)",
             "C": "near-identical names within 1 km with the same website or Place ID"}
WHY_PLAIN = {"A": "Same Google listing.", "B": "Same name at the same address.",
             "C": "Near-identical name in the same spot, same website."}


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _thing(r: dict) -> str:
    cat = str(r.get("Category") or "").strip().lower()
    return "venue" if cat in ("", "venue") else cat


def plain_card(survivor: dict, losers: list[dict], rules: list[str], refs: RefIndex, why_s: str,
               reported: str = "", why_text: str = "") -> dict:
    """The 3-4 short lines Des and Kate see. No ids, table names, Place IDs or row counts beyond what a person cares
    about (PDFs, couples who saved it)."""
    lv = [m.get("Vendor_ID") for m in losers]
    n_times = {2: "twice", 3: "three times", 4: "four times"}.get(len(losers) + 1, f"{len(losers) + 1} times")
    where = _place(survivor)
    headline = (f"These look like the same {_thing(survivor)}: *{_esc(survivor.get('Name'))}*"
                f"{f' ({_esc(where)})' if where else ''} listed {n_times}.")
    if why_text:                             # a person asked for this merge in Slack (merge_requests.py)
        why = why_text
    elif reported:
        why = f"Reported in #feedback: “{reported}”"
    else:
        best = next((r for r in ("A", "B", "C") if r in rules), None)
        why = WHY_PLAIN.get(best, "Same name and place.")
    pdfs = sum(refs.counts.get(10, {}).get(v, 0) for v in lv)
    saved = sum(refs.counts.get(1, {}).get(v, 0) for v in lv) if 1 in refs.counts else None
    plans = sum(refs.counts.get(90, {}).get(v, 0) for v in lv) if 90 in refs.counts else 0
    pricing = sum(refs.counts.get(t, {}).get(v, 0) for t in (36, 37, 92, 93, 84, 85) for v in lv)
    moving = []
    if pdfs:
        moving.append(f"its {_plural(pdfs, 'PDF')}" if len(losers) == 1 else _plural(pdfs, "PDF"))
    elif 10 not in refs.counts:
        moving.append("its PDFs")
    moving.append(_plural(saved, "saved-by couple") if saved else "everyone who saved it")
    if plans:
        moving.append("1 couple's plan" if plans == 1 else f"{plans} couples' plans")
    if pricing or 36 not in refs.counts:
        moving.append("its pricing")
    keep = {"it's the one couples can see": "the visible listing", "it has more PDFs": "the listing with more PDFs",
            "couples click it more": "the listing couples click most",
            "it has the Google Place ID": "the listing linked to Google",
            "you asked to keep it": "the listing you named to keep"}.get(why_s, "the original listing")
    moves = ", ".join(moving[:-1]) + f" and {moving[-1]}" if len(moving) > 1 else moving[0]
    dup = "duplicate is" if len(losers) == 1 else "duplicates are"
    happens = f"We'll keep {keep}, move {moves} onto it, and add up its clicks."
    # cards.render ends this sentence with ", and you can undo this from the card." only when an undo is possible
    hidden = f"The {dup} hidden from search, not deleted"
    return {"headline": headline, "why": why, "happens": happens, "hidden": hidden}


def _fmt(v, n: int = 80) -> str:
    if v is None or v == "" or v == []:
        return "—"
    t = v if isinstance(v, str) else json.dumps(v, default=str)
    t = " ".join(t.split())
    return _esc(t if len(t) <= n else t[: n - 1] + "…")


def details_text(p: dict) -> list[str]:
    """The full technical record for the Details reply: every field side by side, per-table row moves, counter rules,
    name-keyed fields. Returned as chunks (each well under Slack's 3,000-char section limit)."""
    pl = p.get("payload") or {}
    snaps = pl.get("snapshots") or {}
    order = [pl.get("survivor")] + list(pl.get("losers") or [])
    chunks = [f"*Technical details* · survivor *{pl.get('survivor')}* ← {', '.join(pl.get('losers') or [])}\n"
              f"Rules matched: {'; '.join(RULE_TEXT.get(r, r) for r in pl.get('rules') or []) or 'reported in #feedback'}."
              f" Survivor chosen because {pl.get('why_survivor', '?')}."]
    cols = []
    for vid in order:
        for k in (snaps.get(vid) or {}):
            if k not in cols:
                cols.append(k)
    lines, cur = [], "*Both records, field by field* (" + " | ".join(order) + ")\n```"
    for k in cols:
        vals = [(snaps.get(vid) or {}).get(k) for vid in order]
        if all(v in (None, "", []) for v in vals):
            continue
        same = all(json.dumps(v, sort_keys=True, default=str) == json.dumps(vals[0], sort_keys=True, default=str)
                   for v in vals)
        row = f"{k}: " + (f"{_fmt(vals[0])} (same)" if same else " | ".join(_fmt(v, 50) for v in vals))
        if len(cur) + len(row) > 2600:
            lines.append(cur + "```")
            cur = "```"
        cur += "\n" + row
    lines.append(cur + "```")
    chunks += lines
    mv = ["*Rows that move to the survivor (table · column · row ids)*"]
    for t in pl.get("tables") or []:
        if t["status"] == "not_counted":
            mv.append(f"• {t['label']} (table {t['table']}): counted and moved by the merge endpoint at apply time")
        elif t["count"]:
            ids = ", ".join(f"{rid}" for rid, _c in t["rows"][:40]) + (" …" if len(t["rows"]) > 40 else "")
            act = {"repoint": "→ survivor", "rebuild": "(regenerated for the survivor by the nightly rebuild)"}.get(
                t["action"], "(left as history)")
            mv.append(f"• {t['label']} (table {t['table']}): {t['count']} {act} · rows {ids}")
    mv.append(f"• Vendor table (11): {', '.join(pl.get('losers') or [])} get merged_into = {pl.get('survivor')} and "
              "Validated_Data = 0 (hidden from search; backed up, restored on undo). Not deleted.")
    chunks.append("\n".join(mv))
    cl = ["*Counters and fields on the survivor (rule · before → after)*"]
    for c in pl.get("counters") or []:
        before = ", ".join(f"{vid}={_fmt(v, 30)}" for vid, v in c["before"].items())
        tail = "; each duplicate's own value set to 0 (no double count)" if "losers_after" in c else ""
        cl.append(f"• {c['column']} · {c['rule']} · {before} → {_fmt(c['after'], 40)}{tail}")
    if len(cl) == 1:
        cl.append("• No counter changes (all equal or empty).")
    chunks.append("\n".join(cl))
    vp = pl.get("visibility")
    if vp is None:                                  # a card proposed before 2026-11-20: work it out from the snapshots
        sn = [snaps.get(vid) or {} for vid in order]
        vp = visibility_plan(sn[0], sn[1:]) if sn else []
    vis = "; ".join(f"{e['vendor_id']} ({e['role']}) {_fmt(e['before'], 5)} → {e['after']}" for e in vp) or \
        "no change (the duplicates are already hidden)"
    chunks.append("*Visibility (Validated_Data, backed up and restored on undo)*: " + vis +
                  ". *Owner-only, never auto-written*: gold_chip " +
                  ", ".join(f"{vid}={_fmt((snaps.get(vid) or {}).get('gold_chip'), 5)}" for vid in order) +
                  ". Couples' emails and the raw Google cache are never stored here.")
    chunks.append("*Stored by name, not id (not rewritten automatically)*\n" +
                  "\n".join(f"• {t} · {f}: {why}" for t, f, why in NAME_KEYED))
    return [c[:2900] for c in chunks]


def build_proposal(group: dict, byid: dict, refs: RefIndex, reported: str = "", survivor_vid: str = "",
                   why_text: str = "") -> dict:
    members = [byid[i] for i in group["ids"]]
    pdfs = refs.pdfs()
    forced = next((m for m in members if survivor_vid and str(m.get("Vendor_ID")) == survivor_vid), None)
    survivor, why_s = (forced, "you asked to keep it") if forced else choose_survivor(members, pdfs)
    losers = [m for m in members if m is not survivor]
    sv, lv = survivor.get("Vendor_ID"), [m.get("Vendor_ID") for m in losers]
    counts, tables = {}, []
    for tid, label, cols, action, user in REF_TABLES:
        if tid in refs.not_counted:
            tables.append({"table": tid, "label": label, "action": action, "user": user, "status": "not_counted",
                           "count": None, "rows": []})
            continue
        n = sum(refs.counts.get(tid, {}).get(v, 0) for v in lv)
        rows = [r for v in lv for r in refs.rows.get(tid, {}).get(v, [])]
        tables.append({"table": tid, "label": label, "action": action, "user": user, "status": "counted",
                       "count": n, "rows": rows})
        if action == "repoint":
            counts[f"t{tid}"] = n
    plain = plain_card(survivor, losers, group.get("rules") or [], refs, why_s, reported=reported, why_text=why_text)
    counters = counter_plan(survivor, losers)
    snapshots = {m.get("Vendor_ID"): safe_snapshot(m) for m in members}
    nxt = "Approve to merge, or Reject if they're different businesses (I won't suggest this pair again)."
    title = f"{_esc(survivor.get('Name'))} listed {len(members)} times"
    return {"title": title[:140], "what": plain["headline"], "why": plain["why"], "touches": "",
            "next_steps": nxt, "plain": plain,
            "payload": {"survivor": sv, "losers": lv, "counts": counts, "total": sum(counts.values()),
                        "row_ids": {m.get("Vendor_ID"): m["id"] for m in members}, "rules": group.get("rules") or [],
                        "why_survivor": why_s, "snapshots": snapshots, "tables": tables, "counters": counters,
                        "visibility": visibility_plan(survivor, losers)},
            "key": group_key([sv] + lv)}


# --- the scan -----------------------------------------------------------------------------------------------
_SCAN_LOCK = threading.Lock()


def scan(xano, store, mode: str = "nightly", max_cards: int = 10, max_pages: int = 300, page_delay: float = 0.0,
         vendor_rows: list[dict] | None = None) -> dict:
    """mode weekly = every group; nightly = groups touching a vendor created since the last scan.
    Returns {"proposals": [...created...], "groups": n, "pages": n, "note": str}. Creates proposals in `store`;
    posting is the caller's job. One scan at a time per process (a second caller gets note "busy")."""
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"proposals": [], "groups": 0, "pages": 0, "note": "a scan is already running", "busy": True}
    try:
        return _scan(xano, store, mode, max_cards, max_pages, page_delay, vendor_rows)
    finally:
        _SCAN_LOCK.release()


def _scan(xano, store, mode, max_cards, max_pages, page_delay, vendor_rows) -> dict:
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
    skip = store.handled_keys("merge")      # incl. approved-in-dry-run (owner 2026-11-22)
    fresh = [g for g in groups
             if group_key([str(byid[i].get("Vendor_ID")) for i in g["ids"]]) not in skip][:max_cards]
    created = []
    if fresh:
        wanted = {str(byid[i].get("Vendor_ID")) for g in fresh for i in g["ids"]}
        refs = RefIndex(xano, max_pages=max_pages, page_delay=page_delay)
        refs.build(wanted, rowids={int(i): str(byid[i].get("Vendor_ID")) for g in fresh for i in g["ids"]})
        for g in fresh:
            gv = [str(byid[i].get("Vendor_ID")) for i in g["ids"]]
            if any(pdf_name_conflict(refs.pdf_names.get(a, ()), refs.pdf_names.get(b, ()))
                   for n, a in enumerate(gv) for b in gv[n + 1:]):
                log.info("dedupe: held, PDFs name different venues: %s", gv)
                continue
            pr = build_proposal(g, byid, refs)
            p = store.create("merge", pr["title"], pr["what"], pr["why"], pr["next_steps"], pr["payload"],
                                        touches=pr["touches"], key=pr["key"], source=f"dedupe:{mode}",
                             plain=pr["plain"])
            if p is not None:                      # None = another scan/feedback already proposed this group
                created.append(p)
        pages = refs.pages_used
    else:
        pages = 0
    store.set_meta("dedupe_max_id", max([int(r["id"]) for r in rows] + [last_max]))
    store.set_meta(f"dedupe_last_{mode}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return {"proposals": created, "groups": len(groups), "pages": pages,
            "note": f"{len(groups)} candidate groups ({mode}); {len(created)} new suggestions"}


# --- XANO_MERGE_PROPOSAL.md tables are GENERATED from the structures above (python gen_merge_doc.py) ----------------
KIND_RULE = {"id": "replace the loser Vendor_ID with the survivor's",
             "ids": "replace in place, de-duplicate, keep order",
             "json": "by KEY only: a string under vendor_id / best_vendor_id / matched_vendor_id / venue_vendor_id equal to a "
                     "loser id, and an int under any *vendor_idx key equal to a loser row id. Free text is never searched",
             "rowid": "replace the loser's table-11 row id with the survivor's"}
KIND_TYPE = {"id": "text", "ids": "text[]", "json": "json", "rowid": "int row id"}
ACTION_TEXT = {"repoint": "repoint", "keep": "**keep** (history / derived)",
               "rebuild": "**not repointed**: regenerated for the survivor by the nightly denorm (unique key embeds the "
                          "Vendor_ID)"}
RULE_DOC = {"sum": "**sum** S + L; each L set to 0", 
            "union": "**union**, S order first", "union_tokens": "**union of tokens**", "max": "**max**",
            "earliest": "**earliest**", "most_recent": "**most recent**",
            "with_place_id": "**from the record whose Place_ID is kept**",
            "fill_if_empty": "**fill if empty** (S kept; L only where S is blank)",
            "recompute": "**recompute** for S after the merge"}
KEEP_SURVIVOR = ["Name", "Category", "State", "Country", "lat", "lng", "scrape_status",
                 "scrape_last_error"]


def merge_doc_tables() -> str:
    out = ["#### Every vendor reference (from `dedupe.REF_TABLES`)", "",
           "| table | label | column | type | action | rule | couple-side |", "|---|---|---|---|---|---|---|"]
    for tid, label, cols, action, user in REF_TABLES:
        for col, kind in cols:
            rule = KIND_RULE[kind] if action == "repoint" else "-"
            out.append(f"| {tid} | {label} | {col} | {KIND_TYPE[kind]} | {ACTION_TEXT[action]} | {rule} | "
                       f"{'**yes**' if user else ''} |")
    out += ["", "#### Fields keyed by vendor NAME (from `dedupe.NAME_KEYED`; not reachable by an id repoint)", "",
            "| table | column | handling |", "|---|---|---|"]
    out += [f"| {t} | {f} | {w} |" for t, f, w in NAME_KEYED]
    out += ["", "#### Table 11 column rules on merge (from `dedupe.COUNTER_RULES`)", "",
            "| column | rule | note |", "|---|---|---|"]
    out += [f"| {c} | {RULE_DOC[r]} | {n} |" for c, (r, n) in COUNTER_RULES.items()]
    out.append(f"| {VISIBILITY_COL} (L) | **hide**: back up, then set \"{HIDDEN}\" | owner exception 2026-11-20 (\"merge hides "
               "the duplicate\"); restored from the backup on undo |")
    out.append(f"| {VISIBILITY_COL} (S) | **show if a duplicate was visible**: back up, then set \"{VISIBLE}\" | only when a "
               "loser was visible and the survivor hidden, so the venue stays in search; restored on undo |")
    out.append(f"| {', '.join(OWNER_ONLY)} | **never auto-written** (owner-only) | curated; untouched by merge and undo |")
    out.append(f"| {', '.join(PERSONAL_FIELDS)} | **never stored, shown or sent** | User_Email is the submitting couple's "
               "email; the Google cache follows the kept Place_ID server-side |")
    out.append(f"| {', '.join(KEEP_SURVIVOR)} | **keep S** | |")
    out.append("| every other flt_* / mk_* denorm column | **recompute** for S | derived from the moved rows |")
    out.append("| merged_into (L only) | := S Vendor_ID | |")
    return "\n".join(out)
