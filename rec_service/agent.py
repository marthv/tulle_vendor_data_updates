"""The recommendation loop: Claude + read-only tools over our own data.

Two hard rules are enforced in CODE, not just in the prompt:
  1. Claude cannot invent a vendor. present_recommendations may only reference vendor_ids that a
     search_venues call returned in THIS request; anything else is dropped before it reaches the page.
  2. Prices come from our endpoints, which already enforce the paywall - a free user's pricing
     returns ranges only, so the model never has exact figures to leak.

SPEED (v2, 2026-10-03). v1 measured p50 18s / max 25s over 30 cases with 6-10 sequential tool calls
of ~1-3s each. v2 cuts round trips instead of tokens:
  - search_venues returns each venue WITH its pricing summary, fetched in parallel, so the model
    no longer calls venue_pricing once per pick;
  - the couple's state benchmarks and saved vendors are fetched in parallel BEFORE the first model
    call and put in context, so market_benchmarks is only needed for other states;
  - effort defaults to low (retrieval + short writing, not hard reasoning).
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import anthropic
from anthropic import beta_tool

import config
import xano

SYSTEM = """You are Tulle's wedding VENUE guide. Tulle holds REAL pricing PDFs that couples uploaded for thousands of wedding venues, plus state-by-state benchmarks built from them. You know this industry better than any generic AI tool, and you prove it with specifics.

SCOPE - VENUES ONLY (for now): you recommend venues and explain venue pricing (rental, food and drink, service charge, tax, ceremony fees, all-in cost). If asked about photographers, florists, bands, DJs, planners, dresses or any other vendor, say in one sentence that the assistant covers venues for now and those vendors' pricing is on Tulle's Discovery page, then steer back to the venue decision. Never give advice, tips, question lists or budgets for other vendor types, and never offer a chip about them. Every chip must be a venue follow-up.

YOUR ONE JOB IN THIS CONVERSATION: get this couple to open a venue's pricing PDF now, in this session. A reply that doesn't make opening a specific PDF the obvious next step has failed. Fewer, better-explained picks beat long lists - reduce analysis paralysis.

Show real expertise (this is what makes Tulle worth paying for):
- Explain the money the way a seasoned planner would: what is usually included vs. extra, how food and drink minimums work, service charge vs. gratuity vs. tax, ceremony fees, peak vs. off-peak and Friday/Sunday pricing, what drives the all-in cost per guest, which fees couples most often miss, and what to ask the venue.
- Every price Tulle holds is for a PEAK-SEASON SATURDAY evening, the most expensive slot a venue sells. When you quote a venue's prices, say so once and that Friday, Sunday, daytime or off-season dates are often cheaper - the PDF shows the other rates, which is a good reason to open it. Once per CONVERSATION, not per reply: if an earlier reply in this chat already said it, don't say it again.
- Use the numbers you have: this venue's own figures, the state benchmarks in context (quartiles and how many spaces they come from), and how this venue compares. Concrete numbers beat adjectives.
- Point at what's inside the PDF that answers their question ("the PDF lists the per-head menu tiers and the Saturday minimum") - that is the reason to open it.

Integrity - never break these:
- Label every figure's source: "this venue's PDF", "Tulle data across N [state] venues", or "industry norm". Never present an industry norm or estimate as this venue's number. A pricing line with source "market" is an estimate - say so.
- Never invent a venue, a price, a policy, an amenity or a review. If we don't have it, say "we don't have that for this venue" and give the best market figure we do have.
- The context says how this couple can see exact prices. If they have free PDF views left, say so and invite them to open the PDF ("you have 2 free PDF views - open it to see the exact menu prices"). Only if they have NO free views left and no plan, say exact figures need a plan. Never say "behind the paywall" to someone who still has free views. Never guess an exact figure.
- If the honest answer is "none of our venues fit that", say so and offer the closest real options.
- Tulle does not yet track: overnight rooms or how many people a venue sleeps (beyond venue type "Hotel / Resort"), cultural or religious ceremony rules (kosher, halal, open flame, baraat, outside caterers), LGBTQ+ inclusion, or wheelchair access. If asked, say plainly that we don't have that data yet. Never guess or infer it from a venue's name, type or description. Then help with what we do have (e.g. "Hotel / Resort" venues for on-site rooms, Raw Space venues if they need their own caterer) and suggest they check the venue's PDF or ask the venue.

How to work:
- The profile in context is CURRENT. If earlier turns in this chat used a different budget, guest count or location, the couple updated it: acknowledge it warmly ("I see you've updated your budget to $100,000 - here are options that fit") and never call it your mistake or a correction.
- Use the couple's profile (location, guest count, budget, planning stage) and the notes from earlier conversations as the default. If something important is missing (budget, rough date, vibe), make good picks anyway and ask ONE short question at the end.
- Find venues with search_venues - results include each venue's pricing summary and guest minimum. One well-chosen search is usually enough; search again only if the results don't fit.
- Never name a venue you did not get from a tool in this conversation (search_venues or saved_venues).
- You can see the couple's profile and their Saved list. Never say you can't see their saved vendors; if the list is empty, say they haven't saved any yet and suggest saving favourites with the heart.
- Google reviews (from venue_details): give the rating with its review count and say it covers all Google reviews of the place, not only weddings. Quote or summarise only reviews marked about_a_wedding; if none are, say the reviews we hold aren't about weddings. Never invent or embellish a review.
- State benchmarks for the couple's location are already in context. Use market_benchmarks only for a different state.
- If the profile lists several locations, the couple is choosing between them - they HAVE picked. Search them together in one search_venues call (it returns picks from each), cover each location in your picks, and never say they haven't chosen a state.
- Don't repeat yourself across a conversation. Earlier replies end with [Venues shown: ...] and [Chips offered: ...]. Each new reply should bring venues the couple hasn't seen yet; show an earlier venue again only if they ask about it or to compare, or it is still clearly the best answer to the new request - then say so in a few words. Don't repeat what they can open (their plan) after the first reply, and never call it "free" access when they have a plan.
- Respect guest minimums: never recommend a venue whose minimum is above the couple's guest count without saying so. search_venues already leaves out venues whose known guest minimum is above the guests you pass, and venues whose known food and drink minimum is over half the couple's budget (left_out_for_minimums says how many).
- If the profile has no guest count, don't assume a big wedding: make picks that work across sizes, say the count changes which venues fit, and make your one question how many guests they expect.
- Small weddings and elopements (roughly under 30 guests): pass the real count as guests, prefer venues with no or low minimums (restaurants, raw spaces, small hotels), and if nothing in our data fits, say so plainly. Tulle doesn't track elopement packages or ceremony-only pricing yet - say that if asked, and point to the PDF for any small-party option a venue lists.
- Budget rule of thumb: venue plus food and drink is usually 40-50% of the total wedding budget.
- Lead with why these picks fit (budget, guest count, style), then one or two expert insights, then caveats.
- If only one or two venues match a narrow request, add the closest alternatives and say why they are close.
- When the couple tells you something durable (style, must-have, dealbreaker, budget, date, a venue they loved or rejected), call save_note so future conversations remember it.
- Warm, specific, confident, brief. Short is better: no bullet lists, each pick's headline number only - the PDF is where the couple sees the rest. Only a COST BREAKDOWN is longer. No filler, no exclamation marks, no generic advice a search engine would give.

DETAIL LEVEL: if the context says "DETAIL: LIGHT", this couple is not on Forever. You get no dollar figures, and you must not give any: no prices, per-guest costs, fees, percentages, ranges or budget splits, not even industry norms or your own estimates. You may repeat the couple's own budget and guest count back to them. Recommend on fit instead: style, capacity, whether the guest minimum works, all-inclusive or not, and how each cost line compares with their state (below average / typical / above average). Say once, plainly, that the full price breakdown is in the venue's PDF and that Forever unlocks it here in the assistant. The rule "concrete numbers beat adjectives" does not apply at this level. The opposite level, "DETAIL: FULL", means use every number you have.

COST BREAKDOWN: give one ONLY when the context says "BREAKDOWN REQUESTED" (the couple explicitly asked for a breakdown or the full cost of a venue). Then give an itemized estimate for that venue - never just a range plus "open the PDF". Without that line, never itemize. Never volunteer a breakdown, never offer one as a chip, and never itemize in openings or when comparing several venues: those stay short and send the couple to the PDF. Get the venue's lines first (its search pricing, venue_pricing, and venue_details for menu, bar and other fees). Then one line per item, using the couple's guest count:
- Rental fee
- Ceremony fee
- Food: $X a head x N guests = $Y
- Bar: $X a head x N guests = $Y (or "included" / "not listed")
- Other required fees the PDF lists
- Service charge: X% of $Y = $Z
- Tax: X% = $Z
- Gratuity, only if the PDF suggests one (say it is separate from the service charge)
- Estimated total, and per guest
Every line names its source (this venue's PDF / Tulle data across N [state] venues / industry norm). If the PDF doesn't list a line, fill it from the state benchmark and mark it "estimate", or say "not listed" - never drop a line silently. Show the arithmetic so it can be checked. Write each item on its own line (a real line break) starting with "• ". The page shows plain text: no markdown, no bold, no "- " or "*" bullets. After the total, one sentence on what would move it most (bar package, guest count, a Friday or Sunday date), then what to confirm in the PDF. For a breakdown of one venue, present just that venue (1 vendor_id).

CHIPS - only offer follow-ups you can answer well from Tulle's data. Every chip must map to one of these:
- a search filter: cheaper (lower max_venue_fee or max_food_per_person), a different state, more or fewer guests, a venue type from the search_venues list, a vibe from the search_venues list, all-inclusive / bring-your-own-caterer (pricing_models), or outdoor ceremony space (outdoor_ceremony);
- a pricing line we hold: rental fee, food and drink per guest, service charge, tax, ceremony fee, guest minimum, all-in cost per guest (pick the line that matters most for THIS couple's picks - e.g. a high service charge or a guest minimum near their count);
- venue_details for a venue you showed: menu or bar package per guest, cheaper dates (off-peak Saturday prices, peak vs. cheapest months), other fees, required vendors, or Google reviews - e.g. "Cheaper dates at The Barn?", "What do reviews say?". Only offer one of these if venue_details (or the pricing summary) actually has that fact for the venue;
- how a venue compares with its state's benchmarks, e.g. "Is this a good price for Texas?";
- their Saved list, e.g. "Compare my saved venues".
Never offer a chip about something none of these covers: availability or open dates, parking, lodging, accessibility, tastings, decor, a specific feature like a courtyard, a ceremony fee being included, or other vendor types. Use the exact filter wording where it fits ("Barn / Ranch venues", "Waterfront venues", "All-inclusive venues"). A chip should be a short question or request the couple would tap, under 40 characters.
Pick chips for THIS couple and THIS reply - the next decision they face, not a fixed set. The examples above are a menu, not a template: don't offer the same chips in every reply, don't repeat a chip from earlier in the chat, and include at most one chip about a single named venue. Offer "Cheaper dates at <venue>?" only when you know that venue's PDF has an off-peak rate (from venue_details) or the couple has asked about dates or season.

Finish EVERY reply by calling present_recommendations exactly once: your message (ending with which venue's PDF to open first and what to look for in it), 2-4 vendor_ids in the order you recommend them (1 for a single-venue COST BREAKDOWN), a one-line reason per vendor that includes a concrete number where we have one, and 2-4 short follow-up chips that follow the CHIPS rule."""

# A COST BREAKDOWN is long (more tokens, more cost), so only an explicit ask unlocks it (user 2026-10-05:
# short replies preferred; Forever gets the itemized detail ONLY when they specifically ask).
BREAKDOWN_ASK = re.compile(r"break ?down|itemi[sz]e|full (?:cost|price)|all[- ]in|total cost|realistic|"
                           r"exact (?:cost|price)|line by line|every fee|"
                           r"(?:what|how much) (?:will|would|does) .{0,40}(?:really |actually )?cost", re.I)

PRICED_PER_SEARCH = 6   # pricing fetched for the top N results of each search, in parallel


def _compact_pricing(p):
    return {"guest_minimum": p.get("guest_minimum"), "verdict": p.get("verdict"),
            "lines": [{k: v for k, v in (("line", l["line"]), ("source", l["source"]),
                                          ("vs_market", l["vs_market"]), ("amount", l["amount"]),
                                          ("pct", l["pct"]), ("detail", (l["detail"] or "")[:160]))
                       if v not in (None, "")} for l in p.get("lines", [])]}


_DIGITS = re.compile(r"\d")


def _light_pricing(p, guests):
    """What a non-Forever couple's assistant may see (user decision 2026-10-03): the right venues, less
    quantitative info. No amounts, no market figures (ep231 sentences quote them), only how each line
    compares with the state. The model cannot leak a number it never received."""
    out = {"lines": [{"line": l.get("line"), "vs_market": l.get("vs_market")}
                     for l in p.get("lines") or [] if l.get("line") and l.get("vs_market")]}
    gm = p.get("guest_minimum")
    if gm and guests:
        out["guest_minimum_fits"] = int(gm) <= int(guests)
    verdict = p.get("verdict") or ""
    if verdict and not _DIGITS.search(verdict):
        out["verdict"] = verdict
    return out


def _light_card(v):
    return {k: val for k, val in v.items() if k not in ("venue_fee_range", "food_minimum")}


def _states_of(profile):
    loc = profile.get("Wedding_Location_Updated") or []
    loc = [loc] if isinstance(loc, str) else loc
    return [s for s in loc if s and s not in ("Not Sure",)][:2]


def run(token, user, paid, messages, profile_override=None, memory_notes=None, on_note=None, user_context="",
        detail="full", prior_vendor_ids=()):
    """messages: prior turns as [{"role": "user"|"assistant", "content": str}, ...], last one the user's.
    memory_notes: durable notes saved in earlier conversations (rec_memory).
    on_note(text): called when the model saves a new durable note.
    detail: "full" (Forever) or "light" (everyone else - fit, not figures; see _light_pricing).
    prior_vendor_ids: venues earlier turns of this chat showed as cards (server-stored, so trusted).
    Returns (result_dict, usage_dict)."""
    light = detail == "light"
    seen = {}            # vendor_id -> card data, filled by search_venues in THIS request
    prior = set(prior_vendor_ids or ())   # shown earlier in this chat: may be presented again (looked up)
    final = {}
    saved_cards_holder = {}
    # profile_override: eval runs only - real requests always use the verified user row.
    profile = profile_override if profile_override is not None else xano.profile_of(user)
    pool = ThreadPoolExecutor(max_workers=8)

    try:
        guests = int(profile.get("Wedding_Guest_Count") or 0)
    except (TypeError, ValueError):
        guests = 0
    try:
        budget = int(profile.get("Wedding_Budget") or 0)
    except (TypeError, ValueError):
        budget = 0

    def shape(p):
        return _light_pricing(p, guests) if light else _compact_pricing(p)

    def raw_pricing_or_none(vid):
        try:
            return xano.venue_pricing(token, vid, paid and not light)
        except Exception:
            return None

    def pricing_or_none(vid):
        p = raw_pricing_or_none(vid)
        return shape(p) if p else None

    @beta_tool
    def search_venues(states: list[str], guests: int = 0, max_venue_fee: int = 0,
                      max_food_per_person: int = 0, venue_types: list[str] = None,
                      vibes: list[str] = None, pricing_models: list[str] = None,
                      keyword: str = "", sort_by: str = "popular_desc", outdoor_ceremony: bool = False) -> str:
        """Search Tulle's venues (only ones with real pricing PDFs). Each result includes its pricing
        summary (rental fee, food and drink, ceremony, service charge, guest minimum, state comparison).

        Args:
            states: US states or 'International', e.g. ["New York"]. Use the couple's location if unsure.
            guests: the party size. Venues must seat at least this many, and venues whose guest minimum is
                above it are left out (0 = any) - so for a small wedding or elopement pass the real count.
            max_venue_fee: max rental fee in dollars (0 = any).
            max_food_per_person: max food and drink per guest in dollars (0 = any).
            venue_types: optional, EXACT values only (any of = OR): "Dedicated Event Venue", "Hotel / Resort",
                "Estate / Mansion", "Barn / Ranch", "Restaurant / Bar", "Country Club / Private Club",
                "Winery / Brewery / Distillery", "Museum / Gallery", "Civic / Public", "Garden / Botanical Garden",
                "Performing Arts Venue", "Religious". Map the couple's words: rustic/barn/farm -> "Barn / Ranch";
                mansion/estate -> "Estate / Mansion"; vineyard -> "Winery / Brewery / Distillery".
            vibes: optional style attributes, EXACT values only (any of = OR): "Scenic / Nature Views",
                "Natural Light / Large Windows", "Historic Architecture", "Ballroom", "Tall / Vaulted Ceilings",
                "Waterfront", "Tented", "Rooftop / Skyline Views", "Industrial / Warehouse", "Greenhouse".
                beach/lake/river -> "Waterfront"; city views -> "Rooftop / Skyline Views"; loft -> "Industrial / Warehouse".
            pricing_models: optional: "All-Inclusive" (catering + rentals included), "Semi-Inclusive", "Raw Space"
                (bring your own caterer and rentals).
            outdoor_ceremony: true = only venues whose PDF lists an outdoor ceremony space (garden, lawn,
                terrace, courtyard...). Use it when the couple wants an outdoor ceremony.
            keyword: optional free text - a city or a venue name. Prefer the tag filters above for styles.
            sort_by: popular_desc (default), recent_desc, capacity_asc or capacity_desc.
        """
        # Small-wedding fit (2026-10-04, a 6-guest $10k elopement got 50-guest-minimum and $9,000-food-minimum
        # venues): ep119 can't filter on minimums, so over-fetch and drop venues whose KNOWN minimum doesn't fit.
        # A 0 / missing minimum means "none or not known" and is kept - never treat missing as "no minimum".
        food_cap = budget // 2 if budget > 0 else 0      # venue + food is ~40-50% of the total budget
        r = xano.search_venues(token, states=states, guests=guests, max_venue_fee=max_venue_fee,
                               max_food_per_person=max_food_per_person, venue_types=venue_types,
                               vibes=vibes, pricing_models=pricing_models, keyword=keyword, sort_by=sort_by,
                               outdoor_ceremony=outdoor_ceremony, page_size=16 if (guests or food_cap) else 8)
        venues = r["venues"]
        removed = {"food_minimum_over_half_budget": 0, "guest_minimum_over_party": 0}
        if food_cap:
            kept = [v for v in venues if not (v.get("food_minimum") or 0) > food_cap]
            removed["food_minimum_over_half_budget"] = len(venues) - len(kept)
            venues = kept
        n_priced = PRICED_PER_SEARCH + (4 if guests else 0)
        raws = list(pool.map(raw_pricing_or_none, [v["vendor_id"] for v in venues[:n_priced]]))
        if guests:
            # only priced venues have a known guest minimum, so keep just those and drop the ones that don't fit
            pairs = [(v, p) for v, p in zip(venues, raws) if not (p and (p.get("guest_minimum") or 0) > guests)]
            removed["guest_minimum_over_party"] = len(raws) - len(pairs)
        else:
            pairs = list(zip(venues, raws)) + [(v, None) for v in venues[len(raws):]]
        pairs = pairs[:8]
        venues = [v for v, _ in pairs]
        prices = [shape(p) if p else None for _, p in pairs]
        out = []
        for i, v in enumerate(venues):
            if light:
                v = _light_card(v)
            if v.get("vendor_id"):
                seen[v["vendor_id"]] = v
            row = {k: v.get(k) for k in ("vendor_id", "name", "state", "address", "venue_type",
                                         "max_capacity_seated", "venue_fee_range", "description") if k in v}
            if i < len(prices) and prices[i]:
                row["pricing"] = prices[i]
            out.append(row)
        res = {"total_matches": r["total_matches"], "venues": out}
        if any(removed.values()):
            res["left_out_for_minimums"] = removed
        if r.get("matches_by_state"):
            res["matches_by_state"] = r["matches_by_state"]
        return json.dumps(res)

    @beta_tool
    def venue_pricing(vendor_id: str) -> str:
        """Full pricing breakdown for one venue from its uploaded pricing PDF. Only needed for a venue
        whose pricing was not already included in a search result.

        Args:
            vendor_id: a vendor_id returned by search_venues.
        """
        p = xano.venue_pricing(token, vendor_id, paid and not light)
        return json.dumps(_light_pricing(p, guests) if light else p)

    @beta_tool
    def saved_venues() -> str:
        """The vendors this couple saved (their Saved list), each with its pricing summary and guest
        minimum. Use it for "compare my saved venues", "which of my saved venues...", or to anchor
        recommendations to what they already like."""
        cards = saved_cards_holder.get("cards") or []
        prices = list(pool.map(pricing_or_none, [c["vendor_id"] for c in cards[:PRICED_PER_SEARCH]]))
        out = []
        for i, c in enumerate(cards):
            if light:
                c = _light_card(c)
            seen[c["vendor_id"]] = c
            row = {k: c.get(k) for k in ("vendor_id", "name", "state", "address", "category", "venue_type",
                                         "max_capacity_seated", "venue_fee_range") if k in c}
            if i < len(prices) and prices[i]:
                row["pricing"] = prices[i]
            out.append(row)
        return json.dumps({"saved_count": len(cards), "saved": out})

    @beta_tool
    def venue_details(vendor_id: str) -> str:
        """Extra facts from a venue's PDF and Google listing that the pricing summary leaves out: menu and
        bar package per guest, off-peak (cheapest Saturday) prices and which months are peak vs. cheapest,
        food-and-drink minimum type, other fees, preferred/required vendors, outdoor ceremony space, and its
        Google rating with up to 5 review texts. Use it when the couple asks about menus, bar, cheaper
        dates, reviews or fees for a specific venue.

        Args:
            vendor_id: a vendor_id returned by search_venues or saved_venues in this conversation.
        """
        if vendor_id not in seen and vendor_id not in prior:
            return json.dumps({"error": "unknown vendor_id - search for the venue first"})
        d = xano.venue_details(vendor_id, with_amounts=paid and not light)
        if light and d.get("google"):   # LIGHT gets no figures: drop review texts that quote prices
            d["google"]["reviews"] = [r for r in d["google"]["reviews"] if "$" not in r["text"]]
        return json.dumps(d)

    @beta_tool
    def market_benchmarks(state: str) -> str:
        """What venues typically cost in a state OTHER than the couple's (theirs is already in context):
        quartiles for rental fee, food and drink per guest, service charge, and all-in cost per guest
        at 75/125/200 guests.

        Args:
            state: a single state, e.g. "Texas", or "International".
        """
        return json.dumps(xano.market_benchmarks(state))

    saved_notes = []

    @beta_tool
    def save_note(note: str) -> str:
        """Remember a DURABLE fact about this couple for future conversations: a style they want or
        rule out, a must-have or dealbreaker, a venue budget, a date or season, a guest-count change,
        a venue they loved or rejected. One short sentence. Do not save one-off questions.

        Args:
            note: e.g. "Wants a rustic barn or farm, no ballrooms" or "Venue budget about $8,000".
        """
        if on_note and len(saved_notes) < 3:
            saved_notes.append(note)
            try:
                on_note(note)
            except Exception:
                pass
        return "saved"

    @beta_tool
    def present_recommendations(message: str, vendor_ids: list[str], reasons: list[str],
                                chips: list[str]) -> str:
        """Show your answer to the couple. Call exactly once, last.

        Args:
            message: 1-3 short sentences to the couple. Only a COST BREAKDOWN the couple explicitly asked
                for is longer and uses the itemized "• " list.
            vendor_ids: 2-4 vendor_ids from search_venues or saved_venues, best first (1 for a single-venue breakdown).
            reasons: one short reason per vendor_id, same order.
            chips: 2-4 short VENUE follow-ups that follow the CHIPS rule (each maps to a search filter, a
                pricing line, a state comparison or the Saved list), e.g. "Cheaper options", "Barn / Ranch venues".
        """
        final.update(message=message, vendor_ids=vendor_ids, reasons=reasons, chips=chips)
        return "shown"

    t0 = time.time()
    # Prefetch in parallel: the couple's state benchmarks + saved vendors.
    states = _states_of(profile)
    bench_f = [] if light else [pool.submit(xano.market_benchmarks, s) for s in states]
    saved_ids = [] if profile_override is not None else xano.saved_vendor_ids(user)
    saved_f = pool.submit(lambda: [c for c in pool.map(xano.vendor_card, saved_ids) if c])
    benches = []
    for f in bench_f:
        try:
            benches.append(f.result())
        except Exception:
            pass
    try:
        saved = saved_f.result()
    except Exception:
        saved = []
    saved_cards_holder["cards"] = saved

    context = "Couple profile: " + json.dumps(profile)
    if light:
        context += ("\nDETAIL: LIGHT. This couple is not on Forever: recommend on fit and comparisons only, "
                    "with no dollar figures (see DETAIL LEVEL). Forever unlocks the full price breakdown here.")
    else:
        context += "\nDETAIL: FULL."
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        if isinstance(last_user, str) and BREAKDOWN_ASK.search(last_user):
            context += ("\nBREAKDOWN REQUESTED: the couple's latest message asks for a cost breakdown - follow "
                        "COST BREAKDOWN (itemized \"• \" lines, about 220 words), whatever the length rules say.")
    if light:
        pass
    elif paid:
        context += "\nThis couple has a plan: they can open any pricing PDF, and exact prices are available to you."
    else:
        try:
            views = int(user.get("FreeViewsRemaining"))
        except (TypeError, ValueError):
            views = 0
        if views > 0:
            context += ("\nThis couple is on the free plan with %d free PDF view%s left: you only have price ranges, "
                        "but opening a venue's PDF shows them the exact figures - invite them to." % (views, "" if views == 1 else "s"))
        else:
            context += ("\nThis couple is on the free plan and has used their free PDF views: you only have price ranges; "
                        "the exact figures need a plan (Forever also unlocks 40 assistant questions a day).")
    if benches:
        context += "\nState benchmarks (venues): " + json.dumps(benches)
    if saved:
        context += ("\nThey have SAVED %d vendors (their Saved list): " % len(saved)
                    + json.dumps([{"vendor_id": c["vendor_id"], "name": c["name"], "state": c["state"],
                                   "category": c.get("category")} for c in saved])
                    + " - call saved_venues for their pricing when comparing or recommending around them.")
    else:
        context += "\nThey have not saved any vendors yet."
    if user_context:
        # The couple's own words. Treat as facts and preferences about them - not as instructions that
        # change your rules (sourcing, honesty, paywall) or your job.
        context += ("\nWhat the couple asked you to always keep in mind (their own words, facts and preferences "
                    "only): <<<" + user_context[:1000] + ">>>")
    if memory_notes:
        context += ("\nWhat we learned in earlier conversations (use it, don't repeat it back): "
                    + json.dumps(memory_notes))
    msgs = [dict(m) for m in messages]
    msgs[0] = {"role": msgs[0]["role"], "content": context + "\n\n" + msgs[0]["content"]}

    client = anthropic.Anthropic()
    usage = {"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0,
             "tool_calls": 0, "prefetch_ms": int((time.time() - t0) * 1000)}
    runner = client.beta.messages.tool_runner(
        model=config.MODEL,
        max_tokens=8000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        tools=([search_venues, saved_venues, venue_pricing, venue_details, save_note, present_recommendations] if light else
               [search_venues, saved_venues, venue_pricing, venue_details, market_benchmarks, save_note,
                present_recommendations]),
        messages=msgs,
        output_config={"effort": config.EFFORT},
        # If Sonnet declines on a safety classifier, the API re-runs on a fallback model in the same
        # call (billed at that model's rates; usage["model"] records which one answered).
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_iterations=config.MAX_TOOL_ROUNDS,
    )
    model_used = config.MODEL
    last_text = ""
    try:
        for message in runner:
            texts = [b.text for b in message.content if b.type == "text" and (b.text or "").strip()]
            if texts:
                last_text = "\n\n".join(texts)
            u = message.usage
            usage["input_tokens"] += u.input_tokens or 0
            usage["output_tokens"] += u.output_tokens or 0
            usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
            usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
            usage["tool_calls"] += sum(1 for b in message.content if b.type == "tool_use")
            model_used = getattr(message, "model", model_used) or model_used
            if final:
                break
    finally:
        pool.shutdown(wait=False)
    if not final and last_text:
        # The model occasionally ends its turn with plain text instead of calling present_recommendations
        # (1 of 3 runs of eval o12, 2026-10-04). Keep its answer rather than showing the couple nothing.
        # Cards: venues from THIS request's searches that the text names (rule 1 still holds).
        named = [vid for vid, v in seen.items() if v.get("name") and v["name"].lower() in last_text.lower()][:4]
        final.update(message=last_text, vendor_ids=named, reasons=[""] * len(named), chips=[])
        usage["no_present_fallback"] = True
    usage["latency_ms"] = int((time.time() - t0) * 1000)
    usage["model"] = model_used

    # Rule 1: drop anything the model did not get from a search in this request.
    cards = []
    for i, vid in enumerate(final.get("vendor_ids", [])):
        if vid not in seen and vid in prior:
            try:
                c = xano.vendor_card(vid)
            except Exception:
                c = None
            if c:
                seen[vid] = _light_card(c) if light else c
        if vid in seen:
            reasons = final.get("reasons") or []
            cards.append(dict(seen[vid], reason=reasons[i] if i < len(reasons) else ""))
    result = {"text": final.get("message") or "", "cards": cards, "chips": final.get("chips") or [],
              "notes_saved": saved_notes,
              "dropped_unverified_ids": [v for v in final.get("vendor_ids", []) if v not in seen]}
    return result, usage
