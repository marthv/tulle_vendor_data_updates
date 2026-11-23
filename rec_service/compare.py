"""Side-by-side comparison of 2-3 venues the couple picked (owner 2026-11-22: "it feels like a word problem").

POST /rec/tool {tool: "compare_picked", args: {vendor_ids: [...], guests?}}. Computed in code: no model call, no
question used, no new table. Two levels, decided HERE and never by the page:
  full  = an active plan AND ep230 entitlement (xano.pi_entitled): the venue page's own numbers (estimate.compute).
  words = everyone else: ep231 only (never ep230) and only how each line compares with the state. No digit of a vendor
          price is ever in the payload; couples open the pricing PDF for the numbers.
A 1-week member has a plan but not ep230 (needs $50+ paid), so they are "words" with reason "member" - their PDFs open.
Nothing is ranked: venues come back in the order the couple picked them. REC_COMPARE_PICK=0 (default) = off."""
import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import config
import estimate

TOOL = "compare_picked"
TYPE = "venue_side_by_side"
V = 1
MIN_VENUES, MAX_VENUES = 2, 3
MAX_GUESTS = 1500
_LINE_KEYS = {"Rental": "rental", "Ceremony on site": "ceremony", "Food and drink": "food", "Service charge": "service"}
_LABELS = {"rental": "Rental", "ceremony": "Ceremony", "food": "Food and drink", "service": "Service charge"}
_SOURCES = {"this venue's PDF": "pdf", "estimate": "estimate", "not listed": "missing", "not stated - package": "missing"}
# ep231 line titles -> the same four rows
# (no ceremony: its badge compares a per-person fee with flat fees - Tavern on the Green, $20 a head, "below average")
_WORD_KEYS = (("rental", "rental"), ("food", "food"), ("service", "service"))
_WORDS = (("below", "below typical"), ("above", "above typical"), ("typical", "typical"), ("average", "typical"))
_DIGIT = re.compile(r"\d")


# ---------------------------------------------------------------- who sees it

def arm(user_id):
    """'compare' | 'control' | 'off'. Same shape as plan.arm: stable per account, internal testers always on."""
    if not config.COMPARE_PICK:
        return "off"
    try:
        uid = int(user_id or 0)
    except (TypeError, ValueError):
        return "off"
    if uid <= 0:
        return "off"
    if uid in config.COMPARE_USER_IDS:
        return "compare"
    if config.COMPARE_AB_PCT <= 0:
        return "off"
    h = int(hashlib.sha256(("%s:%d" % (config.COMPARE_AB_SALT, uid)).encode()).hexdigest()[:8], 16) % 100
    return "compare" if h < config.COMPARE_AB_PCT else "control"


# ---------------------------------------------------------------- daily cap (tool calls sit outside the question limits)

_USED, _USED_LOCK = {}, threading.Lock()


def spend(user_id, vendor_ids):
    """Count DISTINCT venues compared per couple per UTC day (in memory). False = over the cap. A what-if on the same
    venues costs nothing, so changing the guest count never runs out."""
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _USED_LOCK:
        got_day, ids = _USED.get(user_id, (day, set()))
        if got_day != day:
            ids = set()
        new = ids | set(vendor_ids)
        if len(new) > config.COMPARE_VENUES_PER_DAY:
            return False
        _USED[user_id] = (day, new)
        if len(_USED) > 20000:
            for k in [k for k, v in _USED.items() if v[0] != day]:
                del _USED[k]
        return True


def clean_ids(raw):
    out = []
    for v in raw if isinstance(raw, (list, tuple)) else []:
        v = str(v or "").strip()
        if re.fullmatch(r"[A-Za-z]{0,3}\d{1,9}", v) and v not in out:
            out.append(v)
    return out[:MAX_VENUES]


def saved_venue_ids(xano, user):
    """The couple's most recently saved VENUES (favorited_vendors is in save order; photographers etc. are left out)."""
    recent = list(dict.fromkeys(str(v).strip() for v in reversed((user or {}).get("favorited_vendors") or []) if str(v or "").strip()))[:12]
    with ThreadPoolExecutor(max_workers=6) as pool:
        cards = list(pool.map(lambda v: _safe(xano.cached_vendor_card, v), recent))
    return [c["vendor_id"] for c in cards if c and (c.get("category") or "Venue") == "Venue"][:MAX_VENUES]


# ---------------------------------------------------------------- one venue

def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return 0


def _clean_words(text, cap=90):
    t = " ".join(str(text or "").replace("_", " ").split())
    return t[:cap]


def _list(v):
    if isinstance(v, str):
        v = re.split(r"[,;|]", v)
    return [_clean_words(x, 40) for x in v or [] if str(x or "").strip()]


def _image(img):
    if isinstance(img, dict):
        img = img.get("url") or ""
    return img if isinstance(img, str) else ""


def _place(card):
    addr = str(card.get("address") or "")
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if parts and parts[-1].lower() in ("usa", "united states", "us"):
        parts = parts[:-1]
    state = card.get("state")
    state = state if isinstance(state, str) else ", ".join(state or [])
    # "80 Wythe Ave, Brooklyn, NY 11249" -> Brooklyn; anything without a "ST 12345" ending is not guessed at
    city = parts[-2] if len(parts) >= 3 and re.match(r"[A-Z]{2}( [0-9]{5})?", parts[-1]) else ""
    return ", ".join(x for x in (city, state) if x)


def _match_space(details, label):
    spaces = (details or {}).get("spaces") or []
    lab = (label or "").strip().lower()
    sp = next((s for s in spaces if lab and (s.get("space") or "").strip().lower() == lab), None)
    return sp or (spaces[0] if len(spaces) == 1 else None)


def _facts(card, details, space_label=""):
    """What the place is like. No money, safe at every level."""
    sp = _match_space(details, space_label)
    about, g = (details or {}).get("about") or {}, (details or {}).get("google") or {}
    room_seats = _int((sp or {}).get("Max_Capacity_Seated"))
    seats = room_seats or _int(card.get("max_capacity_seated"))
    types = _list(about.get("venue_type") or card.get("venue_type"))
    models = [x for x in types if "inclusive" in x.lower() or "catering" in x.lower() or "raw space" in x.lower()]
    types = [x for x in types if x not in models]      # a pricing style in the type field is not a setting
    out = {"setting": types[:2], "style": _list(about.get("vibes"))[:3],
           "seats": seats or None, "seats_is_room": bool(room_seats and space_label),
           "offering": _clean_words(about.get("offering"), 60) or (models or [None])[0],
           "rating": g.get("rating"), "reviews": _int(g.get("review_count")) or None}
    return out


def _cost(e, inp):
    lines = []
    for l in e["lines"]:
        key = _LINE_KEYS.get(l["item"])
        if not key:
            continue
        row = {"key": key, "label": _LABELS[key], "amount": int(round(l.get("amount") or 0)),
               "source": _SOURCES.get(l["source"], "missing")}
        if l.get("included_in_rental"):
            row["included"] = True
        if l["item"] == "Food and drink":
            if l.get("per_guest"):
                row["per_guest"] = round(l["per_guest"])
            if l.get("minimum_applies"):     # the amount IS the PDF's minimum, whatever the per-guest figure's source was
                row.update(minimum_applies=True, source="pdf")
                if l.get("minimum_only"):
                    row["minimum_only"] = True
        if l["item"] == "Service charge":
            row["pct"] = l.get("pct")
        if key == "ceremony" and row["source"] == "pdf" and not row["amount"]:
            row["included"] = True
        lines.append(row)
    return {"total": e["total_before_tax_and_gratuity"], "guests": e["guests"], "per_guest": e["per_guest"],
            "at_minimum": bool(e.get("priced_at_guest_minimum")), "lines": lines,
            "tax_pct": (e.get("tax") or {}).get("pct"), "estimated": [r["label"] for r in lines if r["source"] == "estimate" and r["amount"] > 0]}


def _word(vs):
    t = str(vs or "").lower()
    if not t or _DIGIT.search(t):
        return None
    return next((w for k, w in _WORDS if k in t), None)


def _word_rows(p):
    out, seen = [], set()
    for l in (p or {}).get("lines") or []:
        title = str(l.get("line") or "").lower()
        key = next((k for frag, k in _WORD_KEYS if frag in title), None)
        w = _word(l.get("vs_market")) if l.get("source") == "venue" else None   # a stand-in is not this venue's price
        if key and w and key not in seen:
            seen.add(key)
            out.append({"key": key, "label": _LABELS[key], "word": w})
    return out


def _safe(f, *a):
    try:
        return f(*a)
    except Exception as ex:
        print("compare:", getattr(f, "__name__", "?"), repr(ex)[:160])
        return None


def _one(xano, token, user, vid, guests, full):
    card = _safe(xano.cached_vendor_card, vid)
    if not card or (card.get("category") or "Venue") != "Venue":
        return None
    p = _safe(xano.cached_venue_pricing, token, vid, full, user)
    details = _safe(xano.cached_venue_details, vid, full)
    pdfs = _safe(lambda v: xano.pdf_summary(xano.pdf_rows(token, v)), vid) or {}
    inp = (p or {}).get("cost_inputs") if full else None
    e = estimate.compute(inp, guests) if inp else None
    room = (e or {}).get("space") or ""
    v = {"vendor_id": card.get("vendor_id"), "vendor_idx": card.get("vendor_idx"), "name": card.get("name") or "",
         "place": _place(card), "image": _image(card.get("image")), "category": "Venue",
         "room": room or None, "pdf_count": _int(pdfs.get("count")), "pdf_year": (pdfs.get("years") or [None])[0]}
    v.update(_facts(card, details, room))
    gm = _int((p or {}).get("guest_minimum"))
    v["guest_minimum"] = gm or None
    v["fits"] = {"seats": (v["seats"] >= guests) if v["seats"] and guests else None,
                 "minimum": (gm <= guests) if gm and guests else None}
    if full:
        v["cost"] = _cost(e, inp) if e else None
        v["off_peak"] = None
        o = estimate.off_peak_override(details, inp.get("space")) if inp else None
        # The lowest-Saturday price comes from table 36, the peak from the panel: 4 of 20 disagree on the peak (different
        # room or an older PDF, 2026-11-22), so compare them only when both agree on the peak rental.
        sp = _match_space(details, room) or {}
        if o and estimate._num(sp.get("Venue_Fee_on_a_Peak_Season_Saturday")) == inp.get("rental"):
            # An off-peak sheet that repeats the peak price is not a cheaper date: show it only when it changes the total.
            eo = estimate.compute(inp, guests, o)
            if eo and eo["total_before_tax_and_gratuity"] < e["total_before_tax_and_gratuity"]:
                v["off_peak"] = dict(_cost(eo, inp), months=_clean_words(o.get("months"), 60) or None)
    else:
        v["words"] = _word_rows(p)
    return v


# ---------------------------------------------------------------- what's different (facts, never a verdict)

def _money(n):
    return "$" + format(int(round(n)), ",")


def _and(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def differences(venues, guests, full, limit=3):
    out = []
    if full:
        priced = [v for v in venues if v.get("cost")]
        same = {v["cost"]["guests"] for v in priced}
        if len(priced) >= 2 and len(same) == 1:
            lo, hi = min(priced, key=lambda v: v["cost"]["total"]), max(priced, key=lambda v: v["cost"]["total"])
            gap = hi["cost"]["total"] - lo["cost"]["total"]
            est = " (some lines estimated)" if lo["cost"]["estimated"] or hi["cost"]["estimated"] else ""
            low = [v["name"] for v in (lo, hi) if any(l.get("minimum_only") for l in v["cost"]["lines"])]
            if low:     # a total that is only a food minimum is a floor, so the gap is not a like-for-like gap
                est = " (food at %s is only %s minimum and may cost more)" % (_and(low), "their" if len(low) > 1 else "its")
            if gap >= 500:
                out.append("%s comes to about %s more than %s for %d guests%s."
                           % (hi["name"], _money(round(gap, -2)), lo["name"], same.pop(), est))
            elif gap >= 0:
                out.append("%s and %s come to about the same for %d guests%s." % (lo["name"], hi["name"], same.pop(), est))
        inc = [v for v in priced if any(l["key"] == "food" and l.get("included") for l in v["cost"]["lines"])]
        if inc and len(inc) < len(priced):
            out.append("Food and drink is inside the package price at %s, and added on top at %s."
                       % (_and([v["name"] for v in inc]), _and([v["name"] for v in priced if v not in inc])))
    else:
        for key in ("rental", "food"):
            got = [(v["name"], next((w["word"] for w in v.get("words") or [] if w["key"] == key), None)) for v in venues]
            got = [(n, w) for n, w in got if w]
            if len(got) >= 2 and len({w for _, w in got}) > 1:
                out.append("%s: %s." % (_LABELS[key], ", ".join("%s is %s" % (n, w) for n, w in got)) )
    for v in venues:
        if v["fits"]["minimum"] is False:
            out.append("%s has a %d guest minimum, above your %d." % (v["name"], v["guest_minimum"], guests))
        elif v["fits"]["seats"] is False:
            where = "%s (%s)" % (v["name"], v["room"]) if v.get("seats_is_room") and v.get("room") else v["name"]
            out.append("%s lists seating for %d, below your %d." % (where, v["seats"], guests))
    off = [v for v in venues if v.get("off_peak")]
    if full and off:
        v = max(off, key=lambda x: x["cost"]["total"] - x["off_peak"]["total"])
        out.append("%s is about %s lower on its lowest-priced Saturdays%s." % (
            v["name"], _money(round(v["cost"]["total"] - v["off_peak"]["total"], -2)),
            " (%s)" % v["off_peak"]["months"] if v["off_peak"]["months"] else ""))
    if len(out) < limit:
        seats = [v for v in venues if v.get("seats")]
        if len(seats) >= 2:
            lo, hi = min(seats, key=lambda v: v["seats"]), max(seats, key=lambda v: v["seats"])
            if hi["seats"] >= lo["seats"] * 1.25:
                out.append("%s seats up to %d; %s up to %d." % (hi["name"], hi["seats"], lo["name"], lo["seats"]))
    if len(out) < limit:
        kinds = [(v["name"], v["setting"][0]) for v in venues if v.get("setting")]
        if len(kinds) >= 2 and len({k for _, k in kinds}) > 1:
            out.append(", ".join("%s: %s" % x for x in kinds) + ".")
    return out[:limit]


# ---------------------------------------------------------------- build

def build(xano, token, user, vendor_ids, *, access, guests=0, default_guests=0, budget=0):
    """The widget, or None when fewer than two of the ids are venues."""
    ids = clean_ids(vendor_ids)
    if len(ids) < MIN_VENUES:
        return None
    asked = _int(guests) if 2 <= _int(guests) <= MAX_GUESTS else 0
    g = asked or _int(default_guests)
    known = bool(g)
    g = g or 100
    full = bool(access and xano.pi_entitled(user))
    with ThreadPoolExecutor(max_workers=MAX_VENUES) as pool:
        venues = [v for v in pool.map(lambda i: _one(xano, token, user, i, g, full), ids) if v]
    if len(venues) < MIN_VENUES:
        return None
    w = {"type": TYPE, "v": V, "level": "full" if full else "words",
         "reason": None if full else ("member" if access else "plan"), "guests": g, "guests_known": known,
         "budget": _int(budget) or None, "venues": venues, "differences": differences(venues, g, full)}
    return w if full else lock(w)


_WORD_KEEP = ("vendor_id", "vendor_idx", "name", "place", "image", "category", "pdf_count", "pdf_year", "setting", "style",
              "seats", "offering", "rating", "reviews", "guest_minimum", "fits", "words")


def lock(w):
    """The words-only version of a widget (also for a stored full one when the plan has lapsed): a whitelist, so a
    price can't ride along in a key nobody thought of."""
    vs = []
    for v in (w or {}).get("venues") or []:
        k = {x: v.get(x) for x in _WORD_KEEP}
        k["words"] = [{"key": r.get("key"), "label": r.get("label"), "word": r.get("word")}
                      for r in v.get("words") or [] if r.get("word") in {"below typical", "typical", "above typical"}]
        vs.append(k)
    diffs = [d for d in w.get("differences") or [] if "$" not in d] if w.get("level") == "words" else (
        differences([dict(v, fits=v.get("fits") or {"seats": None, "minimum": None}, setting=v.get("setting") or [])
                     for v in vs], w.get("guests") or 100, False))
    return {"type": TYPE, "v": V, "level": "words", "reason": w.get("reason") or "plan", "guests": w.get("guests"),
            "guests_known": w.get("guests_known"), "budget": None, "venues": vs, "differences": diffs}


def summary_text(w):
    """The line stored with the widget (no model); later turns of the chat read it."""
    names = [v["name"] for v in w["venues"]]
    if w["level"] == "full":
        est = any((v.get("cost") or {}).get("estimated") for v in w["venues"])
        return "Here are %s side by side for %d guests.%s" % (_and(names), w["guests"], " Estimated parts are marked." if est else "")
    return "Here are %s side by side for %d guests. Their exact prices are in each venue's pricing PDF." % (_and(names), w["guests"])
