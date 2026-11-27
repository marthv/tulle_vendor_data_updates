"""Side-by-side comparison of 2-3 venues the couple picked (owner 2026-11-22: "it feels like a word problem").

POST /rec/tool {tool: "compare_picked", args: {vendor_ids: [...], guests?}}. Computed in code: no model call, no
question used, no new table. Two levels, decided HERE and never by the page:
  full  = an active plan AND ep230 entitlement (xano.pi_entitled): the venue page's own numbers (estimate.compute).
  words = everyone else: ep231 only (never ep230) and only how each line compares with the state. No digit of a vendor
          price is ever in the payload; couples open the pricing PDF for the numbers.
A 1-week member has a plan but not ep230 (needs $50+ paid), so they are "words" with reason "member" - their PDFs open.
Nothing is ranked: venues come back in the order the couple picked them. REC_COMPARE_PICK=0 (default) = off."""
import contextvars
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


def _cat(card):
    return (card or {}).get("category") or "Venue"


def saved_ids(xano, user, category=None):
    """REC_COMPARE_ANY_CATEGORY: the couple's most recent saved vendors of ONE category (never mixed). category: the one
    asked for; none = the category of the newest saved vendor that has at least one other saved in the same category."""
    recent = list(dict.fromkeys(str(v).strip() for v in reversed((user or {}).get("favorited_vendors") or []) if str(v or "").strip()))[:24]
    with ThreadPoolExecutor(max_workers=6) as pool:
        cards = [c for c in pool.map(lambda v: _safe(xano.cached_vendor_card, v), recent) if c and c.get("vendor_id")]
    cats = [_cat(c) for c in cards]
    want = str(category or "").strip()
    if not want:
        want = next((k for k in cats if cats.count(k) >= MIN_VENUES), cats[0] if cats else "Venue")
    return list(dict.fromkeys(c["vendor_id"] for c in cards if _cat(c) == want))[:MAX_VENUES]


def saved_by_category(xano, user):
    """{category: count} of the couple's saved vendors (the page offers "Compare 2 photographers" per category)."""
    recent = list(dict.fromkeys(str(v).strip() for v in reversed((user or {}).get("favorited_vendors") or []) if str(v or "").strip()))[:24]
    with ThreadPoolExecutor(max_workers=6) as pool:
        cards = [c for c in pool.map(lambda v: _safe(xano.cached_vendor_card, v), recent) if c]
    out = {}
    for c in cards:
        out[_cat(c)] = out.get(_cat(c), 0) + 1
    return out


def saved_venue_ids(xano, user):
    """The couple's most recently saved VENUES (favorited_vendors is in save order; photographers etc. are left out)."""
    recent = list(dict.fromkeys(str(v).strip() for v in reversed((user or {}).get("favorited_vendors") or []) if str(v or "").strip()))[:12]
    with ThreadPoolExecutor(max_workers=6) as pool:
        cards = list(pool.map(lambda v: _safe(xano.cached_vendor_card, v), recent))
    return list(dict.fromkeys(c["vendor_id"] for c in cards if c and (c.get("category") or "Venue") == "Venue"))[:MAX_VENUES]


# ---------------------------------------------------------------- one venue

def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return 0


def _clean_words(text, cap=90):
    t = " ".join(str(text or "").replace("_", " ").split())
    return t[:cap]


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def _months(raw):
    """'January, February, December' -> 'Jan, Feb, Dec'; 'Not listed', 'N/A' and the like -> None."""
    t = " ".join(str(raw or "").split())
    hits = [m[:3] for m in _MONTHS if re.search(r"\b%s[a-z]*\b" % m[:3], t, re.I)]
    return ", ".join(hits) or None


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


def _one_other(xano, token, card, full):
    """A photographer, band, florist... (REC_COMPARE_ANY_CATEGORY): only what we hold for the whole vendor - its style words
    and its pricing PDFs. No price (QA 2026-11-26: the stored starting price can be the wrong package), no invented fields."""
    vid = card.get("vendor_id")
    pdfs = _safe(lambda v: xano.pdf_summary(xano.pdf_rows(token, v)), vid) or {}
    cat = _cat(card)
    style = [x for x in _list(card.get("venue_type")) if x.lower() != cat.lower() and "inclusive" not in x.lower()][:3]
    v = {"vendor_id": vid, "vendor_idx": card.get("vendor_idx"), "name": card.get("name") or "", "place": _place(card),
         "image": _image(card.get("image")), "category": cat, "room": None, "pdf_count": _int(pdfs.get("count")),
         "pdf_year": (pdfs.get("years") or [None])[0], "setting": [], "style": style, "seats": None, "seats_is_room": False,
         "offering": None, "rating": None, "reviews": None, "guest_minimum": None, "fits": {"seats": None, "minimum": None}}
    if not full:
        v["words"] = []
    return v


def _one(xano, token, user, vid, guests, full):
    card = _safe(xano.cached_vendor_card, vid)
    if card and _cat(card) != "Venue" and config.COMPARE_ANY_CATEGORY:
        return _one_other(xano, token, card, full)
    if not card or (card.get("category") or "Venue") != "Venue":
        return None
    vid = card.get("vendor_id") or vid       # a merged duplicate: the kept vendor for every read below, never a mix
    with ThreadPoolExecutor(max_workers=3) as pool:      # three reads at once per venue (9 s cold one after another)
        fp = pool.submit(_safe, xano.cached_venue_pricing, token, vid, full, user)
        fd = pool.submit(_safe, xano.cached_venue_details, vid, full)
        fv = pool.submit(_safe, lambda v: xano.pdf_summary(xano.pdf_rows(token, v)), vid)
        p, details, pdfs = fp.result(), fd.result(), fv.result() or {}
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
                v["off_peak"] = dict(_cost(eo, inp), months=_months(o.get("months")))
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
        # REC_COMPARE_DECIDE: no sentence says one venue costs more than another (2026-11-25): for audited pairs within 25% of each
        # other our totals name the pricier one right 54% of the time (reports/assistant_visuals.md).
        same = {v["cost"]["guests"] for v in priced}
        if not config.COMPARE_DECIDE and len(priced) >= 2 and len(same) == 1:
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
            where = "%s (%s)" % (v["name"], v["room"]) if v.get("seats_is_room") and v.get("room") and v["room"] != v["name"] else v["name"]
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


# ---------------------------------------------------------------- the couple's pick (REC_COMPARE_DECIDE)

PICK_SLOT = "Venue"
_PICK_LOCKS = [threading.Lock() for _ in range(64)]


def _pick_rows(xano, user_id, slot=PICK_SLOT):
    return [r for r in xano.plan_items(user_id, picks=True) if r.get("slot") == slot]


def pick_of(xano, user_id, slot=PICK_SLOT):
    """{"vendor_id", "name"} the couple picked, or None. The newest active row (of that category's slot)."""
    rows = _pick_rows(xano, user_id, slot)
    return {"vendor_id": rows[-1].get("vendor_id") or "", "name": rows[-1].get("vendor_name") or ""} if rows else None


def picks_of(xano, user_id):
    """REC_COMPARE_ANY_CATEGORY: {category: pick} - one pick per category."""
    out = {}
    for r in xano.plan_items(user_id, picks=True):
        if r.get("slot"):
            out[r["slot"]] = {"vendor_id": r.get("vendor_id") or "", "name": r.get("vendor_name") or ""}   # newest last
    return out


def set_pick(xano, user_id, vendor_id, chat_id=0, expect="", category=""):
    """Set (a venue id) or clear ("") the pick. Returns (ok, pick as stored now): ok only when what is stored is what was asked.
    The new row is written before the old one is marked removed, so a failure half way never loses a pick, and a row that
    could not be marked removed is older than the new one (pick_of reads the newest). LookupError = not a venue we list. expect: a
    clear is for that venue only, so a stale tab never clears a newer pick. Everything runs inside the couple's lock: taps
    are stored in the order they arrive.
    REC_COMPARE_ANY_CATEGORY: any listed vendor; its own category is the slot, one pick per category (a clear names the
    category in `category`, default Venue). Off: venues only, exactly as before."""
    anyc = config.COMPARE_ANY_CATEGORY
    with _PICK_LOCKS[int(user_id) % len(_PICK_LOCKS)]:
        card = None
        if vendor_id:
            ids = clean_ids([vendor_id])
            card = (xano.listed_vendor if anyc else xano.listed_venue)(ids[0]) if ids else None
            if not card:
                raise LookupError("vendor" if anyc else "venue")
        slot = (_cat(card) if card else (str(category or "").strip()[:40] or PICK_SLOT)) if anyc else PICK_SLOT
        old = _pick_rows(xano, user_id, slot)
        if not vendor_id and expect and old and old[-1].get("vendor_id") != expect:
            return False, pick_of(xano, user_id, slot)
        vid = str((card or {}).get("vendor_id") or "")
        newest = old[-1].get("vendor_id") if old else ""
        if vid and newest != vid:
            xano.plan_add({"user_id": user_id, "slot": slot, "vendor_id": vid, "vendor_name": (card.get("name") or "")[:200],
                           "est_cost": 0, "note": "", "source": xano.PICK_SOURCE, "chat_id": int(chat_id or 0), "status": "active"})
            drop = old
        else:
            drop = old[:-1] if vid else old
        for r in drop:       # oldest first, and clearing stops at the first failure: an older pick must never come back
            if not _safe(xano.plan_remove, user_id, r.get("id")) and not vid:
                break
        got = pick_of(xano, user_id, slot)
        return str((got or {}).get("vendor_id") or "") == vid, got


# ---------------------------------------------------------------- venues to compare a named one with (REC_NAMED_VENUE)

NEAR_LIMIT_MILES = 40


def _num(v):
    try:
        return bool(float(v))
    except (TypeError, ValueError):
        return False


def near_venues(xano, railway_search, token, card, guests=0, limit=2):
    """Up to `limit` venues near a named one that seat the couple's guests: name, place, photo, distance. One search of its
    state; no price is read or returned."""
    st = card.get("state")
    st = st if isinstance(st, str) else (st or [""])[0]
    if not st or "," in st or not card.get("vendor_id"):
        return []
    params = xano.venue_search_params(states=[st], guests=_int(guests), page_size=60)
    d, _ = railway_search.railway_get(token, params)
    items = [it for it in (d or {}).get("items") or [] if _num(it.get("lat")) and _num(it.get("lng"))
             and xano.geo_sanity.coords_match_state(it.get("State"), it.get("lat"), it.get("lng"))]
    me = next((it for it in items if it.get("Vendor_ID") == card["vendor_id"]), None)
    if not me:      # further down the list than one page: its own coordinates by name
        p = dict(xano.venue_search_params(states=[st], keyword=re.sub(r"[^a-z0-9 ]", " ", (card.get("name") or "").lower()), page_size=10))
        dd, _ = railway_search.railway_get(token, p)
        me = next((it for it in (dd or {}).get("items") or [] if it.get("Vendor_ID") == card["vendor_id"]
                   and _num(it.get("lat")) and _num(it.get("lng"))), None)
    if not me:
        return []
    mine = re.sub(r"[^a-z0-9]", "", (card.get("name") or "").lower())
    out, names = [], {mine}
    for it in sorted(items, key=lambda x: xano.miles(float(me["lat"]), float(me["lng"]), float(x["lat"]), float(x["lng"]))):
        miles = xano.miles(float(me["lat"]), float(me["lng"]), float(it["lat"]), float(it["lng"]))
        key = re.sub(r"[^a-z0-9]", "", (it.get("Name") or "").lower())
        if miles > NEAR_LIMIT_MILES or not key or any(key in n or n in key for n in names):
            continue
        names.add(key)
        out.append({"vendor_id": it.get("Vendor_ID"), "vendor_idx": it.get("id"), "name": it.get("Name"), "category": "Venue",
                    "state": it.get("State"), "address": it.get("Address"), "image": _image(it.get("image_1"))})      # no distance: 750 venues share another's pin
        if len(out) == limit:
            break
    return out


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
    by_id = {}
    for v in venues:     # a merged duplicate picked with its kept vendor is one venue
        by_id.setdefault(v["vendor_id"] or id(v), v)
    venues = list(by_id.values())
    if config.COMPARE_ANY_CATEGORY and venues:      # one category per compare, never mixed: the first one picked decides
        venues = [v for v in venues if v.get("category") == venues[0].get("category")]
    if len(venues) < MIN_VENUES:
        return None
    w = {"type": TYPE, "v": V, "level": "full" if full else "words",
         "reason": None if full else ("member" if access else "plan"), "guests": g, "guests_known": known,
         "budget": _int(budget) or None, "venues": venues, "differences": differences(venues, g, full)}
    if config.COMPARE_ANY_CATEGORY:
        w["category"] = venues[0].get("category") or "Venue"
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
    out = {"type": TYPE, "v": V, "level": "words", "reason": w.get("reason") or "plan", "guests": w.get("guests"),
           "guests_known": w.get("guests_known"), "budget": None, "venues": vs, "differences": diffs}
    if w.get("category"):       # REC_COMPARE_ANY_CATEGORY widgets only
        out["category"] = str(w["category"])[:40]
    return out


# ---------------------------------------------------------------- ask first (REC_COMPARE_ASK, owner 2026-11-27)

PICKER = "compare_picker"
PICKER_MAX_OPTIONS = 12
ASK_TEXT = "Which venues do you want to compare? Pick 2 or 3."
FEW_TEXT = ("You need at least 2 venues to compare. Save a couple you like, or look one up by name, "
            "and they'll show up here to pick.")
# A general request to compare, with no venue named: "compare venues", "compare my saved venues", the Tools item's
# "Compare my venues and quotes", the agent's "Yes, compare my saved venues". "Compare The Plaza and Oheka" is NOT one
# (the model / the card buttons answer that).
_ASK_RE = re.compile(
    r"^\s*(?:yes[,!. ]*|ok(?:ay)?[,!. ]*|sure[,!. ]*)?(?:please\s+|can you\s+|could you\s+|let'?s\s+|i want to\s+|help me\s+)?"
    r"(?:compare|comparing)\s+(?:all\s+)?(?:of\s+)?(?:my|our|the|these|those|some)?\s*"
    r"(?:last\s+)?(?:saved|favou?rite[sd]?|liked|hearted|shortlisted)?\s*(?:venues?|options|places|picks|ones)?"
    r"(?:\s+(?:and|&)\s+(?:my\s+|our\s+)?quotes)?(?:\s+side\s+by\s+side)?(?:\s+(?:please|for me))?\s*[.!?]*\s*$", re.I)
_ASK_BARE = re.compile(r"^\s*(?:compare|compare saved|side by side|venue compare|compare tool)\s*[.!?]*\s*$", re.I)


# Per request: only a page that can DRAW the picker asks for it (?compare_ask=1, sent by the component from 11-27). An older
# component (or the vendor-page bubble on an old version) keeps today's answer, so the server can ship first.
REQ = contextvars.ContextVar("rec_compare_ask", default=False)


def ask_on(user):
    return bool(config.COMPARE_ASK) and bool(REQ.get()) and arm((user or {}).get("id")) == "compare"


def is_ask(text):
    t = str(text or "").strip()
    if not t or len(t) > 80:
        return False
    if _ASK_BARE.match(t):
        return True
    m = _ASK_RE.match(t)
    # "compare" alone with a trailing word we did not list (a venue name) never matches the anchored pattern
    return bool(m) and bool(re.search(r"venue|saved|favou?rite|liked|hearted|option|place|pick|quote|ones|side by side", t, re.I))


def _option(card, saved):
    return {"vendor_id": str(card.get("vendor_id")), "vendor_idx": card.get("vendor_idx"),
            "name": _clean_words(card.get("name") or "", 80), "place": _place(card), "image": _image(card.get("image")),
            "category": _cat(card), "saved": bool(saved)}


def picker(xano, user, chat_cards=()):
    """The question + the couple's venues to pick from: saved venues (newest first) then venues shown in this chat.
    Names, places and photos only - no price of any kind, so it is the same for every plan."""
    recent = list(dict.fromkeys(str(v).strip() for v in reversed((user or {}).get("favorited_vendors") or [])
                                if str(v or "").strip()))[:PICKER_MAX_OPTIONS * 2]
    with ThreadPoolExecutor(max_workers=6) as pool:
        cards = [c for c in pool.map(lambda v: _safe(xano.cached_vendor_card, v), recent) if c and c.get("vendor_id")]
    opts, seen = [], set()
    for c, saved in [(c, True) for c in cards] + [(c, False) for c in chat_cards or [] if isinstance(c, dict)]:
        vid = str(c.get("vendor_id") or "")
        if not vid or vid in seen or _cat(c) != "Venue" or not c.get("name"):
            continue
        seen.add(vid)
        opts.append(_option(c, saved))
    opts = opts[:PICKER_MAX_OPTIONS]
    return {"type": PICKER, "v": 1, "venues": opts, "min": MIN_VENUES, "max": MAX_VENUES,
            "enough": len(opts) >= MIN_VENUES}


_PICK_KEEP = ("vendor_id", "vendor_idx", "name", "place", "image", "category", "saved")


def lock_picker(w):
    """Whitelist rebuild for free couples (free_mode.widget): nothing but names, places and photos."""
    vs = [{k: v.get(k) for k in _PICK_KEEP} for v in (w or {}).get("venues") or [] if isinstance(v, dict)]
    for v in vs:
        for k in ("name", "place"):
            if "$" in str(v.get(k) or ""):
                v[k] = re.sub(r"\$\s?[\d,.]+[kKmM]?", "", str(v[k])).strip()
    return {"type": PICKER, "v": 1, "venues": vs, "min": MIN_VENUES, "max": MAX_VENUES, "enough": len(vs) >= MIN_VENUES}


def picker_text(w):
    return ASK_TEXT if (w or {}).get("enough") else FEW_TEXT


def picker_chips(w):
    """None: the picker carries its own Find venues / Look up a venue buttons (QA 11-27: chips repeated them, and a
    "Look up a venue" chip went to the model instead of the name search)."""
    return []


def chat_cards(history):
    """Venue cards shown in this chat, newest answer first (for the picker)."""
    out = []
    for t in reversed(list(history or [])):
        if (t or {}).get("role") == "assistant":
            out += [c for c in (t.get("cards") or []) if isinstance(c, dict) and not c.get("meta")]
    return out


def summary_text(w):
    """The line stored with the widget (no model); later turns of the chat read it."""
    names = [v["name"] for v in w["venues"]]
    if (w.get("category") or "Venue") != "Venue":   # REC_COMPARE_ANY_CATEGORY: no guest count for a photographer
        return "Here are %s side by side.%s" % (_and(names), "" if w["level"] == "full" else " Their exact prices are in each one's pricing PDF.")
    if w["level"] == "full":
        est = any((v.get("cost") or {}).get("estimated") for v in w["venues"])
        return "Here are %s side by side for %d guests.%s" % (_and(names), w["guests"], " Estimated parts are marked." if est else "")
    return "Here are %s side by side for %d guests. Their exact prices are in each venue's pricing PDF." % (_and(names), w["guests"])
