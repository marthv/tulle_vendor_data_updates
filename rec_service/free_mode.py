"""FREE MODE (owner decisions 2026-10-14): signed-in users with no active plan can use the assistant, but
they get NOTHING about price - no dollar figures, ranges, per-head costs, percentages, fees, budget math, and
no relative price claims ("affordable", "budget-friendly", "below typical"). They get picks and non-price
reasons (capacity, style, location, rating, what's included) and every answer sends them to the vendor's
pricing PDF.

Enforced in CODE at three layers, not only in the prompt:
  1. Tools: price-bearing tools (venue_pricing, market_benchmarks, pe_market_rates) are not offered at all,
     and the search / saved / details tools offered are wrappers that drop price params (max_venue_fee,
     max_food_per_person, max_price) and strip every price field from what the model reads (see tools()).
  2. Output text: sanitize_text() drops every sentence that quotes a figure or makes a price claim, then
     makes sure the reply points at the pricing PDF.
  3. Payload: cards are rebuilt from a WHITELIST of non-price keys (card()) with price_locked=True, and
     chips about cost/budget are replaced (chips()).
Pure module (only json/re + the anthropic tool decorator) so it is unit-testable offline.
"""
import json
import re

import chips as _chips   # pure too (no app/config imports)

try:   # tests stub the SDK out; tools() is the only part that needs it
    from anthropic import beta_tool
except Exception:  # pragma: no cover
    beta_tool = None

# ---------------------------------------------------------------- what counts as price talk

# Figures: $ / currency amounts, "38K", "5,000 dollars", percentages.
_FIGURE = re.compile(
    r"[$€£]\s?\d"                                   # $38,000  EUR/GBP too
    r"|\b\d[\d,.]*\s?[kK]\b"                                  # 38K, 5k
    r"|\b\d[\d,.]*\s?(?:dollars?|bucks|usd|grand)\b"           # 5,000 dollars
    r"|\b\d[\d,.]*\s?(?:%|percent\b|per ?cent\b)",             # 22%, 8 percent
    re.I)
# Price language without a figure: relative price claims, budget math, price lines.
_PRICE_WORDS = re.compile(
    r"\b(?:affordab\w*|afford|inexpensive|expensive|pricey|pricier|priciest|cheap|cheaper|cheapest|"
    r"budget[- ]?friendly|bargain|splurge|low[- ]cost|lower[- ]cost|higher[- ]cost|cost[- ]effective|"
    r"economical|overpriced|reasonably priced|well[- ]priced|competitively priced|"
    r"(?:lower|higher|mid|premium|high)[- ]?(?:priced|range price|end price)|"
    r"(?:good|great|best|better|solid) value|value for (?:the )?money|"
    r"costs? (?:less|more)|charges? (?:less|more)|"
    r"(?:within|under|over|above|below|inside|fits?|fitting|stretch(?:es)?|exceeds?|blows?|on) "
    r"(?:your |the |their |a |this )?budget|budget (?:range|math|split|share|breakdown)|of (?:your|the|their) budget|"
    r"(?:below|above|under|over) (?:the )?(?:average|typical|market|state average)|"
    r"typical (?:price|cost|rate)s?|market rates?|price range|price point|"
    r"service charge|gratuit\w*|admin(?:istrative)? fees?|rental fees?|venue fees?|ceremony fees?|"
    r"minimum spend|(?:food|f&b) (?:and|&) (?:drink|beverage) minimum|f&b minimum|food minimum|"
    r"per[- ](?:head|person|guest|plate|hour)|a head|all[- ]in (?:cost|price|total)|"
    r"peak[- ]season (?:saturday )?(?:price|rate)s?|off[- ]peak (?:price|rate)s?|discount\w*|"
    r"tax(?:es)?|sales tax|dollars?)\b",
    re.I)

PDF_STEER = "Open the pricing PDF on each card to see exactly what they charge."
FALLBACK_TEXT = ("Here are picks that fit what you've told me so far. Exact pricing lives in each vendor's PDF "
                 "(and on Tulle plans), so open the pricing PDF on a card to see exactly what they charge.")


def has_price_talk(text):
    """True if text quotes a figure or makes any price / budget claim."""
    t = text or ""
    return bool(_FIGURE.search(t) or _PRICE_WORDS.search(t))


def _sentences(text):
    """Split into sentences, keeping line breaks as their own boundaries."""
    out = []
    for line in (text or "").split("\n"):
        out.extend(p for p in re.split(r"(?<=[.!?])\s+", line) if p.strip())
        out.append("\n")
    return out[:-1] if out else []


def sanitize_text(text):
    """(clean_text, sentences_removed). Drops each sentence that talks price, then guarantees a pointer to the
    pricing PDF. If too little is left to stand on its own, the whole reply becomes FALLBACK_TEXT."""
    kept, removed = [], 0
    for s in _sentences(text):
        if s == "\n":
            kept.append(s)
        elif has_price_talk(s):
            removed += 1
        else:
            kept.append(s)
    clean = re.sub(r"[ \t]*\n[ \t]*", "\n", " ".join(kept)).strip()
    clean = re.sub(r"\n{3,}", "\n\n", re.sub(r" +", " ", clean))
    if len(clean) < 40:
        return FALLBACK_TEXT, removed + (1 if clean else 0)
    if "pdf" not in clean.lower():
        clean = clean.rstrip() + " " + PDF_STEER
    return clean, removed


# ---------------------------------------------------------------- cards

# Everything a free card may carry. Anything else (venue_fee_range, food_minimum, at_a_glance, price_from,
# price_to, price_label, price_range, estimate_label, ...) is dropped - a whitelist, so a price field added to
# the card shape later stays hidden from free users by default.
CARD_KEYS = ("vendor_id", "vendor_idx", "name", "state", "address", "category", "venue_type",
             "max_capacity_seated", "image", "package_count", "packages_label", "hours_label", "more", "via_more")


def card(c):
    """A card safe for a free user: whitelisted keys, description and reason without price sentences."""
    out = {k: c[k] for k in CARD_KEYS if k in c and c[k] not in (None, "")}
    desc = " ".join(s for s in _sentences(c.get("description") or "") if s != "\n" and not has_price_talk(s))
    if desc.strip():
        out["description"] = desc.strip()[:300]
    reason = c.get("reason") or ""
    out["reason"] = "" if has_price_talk(reason) else reason
    out["price_locked"] = True
    return out


# ---------------------------------------------------------------- chips

_PRICE_CHIP = re.compile(r"cost|price|pricing|cheap|budget|afford|expens|fee|\$|\d\s?k\b|value|deal|minimum|"
                         r"charge|tax|per guest|per head|realistic|spend|money|off[- ]?peak", re.I)
STARTER_CHIPS = ["Try a different style", "Is a venue I like a good fit?", "Outdoor ceremony space",
                 "Change my guest count"]
# REC_COST_TOOLS_IN_CHAT on (2026-10-25): the whole-wedding estimate is open to free users (market averages, not
# vendor prices), so it takes the venue-fit slot the same way it does in chips.STARTER_CHIPS_COST_TOOLS.
STARTER_CHIPS_COST_TOOLS = ["Try a different style", "What will our wedding cost?", "Outdoor ceremony space",
                            "Change my guest count"]
TOPUPS = ["All-inclusive venues", "Bring-your-own-caterer venues", "Outdoor ceremony space", "Waterfront venues",
          "Historic Architecture venues", "Find a photographer"]


def _key(c):
    return re.sub(r"[^a-z0-9 ]", "", (c or "").lower()).strip()


def chips(chip_list, history=(), planning=False):
    """Drop chips about money (cost, cheaper, budget, fees, value, dates = off-peak pricing); top up to 2 from
    TOPUPS, skipping any chip already offered earlier in the chat. planning=True (REC_COST_TOOLS_IN_CHAT on,
    2026-10-25): next-planning-step and action chips pass even though they say "cost" / "budget" - the estimate,
    split and budget status are market averages or the couple's own numbers, and free compare shows only their
    own quotes. Price-FILTERED searches ("Find venues within my venue budget") still drop (chips.free_ok)."""
    offered = {_key(c) for t in history or () if t.get("role") == "assistant" for c in (t.get("chips") or [])}
    out, keys = [], set()
    for c in chip_list or []:
        k = _key(c)
        if k and k not in keys and ((planning and _chips.free_ok(c)) or not _PRICE_CHIP.search(c)):
            out.append(c)
            keys.add(k)
    for c in TOPUPS:
        if len(out) >= 2:
            break
        if _key(c) not in keys and _key(c) not in offered:
            out.append(c)
            keys.add(_key(c))
    for c in TOPUPS:   # everything offered already: repeat rather than show fewer than 2
        if len(out) >= 2:
            break
        if _key(c) not in keys:
            out.append(c)
            keys.add(_key(c))
    return out[:4]


# ---------------------------------------------------------------- cost-tool widgets (2026-10-25)

def widget(w):
    """A cost-tool widget safe for a free user. Estimate / split / budget status are market averages and the
    couple's own numbers (owner 2026-10-25: allowed). venue_compare must not show vendor PDF pricing: only the
    couple's manual quotes stay, locked=True with reason unlock_pdf_pricing (also for a widget stored while
    they had a plan)."""
    if not isinstance(w, dict) or w.get("type") != "venue_compare":
        return w
    return dict(w, rows=[r for r in w.get("rows") or [] if r.get("source") == "manual"], unpriced=[],
                locked=True, reason="unlock_pdf_pricing")


def widgets(ws):
    return [widget(w) for w in ws or []]


# ---------------------------------------------------------------- whole responses

def sanitize_result(result):
    """Apply the free rules to an agent.run result. Adds price_locked=True and price_redactions (sentences
    or reasons removed - logged, so leaks the prompt didn't prevent are measurable)."""
    text, removed = sanitize_text(result.get("text") or "")
    cards = [card(c) for c in result.get("cards") or []]
    removed += sum(1 for c, o in zip(result.get("cards") or [], cards) if (c.get("reason") or "") and not o["reason"])
    return dict(result, text=text, cards=cards, more_cards=[card(c) for c in result.get("more_cards") or []],
                chips=chips(result.get("chips")), widgets=widgets(result.get("widgets")), price_locked=True,
                price_redactions=removed)


def sanitize_turns(turns):
    """Stored conversation turns re-shown to a free user (e.g. a lapsed member's old chats): same rules."""
    out = []
    for t in turns:
        t = dict(t)
        if t.get("role") == "assistant":
            t["text"] = sanitize_text(t.get("text") or "")[0] if t.get("text") else t.get("text")
            t["cards"] = [card(c) for c in t.get("cards") or []]
            if "more_cards" in t:
                t["more_cards"] = [card(c) for c in t.get("more_cards") or []]
            t["chips"] = chips(t.get("chips")) if t.get("chips") else t.get("chips")
            if "widgets" in t:
                t["widgets"] = widgets(t.get("widgets"))
            t["price_locked"] = True
        out.append(t)
    return out


# ---------------------------------------------------------------- what the model reads

PROFILE_HIDDEN = ("Wedding_Budget",)


def profile_for_model(profile):
    """The couple's profile minus the budget: with no prices there is nothing to do with it except budget
    math, which free mode forbids. (search_venues still uses it server-side to leave out venues whose food
    minimum is over half the budget - a fit filter the model never sees.)"""
    return {k: v for k, v in (profile or {}).items() if k not in PROFILE_HIDDEN}


def _venue_row(row):
    r = {k: row[k] for k in ("vendor_id", "name", "state", "address", "venue_type", "max_capacity_seated",
                             "category", "distance_miles") if k in row}
    desc = " ".join(s for s in _sentences(row.get("description") or "") if s != "\n" and not has_price_talk(s))
    if desc.strip():
        r["description"] = desc.strip()[:300]
    p = row.get("pricing") or {}
    if "guest_minimum_fits" in p:
        r["guest_minimum_fits"] = p["guest_minimum_fits"]
    return r


def venue_result(raw):
    """search_venues / saved_venues JSON -> the same without any price: no venue_fee_range, no pricing lines,
    no vs-market labels, no verdict; only whether the guest minimum fits."""
    d = json.loads(raw)
    if isinstance(d.get("venues"), list):
        d["venues"] = [_venue_row(v) for v in d["venues"]]
    if isinstance(d.get("saved"), list):
        d["saved"] = [_venue_row(v) for v in d["saved"]]
    lo = d.get("left_out_for_minimums")
    if lo:   # the food-minimum filter is budget math - only say venues were left out for minimums
        d["left_out_for_minimums"] = sum(int(x or 0) for x in lo.values())
    return json.dumps(d)


_PE_ROW_KEYS = ("vendor_id", "name", "state", "category", "tags", "package_types", "package_count", "hours_range",
                "offers", "keyword_match")


def pe_result(raw):
    """search_photographers_entertainment JSON -> no prices, no price_range / card_price, no market rows, no
    vs-state price labels; package names with a $ and blurb sentences with figures removed."""
    d = json.loads(raw)
    d.pop("market", None)
    rows = []
    for v in d.get("vendors") or []:
        r = {k: v[k] for k in _PE_ROW_KEYS if k in v}
        blurb = " ".join(s for s in _sentences(v.get("blurb") or "") if s != "\n" and not has_price_talk(s))
        if blurb.strip():
            r["blurb"] = blurb.strip()[:300]
        pk = []
        for p in v.get("top_packages") or []:
            q = {k: p[k] for k in ("hours", "type", "team", "included", "addons") if k in p}
            name = p.get("name") or ""
            q["name"] = "Package" if has_price_talk(name) else name or "Package"
            q["included"] = [x for x in q.get("included") or [] if not has_price_talk(x)]
            q["addons"] = [x for x in q.get("addons") or [] if not has_price_talk(x)]
            pk.append({k: val for k, val in q.items() if val not in (None, "", [])})
        if pk:
            r["top_packages"] = pk
        rows.append(r)
    d["vendors"] = rows
    return json.dumps(d)


_DETAIL_KEYS = ("space", "Outside_Ceremony_Space", "Preferred_Vendors", "Guest_Min_Highest_Sat",
                "Guest_Min_Lowest_Sat", "Max_Capacity_Seated", "Venue_Offering")


def details_result(raw):
    """venue_details JSON -> spaces with capacity / guest minimums / ceremony space / preferred vendors only
    (no peak or cheapest months, no minimum-spend type, no fees); Google rating + reviews without price talk."""
    d = json.loads(raw)
    if d.get("error"):
        return raw
    out = {"vendor_id": d.get("vendor_id"),
           "spaces": [{k: s[k] for k in _DETAIL_KEYS if k in s} for s in d.get("spaces") or []]}
    g = d.get("google")
    if g:
        out["google"] = {"rating": g.get("rating"), "review_count": g.get("review_count"), "note": g.get("note"),
                         "reviews": [r for r in g.get("reviews") or [] if not has_price_talk(r.get("text"))]}
    return json.dumps(out)


RULES = """FREE MODE - this couple has no Tulle plan. These rules OVERRIDE every rule above about money: explaining costs, the peak-Saturday caveat, "concrete numbers beat adjectives", labelling figures, benchmarks, budget rules of thumb, COST BREAKDOWN and DETAIL LEVEL.
- You have no price data and must give none: no dollar amounts, ranges, per-guest, per-hour or package prices, percentages, fees, service charge, tax or gratuity, food and drink minimums, budget shares, industry-norm figures or estimates of any kind.
- No relative price claims either: never call a vendor affordable, cheap, cheaper, inexpensive, budget-friendly, good value, expensive, pricey or premium, never say it is below or above typical, never say it fits or is over their budget, and never order or rank picks by price.
- Recommend on fit only: capacity and guest count, whether the guest minimum works, venue type, style and setting, location, outdoor ceremony space, all-inclusive or bring-your-own-caterer, Google rating and review count, and for photographers and entertainers what the packages include (hours, team size, second shooter, photo booth, ceremony sound).
- Every reply sends the couple to the pricing PDF of a vendor you picked, e.g. "Open <name>'s pricing PDF to see exactly what they charge." The page shows the button.
- If they ask about cost, price, budget, cheaper options or whether something is affordable: answer warmly that Tulle's exact pricing lives in each vendor's PDF (and on Tulle plans), offer to shortlist vendors that fit so they can open those PDFs, and give no numbers or price comparisons. Never ask for their budget. Never repeat a figure the couple typed (their budget, a per-head price, a price cap such as "under $2,000") - acknowledge the question without the number.
- If the context says how many free PDF views they have left, mention it once ("you have 2 free PDF views - use one on <name>'s PDF").
- reasons: one short fit reason per vendor; the only numbers allowed anywhere are guest counts, capacity, hours of coverage, team size, ratings and review counts.
- Chips: never about price, cost, budget, cheaper options, fees, value or cheaper dates. Use style, setting, venue type, guest count, location, outdoor ceremony, all-inclusive, reviews, or another vendor type."""


# Appended to RULES only when config.COST_TOOLS_IN_CHAT is on (agent.run).
COST_TOOLS_RULE = """- Cost tools (estimate_wedding_cost, split_budget, budget_status, compare_venues): you may call them when the couple asks what a wedding costs, how to split their budget, where their budget stands or to compare their venues. Their numbers appear to the couple in a chart under your message - your own text stays figure-free ("Here's a typical-cost estimate for your state and guest count"). compare_venues shows only the couple's own quotes on this plan.
- Next-step chips (NEXT PLANNING STEP above) are allowed on this plan even though they mention cost or budget: "What will our wedding cost?", "Split our budget by category", "Compare my saved venues", "What's left for photography?" and the action chips. Not the price-filtered searches ("Find venues within my venue budget", "Find a photographer in my budget") - offer "Find a photographer" instead."""


def _tool(name):
    """beta_tool(name=...) - or, under the tests' SDK stub, a plain function renamed to the tool name."""
    try:
        return beta_tool(name=name)
    except TypeError:
        def deco(f):
            f.__name__ = name
            return f
        return deco


def _fn(tool):
    """The plain Python function behind a beta_tool (the tests' stub hands plain functions already)."""
    return getattr(tool, "func", tool)


def tools(search_venues, search_pe, saved_venues, venue_details, save_note, present_recommendations):
    """The tool set a free user's model gets: no venue_pricing / market_benchmarks / pe_market_rates, and
    search/saved/details wrappers with no price inputs and no price outputs. Same tool NAMES as the full set,
    so the shared SYSTEM prompt still reads naturally."""

    @_tool("search_venues")
    def free_search_venues(states: list[str], guests: int = 0, venue_types: list[str] = None,
                           vibes: list[str] = None, pricing_models: list[str] = None, keyword: str = "",
                           sort_by: str = "popular_desc", outdoor_ceremony: bool = False, city: str = "",
                           radius_miles: int = 0, relax: list[str] = None) -> str:
        """Search Tulle's venues (only ones with real pricing PDFs). Results carry capacity, venue type,
        description and whether the guest minimum fits - no prices (this couple has no plan). Venues already
        shown in this chat are left out automatically; the chat's ACTIVE FILTERS (guests, city) apply to any you
        leave empty.

        Args:
            states: US states or 'International', e.g. ["New York"]. Use the couple's location if unsure.
            guests: the party size. Venues must seat at least this many, and venues whose guest minimum is
                above it are left out (0 = any) - so for a small wedding or elopement pass the real count.
            venue_types: optional, EXACT values only (any of = OR): "Dedicated Event Venue", "Hotel / Resort",
                "Estate / Mansion", "Barn / Ranch", "Restaurant / Bar", "Country Club / Private Club",
                "Winery / Brewery / Distillery", "Museum / Gallery", "Civic / Public", "Garden / Botanical Garden",
                "Performing Arts Venue", "Religious", "University / College", "Zoo / Aquarium". Map the couple's words: rustic/barn/farm -> "Barn / Ranch";
                mansion/estate -> "Estate / Mansion"; vineyard -> "Winery / Brewery / Distillery".
            vibes: optional style attributes, EXACT values only (any of = OR): "Scenic / Nature Views",
                "Natural Light / Large Windows", "Historic Architecture", "Ballroom", "Tall / Vaulted Ceilings",
                "Waterfront", "Tented", "Rooftop / Skyline Views", "Industrial / Warehouse", "Greenhouse".
                beach/lake/river -> "Waterfront"; city views -> "Rooftop / Skyline Views"; loft -> "Industrial / Warehouse".
                glasshouse/conservatory/atrium -> "Greenhouse"; light-filled -> "Natural Light / Large Windows";
                historic/grand building -> "Historic Architecture"; elegant/black-tie -> "Ballroom" plus "Historic Architecture".
            pricing_models: optional: "All-Inclusive" (outside catering NOT allowed), "Semi-Inclusive" (some
                rentals included, outside catering allowed), "Raw Space" (empty space only, no tables or chairs; rare).
                Bring their own caterer / outside catering -> ["Raw Space", "Semi-Inclusive"], never "Raw Space" alone.
            keyword: optional free text - a city or a venue name. Prefer the tag filters above for styles.
            sort_by: popular_desc (default), recent_desc, capacity_asc or capacity_desc.
            outdoor_ceremony: true = only venues whose PDF lists an outdoor ceremony space.
            city: optional town or city the couple named or wants to stay near ("Flower Mound", "San Diego",
                "Manhattan"): venues within radius_miles of it, nearest first, each with distance_miles. For a
                region, pass its main town with a bigger radius_miles. keyword does not filter by location.
            radius_miles: how far from the city (default 25, max 100). "Within an hour" ~ 50.
            relax: active filters to drop because the couple changed them: "guests", "city" or "radius_miles".
        """
        return venue_result(_fn(search_venues)(states=states, guests=guests, venue_types=venue_types, vibes=vibes,
                                               pricing_models=pricing_models, keyword=keyword, sort_by=sort_by,
                                               outdoor_ceremony=outdoor_ceremony, city=city,
                                               radius_miles=radius_miles,
                                               relax=[r for r in (relax or [])
                                                      if r in ("guests", "city", "radius_miles")]))

    @_tool("search_photographers_entertainment")
    def free_search_pe(category: str, states: list[str], types: list[str] = None, offers: list[str] = None,
                       hour_bands: list[str] = None, keyword: str = "", sort_by: str = "popular_desc") -> str:
        """Search Tulle's photographers or entertainers (only ones with real pricing PDFs). Results carry what
        their packages include (hours, type, team, inclusions) - no prices (this couple has no plan).

        Args:
            category: "Photography" or "Entertainment".
            states: US states or 'International', e.g. ["New York"]. Use the couple's location if unsure.
            types: optional, EXACT values (any of = OR). Photography package types: "Wedding Day", "Engagement",
                "Elopement", "Other". Entertainment service types: "DJ", "Live Band", "Ensemble", "Solo Performer",
                "Other Entertainment". Pianist/harpist/violinist/singer -> "Solo Performer"; quartet/trio -> "Ensemble".
            offers: optional, EXACT values (any of = OR). Photography: "Second Shooter", "Engagement Session",
                "Album", "Prints", "Full Gallery Rights", "Videography", "Drone", "Rehearsal Dinner",
                "Content Creation". Entertainment: "Photo Booth", "Ceremony Sound", "Sound & Lighting Provided",
                "Monogram / Projection", "Special Effects".
            hour_bands: Photography only, coverage hours: "6 Hours or Less", "7-10 Hours", "11+ Hours".
            keyword: optional instrument or style, e.g. "piano", "harp", "string quartet", "jazz".
            sort_by: popular_desc (default) or recent_desc.
        """
        return pe_result(_fn(search_pe)(category=category, states=states, types=types, offers=offers,
                                        hour_bands=hour_bands, keyword=keyword, sort_by=sort_by))

    @_tool("saved_venues")
    def free_saved_venues() -> str:
        """The vendors this couple saved (their Saved list) with capacity, type and whether the guest minimum
        fits. Use it for "compare my saved venues" or to anchor recommendations to what they already like."""
        return venue_result(_fn(saved_venues)())

    @_tool("venue_details")
    def free_venue_details(vendor_id: str) -> str:
        """Extra facts about one venue: its spaces (capacity, guest minimums, outdoor ceremony space,
        preferred or required vendors) and its Google rating with up to 5 review texts. No prices.

        Args:
            vendor_id: a vendor_id returned by search_venues or saved_venues in this conversation.
        """
        return details_result(_fn(venue_details)(vendor_id=vendor_id))

    return [free_search_venues, free_search_pe, free_saved_venues, free_venue_details, save_note,
            present_recommendations]
