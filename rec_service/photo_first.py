"""PHOTO-FIRST Assistant (owner 2026-11-26: "more pictures, less wording"). REC_PHOTO_FIRST=0 (default) = nothing here
runs and every response is exactly as before.

When on, app._pf() passes each answer through apply():
  text        one short line ("3 in Brooklyn for 100 guests"), never a paragraph. The full answer is not sent; it is
              still what is stored and what the model reads back.
  cards       each card gains `facts` - icons + words the card can show: Seats N and up to two setting tags we hold for
              the WHOLE venue (venue type, vibes). Never a guest minimum (that belongs to one room and season), never a
              pricing style (all-inclusive and the like are left out).
              Members (level full) also get `price_line` {text, source} on VENUE cards: one line naming where it came
              from - "Rental $7,000, from its PDF" or "About $38,100 for 100 guests, an estimate". Light: the rounded
              band, marked as an estimate. Free: no price line, and no price digit anywhere. Other vendors: no price line.
  understood  {place, guests, date} - what we searched with, as chips the couple can change.
  not_found   {name, question, chip} when we don't hold the venue they named.
Nothing is ranked or judged: no best / lowest / cheapest words are added here.

Also the "cost_lines" quick action (POST /rec/tool {tool: "cost_lines", args: {vendor_id, guests?}}): the venue page's own
lines (Rental, Food and drink, Ceremony, Service charge - the design's "Admin fee"; the PDF line is a service charge) with the room named, each "From the PDF" or "Estimate" (full), or for
everyone else only WHERE each part is ("In the PDF" / "Not in this PDF") with no digits.
Pure except build_cost_lines (xano is passed in)."""
import contextvars
import re

import config
import free_mode

# Per request (QA 2026-11-26): only a page that ASKS for photo-first gets it (?photo_first=1, sent by the component when its
# photoFirst prop is on). Every other Assistant instance (the vendor-page bubble...) gets today's answer even with the switch on.
REQ = contextvars.ContextVar("rec_photo_first", default=False)
PF_ONLY_KEYS = ("setting_tags", "rental_source")   # card keys only photo-first adds (stripped for every other request)


def on():
    return bool(config.PHOTO_FIRST and REQ.get())


def strip_cards(cards):
    return [{k: v for k, v in c.items() if k not in PF_ONLY_KEYS} if isinstance(c, dict) else c for c in cards or []]


def strip(out):
    """A response for a request that did not ask for photo-first: the card keys the switch adds are taken out again, so it
    is today's response."""
    if not isinstance(out, dict):
        return out
    res = dict(out)
    for k in ("cards", "more_cards"):
        if isinstance(res.get(k), list):
            res[k] = strip_cards(res[k])
    return res

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
        fee = (card.get("venue_fee_range") or [None])[0]
        if isinstance(fee, (int, float)) and fee > 0 and card.get("rental_source") == "pdf":
            return {"text": "Rental %s, from its PDF" % _usd(fee), "source": "pdf"}
        tot, g = card.get("estimate_total"), _int(card.get("estimate_guests"))
        if isinstance(tot, (int, float)) and tot > 0 and (card.get("category") or "Venue") == "Venue":
            t = "About %s for %d guests, an estimate" % (_usd(round(tot, -2)), g) if g else "About %s, an estimate" % _usd(round(tot, -2))
            if "minimum spend" in str(card.get("estimate_label") or ""):   # the stored caveat stays
                t += "; food at its minimum spend, may cost more"
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


def _area(out, cards):
    f = out.get("search_filters") or {}
    if f.get("city"):
        return str(f["city"]).split(",")[0].strip()
    st = f.get("states") or []
    if len(st) == 1 and st[0] != "International":     # "3 in International" says nothing: no place then
        return str(st[0])
    states = {c.get("state") for c in cards if isinstance(c.get("state"), str) and c.get("state")} - {"International"}
    return states.pop() if len(states) == 1 else ""


def _guests(out, profile):
    return _int((out.get("search_filters") or {}).get("guests")) or _int((profile or {}).get("Wedding_Guest_Count"))


_OUTSIDE = re.compile(r"It's outside (.+?), where you're planning")
_NOT_IN = re.compile(r"We don't have one in (.+?) - this is the")


def short_line(out, profile=None):
    """The one line above the cards. Says only what the cards can't."""
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
    if cards:
        cat = cards[0].get("category") or "Venue"
        noun = _NOUNS.get(cat, "")
        area, g = _area(out, cards), _guests(out, profile)
        n = len(cards)
        line = ("%d %s" % (n, noun)).strip() if noun else ("One" if n == 1 else str(n))
        if area:
            line += " in %s" % area
        if g and cat == "Venue":
            line += " for %d guests" % g
        return line if (area or g or noun) else ("Here it is" if n == 1 else "%d to look at" % n)
    s = first_sentence(text)
    if not s or len(s.split()) > MAX_WORDS or _BANNED.search(s):
        return NEUTRAL_WIDGET if out.get("widgets") else NEUTRAL_TEXT
    return s


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


def _free_safe(text):
    """A line a free couple may read: no price talk; guest counts are fine."""
    return not free_mode.has_price_talk(text or "")


def apply(out, level, profile=None):
    """The photo-first version of one answer (a new dict; `out` itself is not changed, so what is stored is the full text)."""
    if not isinstance(out, dict) or out.get("stopped"):
        return out
    free = level == "free" or bool(out.get("price_locked"))
    line = short_line(out, profile)
    if (free and not _free_safe(line)) or _BANNED.search(line):
        line = "Here are some to look at" if out.get("cards") else NEUTRAL_TEXT
    cards = []
    for c in out.get("cards") or []:
        if not isinstance(c, dict):
            cards.append(c)
            continue
        c2 = dict(c, facts=facts(c, free))
        pl = None if free else price_line(c, level)
        if pl:
            c2["price_line"] = pl
        cards.append(c2)
    # The full answer is not sent (QA 2026-11-26: today's paragraph judges price in 13 of 30 member answers); it is still
    # what is stored and what the model reads back.
    res = dict(out, text=line, cards=cards, photo_first=True, understood=understood(out, profile))
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
        if key == "service" and l.get("pct"):
            row["pct"] = l["pct"]
        lines.append(row)
    return dict(base, level="full", room=e.get("space") or None, lines=lines, total=c["total"],
                estimated=[r["label"] for r in lines if r["source"] == "estimate" and r["amount"]],
                total_note="%s%d guests, before tax and tip" % ((e.get("space") + ", ") if e.get("space") else "", g))


def cost_summary(w):
    """The line stored with the widget (no model)."""
    if w.get("level") == "full":
        return "What %s costs for %d guests%s." % (w["name"], w["guests"], ", some parts estimated" if w.get("estimated") else "")
    return "Where each part of %s's price is. The numbers are in its pricing PDF." % w["name"]
