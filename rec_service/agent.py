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
import time
from concurrent.futures import ThreadPoolExecutor

import anthropic
from anthropic import beta_tool

import config
import xano

SYSTEM = """You are Tulle's wedding planning guide. Tulle holds REAL pricing PDFs that couples uploaded for thousands of wedding venues and vendors, plus state-by-state benchmarks built from them. You know this industry better than any generic AI tool, and you prove it with specifics.

YOUR ONE JOB IN THIS CONVERSATION: get this couple to open a venue's pricing PDF now, in this session. A reply that doesn't make opening a specific PDF the obvious next step has failed. Fewer, better-explained picks beat long lists - reduce analysis paralysis.

Show real expertise (this is what makes Tulle worth paying for):
- Explain the money the way a seasoned planner would: what is usually included vs. extra, how food and drink minimums work, service charge vs. gratuity vs. tax, ceremony fees, peak vs. off-peak and Friday/Sunday pricing, what drives the all-in cost per guest, which fees couples most often miss, and what to ask the venue.
- Use the numbers you have: this venue's own figures, the state benchmarks in context (quartiles and how many spaces they come from), and how this venue compares. Concrete numbers beat adjectives.
- Point at what's inside the PDF that answers their question ("the PDF lists the per-head menu tiers and the Saturday minimum") - that is the reason to open it.

Integrity - never break these:
- Label every figure's source: "this venue's PDF", "Tulle data across N [state] venues", or "industry norm". Never present an industry norm or estimate as this venue's number. A pricing line with source "market" is an estimate - say so.
- Never invent a venue, a price, a policy, an amenity or a review. If we don't have it, say "we don't have that for this venue" and give the best market figure we do have.
- Free-plan couples see price RANGES; the exact figures are in the PDFs behind the paywall. Say that plainly - it is the honest reason to open or unlock the PDF. Never guess an exact figure.
- If the honest answer is "none of our venues fit that", say so and offer the closest real options.

How to work:
- Use the couple's profile (location, guest count, budget, planning stage) and the notes from earlier conversations as the default. If something important is missing (budget, rough date, vibe), make good picks anyway and ask ONE short question at the end.
- Find venues with search_venues - results include each venue's pricing summary and guest minimum. One well-chosen search is usually enough; search again only if the results don't fit.
- Never name a venue you did not get from a tool in this conversation.
- State benchmarks for the couple's location are already in context. Use market_benchmarks only for a different state.
- Respect guest minimums: never recommend a venue whose minimum is above the couple's guest count without saying so.
- Budget rule of thumb: venue plus food and drink is usually 40-50% of the total wedding budget.
- Lead with why these picks fit (budget, guest count, style), then one or two expert insights, then caveats.
- If only one or two venues match a narrow request, add the closest alternatives and say why they are close.
- When the couple tells you something durable (style, must-have, dealbreaker, budget, date, a venue they loved or rejected), call save_note so future conversations remember it.
- Warm, specific, confident, brief (about 80-140 words). No filler, no exclamation marks, no generic advice a search engine would give.

Finish EVERY reply by calling present_recommendations exactly once: your message (ending with which venue's PDF to open first and what to look for in it), 2-4 vendor_ids in the order you recommend them, a one-line reason per vendor that includes a concrete number where we have one, and 2-4 short follow-up chips the couple might tap."""

PRICED_PER_SEARCH = 6   # pricing fetched for the top N results of each search, in parallel


def _compact_pricing(p):
    return {"guest_minimum": p.get("guest_minimum"), "verdict": p.get("verdict"),
            "lines": [{k: v for k, v in (("line", l["line"]), ("source", l["source"]),
                                          ("vs_market", l["vs_market"]), ("amount", l["amount"]),
                                          ("pct", l["pct"]), ("detail", (l["detail"] or "")[:160]))
                       if v not in (None, "")} for l in p.get("lines", [])]}


def _states_of(profile):
    loc = profile.get("Wedding_Location_Updated") or []
    loc = [loc] if isinstance(loc, str) else loc
    return [s for s in loc if s and s not in ("Not Sure",)][:2]


def run(token, user, paid, messages, profile_override=None, memory_notes=None, on_note=None):
    """messages: prior turns as [{"role": "user"|"assistant", "content": str}, ...], last one the user's.
    memory_notes: durable notes saved in earlier conversations (rec_memory).
    on_note(text): called when the model saves a new durable note.
    Returns (result_dict, usage_dict)."""
    seen = {}            # vendor_id -> card data, filled by search_venues in THIS request
    final = {}
    # profile_override: eval runs only - real requests always use the verified user row.
    profile = profile_override if profile_override is not None else xano.profile_of(user)
    pool = ThreadPoolExecutor(max_workers=8)

    def pricing_or_none(vid):
        try:
            return _compact_pricing(xano.venue_pricing(token, vid, paid))
        except Exception:
            return None

    @beta_tool
    def search_venues(states: list[str], guests: int = 0, max_venue_fee: int = 0,
                      max_food_per_person: int = 0, venue_types: list[str] = None,
                      vibes: list[str] = None, pricing_models: list[str] = None,
                      keyword: str = "", sort_by: str = "popular_desc") -> str:
        """Search Tulle's venues (only ones with real pricing PDFs). Each result includes its pricing
        summary (rental fee, food and drink, ceremony, service charge, guest minimum, state comparison).

        Args:
            states: US states or 'International', e.g. ["New York"]. Use the couple's location if unsure.
            guests: venue must seat at least this many (0 = any).
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
            keyword: optional free text - a city or a venue name. Prefer the tag filters above for styles.
            sort_by: popular_desc (default), recent_desc, capacity_asc or capacity_desc.
        """
        r = xano.search_venues(token, states=states, guests=guests, max_venue_fee=max_venue_fee,
                               max_food_per_person=max_food_per_person, venue_types=venue_types,
                               vibes=vibes, pricing_models=pricing_models, keyword=keyword, sort_by=sort_by)
        venues = r["venues"]
        prices = list(pool.map(pricing_or_none, [v["vendor_id"] for v in venues[:PRICED_PER_SEARCH]]))
        out = []
        for i, v in enumerate(venues):
            if v.get("vendor_id"):
                seen[v["vendor_id"]] = v
            row = {k: v.get(k) for k in ("vendor_id", "name", "state", "address", "venue_type",
                                         "max_capacity_seated", "venue_fee_range", "description")}
            if i < len(prices) and prices[i]:
                row["pricing"] = prices[i]
            out.append(row)
        return json.dumps({"total_matches": r["total_matches"], "venues": out})

    @beta_tool
    def venue_pricing(vendor_id: str) -> str:
        """Full pricing breakdown for one venue from its uploaded pricing PDF. Only needed for a venue
        whose pricing was not already included in a search result.

        Args:
            vendor_id: a vendor_id returned by search_venues.
        """
        return json.dumps(xano.venue_pricing(token, vendor_id, paid))

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
            message: 1-3 short sentences to the couple.
            vendor_ids: 2-4 vendor_ids from search_venues, best first.
            reasons: one short reason per vendor_id, same order.
            chips: 2-4 short follow-up suggestions, e.g. "Cheaper options", "More rustic".
        """
        final.update(message=message, vendor_ids=vendor_ids, reasons=reasons, chips=chips)
        return "shown"

    t0 = time.time()
    # Prefetch in parallel: the couple's state benchmarks + saved vendors.
    states = _states_of(profile)
    bench_f = [pool.submit(xano.market_benchmarks, s) for s in states]
    saved_f = pool.submit(lambda: [] if profile_override is not None else xano.saved_vendors(token))
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

    context = "Couple profile: " + json.dumps(profile)
    if paid:
        context += "\nThis couple has paid access: exact prices are available."
    else:
        context += "\nThis couple is on the free plan: pricing shows ranges; exact figures are in the PDFs behind the paywall."
    if benches:
        context += "\nState benchmarks (venues): " + json.dumps(benches)
    if saved:
        context += "\nVendors they saved: " + json.dumps(saved)
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
        tools=[search_venues, venue_pricing, market_benchmarks, save_note, present_recommendations],
        messages=msgs,
        output_config={"effort": config.EFFORT},
        # If Sonnet declines on a safety classifier, the API re-runs on a fallback model in the same
        # call (billed at that model's rates; usage["model"] records which one answered).
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_iterations=config.MAX_TOOL_ROUNDS,
    )
    model_used = config.MODEL
    try:
        for message in runner:
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
    usage["latency_ms"] = int((time.time() - t0) * 1000)
    usage["model"] = model_used

    # Rule 1: drop anything the model did not get from a search in this request.
    cards = []
    for i, vid in enumerate(final.get("vendor_ids", [])):
        if vid in seen:
            reasons = final.get("reasons") or []
            cards.append(dict(seen[vid], reason=reasons[i] if i < len(reasons) else ""))
    result = {"text": final.get("message") or "", "cards": cards, "chips": final.get("chips") or [],
              "notes_saved": saved_notes,
              "dropped_unverified_ids": [v for v in final.get("vendor_ids", []) if v not in seen]}
    return result, usage
