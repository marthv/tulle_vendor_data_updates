"""Whole-wedding proposal - "Plan my wedding" (owner 2026-11-15). OFF unless REC_WHOLE_WEDDING=1 (config.py).

Why: more than half of free couples ask one question and leave, and about 1 couple a week adds anything to My wedding -
couples don't know what to ask next. So the Planner goes first: from budget, guest count, area and date it lays out a
whole wedding (a venue + a pick or a clearly labelled estimate for every category of the plan sheet), totals it against
the budget, and keeps "My wedding" visibly in step. A wrong proposal is still useful: it gives the couple something to
react to ("too expensive", "we want a barn", "swap the photographer").

NO MODEL CALL. The proposal is assembled in code from the same lookups the chat's tools use, so a line can never be
"an estimate presented as a quote" - every row carries basis "real" (a figure from the vendor's pricing PDF in Tulle's
data) or "estimate" (Tulle's typical budget split of the couple's own budget, cost_tools.split_budget over table 19):
  Venue                 xano.search_venues / venues_near -> xano.cached_venue_pricing -> estimate.compute (the venue
                        page's all-in for their guest count - the chat's estimate_cost path; no new pricing maths)
  Photography, Music    xano.search_pe: the vendor's lowest matching package price from their PDF
  Flowers               xano.search_listings; a florist with flt_fl_* pricing gives its typical total, else an estimate
  Catering              inside the venue's all-in when that estimate has a food and drink line; else listing + estimate
  Planner, Hair & makeup  xano.search_listings (a name; the search returns no figure for them) + an estimate from the
                        couple's budget. The row says "Tulle has their pricing PDF" only after the vendor's PDF
                        list (xano.pdf_rows, ep93) was read and holds one - see say_estimates
  Everything else       the other budget-split categories (attire, rentals, stationery ...) as one estimate line
Tiers: figures only at detail "full". Anyone else gets the plan's SHAPE - names and estimates of their own budget, real
prices locked (never fetched from the paid endpoint, never put in the payload); "light" adds the rounded bands the chat's
light cards already show. free_mode.widget() re-locks a stored proposal for a lapsed member, and
app._ww_entitled re-applies the CURRENT entitlement to an earlier proposal before a reaction or an add is built from it.
Labels: a row is "real" only when its whole amount is that vendor's own priced data (venue_basis); anything part-
estimated is an estimate and says which part is real. row["amount_from"] says who may see or store the number.

My wedding: adds are table-33 rows (the store the My wedding screen reads - wedding.view), Selected false = "still
deciding"; "we already booked X for $Y" is a Selected true row with the couple's own number. Table 90 (plan.py) is not
written. The strip under the chat is computed from wedding.view, so the chat and the screen never disagree.

Pure helpers + the builder here; app.py owns the endpoints, the guard and the usage rows (kind "ww_proposal").
"""
import datetime as dt
import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import agent
import config
import cost_tools
import estimate
import free_mode
import plan
import prefs
import profile_questions
import wedding
import xano

V = 1
PROPOSAL = "wedding_proposal"       # widget type of the proposal card
CONTEXT = "ww_context"              # widget type of the "a few details" card
KIND = "ww_proposal"                # rec_usage kind of a counted proposal (guards count it as a question)
SLOTS = plan.SLOTS                  # (key, label) x 7 in booking order - the plan sheet's categories
SLOT_KEYS = plan.SLOT_KEYS
PRICED_SLOTS = ("Venue", "Photography", "Entertainment", "Flowers")    # categories with real prices in Tulle's data
LISTING_SLOTS = ("Food and Beverage", "Wedding Planner", "Beauty")     # listings only: an estimate carries the amount
NEAR_MILES = agent.NEAR_MILES
PRICE_N = agent.PRICE_WAVE          # venues priced per proposal: one parallel wave, like the chat's first wave
REAL = "Real price from Tulle data"
ESTIMATE = "Estimate"
YOURS = "Your price"
REAL_LOCKED = REAL + " · locked"            # a package price straight off the vendor's sheet, not shown on this plan
PRICED_LOCKED = "Priced in Tulle data · locked"   # a locked figure we cannot call real: it may be part or all estimate
ESTIMATE_LOCKED = ESTIMATE + " · locked"    # a re-locked estimate that was worked out from a vendor's data
# Where a row's amount comes from (row["amount_from"]) - what decides who may see or store it:
#   "vendor"  worked out from that vendor's data in Tulle (real OR estimate): full detail only, locked for anyone else
#   "split"   a share of the couple's own budget (Tulle's typical split): open to everyone
#   "yours"   the couple's own number
FROM_VENDOR, FROM_SPLIT, FROM_YOURS = "vendor", "split", "yours"
GUESTS_MIN, GUESTS_MAX = 2, 1500
BUDGET_MIN, BUDGET_MAX = 1000, 5_000_000
PRICE_MAX = 10_000_000
ACTIONS = ("start", "context", "cheaper", "nicer", "swap", "style", "budget", "booked")
LABEL_VENUES = "Finding venues that fit %d guests…"
LABEL_VENUES_ANY = "Finding venues that fit you…"
LABEL_PRICING = "Pricing the day…"
LABEL_BUDGET = "Checking your budget…"


# ---------------------------------------------------------------- who gets it

def bucket(user_id):
    """0-99, stable per account: sha256("wholewedding:" + user_id) % 100."""
    return int(hashlib.sha256(("wholewedding:%s" % user_id).encode()).hexdigest(), 16) % 100


def arm(user_id):
    """"off" (flag off) | "forced" (REC_WHOLE_WEDDING_USER_IDS) | "ww" | "control"."""
    if not config.WHOLE_WEDDING:
        return "off"
    try:
        uid = int(user_id or 0)
    except (TypeError, ValueError):
        uid = 0
    if not uid:
        return "control"
    if uid in config.WHOLE_WEDDING_USER_IDS:
        return "forced"
    return "ww" if bucket(uid) < config.WHOLE_WEDDING_PCT else "control"


def assigned(user_id):
    return arm(user_id) in ("ww", "forced")


# ---------------------------------------------------------------- what the couple typed

_NOT_WHOLE = (r"(?!\s+(?:budget|venue|photo|video|flower|florist|music|dj\b|band|cater|food|planner|coordinator|"
              r"timeline|dress|attire|shower|registry|website|invit|hair|make))")
_START = re.compile(
    r"\b(?:plan|design|propose|map out|put together|lay out)\b[^.?!\n]{0,24}\b(?:my|our|the|a) "
    r"(?:whole |entire |full |dream )?wedding\b" + _NOT_WHOLE +
    r"|\b(?:build|create|make|give|show|draft)\s+(?:me|us)\s+(?:a|an|our|my|the)\s+(?:whole |entire |full |complete |"
    r"proposed |dream )?wedding\b" + _NOT_WHOLE +
    r"|\bbuild\s+(?:my|our)\s+(?:whole |entire |full )?wedding\b" + _NOT_WHOLE +
    r"|\bwhole[- ]wedding (?:plan|proposal)\b", re.I)
_REFRESH = re.compile(r"^I updated my wedding details \(", re.I)     # the profile sheet's own follow-up message


def detect_start(text):
    """True for "plan my wedding" / "build me a wedding" / "plan our whole wedding for 120 guests" - never for a
    request about one category ("plan my wedding budget") or a long message (the model reads those)."""
    t = (text or "").strip()
    return bool(t) and len(t) <= 200 and bool(_START.search(t))


_AMOUNT = r"\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?(k|m|thousand|grand)?\b"
_MONEY_IN = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k|m)?\b|\b(\d{1,3}(?:\.\d+)?)\s?(k)\b"
                       r"|\b(\d{1,3}(?:,\d{3})+)\b(?!\s*(?:guests?|people|ppl|persons?))", re.I)
_GUESTS_IN = re.compile(r"\b(\d{1,4})\s*(?:guests?|people|ppl|persons?|heads)\b", re.I)


def money_in(text):
    """The first dollar amount in a message ("$60k", "$12,000", "45k", "60,000") as an int, or 0."""
    m = _MONEY_IN.search(text or "")
    if not m:
        return 0
    num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4)) if m.group(3) else (m.group(5), "")
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return 0
    v *= 1000 if (unit or "").lower() == "k" else 1_000_000 if (unit or "").lower() == "m" else 1
    return int(round(v))


def typed_context(text):
    """What a typed message says about the wedding: {guests?, budget?, state?} - used for THIS plan only (the page
    already offers to save a typed guest count or budget to the profile; a typed value is never written here)."""
    t = text or ""
    out = {}
    g = _GUESTS_IN.search(t)
    if g and 2 <= int(g.group(1)) <= 1500:
        out["guests"] = int(g.group(1))
    b = money_in(_GUESTS_IN.sub(" ", t))
    if 1000 <= b <= 5_000_000:
        out["budget"] = b
    low = " %s " % re.sub(r"[^a-z ]", " ", t.lower())
    for s in sorted(profile_questions.CITIES_BY_STATE, key=len, reverse=True):
        if " %s " % s.lower() in low:
            out["state"] = s
            break
    return out


_BOOKED = re.compile(r"\b(?:booked|hired|reserved|signed with|signed|locked in|put (?:a |our |the )?deposit|"
                     r"already have|already got|went with|going with|chose|picked)\b", re.I)
_CHEAPER = re.compile(r"\b(?:too expensive|too much|too pricey|too high|cheaper|less expensive|more affordable|"
                      r"can'?t afford|bring (?:it|the cost|the price|that) down|lower (?:the )?(?:cost|price|total)|"
                      r"over (?:our|my|the) budget|spend less|trim (?:it|the)|cut (?:it|the cost|costs))\b", re.I)
_NICER = re.compile(r"\b(?:nicer|fancier|more upscale|upscale|upgrade|step it up|splurge|higher[- ]end|"
                    r"more luxur\w+|luxur\w+|spend more|room to spend|go bigger)\b", re.I)
_SWAP = re.compile(r"\b(?:swap|change|replace|switch|different|another|other|new|don'?t (?:like|love|want)|"
                   r"do not (?:like|want)|not (?:a fan|into|feeling|sold)|hate|skip)\b", re.I)
_BUDGET = re.compile(r"\b(?:budget|spend|target|afford|max(?:imum)?|cap|ceiling|up to|no more than)\b", re.I)
_WANT = re.compile(r"\b(?:want|prefer|love|like|looking for|rather|more of|dream(?:ing)? of|thinking|how about|"
                   r"what about|instead|swap|change|switch|show)\b", re.I)
_CAT_WORD = re.compile(r"\b(venue|photograph\w*|videograph\w*|dj|band|music\w*|entertain\w*|flor\w+|flowers?|"
                       r"cater\w*|food|planner|coordinat\w*|hair|make[\s-]?up|beauty)\b", re.I)


def _slot_in(text):
    m = _CAT_WORD.search(text or "")
    if not m:
        return ""
    c = agent.resolve_category(m.group(1))
    return c if c in SLOT_KEYS else ""


def _style_in(text):
    for value, pat in profile_questions._STYLE_WORDS.items():
        if re.search(r"\b(?:%s)" % pat, text or "", re.I):
            return value
    return ""


_BOOK_VERB = r"(?:booked|hired|reserved|signed with|signed|locked in|went with|going with|chose|picked)"
_CAT_ALT = (r"(?:venue|photograph\w*|videograph\w*|dj|band|music\w*|entertain\w*|flor\w+|flowers?|cater\w*|food|"
            r"planner|coordinat\w*|hair(?: and make[\s-]?up)?|make[\s-]?up|beauty)")


def _booked_name(text):
    """The vendor name in "we booked our venue: Hackett House for $12,000" / "we booked Hackett House as our venue
    for $12k" / "our photographer is booked - Jane Doe, $4,500" / "already booked the venue, Rosewood Barn, $9,000".
    "" when the message doesn't name one (the reply then asks for it)."""
    t = re.sub(r"\s+", " ", text or "").strip()
    m = _MONEY_IN.search(_GUESTS_IN.sub(lambda g: " " * len(g.group(0)), t))
    head = t[:m.start()] if m else t                                   # the price is never part of the name
    hit = (re.search(r"(?::|\s[-–—])\s*(.+)$", head)
           or re.search(r"\b%s\s+(?!(?:our|my|the|a)\s+%s\b)(.+?)\s+(?:as|for|to be)\s+(?:our|my|the)\b" % (_BOOK_VERB, _CAT_ALT), head, re.I)
           or re.search(r"\b(?:our|my|the|a)\s+%s\b\s*(?:is|was|will be)?\s*,?\s*(?:booked|hired|reserved)?\s*,?\s*"
                        r"(?:is|at|with|called)?\s+(.+)$" % _CAT_ALT, head, re.I)
           or re.search(r"\b%s\s+(.+)$" % _BOOK_VERB, head, re.I))
    name = (hit.group(1) if hit else "").strip()
    for _ in range(3):
        name = re.sub(r"^(?:is|at|with|called|named|it'?s)\s+", "", name.strip(" .,-–—:;!"), flags=re.I)
        name = re.sub(r"\s+(?:for|at|costs?|cost us|is|was|and it (?:is|was|costs?))$", "", name.strip(" .,-–—:;!"), flags=re.I)
    if (len(name) < 2 or re.fullmatch(r"(?:(?:our|my|the|a)\s+)?" + _CAT_ALT, name, re.I)
            or re.fullmatch(_BOOK_VERB, name, re.I)):
        return ""
    return name[:200]


def reaction(text):
    """A plain-language reply to a proposal -> {"action", ...} or None (the model reads anything else, as before).
    booked {category, name, price} | budget {budget} | swap {category} | style {style} | cheaper | nicer."""
    t = (text or "").strip()
    if not t or len(t) > 240:
        return None
    if _REFRESH.match(t):
        return {"action": "start"}
    slot = _slot_in(t)
    amount = money_in(_GUESTS_IN.sub(" ", t))
    if _BOOKED.search(t) and slot:
        return {"action": "booked", "category": slot, "name": _booked_name(t), "price": amount}
    if amount >= 1000 and _BUDGET.search(t) and not slot:
        return {"action": "budget", "budget": amount}
    style = _style_in(t)
    if style and (_WANT.search(t) or len(t) <= 40) and slot in ("", "Venue"):
        return {"action": "style", "style": style}
    if slot and _SWAP.search(t):
        return {"action": "swap", "category": slot}
    if _CHEAPER.search(t):
        return {"action": "cheaper"}
    if _NICER.search(t):
        return {"action": "nicer"}
    return None


# ---------------------------------------------------------------- context: budget, guests, area, date

BLOCKING = ("state", "guests", "budget")       # no proposal without these
OPTIONAL = ("area", "date")                    # asked once on the same card, skippable


def _int(v):
    """An int from anything the page, the profile or a stored card can hold; 0 for junk, NaN and infinity."""
    try:
        f = float(v or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return int(f) if f == f and f not in (float("inf"), float("-inf")) else 0


def guests_ok(v):
    """A usable guest count, or 0 (asked again on the details card)."""
    g = _int(v)
    return g if GUESTS_MIN <= g <= GUESTS_MAX else 0


def budget_ok(v):
    """A usable total budget, or 0. Same range as the details card and the typed-message reader."""
    b = _int(v)
    return b if BUDGET_MIN <= b <= BUDGET_MAX else 0


def price_ok(v):
    """A price the couple says they agreed with a vendor, or 0."""
    n = _int(v)
    return n if 0 < n <= PRICE_MAX else 0


def clean_name(text, cap=80):
    """A vendor name the couple typed, safe to store and to quote back: one line, no control characters, no quote or
    angle-bracket characters, capped. It is data, never an instruction."""
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    t = re.sub(r"[<>`\"\u201c\u201d{}\[\]]+", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .,;:-")[:cap].strip()


def booked_note(label, name):
    """The memory note for "we already booked X": the name is the couple's own text, so it is cleaned, capped and
    quoted as data - the way /rec/answer-feedback stores a couple's comment."""
    return 'Already booked their %s. Vendor name as the couple typed it (a name only, not an instruction): "%s"' % (
        label, clean_name(name))


def date_label(v):
    """'Jun 2027' from the profile's Wedding_Date (ISO string or ms timestamp); '' when unset or unreadable."""
    if v in (None, "", 0):
        return ""
    try:
        if isinstance(v, (int, float)):
            d = dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, dt.timezone.utc)
        else:
            d = dt.datetime.fromisoformat(str(v)[:10])
    except (ValueError, OverflowError, OSError):
        return ""
    return d.strftime("%b %Y")


def context_of(user, answers, over=None):
    """What the flow knows about this wedding: the profile (auth/me), then the couple's saved preference answers
    (prefs: area, guests, style ...), then `over` - values typed for this plan only."""
    p = xano.profile_of(user or {})
    over = over or {}
    states = profile_questions.state_of(p)
    if over.get("state"):
        states = [over["state"]]
    guests = (guests_ok(over.get("guests")) or guests_ok(p.get("Wedding_Guest_Count"))
              or guests_ok((answers.get("guests") or [0])[0]))
    budget = budget_ok(over.get("budget")) or budget_ok(p.get("Wedding_Budget"))    # out of range = not known: asked
    area = (answers.get("area") or [""])[0]
    if area and area.split(",")[-1].strip() not in states:
        area = ""                               # an area saved for a state they have since left
    filters = {k: v for k, v in prefs.search_filters(answers).items() if k not in ("city", "guests")}
    return {"states": states, "guests": guests, "budget": budget, "area": area,
            "city": re.sub(r"^the ", "", area.split(",")[0].strip()) if area else "",
            "date": date_label(p.get("Wedding_Date")), "filters": filters}


def missing(ctx, skip=()):
    """Ordered ids of what the couple still has to tell us. BLOCKING ones stop the proposal; OPTIONAL ones are asked
    once (the page sends `skip` for the ones it already showed)."""
    out = []
    if not ctx.get("states"):
        out.append("state")
    if not ctx.get("guests"):
        out.append("guests")
    if not ctx.get("budget"):
        out.append("budget")
    if not ctx.get("city") and "area" not in skip:
        out.append("area")
    if not ctx.get("date") and "date" not in skip:
        out.append("date")
    return out


def blocked_by(need):
    return [f for f in need if f in BLOCKING]


def context_card(ctx, need):
    """The one compact question card: only the missing fields, in order. The page draws it from this."""
    states = ctx.get("states") or []
    fields = []
    for f in need:
        if f == "state":
            fields.append({"id": "state", "label": "Which state is the wedding in?", "kind": "state"})
        elif f == "guests":
            fields.append({"id": "guests", "label": "Roughly how many guests?", "kind": "number", "placeholder": "e.g. 120",
                           "options": [{"label": l, "value": v} for l, v in profile_questions.GUEST_OPTIONS]})
        elif f == "budget":
            fields.append({"id": "budget", "label": "What's your total budget?", "kind": "money",
                           "placeholder": "e.g. $45,000 or 45k"})
        elif f == "area":
            fields.append({"id": "area", "label": "Near which area?" if not states else
                           "Near which part of %s?" % (states[0] if len(states) == 1 else "your states"),
                           "kind": "choice", "optional": True, "options": profile_questions.area_options(states) if states else [],
                           "any_label": "Anywhere" if not states else "Anywhere in %s" % (
                               states[0] if len(states) == 1 else "those states")})
        elif f == "date":
            fields.append({"id": "date", "label": "When is it?", "kind": "month", "optional": True,
                           "any_label": "Not sure yet"})
    have = []
    if states:
        have.append(" and ".join(states))
    if ctx.get("guests"):
        have.append("%d guests" % ctx["guests"])
    if ctx.get("budget"):
        have.append("$%s budget" % format(ctx["budget"], ","))
    if ctx.get("date"):
        have.append(ctx["date"])
    card = {"type": CONTEXT, "v": V, "title": "A few details and I'll plan your wedding", "fields": fields, "have": have,
            "cta": "Plan my wedding", "note": "Saved to your wedding details - change them anytime."}
    if "state" in need and "area" in need:
        # the state is chosen on this card, so the page needs every state's area chips to show the right four
        card["areas"] = {s: profile_questions.area_options([s]) for s in profile_questions.CITIES_BY_STATE}
    return card


def clean_answers(raw, states=()):
    """The card's answers -> ({guests?, budget?, states?, date?} for the profile, area value or "", skip ids, bad ids).
    The profile part goes through app.profile_fields (the same validation as the profile sheet); `area` must be one
    of profile_questions' known areas (prefs never stores free text)."""
    raw = raw if isinstance(raw, dict) else {}
    prof, bad, skip, area = {}, [], [], ""
    if raw.get("guests") not in (None, ""):
        g = _int(str(raw["guests"]).replace(",", ""))
        if 2 <= g <= 1500:
            prof["guests"] = g
        else:
            bad.append("guests")
    if raw.get("budget") not in (None, ""):
        b = raw["budget"] if isinstance(raw["budget"], (int, float)) else money_in("$" + str(raw["budget"]).lstrip("$ "))
        b = _int(b)
        if 1000 <= b <= 5_000_000:
            prof["budget"] = b
        else:
            bad.append("budget")
    st = str(raw.get("state") or "").strip()
    if st:
        if st in profile_questions.CITIES_BY_STATE:
            prof["states"] = [st]
        else:
            bad.append("state")
    d = str(raw.get("date") or "").strip()
    if d:
        d = d + "-01" if re.fullmatch(r"\d{4}-\d{2}", d) else d[:10]     # a month is enough: stored as its 1st
        try:
            dt.date.fromisoformat(d)
            prof["date"] = d
        except ValueError:
            bad.append("date")
    elif "date" in raw:
        skip.append("date")
    a = str(raw.get("area") or "").strip()
    if a and a.lower() not in ("any", "anywhere"):
        ok_states = set(prof.get("states") or states or ())
        if a in profile_questions.all_area_values() and (not ok_states or a.split(",")[-1].strip() in ok_states):
            area = a
        else:
            bad.append("area")
    elif "area" in raw:
        skip.append("area")
    return prof, area, skip, sorted(set(bad))


# ---------------------------------------------------------------- the proposal

def _money(n):
    return "$" + format(int(round(n or 0)), ",")


def _state1(st):
    return st[0] if isinstance(st, list) and st else (st or "")


def split_amounts(token):
    """Tulle's typical budget split (table 19 through cost_tools.split_budget - the "Split my budget" tool's own
    maths) as a function of the target: target -> {category: {"amount", "pct"}}. One table read per proposal."""
    cats = xano.budget_categories(token)

    def at(target):
        w = cost_tools.split_budget(target, "normal", (), categories=cats)["widget"]
        return {c["name"]: {"amount": int(c["amount"]), "pct": int(c["pct"])} for c in w["categories"] if c["included"]}
    return at


def _row(slot, **k):
    base = {"slot": slot, "label": dict(SLOTS).get(slot, slot), "name": "", "vendor_id": "", "vendor_idx": None,
            "amount": None, "amount_from": "", "basis": "none", "badge": "No pick yet", "locked": False, "image": "",
            "band": "",
            "why": "", "based_on": "", "breaks": "", "added": False, "booked": False, "changed": False}
    base.update(k)
    return base


def _image(v):
    """The vendor's listing photo (table 11 image_1 - on every card the chat shows, for free couples too), or ""."""
    u = (v or {}).get("image")
    return u.strip()[:500] if isinstance(u, str) and u.strip().lower().startswith("https://") else ""


def _estimate_row(slot, share, target, listing=None, why=""):
    """A budget line that is NOT a quote: Tulle's typical split of the couple's own target, with a named listing when
    Tulle lists one. share = {"amount", "pct"} or None (no split available: the row says so, no number).
    With a listing, `why` is only the lead-in (how they fit); say_estimates adds the sentence about their pricing PDF
    once it has checked that they have one."""
    r = _row(slot, basis="estimate", badge=ESTIMATE)
    if share and share.get("amount"):
        r.update(amount=int(share["amount"]), amount_from=FROM_SPLIT)
        r["based_on"] = "Tulle's typical budget split: %d%% of a %s budget. Not a quote." % (share["pct"], _money(target))
    else:
        r.update(badge="No estimate yet", basis="none", based_on="No estimate could be worked out this time.")
    if listing:
        r.update(name=listing.get("name") or "", vendor_id=str(listing.get("vendor_id") or ""),
                 vendor_idx=listing.get("vendor_idx"), image=_image(listing), _lead=why,
                 why=" · ".join(x for x in (why, free_mode.WW_EST_PLAIN if r["amount"] else "") if x))
    else:
        r["why"] = why or "No pick yet. A starting number to plan around."
    return r


_PDFS = {}                   # vendor_id -> (count >= 1, unix time): vendors KNOWN to have a pricing PDF, nothing else
_PDFS_TTL_S = 6 * 3600
_PDFS_LOCK = threading.Lock()
_PDF_POOL = ThreadPoolExecutor(max_workers=8)     # its own pool: a slow PDF-list read never holds a proposal's thread
PDF_CHECK_S = 3.0            # all of one proposal's PDF-list reads together; past it the rows say nothing about a PDF


def pdf_count(token, vendor_id, meta=None):
    """How many pricing PDFs this vendor has (the vendor page's own list, ep93 - what "Pricing PDF" opens):
    n >= 1 = known to have one, 0 = this read came back empty, None = could not be read (unknown).
    Only a positive count is cached (and shared between accounts: "this vendor has a PDF" is true for everyone). An
    empty list or a failed read is never remembered, so one account's empty read cannot become "no PDF" for another
    account or for the next six hours."""
    vid = str(vendor_id or "")
    if not vid:
        return None
    with _PDFS_LOCK:
        hit = _PDFS.get(vid)
    if hit and time.time() - hit[1] < _PDFS_TTL_S:
        return hit[0]
    try:
        if meta:
            meta.call()
        n = int(xano.pdf_summary(xano.pdf_rows(token, vid))["count"])
    except Exception as e:
        print("ww pdf count failed:", vid, repr(e)[:120])
        return None
    if n > 0:
        with _PDFS_LOCK:
            if len(_PDFS) > 5000:
                _PDFS.clear()
            _PDFS[vid] = (n, time.time())
    return n


def estimate_why(lead, pdfs, reader, has_amount=True):
    """What an estimate row with a named vendor says. "Tulle has their pricing PDF" ONLY when the PDF list was read
    and holds at least one (pdfs > 0); unknown or none -> the plain line, which claims nothing about the vendor."""
    if not has_amount:
        tail = ""
    elif pdfs and pdfs > 0:
        tail = free_mode.WW_EST[free_mode.ww_reader(reader)]
    else:
        tail = free_mode.WW_EST_PLAIN
    return " · ".join(x for x in (lead or "", tail) if x)


def say_estimates(token, rows, reader, meta=None):
    """Finish the estimate rows built just now that name a vendor: read each one's PDF list (in parallel, together
    bounded by PDF_CHECK_S - a read that is late counts as unknown and the plan is delivered without it) and write
    the line this reader gets. Rows kept from an earlier proposal are not read again; proposal_wording keeps theirs
    right. The payload carries `pdfs` ONLY when the list was read: 0 = known to have none (the page then hides
    "Pricing PDF"), absent = unknown (the button stays). The internal lead-in never leaves this function."""
    todo = [r for r in rows if "_lead" in r]
    need = [r for r in todo if r.get("vendor_id") and not r.get("locked") and not r.get("booked")]
    futs = [(r, _PDF_POOL.submit(pdf_count, token, r["vendor_id"], meta)) for r in need]
    deadline = time.time() + PDF_CHECK_S
    for r, f in futs:
        try:
            n = f.result(timeout=max(0.0, deadline - time.time()))
        except Exception:      # late (or failed): unknown
            n = None
        if n is not None:
            r["pdfs"] = n
        r["why"] = estimate_why(r["_lead"], n, reader, bool(r.get("amount")))
    for r in todo:
        r.pop("_lead", None)
    return rows


def locked_why(fact, reader):
    """A locked row's line: the checked fact that says a PDF-derived price exists, then how this reader opens it."""
    return "%s %s" % (fact, free_mode.WW_OPEN[free_mode.ww_reader(reader)])


def _has_pdf_price(v):
    """True when the venue's search card itself carries a price read from its pricing PDF (table 11's fee range or
    food-and-drink minimum) - the checked fact a locked venue row rests on."""
    fee = v.get("venue_fee_range")
    return bool((isinstance(fee, (list, tuple)) and any(isinstance(x, (int, float)) and x > 0 for x in fee))
                or (isinstance(v.get("food_minimum"), (int, float)) and v["food_minimum"] > 0))


def _over(amount, cap, label):
    if amount and cap and amount > cap:
        return "Over your %s share (%s) by %s" % (label, _money(cap), _money(amount - cap))
    return ""


def _prev_amount(prev_row):
    """The figure "cheaper" / "nicer" compares against: the previous pick's own amount, when the couple may see it."""
    r = prev_row or {}
    return r.get("amount") if r.get("amount_from") == FROM_VENDOR and not r.get("locked") else None


def venue_basis(e):
    """How an all-in from estimate.compute may be labelled. -> (basis, badge, based_on).
    "real" ONLY when every line that puts money in the total comes from this venue's own price sheet. One estimated
    line (Tulle's food-and-drink or service estimate for venues like it) makes the whole amount an estimate, and the
    label says which part is real."""
    paid = [l for l in (e or {}).get("lines") or [] if (l.get("amount") or 0) > 0]
    pdf = [l["item"].lower() for l in paid if l.get("source") == "this venue's PDF"]
    est = [l["item"].lower() for l in paid if l.get("source") != "this venue's PDF"]
    if pdf and not est:
        return "real", REAL, "From this venue's pricing PDF: %s." % ", ".join(pdf)
    if pdf:
        text = "%s from their price sheet; %s estimated by Tulle. Not a quote." % (", ".join(pdf), ", ".join(est))
        return "estimate", ESTIMATE, text[0].upper() + text[1:]
    return "estimate", ESTIMATE, ("Tulle's estimate for venues this size. No rental or food price of this venue's own "
                                  "is in it. Not a quote.")


def choose(cands, cap, mode="base", prev_amount=None):
    """Pick from [(item, amount or None)] (popularity order). -> (item, amount) or None.
    base: the first priced one at or under the cap; none fits -> the lowest priced (the row then says what breaks);
          nothing priced -> the first one, amount None.
    cheaper / nicer (prev_amount known): the first one priced below / above the previous pick (nicer stays under the
          cap); None = nothing cheaper / nicer exists, keep the previous pick."""
    cands = list(cands or [])
    priced = [(v, a) for v, a in cands if a]
    if mode == "cheaper" and prev_amount:
        pool = [(v, a) for v, a in priced if a < prev_amount]
        if not pool:
            return None
        fit = [(v, a) for v, a in pool if not cap or a <= cap]
        return fit[0] if fit else min(pool, key=lambda x: x[1])
    if mode == "nicer" and prev_amount:
        pool = [(v, a) for v, a in priced if a > prev_amount and (not cap or a <= cap)]
        return pool[0] if pool else None
    fit = [(v, a) for v, a in priced if not cap or a <= cap]
    if fit:
        return fit[0]
    if priced:
        return min(priced, key=lambda x: x[1])
    return (cands[0][0], None) if cands else None


class _Meta:
    """Counts and timings of one build (the usage row and the Mixpanel event read them). Thread-safe: the category
    lookups run in parallel."""

    def __init__(self):
        self.calls = 0
        self.ms = {}
        self.categories = []
        self._lock = threading.Lock()

    def call(self, n=1):
        with self._lock:
            self.calls += n

    def timed(self, key, fn, *a, **k):
        t = time.time()
        try:
            return fn(*a, **k)
        finally:
            self.ms[key] = self.ms.get(key, 0) + int((time.time() - t) * 1000)


def _venue_search(token, ctx, filt, use_city, guests, slog, meta):
    states = ctx["states"]
    kw = {k: filt[k] for k in ("venue_types", "vibes", "pricing_models") if filt.get(k)}
    if filt.get("outdoor_ceremony"):
        kw["outdoor_ceremony"] = True
    if use_city and ctx.get("city") and states != ["International"]:
        try:
            meta.call()
            near = xano.geocode_city(ctx["city"], states)
        except Exception:
            near = None
        if near:
            try:
                meta.call()
                return xano.venues_near(near[0], near[1], NEAR_MILES, guests=guests, **kw)["venues"]
            except Exception:
                pass
    meta.call()
    return xano.search_venues(token, states=states, guests=guests, page_size=16, search_log=slog, **kw)["venues"]


def _venue_rows(token, user, ctx, full, detail, cap, share, target, mode, prev_row, exclude, slog, prog, meta, style=""):
    """(row or None, food, relaxed) for the venue. food = the venue estimate's own food and drink line, or None;
    relaxed = what had to be dropped to find anything ("style", "area", "guests"). row None = nothing (new) to show."""
    guests, budget = ctx["guests"], ctx["budget"]
    filt = dict(ctx.get("filters") or {})
    if style:
        filt["venue_types"] = [style]
    has_style = any(filt.get(k) for k in ("venue_types", "vibes", "pricing_models", "outdoor_ceremony"))
    has_city = bool(ctx.get("city"))
    # Each step is one search; the first that finds a venue wins. A style the couple just asked for is never dropped.
    ladder = [(filt, True, guests, [])]
    if has_style and not style:
        ladder.append(({}, True, guests, ["style"]))
    if has_city:
        ladder.append((filt if style else {}, False, guests, ["area"] + (["style"] if has_style and not style else [])))
    if not style:
        ladder.append(({}, False, 0, (["area"] if has_city else []) + (["style"] if has_style else []) + ["guests"]))
    venues, relaxed = [], []
    t0 = time.time()
    for f, use_city, g, rel in ladder:
        try:
            found = _venue_search(token, ctx, f, use_city, g, slog, meta)
        except Exception as e:
            print("ww venue search failed:", repr(e)[:200])
            found = []
        found = [v for v in found if v.get("vendor_id") and str(v["vendor_id"]) not in exclude]
        if found:
            venues, relaxed = found, rel
            break
    meta.ms["venue_search_ms"] = int((time.time() - t0) * 1000)
    if not venues:
        return None, None, relaxed
    # The chat's own fit filter (agent.search_venues): a food and drink minimum over half the budget never fits.
    food_cap = budget // 2 if budget > 0 else 0
    if food_cap:
        venues = [v for v in venues if not (v.get("food_minimum") or 0) > food_cap] or venues
    prog.check()
    prog.set(LABEL_PRICING)
    window = venues[:PRICE_N]
    t1 = time.time()

    def price(v):
        try:
            return xano.cached_venue_pricing(token, v["vendor_id"], full, user=user)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=max(1, len(window))) as ex:
        raws = list(ex.map(price, window))
    meta.call(len(window))
    meta.ms["venue_pricing_ms"] = int((time.time() - t1) * 1000)
    g_check = 0 if "guests" in relaxed else guests
    pairs = [(v, p) for v, p in zip(window, raws) if not agent._misfit(p, g_check, 0, 0)] or list(zip(window, raws))
    cands = []
    for v, p in pairs:
        e = estimate.compute((p or {}).get("cost_inputs"), guests) if full else None
        cands.append(((v, p, e), e["total_before_tax_and_gratuity"] if e else None))
    if "guests" in relaxed and mode == "base":
        # nothing seats them: the biggest room is the closest, whatever it costs (the row says what breaks)
        cands.sort(key=lambda c: -_int(c[0][0].get("max_capacity_seated")))
        picked = cands[0] if cands else None
    else:
        picked = choose(cands, cap, mode, _prev_amount(prev_row))
    if picked is None:
        return None, None, relaxed
    (v, p, e), amount = picked
    seats = _int(v.get("max_capacity_seated"))
    row = _row("Venue", name=v.get("name") or "", image=_image(v), vendor_id=str(v.get("vendor_id") or ""), vendor_idx=v.get("vendor_idx"),
               basis="real", badge=REAL)
    fit = []
    if seats:
        fit.append("Seats %d" % seats)
    if v.get("distance_miles") is not None and has_city and "area" not in relaxed:
        fit.append("%s miles from %s" % (v["distance_miles"], ctx["city"]))
    if "guests" in relaxed and seats and guests and seats < guests:
        row["breaks"] = "Seats %d and you have %d guests. I found no venue that seats %d here." % (seats, guests, guests)
    if "area" in relaxed and has_city:
        fit.append("nothing fits within %d miles of %s, so this is elsewhere in %s" % (
            NEAR_MILES, ctx["city"], _state1(v.get("state")) or "the state"))
    if "style" in relaxed:
        fit.append("no venue of your saved style fits, so this one drops that preference")
    food = None
    if full and e:
        lines = {l["item"]: l for l in e["lines"]}
        basis, badge, based_on = venue_basis(e)
        row.update(amount=int(amount), amount_from=FROM_VENDOR, basis=basis, badge=badge, based_on=based_on)
        fit.append("all-in for %d guests before tax and tip" % e["guests"])
        if e.get("priced_at_guest_minimum"):
            row["based_on"] += " " + e["priced_at_guest_minimum"]
        over = _over(amount, cap, "venue and food")
        row["breaks"] = ("%s %s." % (row["breaks"], over)) if row["breaks"] and over else row["breaks"] or over
        fl = lines.get("Food and drink") or {}
        if (fl.get("amount") or 0) > 0:
            food = {"amount": int(fl["amount"]), "from_pdf": fl.get("source") == "this venue's PDF"}
    elif full or not _has_pdf_price(v):
        # No all-in could be worked out for this venue (no cost inputs came back), or - for a couple without full
        # figures - its card carries no PDF-derived price to lock: a named pick, and the amount is the budget-split
        # estimate. What it says about the venue's PDF is decided in say_estimates, after reading its PDF list.
        est_row = _estimate_row("Venue", share, target, v, why=" · ".join(fit))
        row.update({k: est_row[k] for k in ("amount", "amount_from", "basis", "badge", "based_on", "_lead")})
        fit = [est_row["why"]]
    else:
        card = agent._with_rental(dict(v), p, guests, budget)
        band = (agent._light_card(card).get("price_band") or {}).get("label") if detail == "light" else ""
        # Not "real": without the figures we cannot know how much of this venue's all-in is its own price sheet.
        # Backing for the claim: _has_pdf_price(v) - the search card carries a fee range / food minimum read from its PDF.
        row.update(basis="locked", locked=True, badge=PRICED_LOCKED, band=band or "",
                   based_on=locked_why("Tulle has prices read from this venue's pricing PDF.", ctx.get("reader")))
    row["why"] = " · ".join(fit)
    return row, food, relaxed


def _pe_row(token, slot, ctx, full, cap, share, target, mode, prev_row, exclude, meta):
    """A photographer or entertainer with real package prices in Tulle's data; None = nothing (new) to show."""
    types = ["Wedding Day"] if slot == "Photography" else None
    meta.call()
    r = xano.search_pe(token, slot, states=ctx["states"], types=types, page_size=14)
    vendors = [v for v in r.get("vendors") or [] if v.get("vendor_id") and str(v["vendor_id"]) not in exclude]
    price = lambda v: (int(v["from_price"]) if isinstance(v.get("from_price"), (int, float)) and v["from_price"] > 0
                       else None)
    picked = choose([(v, price(v) if full else None) for v in vendors], cap, mode, _prev_amount(prev_row))
    if picked is None:
        return None
    v, amount = picked
    label = dict(SLOTS)[slot].lower()
    fit = " · ".join(x for x in (agent._pe_type_label(v), agent._hours_label(v)) if x).replace(" - ", ": ")
    if not price(v):
        # no package price came back for them: nothing to show AND nothing to lock - an estimate, for everyone
        return _estimate_row(slot, share, target, v, why=fit)
    row = _row(slot, name=v.get("name") or "", image=_image(v), vendor_id=str(v.get("vendor_id") or ""), vendor_idx=v.get("vendor_idx"),
               basis="real", badge=REAL, why=fit)
    if full:
        row.update(amount=amount, amount_from=FROM_VENDOR, breaks=_over(amount, cap, label),
                   based_on="Their lowest %spackage in the pricing PDF Tulle holds. Other packages cost more." % (
                       "wedding-day " if slot == "Photography" else ""))
    else:
        # Backing for the claim: price(v) - this vendor's from_price, the lowest package price read from their PDF.
        row.update(locked=True, badge=REAL_LOCKED,
                   based_on=locked_why("Tulle has a package price read from their pricing PDF.", ctx.get("reader")))
    return row


def _listing_pick(token, slot, ctx, exclude, slog, meta):
    meta.call()
    try:
        r = xano.search_listings(token, slot, states=ctx["states"], page_size=8, search_log=slog)
    except Exception as e:
        print("ww listing search failed:", slot, repr(e)[:200])
        return []
    return [v for v in r.get("vendors") or [] if v.get("vendor_id") and str(v["vendor_id"]) not in exclude]


def _flowers_row(token, ctx, full, detail, cap, share, target, mode, prev_row, exclude, slog, meta):
    vendors = _listing_pick(token, "Flowers", ctx, exclude, slog, meta)
    total = lambda v: _int((v.get("florist_pricing") or {}).get("typical_total")) or None
    if not full:
        if not vendors:
            return _estimate_row("Flowers", share, target)
        v = vendors[0]
        if not total(v):
            return _estimate_row("Flowers", share, target, v)
        band = agent._florist_note(v.get("florist_pricing"), "light") if detail == "light" else ""
        return _row("Flowers", name=v.get("name") or "", image=_image(v), vendor_id=str(v.get("vendor_id") or ""),
                    vendor_idx=v.get("vendor_idx"), basis="locked", badge=PRICED_LOCKED, locked=True, band=band or "",
                    # Backing: total(v) - a typical total worked out from this florist's own pricing PDF (flt_fl_*).
                    based_on=locked_why("Tulle has prices read from this florist's pricing PDF.", ctx.get("reader")))
    picked = choose([(v, total(v)) for v in vendors], cap, mode, _prev_amount(prev_row))
    if picked is None:
        return None if vendors or prev_row else _estimate_row("Flowers", share, target)
    v, amount = picked
    if not amount:
        return _estimate_row("Flowers", share, target, v)
    fp = v.get("florist_pricing") or {}
    return _row("Flowers", name=v.get("name") or "", image=_image(v), vendor_id=str(v.get("vendor_id") or ""),
                vendor_idx=v.get("vendor_idx"), amount=amount, amount_from=FROM_VENDOR, basis="estimate", badge=ESTIMATE,
                why=("%s tier" % fp["tier"]) if fp.get("tier") else "",
                # Not a price on their sheet: Tulle's typical order priced from it. So an estimate, and it says so.
                based_on="Tulle's typical wedding order priced from this florist's own price sheet. Your order will "
                         "differ. Not a quote.",
                breaks=_over(amount, cap, "flowers"))


def _listing_row(token, slot, ctx, share, target, exclude, slog, meta):
    vendors = _listing_pick(token, slot, ctx, exclude, slog, meta)
    return _estimate_row(slot, share, target, vendors[0] if vendors else None)


def _food_row(food, full):
    """Catering when the venue's all-in already counts food and drink: no second amount (it would be counted twice)."""
    if food and full:
        return _row("Food and Beverage", basis="included", badge=REAL if food["from_pdf"] else ESTIMATE,
                    why="Inside the venue's all-in above: %s of it is food and drink." % _money(food["amount"]),
                    based_on="From the venue's pricing PDF." if food["from_pdf"] else
                    "Tulle's estimate for this venue. No food price of its own is in it. Not a quote.")
    return None


def _yours(slot, item):
    return _row(slot, name=item.get("name") or "", vendor_id=str(item.get("vendor_id") or ""),
                amount=_int(item.get("price")) or None, amount_from=FROM_YOURS, basis="yours", badge=YOURS, booked=True,
                added=True,
                why="Booked by you", based_on="Your own number from My wedding.")


def slot_of_category(cat):
    c = str(cat or "").strip().lower()
    for key, label in SLOTS:
        if c in (key.lower(), label.lower()):
            return key
    return wedding.BY_TABLE[c]["key"] if c in wedding.BY_TABLE else ""


def slot_items(view):
    """wedding.view output -> {slot: {"booked": item or None, "considering": [items, newest first]}} for the 7 slots."""
    by = {k: {"booked": None, "considering": []} for k in SLOT_KEYS}
    for c in (view or {}).get("categories") or []:
        if c.get("key") in by:
            by[c["key"]]["booked"] = c.get("booked")
            by[c["key"]]["considering"] = list(c.get("quotes") or [])
    for e in (view or {}).get("extras") or []:
        s = slot_of_category(e.get("category"))
        if s and not by[s]["booked"]:
            by[s]["booked"] = e
    for q in (view or {}).get("considering") or []:
        s = slot_of_category(q.get("category"))
        if s:
            by[s]["considering"].append(q)
    return by


def _same(a, b):
    return bool(a) and bool(b) and str(a).strip().lower() == str(b).strip().lower()


def mark_added(rows, view):
    """Set each row's `added` from what My wedding already holds (same vendor, or same name in that category)."""
    by = slot_items(view)
    for r in rows:
        if r.get("basis") == "yours":
            continue
        s = by.get(r["slot"]) or {}
        items = ([s["booked"]] if s.get("booked") else []) + list(s.get("considering") or [])
        r["added"] = any((r.get("vendor_id") and _same(i.get("vendor_id"), r["vendor_id"]))
                         or _same(i.get("name"), row_name(r)) for i in items)
    return rows


def build(token, user, ctx, detail, *, prev=None, action="base", category="", style="", view=None, tracker=None,
          slog=None):
    """Assemble the proposal. -> (widget, meta). Raises progress.Stopped at a checkpoint once the couple taps Stop.
    prev: the proposal this one reacts to (its target, rows and swap history); action: base | cheaper | nicer | swap |
    style | budget; category: the slot a swap changes; style: a venue type the couple just asked for;
    view: wedding.view output - what the couple already booked is theirs and is never re-proposed."""
    import progress as progress_mod
    prog = tracker or progress_mod.NULL
    meta = _Meta()
    full = detail == "full"
    prev = prev if isinstance(prev, dict) and prev.get("type") == PROPOSAL else None
    if not prev and action not in ("base", "budget"):
        action = "base"
    pct = config.WHOLE_WEDDING_STEP_PCT
    budget = ctx["budget"]
    old_target = _int((prev or {}).get("target")) or budget
    moved = lambda sign: (old_target * (100 + sign * pct) + 5000) // 10000 * 100      # to the nearest $100, in integers
    target = (moved(-1) if action == "cheaper" else moved(1) if action == "nicer" else
              budget if action in ("base", "budget") else old_target)
    mode = action if action in ("cheaper", "nicer") else "base"
    prev_rows = {r.get("slot"): r for r in (prev or {}).get("rows") or [] if isinstance(r, dict)}
    exclude = {k: [str(x) for x in v] for k, v in ((prev or {}).get("exclude") or {}).items() if k in SLOT_KEYS}
    one = ("Venue" if action == "style" else category) if action in ("swap", "style") else ""
    if one:
        cur = str((prev_rows.get(one) or {}).get("vendor_id") or "")
        if cur and cur not in exclude.setdefault(one, []):
            exclude[one].append(cur)
    redo = {one} if one else set(SLOT_KEYS)
    booked = {s: v["booked"] for s, v in slot_items(view).items() if v["booked"]}
    for s, r in prev_rows.items():                     # a row the couple booked through the chat stays theirs
        if r.get("basis") == "yours" and s not in booked:
            booked[s] = {"name": r.get("name"), "price": r.get("amount"), "vendor_id": r.get("vendor_id")}
    redo -= set(booked)
    prog.check()
    prog.set((LABEL_VENUES % ctx["guests"]) if ctx.get("guests") else LABEL_VENUES_ANY)
    t0 = time.time()
    meta.call()
    split = meta.timed("split_ms", split_amounts, token)(target)
    share = split.get
    cap_of = lambda s: (split.get(s) or {}).get("amount") or 0
    venue_cap = cap_of("Venue") + cap_of("Food and Beverage")    # a venue's all-in counts its own food and drink
    ex = lambda s: set(exclude.get(s) or [])
    out, food, relaxed = {}, None, []
    t_v = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut = {}
        if "Venue" in redo:
            fut["Venue"] = pool.submit(_venue_rows, token, user, ctx, full, detail, venue_cap, share("Venue"), target, mode,
                                       prev_rows.get("Venue"), ex("Venue"), slog, prog, meta, style)
        for s in ("Photography", "Entertainment"):
            if s in redo:
                fut[s] = pool.submit(_pe_row, token, s, ctx, full, cap_of(s), share(s), target, mode, prev_rows.get(s),
                                     ex(s), meta)
        if "Flowers" in redo:
            fut["Flowers"] = pool.submit(_flowers_row, token, ctx, full, detail, cap_of("Flowers"), share("Flowers"),
                                         target, mode, prev_rows.get("Flowers"), ex("Flowers"), slog, meta)
        for s in LISTING_SLOTS:
            if s in redo:
                fut[s] = pool.submit(_listing_row, token, s, ctx, share(s), target, ex(s), slog, meta)
        for s, f in fut.items():
            try:
                res = f.result()
            except progress_mod.Stopped:
                raise
            except Exception as e:      # one category failing must not sink the plan: it becomes an estimate line
                print("ww %s failed:" % s, repr(e)[:200])
                res = (None, None, []) if s == "Venue" else None
            if s == "Venue":
                out[s], food, relaxed = res
            else:
                out[s] = res
    meta.ms["lookups_ms"] = int((time.time() - t_v) * 1000)
    meta.categories = [s.lower() for s in SLOT_KEYS if s in fut]
    prog.check()
    prog.set(LABEL_BUDGET)
    venue_redone = "Venue" in redo and out.get("Venue") is not None
    rows, changes = [], []
    for slot, label in SLOTS:
        old = prev_rows.get(slot)
        kept_why = ""
        if slot in booked:
            row = _yours(slot, booked[slot])
        elif slot == "Food and Beverage":
            inc = _food_row(food, full) if venue_redone else None
            if inc:                                         # the new venue's all-in already counts food and drink
                row = inc
            elif venue_redone and (old or {}).get("basis") == "included":
                row = out.get(slot) or _estimate_row(slot, share(slot), target)    # the new venue has no food line
            elif slot in redo and (old or {}).get("basis") == "included":
                row = dict(old)
                kept_why = "it is counted inside the venue's all-in, so swap the venue to change it"
            elif slot in redo:
                row = out.get(slot) or _estimate_row(slot, share(slot), target)
            else:
                row = dict(old or _estimate_row(slot, share(slot), target))
        elif slot in redo:
            row = out.get(slot)
            if row is None and old:
                row = dict(old)
                # only what this search checked: it found no other match (it reads the top results, not everything)
                kept_why = (("I found no %s venue that fits here" % style.split(" / ")[0].lower()) if action == "style" else
                            "I found no other match here" if action == "swap" else
                            "I found nothing cheaper here" if mode == "cheaper" else
                            "I found nothing nicer that still fits here" if mode == "nicer" else "")
            elif row is None and slot == "Venue":
                row = _row("Venue", badge="No venue found", why="I could not find a venue for %s this time." % (
                    " and ".join(ctx["states"]) or "this area"))
            elif row is None:
                row = _estimate_row(slot, share(slot), target, why="I could not find a %s to suggest here this time. "
                                    "A starting number to plan around." % label.lower())
        else:
            row = dict(old or _row(slot))
        row["changed"] = bool(prev and old and _changed(old, row))
        if row["changed"]:
            changes.append(_change_line(label, old, row))
        elif prev and old and slot in redo and (one or (kept_why and _prev_amount(old) is not None)):
            changes.append("%s: %s%s" % (label, kept_why or "I found no other match here",
                                         (", so I kept %s" % old["name"]) if old.get("name") else ""))
        rows.append(row)
    names = [n for n in split if n not in SLOT_KEYS]
    other_amount = sum(split[n]["amount"] for n in names)
    other = None
    if other_amount:
        other = {"label": "Everything else", "amount": other_amount, "basis": "estimate", "badge": ESTIMATE,
                 "why": ", ".join(names),
                 "based_on": "Tulle's typical budget split: %d%% of a %s budget. Not a quote." % (
                     sum(split[n]["pct"] for n in names), _money(target))}
    say_estimates(token, rows, ctx.get("reader"), meta)
    mark_added(rows, view)
    old_where = (prev or {}).get("where") or ""
    where = (old_where if one and not venue_redone and old_where else
             ("near %s" % ctx["area"]) if ctx.get("area") and "area" not in relaxed else
             "in " + " and ".join(ctx["states"]))
    if action == "cheaper":
        changes.insert(0, "Aimed %d%% lower: a %s target instead of %s" % (
            config.WHOLE_WEDDING_STEP_PCT, _money(target), _money(old_target)))
    elif action == "nicer":
        changes.insert(0, "Aimed %d%% higher: a %s target instead of %s" % (
            config.WHOLE_WEDDING_STEP_PCT, _money(target), _money(old_target)))
    elif action == "budget" and prev and old_target != target:
        changes.insert(0, "Planned to %s instead of %s" % (_money(target), _money(old_target)))
    widget = {"type": PROPOSAL, "v": V, "title": "Your wedding, proposed", "detail": detail, "mode": action,
              "total_label": "This plan's total",
              "guests": ctx["guests"], "budget": budget, "target": target, "where": where, "when": ctx.get("date") or "",
              "states": list(ctx["states"]), "area": ctx.get("area") or "", "rows": rows, "other": other,
              "exclude": exclude, "changes": changes,
              "alternates": [{"action": "cheaper", "label": "Make it cheaper"},
                             {"action": "nicer", "label": "Make it nicer"}],
              "note": "Real prices come from the pricing PDFs couples shared with Tulle. Estimates are a share of "
                      "your own budget, never a quote. Venue totals are before tax and tip."}
    total_up(widget)
    widget["changed"] = bool(not prev or any(r.get("changed") for r in rows) or target != old_target)
    meta.ms["total_ms"] = int((time.time() - t0) * 1000)
    return widget, meta


def row_name(r):
    return r.get("name") or ("%s (not chosen yet)" % r.get("label", r.get("slot", "")))


def _changed(old, new):
    return (str(old.get("vendor_id") or "") != str(new.get("vendor_id") or "") or (old.get("name") or "") != (new.get("name") or "")
            or old.get("amount") != new.get("amount") or old.get("basis") != new.get("basis"))


def _change_line(label, old, new):
    a = lambda r: (" (%s)" % _money(r["amount"])) if r.get("amount") and not r.get("locked") else ""
    if (old.get("name") or "") != (new.get("name") or ""):
        return "%s: %s%s instead of %s%s" % (label, new.get("name") or "an estimate", a(new),
                                             old.get("name") or "an estimate", a(old))
    if old.get("amount") != new.get("amount") and new.get("amount"):
        return "%s: %s instead of %s" % (label, _money(new["amount"]), _money(old.get("amount")) if old.get("amount") else "no figure")
    return "%s: updated" % label


def total_up(widget):
    """Total against the budget - full detail only (a locked plan has no total: its real prices are not in it)."""
    rows = widget.get("rows") or []
    if widget.get("detail") != "full":
        widget.update(total=None, over_under=None, verdict="", total_locked=True,
                      total_note="Your total unlocks with the real prices.")
        return widget
    total = sum(_int(r.get("amount")) for r in rows if r.get("basis") in ("real", "estimate", "yours"))
    total += _int((widget.get("other") or {}).get("amount"))
    budget = _int(widget.get("budget"))
    diff = total - budget if budget else None
    missing_rows = [r["label"] for r in rows if r.get("basis") in ("none",) or (r.get("basis") != "included" and not r.get("amount"))]
    widget.update(total=total, over_under=diff, total_locked=False,
                  verdict="" if diff is None else "over" if diff > 0 else "under" if diff < 0 else "on",
                  total_note=("%s over your %s budget" % (_money(diff), _money(budget)) if diff and diff > 0 else
                              "%s under your %s budget" % (_money(-diff), _money(budget)) if diff and diff < 0 else
                              "Right on your %s budget" % _money(budget) if budget else "")
                  + ((" · no figure yet for %s" % ", ".join(missing_rows)) if missing_rows else ""))
    return widget


def apply_booked(widget, slot, name, price):
    """The couple told us they booked this category themselves: the row becomes theirs, with their number."""
    w = dict(widget, rows=[dict(r) for r in widget.get("rows") or []], mode="booked")
    for i, r in enumerate(w["rows"]):
        if r.get("slot") == slot:
            w["rows"][i] = dict(_yours(slot, {"name": name, "price": price}), changed=True)
    if slot == "Venue":
        # their venue's price is their own number: the proposed venue's food line no longer applies
        for i, r in enumerate(w["rows"]):
            if r.get("slot") == "Food and Beverage" and r.get("basis") == "included":
                w["rows"][i] = _row("Food and Beverage", basis="none", badge="Check your venue's quote",
                                    why="Ask your venue whether food and drink is in the price you agreed.")
    w["changes"] = ["%s: %s, booked by you for %s" % (dict(SLOTS).get(slot, slot), name, _money(price))]
    w["changed"] = True
    return total_up(w)


def text_for(widget, action="base"):
    """The 2-3 sentences around the card. Figures only at full detail; never a price for anyone else."""
    rows = widget.get("rows") or []
    venue = next((r for r in rows if r.get("slot") == "Venue"), {})
    full = widget.get("detail") == "full"
    g, where = widget.get("guests"), widget.get("where") or ""
    head = "a whole wedding for %s guests %s" % (g, where)
    changes = [c for c in widget.get("changes") or [] if c]
    if action in ("cheaper", "nicer", "swap", "style", "budget", "booked") and changes:
        lead = {"cheaper": "Made it cheaper.", "nicer": "Made it nicer.", "swap": "Swapped.", "style": "Updated the venue.",
                "budget": "Re-planned to your new budget.", "booked": "Noted, and saved to My wedding as booked."}[action]
        s = lead + " " + ". ".join(changes[:4]) + "."
        if full and widget.get("total"):
            s += " The plan now comes to about %s%s." % (_money(widget["total"]), (", " + widget["total_note"]) if widget.get("total_note") else "")
        return s
    if not venue.get("name"):
        s = "Here's %s. I could not find a venue there to build it around this time, so the other lines are a start." % head
    elif venue.get("breaks"):
        s = "Here's %s. No venue I found fits everything, so %s is the closest: %s." % (
            head, venue["name"], venue["breaks"][0].lower() + venue["breaks"][1:].rstrip("."))
    else:
        s = "Here's %s, built around %s, with a pick or an estimate for every category." % (head, venue["name"])
    if full and widget.get("total"):
        s += " It comes to about %s%s." % (_money(widget["total"]), (", " + widget["total_note"]) if widget.get("total_note") else "")
        s += " Each line says whether it's a real price from Tulle's data or an estimate."
    else:
        s += (" Vendors' real figures stay locked on your plan: open a pick's PDF to see them, or unlock a plan to "
              "see the whole total here.")
    s += " Add what you like to My wedding, or tell me what's wrong and I'll change it."
    return s


# ---------------------------------------------------------------- My wedding: rows and the strip

FLOW_MARK = "Tulle Planner proposal · "


def _stamp(name, amount):
    """What an add leaves in Package_Name so a later add can tell "still exactly as the Planner wrote it" from "the
    couple has edited it": the amount written and a short hash of the name written."""
    return "ww:%d:%s" % (int(amount or 0), hashlib.sha1(str(name or "").strip().lower().encode()).hexdigest()[:6])


# The table-33 columns an add writes (package_ops) and the ones Xano fills in. flow_owned is an ALLOW-list over them:
# a value in ANY other column was put there by the couple. What the couple can edit: on the My wedding screen
# (POST /rec/wedding/item, /book, /unbook) Notes, Files, total_cost, Balance_Paid / Balance_Left and Selected; in the
# app's Budget tab also vendor_name, Package_Name, Category, Contact_Information (wedding._item shows it as "contact")
# and whatever other columns that tab has or gains - which is why this is not a list of their columns.
_FLOW_WRITES = frozenset(("user_id", "vendor_name", "total_cost", "Balance_Paid", "Balance_Left", "Category", "Selected",
                          "Notes", "Package_Name", "vendor_id"))
_ROW_SYSTEM = frozenset(("id", "created_at", "updated_at"))


def _blank(v):
    return v is None or v is False or (isinstance(v, str) and not v.strip()) or (
        isinstance(v, (int, float)) and v == 0) or (isinstance(v, (list, dict, tuple)) and not v)


def flow_owned(p):
    """True only for a table-33 row this flow wrote AND nobody has touched since: it carries the flow's marker, is
    still "deciding" (not booked), has no notes, no file and nothing paid, its name and amount are exactly the ones
    stamped when it was written, and EVERY column the flow does not write is still empty (a contact the couple typed,
    or any other couple-entered value, makes it theirs). Anything else is the couple's - a row they made, booked,
    renamed, re-priced or annotated - and is never replaced or changed here: a new row is added beside it.
    (No marker column exists; this errs towards "theirs".)"""
    pn = str(p.get("Package_Name") or "")
    if not pn.startswith(FLOW_MARK) or p.get("Selected") is True or wedding.is_example(p):
        return False
    if wedding.user_notes(p) or wedding.file_of(p) or p.get("Files") or _int(p.get("Balance_Paid")):
        return False
    if any(not _blank(v) for k, v in p.items() if k not in _FLOW_WRITES and k not in _ROW_SYSTEM):
        return False
    if _int(p.get("Balance_Left")) != _int(p.get("total_cost")):
        return False
    return pn.endswith(" · " + _stamp(p.get("vendor_name"), _int(p.get("total_cost"))))


def package_ops(user_id, widget, slots, packages=()):
    """What "Add to My wedding" writes to table 33 for the chosen proposal rows -> [(op, slot, row, item)].
    Rows are "still deciding" (Selected false), the shape the Budget tab's own unbooked packages have.
      add      nothing of the flow's in that category: a new row
      replace  the flow's own untouched row for that category holds a DIFFERENT vendor (the couple swapped, then
               added again): that row is rewritten in place, so a category never holds two Planner picks
      refresh  the flow's own untouched row holds the SAME vendor but the proposal's number moved: amount updated
    Idempotent per (category, vendor): a vendor My wedding already holds in that category - booked, the couple's own
    row, or ours - is never written twice. Rows the couple made, booked or edited are never changed (flow_owned).
    total_cost / Balance_Left carry the proposal's number at full detail; a locked row stores NO amount (0, the
    table's "no price yet") - a vendor's figure is not ours to copy out for a couple without a plan."""
    want = set(SLOT_KEYS) if slots in ("all", None) else {s for s in slots if s in SLOT_KEYS}
    pk = [p for p in packages or [] if not wedding.is_example(p)]
    newest = lambda ps: max(ps, key=lambda p: (p.get("created_at") or 0, p.get("id") or 0))
    out = []
    for r in (widget or {}).get("rows") or []:
        slot = r.get("slot")
        if slot not in want or r.get("basis") in ("yours", "included", "none") or r.get("booked"):
            continue
        vid, name = str(r.get("vendor_id") or ""), row_name(r)[:200]
        amount = 0 if r.get("locked") else max(0, min(_int(r.get("amount")), PRICE_MAX))
        basis = ("price locked - see the pricing PDF" if r.get("locked") else
                 "real price from Tulle data" if r.get("basis") == "real" else "estimate, not a quote")
        row = {"user_id": int(user_id), "vendor_name": name, "total_cost": amount, "Balance_Paid": 0,
               "Balance_Left": amount, "Category": wedding.table_category(slot), "Selected": False, "Notes": "",
               "Package_Name": "%s%s · tulle:%s · %s" % (FLOW_MARK, basis, vid or "manual", _stamp(name, amount))}
        if vid:
            row["vendor_id"] = vid
        in_slot = [p for p in pk if slot_of_category(p.get("Category")) == slot]
        same = [p for p in in_slot if (vid and _same(wedding._vid(p), vid)) or _same(p.get("vendor_name"), name)]
        if same:
            ours = [p for p in same if flow_owned(p)]
            if amount and len(ours) == len(same) and _int(newest(ours).get("total_cost")) != amount:
                out.append(("refresh", slot, row, newest(ours)))
            continue
        ours = [p for p in in_slot if flow_owned(p)]
        out.append(("replace", slot, row, newest(ours)) if ours else ("add", slot, row, None))
    return out


def booked_in(view, slot):
    """Every booked My wedding item of this category: its main booking, then the ones the screen files as extras."""
    cat = next((c for c in (view or {}).get("categories") or [] if c.get("key") == slot), {})
    return ([cat["booked"]] if cat.get("booked") else []) + [
        e for e in (view or {}).get("extras") or [] if slot_of_category(e.get("category")) == slot]


def booked_item(view, slot, name):
    """The booked My wedding item with this name in this category, or None."""
    return next((i for i in booked_in(view, slot) if _same(i.get("name"), name)), None)


def flow_row_named(packages, slot, name):
    """The flow's own untouched "still deciding" row for this vendor in this category (newest), or None: the row
    "we booked X" turns into the booking, instead of a second row for the same vendor."""
    mine = [p for p in packages or [] if slot_of_category(p.get("Category")) == slot and flow_owned(p)
            and _same(p.get("vendor_name"), name)]
    return max(mine, key=lambda p: (p.get("created_at") or 0, p.get("id") or 0)) if mine else None


def booked_changes(p):
    """What turns that row into the couple's booking: Selected, and the same Package_Name /rec/wedding/book writes
    (the vendor link kept). The price is set by the caller - the couple's own number, never the proposal's."""
    return {"Selected": True, "Package_Name": "Booked in My wedding · tulle:" + (wedding._vid(p) or "manual")}


def add_summary(ops):
    """One ready-made line for the strip when an add did more than one thing and not all of them were adds - the
    page prints a single change as it is, and counts several as "N added", which is wrong for a replace / update."""
    n = {k: sum(1 for op, *_ in ops if op == k) for k in ("add", "replace", "refresh")}
    if len(ops) < 2 or n["add"] == len(ops):
        return None
    parts = [("%d added" % n["add"]) if n["add"] else "", ("%d replaced" % n["replace"]) if n["replace"] else "",
             ("%d updated" % n["refresh"]) if n["refresh"] else ""]
    return ", ".join(x for x in parts if x) + " in My wedding"


def same_plan(a, b):
    """True when two proposals are the same plan for the same inputs (what was asked, who may see what, every pick
    and every number) - a repeat of it is re-shown, not charged again. What My wedding holds ("added") is ignored."""
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False

    def key(w):
        return ([w.get(k) for k in ("detail", "guests", "budget", "target", "states", "area", "when", "total")],
                [(r.get("slot"), str(r.get("vendor_id") or ""), r.get("name") or "", r.get("amount"), r.get("basis"),
                  bool(r.get("locked")), r.get("band") or "") for r in w.get("rows") or [] if isinstance(r, dict)],
                (w.get("other") or {}).get("amount"))
    return key(a) == key(b)


def booked_row(user_id, slot, name, price):
    """"We already booked X for $Y": the same row POST /rec/wedding/book writes (Selected true, their own number)."""
    price = max(0, min(_int(price), PRICE_MAX))
    return {"user_id": int(user_id), "vendor_name": str(name or "").strip()[:200], "total_cost": price, "Balance_Paid": 0,
            "Balance_Left": price, "Category": wedding.table_category(slot), "Selected": True, "Notes": "",
            "Package_Name": "Booked in My wedding · tulle:manual"}


def strip(view, changed=()):
    """The persistent "My wedding" line, from wedding.view output only (the screen's own numbers).
    started = slots with something booked or still being decided; planned = everything booked (the screen's
    booked_total) + the newest still-deciding item of each slot that has nothing booked."""
    if not isinstance(view, dict) or view.get("enabled") is False:
        return None
    by = slot_items(view)
    slots, planned = [], _int(view.get("booked_total"))
    for key, label in SLOTS:
        s = by[key]
        if s["booked"]:
            slots.append({"slot": key, "label": label, "state": "booked", "name": s["booked"].get("name") or ""})
        elif s["considering"]:
            top = s["considering"][0]
            planned += _int(top.get("price"))
            slots.append({"slot": key, "label": label, "state": "considering", "name": top.get("name") or ""})
        else:
            slots.append({"slot": key, "label": label, "state": "open", "name": ""})
    started = sum(1 for s in slots if s["state"] != "open")
    budget = _int(view.get("budget")) or None
    # "In My wedding so far" - what the couple already saved or booked there, NOT the proposal's total above it (the
    # first live test showed "$17,650 of $12,345 planned" beside a $12,905 plan and read as one number contradicting the other).
    label = ("In My wedding so far: %d of %d categories" % (started, len(SLOTS))) if started else "My wedding: nothing added yet"
    if planned and budget:
        label += " · %s of your %s budget" % (_money(planned), _money(budget))
    elif planned:
        label += " · %s" % _money(planned)
    return {"started": started, "of": len(SLOTS), "planned": planned, "booked_total": _int(view.get("booked_total")),
            "budget": budget, "label": label, "slots": slots, "changed": [c for c in changed if c][:6],
            "action": "Open my wedding"}


# ---------------------------------------------------------------- reading the flow back out of a chat

def _ww_widgets(turn):
    return [w for w in (turn or {}).get("widgets") or [] if isinstance(w, dict) and w.get("type") in (PROPOSAL, CONTEXT)]


def latest_proposal(history):
    for t in reversed(history or []):
        if t.get("role") == "assistant":
            for w in t.get("widgets") or []:
                if isinstance(w, dict) and w.get("type") == PROPOSAL:
                    return w
    return None


def pending_context(history):
    """The context card of the LATEST assistant turn, or None."""
    last = next((t for t in reversed(history or []) if t.get("role") == "assistant"), None)
    return next((w for w in _ww_widgets(last) if w.get("type") == CONTEXT), None)


def flow_active(history, within=3):
    """True while the whole-wedding flow is what the chat is about: one of the last few answers carries its card."""
    seen = 0
    for t in reversed(history or []):
        if t.get("role") != "assistant":
            continue
        if _ww_widgets(t):
            return True
        seen += 1
        if seen >= within:
            break
    return False
