"""Builds conditional_rates.json (REC_PHOTO_FIRST member rental line, owner ruling T0-54, 2026-11-26) OFFLINE from STORED
table-37 rows (granular pricing, one row per stored rate). No PDF is read, here or at runtime.

A stored rate can be for some couples only: V2118 Newton White Mansion's $7,480 Saturday rate is the Prince George's /
Montgomery County resident ("Bi-County") rate; its PDF says everyone else pays $9,724, and that figure is stored nowhere.
The extraction keeps the condition in the row's Notes. For each stored rate (row) this says:
  None (not listed)  the rate is the one everyone pays: a "Non-resident ..." / "Non-member ..." rate, the condition is only a
                     discount or a waiver ("10% military discount"), or another figure is the residents' one
                     ("Mon-Thu rate $80/hr (resident $55/hr)")
  both               the rate is for residents AND the everyone-else rate is in the same note -> "Rental $35 for county
                     residents ($75 otherwise), from its PDF"
  conditional        the rate is for residents / members and no everyone-else rate is stored -> "Rental $7,480 ... for
                     county residents, from its PDF" (owner bar 12.6a #1: shown, labelled - never hidden)
  not_private        a business / non-profit / organization rate -> "... for non-profits" / "... for businesses"
  unclear            a residents / members rate is named but which one this row is can't be told -> no rental line

Usage: python build_conditional_rates.py rows.json > conditional_rates.json, where rows.json is {id: table-37 row} for the
rows whose Notes contain any of KEYWORDS (read with the Xano MCP searchTableContent, "Notes|includes": <keyword>, 500 a
page). Pure: classify() is unit-tested in tests/test_photo_first.py."""
import json
import re
import sys

KEYWORDS = ("resident", "county", "member", "military", "non-profit", "nonprofit", "alumni", "student", "employee",
            "faculty", "veteran", "in-district", "out-of-district", "in-state", "out-of-state", "city of", "township",
            "affiliat", "church", "parishioner", "congregation", "staff", "senior", "discount", "taxpayer")

_N = r"\$?\s?(\d[\d,]*(?:\.\d+)?)"
_STD = re.compile(r"\b(?:non[- ]?(?:[a-z]+[- ])?residents?|non[- ]?members?|non[- ]?city|non[- ]?county|"
                  r"out[- ]of[- ](?:county|district|state|city|town|area)|standard (?:rate|pricing|price)|public rate|"
                  r"general public|general rate|everyone else|all others)\b", re.I)
_WHO = (r"\b(?:residents?|resident's|members?|membership|taxpayers?|in-district|in-county|bi-county)\b"
        r"(?!\s+(?:lounges?|club|hall|room|area|suite))")
_WHO_RX = re.compile(_WHO, re.I)
_DISC = re.compile(r"[^.;()]*\b(?:discount\w*|waived?)\b[^.;()]*", re.I)
_BIZ = re.compile(r"\b(?:commercial|business|corporate|non-?profit|organization|group)\b", re.I)
_PRIVATE = re.compile(r"\b(?:private|individual|for-profit|personal)\b", re.I)
_OTHER = re.compile(r"NONRES\w*(?:\s+(?:private|wedding|daily|hourly|rate|pricing|price|is|of)|\s*[:=-])*\s*" + _N, re.I)
_REQUIRED = re.compile(r"\b(?:membership|members?)\s+(?:is\s+)?required\b|\bmembers?\s+only\b", re.I)


def num(s):
    try:
        return int(float(str(s).replace("$", "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _label(t):
    tl = t.lower()
    if "member" in tl:
        return "members"
    if "county" in tl:
        return "county residents"
    if "township" in tl:
        return "township residents"
    if re.search(r"\bcity\b", tl):
        return "city residents"
    return "residents"


def _biz(t):
    """A business / non-profit rate, with the words the note uses: "non-profits" or "businesses"."""
    return {"kind": "not_private", "condition": "non-profits" if re.search(r"non-?profit|charit", t, re.I) else "businesses"}


def classify(notes, fee):
    """One stored rate: None, or {"kind", "condition"?, "other"?} (see the module docstring)."""
    notes = " ".join(str(notes or "").split())
    if not notes or not fee or fee <= 0:
        return None
    t = _STD.sub(lambda m: "NONMEM" if "member" in m.group(0).lower() else "NONRES", notes)
    lead = re.split(r"[.;]\s", t, maxsplit=1)[0]
    if "NONRES" in lead or "NONMEM" in lead:            # "Non-resident Saturday rate ...": what everyone else pays
        return _biz(lead) if _BIZ.search(lead) and not _PRIVATE.search(lead) else None
    if re.search(r"\bsame (?:pricing|price|rate)s?\b", t, re.I):
        return None
    rest, lead2 = _DISC.sub(" ", t), _DISC.sub(" ", lead)
    # Another dollar figure beside the who-word, and this row is not itself named as that rate: this row is the other one.
    if _REQUIRED.search(rest):                          # "Membership required: $140": the rate needs a membership
        return {"kind": "conditional", "condition": "members"}
    figs = [num(m.group(1)) for m in re.finditer(_WHO + r"[^$\d]{0,25}\$\s?(\d[\d,]*)", rest, re.I)]
    named = re.search(r"\bfor\s+[\w' .-]{0,30}" + _WHO, lead2, re.I) or \
        re.match(r"\W*[\w.' -]{0,30}?" + _WHO + r"\W+(?:[a-z]+\s+)?(?:rate|pricing|price)\b", lead2, re.I)
    if figs and all(f != fee for f in figs) and not named:
        return None
    if _BIZ.search(lead2) and re.search(r"\b(?:rate|pricing|price|group)\b", lead2, re.I) and not _PRIVATE.search(lead2):
        return _biz(lead2)
    if _WHO_RX.search(lead2) or _REQUIRED.search(rest):
        lab = _label(lead2 if _WHO_RX.search(lead2) else "member")
        other = sorted({num(m.group(1)) for m in _OTHER.finditer(t)} - {None})
        other = [o for o in other if o > fee]
        # a note that also prices businesses / non-profits ("Commercial Non-Resident: $550/hr") never pairs two figures
        if lab != "members" and len(other) == 1 and not _BIZ.search(t):
            return {"kind": "both", "condition": lab, "other": other[0]}
        return {"kind": "conditional", "condition": lab}
    if _WHO_RX.search(rest) and re.search(r"\b(?:rate|pricing|price)\b", rest, re.I):
        return {"kind": "unclear"}
    return None


def build(rows):
    """{vendor_id: {str(fee): entry}}; one fee held by two rows of a venue that disagree is "unclear"."""
    out = {}
    for r in rows:
        fee = num(r.get("Venue_Fee"))
        c = classify(r.get("Notes"), fee)
        if not c:
            continue
        v = out.setdefault(str(r.get("Vendor_ID") or ""), {})
        k = str(fee)
        if k in v and v[k] != c:
            c = {"kind": "unclear"}
        v[k] = c
    return {k: v for k, v in sorted(out.items()) if k}


if __name__ == "__main__":
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    rows = list(data.values()) if isinstance(data, dict) else data
    json.dump(build(rows), sys.stdout, indent=0, sort_keys=True)
