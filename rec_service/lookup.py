"""Venue name lookup fast path (owner decision 2026-10-26).

The most common Planner request is ONE venue by name (~60 of ~250 typed questions, 2026-10) and about half of them
failed ("I couldn't find...") after ~12 s of model + search time: ep119 / the Railway search-api match a substring of
the name, so "Khol mansion" never finds Kohl Mansion (V1675), and the model often searched with the wrong state.

This module answers those WITHOUT the model:
  detect(text)   deterministic, conservative: is this message "look up <venue name> [in <place>]"?
  find(...)      candidate retrieval from table 11 by the normalized name key (mk_name_key, lowercase alphanumerics,
                 e.g. "kohlmansion"), then by each distinctive token, then (only if still unsure) by typo variants of
                 the most distinctive token (adjacent transpositions, plural stripped: "khol" -> "kohl"), all
                 rescored in code with token + character similarity (difflib) against the Name AND the Google name.
Pure functions except find(), which takes the search function as an argument (tests run offline).
"""
import difflib
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------- normalizing + similarity

STOP = {"the", "a", "an", "of", "at", "and", "by", "on", "in", "to", "la", "le", "el", "il"}
# Venue-type words: they say WHAT a place is, not WHICH one ("Oheka Castle" is found by "oheka"). Kept in the score
# as a small penalty when the couple named one the candidate lacks ("Royal Palm Mansion" vs "Royal Palm Manor").
GENERIC = {"venue", "venues", "hotel", "inn", "resort", "spa", "mansion", "manor", "castle", "estate", "estates",
           "garden", "gardens", "house", "hall", "club", "country", "golf", "farm", "farms", "barn", "ranch",
           "vineyard", "vineyards", "winery", "ballroom", "center", "centre", "events", "event", "lodge", "loft",
           "studio", "room", "rooms", "rooftop", "terrace", "chapel", "collection", "restaurant", "bistro", "museum",
           "space", "place", "pavilion", "villa", "retreat", "grand", "historic", "and"}

HIGH = 0.88        # one match at or above this, clearly ahead of the next -> answer with that venue
MARGIN = 0.07      # ...ahead of the runner-up by at least this
MID = 0.72         # matches at or above this (up to 3) -> "which one did you mean?"


def norm(s):
    """Lowercase, accents and apostrophes off, & -> and, everything else non-alphanumeric -> space."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def tokens(s):
    return [t for t in norm(s).split() if t not in STOP]


def key(s):
    """The comparable key: like table 11's mk_name_key (alphanumerics only), without a leading "the"."""
    n = norm(s)
    n = re.sub(r"^the ", "", n)
    return n.replace(" ", "")


def _transposed(a, b):
    if len(a) != len(b) or len(a) < 3:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def sim(a, b):
    """Token similarity 0..1. Exact 1; plural/singular 0.97; one adjacent swap ("khol"/"kohl") 0.9; else difflib."""
    if a == b:
        return 1.0
    a1, b1 = a.rstrip("s"), b.rstrip("s")
    if a1 == b1 and a1:
        return 0.97
    if _transposed(a, b) or _transposed(a1, b1):
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def _best(t, toks, floor=0.75):
    s = max((sim(t, u) for u in toks), default=0.0)
    return s if s >= floor else 0.0


# ---- name match v2 (config.NAME_MATCH_V2, 2026-11-18; #feedback 11-17, chat 2544) ----
# "Holly Hedge" / "Holly Hedge Estate" were reported missing: the listing is "HollyHedge Estate" (Pennsylvania, checked
# read-only through ep119 on 2026-11-18). The search matches whole words, so "holly hedge", "holly" and "hedge" never
# return it, and score() compares word by word, so "holly"/"hedge" vs "hollyhedge" scored low even when it was there.
# Rule: the couple's DISTINCTIVE words (stop words and venue-type words dropped), at least two of them and at least 6
# letters joined, must spell - joined, case / punctuation / spacing ignored - a run of WHOLE consecutive words of the
# stored name ("hollyhedge" = word 1 of "HollyHedge Estate"; "holly"+"hedge" = words 1-2 of "Holly Hedge Estate &
# Gardens"). Whole-word alignment means "Holly Hedge" never matches "Hollyhedgerow" or "The Holly Farm", and the
# two-distinctive-word minimum means "The", "Estate", "Barn", "Holly Estate" or "The Barn at" never over-match.
JOIN_MIN_WORDS = 2
JOIN_MIN_CHARS = 6


def _distinctive(s):
    return [t for t in tokens(s) if t not in GENERIC]


def joined_match(query, name):
    """True when the query's 2+ distinctive words, joined, equal a run of whole consecutive words of the name."""
    qd = _distinctive(query)
    if len(qd) < JOIN_MIN_WORDS:
        return False
    want = "".join(qd)
    if len(want) < JOIN_MIN_CHARS:
        return False
    ct = tokens(name)
    for i in range(len(ct)):
        run = ""
        for j in range(i, len(ct)):
            run += ct[j]
            if run == want:
                return True
            if len(run) >= len(want) or not want.startswith(run):
                break
    return False


def score(query, name, location="", v2=False):
    """How well a candidate's name matches what the couple typed (0..1). Location words they gave ("Ashton Gardens,
    Atlanta") count as covering the same words in the candidate's name ("Ashton Gardens | Atlanta")."""
    if not name:
        return 0.0
    if key(query) and key(query) == key(name):
        return 1.0
    qt, ct, lt = tokens(query), tokens(name), tokens(location)
    if not qt or not ct:
        return 0.0
    qd = [t for t in qt if t not in GENERIC] or qt
    cd = [t for t in ct if t not in GENERIC] or ct
    q_cov = sum(_best(t, ct) for t in qd) / len(qd)
    c_cov = sum(_best(t, qt + lt) for t in cd) / len(cd)
    char = difflib.SequenceMatcher(None, key(query), key(name)).ratio()
    missing = sum(1 for t in qt if t in GENERIC and t not in qd and not _best(t, ct, 0.9))
    # ...and a smaller one for a type word only the candidate has ("the Astoria" vs "Astoria Ballroom").
    extra = sum(1 for t in ct if t in GENERIC and t not in cd and not _best(t, qt + lt, 0.9))
    base = max(0.0, 0.6 * q_cov + 0.3 * c_cov + 0.1 * char - min(0.2, 0.1 * missing) - min(0.1, 0.05 * extra))
    # The whole candidate name, 2+ distinctive words, typed in order inside a longer message ("Harold Pratt House"
    # in "The Harold Pratt House and Peterson Hall"; "Royal Palm" in "royal palm mansion") is a near-certain match.
    if len(cd) >= 2 and not extra and " %s " % " ".join(cd) in " %s " % " ".join(qd):
        base = max(base, 0.9)
    if v2 and joined_match(query, name):
        base = max(base, 0.9)
    return base


def strong(query, name, v2=False):
    """Every distinctive word they typed matches a word of the name nearly exactly (exact, plural or one swap).
    A one-word message with no lookup cue must be this strong: "Khols" -> Kohl Mansion yes, "summer" -> Summerour no."""
    qt, ct = tokens(query), tokens(name)
    qd = [t for t in qt if t not in GENERIC] or qt
    if v2 and joined_match(query, name):
        return True
    return bool(qd and ct) and all(_best(t, ct, 0.9) for t in qd)


# ---------------------------------------------------------------- detection

US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia"}
STATE_ABBR = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
    "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey",
    "nm": "new mexico", "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio",
    "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia",
    "wa": "washington", "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia"}
_AMBIG_ABBR = {"in", "or", "me", "hi", "la", "ok", "de"}   # state codes that are also plain words
# Places couples answer "which area?" with. A bare place is never a venue lookup.
PLACES = {"nyc", "new york city", "manhattan", "brooklyn", "queens", "bronx", "staten island", "long island",
          "hudson valley", "catskills", "the catskills", "hamptons", "the hamptons", "westchester", "jersey shore",
          "los angeles", "la", "san francisco", "sf", "bay area", "san diego", "napa", "sonoma", "chicago",
          "houston", "dallas", "austin", "san antonio", "fort worth", "miami", "orlando", "tampa", "atlanta",
          "boston", "philadelphia", "philly", "pittsburgh", "seattle", "portland", "denver", "phoenix", "scottsdale",
          "las vegas", "nashville", "charleston", "savannah", "asheville", "new orleans", "dc", "washington dc",
          "baltimore", "detroit", "minneapolis", "st louis", "kansas city", "charlotte", "raleigh", "richmond",
          "palm springs", "santa barbara", "big sur", "hill country", "lake tahoe", "tahoe", "cape cod",
          "martha's vineyard", "upstate", "upstate new york", "north jersey", "south jersey", "international",
          "italy", "mexico", "france", "spain", "greece", "portugal", "england", "uk", "ireland", "canada"}
# A message that says any of these is a search or a follow-up, not one venue by name.
REJECT = re.compile(
    r"\b(venues?|places?|spots?|options?|cheaper|cheapest|cheap|affordable|under|below|less|budget|near|nearby|"
    r"around|within|close|more|another|other|others|similar|compare|comparing|versus|vs|best|top|recommend\w*|"
    r"suggest\w*|ideas?|which|what|why|how|when|where|should|could|would|will|help|show|me|my|our|we|us|i|you|"
    r"your|list|breakdown|break|itemi[sz]e|total|estimate|outdoor|indoor|rustic|elegant|modern|boho|style|vibe|"
    r"vibes|any|some|all|guests?|people|weddings?|elopement|photographers?|djs?|bands?|caterers?|catering|"
    r"florists?|planners?|month|year|date|saturday|friday|sunday|try|different|change|split|open|saved|"
    r"yes|no|ok|okay|thanks|thank|thx|sure|great|cool|perfect|nice|awesome|sounds|good|love|like|hmm|nope|"
    r"yeah|yep|maybe|instead|also|too|again|start|reset|hi|hello|hey)\b|\$|\d\s?k\b|\d{4,}", re.I)

_PREFIXES = [
    r"(?:hi|hey|hello)\b[,!. ]*",
    r"(?:can|could|would) you (?:please )?(?:find|show(?: me)?|look up|pull up|check(?: on)?|search(?: for)?|get"
    r"(?: me)?|give me)\s+(?:the\s+)?(?:pricing|prices?|info|details)?\s*(?:for|on|of)?\s*",
    r"(?:please )?(?:find|look ?up|pull up|search(?: for)?|check(?: on)?|show me)\s+",
    r"(?:do|does) (?:you|tulle|y'?all)(?: guys)? have\s+(?:any(?:thing)? (?:on|for)\s+|pricing (?:for|on)\s+"
    r"|info (?:on|for)\s+)?",
    r"(?:what|how much)(?:'s| is| are| does| do)?\s+(?:the\s+)?(?:pricing|price|prices|cost|costs)\s+(?:for|of|at)\s+",
    r"how much (?:is|are|does|do|for)\s+",
    r"(?:pricing|prices?|costs?|quotes?|info|information|details)\s+(?:for|of|at|on)\s+",
    r"(?:tell me about|what about|how about|info on|information on|any info on)\s+",
]
_SUFFIXES = [
    r"\s+(?:pricing|prices?|costs?|price list|rates|packages|quotes?|info|details|pdfs?)$",
    r"\s+(?:cost|charge)s?$",
    r"\s+(?:wedding )?(?:venue)$",
]
_LISTED = re.compile(r",?\s+(?:listed|available|on tulle|in tulle|on your (?:site|app|platform))\b.*$", re.I)
_GUESTS = re.compile(r"\s+for\s+(?:a|an|our|my|the)?\s*[^,]{0,40}?\b(?:wedding|guests?|people|ppl|event|reception|"
                     r"ceremony)s?\b.*$", re.I)
_CUE = re.compile(r"\b(pricing|prices?|costs?|quote|listed|look ?up|find|do you have|have pricing|how much|info|"
                  r"details|tell me about)\b", re.I)

# Words that describe the KIND of place or a need, not WHICH place (2026-11-03, chat 1170: "Only gardens and nature
# please" was read as a venue name and the couple was told "We don't have Only gardens and nature please in Tulle yet";
# same for "Ceremony only", "Halal food", "Overnight accommodations", "And one with a rooftop?"). A message made ONLY of
# these (plus filler) is a style / needs request and goes to the model, which filters on venue types and vibes.
# Real names keep at least one word outside this set ("Ashton Gardens", "Lake Katherine Nature Center").
DESCRIPTORS = {
    "garden", "gardens", "botanical", "botanic", "nature", "natural", "naturey", "scenic", "view", "views",
    "greenery", "green", "flowers", "floral", "wildflowers", "outdoors", "outside", "open", "air", "fresh",
    "waterfront", "water", "beach", "beachfront", "beachy", "lake", "lakes", "lakeside", "lakefront", "river",
    "riverside", "riverfront", "ocean", "oceanfront", "sea", "seaside", "coastal", "bay", "waterside",
    "mountain", "mountains", "mountainside", "forest", "forests", "woods", "wooded", "woodland", "woodsy", "trees",
    "park", "parks", "meadow", "field", "fields", "desert", "countryside", "nautical",
    "barn", "barns", "ranch", "farm", "farms", "rustic", "vineyard", "vineyards", "winery", "wineries", "brewery",
    "distillery", "historic", "historical", "ballroom", "ballrooms", "rooftop", "rooftops", "roof", "skyline",
    "urban", "city", "industrial", "warehouse", "loft", "lofts", "greenhouse", "glasshouse", "conservatory",
    "atrium", "glass", "tent", "tented", "courtyard", "terrace", "lawn", "patio", "chapel", "church", "castle",
    "castles", "mansion", "mansions", "estate", "estates", "cabin", "cabins", "lodge", "lodges", "tropical",
    "intimate", "small", "smaller", "large", "larger", "big", "bigger", "romantic", "whimsical", "moody", "dark",
    "classic", "classy", "chic", "luxury", "luxurious", "upscale", "fancy", "simple", "cozy", "bohemian",
    "vintage", "unique", "quirky", "minimalist", "glam", "glamorous", "fairytale", "enchanted", "magical",
    "aesthetic", "pretty", "beautiful", "gorgeous", "stunning", "light", "bright", "airy", "windows", "sunset",
    "accommodations", "accommodation", "lodging", "overnight", "onsite", "site", "stay", "stays", "rooms", "guest",
    "food", "halal", "kosher", "vegan", "vegetarian", "gluten", "free", "byob", "inclusive", "ceremony",
    "ceremonies", "reception", "receptions", "only", "just", "please", "pls", "one", "ones", "with", "without",
    "plus", "or", "and", "kind", "kinds", "type", "types", "setting", "settings", "feel", "feeling", "theme",
    "themed", "look", "looking", "for", "private", "accessible", "pet", "dog", "friendly", "parking",
    "fall", "spring", "summer", "winter", "autumn", "space", "spaces", "setup", "style", "styles"}


# Hotel / buyout words (2026-11-07, chat 1260: "what about hotel buyout?" -> "We don't have Hotel Buyout in Tulle").
DESCRIPTORS |= {"hotel", "hotels", "resort", "resorts", "inn", "inns", "buyout", "buyouts", "exclusive",
                "exclusivity", "venue", "venues", "spot", "spots"}

# A follow-up, a complaint or a correction - never one venue by name (planner log 11-03..11-06: "is that inclusive?",
# "It's stupid", "Not new york", "nevermind - disregard" were each answered "We don't have <that> in Tulle yet - I've
# passed it to our team" with three unrelated venues). Real venue names don't open with these words or contain them.
_CONVERSATIONAL = re.compile(
    r"^(?:not|no|nope|never ?mind|nvm|disregard|forget|ignore|stop|wait|actually|ugh|um+|uh+|lol|"
    r"is|are|was|were|does|do|did|can|will|has|have|its|it'?s|this|that|these|those|they|it)\b"
    r"|\b(?:stupid|dumb|useless|terrible|awful|sucks?|hate|wrong|annoying|broken|worst|confusing|disregard|"
    r"nevermind|never mind|that|this|those|these|them|it|its|it's)\b", re.I)


def conversational(text):
    """True when the message reads as a follow-up / complaint / correction rather than a venue's name."""
    return bool(_CONVERSATIONAL.search(" ".join(str(text or "").replace("’", "'").split())))


# "South Lake Tahoe", "Northern Virginia", "upper east side": a direction word plus a place is still a place.
_DIRECTION = re.compile(r"^(?:north|south|east|west|northern|southern|eastern|western|central|upper|lower|"
                        r"greater|downtown|coastal|northeast|northwest|southeast|southwest)\s+(.+?)(?:\s+area)?$")


def _descriptive_only(name):
    """True when every word of the would-be name says what KIND of place (or a need), none says which one."""
    ws = tokens(name)
    return bool(ws) and all(w in DESCRIPTORS for w in ws)


def _place(s):
    n = norm(s)
    n = re.sub(r"\s+area$", "", n)
    # "Orange County", "Jefferson Parish". Not "valley": Bear Brook Valley is a venue (Kate, 2026-11-01).
    if re.search(r"\b(?:county|parish|metro)$", n) and len(n.split()) <= 4:
        return True
    m = _DIRECTION.match(n)
    if m and _place(m.group(1)):
        return True
    # "Houston texas", "austin, tx" (planner log 11-05: "Houston texas" got three venues with Houston in the name and
    # "which one did you mean?"; the couple's next message was a complaint). A known place plus its state is a place.
    w = n.split()
    for k in (2, 1):
        tail = " ".join(w[-k:])
        if len(w) > k and (tail in US_STATES or (k == 1 and tail in STATE_ABBR)) and _place(" ".join(w[:-k])):
            return True
    return n in US_STATES or n in STATE_ABBR or n in PLACES or n.replace(" ", "") in {p.replace(" ", "") for p in PLACES}


# Destination-wedding places outside Tulle's coverage (2026-11-02, chat 929: "Rosewood Bermuda" got "tell me the city"
# twice and the couple left). A venue or location naming one of these gets the honest "Tulle covers US venues" line
# instead of a search of their profile state. Hawaii, Alaska and Puerto Rico are not here on purpose.
DESTINATIONS = {
    "bermuda", "bahamas", "jamaica", "mexico", "cabo", "cancun", "tulum", "riviera maya", "punta cana", "dominican",
    "costa rica", "belize", "aruba", "barbados", "st lucia", "saint lucia", "turks", "caicos", "antigua", "cayman",
    "italy", "tuscany", "amalfi", "lake como", "france", "paris", "provence", "spain", "portugal", "greece",
    "santorini", "mykonos", "ireland", "scotland", "england", "london", "iceland", "croatia", "switzerland",
    "austria", "germany", "bali", "thailand", "japan", "fiji", "tahiti", "bora bora", "maldives", "dubai", "morocco",
    "south africa", "australia", "new zealand", "canada", "toronto", "vancouver", "montreal", "europe", "caribbean"}


def destination_in(text):
    """The destination word a message names ("Rosewood Bermuda" -> "bermuda"), or ""."""
    t = " " + norm(text) + " "
    for d in sorted(DESTINATIONS, key=len, reverse=True):
        if " " + d + " " in t:
            return d
    return ""


def state_of(location):
    """'Brooklyn, NY' -> 'New York'; 'texas' -> 'Texas'; 'Lake Como, Italy' -> 'International'; else ''."""
    n = norm(location)
    parts = [p.strip() for p in re.split(r",", str(location or "")) if p.strip()] or [n]
    for p in reversed(parts):
        pn = norm(p)
        if pn in US_STATES:
            return pn.title().replace(" Of ", " of ")
        if pn in STATE_ABBR:
            return STATE_ABBR[pn].title().replace(" Of ", " of ")
    if any(w in n.split() or w == n for w in ("italy", "mexico", "france", "spain", "greece", "portugal", "england",
                                                "uk", "ireland", "canada", "international", "como")):
        return "International"
    if n in ("brooklyn", "manhattan", "queens", "bronx", "nyc", "new york city", "hudson valley", "catskills",
             "hamptons", "long island", "westchester", "staten island"):
        return "New York"
    return ""


# Vendor-type words (2026-11-07): "Bloom florist", "Jane Doe Photography", "DJ Mike" name ONE vendor of that type.
# The word is taken off for detection (REJECT would read "florist" as a search) and becomes the category to search.
VENDOR_WORDS = [
    ("Photography", r"photographers?|photography|photo|videographers?|videography|video|films?"),
    ("Entertainment", r"djs?|bands?|entertainment|musicians?|music|strings|quartet"),
    ("Flowers", r"florists?|floral|flowers?"),
    ("Food and Beverage", r"caterers?|catering|bakery|cakes?|bar service"),
    ("Beauty", r"makeup|make up|hair|beauty|mua|salon"),
    ("Wedding Planner", r"planners?|planning|coordinators?|coordination|events? co"),
]
_VW = re.compile(r"^(?:(?P<pre>%s)\s+)?(?P<core>.+?)(?:\s+(?P<post>%s))?$" % (
    "|".join(p for _, p in VENDOR_WORDS), "|".join(p for _, p in VENDOR_WORDS)), re.I)


def vendor_hint(name):
    """('core name', 'Photography') when the name starts or ends with a vendor-type word, else (name, '')."""
    m = _VW.match(" ".join(str(name or "").split()))
    if not m or not (m.group("pre") or m.group("post")):
        return name, ""
    word = (m.group("post") or m.group("pre")).lower()
    cat = next((c for c, p in VENDOR_WORDS if re.fullmatch(p, word, re.I)), "")
    core = m.group("core").strip(" ,.-:")
    # "DJs with a photo booth", "photographers for elopements", "florist near me": a search, not a business name.
    if re.search(r"\b(?:with|for|who|that|which|near|under|over|around)\b", core, re.I):
        return name, ""
    return (core, cat) if cat and tokens(core) else (name, "")


# Area / location constraints (v2, 2026-11-18, chat 2565): "Stick to the Miami area, no further north than Davie. No
# further south than Homestead." and "The cities include Davie, Miramar, Pembroke Pines, Hialeah, ..." were each read as
# a venue name and answered "We don't have ... in Tulle yet". Each pattern is something a venue name doesn't say.
# "area" counts only at the end of a phrase ("the Miami area,"), so "Area Four" and "Bay Area Discovery Museum" pass.
_AREA = re.compile(
    r"\b(?:no|not)\s+(?:further|farther|more)\s+(?:north|south|east|west)\b"
    r"|\b(?:further|farther)\s+(?:north|south|east|west)\s+than\b"
    r"|\b(?:north|south|east|west)\s+of\s+(?!the\b)\w"
    r"|\b(?:cities|towns|areas|neighbou?rhoods|counties|suburbs|regions)\s+(?:include|including|like|such as|are)\b"
    r"|\b(?:within|inside)\s+(?:an?\s+)?\d+\s*(?:miles?|mi|minutes?|mins?|hours?|hrs?|km)\b"
    r"|\b\d+\s*(?:miles?|mi|minutes?|mins?|km)\s+(?:from|of|away)\b"
    r"|\b(?:stick|keep it|stay|staying)\s+(?:to|in|within|around|near|close to)\b"
    r"|\w\s+area\b(?=\s*(?:[,.;:!?]|$))", re.I)


def area_request(text):
    """True when the message reads as an area / location constraint rather than one venue's name: one of _AREA's
    phrases, or a list of 4+ short comma-separated items ("Davie, Miramar, Pembroke Pines, Hialeah"). Short = at most
    3 words, so "Il Sereno, the hotel in Torno on Lake Como, Italy, listed ...?" (3 commas) is still a name."""
    t = " ".join(str(text or "").replace("’", "'").split())
    if _AREA.search(t):
        return True
    items = [x.strip(" .?!") for x in re.split(r"\s*,\s*|\s+(?:and|or)\s+", t)]
    return sum(1 for x in items if x and len(x.split()) <= 3) >= 4


# ---- LOOKUP V3 (config.LOOKUP_V3, 2026-11-25): edits and sentences ----
# Opens like an instruction to change or do something. Real names can open with one of these too ("Swan House" does not,
# "Book Cadillac" does), so it never blocks a lookup: the message is only answered when the named vendor IS found.
_EDIT = re.compile(r"^(?:please\s+|can you\s+|just\s+|pls\s+)?(?:remove|drop|delete|cancel|swap|switch|replace|change|update|"
                   r"edit|add|include|exclude|skip|keep|take|get rid|undo|unsave|save|move|mark|set|put|book|send|email|"
                   r"call|text|remind|schedule|write|draft|make|create|build|plan|turn|give|tell|forget|don'?t|do not)\b", re.I)
_PERSON = re.compile(r"\b(?:i|i'?m|i'?ve|i'?d|we|we'?re|we'?ve|my|our|us|me|you|your)\b", re.I)
_SERVICE_ONLY = re.compile(r"^(?:(?:a|an|the|wedding|local|good)\s+)*(?:%s)$" % "|".join(p for _, p in VENDOR_WORDS), re.I)
NON_VENDOR = {"dessert", "desserts", "pictures", "picture", "pics", "photos", "images", "reviews", "review", "map",
              "menu", "menus", "cake", "cakes", "flowers", "music", "invitations", "dj", "djs", "band", "bands",
              "photographer", "photographers", "videographer", "videographers", "florist", "florists", "wedding",
              "weddings", "rehearsal", "dinner", "brunch", "shower", "party", "engagement", "bachelorette", "cash", "block",
              "makeup", "makeups", "hair", "honeymoon", "afterparty", "cocktail", "hour", "vendor", "vendors", "guest", "guests", "free", "more", "link", "links", "pdf", "pdfs"}


def _soft(raw, cue):
    """Reads like an edit or a sentence: answered by the lookup only when a vendor by that name is really found."""
    t = _clean(raw)
    return bool(_EDIT.match(t) and not cue) or bool(_PERSON.search(re.sub(r"(?i)^.*?\b(?:do you have|tell me about|show me|look up|"
                r"find me|can you \w+|could you \w+)\b", "", t)))


# Regions couples type that PLACES lacks (v3 only).
V3_PLACES = {"finger lakes", "poconos", "berkshires", "adirondacks", "outer banks", "smoky mountains", "lake george", "lake geneva",
             "napa valley", "sonoma valley", "central valley", "shenandoah valley", "brandywine valley"}


def _nothing_named(core, check):
    """No word left that could name a business: "dessert", "pictures", "gluten free wedding cake", "videographer"."""
    ws = tokens(_clean(check)) or tokens(_clean(core))
    need = lambda w: (w in NON_VENDOR or w in NON_VENUE or w in DESCRIPTORS or
                      (len(w) >= 6 and any(w[0] == n[0] and sim(w, n) >= 0.9 for n in NON_VENUE if len(n) >= 6)))   # "mutiday"
    return bool(_SERVICE_ONLY.match(norm(core))) or (bool(ws) and all(need(w) for w in ws))


def detect(text, last_assistant="", v2=False, guard=False, dym=False, v3=False):
    """{"name", "location", "alt_name", "alt_location", "cue"} when the message looks like "look up <venue name>",
    else None. Conservative on purpose: anything that reads like a search or a follow-up goes to the model.
    last_assistant: the previous assistant message - a bare reply to its question ("Austin") is never a lookup.
    guard (config.LOOKUP_GUARD) / dym (config.DID_YOU_MEAN), 2026-11-18: see the block at the end of this module.
    Both False = exactly as before."""
    raw = " ".join(str(text or "").split())
    if not raw or len(raw) > 200:
        return None
    if v2 and area_request(raw):
        return None          # an area / location constraint: the model's area search reads it (chat 2565)
    if guard and non_venue_text(raw):
        return None          # a question / complaint / "I'm looking for ..." / an area: the model reads it
    if conversational(raw) and not _CUE.search(raw):
        return None
    # "Is Il Sereno ... listed in Tulle's vendor directory? What is its pricing?" -> the first sentence.
    first = re.split(r"[?!]\s+", raw)[0].strip()
    s = first.rstrip("?!. ")
    cue = bool(_CUE.search(raw))
    listed = _LISTED.search(s)
    if listed:
        s = s[:listed.start()]
        s = re.sub(r"^(?:is|are)\s+", "", s, flags=re.I)
    s = _GUESTS.sub("", s)
    for _ in range(2):
        for p in _PREFIXES:
            s = re.sub(r"^" + p, "", s, flags=re.I)
        for p in _SUFFIXES:
            s = re.sub(p, "", s, flags=re.I)
        s = s.strip(" ,.-:")
    if guard or dym:
        # "show me the pricing for the sapphire estate in sharon massachusetts" (11-12): "the pricing for" stayed in
        # the name because the prefix list expects "pricing for" at the start.
        s = re.sub(r"^the\s+(?:pricing|prices?|costs?|price list|info|details|rates)\s+(?:for|of|at|on)\s+", "", s,
                   flags=re.I).strip(" ,.-:")
    if not s:
        return None
    # Location: "Bacchus, Brooklyn" / "Il Sereno, the hotel in Torno on Lake Como, Italy" / "Bacchus in Brooklyn".
    name, location = s, ""
    if "," in s:
        name, tail = s.split(",", 1)
        tail = tail.strip()
        m = re.match(r"^(?:the|a|an)\s+[\w-]+\s+(?:in|on|at|near)\s+(.+)$", tail, re.I)
        location = (m.group(1) if m else tail).strip(" ,.")
        m = re.match(r"^(.+?)\s+in\s+(.+)$", name, re.I)   # "Cresthollow in Long Island, NY"
        if m and len(m.group(2).split()) <= 4:
            name, location = m.group(1), "%s, %s" % (m.group(2), location)
    else:
        m = re.match(r"^(.+?)\s+in\s+(.+)$", s, re.I)
        if m and len(m.group(2).split()) <= 4:
            name, location = m.group(1), m.group(2)
    name = name.strip(" ,.-:")
    # "the Astoria at Houston" - " at " is also part of real names ("The Barn at Forest Hill"), so it is only an
    # alternative reading, tried alongside the full name.
    alt_name, alt_location = "", ""
    m = re.match(r"^(.+?)\s+at\s+(.+)$", name, re.I)
    if m and not location and len(m.group(2).split()) <= 3:
        alt_name, alt_location = m.group(1), m.group(2)
    # "Bear brook nj" / "The Mill new jersey": a bare state at the end of the name, no comma or "in" (Kate,
    # 2026-11-01: a couple typed that and was told we don't have it; Bear Brook Valley scored 0.67 with "nj" counted
    # as part of the name). An alternative reading only - the full name is still tried - and never for an
    # abbreviation that is also a word a venue name can end with ("in", "or", "me", "hi", "la", "ok", "de").
    if not location and not alt_name:
        nw = name.split()
        for k in (2, 1):
            if len(nw) > k:
                tail = norm(" ".join(nw[-k:]))
                rest = [t for t in tokens(" ".join(nw[:-k])) if t not in GENERIC]   # "Hotel Washington": no alt
                if rest and (tail in US_STATES or (k == 1 and tail in STATE_ABBR and tail not in _AMBIG_ABBR)):
                    alt_name, alt_location = " ".join(nw[:-k]).strip(" ,.-:"), " ".join(nw[-k:])
                    break
    if dym:
        if location:
            location = place_hint(location)          # "sharon massachusetts" -> "sharon, Massachusetts"
        elif not alt_name:
            alt_name, alt_location = city_tail(name)  # "fox theatre atl" -> "fox theatre" + "Atlanta, Georgia"
    words = norm(name).split()
    if not words or len(words) > 7 or len(norm(name)) < 3:
        return None
    core, category = vendor_hint(name)
    # Vendor names often hold "Wedding(s)" ("The Post Wedding Photographer", "Expressway Music Wedding DJ NYC"): with
    # a vendor-type word present it is part of the name, not a search word - as long as another word is left.
    check = core
    if category:
        check = re.sub(r"(?i)\bweddings?\b", " ", core).strip()
        if not tokens(check):
            return None          # "wedding photographer" = a search
    if REJECT.search(check) or _place(core) or _descriptive_only(core) or conversational(core):
        return None
    if guard and non_venue_name(core):
        return None          # "Pricing", "bar tender", "estate multi day", "Texas city"
    only_exact = False
    if v3 and re.sub(r"^the ", "", norm(core)) in V3_PLACES:
        return None
    if v3 and _nothing_named(core, check):
        only_exact = True    # "dessert", "pictures", "hair and makeup": the model reads it unless a vendor is called just that
    if category and len(norm(core).split()) == 1 and len(norm(core)) < 4:
        return None          # "DJ me", "photo op": too short to be a business name
    after_question = False
    if not cue:
        if len(words) == 1 and len(words[0]) < 5:
            return None
        # A bare reply to our own question ("Austin", "Garden weddings") used to be dropped here. Every Planner
        # answer ends with a question, so "Bear brook nj" typed mid-chat never reached the lookup and the model said
        # "Bear Brook has no venues listed" (owner, chat 926, 2026-11-01). Now it is flagged: app.py shows the venue
        # only when the search actually finds one and otherwise hands the text to the model exactly as before.
        if (last_assistant or "").strip().endswith("?"):
            after_question = True
    out = {"name": name, "location": location, "alt_name": alt_name, "alt_location": alt_location, "cue": cue,
           "words": len(words), "after_question": after_question, "category": category}
    if v2:
        out["v2"] = True      # find / resolve use the joined-words match (absent = exactly as before)
    if dym:
        out["dym"] = True     # find keeps its candidate pool and tries dropped-letter spellings (absent = as before)
    if v3:
        out["v3"] = True
        if only_exact:
            out["soft"] = "exact"
        elif _soft(raw, cue):
            out["soft"] = "edit"    # only a real find is answered; anything else goes to the model
        elif alt_location and not location and not cue:
            out["soft"] = "place"   # "Algonquin Illinois" is as often a town: never "we don't have it"
    return out


# ---------------------------------------------------------------- retrieval + ranking

def _variants(tok):
    """Typo variants of one token: plural stripped, and every adjacent transposition (of both)."""
    out = []
    for base in dict.fromkeys([tok, tok.rstrip("s")]):
        if len(base) < 4:
            continue
        out.append(base)
        out += [base[:i] + base[i + 1] + base[i] + base[i + 2:] for i in range(len(base) - 1)
                if base[i] != base[i + 1]]
    return list(dict.fromkeys(out))


def first_queries(name, alt_name="", v2=False):
    """Stage 1 key searches: the whole name key, then each distinctive token (longest first)."""
    qs = []
    for n in (name, alt_name):   # the whole name as words ("kohl mansion"): the search matches whole words
        w = " ".join(tokens(n))
        if len(key(n)) >= 3 and w:
            qs.append(w)
    # The name's distinctive words in order, without type words ("royal palm" for "Royal palm mansion"; "harold
    # pratt" for "The Harold Pratt House and Peterson Hall"): the search ANDs words, and most clicked first means a
    # lone common word ("royal") can push the venue off the page (2026-10-26 live check).
    od = [t for t in tokens(name) if t not in GENERIC]
    if len(od) >= 2:
        qs += [" ".join(od[:2]), " ".join(od)]
    d = [t for t in tokens(name) if t not in GENERIC and (len(t) >= 4 or any(c.isdigit() for c in t))]
    qs += sorted(d, key=len, reverse=True)[:3]
    if v2 and len(od) >= JOIN_MIN_WORDS and len("".join(od)) >= JOIN_MIN_CHARS:
        # The distinctive words joined ("hollyhedge" for "Holly Hedge Estate" -> HollyHedge Estate): the search
        # matches whole words, so a listing that spells them as one word is only found this way. Kept inside the
        # same 6-search round (it replaces the last single-word search when the round is full).
        joined = "".join(od[:2]) if len(od) > 2 else "".join(od)
        qs = list(dict.fromkeys(qs))
        if joined not in qs:
            qs = qs[:5] + [joined]
    return list(dict.fromkeys(qs))[:6]


def typo_queries(name, done=()):
    """Stage 2 (only when stage 1 found nothing confident): typo variants of the two most distinctive tokens."""
    d = sorted([t for t in tokens(name) if t not in GENERIC and len(t) >= 4], key=len, reverse=True)[:2]
    qs = [v for t in d for v in _variants(t) if v not in done]
    return list(dict.fromkeys(qs))[:8]


def _rank(rows, q):
    """[(score, row)] best first, one per vendor."""
    best = {}
    for r in rows:
        vid = r.get("vendor_id")
        if not vid:
            continue
        v2 = bool(q.get("v2"))
        s = max(score(q["name"], r.get("name"), q.get("location"), v2),
                score(q["name"], r.get("google_name"), q.get("location"), v2))
        if q.get("alt_name"):
            s = max(s, score(q["alt_name"], r.get("name"), q.get("alt_location"), v2),
                    score(q["alt_name"], r.get("google_name"), q.get("alt_location"), v2))
        loc = tokens(q.get("location") or q.get("alt_location") or "")
        if loc:
            hay = set(tokens("%s %s" % (r.get("address") or "", r.get("state") or "")))
            if all(_best(t, hay, 0.9) for t in loc if t not in STATE_ABBR) or state_of(
                    q.get("location") or q.get("alt_location")) == (r.get("state") or "") != "":
                s = min(1.0, s + 0.06)    # in the place they named: a tie-break, never a match on its own
        if q.get("solo") and not (strong(q["name"], r.get("name"), v2) or strong(q["name"], r.get("google_name"), v2)):
            continue
        if s > best.get(vid, (0, None))[0]:
            best[vid] = (s, r)
    return sorted(best.values(), key=lambda x: -x[0])


def decide(ranked):
    """('found', [row]) | ('choices', rows[:3]) | ('not_found', [])."""
    if not ranked:
        return "not_found", []
    top = ranked[0][0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if top >= HIGH and top - second >= MARGIN:
        return "found", [ranked[0][1]]
    close = [r for s, r in ranked if s >= MID and s >= top - 0.15][:3]
    if close:
        return "choices", close
    return "not_found", []


def _promote_single(q, status, matches):
    """ONE close match whose name holds every distinctive word they typed (nearly exactly) is the venue, not a
    question (planner log 11-05: "Dinolfos" got "The closest match we have is DiNolfo's Banquets of Homer Glen - is
    that the one you meant?" three times; the couple retyped instead of tapping). The candidate's extra words
    ("Banquets of Homer Glen") are what kept its score under HIGH."""
    if status != "choices" or len(matches) != 1:
        return status, matches
    m = matches[0]
    names = [q.get("name") or ""] + ([q["alt_name"]] if q.get("alt_name") else [])
    v2 = bool(q.get("v2"))
    if any(strong(n, m.get("name"), v2) or strong(n, m.get("google_name"), v2) for n in names if n):
        return "found", matches
    return status, matches


def find(q, search, workers=6):
    """q: detect() output. search(term) -> rows (each with vendor_id, name, google_name, address, state); it may
    raise. Returns {"status", "matches", "queries", "scores"}. Stage 2 (typo variants) runs only when stage 1 is not
    confident, so a correctly spelled name costs one parallel round of Xano calls."""
    q = dict(q, solo=(not q.get("cue")) and q.get("words", 2) == 1)
    rows, done = [], []

    def run(terms):
        """One parallel round. Raises LookupFailed when EVERY search in it failed (Xano down): the caller then
        falls back to the model - "we don't have it" must never be said because our own lookup broke."""
        errors = []

        def one(term):
            try:
                return search(term) or []
            except Exception as e:
                errors.append(e)
                return []
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(terms)))) as ex:
            for got in ex.map(one, terms):
                rows.extend(got)
        done.extend(terms)
        if terms and len(errors) == len(terms):
            raise LookupFailed(str(errors[0])[:200])

    terms = first_queries(q["name"], q.get("alt_name"), bool(q.get("v2")))
    if not terms:
        return {"status": "not_found", "matches": [], "queries": [], "scores": []}
    run(terms)
    ranked = _rank(rows, q)
    status, matches = decide(ranked)
    if status != "found":
        extra = typo_queries(q["name"], done)
        if extra:
            run(extra)
            ranked = _rank(rows, q)
            status, matches = decide(ranked)
    if q.get("v3") and status != "found" and not any(holds_all(q, m) for m in _unique(rows)):
        extra = spelling_queries(q["name"], done)
        if extra:
            try:
                run(extra)
            except LookupFailed:
                pass
            ranked = _rank(rows, q)
            status, matches = decide(ranked)
    if q.get("dym") and status != "found" and not dym_candidates(q, _unique(rows)):
        # Stage 3 (REC_DID_YOU_MEAN): one letter dropped from the most distinctive word ("rhyland" -> "ryland"). A
        # failure here never turns into "we don't have it" - the earlier rounds already answered.
        extra = deletion_queries(q["name"], done)
        if extra:
            try:
                run(extra)
            except LookupFailed:
                pass
            ranked = _rank(rows, q)
            status, matches = decide(ranked)
    status, matches = _promote_single(q, status, matches)
    out = {"status": status, "matches": matches, "queries": list(done),
           "scores": [round(s, 3) for s, _ in ranked[:5]],
           "near": [r.get("name") for s, r in ranked[:3] if r.get("name")],
           # the venue log's best match + alternatives (2026-11-18; read only by venue_log)
           "ranked": [{"vendor_id": r.get("vendor_id"), "name": r.get("name"), "state": _st(r.get("state")),
                       "score": round(s, 3)} for s, r in ranked[:4]]}
    if q.get("dym"):
        out["pool"] = _unique(rows)
    return out


def _unique(rows):
    seen, out = set(), []
    for r in rows:
        vid = r.get("vendor_id")
        if vid and vid not in seen:
            seen.add(vid)
            out.append(r)
    return out


# ---------------------------------------------------------------- one answer instead of "which one?" (2026-11-07)
# Planner log 11-03..11-06: 22 "which one did you mean?" answers - 11 couples left right away, not one tapped a name,
# 2 opened a PDF. 21 "found" answers - 9 opened a PDF next. So when one match is the likely one, answer with it and
# offer the others by name underneath.
LEAD = 0.04        # the top match is the guess when it leads the next by this much (replayed on the log's cases)


def _st(s):
    return s[0] if isinstance(s, list) and s else (s or "")


def _in_place(m, location):
    """The venue is in the place the couple named: same state, or every word of the place is in its address."""
    st = state_of(location)
    if st and st == _st(m.get("state")):
        return True
    lt = [t for t in tokens(location) if t not in STATE_ABBR and t not in US_STATES]
    hay = set(tokens("%s %s" % (m.get("address") or "", _st(m.get("state")))))
    return bool(lt) and all(_best(t, hay, 0.9) for t in lt)


def resolve(q, res, prefer_states=()):
    """find() output -> the same dict with status:
      found    every match is the same venue listed twice ("The Breakers on the Ocean" / "Breakers on the Ocean")
      best     matches[0] is the likely venue - the only one in the couple's wedding state, or clearly ahead on
               score - and the rest are offered by name
      unsure   one bare word with several equal matches ("aspen", "North"): the model reads it (often a place)
      choices  several equal matches, as before
    Anything other than "choices" passes through unchanged. Pure."""
    if res.get("status") != "choices" or not res.get("matches"):
        return res
    ms = list(res["matches"])
    # No real match (2026-11-07): the couple named a place and none of these is there ("The Ritz-Carlton Club, Aspen
    # Highlands" -> Ritz-Carltons in Boston, Atlanta and California), or none holds every distinctive word they typed.
    # Then it is a venue we don't have - said honestly, with venues near the place they named (app.py) - not a quiz.
    loc = q.get("location") or ""
    if loc:
        here = [m for m in ms if _in_place(m, loc)]
        if not here:
            return dict(res, status="not_found", matches=[])
        ms = here
    names = [n for n in (q.get("name"), q.get("alt_name")) if n]
    v2 = bool(q.get("v2"))
    if not any(strong(n, m.get("name"), v2) or strong(n, m.get("google_name"), v2) for m in ms for n in names):
        return dict(res, status="not_found", matches=[])
    # Every word they typed, the venue-type word included ("Ashton GARDENS"): those matches beat ones sharing only the
    # distinctive word (2026-11-07 live: "Ashton gardens" listed Ashton Farm and Ashton Place as matches).
    full = [m for m in ms if any(all(_best(t, tokens(m.get(f) or ""), 0.9) for t in tokens(n))
                                 or (v2 and joined_match(n, m.get(f) or ""))
                                 for n in names for f in ("name", "google_name"))]
    if full:
        ms = full
    if len(ms) == 1:
        return dict(res, status="found", matches=ms)
    res = dict(res, matches=ms)
    if len({key(m.get("name")) for m in ms}) == 1:
        return dict(res, status="found", matches=ms[:1])
    want = {str(s).strip().lower() for s in prefer_states if s}
    if want:
        mine = [m for m in ms if str(_st(m.get("state"))).strip().lower() in want]
        if len(mine) == 1:
            return dict(res, status="best", matches=mine + [m for m in ms if m is not mine[0]])
    sc = list(res.get("scores") or [])
    if len(sc) >= 2 and sc[0] - sc[1] >= LEAD:
        return dict(res, status="best")
    if not q.get("cue") and q.get("words", 2) == 1:
        return dict(res, status="unsure")
    return res


class LookupFailed(Exception):
    pass


# ================================================================ 2026-11-18 (owner's 14-day Planner review)
# ---- LOOKUP GUARD (config.LOOKUP_GUARD) + the Slack rule (config.VENUE_LOG) share these. ----
# Words that say what a couple NEEDS or ASKS, never which venue: a message made only of these (plus the descriptor
# words above) is not a venue name ("Pricing", "bar tender", "estate multi day").
NON_VENUE = {
    "pricing", "price", "prices", "priced", "cost", "costs", "quote", "quotes", "info", "information", "details",
    "budget", "deposit", "contract", "availability", "available", "bartender", "bartenders", "bar", "tender",
    "officiant", "officiants", "catering", "caterer", "rentals", "rental", "decor", "cake", "dress", "menu",
    "package", "packages", "minimum", "minimums", "fee", "fees", "tax", "tip", "tips", "gratuity", "tasting",
    "shuttle", "transportation", "curfew", "multi", "multiday", "day", "days", "weekend", "weekday", "weekdays",
    "date", "dates", "time", "times", "option", "options", "looking", "im", "need", "needs", "want", "wants"}
_QSTART = re.compile(r"^(?:is|are|was|were|does|do|did|can|could|will|would|should|has|have|what|whats|how|why|"
                     r"when|where|which|who|any|anything)\b", re.I)
_SEEKING = re.compile(r"^(?:i'?m|im|i am|we'?re|were|we are|i|we)\s+(?:looking|searching|hoping|trying|want|need)\b",
                      re.I)
_AREAS = re.compile(r"\b(?:surrounding|surounding|serounding|nearby)\s+areas?\b|\bareas\b", re.I)
_STATE_PLACE = re.compile(r"^(?:%s)\s+(?:city|county|area|region|state)$" % "|".join(sorted(US_STATES, key=len,
                                                                                           reverse=True)))


def _clean(text):
    """Apostrophes as typed on phones (curly, or mangled to U+FFFD on the way in) -> '."""
    return " ".join(str(text or "").replace("�", "'").replace("’", "'").split())


def non_venue_text(text):
    """True when the whole message is a question, a complaint, a "we're looking for ..." search or an area - never a
    venue's name - and it has no lookup cue ("pricing", "do you have", "listed", ...)."""
    t = _clean(text)
    if not t:
        return False
    cue = bool(_CUE.search(t))
    if conversational(t) and not cue:
        return True
    if _QSTART.match(t) and not cue and not re.match(r"(?i)^(?:what|how) about\b", t):
        return True          # "what about / how about <name>" stays a lookup (a _PREFIXES intro)
    if _SEEKING.match(t) and not cue:
        return True
    return bool(_AREAS.search(t))


def non_venue_name(name):
    """True when the would-be name holds no word that could name a venue: only need / ask words and descriptors, or
    a state + "city" ("Texas city")."""
    ws = tokens(_clean(name))
    if not ws:
        return True
    if all(w in NON_VENUE or w in DESCRIPTORS for w in ws):
        return True
    return bool(_STATE_PLACE.match(norm(name)))


def slack_worthy(name, text):
    """The "Venue not in Tulle" notice (config.VENUE_LOG on): 2+ name-like words, at least one of them distinctive,
    and the message is not a question / complaint. "Madonna inn" yes; "Pricing", "It's stupid", "Oheka" no."""
    if non_venue_text(text) or non_venue_name(name):
        return False
    ws = [w for w in tokens(_clean(name)) if w not in NON_VENUE and len(w) >= 2]
    if len(ws) < 2:
        return False
    return not all(w in DESCRIPTORS or w in GENERIC for w in ws)


# ---- DID YOU MEAN (config.DID_YOU_MEAN) ----
# City short forms and big-city names couples tack onto a venue ("Fox Theatre atl", "Public nyc", "The Thompson
# houston"). Read as an ALTERNATIVE place, like the trailing state - the full typed name is still tried.
CITY_HINTS = {
    "atl": "Atlanta, Georgia", "atlanta": "Atlanta, Georgia", "nyc": "New York, New York",
    "manhattan": "New York, New York", "brooklyn": "Brooklyn, New York", "philly": "Philadelphia, Pennsylvania",
    "philadelphia": "Philadelphia, Pennsylvania", "chi": "Chicago, Illinois", "chicago": "Chicago, Illinois",
    "sf": "San Francisco, California", "san francisco": "San Francisco, California", "dc": "Washington, District of Columbia",
    "nola": "New Orleans, Louisiana", "new orleans": "New Orleans, Louisiana", "atx": "Austin, Texas",
    "austin": "Austin, Texas", "htx": "Houston, Texas", "houston": "Houston, Texas", "dfw": "Dallas, Texas",
    "dallas": "Dallas, Texas", "stl": "St. Louis, Missouri", "kc": "Kansas City, Missouri", "slc": "Salt Lake City, Utah",
    "phx": "Phoenix, Arizona", "phoenix": "Phoenix, Arizona", "vegas": "Las Vegas, Nevada",
    "las vegas": "Las Vegas, Nevada", "boston": "Boston, Massachusetts", "miami": "Miami, Florida",
    "nashville": "Nashville, Tennessee", "denver": "Denver, Colorado", "seattle": "Seattle, Washington",
    "san diego": "San Diego, California", "charleston": "Charleston, South Carolina", "savannah": "Savannah, Georgia",
    "pittsburgh": "Pittsburgh, Pennsylvania", "baltimore": "Baltimore, Maryland", "detroit": "Detroit, Michigan",
    "minneapolis": "Minneapolis, Minnesota", "orlando": "Orlando, Florida", "tampa": "Tampa, Florida"}


def place_hint(location):
    """A typed place the state reader can use: "atl" -> "Atlanta, Georgia"; "sharon massachusetts" -> "sharon,
    Massachusetts" (state_of reads the part after the last comma); anything else unchanged."""
    n = norm(location)
    if n in CITY_HINTS:
        return CITY_HINTS[n]
    if state_of(location):
        return location
    w = n.split()
    for k in (2, 1):
        tail = " ".join(w[-k:])
        if len(w) > k and (tail in US_STATES or (k == 1 and tail in STATE_ABBR and tail not in _AMBIG_ABBR)):
            return "%s, %s" % (" ".join(w[:-k]), (STATE_ABBR.get(tail) or tail).title())
    return location


def city_tail(name):
    """("fox theatre", "Atlanta, Georgia") for "fox theatre atl"; ("", "") when the name does not end in a known city
    or nothing distinctive would be left ("Hotel Houston" stays one name)."""
    nw = str(name or "").split()
    for k in (2, 1):
        if len(nw) > k:
            tail = norm(" ".join(nw[-k:]))
            rest = [t for t in tokens(" ".join(nw[:-k])) if t not in GENERIC]
            if rest and tail in CITY_HINTS:
                return " ".join(nw[:-k]).strip(" ,.-:"), CITY_HINTS[tail]
    return "", ""


def deletion_queries(name, done=()):
    """Stage 3: the most distinctive word (5+ letters) with one letter dropped - "rhyland" -> "ryland" - and with an
    "s" added ("elkin" -> "elkins")."""
    d = sorted([t for t in tokens(name) if t not in GENERIC and len(t) >= 5], key=len, reverse=True)[:1]
    qs = [t + "s" for t in d if not t.endswith("s")] + [t[:i] + t[i + 1:] for t in d for i in range(len(t))]
    return [x for x in dict.fromkeys(qs) if x not in done][:8]


def spelling_queries(name, done=()):
    """v3: every distinctive word of 4+ letters with an "s" added ("lamb hill" -> "lambs") or a doubled letter made single
    ("sapphire" -> "saphire"), longest first."""
    d = sorted([t for t in tokens(name) if t not in GENERIC and len(t) >= 4], key=len, reverse=True)[:2]
    qs = []
    for t in d:
        qs += ([t + "s"] if not t.endswith("s") else [t[:-1]]) + [t[:i] + t[i + 1:] for i in range(1, len(t)) if t[i] == t[i - 1]]
    return [x for x in dict.fromkeys(qs) if x not in done][:4]


def _same_word(t, toks):
    """Exact, plural or one swap of letters. "capitale" is not "capital": a looser spelling may only be asked about."""
    return any(sim(t, u) >= 0.97 or _transposed(t, u) for u in toks)


def _near_word(t, toks):
    """One letter off in a long word ("shacamaxon" / "shackamaxon"). Only ever beside another word that matches exactly."""
    return len(t) >= 6 and any(len(u) >= 6 and u[0] == t[0] and abs(len(u) - len(t)) <= 1 and sim(t, u) >= 0.9 for u in toks)


def _city_words(place):
    return [t for t in tokens(place) if t not in STATE_ABBR and t not in {w for st in US_STATES for w in st.split()}]


def in_place(m, place):
    """The place they named, city included: "The Thompson houston" is not the one in Dallas."""
    if not place:
        return True
    hay = tokens("%s %s" % (m.get("address") or "", _st(m.get("state"))))
    city = _city_words(place)
    if city:
        return all(_same_word(t, hay) for t in city)
    st = state_of(place)
    return not st or st == _st(m.get("state"))


def _holds(name, m, cand, placed=False, loose=False):
    """Every word they typed is in the candidate's name, or says where it is (its own address / state). One type word may be
    missing, only when they named the place it is in (placed: "Thompson hotel in Seattle" / Thompson Seattle) and the name
    carries no other type word. At least one word that is not a type or a place must be in the name. loose: one long word
    may be a letter off when another word matches exactly."""
    if not name or not cand:
        return False
    if key(name) == key(cand):
        return True
    typed, ct = tokens(name), tokens(cand)
    where = tokens("%s %s" % (m.get("address") or "", _st(m.get("state"))))
    joined = joined_match(name, cand)
    nd, cd = _distinctive(name), _distinctive(cand)
    off = [t for t in nd if not _same_word(t, ct) and not _same_word(t, where) and _near_word(t, ct)] if loose and not joined else []
    if len(off) > 1 or (off and not any(_same_word(t, ct) for t in typed)):
        off = []
    if not joined and not any(_same_word(t, ct) or t in off for t in nd or typed):
        return False
    missing = 0
    for t in typed:
        if _same_word(t, ct) or t in off or (joined and t in nd):
            continue
        if t in GENERIC:
            missing += 1
            if not placed or missing > 1 or any(g in GENERIC and not _same_word(g, typed) for g in ct):
                return False
        elif not _same_word(t, where):
            return False
    named = nd if joined else [t for t in nd if _same_word(t, ct) or t in off]
    if (not named or _place(" ".join(named)) or all(_same_word(t, where) for t in named)):
        # only a place (and a type) in common: the name itself must add nothing ("Fort Worth club" is not "Woman's Club of
        # Fort Worth")
        return all(_same_word(t, typed) for t in cd)
    return True


def holds_all(q, m, loose=False):
    for f in ("name", "google_name"):
        loc = q.get("location") or ""
        if _holds(q.get("name"), m, m.get(f), bool(loc) and in_place(m, loc), loose):
            return True
        # the other reading ("Aspen Grove" AT "Sundance Trail") only when the venue is there
        if q.get("alt_name") and in_place(m, q.get("alt_location")) and _holds(q["alt_name"], m, m.get(f), True, loose):
            return True
    return False


_VENDOR_TOKEN = re.compile("|".join(p for _, p in VENDOR_WORDS) + r"|designs?|studios?|co|company|productions?|group|"
                           r"collective|llc|inc|services?|creative|cinema|artistry|beauty|makeup|hair|music", re.I)


def same_name(q, m):
    """A photographer / DJ / ... found for a name with no vendor word in it: its own name adds nothing but vendor words ("Mike
    Lentz" -> "Mike Lentz | Videographer"; "four seasons" is not "The Four Seasons Ensemble")."""
    typed = [t for n in (q.get("name"), q.get("alt_name")) if n for t in tokens(n)]
    return all(_best(t, typed, 0.9) or t in GENERIC or _VENDOR_TOKEN.fullmatch(t) for t in tokens(m.get("name")))


def exact(q, m):
    return any(n and key(n) == key(m.get(f)) for n in (q.get("name"), q.get("alt_name")) for f in ("name", "google_name"))


def certain(q, res, pool=None):
    """v3: a match is only STATED ("Here's X", "We have 2 that match") when it holds every word they typed and is in the place
    they named. Otherwise it becomes "not found" and did_you_mean may offer it as a question. The other way round, a miss whose
    candidates hold every word is a match ("Skylands manor"). Pure."""
    if not q.get("v3"):
        return res
    place = q.get("location") or ""
    have = list(res.get("matches") or []) + list((pool if pool is not None else res.get("pool")) or [])
    loose = not any(holds_all(q, m) for m in have)       # a letter off counts only when nothing is spelled as they typed it
    ms = [m for m in res.get("matches") or [] if holds_all(q, m, loose)]
    if res.get("status") == "found":
        return res if ms else dict(res, status="not_found", matches=[])     # elsewhere is said by place_fix
    if res.get("status") in ("best", "choices"):
        ms = [m for m in ms if in_place(m, place)]
        full = [m for m in ms if exact(q, m)]
        if full and len(full) < len(ms):
            ms = full
    elif res.get("status") == "not_found" and len(tokens(q.get("name"))) >= 2:
        ms = [m for m in (pool if pool is not None else res.get("pool")) or [] if holds_all(q, m, loose) and in_place(m, place)][:3]
    else:
        return res
    if not ms:
        return dict(res, status="not_found", matches=[])
    return dict(res, status="found" if len(ms) == 1 else "choices" if res["status"] == "not_found" else res["status"], matches=ms)


def _generic_typo(t, v3=False):
    """'meusuem' is a misspelt 'museum': a venue-type word, not a distinctive one."""
    return len(t) >= 5 and t not in GENERIC and any(
        g[0] == t[0] and abs(len(g) - len(t)) <= 1 and sim(t, g) >= (0.7 if v3 else 0.6) for g in GENERIC if len(g) >= 4)


DYM_ONE_WORD = 0.9     # v3: the only matching word must be this close ("rhyland"/"ryland" 0.92, "amway"/"away" 0.89)


def dym_score(query, name, v3=False):
    """0 when the candidate does not hold the couple's distinctive words; else 0..1. Each distinctive word (venue-type
    words and their misspellings left out) must match a word of the name closely (>= 0.8, 0.9 under 5 letters); the
    longest must match; at least half of them must; with 3+ words at least 2 must."""
    qd = [t for t in _distinctive(query) if not _generic_typo(t, v3)] or _distinctive(query)
    ct = tokens(name)
    if not qd or not ct:
        return 0.0
    sims = []
    for t in qd:
        b = max(sim(t, u) for u in ct)
        sims.append(b if b >= (0.8 if len(t) >= 5 else 0.9) else 0.0)
    matched = sum(1 for s in sims if s)
    longest = max(range(len(qd)), key=lambda i: len(qd[i]))
    if not matched or not sims[longest] or matched / len(qd) < 0.5 or (len(qd) >= 3 and matched < 2):
        return 0.0
    if v3 and matched < len(qd) and len(qd) == 2:
        return 0.0           # "cape club of sharon" is not "Sharon Valley Ranch"
    if v3 and matched == 1 and sims[longest] < DYM_ONE_WORD:
        return 0.0
    return round(0.7 * matched / len(qd) + 0.3 * sum(sims) / len(qd), 3)


def dym_candidates(q, pool):
    """[(score, row)] best first: rows of the pool that hold the couple's distinctive words."""
    names = [n for n in (q.get("name"), q.get("alt_name")) if n]
    out = []
    for m in pool or []:
        s = max([dym_score(n, m.get(f) or "", bool(q.get("v3"))) for n in names for f in ("name", "google_name")] or [0.0])
        if s > 0:
            out.append((s, m))
    if q.get("v3"):      # the type word they typed breaks a tie: "Elkin ESTATE" -> Elkins Estate before Elkin Creek Vineyard
        ty = [t for n in names[:1] for t in tokens(n) if t in GENERIC]
        out = [(s + 0.05 * sum(1 for t in ty if t in tokens(m.get("name") or "")), m) for s, m in out]
    return sorted(out, key=lambda x: -x[0])


def _place_of(q):
    return q.get("location") or q.get("alt_location") or ""


def soft_ok(q, res):
    """v3: may this lookup be answered at all? False = the model reads the message."""
    kind, st = q.get("soft"), res.get("status")
    if not kind:
        return True
    if kind == "exact":
        return st in ("found", "best", "elsewhere") and bool(res.get("matches")) and exact(q, res["matches"][0])
    return st in (("found", "best", "elsewhere") if kind == "edit" else ("found", "best", "elsewhere", "choices", "dym"))


def did_you_mean(q, res, prefer_states=()):
    """A not_found lookup whose candidate pool holds the couple's words -> status "dym" with up to 3 matches (in the
    place they named, else their wedding state(s), first). Anything else passes through unchanged. Pure."""
    if res.get("status") != "not_found":
        return res
    cands = dym_candidates(q, res.get("pool"))
    if not cands:
        return res
    place = _place_of(q)
    want = {str(s).strip().lower() for s in prefer_states if s}

    def rank(c):
        m = c[1]
        here = _in_place(m, place) if place else str(_st(m.get("state"))).strip().lower() in want
        return (0 if here else 1, -c[0])
    cands.sort(key=rank)
    return dict(res, status="dym", matches=[m for _, m in cands[:3]], dym_scores=[s for s, _ in cands[:3]])


def place_fix(q, res, prefer_states=()):
    """A found / best venue that is NOT in the place the couple named ("The Sanctuary RI" -> the New York one) or,
    with no place named, not in their wedding state(s) ("Central park" during a New York chat -> Minnesota):
      - a candidate in that place that holds their words -> status "best", that one first, the other offered;
      - none -> status "elsewhere" (the same card, and the answer says where it is)."""
    if res.get("status") not in ("found", "best") or not res.get("matches"):
        return res
    top = res["matches"][0]
    names = [n for n in (q.get("name"), q.get("alt_name")) if n]
    v2 = bool(q.get("v2"))

    def holds(m):
        if q.get("v3"):      # an exact name somewhere else is never swapped for a lookalike near them
            return holds_all(q, m) and (exact(q, m) or not exact(q, top))
        return any(strong(n, m.get(f) or "", v2) or dym_score(n, m.get(f) or "") > 0
                   for n in names for f in ("name", "google_name"))
    place = _place_of(q)
    if place and (state_of(place) or [t for t in tokens(place) if t not in STATE_ABBR]):
        inside = (lambda m: in_place(m, place)) if q.get("v3") else (lambda m: _in_place(m, place))
        where = place
    else:
        want = {str(s).strip().lower() for s in prefer_states if s}
        if not want:
            return res
        inside = lambda m: str(_st(m.get("state"))).strip().lower() in want
        where = ", ".join(s for s in prefer_states if s)
    if inside(top):
        return res
    pool = [m for m in (res.get("pool") or []) + list(res["matches"]) if m.get("vendor_id") != top.get("vendor_id")]
    here = [m for m in pool if inside(m) and holds(m)]
    if here:
        return dict(res, status="best", matches=[here[0], top], place_fixed=True)
    return dict(res, status="elsewhere", matches=[top], elsewhere_of=where, typed_place=bool(place))
