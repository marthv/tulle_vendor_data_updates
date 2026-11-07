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


def score(query, name, location=""):
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
    return base


def strong(query, name):
    """Every distinctive word they typed matches a word of the name nearly exactly (exact, plural or one swap).
    A one-word message with no lookup cue must be this strong: "Khols" -> Kohl Mansion yes, "summer" -> Summerour no."""
    qt, ct = tokens(query), tokens(name)
    qd = [t for t in qt if t not in GENERIC] or qt
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


def detect(text, last_assistant=""):
    """{"name", "location", "alt_name", "alt_location", "cue"} when the message looks like "look up <venue name>",
    else None. Conservative on purpose: anything that reads like a search or a follow-up goes to the model.
    last_assistant: the previous assistant message - a bare reply to its question ("Austin") is never a lookup."""
    raw = " ".join(str(text or "").split())
    if not raw or len(raw) > 200:
        return None
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
    words = norm(name).split()
    if not words or len(words) > 7 or len(norm(name)) < 3:
        return None
    if REJECT.search(name) or _place(name) or _descriptive_only(name) or conversational(name):
        return None
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
    return {"name": name, "location": location, "alt_name": alt_name, "alt_location": alt_location, "cue": cue,
            "words": len(words), "after_question": after_question}


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


def first_queries(name, alt_name=""):
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
        s = max(score(q["name"], r.get("name"), q.get("location")),
                score(q["name"], r.get("google_name"), q.get("location")))
        if q.get("alt_name"):
            s = max(s, score(q["alt_name"], r.get("name"), q.get("alt_location")),
                    score(q["alt_name"], r.get("google_name"), q.get("alt_location")))
        loc = tokens(q.get("location") or q.get("alt_location") or "")
        if loc:
            hay = set(tokens("%s %s" % (r.get("address") or "", r.get("state") or "")))
            if all(_best(t, hay, 0.9) for t in loc if t not in STATE_ABBR) or state_of(
                    q.get("location") or q.get("alt_location")) == (r.get("state") or "") != "":
                s = min(1.0, s + 0.06)    # in the place they named: a tie-break, never a match on its own
        if q.get("solo") and not (strong(q["name"], r.get("name")) or strong(q["name"], r.get("google_name"))):
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
    if any(strong(n, m.get("name")) or strong(n, m.get("google_name")) for n in names if n):
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

    terms = first_queries(q["name"], q.get("alt_name"))
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
    status, matches = _promote_single(q, status, matches)
    return {"status": status, "matches": matches, "queries": list(done),
            "scores": [round(s, 3) for s, _ in ranked[:5]],
            "near": [r.get("name") for s, r in ranked[:3] if r.get("name")]}


class LookupFailed(Exception):
    pass
