"""Preference questions (beta feedback 2026-10-08, user 19303 on 4 weeks: "it should ask more questions to learn my
preferences before showing random venues"; Kate: use criteria the filters don't surface, e.g. rooftop, waterfront,
high ceilings). Pure - no app/config imports, so tests can load it alone.

Every option maps to something ep119 can actually filter on (venue_types[], vibes[], pricing_models[],
outdoor_ceremony) or, for "priority", to how the model ranks. Answers are stored in rec_memory.notes as entries
with kind="pref" (no schema change), so they also show - and can be deleted - in "What Tulle Assistant knows
about you".
"""

QUESTIONS = [
    # 2026-10-08 v2 (user: "we're missing some venue vibes ... and some place descriptions"): all 14 venue types
    # Discovery's Venue Type filter offers, plus a soft "overall feel" question for aesthetics no filter covers.
    {"id": "style", "question": "What kind of place feels like you?", "multi": True, "max": 3,
     "options": [{"label": l, "value": l} for l in (
         "Estate / Mansion", "Barn / Ranch", "Garden / Botanical Garden", "Hotel / Resort", "Restaurant / Bar",
         "Winery / Brewery / Distillery", "Museum / Gallery", "Dedicated Event Venue",
         "Country Club / Private Club", "Performing Arts Venue", "University / College", "Civic / Public",
         "Zoo / Aquarium", "Religious")]},
    {"id": "feel", "question": "What's the overall feel?", "multi": True, "max": 3,
     "options": [{"label": l, "value": l} for l in (
         "Rustic", "Modern / minimalist", "Classic / timeless", "Romantic", "Glam / black-tie",
         "Boho / garden party", "Industrial chic", "Intimate / cozy", "Coastal", "Whimsical / artsy")]},
    {"id": "setting", "question": "Any must-haves for the setting?", "multi": True, "max": 3,
     "options": [{"label": l, "value": v} for l, v in (
         ("Waterfront", "Waterfront"), ("Rooftop or skyline views", "Rooftop / Skyline Views"),
         ("High ceilings", "Tall / Vaulted Ceilings"), ("Historic architecture", "Historic Architecture"),
         ("Lots of natural light", "Natural Light / Large Windows"), ("Nature views", "Scenic / Nature Views"),
         ("Ballroom", "Ballroom"), ("Industrial / loft", "Industrial / Warehouse"), ("Tented", "Tented"),
         ("Greenhouse", "Greenhouse"))]},
    {"id": "catering", "question": "How do you want food and setup handled?", "multi": False,
     "options": [{"label": l, "value": v} for l, v in (
         ("One all-inclusive package", "All-Inclusive"), ("Some included, some our own", "Semi-Inclusive"),
         ("Bring our own caterer", "Raw Space"), ("Not sure yet", ""))]},
    {"id": "ceremony", "question": "Where will the ceremony be?", "multi": False,
     "options": [{"label": l, "value": v} for l, v in (
         ("Outdoors at the venue", "outdoor"), ("Indoors at the venue", "indoor"),
         ("Somewhere else", "elsewhere"), ("Not sure yet", ""))]},
    {"id": "priority", "question": "What matters most to you?", "multi": False,
     "options": [{"label": l, "value": l} for l in (
         "Staying on budget", "The look and feel", "Room for our guest count", "The location")]},
]
_BY_ID = {q["id"]: q for q in QUESTIONS}
_NOTE_PREFIX = {"style": "Style", "feel": "Overall feel", "setting": "Setting must-haves", "catering": "Food and setup",
                "ceremony": "Ceremony", "priority": "Matters most"}


def clean(answers):
    """Keep only known questions and known option values (a public-ish endpoint: never store free text here)."""
    out = {}
    for qid, vals in (answers or {}).items():
        q = _BY_ID.get(qid)
        if not q:
            continue
        vals = [vals] if isinstance(vals, str) else list(vals or [])
        allowed = {o["value"] for o in q["options"]}
        keep = []
        for v in vals:
            if isinstance(v, str) and v in allowed and v and v not in keep:
                keep.append(v)
        keep = keep[:q.get("max", 1)] if q["multi"] else keep[:1]
        if keep:
            out[qid] = keep
    return out


def _label(qid, value):
    for o in _BY_ID[qid]["options"]:
        if o["value"] == value:
            return o["label"]
    return value


def to_notes(answers):
    """One readable memory line per answered question, e.g. 'Setting must-haves: Waterfront, High ceilings'."""
    return [{"kind": "pref", "key": qid, "values": vals,
             "note": "%s: %s" % (_NOTE_PREFIX[qid], ", ".join(_label(qid, v) for v in vals))}
            for qid, vals in clean(answers).items() if qid in _NOTE_PREFIX]


def from_notes(notes):
    """Stored pref entries -> answers dict (the latest entry per question wins)."""
    out = {}
    for n in notes or []:
        if isinstance(n, dict) and n.get("kind") == "pref" and n.get("key"):
            out[n["key"]] = list(n.get("values") or [])
    return clean(out)


def asked(notes):
    """True once the couple answered OR skipped the questions - don't ask again automatically."""
    return any(isinstance(n, dict) and n.get("kind") in ("pref", "pref_skipped") for n in notes or [])


def search_hint(answers):
    """The line the model gets: which search_venues filters the answers map to, plus the ranking priority."""
    a = clean(answers)
    if not a:
        return ""
    f = []
    if a.get("style"):
        f.append("venue_types=" + repr(a["style"]))
    if a.get("setting"):
        f.append("vibes=" + repr(a["setting"]))
    if a.get("catering"):
        f.append("pricing_models=" + repr(a["catering"]))
    if a.get("ceremony") == ["outdoor"]:
        f.append("outdoor_ceremony=true")
    line = ("The couple's preference answers (they chose these themselves): "
            + "; ".join(n["note"] for n in to_notes(a)) + ".")
    if f:
        line += (" Use them as search_venues filters first (" + ", ".join(f) + "). If that leaves fewer than 3 "
                 "good fits, drop the least important filter, search again, and say in a few words which "
                 "preference you relaxed.")
    if a.get("feel"):
        line += (" Their overall feel (" + ", ".join(a["feel"]) + ") has no search filter: use it to choose among "
                 "results (venue type, description, vibes) and to explain fit, never as a reason to invent details.")
    if a.get("priority"):
        line += " Rank picks by what matters most to them: " + a["priority"][0] + "."
    return line
