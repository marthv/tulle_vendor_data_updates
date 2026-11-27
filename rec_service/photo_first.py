"""PHOTO-FIRST Assistant (owner 2026-11-26: "more pictures, less wording"). REC_PHOTO_FIRST=0 (default) = nothing here
runs and every response is exactly as before.

When on, app._pf() passes each answer through apply():
  text        one short line ("3 in Brooklyn for 100 guests"), never a paragraph. The full answer is not sent; it is
              still what is stored and what the model reads back.
  cards       each card gains `facts` - icons + words the card can show: Seats N and up to two setting tags we hold for
              the WHOLE venue (venue type, vibes). Never a guest minimum (that belongs to one room and season), never a
              pricing style (all-inclusive and the like are left out).
              Members (level full) also get `price_line` {text, source: "estimate"} on VENUE cards: the labelled estimate
              only, "About $38,100 for 100 guests before tax and tip, an estimate" - never a rental line (owner ruling T0-55,
              2026-11-26: QA found 11 of 28 member rental lines wrong against their PDFs). Light: the rounded band, marked
              as an estimate. Free: no price line, and no price digit anywhere. Other vendors: no price line.
              Free, no cards, a price question or a cost widget held back: UPGRADE_LINE + the existing upgrade card.
  understood  {place, guests, date} - what we searched with, as chips the couple can change.
  not_found   {name, question, chip} when we don't hold the venue they named.
Nothing is ranked or judged: no best / lowest / cheapest words are added here.

Also the "cost_lines" quick action (POST /rec/tool {tool: "cost_lines", args: {vendor_id, guests?}}): the venue page's own
lines (Rental, Food and drink, Ceremony, Service charge - the design's "Admin fee"; the PDF line is a service charge) with the room named, each "From the PDF" or "Estimate" (full), or for
everyone else only WHERE each part is ("In the PDF" / "Not in this PDF") with no digits.
Pure except build_cost_lines (xano is passed in)."""
import contextvars
import hashlib
import json
import re

import config
import free_mode

# Per request (QA 2026-11-26): only a page that ASKS for photo-first gets it (?photo_first=1, sent by the component when its
# photoFirst prop is on). Every other Assistant instance (the vendor-page bubble...) gets today's answer even with the switch on.
REQ = contextvars.ContextVar("rec_photo_first", default=False)
PF_ONLY_KEYS = ("setting_tags", "rental_source", "rental_when")   # card keys only photo-first adds (stripped for every other request)
PF_ONLY_TOP = ("unmatched_name",)                   # response keys only photo-first adds


# The signed-in account of this request, for the 50/50 (REC_PHOTO_FIRST_AB_PCT). The middleware sets a fresh box per request
# and app._auth fills it, so the endpoint and its background tasks (copies of the request context) all read the same id.
WHO = contextvars.ContextVar("rec_photo_first_who", default=None)


def seen(user):
    box = WHO.get()
    if isinstance(box, dict) and isinstance(user, dict):
        box["uid"] = user.get("id")


def arm(user_id):
    """'photo_first' | 'control' | 'off'. Same shape as compare.arm: stable per account (sha256(salt:id) % 100 < pct),
    internal testers (REC_PHOTO_FIRST_USER_IDS) always in. 'off' = the switch is off, no account, or pct 0."""
    if not config.PHOTO_FIRST:
        return "off"
    try:
        uid = int(user_id or 0)
    except (TypeError, ValueError):
        return "off"
    if uid <= 0:
        return "off"
    if uid in config.PHOTO_FIRST_USER_IDS:
        return "photo_first"
    if config.PHOTO_FIRST_AB_PCT <= 0:
        return "off"
    h = int(hashlib.sha256(("%s:%d" % (config.PHOTO_FIRST_AB_SALT, uid)).encode()).hexdigest()[:8], 16) % 100
    return "photo_first" if h < config.PHOTO_FIRST_AB_PCT else "control"


def _uid(user=None):
    if isinstance(user, dict):
        return user.get("id")
    box = WHO.get()
    return box.get("uid") if isinstance(box, dict) else None


def on(user=None):
    """This request gets photo-first: the switch is on, the page asked, and (with the 50/50 set) the account is in the
    'photo_first' arm. pct 0: a page asking gets it, as before. pct > 0 and no known account: no (control's answer)."""
    if not (config.PHOTO_FIRST and REQ.get()):
        return False
    a = arm(_uid(user))
    return a == "photo_first" or (a == "off" and config.PHOTO_FIRST_AB_PCT <= 0)


def strip_cards(cards):
    return [{k: v for k, v in c.items() if k not in PF_ONLY_KEYS} if isinstance(c, dict) else c for c in cards or []]


def strip(out):
    """A response for a request that did not ask for photo-first: the card keys the switch adds are taken out again, so it
    is today's response."""
    if not isinstance(out, dict):
        return out
    res = {k: v for k, v in out.items() if k not in PF_ONLY_TOP}
    for k in ("cards", "more_cards"):
        if isinstance(res.get(k), list):
            res[k] = strip_cards(res[k])
    return res

# Owner ruling T0-53 (2026-11-26): no dollar figure reaches a free couple anywhere in photo-first. The cost tools' widgets
# carry figures (market tiers, their own budget split, their own quotes): members only here. Their place: UPGRADE_LINE + the
# product's existing upgrade card (upsell.prompt(): "See the exact prices" / "See plans").
FREE_HIDDEN_WIDGETS = ("cost_estimate", "budget_split", "budget_status", "venue_compare")
UPGRADE_LINE = "See real prices with full access"
COST_TOOL = "cost_lines"
COST_TYPE = "cost_lines"
MAX_WORDS = 15
# Price framed as quality never reaches the line (owner bar: no best / lowest / cheapest ...).
_BANNED = re.compile(r"\b(best|lowest|highest|cheapest|cheaper|priciest|bargain|expensive|pricey|overpriced|affordable|"
                     r"good value|great value|value for money|fits? best|top pick|steal|splurge)\b", re.I)
NEUTRAL_WIDGET = "Here it is"
NEUTRAL_TEXT = "Ask about a venue or a town to see photos"
QUESTION_NEAR = "Which town is it near?"
CHIP_QUOTE = "Check their quote or pricing PDF"

# (pattern on the venue's type / vibe words, label, icon). First match per label wins; order = how telling it is.
_SETTINGS = (
    (r"botanic|garden", "Garden", "leaf"),
    (r"greenhouse|conservatory", "Greenhouse", "leaf"),
    (r"waterfront|beach|lake|ocean|river|harbor|marina", "Waterfront", "water"),
    (r"rooftop|skyline", "Rooftop", "bld"),
    (r"historic", "Historic", "bld"),
    (r"estate|mansion", "Estate", "bld"),
    (r"barn|ranch|farm", "Barn", "barn"),
    (r"winery|vineyard|brewery|distillery", "Winery", "glass"),
    (r"hotel|resort", "Hotel", "bld"),
    (r"museum|gallery", "Museum", "bld"),
    (r"ballroom", "Ballroom", "bld"),
    (r"window|natural light", "Big windows", "win"),
    (r"loft|industrial|warehouse", "Loft", "win"),
    (r"vaulted|tall ceiling", "High ceilings", "win"),
    (r"tent", "Tented", "leaf"),
    (r"country club|golf|private club", "Country club", "leaf"),
    (r"restaurant|\bbar\b", "Restaurant", "fork"),
    (r"performing arts|theat", "Theater", "bld"),
    (r"religious|chapel|church", "Chapel", "bld"),
    (r"university|college", "Campus", "bld"),
    (r"zoo|aquarium", "Zoo or aquarium", "leaf"),
    (r"scenic|nature view|mountain", "Views", "leaf"),
    (r"civic|public|park", "Park or public space", "leaf"),
)
# A pricing style is not a setting (owner: leave all-inclusive out until we know which record is right).
_NOT_SETTING = re.compile(r"inclusive|catering|raw space|byo|package", re.I)
_NOUNS = {"Photography": "photographers", "Entertainment": "bands and DJs", "Flowers": "florists",
          "Beauty": "hair and makeup artists", "Food and Beverage": "caterers", "Wedding Planner": "planners"}
_NOUN1 = {"Photography": "photographer", "Entertainment": "band or DJ", "Flowers": "florist",
          "Beauty": "hair and makeup artist", "Food and Beverage": "caterer", "Wedding Planner": "planner"}
_DIGIT = re.compile(r"\d")
_SENT = re.compile(r"(.+?[.!?])(?:\s|$)", re.S)


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return 0


def _words(v):
    if isinstance(v, str):
        v = re.split(r"[,;|/]", v)
    return [str(x).strip() for x in v or [] if str(x or "").strip()]


def setting_tags(card, limit=2):
    """[{icon, label}] from what we hold for the whole venue: its type(s) and vibes. Never a pricing style."""
    raw = [w for w in _words(card.get("venue_type")) + _words(card.get("setting_tags")) if not _NOT_SETTING.search(w)]
    blob = " | ".join(raw).lower()
    out = []
    for rx, label, icon in _SETTINGS:
        if len(out) >= limit:
            break
        if re.search(rx, blob) and label not in [o["label"] for o in out]:
            out.append({"icon": icon, "label": label})
    return out


def facts(card, free=False):
    """Icon facts for one card (at most 3). Venues: seats + setting. Other vendors: their style words only. free: a word with
    a digit in it is dropped unless it is the seat count."""
    cat = card.get("category") or "Venue"
    out = []
    if cat == "Venue":
        seats = _int(card.get("max_capacity_seated"))
        if seats > 0:
            out.append({"icon": "seat", "label": "Seats %d" % seats})
        out += setting_tags(card)
    else:
        for w in _words(card.get("venue_type"))[:2]:
            if w.lower() not in (cat.lower(), (_NOUNS.get(cat) or "").lower()) and not _NOT_SETTING.search(w):
                out.append({"icon": "tag", "label": w[:40]})
        if card.get("packages_label"):
            out.append({"icon": "list", "label": str(card["packages_label"])[:40]})
    if free:
        out = [f for f in out if f["icon"] in ("seat", "people") or not _DIGIT.search(f["label"])]
    return out[:3]


def _usd(n):
    return "$" + format(int(round(n)), ",")


def price_line(card, level):
    """{"text", "source"} or None. source: "pdf" | "estimate". Free: always None. Venues only."""
    if level == "free" or card.get("price_locked") or (card.get("category") or "Venue") != "Venue":
        return None
    if level == "full":
        # Owner ruling T0-55 (2026-11-26): no rental line on a member card ("Rental $X on a peak-season Saturday, from its
        # PDF", with or without a condition) - independent QA found 11 of 28 wrong against their PDFs. The labelled
        # estimate only, worded as today's Assistant words it. The rental is in the PDF and in "What it costs".
        tot, g = card.get("estimate_total"), _int(card.get("estimate_guests"))
        if isinstance(tot, (int, float)) and tot > 0 and (card.get("category") or "Venue") == "Venue":
            t = ("About %s for %d guests before tax and tip, an estimate" % (_usd(round(tot, -2)), g) if g
                 else "About %s before tax and tip, an estimate" % _usd(round(tot, -2)))
            if "minimum spend" in str(card.get("estimate_label") or ""):   # the stored caveat stays
                t += "; food may be more"   # Compare's words for a food minimum-only total (VenueCompare subLine)
            return {"text": t, "source": "estimate"}
        # Photographers, bands, florists...: no price line (QA 2026-11-26: the stored starting price can be the wrong
        # package - V5338 $800 is an engagement session, the PDF's wedding coverage starts at $9,500). Their PDF has it.
        return None
    band = card.get("price_band") or {}
    if band.get("label"):
        t = str(band["label"])
        return {"text": t[0].upper() + t[1:] + ", an estimate", "source": "estimate"}
    return None


def first_sentence(text):
    t = " ".join(str(text or "").split())
    m = _SENT.match(t)
    return (m.group(1) if m else t).strip()


def _state_of(c):
    st = c.get("state")
    return st if isinstance(st, str) else (st[0] if isinstance(st, list) and len(st) == 1 else "")


def _place_phrase(out, cards):
    """("in"|"near", place) or ("", ""). A state or city is named only when EVERY card is in it; a radius search says
    "near <town>"; otherwise no place (QA 2026-11-26: "in Jersey City" over three New York venues)."""
    f = out.get("search_filters") or {}
    city = str(f.get("city") or "").split(",")[0].strip()
    if city and cards:
        in_city = re.compile(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(city), re.I)
        if all(in_city.search(str(c.get("address") or "")) for c in cards):
            return "in", city
        if f.get("radius_miles") or any(c.get("distance_miles") is not None for c in cards):
            return "near", city
    if city and not cards:
        return "in", city
    states = {_state_of(c) for c in cards}
    st = f.get("states") or []
    cand = st[0] if len(st) == 1 else (states.copy().pop() if len(states) == 1 else "")
    if cand and cand != "International" and cards and states == {cand}:
        return "in", cand
    if cand and cand != "International" and not cards:
        return "in", cand
    return "", ""


def _area(out, cards):
    return _place_phrase(out, cards)[1]


def _guests(out, profile):
    """The guest count the search used, else the profile's - the same for every level (QA round 5, msg 5337: the search
    was 200 guests, the stored estimates were for 100; the member line said "for 100 guests", the free line "for 200
    guests"). A free card carries no priced count, so the headline can only agree across levels if it never reads one.
    A member's price line names the count ITS figure is for ("About $28,800 for 100 guests ..., an estimate")."""
    return _int((out.get("search_filters") or {}).get("guests")) or _int((profile or {}).get("Wedding_Guest_Count"))


_OUTSIDE = re.compile(r"[Ii]t['\u2019]s outside ([^.,;]+?), where you['\u2019]re planning")
# A vendor we don't list, in the model's own words (any category). Read live by agent.unmatched_name (after a search's
# own mark, and only for a name the couple typed) and for turns stored before the switch.
_CAP = r"(?:The\s+)?[A-Z0-9][\w'\u2019&.-]*(?:\s+(?:[A-Z0-9][\w'\u2019&.-]*|of|the|and|at|on|by|&))*"
_LOW = r"[a-z][\w'\u2019&-]*(?:\s+[a-z][\w'\u2019&-]*){1,4}"
_END = r"(?=\s+(?:in|among|on|yet|at|anywhere|here)\b|[.,;!?]|$)"
_NOT_LISTED = (
    re.compile(r"\bWe (?:still )?don['\u2019]t (?:have|list) (?:an? )?(" + _CAP + r")"),
    re.compile(r"\b(?:we|I) (?:still )?(?:can['\u2019]t|couldn['\u2019]t|could not|cannot) find (?:an? |any )?(" + _CAP + r")"),
    re.compile(r"\b(?:we|I) (?:still )?(?:can['\u2019]t|couldn['\u2019]t|could not|cannot) find (?:an? |any )?(" + _LOW + r")" + _END),
    re.compile(r"\b(?i:we) (?:still )?don['\u2019]t (?:have|list) (" + _LOW + r")" + _END),   # only "we" ignores case
    re.compile(r"(" + _CAP + r"),? (?:isn['\u2019]t|is not) (?:in Tulle['\u2019]s listings|listed on Tulle|on Tulle|in Tulle|one of the (?:venues|vendors))"),
    re.compile(r"\bTulle (?:doesn['\u2019]t|does not) (?:have|list) (?:an? )?(" + _CAP + r")"))
_NOT_A_NAME = {"a", "an", "the", "pricing", "prices", "price", "any", "that", "this", "it", "one", "their", "its", "your", "exact",
               "tulle", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february",
               "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "pdf", "pdfs",
               "dj", "djs", "band", "bands", "florist", "florists", "photographer", "photographers", "caterer", "caterers",
               "planner", "planners", "venue", "venues", "vendor", "vendors", "music", "catering", "flowers", "data", "info",
               "information", "details", "numbers", "figures", "rates", "rate", "availability", "dates", "date", "room",
               "rooms", "space", "spaces", "package", "packages", "menu", "menus", "reviews", "photos", "many", "much",
               "enough", "more", "those", "these", "them", "there", "anything", "something", "everything", "every", "all"}


def _bad_name(name):
    """A year, a category, a place, an acronym or a need is never 'a vendor we don't have'."""
    ws = name.split()
    first = ws[0].lower().strip("'\u2019")
    if first in _NOT_A_NAME or re.fullmatch(r"(?:19|20)\d\ds?", first):
        return True
    if len(ws) == 1 and (re.fullmatch(r"[A-Z0-9&]{2,5}", ws[0]) or ws[0].lower() in _NOT_A_NAME):
        return True        # NYC, BYOB, AV, DJ
    if all(w.lower() in _NOT_A_NAME or re.fullmatch(r"[A-Z0-9&]{2,5}|\d+", w) for w in ws):
        return True
    return not name_like(name)


def not_listed(text):
    """'X' when the answer says we don't have / can't find / don't list a vendor by name, else ''. Whether the couple
    named it is checked by the callers (typed_not_listed)."""
    t = " ".join(str(text or "").split())
    for rx in _NOT_LISTED:
        for m in rx.finditer(t):
            name = m.group(1).strip(" .,'\u2019")
            name = re.sub(r"(?:\s+(?:yet|in|on|at|by|and|the|of|among|anywhere|here))+$", "", name)
            bare = re.sub(r"^the\s+", "", name, flags=re.I)   # "The Ryland Inn" is checked as "Ryland Inn" (QA round 5, msg 6705)
            if bare and len(bare.split()) <= 8 and not _bad_name(bare):
                return name
    return ""


# Words that say what KIND of vendor or place, or where, never which one (with lookup's GENERIC / DESCRIPTORS / NON_VENUE).
_KIND = {"outdoor", "indoor", "rustic", "modern", "industrial", "boho", "bohemian", "vintage", "classic", "elegant", "luxury",
         "luxurious", "intimate", "small", "large", "big", "cheap", "affordable", "romantic", "unique", "cozy", "charming",
         "beautiful", "pretty", "nice", "fancy", "upscale", "casual", "local", "nearby", "best", "top", "wedding", "weddings",
         "reception", "ceremony", "photo", "photography", "videographer", "videography", "makeup", "hair", "entertainment",
         "live", "under", "over", "near", "around", "with", "for", "that", "cap", "budget"}
_GEO_END = {"valley", "valleys", "mountains", "mountain", "mtns", "hills", "coast", "shore", "region", "area", "countryside",
            "country", "keys", "islands", "peninsula", "county", "parish", "metro", "upstate", "downstate", "downtown",
            "uptown", "midtown", "suburbs", "city", "town", "state"}


def name_like(name, min_words=1):
    """Could this name ONE vendor? min_words name words, at least one of them distinctive (not a kind / style / need /
    category word), not ending in a region word ("Napa Valley"), and not a place, a style or a need to lookup."""
    import lookup   # lazy: lookup pulls in more of the service
    ts = lookup.tokens(name)
    if len(ts) < min_words or not ts:
        return False
    plain = lookup.GENERIC | lookup.DESCRIPTORS | lookup.NON_VENUE | _KIND | _NOT_A_NAME
    if not any(t not in plain and not t.isdigit() for t in ts) or ts[-1] in _GEO_END:
        return False
    try:
        return not (lookup._place(name) or lookup._descriptive_only(name) or lookup.non_venue_name(name))
    except Exception:
        return False


def couple_typed(name, last_user, min_words=2):
    """The part of `name` the couple typed themselves, when it could name one vendor (name_like), else ''. All of it when they
    typed all of it; else its leading words they typed when only the words after them are not theirs ("Giando on the Water
    Brooklyn" -> "Giando on the Water" for "giando on the water williamsburg": the model added the borough)."""
    import lookup
    said = lookup.key(last_user)
    ws = " ".join(str(name or "").split()).split(" ")
    strip = lambda run: " ".join(run).strip(" ,.;:-")
    whole = strip(ws)
    if lookup.key(whole) and lookup.key(whole) in said:
        return whole if name_like(whole, min_words) else ""
    for j in range(len(ws) - 1, 0, -1):     # longest typed lead whose tail the couple did not type
        run = ws[:j]
        while run and lookup.norm(run[-1]) in lookup.STOP:
            run = run[:-1]
        lead, tail = strip(run), strip(ws[j:])
        if lead and lookup.key(lead) in said and lookup.key(tail) not in said:
            return lead if name_like(lead, min_words) else ""
    return ""


def holds_name(name, names):
    """Any of `names` is (or may be) the vendor they named: the same key, one key inside the other (5+ letters), or a
    lookup score at the "which one did you mean?" level. Errs on the side of yes: a wrong "We don't have X" over a card
    that IS X is worse than no "We don't have"."""
    import lookup
    k = lookup.key(name)
    for n in names or []:
        n = re.sub(r"\s*\([^)]*\)\s*$", "", str(n or ""))      # "Name (State)", as find_by_name lists close names
        nk = lookup.key(n)
        if not nk or not k:
            continue
        if nk == k or (min(len(nk), len(k)) >= 5 and (k in nk or nk in k)) or lookup.score(name, n) >= lookup.MID:
            return True
    return False


def typed_not_listed(text, last_user, shown=()):
    """The vendor the answer says we don't have / can't find (not_listed), when the couple typed that name themselves and
    no card shown may be it; else ''. last_user None = not known (a turn read back without its question): wording only."""
    name = not_listed(text)
    if not name:
        return ""
    if last_user is not None:
        name = couple_typed(name, last_user, min_words=1)
    return name if name and not holds_name(name, shown) else ""


_NOT_IN = re.compile(r"We don't have one in (.+?) - this is the")


_HERES = re.compile(r"^Here['\u2019]s (.+?) in [^.]+\.")
_VERB = re.compile(r"\b(?:is|are|was|were|be|been|has|have|had|do|does|did|can|will|would|should|could|may|might|must|need|"
                   r"needs|charge|charges|cost|costs|seat|seats|include|includes|offer|offers|ask|asks|play|plays|bring|"
                   r"brings|run|runs|start|starts|get|gets|see|look|open|try|show|shows|make|makes|go|goes|take|takes|give|"
                   r"gives|add|adds|depend|depends|vary|varies|book|books|hold|holds|fit|fits|list|lists|let|lets|want|"
                   r"come|comes|keep|keeps|find|pay|pays|plan|work|works|\w+ed|\w+ing|\w+['\u2019](?:s|re|ll|ve|d))\b", re.I)
_PRICEY = re.compile(free_mode._FIGURE.pattern + r"|\$", re.I)


def _usable(s):
    """A no-card line worth showing: 5+ words, a verb, no price figure (any level - a figure only ever rides on a sourced
    price line), no price-as-quality word, not the free fallback."""
    return bool(s) and len(s.split()) >= 5 and bool(_VERB.search(s)) and not _PRICEY.search(s) \
        and not _BANNED.search(s) and not s.startswith("Here are picks")


def short_line(out, profile=None, legacy=False):
    """The one line above the cards. Says only what the cards can't. legacy: a turn stored before the switch (no
    structural marks stored with it): the text fallbacks apply."""
    cards = [c for c in out.get("cards") or [] if isinstance(c, dict)]
    lk = out.get("lookup") or {}
    status, name = lk.get("status"), str(lk.get("query") or "").strip()
    text = out.get("text") or ""
    if lk:
        if status == "elsewhere":
            m = _OUTSIDE.search(text) or _NOT_IN.search(text)
            if m and m.re is _OUTSIDE:
                return "Outside %s, where you're planning" % m.group(1)
            if m:
                return "We don't have one in %s. This is the one we have." % m.group(1)
        if status in ("found", "best", "elsewhere"):
            return "The one you asked about" if len(cards) <= 1 else "The one you asked about, and close matches"
        if status == "choices":
            return "%d match “%s”" % (len(cards), name) if name else "%d matches" % len(cards)
        if status == "dym":
            return "Did you mean this one?" if len(cards) == 1 else "Did you mean one of these?"
        if "Tulle covers US venues" in text:
            return "We don't have %s. Tulle covers US venues." % name
        return "We don't have %s." % name if name else "We don't have that one."
    miss = out.get("unmatched_name") or (typed_not_listed(text, out.get("last_user"), [c.get("name") for c in cards])
                                         if legacy else "")
    if legacy and len(cards) == 1:   # an old single-venue lookup ("Here's X in Y. ...")
        m = _HERES.match(" ".join(text.split()))
        if m and re.sub(r"\W", "", m.group(1).lower()) == re.sub(r"\W", "", str(cards[0].get("name") or "").lower()):
            return "The one you asked about"
    if miss:     # lead with the no: the cards are what we do have
        head = "We don't have %s." % miss
        rest = short_line(dict(out, text="", unmatched_name=""), profile) if cards else ""
        return head + " " + rest if rest and len((head + " " + rest).split()) <= MAX_WORDS else head
    if cards:
        cats = {c.get("category") or "Venue" for c in cards}
        n = len(cards)
        # One kind: its noun (venues have none: "3 in Brooklyn"). Mixed kinds (QA round 5, msg 5388: a venue and two DJs
        # read "3 near Manhattan for 400 guests"): "vendors", and no guest count (that is a venue search's).
        cat = cats.pop() if len(cats) == 1 else None
        noun = (_NOUN1 if n == 1 else _NOUNS).get(cat, "") if cat else ("vendor" if n == 1 else "vendors")
        (prep, area), g = _place_phrase(out, cards), _guests(out, profile)
        line = ("%d %s" % (n, noun)).strip() if noun else ("One" if n == 1 else str(n))
        if area:
            line += " %s %s" % (prep, area)
        if g and cat == "Venue":
            line += " for %d guests" % g
        return line if (area or g or noun) else ("Here it is" if n == 1 else "%d to look at" % n)
    first = first_sentence(text)
    # A "we couldn't find / don't have X" sentence is never the line (QA round 5, msg 6705: a cut-off "I couldn't find The
    # Ryland Inn among the New Jersey venues..."): it is "We don't have X." above when the couple typed X, else neutral.
    s = "" if (not_listed(first) or _SAYS_MISSING.search(first)) else clip(first)
    if not _usable(s):
        return NEUTRAL_WIDGET if out.get("widgets") else NEUTRAL_TEXT
    return s


# Our own "we don't have it" wording (any case, any subject we / I / Tulle), for the no-card line only.
_SAYS_MISSING = re.compile(r"\b(?:we|i|tulle)\s+(?:still\s+)?(?:can['’]t|couldn['’]t|could not|cannot|didn['’]t|did not|"
                           r"don['’]t|doesn['’]t|do not|does not)\s+(?:find|locate|see|have|list|carry)\b|"
                           r"\b(?:isn['’]t|is not|aren['’]t|are not)\s+(?:listed|on tulle|in tulle|one of (?:the|our))\b", re.I)


def clip(s, n=MAX_WORDS):
    """At most n words; a cut sentence ends with an ellipsis. A cut never leaves an unfinished parenthesis or a price
    without its source: that clause is dropped, and '' (= the neutral line) when too little is left."""
    w = str(s or "").split()
    if len(w) <= n:
        return " ".join(w)
    t = " ".join(w[:n])
    if t.count("(") > t.count(")"):
        t = t[:t.rfind("(")]
    if "$" in t:
        d = t.find("$")
        cut = max(t.rfind(", ", 0, d), t.rfind("; ", 0, d), t.rfind(" - ", 0, d))
        t = t[:cut] if cut > 0 else ""
    t = t.rstrip(" ,;:-")
    return t + "\u2026" if len(t.split()) >= 4 else ""


_REOPEN_KEEP = re.compile(r"\bwe (?:still )?don['\u2019]t have\b", re.I)


def reopen_line(text, last_user=None, shown=()):
    """A turn stored before the switch (no lookup kept with it): 'outside X, where you're planning' anywhere in it, or a
    'We don't have ...' first sentence (a vendor the couple typed, as live: typed_not_listed) is the line, since the card
    can't say it. None = shape as usual. last_user None = the question is not known: the wording alone."""
    m = _OUTSIDE.search(" ".join(str(text or "").split()))
    if m:
        return "Outside %s, where you're planning" % m.group(1)
    s = first_sentence(text)
    if s and _REOPEN_KEEP.search(s):
        name = typed_not_listed(s, last_user, shown)
        return ("We don't have %s." % name) if name else None   # "We don't have 2027 pricing" -> the normal line
    return None


def understood(out, profile=None):
    """{place, guests, date}; a key is left out when we don't know it."""
    import whole_wedding   # date_label only (lazy: whole_wedding imports xano)
    cards = [c for c in out.get("cards") or [] if isinstance(c, dict)]
    u = {}
    place = _area(out, cards)
    if place:
        u["place"] = place
    g = _guests(out, profile)
    if g:
        u["guests"] = g
    d = whole_wedding.date_label((profile or {}).get("Wedding_Date"))
    if d:
        u["date"] = d
    return u


def price_answer(text):
    """An answer about price for a free couple: price talk, a figure, or the plan line the price-question rule adds."""
    t = str(text or "")
    return free_mode.has_price_talk(t) or bool(_PRICEY.search(t)) or bool(re.search(r"\bForever\b", t)) \
        or t.strip() == UPGRADE_LINE or free_mode.FALLBACK_TEXT in t or free_mode.PDF_STEER in t


def upgrade_prompt():
    """The product's existing upgrade card (upsell.prompt: "See the exact prices", CTA "See plans"), for a couple with no plan."""
    import upsell
    return upsell.prompt(has_plan=False)


def tool_upgrade(name, chips=()):
    """POST /rec/tool for a free couple on a photo-first page (T0-53): a cost tool whose widget carries figures is not run;
    the answer is the upgrade line and the existing upgrade card. Chips: only ones with no price talk."""
    return {"tool": name, "text": UPGRADE_LINE, "widgets": [], "photo_first": True, "upsell_prompt": upgrade_prompt(),
            "chips": [c for c in chips or [] if not price_answer(c if isinstance(c, str) else json.dumps(c))]}


def _free_safe(text):
    """A line a free couple may read: no price talk; guest counts are fine."""
    return not free_mode.has_price_talk(text or "")


_PRICED_FOR = re.compile(r"\bfor ([\d,]+) guests\b")


def today_shows_price(card, level, card_totals=False):
    """Owner ruling T0-56 (2026-11-26): on a "Show 3 more" card photo-first shows a price line only when today's
    (switch-off) card shows that same figure. Today's card can show it in three places: the per-card total (REC_CARD_TOTALS,
    `card_totals` = the /rec/status flag for this couple), the band line (light: price_band, hidden when the reason quotes a
    figure) and the code-written reason (agent.why_lines falls back to the estimate sentence when there is no rental range)."""
    if level == "full":
        tot = card.get("estimate_total")
        if not (isinstance(tot, (int, float)) and tot > 0):
            return False
        return bool(card_totals) or _usd(tot) in str(card.get("reason") or "")
    if level == "light":
        b = card.get("price_band") or {}
        return bool(b.get("label")) and _num(b.get("high")) > 0 and not re.search(r"[$€£]\s?\d", str(card.get("reason") or ""))
    return False


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0


def apply(out, level, profile=None, reopened=False, card_totals=False):
    """The photo-first version of one answer (a new dict; `out` itself is not changed, so what is stored is the full text).
    reopened: a stored turn read back (chat_detail); one stored before the switch has no lookup with it.
    out["is_more"]: a "Show 3 more" answer (T0-56: a card's price line only when today's card shows that figure;
    card_totals = REC_CARD_TOTALS on for this couple, as /rec/status reports it)."""
    if not isinstance(out, dict) or out.get("stopped"):
        return out
    free = level == "free" or bool(out.get("price_locked"))
    hid = free and any(isinstance(w, dict) and w.get("type") in FREE_HIDDEN_WIDGETS for w in out.get("widgets") or [])
    if hid:
        # Owner rulings 2026-11-26 / T0-53: in a photo-first answer a free couple does not see a cost widget (market-
        # average dollar tiers, e.g. msgs 6686 / 6031; their budget split). Members, and every answer with the switch off, keep it.
        out = dict(out, widgets=[w for w in out["widgets"] if not (isinstance(w, dict) and w.get("type") in FREE_HIDDEN_WIDGETS)])
    legacy = reopened and not out.get("pf_stored")      # stored before the switch: no structural marks with it
    shown = [c.get("name") for c in out.get("cards") or [] if isinstance(c, dict)]
    line = (reopen_line(out.get("text"), out.get("last_user"), shown) if legacy else None) \
        or short_line(out, profile, legacy=legacy)
    if (free and not _free_safe(line)) or _BANNED.search(line) or _PRICEY.search(line):
        line = "Here are some to look at" if out.get("cards") else NEUTRAL_TEXT
    # T0-53: a free couple who asked what something costs (a cost widget held back, the price-question card, or an answer
    # about price) gets the upgrade line and the upgrade card, not "Ask about a venue or a town to see photos". Only in
    # place of that non-answer: a "We don't have X." or any other line of its own stays.
    named = bool(out.get("lookup") or out.get("unmatched_name")) or line.startswith(("We don't have", "Outside "))
    upgrade = free and not out.get("cards") and not named and (hid or bool(out.get("upsell_prompt")) or (
        price_answer(out.get("text")) and (line in (NEUTRAL_TEXT, NEUTRAL_WIDGET, UPGRADE_LINE) or "Forever" in line)))
    if upgrade:
        line = UPGRADE_LINE
    cards = []
    for c in out.get("cards") or []:
        if not isinstance(c, dict):
            cards.append(c)
            continue
        c2 = dict(c, facts=facts(c, free))
        pl = None if free else price_line(c, level)
        if pl and out.get("is_more") and not today_shows_price(c, level, card_totals):
            pl = None
        if pl:
            c2["price_line"] = pl
        cards.append(c2)
    # One guest count on screen (QA round 6, row 4562 / msg 5068: "3 near New York for 300 guests" over member lines "About
    # $141,200 for 250 guests" - Show 3 more's stored estimates were priced at 250). Re-pricing at the headline's count needs
    # every venue's panel again; the cards' figures are what we hold. So when a shown price line is for another count, the
    # headline names none ("3 near New York"); each line still names the count ITS figure is for. Free: no price lines.
    g = _guests(out, profile)
    if g and any(_PRICED_FOR.search(str((c.get("price_line") or {}).get("text") or "")) and
                 _int(_PRICED_FOR.search(str(c["price_line"]["text"])).group(1).replace(",", "")) != g
                 for c in cards if isinstance(c, dict)):
        line = line.replace(" for %d guests" % g, "")
    # The full answer is not sent (QA 2026-11-26: today's paragraph judges price in 13 of 30 member answers); it is still
    # what is stored and what the model reads back.
    res = dict(out, text=line, cards=cards, photo_first=True, understood=understood(out, profile))
    if upgrade:
        res["upsell_prompt"] = out.get("upsell_prompt") or upgrade_prompt()
    res.pop("pf_stored", None)
    res.pop("last_user", None)
    lk, vr = out.get("lookup") or {}, out.get("venue_request")
    if vr and lk.get("status") not in ("found", "best", "choices", "dym", "elsewhere"):
        res["not_found"] = {"name": vr.get("name") or lk.get("query") or "", "question": QUESTION_NEAR, "chip": CHIP_QUOTE}
    return res


# ---------------------------------------------------------------- "What it costs" (cost_lines quick action)

_COST_ROWS = (("rental", "Rental", "cal"), ("food", "Food and drink", "fork"), ("ceremony", "Ceremony", "ring"),
              ("service", "Service charge", "bell"))
_WHERE_FRAG = (("rental", ("rental", "venue fee", "site fee")), ("food", ("food", "f&b", "catering", "minimum")),
               ("ceremony", ("ceremony",)), ("service", ("service", "admin")))
WHERE_IN, WHERE_NOT = "In the PDF", "Not in this PDF"


def lock_cost(w):
    """The words-only version of a cost_lines widget (also for a stored full one shown to a couple without figures):
    rebuilt from a whitelist, so no amount can ride along."""
    rows = []
    for r in (w or {}).get("lines") or []:
        where = r.get("where") if r.get("where") in (WHERE_IN, WHERE_NOT) else (
            WHERE_IN if r.get("source") == "pdf" else WHERE_NOT)
        rows.append({"key": r.get("key"), "label": r.get("label"), "icon": r.get("icon"), "where": where})
    keep = {k: (w or {}).get(k) for k in ("vendor_id", "vendor_idx", "name", "image", "place", "guests")}
    room = (w or {}).get("room")
    keep["room"] = room if room and not _DIGIT.search(str(room)) else None
    return dict(keep, type=COST_TYPE, level="words", lines=rows)


def _where_rows(p):
    got = {}
    for l in (p or {}).get("lines") or []:
        title = str(l.get("line") or "").lower()
        for key, frags in _WHERE_FRAG:
            if any(f in title for f in frags):
                got[key] = got.get(key) or l.get("source") == "venue"
    return [{"key": k, "label": lab, "icon": ic, "where": WHERE_IN if got.get(k) else WHERE_NOT} for k, lab, ic in _COST_ROWS]


def build_cost_lines(xano, token, user, vendor_id, *, access, guests=0, default_guests=0):
    """The widget, or None when it is not a venue we list. full = an active plan AND ep230 entitlement (compare's rule)."""
    import compare
    import estimate
    ids = compare.clean_ids([vendor_id])
    card = compare._safe(xano.cached_vendor_card, ids[0]) if ids else None
    if not card or (card.get("category") or "Venue") != "Venue":
        return None
    vid = card.get("vendor_id") or ids[0]
    g = _int(guests) if 2 <= _int(guests) <= compare.MAX_GUESTS else (_int(default_guests) or 100)
    full = bool(access and xano.pi_entitled(user))
    p = compare._safe(xano.cached_venue_pricing, token, vid, full, user)
    base = {"type": COST_TYPE, "vendor_id": vid, "vendor_idx": card.get("vendor_idx"), "name": card.get("name") or "",
            "image": compare._image(card.get("image")), "place": compare._place(card), "guests": g}
    inp = (p or {}).get("cost_inputs") if full else None
    e = estimate.compute(inp, g) if inp else None
    if not (full and e):
        return lock_cost(dict(base, room=None, lines=_where_rows(p)))
    c = compare._cost(e, inp)
    by = {l["key"]: l for l in c["lines"]}
    lines = []
    for key, label, icon in _COST_ROWS:
        l = by.get(key)
        if not l:
            continue
        src = "pdf" if l["source"] == "pdf" else ("estimate" if l["source"] == "estimate" else "missing")
        row = {"key": key, "label": label, "icon": icon, "amount": l["amount"], "source": src,
               "note": "From the PDF" if src == "pdf" else ("Estimate" if src == "estimate" else "Not in this PDF")}
        if l.get("included"):
            row["included"] = True
            row["note"] = "Included" + (", from the PDF" if src == "pdf" else "")
        elif key == "food" and l.get("minimum_applies"):
            # QA round 8: held at the venue's minimum, in the Compare table's own words (VenueCompare part()): "minimum
            # spend", or "minimum, may be more" when the PDF gives only a minimum
            row["note"] += ", minimum, may be more" if l.get("minimum_only") else ", minimum spend"
            row["minimum_applies"] = True
            if l.get("minimum_only"):
                row["minimum_only"] = True
        if key == "service" and l.get("pct"):
            row["pct"] = l["pct"]
        lines.append(row)
    est = [r["label"] for r in lines if r["source"] == "estimate" and r["amount"]]
    # QA round 8: the total is marked the way Compare's total is (VenueCompare subLine): "food may be more" when the food
    # is only the PDF's minimum, "est." when any row with an amount is an estimate.
    marks = (", food may be more" if any(r.get("minimum_only") for r in lines) else "") + (", est." if est else "")
    # QA round 9 (Wainwright House, 60 asked, PDF "80 - 240 guests"): the total is priced at the count estimate.compute
    # used, so that is the count shown; below the venue's guest minimum it is worded as Compare's subLine words it
    # (VenueCompare: c.at_minimum -> "for <c.guests>, its minimum", c = compare._cost, same at_minimum/guests).
    pg, at_min = _int(c.get("guests") or e.get("guests")) or g, bool(c.get("at_minimum"))
    count = ("for %d, its minimum" % pg) if at_min else ("%d guests" % pg)
    out = dict(base, guests=pg, level="full", room=e.get("space") or None, lines=lines, total=c["total"], estimated=est,
               total_note="%s%s, before tax and tip%s" % ((e.get("space") + ", ") if e.get("space") else "", count, marks))
    if at_min:
        out["at_minimum"] = True
    return out


def cost_summary(w):
    """The line stored with the widget (no model). Compare's words (compare.summary_text): " Estimated parts are marked."
    and, below the guest minimum, "for <n>, its minimum"."""
    if w.get("level") == "full":
        count = ("for %d, its minimum" % w["guests"]) if w.get("at_minimum") else ("for %d guests" % w["guests"])
        return "What %s costs %s.%s" % (w["name"], count, " Estimated parts are marked." if w.get("estimated") else "")
    return "Where each part of %s's price is. The numbers are in its pricing PDF." % w["name"]
