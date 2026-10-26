"""Thin profile -> at most 2 short questions with tap answers on the opening (owner decision 2026-10-26).

Feedback: "it should ask more questions to learn my preferences before showing random venues"; the 1-star Flower
Mound user's profile had only a state. Before this, a thin profile got ONE closing question the model wrote in
prose (STATE ONLY / NONE IN PROFILE / preference rules in agent.SYSTEM), the opening's chips were the fixed
starter chips (so the question had no tap answers), and the answer was only kept if the model chose to save_note.

THIN, precisely - any of these gaps (checked in this order, at most 2 asked):
  guests  no Wedding_Guest_Count in the profile and no saved "guests" answer
  area    a US state is known (Wedding_Location_Updated) but no city / area: no saved "area" answer, no city in the
          chat's active filters, and no memory note / user context naming one ("Wants to stay near: ...")
          (no state at all -> not asked here: the existing NONE IN PROFILE rule asks for the state)
  style   no wedding_vibes in the profile and no saved style / setting / feel answer
A question is asked on at most MAX_ASKS openings (hidden pref_asked markers in rec_memory.notes), so an ignored
question doesn't come back forever. Answers are stored as kind="pref" notes (prefs.py) with src="chat".

Pure - no app/config imports, so tests can load it alone.
"""
import re

import prefs

MAX_QUESTIONS = 2
MAX_ASKS = 2      # openings a question may be shown on before we stop asking it

# 4 areas per state for the "Which part of <state>?" chips: the largest wedding markets, Tulle-relevant.
CITIES_BY_STATE = {
    "Alabama": ["Birmingham", "Huntsville", "Mobile", "Montgomery"],
    "Alaska": ["Anchorage", "Fairbanks", "Juneau", "Girdwood"],
    "Arizona": ["Phoenix", "Scottsdale", "Tucson", "Sedona"],
    "Arkansas": ["Little Rock", "Fayetteville", "Bentonville", "Hot Springs"],
    "California": ["Los Angeles", "San Diego", "San Francisco", "Napa"],
    "Colorado": ["Denver", "Boulder", "Colorado Springs", "Breckenridge"],
    "Connecticut": ["Hartford", "New Haven", "Stamford", "Mystic"],
    "Delaware": ["Wilmington", "Dover", "Newark", "Rehoboth Beach"],
    "District of Columbia": ["Washington"],
    "Florida": ["Miami", "Orlando", "Tampa", "Jacksonville"],
    "Georgia": ["Atlanta", "Savannah", "Athens", "Augusta"],
    "Hawaii": ["Honolulu", "Maui", "Kauai", "Kona"],
    "Idaho": ["Boise", "Coeur d'Alene", "Idaho Falls", "Sun Valley"],
    "Illinois": ["Chicago", "Naperville", "Rockford", "Peoria"],
    "Indiana": ["Indianapolis", "Fort Wayne", "Bloomington", "South Bend"],
    "Iowa": ["Des Moines", "Cedar Rapids", "Iowa City", "Davenport"],
    "Kansas": ["Kansas City", "Wichita", "Lawrence", "Topeka"],
    "Kentucky": ["Louisville", "Lexington", "Bowling Green", "Covington"],
    "Louisiana": ["New Orleans", "Baton Rouge", "Lafayette", "Shreveport"],
    "Maine": ["Portland", "Bar Harbor", "Kennebunkport", "Camden"],
    "Maryland": ["Baltimore", "Annapolis", "Frederick", "Bethesda"],
    "Massachusetts": ["Boston", "Cape Cod", "Worcester", "the Berkshires"],
    "Michigan": ["Detroit", "Grand Rapids", "Ann Arbor", "Traverse City"],
    "Minnesota": ["Minneapolis", "St. Paul", "Duluth", "Rochester"],
    "Mississippi": ["Jackson", "Oxford", "Biloxi", "Hattiesburg"],
    "Missouri": ["St. Louis", "Kansas City", "Springfield", "Columbia"],
    "Montana": ["Bozeman", "Missoula", "Billings", "Whitefish"],
    "Nebraska": ["Omaha", "Lincoln", "Kearney", "Grand Island"],
    "Nevada": ["Las Vegas", "Reno", "Lake Tahoe", "Henderson"],
    "New Hampshire": ["Manchester", "Portsmouth", "Concord", "North Conway"],
    "New Jersey": ["Jersey City", "Newark", "Princeton", "Cape May"],
    "New Mexico": ["Albuquerque", "Santa Fe", "Taos", "Las Cruces"],
    "New York": ["Manhattan", "Brooklyn", "Long Island", "the Hudson Valley"],
    "North Carolina": ["Charlotte", "Raleigh", "Asheville", "Wilmington"],
    "North Dakota": ["Fargo", "Bismarck", "Grand Forks", "Minot"],
    "Ohio": ["Columbus", "Cleveland", "Cincinnati", "Dayton"],
    "Oklahoma": ["Oklahoma City", "Tulsa", "Norman", "Edmond"],
    "Oregon": ["Portland", "Bend", "Eugene", "the Willamette Valley"],
    "Pennsylvania": ["Philadelphia", "Pittsburgh", "Lancaster", "the Poconos"],
    "Rhode Island": ["Providence", "Newport", "Narragansett", "Westerly"],
    "South Carolina": ["Charleston", "Greenville", "Columbia", "Myrtle Beach"],
    "South Dakota": ["Sioux Falls", "Rapid City", "Brookings", "Spearfish"],
    "Tennessee": ["Nashville", "Knoxville", "Memphis", "Chattanooga"],
    "Texas": ["Dallas", "Austin", "Houston", "San Antonio"],
    "Utah": ["Salt Lake City", "Park City", "Provo", "St. George"],
    "Vermont": ["Burlington", "Stowe", "Woodstock", "Manchester"],
    "Virginia": ["Richmond", "Charlottesville", "Virginia Beach", "Northern Virginia"],
    "Washington": ["Seattle", "Spokane", "Tacoma", "Woodinville"],
    "West Virginia": ["Charleston", "Morgantown", "Huntington", "Lewisburg"],
    "Wisconsin": ["Milwaukee", "Madison", "Green Bay", "Lake Geneva"],
    "Wyoming": ["Jackson", "Cheyenne", "Casper", "Sheridan"],
}

# Guest bands: label is what the chip says (and what a tap sends as the message); value is the count searched.
GUEST_OPTIONS = [("Under 50 guests", "40"), ("50-100 guests", "100"), ("100-150 guests", "150"),
                 ("150+ guests", "200")]
# "What kind of place?" - the most-picked venue types (prefs "style" values, i.e. exact search vocab).
STYLE_OPTIONS = [("Barn or ranch", "Barn / Ranch"), ("Estate or mansion", "Estate / Mansion"),
                 ("Hotel or resort", "Hotel / Resort"), ("Garden", "Garden / Botanical Garden"),
                 ("Winery or vineyard", "Winery / Brewery / Distillery")]
_STYLE_WORDS = {"Barn / Ranch": r"barn|ranch|farm|rustic", "Estate / Mansion": r"estate|mansion|manor",
                "Hotel / Resort": r"hotel|resort", "Garden / Botanical Garden": r"garden|botanical",
                "Winery / Brewery / Distillery": r"winery|vineyard|brewery|distillery"}
_AREA_NOTE = re.compile(r"stay(?:ing)? (?:near|close to)|wants? to be (?:near|close to)|wedding (?:city|area)|"
                        r"\b(?:city|area|town)\s*:", re.I)


def state_of(profile):
    loc = (profile or {}).get("Wedding_Location_Updated") or []
    loc = [loc] if isinstance(loc, str) else loc
    states = [x.strip() for l in loc if l for x in str(l).split(",") if x.strip()]
    return [s for s in states if s in CITIES_BY_STATE][:2]


def area_options(states):
    """'Near Dallas' chips: 4 areas of one state, or 2 + 2 for two states. value = 'City, State'."""
    per = 4 if len(states) == 1 else 2
    return [{"label": "Near " + c, "value": "%s, %s" % (c, s)} for s in states for c in CITIES_BY_STATE[s][:per]]


def all_area_values():
    return {"%s, %s" % (c, s) for s, cs in CITIES_BY_STATE.items() for c in cs}


def gaps(profile, answers, notes=(), user_context="", active_filters=None):
    """Ordered missing pieces among guests / area / style (see the module docstring)."""
    p, a = profile or {}, answers or {}
    out = []
    try:
        has_guests = int(p.get("Wedding_Guest_Count") or 0) > 0
    except (TypeError, ValueError):
        has_guests = False
    if not has_guests and not a.get("guests"):
        out.append("guests")
    texts = [n.get("note") or "" for n in notes or () if isinstance(n, dict)] + [user_context or ""]
    if (state_of(p) and not a.get("area") and not (active_filters or {}).get("city")
            and not any(_AREA_NOTE.search(t) for t in texts)):
        out.append("area")
    if not p.get("wedding_vibes") and not (a.get("style") or a.get("setting") or a.get("feel")):
        out.append("style")
    return out


def asked_counts(notes):
    c = {}
    for n in notes or ():
        if isinstance(n, dict) and n.get("kind") == "pref_asked" and n.get("key"):
            c[n["key"]] = c.get(n["key"], 0) + 1
    return c


def question(qid, profile):
    if qid == "guests":
        return {"id": "guests", "text": "Roughly how many guests?", "multi": False,
                "options": [{"label": l, "value": v} for l, v in GUEST_OPTIONS]}
    if qid == "area":
        states = state_of(profile)
        where = states[0] if len(states) == 1 else "your states"
        return {"id": "area", "text": "Which part of %s should we look near?" % where, "multi": False,
                "options": area_options(states)}
    if qid == "style":
        return {"id": "style", "text": "What kind of place feels like you?", "multi": False,
                "options": [{"label": l, "value": v} for l, v in STYLE_OPTIONS]}
    return None


def questions(profile, notes=(), user_context="", active_filters=None):
    """Up to MAX_QUESTIONS question dicts for this opening ([] when the profile isn't thin)."""
    answers = prefs.from_notes(notes)
    counts = asked_counts(notes)
    ids = [q for q in gaps(profile, answers, notes, user_context, active_filters) if counts.get(q, 0) < MAX_ASKS]
    return [q for q in (question(i, profile) for i in ids[:MAX_QUESTIONS]) if q]


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()


def match_answer(q, text):
    """The option value the couple's message answers `q` with, or None. Tapped chips match their label exactly;
    typed answers: a guest count ("about 120", "120 guests"), an area name ("Dallas", "near austin"), a place word
    ("a barn"). Long messages (> 60 chars) are left to the model - they are new requests, not answers."""
    t = (text or "").strip()
    if not t or len(t) > 60 or not q:
        return None
    words = " ".join(_norm(t))
    for o in q.get("options") or []:
        if " ".join(_norm(o["label"])) == words or " ".join(_norm(o["value"])) == words:
            return o["value"]
    if q["id"] == "guests":
        if re.search(r"[$€£]|\d\s?k\b", t, re.I):
            return None
        # A bare count ("120", "about 120ish") or a count with "guests" / "people" - never "Top 5 venues".
        m = (re.match(r"^\s*(?:about|around|roughly|approx\w*|maybe|like|~)?\s*(\d{1,4})\s*(?:ish)?\s*"
                      r"(?:guests?|people|ppl|persons?)?\s*[.!]?\s*$", t, re.I)
             or re.search(r"\b(\d{1,4})\s*(?:guests?|people|ppl|persons?)\b", t, re.I))
        if m and 2 <= int(m.group(1)) <= 1500:
            return m.group(1)
        return None
    if q["id"] == "area":
        for o in q.get("options") or []:
            city = o["value"].split(",")[0]
            if " %s " % " ".join(_norm(city)).replace("the ", "") in " %s " % words:
                return o["value"]
        return None
    if q["id"] == "style":
        for o in q.get("options") or []:
            if re.search(r"\b(?:%s)" % _STYLE_WORDS.get(o["value"], "$^"), t, re.I):
                return o["value"]
    return None


def answers_from_reply(pending, text):
    """{question_id: [value]} for every pending question the message answers."""
    out = {}
    for q in pending or ():
        v = match_answer(q, text)
        if v:
            out[q["id"]] = [v]
    return out


def context_line(qs):
    """What the model is told when the page will show the questions under its message."""
    if not qs:
        return ""
    return ("\nPROFILE QUESTIONS: under your message the page asks the couple " + " and ".join(
        '"%s"' % q["text"] for q in qs) + " with tap answers. Still show 3 strong picks from what you know (never "
        "zero vendors). Do not ask these or any other question yourself, and skip the closing question this time; "
        "end with one short line such as \"Answer below and I'll tighten these picks.\"")
