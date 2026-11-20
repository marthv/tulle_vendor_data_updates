"""#feedback trends: a deterministic classifier for the templated posts that land in #feedback, the "Venue not in
Tulle" triage, and the weekly read-only trend digest for #growth-strategy.

Why these two (read of #feedback 2026-09-01..2026-11-20, see the report): "Venue not in Tulle" is the largest stream
the bot can act on (80 posts in the 4 weeks since it started on 2026-10-26, ~20/week) and about 4 in 10 of them are not
missing at all: 15 name the venue differently from how we list it ("Holly Hedge" vs "HollyHedge Estate", "Fox Theatre
Atl") and 18 are not venue names ("Ceremony only", "Houston and surrounding areas"). PDF data problems (no pricing,
wrong PDF attached, won't open) are next, but fixing them needs a Xano write path the bot doesn't have, so for now they
are counted in the digest, not proposed.

No LLM calls: the posts are written by our own webhooks (rec_service/notify.py, Xano), so their shape is known.
Nothing here writes anywhere except Slack messages; the add-vendor card goes through the normal approve/dry-run flow.
"""
from __future__ import annotations

import collections
import logging
import re
import time

import dedupe
from cards import esc

log = logging.getLogger("slack_qa.trends")

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
USER_ID = re.compile(r"\buser (\d{1,7})\b")
VENUE_REQ = re.compile(r"^\s*\*Venue not in Tulle\*")
REQ_NAME = re.compile(r"asked for \*(?P<name>[^*\n]{1,160})\*(?:[ \t]*\((?P<loc>[^)\n]{1,160})\))?")
ISSUE = re.compile(r"reported an issue with (?P<vendor>.*?)\s*\((?P<vid>V\d{1,6})\)\s*\.?\s*Details:\s*(?P<details>.+)",
                   re.S | re.I)
STOPPED = re.compile(r"\*What stopped you:\*\s*(?P<reason>[^|\n]+)")
RATING = re.compile(r"Rating (?P<n>[1-5])/5 - (?P<kind>promoter|detractor|passive)", re.I)
FORM = re.compile(r"reported a \d{1,2}/10 rated issue/request", re.I)
# the paywall survey's own fixed options -> our label. Anything else counts as "other"; free text is never shown.
PAYWALL_OPTIONS = [("costs more", "costs too much"), ("see more", "want to see more first"),
                   ("come back", "will come back later"), ("browsing", "just browsing"),
                   ("pdfs i saw", "PDFs didn't have what they needed"), ("isn't on tulle", "vendor not on Tulle")]


def paywall_label(reason: str) -> str:
    r = (reason or "").lower()
    return next((label for key, label in PAYWALL_OPTIONS if key in r), "other")

US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "ohio", "oklahoma", "oregon", "pennsylvania", "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "wisconsin", "wyoming"}
STATE_ABBR = {"al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky",
              "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd",
              "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy"}
# words that say WHERE, not WHICH venue: dropped before matching ("Fox Theatre Atl" -> "fox theatre")
PLACE_WORDS = US_STATES | STATE_ABBR | {"new", "york", "jersey", "carolina", "dakota", "hampshire", "mexico", "rhode",
                                        "island", "west", "nyc", "atl", "dc", "philly", "usa"}
PLACE_FILLER = {"city", "not", "near", "in", "and", "the", "of"}
# a request made of these is a preference or a question, not a vendor name
NON_VENUE = {"nevermind", "disregard", "stupid", "rooftop", "halal", "gluten", "bartender", "buyout", "inclusive",
             "overnight", "accommodations", "multi", "multiday", "areas", "area", "cities", "surrounding", "please",
             "only", "tender"}
FILLER = {"the", "pricing", "price", "prices", "for", "show", "me", "of", "any", "info", "on", "what", "is", "cost",
          "about", "how", "do", "you", "have", "a", "an", "venue", "wedding", "weddings", "listed", "can", "see"}


# --- venue requests ---------------------------------------------------------------------------------------
def parse_venue_request(text: str) -> dict | None:
    """{"name", "loc", "user_id", "email"} from a "*Venue not in Tulle*" post, or None."""
    if not VENUE_REQ.search(text or ""):
        return None
    m = REQ_NAME.search(text)
    if not m:
        return None
    uid = USER_ID.search(text)
    em = EMAIL.search(text)
    return {"name": m.group("name").strip(), "loc": (m.group("loc") or "").strip(),
            "user_id": int(uid.group(1)) if uid else None, "email": em.group(0) if em else ""}


def clean_request(name: str) -> str:
    """Drop leading filler ("The Pricing For The Sapphire Estate" -> "Sapphire Estate"); keep the couple's words."""
    words = (name or "").strip().rstrip("?").split()
    while words and dedupe.norm(words[0]) in FILLER | {""}:
        words.pop(0)
    return " ".join(words)[:80]


def core_tokens(name: str, loc: str = "") -> list[str]:
    """The words that say WHICH vendor. Falls back to the whole (cleaned, then raw) name when every word is a place
    word ("Brooklyn" asked for in "Brooklyn"), so a request never collapses to nothing."""
    loc_t = set(dedupe.norm(loc).split())
    full = dedupe.norm(clean_request(name)).split()
    return [t for t in full if t not in PLACE_WORDS and t not in loc_t] or full or dedupe.norm(name).split()


# where a request says it is: the location field plus place words inside the name ("Fox Theatre Atl")
ABBR_STATE = {"al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
              "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia",
              "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
              "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts",
              "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
              "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico",
              "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
              "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
              "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia",
              "wa": "washington", "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
              "nyc": "new york", "atl": "georgia", "philly": "pennsylvania", "dc": "district of columbia"}
MULTI_STATES = [v for v in set(ABBR_STATE.values()) if " " in v]


def request_places(name: str, loc: str = "") -> set[str]:
    """Places a request names, as state names / city words: {"new jersey"}, {"georgia"}, {"dallas", "texas"}."""
    out = set()
    loc_n = dedupe.norm(loc)
    words = [w for w in (dedupe.norm(clean_request(name)).split() + loc_n.split())]
    text = " ".join(words)
    for st in MULTI_STATES:
        if f" {st} " in f" {text} ":
            out.add(st)
    for w in words:
        if w in US_STATES:
            out.add(w)
        elif w in ABBR_STATE and w not in ("in", "me", "or", "ok", "oh", "hi", "la", "de", "co", "id", "ma", "pa", "al",
                                           "ne", "mo", "va", "wa"):
            out.add(ABBR_STATE[w])                     # skip 2-letter words that are also English / name words
    for part in [p.strip() for p in loc_n.split(",") if p.strip()]:   # the city part of "dallas, texas"
        if part not in US_STATES and part not in ABBR_STATE and part not in MULTI_STATES:
            out.add(part)
    return out


def listing_place(r: dict) -> str:
    st = dedupe.norm(r.get("State"))
    addr = dedupe.norm(r.get("Address"))
    return f" {addr} {st} {ABBR_STATE.get(st, '')} " + " ".join(ABBR_STATE[t] for t in addr.split() if t in ABBR_STATE
                                                         and len(t) == 2) + " "


def place_matches(places: set[str], r: dict) -> bool:
    lp = listing_place(r)
    return any(f" {p} " in lp for p in places)


def not_a_venue(name: str) -> bool:
    """A preference, a question or a place, not a vendor name (heuristic; errs towards 'it is a venue', since the
    worst case there is a card someone rejects)."""
    toks = dedupe.norm(clean_request(name)).split()
    if not toks or len("".join(toks)) < 3:
        return True
    if any(t in NON_VENUE for t in toks):
        return True
    if all(t in PLACE_WORDS | PLACE_FILLER for t in toks):          # "Texas city", "Not new york"
        return True
    rest = [t for t in toks if t not in US_STATES]
    return len(rest) == 1 and len(rest) < len(toks)                 # "Algonquin Illinois": a town + a state


def match_vendor(name: str, loc: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(rows we already list under another spelling, the 3 closest names). Same name ignoring spaces, token-sort
    >= 92, or every word of the request (minus place words) inside a listed name (2+ words, or one word of 6+
    letters). Narrowed by the location when one was given and it matches."""
    core = core_tokens(name, loc)
    q = " ".join(core)
    sq = q.replace(" ", "")
    if not q:
        return [], []
    scored = []
    for r in rows:
        rn = dedupe.norm(r.get("Name"))
        if not rn:
            continue
        s = dedupe.ratio(q, rn)
        rt = set(rn.split())
        same = rn.replace(" ", "") == sq or s >= 92 or \
            (set(core) <= rt and (len(core) >= 2 or len(sq) >= 6))
        scored.append((same, s, r))
    have = [r for same, _s, r in scored if same]
    if loc and len(have) > 1:
        lt = dedupe.norm(loc)
        near = [r for r in have if lt and (lt in dedupe.norm(r.get("Address")) or lt in dedupe.norm(r.get("State")))]
        have = near or have
    closest = [r for _same, _s, r in sorted(scored, key=lambda t: -t[1])[:3]]
    return have, closest


def listed_match(name: str, loc: str, rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(confirmed, possible, closest). A listing only counts as "we already list it" when the request names a place
    and the listing is in it (or the listing has no location at all). Same name with no place, or in another place,
    is only a POSSIBLE match: the request still gets its add-vendor card, which names the possible match."""
    have, closest = match_vendor(name, loc, rows)
    places = request_places(name, loc)
    confirmed = [r for r in have if not (str(r.get("State") or "").strip() or str(r.get("Address") or "").strip())
                 or (places and place_matches(places, r))]
    return confirmed, [r for r in have if r not in confirmed], closest


def request_key(name: str, loc: str = "") -> str:
    return "vreq:" + "".join(core_tokens(name, loc))[:80]


def place_label(r: dict) -> str:
    c = dedupe.city(str(r.get("Address") or ""))
    st = str(r.get("State") or "").strip()
    return ", ".join(x for x in (c.title() if c else "", st) if x)


# --- classify any #feedback post ----------------------------------------------------------------------------
ISSUE_RULES = [   # first match wins; order matters ("the pricing is for a different venue" is a wrong PDF)
    ("duplicate_listing", r"duplicate"),
    ("venue_closed", r"\bclosed\b"),
    ("wrong_pdf", r"wrong venue|different (golf )?venue|not (a|the) pdf for|isn'?t a pdf for|is (actually )?the pdf for|"
                  r"links the price guide to|sheet is for|is for (a )?different|pricing sheet listed here is not for|"
                  r"pdf for a different|document is for a different"),
    ("pricing_estimate_wrong", r"pricing intelligence|double count|per person cost is wrong|underestimat|overestimat"),
    ("wrong_location", r"wrong state|on (the )?map|pinning|showing up in|\bnot (ohio|texas)\b|in (south carolina|texas) not"),
    ("pdf_wont_open", r"blank|not opening|doesn'?t (open|work)|didn'?t open|nothing (happened|popped)|link is (dead|broken)|"
                      r"broken|\b404\b|black screen|can'?t see|no longer see"),
    ("cropped_pdf", r"cropped|cut off"),
    ("outdated_pricing", r"out of date|outdated|\b20(1\d|2[0-6]) pricing|not (b )?2027|need 2027"),
    ("no_pricing_in_pdf", r"no (useful |real )?pric|incomplete|menu|not visible|no price|didn'?t really show|"
                          r"only for catering|not wedding pricing|no (venue|room|rental)|only (about )?food|"
                          r"just a range|range listed|printout|nothing"),
    ("wrong_venue_details", r"capacity|accommodate|does not have|doesn'?t have"),
]
LABEL = {
    "venue_request": "Venue not in Tulle (Planner)", "no_pricing_in_pdf": "PDF has no / incomplete pricing",
    "wrong_pdf": "Wrong PDF attached (another venue's)", "pdf_wont_open": "PDF won't open / blank / dead link",
    "outdated_pricing": "Outdated pricing (old year)", "cropped_pdf": "Cropped PDF",
    "pricing_estimate_wrong": "Pricing estimate wrong", "wrong_location": "Wrong location / state on map",
    "wrong_venue_details": "Wrong venue details", "duplicate_listing": "Duplicate listing", "venue_closed": "Venue closed",
    "issue_other": "Other vendor issue", "nps_detractor": "NPS detractors", "nps_promoter": "NPS promoters",
    "paywall_reason": "Paywall: what stopped them", "paywall_walkaway": "Paywall walk-aways",
    "planner_feedback": "Planner beta ratings", "planner_thumbs_down": "Planner thumbs down",
    "planner_thumbs_up": "Planner thumbs up", "feature_request": "Feature requests", "refund_request": "Refund requests",
    "support_message": "Support messages / other form posts",
}
PDF_PROBLEMS = ("no_pricing_in_pdf", "wrong_pdf", "pdf_wont_open", "outdated_pricing", "cropped_pdf")
# what the bot does with each category today (the digest says so, so nobody expects more)
BOT_ACTION = {
    "venue_request": "card in the post's thread: add to the requested-vendors list (approve files a sourcing task)",
    "duplicate_listing": "merge card (existing)",
    "wrong_venue_details": "vendor-edit card when the post names the new value (existing)",
}


def classify(text: str) -> str | None:
    """Category of one #feedback post, or None for team chatter / anything not from our forms."""
    t = text or ""
    if VENUE_REQ.search(t):
        return "venue_request"
    m = ISSUE.search(t)
    if m:
        d = m.group("details").lower()
        return next((cat for cat, rx in ISSUE_RULES if re.search(rx, d)), "issue_other")
    r = RATING.search(t)
    if r:
        return "nps_detractor" if r.group("kind").lower() == "detractor" else "nps_promoter"
    if STOPPED.search(t):
        return "paywall_reason"
    if "Paywall walk-away" in t:
        return "paywall_walkaway"
    if "Tulle Assistant thumbs down" in t:
        return "planner_thumbs_down"
    if "Tulle Assistant thumbs up" in t:
        return "planner_thumbs_up"
    if "Tulle Assistant beta feedback" in t:
        return "planner_feedback"
    if FORM.search(t):
        if re.search(r"Category:\s*Features", t):
            return "feature_request"
        if re.search(r"\brefund\b", t, re.I):
            return "refund_request"
        return "support_message"
    if re.match(r"\s*Name:.*Message:\s*\S", t, re.S):
        return "support_message"
    return None


def _internal(text: str) -> bool:
    import reports
    uid = USER_ID.search(text or "")
    em = EMAIL.search(text or "")
    return reports.is_internal(int(uid.group(1)) if uid else None, em.group(0) if em else "")


# --- weekly digest ------------------------------------------------------------------------------------------
def _post_text(m: dict) -> str:
    from feedback_watch import post_text
    return post_text(m)


def build_digest(messages: list[dict], now: float | None = None, rows: list[dict] | None = None,
                 top: int = 8) -> str:
    """The weekly digest text. messages = #feedback history (any order, 28+ days); rows = live table-11 rows for the
    "we already list it" split (None = not checked). Internal / test accounts are left out (standing rule)."""
    now = now or time.time()
    wk, prev, month = now - 7 * 86400, now - 14 * 86400, now - 28 * 86400
    this_c, prev_c = collections.Counter(), collections.Counter()
    reasons = collections.Counter()
    req = {}                               # key -> {"name", "users": set, "n"}
    req_split = collections.Counter()      # this week's venue requests: missing / listed / not_venue
    problems = collections.defaultdict(lambda: {"name": "", "n": 0, "cats": collections.Counter()})
    excluded = 0
    for m in messages:
        try:
            ts = float(m.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts < month or ts > now:
            continue
        if any(b.get("type") == "actions" for b in m.get("blocks") or []):
            continue
        text = _post_text(m)
        cat = classify(text)
        if cat is None:
            continue
        if _internal(text):
            excluded += 1
            continue
        if ts >= wk:
            this_c[cat] += 1
            if cat == "paywall_reason":
                reasons[paywall_label(STOPPED.search(text).group("reason"))] += 1
        elif ts >= prev:
            prev_c[cat] += 1
        if cat == "venue_request":
            vr = parse_venue_request(text)
            if not vr:
                continue
            if not_a_venue(vr["name"]):
                kind = "not_venue"
            elif rows is not None and listed_match(vr["name"], vr["loc"], rows)[0]:
                kind = "listed"
            else:
                kind = "missing"
            if ts >= wk:
                req_split[kind] += 1
            if kind == "missing":
                k = request_key(vr["name"], vr["loc"])
                e = req.setdefault(k, {"name": clean_request(vr["name"]), "users": set(), "n": 0})
                e["n"] += 1
                e["users"].add(vr["user_id"] or vr["email"] or ts)
        elif cat in PDF_PROBLEMS or cat in ("pricing_estimate_wrong", "wrong_location", "wrong_venue_details",
                                            "venue_closed", "duplicate_listing", "issue_other"):
            im = ISSUE.search(text)
            p = problems[im.group("vid")]
            p["name"] = im.group("vendor").strip()[:60]
            p["n"] += 1
            p["cats"][cat] += 1

    def delta(cats) -> str:
        a, b = sum(this_c[c] for c in cats), sum(prev_c[c] for c in cats)
        d = a - b
        return f"{a} ({'+' if d > 0 else ''}{d if d else 'same'} vs the week before)"

    day = time.strftime("%b %d", time.gmtime(wk + 86400)).replace(" 0", " ")
    end = time.strftime("%b %d", time.gmtime(now)).replace(" 0", " ")
    lines = [f"*#feedback trends, {day} to {end}* (each number vs the 7 days before)"]
    if not this_c["venue_request"]:
        split = ""
    elif rows is None:
        split = (f": {req_split['missing']} look like vendor names (not checked against our list), "
                 f"{req_split['not_venue']} not a venue name")
    else:
        split = (f": {req_split['missing']} we don't list, {req_split['listed']} we already list under another "
                 f"name, {req_split['not_venue']} not a venue name")
    lines.append(f"• *Venue not in Tulle:* {delta(['venue_request'])}{split}")
    pdf_parts = ", ".join(f"{this_c[c]} {LABEL[c].lower()}" for c in PDF_PROBLEMS if this_c[c])
    lines.append(f"• *PDF problems:* {delta(PDF_PROBLEMS)}" + (f": {pdf_parts}" if pdf_parts else ""))
    for c in ("pricing_estimate_wrong", "wrong_location", "wrong_venue_details", "duplicate_listing", "venue_closed"):
        if this_c[c] or prev_c[c]:
            lines.append(f"• *{LABEL[c]}:* {delta([c])}")
    lines.append(f"• *NPS:* {this_c['nps_promoter']} promoters, {this_c['nps_detractor']} detractors "
                 f"(detractors {delta(['nps_detractor'])})")
    if this_c["planner_feedback"] or this_c["planner_thumbs_down"]:
        lines.append(f"• *Planner:* {this_c['planner_feedback']} beta ratings, {this_c['planner_thumbs_down']} thumbs "
                     f"down, {this_c['planner_thumbs_up']} thumbs up")
    if reasons:
        lines.append("• *Paywall, what stopped them:* " +
                     ", ".join(f"{r} {n}" for r, n in reasons.most_common()))
    for c in ("feature_request", "refund_request"):
        if this_c[c]:
            lines.append(f"• *{LABEL[c]}:* {this_c[c]}")
    asked = sorted(req.values(), key=lambda e: (-len(e["users"]), -e["n"], e["name"].lower()))[:top]
    if asked:
        lines.append("\n*Most-requested vendors we don't list (last 28 days)*")
        lines += [f"• {esc(e['name'])}: {len(e['users'])} couple{'s' if len(e['users']) != 1 else ''}"
                  + (f", asked {e['n']} times" if e["n"] > len(e["users"]) else "") for e in asked]
    rep = sorted(((v, p) for v, p in problems.items() if p["n"] >= 2), key=lambda t: (-t[1]["n"], t[0]))[:top]
    if rep:
        lines.append("\n*Venues reported more than once (last 28 days)*")
        lines += [f"• {esc(p['name'])} ({v}): {p['n']} reports, "
                  + ", ".join(f"{LABEL[c].lower()} {n}" for c, n in p["cats"].most_common()) for v, p in rep]
    lines.append("\n_What I do with these: venue requests get an add-to-list card in their thread; duplicates get a "
                 "merge card. PDF and pricing problems are counted here only (fixing them needs a data write I don't "
                 f"have yet). Team and test accounts left out ({excluded} posts)._")
    return "\n".join(lines)[:3900]


def run_digest(bot, now: float | None = None) -> str:
    import config
    channel = config.trend_digest_channel()
    if not channel or not config.feedback_channel():
        return "trends:off"
    msgs = bot.slack.history_all(config.feedback_channel(), days=29, max_pages=config.trend_digest_max_pages())
    rows = None
    if getattr(bot.xano, "enabled", False):
        try:
            from feedback_watch import VendorDirectory
            rows = VendorDirectory(bot.xano).rows()
        except Exception:  # noqa: BLE001 - the digest still runs without the "already listed" split
            log.exception("trend digest: vendor read failed")
    text = build_digest(msgs, now=now, rows=rows) + "\n_Tulle Bot (automated, read-only)._"
    res = bot.slack.post_message(channel, text, sanitize=True)
    return f"trends:{len(msgs)}:{'ok' if res.get('ok') else res.get('error')}"
