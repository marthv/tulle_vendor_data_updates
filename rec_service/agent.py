"""The recommendation loop: Claude + read-only tools over our own data.

Two hard rules are enforced in CODE, not just in the prompt:
  1. Claude cannot invent a vendor. present_recommendations may only reference vendor_ids that a
     search_venues call returned in THIS request; anything else is dropped before it reaches the page.
  2. Prices come from our endpoints, which already enforce the paywall - a free user's pricing
     returns ranges only, so the model never has exact figures to leak.
     EXCEPTION (2026-10-11): ep119's photographer/entertainment path returns EXACT package prices to any
     signed-in user, so for DETAIL: LIGHT this module strips them itself (_pe_row / _pe_card) before the
     model or the card sees them.

SPEED (v2, 2026-10-03). v1 measured p50 18s / max 25s over 30 cases with 6-10 sequential tool calls
of ~1-3s each. v2 cuts round trips instead of tokens:
  - search_venues returns each venue WITH its pricing summary, fetched in parallel, so the model
    no longer calls venue_pricing once per pick;
  - the couple's state benchmarks and saved vendors are fetched in parallel BEFORE the first model
    call and put in context, so market_benchmarks is only needed for other states;
  - effort defaults to low (retrieval + short writing, not hard reasoning).
"""
import functools
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import anthropic
from anthropic import beta_tool

import config
import cost_tools
import estimate
import free_mode
import progress as progress_mod
import railway_search
import xano

SYSTEM = """You are Tulle's wedding vendor guide for VENUES, PHOTOGRAPHERS and ENTERTAINMENT. Tulle holds REAL pricing PDFs that couples uploaded for thousands of wedding venues and hundreds of photographers, DJs, bands and musicians, plus state-by-state benchmarks built from them. You know this industry better than any generic AI tool, and you prove it with specifics.

SCOPE - three vendor types:
- Venues (search_venues): rental, food and drink, service charge, tax, ceremony fees, all-in cost.
- Photographers (search_photographers_entertainment, category "Photography"). Videographers: Tulle holds them as photographers whose packages include "Videography" - search Photography with offers ["Videography"] and say so.
- Entertainment (category "Entertainment"): DJs, live bands, ensembles and solo performers. There is no instrument field: a pianist, harpist, violinist, cellist, guitarist or singer -> types ["Solo Performer"] + keyword (e.g. "piano"); a string quartet or trio -> types ["Ensemble"] + keyword "string quartet". Photo booths come with DJs: offers ["Photo Booth"].
- Anything else (florists and flowers, catering on its own, hair and makeup, planners, dresses and attire, cakes, rentals, stationery): say in one sentence that the assistant doesn't have solid pricing for those yet and Tulle's Discovery page shows what we do have, then offer help with venues, photographers or entertainment. Officiants: Tulle has no officiant data at all - say so. Never give advice, tips, question lists or budgets for these, and never offer a chip about them.

YOUR ONE JOB IN THIS CONVERSATION: get this couple to open the pricing PDF of the vendor that answers their question, now, in this session - a venue's PDF for a venue question, the photographer's or entertainer's own pricing PDF for theirs (never send a photographer question to a venue PDF). A reply that doesn't make opening a specific PDF the obvious next step has failed. Couples asked for MORE options and for help telling them apart (2026-10-14: a third of all messages were "more"): for a search, show 4-5 picks when the results allow, and make each card's reason say how it DIFFERS from the other picks (price position, size, style, fee structure, location), never the same sentence twice. The message itself is 2-3 sentences (about 60 words; only a COST BREAKDOWN or a budget question may run longer): lead with the top pick and why.

Show real expertise (this is what makes Tulle worth paying for):
- Explain the money the way a seasoned planner would: what is usually included vs. extra, how food and drink minimums work, service charge vs. gratuity vs. tax, ceremony fees, peak vs. off-peak and Friday/Sunday pricing, what drives the all-in cost per guest, which fees couples most often miss, and what to ask the venue.
- Every price Tulle holds is for a PEAK-SEASON SATURDAY evening, the most expensive slot a venue sells. When you quote a venue's prices, say so once and that Friday, Sunday, daytime or off-season dates are often cheaper - the PDF shows the other rates, which is a good reason to open it. Once per CONVERSATION, not per reply: if an earlier reply in this chat already said it, don't say it again.
- Use the numbers you have: this venue's own figures, its computed estimate, the state benchmarks in context (quartiles and how many spaces they come from), and how this venue compares. Concrete numbers beat adjectives.
- Point at what's inside the PDF that answers their question ("the PDF lists the per-head menu tiers and the Saturday minimum") - that is the reason to open it.

Integrity - never break these:
- Each venue's card shows ONE rental price: the "Rental fee" line of its pricing summary (also in venue_fee_range). When you quote a venue's rental, quote that figure. If you mention another space's rental from venue_details, name the space and say it differs from the card.
- ONE SET OF NUMBERS (couples check us against the venue page and against our own earlier replies; contradicting either loses their trust):
  - Totals and per-guest costs: quote ONLY a venue's "estimate" (on its search or saved row) or an estimate_cost result. Never add, multiply or apply percentages yourself. For a what-if (a different guest count, the cheapest Saturday) call estimate_cost. Its total is "before tax and gratuity", exactly like the venue's page - say so.
  - "Typical" or "average" for a state: quote ONLY the State benchmarks in context or market_benchmarks, and say the scope each metric gives (e.g. "Connecticut venues that seat 151-250, 25 spaces"). A venue's own pricing line tells you only how it compares (vs_market) - never attach a dollar figure to that comparison.
  - A line marked "estimate" or "Not in this venue's PDF" is not this venue's number: say it is an estimate. Never call a missing rental fee "$0" or "no rental fee" - say the PDF doesn't list one.
  - Once you have quoted a figure in this chat, keep using it. If the couple changes the guest count or date, say what changed and give the new estimate.
- Label every figure's source: "this venue's PDF" / "this photographer's PDF", "Tulle data across N [state] venues" / "Tulle data across N packages from M [state] DJs", or "industry norm". Never present an industry norm or estimate as this venue's number. A pricing line with source "market" is an estimate - say so.
- Never invent a vendor, a price, a package, a policy, an amenity or a review. If we don't have it, say "we don't have that for this venue" and give the best market figure we do have.
- The context says how this couple can see exact prices. If they have free PDF views left, say so and invite them to open the PDF ("you have 2 free PDF views - open it to see the exact menu prices"). Only if they have NO free views left and no plan, say exact figures need a plan. Never say "behind the paywall" to someone who still has free views. Never guess an exact figure.
- If the honest answer is "none of our venues fit that", say so and offer the closest real options.
- Tulle does not yet track: overnight rooms or how many people a venue sleeps (beyond venue type "Hotel / Resort"), cultural or religious ceremony rules (kosher, halal, open flame, baraat, outside caterers), LGBTQ+ inclusion, or wheelchair access. If asked, say plainly that we don't have that data yet. Never guess or infer it from a venue's name, type or description. Then help with what we do have (e.g. "Hotel / Resort" venues for on-site rooms, Raw Space venues if they need their own caterer) and suggest they check the venue's PDF or ask the venue.

How to work:
- The profile in context is CURRENT. If earlier turns in this chat used a different budget, guest count or location, the couple updated it: acknowledge it warmly ("I see you've updated your budget to $100,000 - here are options that fit") and never call it your mistake or a correction.
- Use the couple's profile (location, guest count, budget, planning stage) and the notes from earlier conversations as the default. If something important is missing (budget, rough date, vibe), make good picks anyway and ask ONE short question at the end.
- Find venues with search_venues - results include each venue's pricing summary and guest minimum. One well-chosen search is usually enough; search again only if the results don't fit.
- Never name a vendor you did not get from a tool in this conversation (search_venues, search_photographers_entertainment or saved_venues).
- You can see the couple's profile and their Saved list. Never say you can't see their saved vendors; if the list is empty, say they haven't saved any yet and suggest saving favourites with the heart.
- Google reviews (from venue_details): give the rating with its review count and say it covers all Google reviews of the place, not only weddings. Quote or summarise only reviews marked about_a_wedding; if none are, say the reviews we hold aren't about weddings. Never invent or embellish a review.
- State benchmarks for the couple's location are already in context. Use market_benchmarks only for a different state.
- If the profile lists several locations, the couple is choosing between them - they HAVE picked. Search them together in one search_venues call (it returns picks from each), cover each location in your picks, and never say they haven't chosen a state.
- If the profile has NO location (the context says "LOCATION: NONE IN PROFILE"): use any state the couple named in this chat or in the notes from earlier conversations, search it, and save it with save_note (e.g. "Wedding location: New York") the first time they name it. Only if no state is known anywhere, search without a state (states []) and still show 2-3 strong picks that fit their guest count and budget, then make your ONE closing question which state they are marrying in. Never answer with zero vendors just to ask for the location, and never ask for a state the couple already gave.
- If the context says "LOCATION: STATE ONLY", you know their state but not where in it. Search the state anyway, then make your ONE closing question which city or area they want to be close to (e.g. "Which part of Texas - near Dallas, Austin, Houston?"), unless they already said it in this chat, in the notes from earlier conversations or in what they asked you to keep in mind - then pass that place as city. Ask it only once per chat. It comes before a style or preference question; a missing guest count still comes first. When they answer, save it with save_note (e.g. "Wants to stay near: Flower Mound, Texas") and search with it as city.
- Don't repeat yourself across a conversation. Earlier replies end with [Vendors shown: ...] and [Chips offered: ...]. search_venues already leaves out venues shown in this chat and their Saved venues (already_shown_left_out says how many), so each new reply brings venues the couple hasn't seen. Show an earlier venue again only if they ask about it by name, ask to compare, or point back at it - then say it's one they've seen. Never call a venue "new" or say they "haven't seen" it unless it is not in any [Vendors shown] list.
- The couple's stated criteria hold until they change them: a location or city, a guest count, a rental or food cap. search_venues keeps them for you (ACTIVE FILTERS); "more", "other options" or "cheaper" never widen them. When they name a city, town or area, or say where they want to stay close to (NYC, Manhattan, San Diego, Flower Mound), pass it as city - keyword does not filter by location. A town is searched by distance: results come nearest first with distance_miles, so say roughly how far each pick is ("about 9 miles from Flower Mound"). If nothing fits within the radius, search again with a bigger radius_miles before you ever say we have nothing nearby, and then say how far the closest options are. Never say a town has no venues just because none have that town in their address. If too few venues fit the couple's other filters, say so and ask before widening those. Don't repeat what they can open (their plan) after the first reply, and never call it "free" access when they have a plan.
- Respect guest minimums: never recommend a venue whose minimum is above the couple's guest count without saying so. search_venues already leaves out venues whose known guest minimum is above the guests you pass, and venues whose known food and drink minimum is over half the couple's budget (left_out_for_minimums says how many).
- If the profile has no guest count, don't assume a big wedding: make picks that work across sizes, say the count changes which venues fit, and make your one question how many guests they expect.
- Small weddings and elopements (roughly under 30 guests): pass the real count as guests, prefer venues with no or low minimums (restaurants, raw spaces, small hotels), and if nothing in our data fits, say so plainly. Tulle doesn't track elopement packages or ceremony-only pricing yet - say that if asked, and point to the PDF for any small-party option a venue lists.
- Budget rule of thumb: venue plus food and drink is usually 40-50% of the total wedding budget.
- "How much should we budget?", "Is our budget realistic?", "What would be a more reasonable budget?": this is the question couples care most about - answer it with numbers, never with one sentence or a question back. Use each state's budget_check in the State benchmarks: the typical venue plus food and drink for their guest count (with its scope and number of spaces), the share of their budget and its verdict word for word, and the whole-wedding budget a typical venue implies. Then name the cheapest real pick shown so far as the way to stay inside it. Never multiply a per-guest figure by guests or call a share "above" or "within" yourself - budget_check has done it. Without a guest count, ask for it after giving the per-guest figures.
- Lead with why these picks fit (budget, guest count, style), then one or two expert insights, then caveats.
- If only one or two venues match a narrow request, add the closest alternatives and say why they are close.
- When the couple tells you something durable (style, must-have, dealbreaker, budget, date, a venue they loved or rejected), call save_note so future conversations remember it. The page tells them it was added to what you know about them, so don't announce it yourself.
- If the context has no preference answers and you don't know their style or setting, make your ONE closing question a preference question (what kind of place, a must-have setting such as waterfront, rooftop or high ceilings, or how they want catering handled), and save the answer with save_note when they reply.
- Warm, specific, confident, brief. Short is better: no bullet lists, each pick's headline number only - its all-in estimate for the couple's guest count when it has one ("about $37,300 for 160 guests before tax and gratuity"), else its rental fee. The PDF is where the couple sees the rest. Only a COST BREAKDOWN is longer. No filler, no exclamation marks, no generic advice a search engine would give.

PHOTOGRAPHERS AND ENTERTAINMENT:
- Their prices are PACKAGE prices from the vendor's own PDF (search_photographers_entertainment returns the packages that match the filters: name, price, hours, type, team size, what's included, add-ons). The peak-Saturday caveat is for venues only - don't say it here. Package prices usually exclude tax, travel and overtime unless the PDF says otherwise.
- Each result's price_range covers its matching packages. The card shows card_price: when you give one starting price, quote that one and name its package. Missing hours = not listed in the PDF.
- The result's "market" is Tulle data for that state (median and middle half, n packages from m vendors); scope "US-wide" means the state had too few packages and the national figure is used - say so. starting_price_vs_state compares each vendor's cheapest package with its own package or service type in that state. pe_market_rates gives the full table for any state (by package type, coverage hours, or service type).
- A price target ("photographers that are $5,000") -> max_price a little above it (about 10%) and say the picks are at or under that. Guest count does not drive these prices: never filter them by guests.
- If keyword_matches is 0, say none of the vendors we hold mention it and show the closest real options as alternatives.
- Expertise to show: photographers - hours of coverage, second shooter, engagement session, album, gallery rights, travel; DJs - ceremony sound, lighting, MC, photo booth; bands - number of musicians, sets and breaks, overtime; ceremony musicians - ceremony only vs. ceremony plus cocktail hour. Industry norm: photography is often around 10-12% of a total wedding budget - label it "industry norm". That is the ONLY budget share you may quote for these vendors: never state one for videography, DJs, bands or musicians.

DETAIL LEVEL: if the context says "DETAIL: LIGHT", this couple is not on Forever. You get no dollar figures, and you must not give any: no prices, per-guest costs, fees, percentages, ranges or budget splits, not even industry norms or your own estimates. You may repeat the couple's own budget and guest count back to them. Recommend on fit instead: style, capacity, whether the guest minimum works, all-inclusive or not, and how each cost line compares with their state (below average / typical / above average). For photographers and entertainers you get no package prices either: recommend on what's included, hours, team size, package type and starting_price_vs_state (below typical / typical / above typical). Say once, plainly, that the full prices are in the vendor's PDF and that Forever unlocks them here in the assistant. The rule "concrete numbers beat adjectives" does not apply at this level. The opposite level, "DETAIL: FULL", means use every number you have.

COST BREAKDOWN: give one ONLY when the context says "BREAKDOWN REQUESTED" (the couple explicitly asked for a breakdown or the full cost of a venue). Then give an itemized estimate for that venue - never just a range plus "open the PDF". Without that line, never itemize. Never volunteer a breakdown, never offer one as a chip, and never itemize in openings or when comparing several venues: those stay short and send the couple to the PDF. Call estimate_cost for the venue first and copy its lines and arithmetic exactly (venue_details adds menu, bar and other fees as notes - never fold them into the total yourself). One line per item, using the couple's guest count:
- Rental fee
- Ceremony fee
- Food: $X a head x N guests = $Y
- Bar: $X a head x N guests = $Y (or "included" / "not listed")
- Other required fees the PDF lists
- Service charge: X% of $Y = $Z
- Estimated total before tax and gratuity, and per guest (estimate_cost's figures)
- Tax: X% = $Z (only if estimate_cost has the venue's rate; otherwise "added on top, rate not stated")
- Gratuity, only if the PDF suggests one (say it is separate from the service charge and not in the total)
Every line names its source as estimate_cost gives it (this venue's PDF / estimate). Never drop a line silently. Show the arithmetic estimate_cost gives so it can be checked. Write each item on its own line (a real line break) starting with "• ". The page shows plain text: no markdown, no bold, no "- " or "*" bullets. After the total, one sentence on what would move it most (bar package, guest count, a Friday or Sunday date), then what to confirm in the PDF. For a breakdown of one venue, present just that venue (1 vendor_id).

CHIPS - only offer follow-ups you can answer well from Tulle's data. Every chip must map to one of these:
- a search filter: cheaper (lower max_venue_fee or max_food_per_person), a different state, more or fewer guests, a venue type from the search_venues list, a vibe from the search_venues list, all-inclusive / bring-your-own-caterer (pricing_models), or outdoor ceremony space (outdoor_ceremony);
- a pricing line we hold: rental fee, food and drink per guest, service charge, tax, ceremony fee, guest minimum, all-in cost per guest (pick the line that matters most for THIS couple's picks - e.g. a high service charge or a guest minimum near their count);
- venue_details for a venue you showed: menu or bar package per guest, cheaper dates (off-peak Saturday prices, peak vs. cheapest months), other fees, required vendors, or Google reviews - e.g. "Cheaper dates at The Barn?", "What do reviews say?". Only offer one of these if venue_details (or the pricing summary) actually has that fact for the venue;
- how a venue compares with its state's benchmarks, e.g. "Is this a good price for Texas?";
- their Saved list, e.g. "Compare my saved venues";
- for photographers and entertainers: a filter of search_photographers_entertainment (cheaper / max_price, a package type such as "Elopement photographers", coverage hours such as "7-10 hour coverage", an offer such as "With a second shooter" or "DJs with a photo booth", a service type such as "Live bands instead"), or the state's market rates ("What do DJs cost in Texas?");
- another supported vendor type, when it is the couple's natural next decision (e.g. "Find a photographer" after venues).
Never offer a chip about something none of these covers: availability or open dates, parking, lodging, accessibility, tastings, decor, a specific feature like a courtyard, a ceremony fee being included, or unsupported vendor types (florists, catering, beauty, planners, attire, officiants). Use the exact filter wording where it fits ("Barn / Ranch venues", "Waterfront venues", "All-inclusive venues"). A chip should be a short question or request the couple would tap, under 40 characters.
Pick chips for THIS couple and THIS reply - the next decision they face, not a fixed set. The examples above are a menu, not a template: don't offer the same chips in every reply, don't repeat a chip from earlier in the chat, and include at most one chip about a single named venue. Offer "Cheaper dates at <venue>?" only when you know that venue's PDF has an off-peak rate (from venue_details) or the couple has asked about dates or season.

Finish EVERY reply by calling present_recommendations exactly once: your message (the first reply in a chat ends with which vendor's PDF to open first and what to look for in it), 3-5 vendor_ids in the order you recommend them (fewer only if fewer fit; 1 for a single-venue COST BREAKDOWN), a short reason per vendor (at most about 15 words) that says what sets it apart from the other picks with a concrete number where we have one (its estimate for venues, when it has one), and 2-4 short follow-up chips that follow the CHIPS rule. A question only about an unsupported vendor type gets 0 vendor_ids."""

# Appended to SYSTEM only when config.COST_TOOLS_IN_CHAT is on (off = the prompt is exactly origin/main's).
COST_TOOLS_RULES = """COST TOOLS (2026-10-25: the Cost tools tab moved into this chat). For ANY question about what a whole wedding costs, how to split their budget, where their budget stands, or comparing the venues they saved or quoted, call the matching tool - estimate_wedding_cost, split_budget, budget_status, compare_venues - even if you also search. Each one draws a chart or table for the couple under your message, so let the widget carry the numbers: quote at most the one or two figures that answer the question (from the tool's facts, exactly), never re-list the table. For a what-if ("what if 120 guests?", "without a planner?") call the tool again with the new argument. A per-venue all-in is still estimate_cost; compare_venues is for the couple's saved venues and their own quotes together.

NEXT PLANNING STEP (owner 2026-10-25) - this extends the CHIPS rule's list. When there's a natural next planning step for this couple, make one chip that step (pick from the list below); otherwise keep to the usual follow-ups. Don't force it.
- after venue picks: "Compare my saved venues", "What will our wedding cost overall?", or "Save one to compare" (only when saved_venues shows fewer than 2 saved venues; if you can't tell, skip it);
- after estimate_wedding_cost: "Split our budget by category", "Compare my saved venues", or "Open Cost tools";
- after split_budget: "Find venues within my venue budget", "Find a photographer in my budget";
- after compare_venues: "Add a quote from a PDF", "Open my budget", or "Find a photographer";
- after budget_status: "What's left for <the biggest category they haven't added yet>?" (e.g. "What's left for photography?"), or "Open my budget".
"Open Cost tools", "Open my budget" and "Add a quote from a PDF" open a tab or the quote upload on the page: write them exactly as shown, and use at most one of the three per reply. The other planning chips are questions you answer next with the cost tools, saved_venues or a search."""

# A COST BREAKDOWN is long (more tokens, more cost), so only an explicit ask unlocks it (user 2026-10-05:
# short replies preferred; Forever gets the itemized detail ONLY when they specifically ask).
BREAKDOWN_ASK = re.compile(r"break ?down|itemi[sz]e|full (?:cost|price)|all[- ]in|total cost|realistic|"
                           r"exact (?:cost|price)|line by line|every fee|"
                           r"(?:what|how much) (?:will|would|does) .{0,40}(?:really |actually )?cost", re.I)

PRICED_PER_SEARCH = 6   # pricing fetched for the top N results of each search, in parallel
PRICED_MAX = 24         # ...or up to this many when a guest count or price cap has to be checked per venue
SHOWN_MAX = 8           # search results the model sees; the rest only feed "Show 3 more"
PRICE_WAVE = 8          # pricing calls per parallel wave (the model's 8 first, see search_venues)
SEARCH_POOL = 60        # one ep119 page of this many when a city or hidden venues need a deeper pool
DEFERRED_WAIT_S = 6.0   # max wait at the end of a request for background "Show 3 more" pricing


def _misfit(p, guests, max_venue_fee, max_food_per_person):
    """Why a priced venue can't be shown for these limits (the left_out_for_minimums key), or None. A venue whose
    pricing failed (p None) has no known minimum or rental and is kept, as before."""
    if guests and p and (p.get("guest_minimum") or 0) > guests:
        return "guest_minimum_over_party"
    if max_venue_fee and (_rental_amount(p) or 0) > max_venue_fee:
        # ep119 caps the CHEAPEST space's rental (flt_min_venue_fee); the card quotes the pricing panel's
        # space, which can be dearer. A card must never show a rental over the couple's cap.
        return "rental_over_cap"
    if max_food_per_person and (((p or {}).get("cost_inputs") or {}).get("pp_fb") or 0) > max_food_per_person:
        return "food_per_guest_over_cap"
    return None


def _line_detail(l, guests):
    """A pricing line's sentence, safe to hand the model. A line the venue's PDF doesn't give ("market") quotes
    a market figure for the venue's OWN capacity band - the source of 1437's "$205 vs $146 a head" (two venues,
    two bands, both called "Connecticut"). Those sentences are dropped: the figure the model may use for a missing
    line is the computed estimate, and typical figures come only from the State benchmarks in context.
    ep230 also leaves a literal "{guests}" in some sentences (the page fills it in) - fill it here."""
    if l.get("source") != "venue":
        return "Not in this venue's PDF."
    d = (l.get("detail") or "")
    return d.replace("{guests}", str(guests) if guests else "100")[:200]


def _compact_pricing(p, guests=0, budget=0):
    out = {"guest_minimum": p.get("guest_minimum"), "verdict": p.get("verdict"),
           "lines": [{k: v for k, v in (("line", l["line"]), ("source", l["source"]),
                                         ("vs_market", l["vs_market"]), ("amount", l["amount"]),
                                         ("pct", l["pct"]), ("detail", _line_detail(l, guests)))
                      if v not in (None, "")} for l in p.get("lines", [])]}
    e = estimate.summary(estimate.compute(p.get("cost_inputs"), guests), budget)
    if e:
        out["estimate"] = e
    return out


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


def _rental_amount(p):
    """The rental fee the pricing summary (PI panel, one space) quotes, or None. The card's price comes from
    here, NOT from table 11's flt_min/max_venue_fee (primary PDF, all spaces): 2026-10-04 The Village, Big Sur
    showed $30,000 on its card while the reply quoted the PDF's $3,600 Elopement rental, because the model was
    handed both numbers. One number per venue now - the one the reply quotes."""
    for l in (p or {}).get("lines") or []:
        if l.get("line") == "Rental fee" and isinstance(l.get("amount"), (int, float)) and l["amount"] > 0:
            return int(l["amount"])
    return None


def _rental_not_disclosed(p):
    return any(l.get("line") == "Rental fee" and l.get("source") != "venue" for l in (p or {}).get("lines") or [])


def _with_rental(v, p, guests=0, budget=0):
    """Card = what the reply quotes: the panel's rental fee, and (FULL) the all-in estimate line.
    A rental the PDF doesn't give is not "$0" (2026-10-13: Farmington Gardens and Saint Clements were
    described as "$0 rental" / "no rental fee" from table 11's 0 placeholders)."""
    amt = _rental_amount(p)
    if amt:
        v = dict(v, venue_fee_range=[amt, amt])
    elif _rental_not_disclosed(p) and not any(v.get("venue_fee_range") or []):
        v = dict(v, venue_fee_range=[None, None])
    e = estimate.compute((p or {}).get("cost_inputs"), guests)
    if e:
        v = dict(v, estimate_label=estimate.label(e, budget), estimate_total=e["total_before_tax_and_gratuity"],
                 estimate_guests=e["guests"])
    return v


def _light_card(v):
    return {k: val for k, val in v.items()
            if k not in ("venue_fee_range", "food_minimum", "estimate_label", "estimate_total", "estimate_guests")}


# ---------------------------------------------------------------- photographers + entertainment

PE_TOP_PACKAGES = 3
_MONEY = re.compile(r"\$\s?\d|\d\s?%|\b\d[\d,.]*\s?(?:k|K|dollars?|USD|usd)\b|\bper (?:hour|hr)\b.{0,6}\d")


def _money(n):
    return "$" + format(int(n), ",") if isinstance(n, (int, float)) and n > 0 else None


def _no_figures(text):
    """LIGHT: drop every sentence of a blurb that quotes money ('Pricing starts at $15,500', '10% discount').
    Sentences without figures (style, coverage, what's included) stay - that is what LIGHT recommends on."""
    parts = re.split(r"(?<=[.!?])\s+", text or "")
    return " ".join(p for p in parts if p and not _MONEY.search(p)).strip()


def _vs_label(price, bucket):
    if not bucket or not isinstance(price, (int, float)) or price <= 0:
        return None
    lo, hi = bucket.get("p25"), bucket.get("p75")
    if not lo or not hi:
        return None
    where = "below typical" if price < lo else "above typical" if price > hi else "typical"
    scope = "US-wide" if bucket.get("scope") == "US-wide" else "state"
    return "%s for %s%s" % (where, bucket.get("bucket") or "packages", " (US-wide figure)" if scope == "US-wide" else "")


def _cheapest(v):
    priced = [p for p in v.get("packages") or [] if isinstance(p.get("price"), (int, float)) and p["price"] > 0]
    return min(priced, key=lambda p: p["price"]) if priced else None


def _pe_type_label(v):
    tags = v.get("vendor_tags") or ""
    if v.get("category") == "Entertainment":
        return ", ".join((v.get("package_types") or [])[:2]) or tags or "Entertainment"
    types = [t for t in (v.get("package_types") or []) if t != "Other"][:2]
    return "Photography" + (" - " + ", ".join(types) if types else "")


def _hours_label(v):
    h = v.get("hours_range")
    if not h:
        return None
    return "%d hours" % h[0] if h[0] == h[1] else "%d-%d hours" % (h[0], h[1])


def _pe_card(v, light):
    """Card for a photographer / entertainer. Same keys a venue card uses where they mean the same thing
    (vendor_id, vendor_idx, name, state, venue_type = the line under the name, image, description), plus
    P/E-only keys the page can show: category, package_count, packages_label, hours_label, price_from /
    price_to / price_label (FULL only). Never venue_fee_range - the venue card labels that a rental fee."""
    lo, hi = (v.get("price_range") or [None, None])[:2]
    lo = v.get("from_price") or lo
    n = int(v.get("package_count") or 0)
    blurb = v.get("blurb") or v.get("description") or ""
    card = {"vendor_id": v.get("vendor_id"), "vendor_idx": v.get("vendor_idx"), "name": v.get("name"),
            "state": v.get("state"), "category": v.get("category"), "venue_type": v.get("venue_type") or _pe_type_label(v),
            "image": v.get("image"), "description": (_no_figures(blurb) if light else blurb)[:300],
            "package_count": n, "packages_label": ("%d package%s" % (n, "" if n == 1 else "s")) if n else None,
            "hours_label": _hours_label(v)}
    if not light and _money(lo):
        card.update(price_from=int(lo), price_to=int(hi) if _money(hi) else int(lo),
                    price_label=("From " + _money(lo)) if hi and hi != lo else _money(lo))
    return {k: val for k, val in card.items() if val not in (None, "")}


def _pe_row(v, bench, light):
    """What the MODEL sees for one photographer / entertainer. LIGHT: no prices, no benchmark figures, no
    blurb sentence with money, no package name with a $ - only fit and how the starting price compares."""
    cheapest = _cheapest(v)
    vs = _vs_label(cheapest["price"], xano.pe_bucket_for(bench, cheapest.get("type"))) if cheapest else None
    pk = []
    for p in (v.get("packages") or [])[:PE_TOP_PACKAGES]:
        row = {"name": p.get("name"), "price": p.get("price"), "hours": p.get("hours"), "type": p.get("type"),
               "team": p.get("team"), "included": (p.get("included") or [])[:4], "addons": (p.get("addons") or [])[:3]}
        if light:
            row.pop("price")
            row["name"] = re.sub(r"\s*\$\s?\d[\d,.]*k?\s*", " ", row["name"] or "").strip() or "Package"
            row["vs_state"] = _vs_label(p.get("price"), xano.pe_bucket_for(bench, p.get("type")))
        pk.append({k: val for k, val in row.items() if val not in (None, "", [])})
    out = {"vendor_id": v.get("vendor_id"), "name": v.get("name"), "state": v.get("state"),
           "category": v.get("category"), "tags": v.get("vendor_tags"), "package_types": v.get("package_types"),
           "package_count": v.get("package_count"), "hours_range": v.get("hours_range"),
           "offers": v.get("offers"), "blurb": (_no_figures(v.get("blurb")) if light else (v.get("blurb") or ""))[:300],
           "top_packages": pk, "starting_price_vs_state": vs, "keyword_match": v.get("keyword_match")}
    if not light:
        out["price_range"] = v.get("price_range")
        out["card_price"] = _pe_card(v, light=False).get("price_label")   # what the card shows - quote this one
    return {k: val for k, val in out.items() if val not in (None, "", [])}


def _market_row(bench, types):
    """The one benchmark line a P/E search result carries (FULL only): the requested type's row, else the
    headline (Wedding Day for photography)."""
    if not bench or not bench.get("available"):
        return None
    b = None
    for t in types or []:
        b = xano.pe_bucket_for(bench, t)
        if b and b.get("bucket") == t:
            break
    b = b or bench.get("headline")
    return dict(b, state=bench.get("state")) if b else None


def _states_of(profile):
    loc = profile.get("Wedding_Location_Updated") or []
    loc = [loc] if isinstance(loc, str) else loc
    return [s for s in loc if s and s not in ("Not Sure",)][:2]


# ---------------------------------------------------------------- figure check (2026-10-13)
# Every dollar figure a reply shows must be one the model was GIVEN this request (tool results, the context,
# the conversation so far) or a simple derivation of the couple's own budget - not one it computed or blended.
# 1437's chat quoted "$205" and "$146" as the Connecticut food average 5 minutes apart. A reply with an
# unsupported figure is bounced back to the model once with the list; a second miss is shown but logged.

_DOLLARS = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?([kK]\b)?")
_NUMBERS = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")
MIN_CHECKED = 20       # figures under $20 (a $5 fee, a $10 add-on) aren't worth a bounce


def dollar_figures(text):
    out = []
    for m in _DOLLARS.finditer(text or ""):
        v = float(m.group(1).replace(",", "") + (m.group(2) or ""))
        out.append((m.group(0).strip(), v * 1000 if m.group(3) else v))
    return out


def numbers_in(text):
    return {float(a.replace(",", "") + (b or "")) for a, b in _NUMBERS.findall(text or "")}


def _close(v, allowed):
    rounded = v >= 100 and v % 100 == 0           # "about $39,900", "$38K": rounded mentions get more room
    tol = 0.04 if rounded else 0.02
    return any(abs(v - a) <= tol * a for a in allowed if a > 0)


def unsupported_figures(text, allowed):
    return [s for s, v in dollar_figures(text) if v >= MIN_CHECKED and not _close(v, allowed)]


GUIDE_LOW, GUIDE_HIGH = 0.40, 0.50     # venue + food and drink as a share of the whole wedding budget
_PG_POINTS = (75, 125, 200)             # table 63 all_in_pg_N guest counts


def _pg_at(metrics, guests, q):
    """All-in cost per guest at the couple's count: linear between table 63's 75 / 125 / 200-guest points,
    flat outside them."""
    pts = [(n, (metrics.get("all_in_pg_%d" % n) or {}).get(q)) for n in _PG_POINTS]
    pts = [(n, v) for n, v in pts if isinstance(v, (int, float))]
    if not pts:
        return None
    if guests <= pts[0][0]:
        return pts[0][1]
    for (n0, v0), (n1, v1) in zip(pts, pts[1:]):
        if guests <= n1:
            return v0 + (v1 - v0) * (guests - n0) / float(n1 - n0)
    return pts[-1][1]


def _r100(n):
    return int(round(n / 100.0)) * 100


def budget_check(bench, guests, budget):
    """"How much should we budget?" answered in CODE (2026-10-13 replay of user 1437: one run gave no number,
    the other multiplied $180-210 x 160 itself and called 48-56% of the budget "well above" a 40-50% guide).
    Typical venue + food and drink for THIS guest count in this state, its share of the couple's budget, a
    verdict, and the whole-wedding budget that typical venue implies. None without a guest count."""
    m = (bench or {}).get("metrics") or {}
    if not guests or not m.get("all_in_pg_200"):
        return None
    q = {k: _pg_at(m, guests, k) for k in ("p25", "median", "p75")}
    if q["median"] is None:
        return None
    tot = {k: _r100(v * guests) for k, v in q.items() if v is not None}
    out = {"guests": guests, "scope": m["all_in_pg_200"].get("scope"), "n_spaces": m["all_in_pg_200"].get("n_spaces"),
           "typical_venue_and_food_total": tot["median"], "per_guest": int(round(q["median"])),
           "middle_half_range": [tot.get("p25"), tot.get("p75")],
           "whole_wedding_budget_for_a_typical_venue": [_r100(tot["median"] / GUIDE_HIGH),
                                                        _r100(tot["median"] / GUIDE_LOW)],
           "note": "Before tax and gratuity. The whole-wedding range assumes venue plus food and drink is 40-50% of it."}
    if budget:
        share = 100.0 * tot["median"] / budget
        out["share_of_their_budget_pct"] = int(round(share))
        out["guideline_for_their_budget"] = [_r100(budget * GUIDE_LOW), _r100(budget * GUIDE_HIGH)]
        out["verdict"] = ("below the 40-50% guideline, so their budget has room" if share < 40 else
                          "within the 40-50% guideline" if share <= 50 else
                          "a little above the 40-50% guideline, so it's a stretch" if share <= 60 else
                          "well above the 40-50% guideline")
    return out


def derived_numbers(guests, budget, totals=()):
    """Figures a reply may state without a tool: shares of the couple's own budget, per guest, and the
    difference between two computed totals (e.g. what the cheapest Saturday saves)."""
    out = set()
    if budget:
        for share in (0.4, 0.45, 0.5):
            out.add(budget * share)
            if guests:
                out.add(budget * share / guests)
        if guests:
            out.add(budget / guests)
    t = sorted(set(totals))
    out |= {b - a for i, a in enumerate(t) for b in t[i + 1:]}
    return out


MORE_STEP = 3      # "Show 3 more" (beta feedback 2026-10-06): the button reveals this many per press
# 2026-10-14: 6 -> 9. 37 of 114 real messages (11 of 21 users, Oct 3-14) asked for more; chats 42 and 64 pressed
# the button twice and then typed "more" because the pool was empty.
MORE_MAX = 9


def more_cards(searched, seen, cards, prior):
    """Next-best venues for the "Show 3 more" button: the rest of THIS request's search results, in search
    order, minus everything presented now or earlier in the chat (no repeats). Same searches as the picks,
    so they keep the couple's criteria - asking the model for "more" drifted (a 30-40 guest romantic
    search ended at a zoo and a fairground). No model call, no question spent. Only whole groups of 3:
    fewer than 3 new matches -> no button. Rule 1 holds: every id came from a search in this request.
    `searched` is the PRIMARY search's results only (see run): 2026-10-09 chat 42 asked for New York City, the
    model also ran a state-wide search, and "Show 3 more" served Arrow Park and Sleepy Hollow from it."""
    skip = {c.get("vendor_id") for c in cards} | set(prior or ())
    out = [dict(seen[v], reason="") for v in searched if v not in skip and v in seen][:MORE_MAX]
    return out[:len(out) // MORE_STEP * MORE_STEP]


# ---------------------------------------------------------------- sticky search filters (2026-10-14)
# Real chats Oct 3-14: 10 replies / 4 users broke a constraint the couple had stated. Chat 33 said "venue fee
# less than $11,000"; three turns later "more options" was searched without the cap and showed the Ritz-Carlton
# Lake Tahoe at $12,000. The prompt already said "keep their criteria" - so the filters are now carried in CODE:
# each answer stores the filters of its main search (xano.parse_message_row "filters"), and the next search_venues
# call in the chat inherits any hard filter it leaves empty, unless the model names it in `relax` because the
# couple changed or dropped it.

STICKY = ("guests", "max_venue_fee", "max_food_per_person", "city", "radius_miles")
NEAR_MILES = 25         # default radius around a town the couple named


def merge_filters(args, active, relax=()):
    """search_venues arguments + the chat's active filters -> the filters to search with. A value the model
    passes always wins (the couple changed it); an empty one inherits the active value unless relaxed. New
    states the model passes drop the inherited city (a city belongs to its state)."""
    active = active or {}
    relax = {str(r).strip().lower() for r in (relax or ())}
    out = dict(args)
    if not out.get("states") and active.get("states"):
        out["states"] = list(active["states"])
    same_states = sorted(out.get("states") or []) == sorted(active.get("states") or [])
    for k in STICKY:
        if out.get(k) or k in relax or not active.get(k):
            continue
        if k in ("city", "radius_miles") and not same_states:
            continue
        if k == "radius_miles" and (out.get("city") or "") != (active.get("city") or ""):
            continue    # a new town starts at the default radius
        out[k] = active[k]
    return out


# NYC boroughs by ZIP (addresses say "New York, NY 10019" or "Brooklyn, NY 11225", never "Manhattan"; a keyword
# search for "Manhattan" only found streets named Manhattan Ave in Brooklyn - chat 42).
_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_CITY_ZIPS = {
    "manhattan": ("100", "101", "102"), "bronx": ("104",), "the bronx": ("104",), "staten island": ("103",),
    "brooklyn": ("112",), "queens": ("110", "111", "113", "114", "116"),
}
for _k in ("new york city", "nyc", "new york, ny", "the city"):
    _CITY_ZIPS[_k] = ("100", "101", "102", "103", "104", "110", "111", "112", "113", "114", "116")


def _norm_city(city):
    return re.sub(r"\s+", " ", (city or "").strip().lower())


def in_city(address, city):
    """True if a venue address is in the couple's city / area. NYC and its boroughs go by ZIP; anything else by
    the city name appearing in the address ("San Diego", "Napa", "Austin")."""
    c = _norm_city(city)
    if not c:
        return True
    a = (address or "").lower()
    if c in _CITY_ZIPS:
        z = _ZIP.findall(address or "")
        return bool(z) and z[-1][:3] in _CITY_ZIPS[c]
    return c in a


def _k(n):
    n = int(n)
    return "$%dK" % round(n / 1000.0) if n >= 10000 else "$" + format(n, ",")


def why_lines(cards, priced=True):
    """One code-written line per card saying how it differs from the others in the same batch (2026-10-14:
    "not great at differentiating"; "Show 3 more" cards had no reason at all). Rental position, capacity,
    venue type / pricing model. priced=False (no plan): no price words - capacity, type and location only."""
    rents = {i: (c.get("venue_fee_range") or [None])[0] for i, c in enumerate(cards)}
    rents = {i: r for i, r in rents.items() if isinstance(r, (int, float)) and r > 0}
    caps = {i: c.get("max_capacity_seated") for i, c in enumerate(cards)
            if isinstance(c.get("max_capacity_seated"), (int, float)) and c["max_capacity_seated"] > 0}
    lo = min(rents, key=rents.get) if priced and len(rents) > 1 else None
    hi_cap = max(caps, key=caps.get) if len(caps) > 1 else None
    lo_cap = min(caps, key=caps.get) if len(caps) > 1 else None
    out = []
    for i, c in enumerate(cards):
        bits = []
        if priced and i in rents:
            bits.append(("lowest rental here, " if i == lo else "") + _k(rents[i]) + " rental")
        elif priced and c.get("estimate_label"):
            bits.append(str(c["estimate_label"]))
        if i == hi_cap:
            bits.append("the largest here, seats %d" % caps[i])
        elif i == lo_cap:
            bits.append("the most intimate here, seats %d" % caps[i])
        elif i in caps:
            bits.append("seats %d" % caps[i])
        vt = c.get("venue_type")
        vt = ", ".join(vt[:1]) if isinstance(vt, list) else vt
        if vt and str(vt).lower() not in ("not sure", "none", ""):
            bits.append(str(vt))
        place = ((c.get("address") or "").split(",")[-2:-1] or [""])[0].strip()
        if place and not any(ch.isdigit() for ch in place):
            bits.append(place)
        out.append("; ".join(bits[:3]))
    return out


def more_text(cards, filters=None, priced=True):
    """The "Show 3 more" reply (was the fixed "Here are 3 more from the same search." on 29 replies): which
    search they come from and the one card that stands out, from the cards' own data. No model call."""
    f = filters or {}
    scope = []
    if f.get("city"):
        scope.append(str(f["city"]))
    elif f.get("states"):
        scope.append(" and ".join(f["states"][:2]))
    if f.get("guests"):
        scope.append("%s guests" % f["guests"])
    if priced and f.get("max_venue_fee"):
        scope.append("rental under %s" % _k(f["max_venue_fee"]))
    head = "%d more from the same search%s, none shown before." % (len(cards), (" (" + ", ".join(scope) + ")") if scope else "")
    rents = [(c.get("venue_fee_range") or [None])[0] for c in cards]
    priced_rents = [(r, c) for r, c in zip(rents, cards) if isinstance(r, (int, float)) and r > 0]
    if priced and len(priced_rents) > 1:
        r, c = min(priced_rents, key=lambda x: x[0])
        return head + " %s has the lowest rental of these (%s)." % (c.get("name"), _k(r))
    caps = [(c.get("max_capacity_seated"), c) for c in cards if isinstance(c.get("max_capacity_seated"), (int, float))
            and c["max_capacity_seated"] > 0]
    if len(caps) > 1:
        n, c = min(caps, key=lambda x: x[0])
        return head + " %s is the most intimate (seats %d)." % (c.get("name"), n)
    return head


# ---------------------------------------------------------------- repeats (2026-10-14)
# 10 replies / 7 users re-showed venues already shown, sometimes as "new" (chat 64 Fonthill Castle, chat 33 The
# Harper OC, chat 29 SF Zoo for "Cheaper options"). The prompt rule was not enough, so: search results leave out
# what this chat showed (and Saved venues unless the couple asked about them), and present_recommendations bounces
# a repeat the couple didn't ask for once.

# The couple pointing back at earlier picks or their Saved list - re-showing those is the answer, not a repeat.
REFERS_BACK = re.compile(r"compare|saved|liked|favou?rite|my list|break ?down|itemi[sz]e|which of|those|these|"
                         r"\bthem\b|again|earlier|first one|top pick|you showed|you mentioned|realistic|"
                         r"venues? i like|the one", re.I)
SAVED_ASK = re.compile(r"saved|liked|favou?rite|my list|hearted|venues? i like", re.I)
NEW_CLAIM = re.compile(r"\bnew\b|haven'?t (?:seen|been shown)|not seen|fresh (?:picks|options)", re.I)
_NAME_STOP = {"the", "and", "venue", "venues", "hotel", "house", "club", "estate", "mansion", "gardens", "garden",
              "weddings", "wedding", "events", "event", "center", "centre", "inn", "farm", "farms", "winery", "park",
              "resort", "lodge", "hall", "manor", "castle", "country", "restaurant", "museum", "spa"}


def named_in(text, name):
    """True if the couple's message names this vendor (a distinctive word of its name, e.g. 'Fonthill')."""
    words = [w for w in re.findall(r"[a-z]+", (name or "").lower()) if len(w) > 3 and w not in _NAME_STOP]
    low = (text or "").lower()
    return any(re.search(r"\b%s\b" % re.escape(w), low) for w in words)


PEAK_CAVEAT = re.compile(r"peak[- ]season saturday|peak saturday|peak-saturday|saturday evening", re.I)
OPEN_PDF = re.compile(r"\bopen\b[^.]{0,80}\bpdf\b", re.I)


def strip_repeat_caveat(text):
    """Drop sentences carrying the peak-Saturday caveat (it was in 60 of 107 real replies; the prompt said
    "once per conversation"). Used only when an earlier reply in this chat already said it. Breakdown lines
    ("• ...") are left alone."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text or "")
    kept = [p for p in parts if p.lstrip().startswith("•") or not PEAK_CAVEAT.search(p)]
    return " ".join(kept).strip() if kept else (text or "")


def run(token, user, paid, messages, profile_override=None, memory_notes=None, on_note=None, user_context="",
        detail="full", prior_vendor_ids=(), preferences="", active_filters=None, progress=None, search_log=None):
    """messages: prior turns as [{"role": "user"|"assistant", "content": str}, ...], last one the user's.
    memory_notes: durable notes saved in earlier conversations (rec_memory).
    on_note(text): called when the model saves a new durable note.
    detail: "full" (Forever) or "light" (everyone else - fit, not figures; see _light_pricing), or "free"
        (no active plan, 2026-10-14: no price of any kind - see free_mode.py; implies light).
    prior_vendor_ids: venues earlier turns of this chat showed as cards (server-stored, so trusted).
    active_filters: the filters of the chat's latest search (stored with that answer) - inherited by search_venues
        (merge_filters) so a stated location, city, guest count or price cap holds until the couple changes it.
    progress: a progress.Tracker (request_id sent, 2026-10-25) - friendly labels as the stages run, and Stop: it is
        checked before every model call and before every tool call (and between search and pricing inside
        search_venues); once set, no further model or tool call runs and the result is {"stopped": True, ...}.
        None = no labels, never stops (exactly as before).
    search_log: a railway_search.SearchLog (2026-10-26); None = one for this user's arm (railway_search.arm). Its
        summary is usage["search"] (engine / fallback / ms of the ep119-path venue searches).
    Returns (result_dict, usage_dict)."""
    prog = progress or progress_mod.NULL
    slog = search_log if search_log is not None else railway_search.SearchLog(railway_search.arm((user or {}).get("id")))
    free = detail == "free"
    light = detail in ("light", "free")
    seen = {}            # vendor_id -> card data, filled by search_venues in THIS request
    extra = {}           # vendor_id -> card data for "Show 3 more" ONLY: search results past the 8 the model sees
    searched = []        # vendor_ids search_venues returned in THIS request, in search order
    searches = []        # one per search_venues call: {"filters": effective filters, "ids": its results in order}
    prior = set(prior_vendor_ids or ())   # shown earlier in this chat: may be presented again (looked up)
    active = dict(active_filters or {})
    last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    last_user = last_user if isinstance(last_user, str) else ""
    asked_back = bool(REFERS_BACK.search(last_user))
    prior_text = " ".join(m["content"] for m in messages[:-1]
                          if m.get("role") == "assistant" and isinstance(m.get("content"), str))
    said_peak = bool(PEAK_CAVEAT.search(prior_text))
    said_pdf = bool(OPEN_PDF.search(prior_text))
    final = {}
    saved_cards_holder = {}
    # profile_override: eval runs only - real requests always use the verified user row.
    profile = profile_override if profile_override is not None else xano.profile_of(user)
    pool = ThreadPoolExecutor(max_workers=8)
    # Background pricing of "Show 3 more" candidates (2026-10-17): its own workers, so it never queues in front
    # of a tool call the model is waiting on.
    defer_pool = ThreadPoolExecutor(max_workers=PRICE_WAVE)
    deferred = []        # one per search_venues call with candidates still being priced
    # Per-tool timing for rec_server_request (2026-10-17): ms per tool name, plus search vs pricing inside
    # search_venues.
    timing = {"search_ms": 0, "pricing_ms": 0, "searches": 0, "priced": 0, "tools": {}}

    batch = {"pricing": 0}   # pricing lookups (one venue each) the current model turn asked for, for the label

    def timed(f):
        @functools.wraps(f)
        def w(*a, **k):
            # Stop checkpoint + progress label (2026-10-25). A stopped request runs no tool at all: Stopped becomes
            # an error tool result inside the runner, and the loop below breaks before the next model call.
            prog.check()
            if f.__name__ in progress_mod.PRICING_TOOLS:
                prog.set(progress_mod.pricing_label(batch["pricing"]))
            else:
                prog.set(progress_mod.TOOL_LABELS.get(f.__name__))
            t = time.time()
            try:
                return f(*a, **k)
            finally:
                timing["tools"][f.__name__] = timing["tools"].get(f.__name__, 0) + int((time.time() - t) * 1000)
        return w

    try:
        guests = int(profile.get("Wedding_Guest_Count") or 0)
    except (TypeError, ValueError):
        guests = 0
    try:
        budget = int(profile.get("Wedding_Budget") or 0)
    except (TypeError, ValueError):
        budget = 0

    guests_default = guests

    def shape(p):
        return _light_pricing(p, guests) if light else _compact_pricing(p, guests, budget)

    def card(v, p):
        return _with_rental(v, p, guests, budget)

    pricing_cache = {}   # vendor_id -> raw pricing payload, fetched once per request

    def raw_pricing_or_none(vid):
        if vid in pricing_cache:
            return pricing_cache[vid]
        try:
            pricing_cache[vid] = xano.venue_pricing(token, vid, paid and not light)
        except Exception:
            pricing_cache[vid] = None
        return pricing_cache[vid]

    def pricing_or_none(vid):
        p = raw_pricing_or_none(vid)
        return shape(p) if p else None

    evidence = []        # every tool result this request, for the figure check
    guard = {"bounced": False, "unsupported": []}

    def _ev(obj):
        out = json.dumps(obj)
        evidence.append(out)
        return out

    @beta_tool
    @timed
    def search_venues(states: list[str], guests: int = 0, max_venue_fee: int = 0,
                      max_food_per_person: int = 0, venue_types: list[str] = None,
                      vibes: list[str] = None, pricing_models: list[str] = None,
                      keyword: str = "", sort_by: str = "popular_desc", outdoor_ceremony: bool = False,
                      city: str = "", radius_miles: int = 0, relax: list[str] = None) -> str:
        """Search Tulle's venues (only ones with real pricing PDFs). Each result includes its pricing
        summary (rental fee, food and drink, ceremony, service charge, guest minimum, state comparison).
        Venues already shown in this chat, and the couple's Saved venues (unless they asked about those), are
        left out automatically (already_shown_left_out). The chat's ACTIVE FILTERS (guests, city, price caps)
        apply automatically to any of those you leave empty; `filters_used` shows what was searched.

        Args:
            city: optional town or city the couple named or wants to stay near, e.g. "Flower Mound", "San
                Diego", "Napa", "Manhattan", "Brooklyn": venues within radius_miles of it, nearest first, each
                with distance_miles (NYC areas: addresses there). Use it whenever they name a place - keyword
                does not filter by location. For a region, pass its main town ("Hill Country" -> "Fredericksburg",
                "Hudson Valley" -> "Kingston") with a bigger radius_miles.
            radius_miles: how far from the city (default 25, max 100). "Within an hour" ~ 50.
            relax: names of active filters to DROP because the couple changed or removed them, any of
                "guests", "max_venue_fee", "max_food_per_person", "city", "radius_miles". Only when they said so.
            states: US states or 'International', e.g. ["New York"]. Use the couple's location if unsure.
            guests: the party size. Venues must seat at least this many, and venues whose guest minimum is
                above it are left out (0 = any) - so for a small wedding or elopement pass the real count.
            max_venue_fee: max rental fee in dollars (0 = any).
            max_food_per_person: max food and drink per guest in dollars (0 = any).
            venue_types: optional, EXACT values only (any of = OR): "Dedicated Event Venue", "Hotel / Resort",
                "Estate / Mansion", "Barn / Ranch", "Restaurant / Bar", "Country Club / Private Club",
                "Winery / Brewery / Distillery", "Museum / Gallery", "Civic / Public", "Garden / Botanical Garden",
                "Performing Arts Venue", "Religious", "University / College", "Zoo / Aquarium". Map the couple's words: rustic/barn/farm -> "Barn / Ranch";
                mansion/estate -> "Estate / Mansion"; vineyard -> "Winery / Brewery / Distillery".
            vibes: optional style attributes, EXACT values only (any of = OR): "Scenic / Nature Views",
                "Natural Light / Large Windows", "Historic Architecture", "Ballroom", "Tall / Vaulted Ceilings",
                "Waterfront", "Tented", "Rooftop / Skyline Views", "Industrial / Warehouse", "Greenhouse".
                beach/lake/river -> "Waterfront"; city views -> "Rooftop / Skyline Views"; loft -> "Industrial / Warehouse".
                glasshouse/glass house/conservatory/atrium/glass walls -> "Greenhouse"; lots of windows/light-filled ->
                "Natural Light / Large Windows"; architectural/historic/grand building -> "Historic Architecture";
                upscale/elegant/luxury/black-tie -> "Ballroom" plus "Historic Architecture" (any of).
            pricing_models: optional: "All-Inclusive" (outside catering NOT allowed), "Semi-Inclusive" (some
                rentals included, outside catering allowed), "Raw Space" (empty space only, no tables or chairs; rare).
                Bring their own caterer / outside catering -> ["Raw Space", "Semi-Inclusive"], never "Raw Space" alone.
            outdoor_ceremony: true = only venues whose PDF lists an outdoor ceremony space (garden, lawn,
                terrace, courtyard...). Use it when the couple wants an outdoor ceremony.
            keyword: optional free text - a city or a venue name. Prefer the tag filters above for styles.
            sort_by: popular_desc (default), recent_desc, capacity_asc or capacity_desc.
        """
        # Small-wedding fit (2026-10-04, a 6-guest $10k elopement got 50-guest-minimum and $9,000-food-minimum
        # venues): ep119 can't filter on minimums, so over-fetch and drop venues whose KNOWN minimum doesn't fit.
        # A 0 / missing minimum means "none or not known" and is kept - never treat missing as "no minimum".
        food_cap = budget // 2 if budget > 0 else 0      # venue + food is ~40-50% of the total budget
        eff = merge_filters({"states": [s for s in (states or []) if s], "guests": int(guests or 0),
                             "max_venue_fee": int(max_venue_fee or 0),
                             "max_food_per_person": int(max_food_per_person or 0),
                             "city": (city or "").strip(), "radius_miles": int(radius_miles or 0)},
                            active, relax)
        states, guests = eff["states"], eff["guests"]
        max_venue_fee, max_food_per_person, city = eff["max_venue_fee"], eff["max_food_per_person"], eff["city"]
        prog.set(progress_mod.search_label(states or _states_of(profile), city))
        q = dict(states=states, guests=guests, max_venue_fee=max_venue_fee, max_food_per_person=max_food_per_person,
                 venue_types=venue_types, vibes=vibes, pricing_models=pricing_models, keyword=keyword,
                 sort_by=sort_by, outdoor_ceremony=outdoor_ceremony)
        # Already shown in this chat, or on the Saved list the couple didn't ask about: left out so "more" and
        # "cheaper" bring venues they haven't seen. A venue the keyword or the couple's message names stays.
        hide = set(prior) | (set() if SAVED_ASK.search(last_user) else set(saved_ids))
        t_search = time.time()
        # A town the couple named -> every venue within radius_miles of it, nearest first (2026-10-21 chat 247).
        # NYC areas keep the ZIP match below; a place that won't geocode (a region, a typo, a Xano or geocoder
        # blip) falls back to matching the name in the address.
        radius = max(5, min(int(eff.get("radius_miles") or NEAR_MILES), 100))
        near = None
        if city and _norm_city(city) not in _CITY_ZIPS and states != ["International"]:
            try:
                near = xano.geocode_city(city, states)
            except Exception:
                near = None
        if near:
            try:
                r = xano.venues_near(near[0], near[1], radius, guests=guests, max_venue_fee=max_venue_fee,
                                     max_food_per_person=max_food_per_person, venue_types=venue_types,
                                     vibes=vibes, pricing_models=pricing_models, keyword=keyword,
                                     outdoor_ceremony=outdoor_ceremony)
                venues = r["venues"]
            except Exception:
                near = None
        if near:
            pass
        elif city or hide:
            # Read the top 60 results. ep119 has no city filter, so a city keeps only the addresses in it; and
            # hiding shown venues from one 16-result page left 0-1 new ones on the 4th "more" (2026-10-14 replay
            # of chat 33: "you've now seen every venue" - untrue).
            # 2026-10-17: ONE page of 60 instead of pages 1-3 of 20 in parallel. Measured on fresh cache keys
            # (6 states): identical venues in identical order, 2.9-4.8s vs 5.9-11.6s - the three parallel
            # pages collided in Xano, and pages 2-3 take ep119's id-list path. A failure retries once at 20.
            try:
                r = xano.search_venues(token, page_size=SEARCH_POOL, search_log=slog, **q)
            except Exception:
                r = xano.search_venues(token, page_size=20, search_log=slog, **q)
            dedup = {}
            for v in r["venues"]:
                dedup.setdefault(v.get("vendor_id"), v)
            venues = [v for v in dedup.values() if in_city(v.get("address"), city)]
        else:
            r = xano.search_venues(token, page_size=16, search_log=slog, **q)
            venues = r["venues"]
        timing["search_ms"] += int((time.time() - t_search) * 1000)
        timing["searches"] += 1
        kw = (keyword or "").lower().strip()
        keep = lambda v: (v.get("vendor_id") not in hide or (kw and kw in (v.get("name") or "").lower())
                          or named_in(last_user, v.get("name")))
        n_before = len(venues)
        venues = [v for v in venues if keep(v)]
        n_hidden = n_before - len(venues)
        removed = {"food_minimum_over_half_budget": 0, "guest_minimum_over_party": 0, "rental_over_cap": 0,
                   "food_per_guest_over_cap": 0}
        if food_cap:
            kept = [v for v in venues if not (v.get("food_minimum") or 0) > food_cap]
            removed["food_minimum_over_half_budget"] = len(venues) - len(kept)
            venues = kept
        # Pricing is one ep230 call per venue (8 in parallel). With a guest count or a price cap every venue has
        # to be priced before it can be shown: up to PRICED_MAX, covering the 8 the model sees + "Show 3 more";
        # with a guest count, venues past PRICED_MAX are dropped (their minimum is unknown), as before.
        # 2026-10-17: priced in waves of 8 until the model's 8 are filled; the rest of the PRICED_MAX window only
        # feeds "Show 3 more", so it is priced in the background while the model writes and filtered the same
        # way in _finish_deferred (same cards, same order as pricing all 24 up front).
        t_price = time.time()
        filtering = bool(guests or max_venue_fee or max_food_per_person)
        window = venues[:PRICED_MAX] if filtering else venues[:PRICED_PER_SEARCH]
        tail = ([] if guests else venues[len(window):])     # kept unpriced, as before
        pairs, i = [], 0
        while i < len(window) and (len(pairs) < SHOWN_MAX or not filtering):
            wave = window[i:i + PRICE_WAVE] if filtering else window
            prog.check()   # Stop between the search and each pricing wave (2026-10-25)
            prog.set(progress_mod.pricing_label(len(wave)))
            raws = list(pool.map(raw_pricing_or_none, [v["vendor_id"] for v in wave]))
            i += len(wave)
            for v, p in zip(wave, raws):
                why = _misfit(p, guests, max_venue_fee, max_food_per_person)
                if why:
                    removed[why] += 1
                else:
                    pairs.append((v, p))
        timing["pricing_ms"] += int((time.time() - t_price) * 1000)
        timing["priced"] += i
        later = window[i:]              # filtering only: priced in the background, filtered at the end
        later_f = [(v, defer_pool.submit(raw_pricing_or_none, v["vendor_id"])) for v in later]
        if not filtering:
            pairs += [(v, None) for v in tail]
            tail = []
        # The model sees the top 8; the rest only feed "Show 3 more" (2026-10-07: capping the whole pool at
        # 8 left at most 5 after the picks - often under 3 once shown venues were removed, so no button).
        rest = [card(v, p) for v, p in pairs[SHOWN_MAX:]]
        pairs = pairs[:SHOWN_MAX]
        venues = [v for v, _ in pairs]
        prices = [shape(p) if p else None for _, p in pairs]
        out = []
        for i, v in enumerate(venues):
            v = card(v, pairs[i][1])
            if light:
                v = _light_card(v)
            if v.get("vendor_id"):
                seen[v["vendor_id"]] = v
                if v["vendor_id"] not in searched:
                    searched.append(v["vendor_id"])
            row = {k: v.get(k) for k in ("vendor_id", "name", "state", "address", "venue_type",
                                         "max_capacity_seated", "venue_fee_range", "description",
                                         "distance_miles") if k in v}
            if not any(row.get("venue_fee_range") or []):
                row["venue_fee_range"] = "rental not in this venue's PDF"   # never read a 0 placeholder as "$0"
            if i < len(prices) and prices[i]:
                row["pricing"] = prices[i]
            out.append(row)
        for v in rest:                      # after the top 8, so "Show 3 more" keeps search order
            if v.get("vendor_id") and v["vendor_id"] not in seen:
                extra[v["vendor_id"]] = _light_card(v) if light else v
                if v["vendor_id"] not in searched:
                    searched.append(v["vendor_id"])
        ids = [v["vendor_id"] for v in out if v.get("vendor_id")] + [v["vendor_id"] for v in rest if v.get("vendor_id")]
        entry = {"filters": {k: val for k, val in dict(eff, venue_types=venue_types or [], vibes=vibes or [],
                                                       pricing_models=pricing_models or [],
                                                       outdoor_ceremony=bool(outdoor_ceremony)).items()
                             if val not in (None, "", 0, [], False)},
                 "ids": ids}
        searches.append(entry)
        if later_f or tail:   # "Show 3 more" candidates still being priced: filtered + appended in _finish_deferred
            deferred.append({"entry": entry, "later": later_f, "tail": tail,
                             "limits": (guests, max_venue_fee, max_food_per_person)})
        res = {"total_matches": r["total_matches"], "venues": out}
        if city:   # ep119's total is the whole state's (candidates still being priced count as matches)
            res["total_matches"] = len(out) + len(rest) + len(later_f) + len(tail)
            if near:
                res["city_note"] = ("venues within %d miles of %s, nearest first (distance_miles on each)"
                                    % (radius, near[2]))
                if not out and radius < 100:
                    res["city_note"] += (". None fit within %d miles: search again with a bigger radius_miles "
                                         "(e.g. %d) before saying we have nothing nearby" % (radius, min(100, radius * 2)))
            else:
                res["city_note"] = "only venues whose address is in %s, from the top 60 results for %s" % (
                    city, " and ".join(states) or "all states")
        if n_hidden:
            res["already_shown_left_out"] = n_hidden
        if any(removed.values()):
            res["left_out_for_minimums"] = {k: v for k, v in removed.items() if v}
        if r.get("matches_by_state") and not city:
            res["matches_by_state"] = r["matches_by_state"]
        used = {k: val for k, val in eff.items() if val not in (None, "", 0, [])}
        if light:   # no figures for a non-member's model, not even a carried price cap
            used = {k: val for k, val in used.items() if k not in ("max_venue_fee", "max_food_per_person")}
        res["filters_used"] = used
        return _ev(res)

    def bench_or_none(category, state):
        try:
            return xano.pe_benchmarks(category, state)
        except Exception:
            return None

    @beta_tool
    @timed
    def search_photographers_entertainment(category: str, states: list[str], max_price: int = 0,
                                           types: list[str] = None, offers: list[str] = None,
                                           hour_bands: list[str] = None, keyword: str = "",
                                           sort_by: str = "popular_desc") -> str:
        """Search Tulle's photographers or entertainers (only ones with real pricing PDFs). Each result has
        its matching packages (name, price, hours, type, team, included, add-ons), price range, and how its
        cheapest package compares with the state's market rate.

        Args:
            category: "Photography" or "Entertainment".
            states: US states or 'International', e.g. ["New York"]. Use the couple's location if unsure.
            max_price: max PACKAGE price in dollars (0 = any). Only packages at or under it count.
            types: optional, EXACT values (any of = OR). Photography package types: "Wedding Day", "Engagement",
                "Elopement", "Other". Entertainment service types: "DJ", "Live Band", "Ensemble", "Solo Performer",
                "Other Entertainment". Pianist/harpist/violinist/singer -> "Solo Performer"; quartet/trio -> "Ensemble".
            offers: optional, EXACT values (any of = OR). Photography: "Second Shooter", "Engagement Session",
                "Album", "Prints", "Full Gallery Rights", "Videography", "Drone", "Rehearsal Dinner",
                "Content Creation". Entertainment: "Photo Booth", "Ceremony Sound", "Sound & Lighting Provided",
                "Monogram / Projection", "Special Effects".
            hour_bands: Photography only, coverage hours: "6 Hours or Less", "7-10 Hours", "11+ Hours".
            keyword: optional instrument or style matched against names, packages and descriptions, e.g. "piano",
                "harp", "string quartet", "jazz". Results that mention it come first (keyword_matches = how many).
            sort_by: popular_desc (default) or recent_desc.
        """
        category = "Photography" if str(category).lower().startswith("photo") else "Entertainment"
        prog.set(progress_mod.pe_label(category))
        sts = [s for s in (states or []) if s] or _states_of(profile)
        bench_f = {s: pool.submit(bench_or_none, category, s) for s in sts}
        r = xano.search_pe(token, category, states=sts, max_price=max_price, types=types, offers=offers,
                           hour_bands=hour_bands, keyword=keyword,
                           sort_by=sort_by if sort_by in ("popular_desc", "recent_desc") else "popular_desc",
                           page_size=14)
        benches = {s: f.result() for s, f in bench_f.items()}
        first = benches.get(sts[0]) if sts else None
        out = []
        for i, v in enumerate(r["vendors"]):
            bench = benches.get(v.get("state")) or first
            card = _pe_card(dict(v, venue_type=_pe_type_label(v)), light)
            vid = card.get("vendor_id")
            if not vid:
                continue
            if i < 8:                       # the model sees the top 8; the rest only feed "Show 3 more"
                seen[vid] = card
                out.append(_pe_row(v, bench, light))
            elif vid not in seen:
                extra[vid] = card
            if vid not in searched:
                searched.append(vid)
        searches.append({"filters": {"category": category, "states": sts},
                         "ids": [v.get("vendor_id") for v in r["vendors"] if v.get("vendor_id")]})
        res = {"category": category, "total_matches": r["total_matches"], "vendors": out}
        if "keyword_matches" in r:
            res["keyword_matches"] = r["keyword_matches"]
        if r.get("matches_by_state"):
            res["matches_by_state"] = r["matches_by_state"]
        if not light:
            mk = [m for m in (_market_row(benches.get(s), types) for s in sts) if m]
            if mk:
                res["market"] = mk
        return _ev(res)

    @beta_tool
    @timed
    def pe_market_rates(category: str, state: str) -> str:
        """What photographers or entertainers typically cost in a state: package-price quartiles (p25 /
        median / p75, with sample sizes) overall, by package type and coverage hours (Photography) or by
        service type - DJ, Live Band, Ensemble, Solo Performer (Entertainment).

        Args:
            category: "Photography" or "Entertainment".
            state: a single state, e.g. "Texas", or "International".
        """
        category = "Photography" if str(category).lower().startswith("photo") else "Entertainment"
        return _ev(xano.pe_benchmarks(category, state))

    @beta_tool
    @timed
    def venue_pricing(vendor_id: str) -> str:
        """Full pricing breakdown for one venue from its uploaded pricing PDF. Only needed for a venue
        whose pricing was not already included in a search result.

        Args:
            vendor_id: a vendor_id returned by search_venues.
        """
        p = xano.venue_pricing(token, vendor_id, paid and not light)
        pricing_cache[vendor_id] = p
        return _ev(dict(shape(p), headline=p.get("headline"), source_note=p.get("source_note")))

    @beta_tool
    @timed
    def saved_venues() -> str:
        """The vendors this couple saved (their Saved list), each with its pricing summary and guest
        minimum. Use it for "compare my saved venues", "which of my saved venues...", or to anchor
        recommendations to what they already like."""
        cards = saved_cards_holder.get("cards") or []
        raws = list(pool.map(raw_pricing_or_none, [c["vendor_id"] for c in cards[:PRICED_PER_SEARCH]]))
        prices = [shape(p) if p else None for p in raws]
        out = []
        for i, c in enumerate(cards):
            if i < len(raws):
                c = card(c, raws[i])
            if light:
                c = _light_card(c)
            seen[c["vendor_id"]] = c
            row = {k: c.get(k) for k in ("vendor_id", "name", "state", "address", "category", "venue_type",
                                         "max_capacity_seated", "venue_fee_range") if k in c}
            if "venue_fee_range" in row and not any(row["venue_fee_range"] or []):
                row["venue_fee_range"] = "rental not in this venue's PDF"
            if i < len(prices) and prices[i]:
                row["pricing"] = prices[i]
            out.append(row)
        return _ev({"saved_count": len(cards), "saved": out})

    @beta_tool
    @timed
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
            return _ev({"error": "unknown vendor_id - search for the venue first"})
        d = xano.venue_details(vendor_id, with_amounts=paid and not light)
        if light and d.get("google"):   # LIGHT gets no figures: drop review texts that quote prices
            d["google"]["reviews"] = [r for r in d["google"]["reviews"] if "$" not in r["text"]]
        return _ev(d)

    @beta_tool
    @timed
    def estimate_cost(vendor_id: str, guests: int = 0, off_peak: bool = False) -> str:
        """The all-in cost of one venue, itemized and computed for you - the same formula as the venue's page:
        rental + ceremony + food and drink (or its minimum, whichever is higher), plus the service charge;
        tax shown separately when the venue states it. Lines the PDF doesn't give are filled with an
        estimate and marked. Use it for every total, per-guest cost, breakdown and what-if (a different
        guest count, the cheapest Saturday) - never do this arithmetic yourself.

        Args:
            vendor_id: a vendor_id returned by search_venues or saved_venues in this conversation.
            guests: the guest count to price (0 = the couple's).
            off_peak: true = the venue's cheapest-Saturday prices from its PDF (if it lists them).
        """
        if vendor_id not in seen and vendor_id not in prior:
            return _ev({"error": "unknown vendor_id - search for the venue first"})
        p = raw_pricing_or_none(vendor_id)
        inp = (p or {}).get("cost_inputs")
        if not inp:
            return _ev({"error": "no pricing breakdown for this venue"})
        g = int(guests or 0) or guests_default
        override = None
        if off_peak:
            override = estimate.off_peak_override(xano.venue_details(vendor_id, with_amounts=True), inp.get("space"))
            if not override:
                return _ev({"off_peak": "this venue's PDF lists no cheapest-Saturday prices",
                                   "peak": estimate.compute(inp, g)})
        e = estimate.compute(inp, g, override)
        if budget:
            e["share_of_budget_pct"] = round(100.0 * e["total_before_tax_and_gratuity"] / budget)
        if off_peak:
            e["prices"] = "cheapest Saturday%s (this venue's PDF)" % (
                " - " + override["months"] if override.get("months") else "")
            e["peak_total_before_tax_and_gratuity"] = estimate.compute(inp, g)["total_before_tax_and_gratuity"]
        return _ev(e)

    @beta_tool
    @timed
    def market_benchmarks(state: str) -> str:
        """What venues typically cost in a state OTHER than the couple's (theirs is already in context):
        quartiles for rental fee, food and drink per guest, service charge, and all-in cost per guest
        at 75/125/200 guests.

        Args:
            state: a single state, e.g. "Texas", or "International".
        """
        return _ev(xano.market_benchmarks(state, guests))

    widgets = []          # cost-tool widgets shown under this answer (cost_tools.py), in call order
    tool_user = dict(user, **profile) if profile_override is not None else user

    def _cost_tool(name, args):
        """Run a cost tool, keep its widget for the page, and hand the model its facts (FULL) - or, at LIGHT / FREE
        (no figures for the model), only what was shown. The widget is the couple's to see either way; free mode's
        widget guard (free_mode.widget) keeps vendor PDF prices out of venue_compare."""
        try:
            out = cost_tools.run_tool(name, args, token, tool_user, access=paid, xano=xano)
        except Exception as e:
            return _ev({"error": "could not run %s right now" % name, "detail": str(e)[:100]})
        w = free_mode.widget(out["widget"]) if free else out["widget"]
        widgets[:] = [x for x in widgets if x.get("type") != w.get("type")] + [w]   # a what-if replaces the last
        if light:
            return _ev({"shown": w["type"], "missing": w.get("missing") or [], "locked": bool(w.get("locked")),
                        "note": "The couple sees this as a chart under your message. Describe it in words - no figures."})
        return _ev({"shown": w["type"], "facts": out["facts"],
                    "note": "The couple sees this as a chart under your message - don't re-list it."})

    @beta_tool
    @timed
    def estimate_wedding_cost(state: str = "", guests: int = 0, major_city: bool = None, peak_season: bool = None,
                              budget: int = 0) -> str:
        """What a whole wedding typically costs in a state for a guest count: Minimalist / Average / Luxury tiers
        (Tulle's state average wedding cost x a guest-count factor, x1.2 peak season, x1.3 major city) and how the
        couple's budget compares with Average. Shown to the couple as a chart. Use it for "how much will our
        wedding cost?", "is our budget enough?", and what-ifs on guests, season or city.

        Args:
            state: a US state, e.g. "New York" (empty = the couple's).
            guests: guest count (0 = the couple's).
            major_city: true = in or near a major city (empty = the couple's profile setting).
            peak_season: true = peak-season date (empty = the couple's profile setting).
            budget: total budget to compare against (0 = the couple's).
        """
        return _cost_tool("estimate_cost", {"state": state, "guests": guests, "major_city": major_city,
                                            "peak_season": peak_season, "budget": budget})

    @beta_tool
    @timed
    def split_budget(total: int = 0, focus: str = "normal", excluded_categories: list[str] = None) -> str:
        """Split a total wedding budget across Tulle's 13 budget categories (Venue, Food and Beverage, Photography,
        Flowers, Entertainment, Wedding Planner, Attire, ...), shown as a chart. Use it for "how should we split our
        budget?", "how much for flowers?", or a what-if without a category.

        Args:
            total: the total to split (0 = the couple's budget).
            focus: what they want to prioritise: "normal" (balanced), "venue", "food", "flowers", "planner" or
                "entertainment".
            excluded_categories: category names they don't need, e.g. ["Wedding Planner"]; the rest re-scale to 100%.
        """
        return _cost_tool("split_budget", {"total": total, "focus": focus,
                                           "excluded_categories": excluded_categories or []})

    @beta_tool
    @timed
    def compare_venues(guests: int = 0) -> str:
        """Compare the couple's SAVED venues (all-in from each venue's pricing PDF, the venue page's formula) with
        the venue quotes they entered themselves, cheapest first, against their budget - shown as a table. Use it
        for "compare my venues", "which of my saved venues is cheapest?".

        Args:
            guests: guest count to price the saved venues at (0 = the couple's).
        """
        return _cost_tool("compare_venues", {"guests": guests})

    @beta_tool
    @timed
    def budget_status() -> str:
        """Where the couple's own budget stands: what they've added on their Budget tab, paid and still owe, by
        category, against their total budget - shown as a chart. Use it for "how are we doing on budget?",
        "how much have we spent?", "what do we still owe?"."""
        return _cost_tool("budget_status", {})

    # REC_COST_TOOLS_IN_CHAT off (default): no cost tools, no prompt paragraph, widgets stays [] - chat as before.
    cost_on = config.COST_TOOLS_IN_CHAT
    cost_tool_list = [estimate_wedding_cost, split_budget, compare_venues, budget_status] if cost_on else []

    saved_notes = []

    @beta_tool
    @timed
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
    @timed
    def present_recommendations(message: str, vendor_ids: list[str], reasons: list[str],
                                chips: list[str]) -> str:
        """Show your answer to the couple. Call exactly once, last.

        Args:
            message: 2-3 short sentences to the couple (about 60 words). Only a COST BREAKDOWN the couple
                explicitly asked for is longer and uses the itemized "• " list; a budget question may take 4.
            vendor_ids: 3-5 vendor_ids from search_venues, search_photographers_entertainment or saved_venues,
                best first (fewer only if fewer fit; 1 for a single-venue breakdown, 0 for a question only about an
                unsupported vendor type).
            reasons: one short reason per vendor_id (at most about 15 words), same order, each saying how it
                differs from the others.
            chips: 2-4 short follow-ups that follow the CHIPS rule (each maps to a search filter, a pricing
                line, a state comparison or the Saved list), e.g. "Cheaper options", "Barn / Ranch venues",
                "DJs with a photo booth".
        """
        shown = " ".join([message or ""] + [r or "" for r in (reasons or [])])
        allowed = set()
        for blob in evidence + [context] + [m.get("content") if isinstance(m.get("content"), str) else ""
                                             for m in messages]:
            allowed |= numbers_in(blob)
        totals = [n for blob in evidence for n in numbers_in(" ".join(
            re.findall(r'"total_(?:before_tax_and_gratuity|with_tax)": (\d+)', blob)))]
        allowed |= derived_numbers(guests, budget, totals)
        bad = unsupported_figures(shown, allowed)
        if bad and not guard["bounced"]:
            guard["bounced"] = True
            guard["first_try"] = bad
            prog.set(progress_mod.THINKING)
            return ("NOT SHOWN. These dollar figures are not in any data you were given: %s. Quote only figures "
                    "from your tools or the context (estimate_cost gives every total, per-guest cost and what-if), "
                    "or describe it in words. Then call present_recommendations again." % ", ".join(bad))
        guard["unsupported"] = bad
        # Repeats the couple didn't ask for (named, or pointed back at with "compare", "those", "my saved"...).
        hidden = set(prior) | (set() if SAVED_ASK.search(last_user) else set(saved_ids))
        names = {**{c["vendor_id"]: c.get("name") for c in saved_cards_holder.get("cards") or []},
                 **{k: v.get("name") for k, v in seen.items()}}
        again = [v for v in vendor_ids or [] if v in hidden and not named_in(last_user, names.get(v))]
        claims_new = bool(again) and bool(NEW_CLAIM.search(message or ""))
        if again and (claims_new or not asked_back) and not guard.get("repeat_bounced"):
            guard["repeat_bounced"] = True
            guard["repeat_first_try"] = [names.get(v) or v for v in again]
            prog.set(progress_mod.THINKING)
            return ("NOT SHOWN. %s %s already shown in this chat or on their Saved list, and the couple didn't ask "
                    "about %s. Replace %s with venues they haven't seen (search again if you need more), or if one "
                    "really is still the best answer keep it and say plainly it's one they've seen. Never call a "
                    "venue new unless it is. Then call present_recommendations again."
                    % (", ".join(names.get(v) or v for v in again), "was" if len(again) == 1 else "were",
                       "it" if len(again) == 1 else "them", "it" if len(again) == 1 else "them"))
        guard["repeats_shown"] = [names.get(v) or v for v in again]
        final.update(message=message, vendor_ids=vendor_ids, reasons=reasons, chips=chips)
        return "shown"

    def _finish_deferred():
        """The "Show 3 more" candidates each search left pricing in the background: wait for them (at most
        DEFERRED_WAIT_S in all; one not back by then counts as a failed pricing call, which keeps it unpriced,
        as before), drop the ones that break the search's limits, and append the rest - then the unpriced tail -
        to that search's ids and the extra cards, in search order."""
        deadline = time.time() + DEFERRED_WAIT_S
        for d in deferred:
            g, fee_cap, food_cap_pp = d["limits"]
            add = []
            for v, f in d["later"]:
                try:
                    p = f.result(timeout=max(0.0, deadline - time.time()))
                except (FutureTimeout, Exception):
                    p = None
                if not _misfit(p, g, fee_cap, food_cap_pp):
                    add.append(card(v, p))
            add += [card(v, None) for v in d["tail"]]
            for v in add:
                vid = v.get("vendor_id")
                if not vid:
                    continue
                if vid not in seen:
                    extra[vid] = _light_card(v) if light else v
                if vid not in searched:
                    searched.append(vid)
                d["entry"]["ids"].append(vid)

    t0 = time.time()
    # Prefetch in parallel: the couple's state benchmarks + saved vendors.
    states = _states_of(profile)
    bench_f = [] if light else [pool.submit(xano.market_benchmarks, s, guests) for s in states]
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

    context = "Couple profile: " + json.dumps(free_mode.profile_for_model(profile) if free else profile)
    if not states:
        # 2026-10-12 user 827: no state in the profile -> the model kept asking "which state?" and showed
        # 0 venues on 3 of 4 replies. Say so explicitly so the NO-location rule in SYSTEM applies.
        context += "\nLOCATION: NONE IN PROFILE (see the no-location rule)."
    elif states != ["International"] and not (active or {}).get("city"):
        # 2026-10-21 chat 247: the profile only says "Texas", so the opening picks were Abilene, San Antonio and
        # Plano for a couple near Flower Mound, and nothing asked where. The profile has no city field.
        context += ("\nLOCATION: STATE ONLY (%s) - no city or area yet (see the state-only rule). Unless this chat, "
                    "the notes or what they asked you to keep in mind already say where, END your message with the "
                    "question which city or area of %s they want to be close to - in the message itself, not only "
                    "as a chip. It takes the place of the closing next-step line in this reply."
                    % (", ".join(states), " or ".join(states)))
    if free:
        context += "\nDETAIL: FREE. This couple has no Tulle plan: no prices of any kind (see FREE MODE)."
        try:
            views = int(user.get("FreeViewsRemaining"))
        except (TypeError, ValueError):
            views = 0
        if views > 0:
            context += ("\nThey have %d free PDF view%s left - invite them to use one on the PDF of your top pick."
                        % (views, "" if views == 1 else "s"))
    elif light:
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
        for b in benches:
            bc = budget_check(b, guests, budget)
            if bc:
                b["budget_check"] = bc
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
    if preferences:
        context += "\n" + preferences
    if memory_notes:
        context += ("\nWhat we learned in earlier conversations (use it, don't repeat it back): "
                    + json.dumps(memory_notes))
    if active:
        shown_f = {k: v for k, v in active.items() if not (light and k in ("max_venue_fee", "max_food_per_person"))}
        context += ("\nACTIVE FILTERS from this chat's latest search (the couple's stated criteria): "
                    + json.dumps(shown_f) + ". search_venues applies guests, city and price caps automatically when "
                    "you leave them empty; pass relax=[...] only for one the couple changed or dropped, and keep the "
                    "states unless they asked for another place.")
    if said_peak and not free:
        context += "\nALREADY SAID in this chat: the peak-season Saturday caveat. Don't repeat it."
    if said_pdf and not free:
        context += ("\nALREADY SAID in this chat: which PDF to open. Don't write \"open X's PDF\" again - the cards "
                    "have the button. If the top pick's PDF answers something specific, say what it shows "
                    "(\"its PDF lists the per-head menu tiers\") in a few words.")
    msgs = [dict(m) for m in messages]
    msgs[0] = {"role": msgs[0]["role"], "content": context + "\n\n" + msgs[0]["content"]}

    client = anthropic.Anthropic()
    usage = {"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0,
             "tool_calls": 0, "prefetch_ms": int((time.time() - t0) * 1000)}
    runner = client.beta.messages.tool_runner(
        model=config.MODEL,
        max_tokens=8000,
        system=[{"type": "text", "text": SYSTEM + ("\n\n" + COST_TOOLS_RULES if cost_on else ""),
                 "cache_control": {"type": "ephemeral"}}]
               + ([{"type": "text", "text": free_mode.RULES + ("\n" + free_mode.COST_TOOLS_RULE if cost_on else "")}]
                  if free else []),
        # LIGHT gets no market-rate tools (market_benchmarks / pe_market_rates are all figures).
        # FREE gets no price-bearing tool at all, and price-free wrappers of the rest (free_mode.tools).
        # Cost tools (2026-10-25) at every level: their widget is the couple's to see; the model only gets their
        # figures at FULL (_cost_tool).
        tools=(free_mode.tools(search_venues, search_photographers_entertainment, saved_venues, venue_details,
                               save_note, present_recommendations) + cost_tool_list if free else
               [search_venues, search_photographers_entertainment, saved_venues, venue_pricing, venue_details,
                save_note, present_recommendations] + cost_tool_list if light else
               [search_venues, search_photographers_entertainment, saved_venues, venue_pricing, venue_details,
                estimate_cost, market_benchmarks, pe_market_rates, save_note, present_recommendations]
               + cost_tool_list),
        messages=msgs,
        output_config={"effort": config.EFFORT},
        **config.thinking_kwargs(),
        # If Sonnet declines on a safety classifier, the API re-runs on a fallback model in the same
        # call (billed at that model's rates; usage["model"] records which one answered).
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_iterations=config.MAX_TOOL_ROUNDS,
    )
    model_used = config.MODEL
    last_text = ""
    # Per-step timing (2026-10-16): one entry per model turn. ms = time since the previous turn ended, i.e. the
    # tools that ran in between + this turn's writing. The *_chars split shows where output tokens go
    # (thinking vs visible text vs tool arguments); think_chars is 0 when thinking is off or omitted.
    usage["turns"] = []
    t_turn = time.time()
    # Stop (2026-10-25): checked before the first model call, after every model turn (before its tools run) and
    # after its tools (before the next model call). The model call in flight when Stop is tapped can't be
    # interrupted; its tokens are counted below and nothing after it runs.
    stopped = prog.cancelled()
    try:
        for message in ([] if stopped else runner):
            now = time.time()
            texts = [b.text for b in message.content if b.type == "text" and (b.text or "").strip()]
            if texts:
                last_text = "\n\n".join(texts)
            u = message.usage
            tools_used = [b.name for b in message.content if b.type == "tool_use"]
            batch["pricing"] = sum(1 for n in tools_used if n in progress_mod.PRICING_TOOLS)
            stopped = prog.cancelled()     # this turn's tools never run
            # Run this turn's tools NOW (the runner caches the result and reuses it for the next request, so
            # nothing runs twice). Two wins: tool time is measured on its own, and once present_recommendations
            # is accepted (final set) we stop here instead of paying for one more model turn whose text is
            # thrown away (measured 2026-10-16: ~2-3s on every answer). A bounced present_recommendations
            # ("NOT SHOWN ...") leaves final empty, so the model still gets its retry turn.
            tool_ms = 0
            if tools_used and config.EARLY_STOP and not stopped:
                t_tools = time.time()
                runner.generate_tool_call_response()
                tool_ms = int((time.time() - t_tools) * 1000)
            usage["turns"].append({
                "ms": int((now - t_turn) * 1000), "tool_ms": tool_ms, "out": u.output_tokens or 0,
                "think_chars": sum(len(getattr(b, "thinking", "") or "") for b in message.content if b.type == "thinking"),
                "text_chars": sum(len(t) for t in texts),
                "tool_arg_chars": sum(len(json.dumps(getattr(b, "input", {}) or {})) for b in message.content if b.type == "tool_use"),
                "tools": tools_used})
            t_turn = time.time()
            usage["input_tokens"] += u.input_tokens or 0
            usage["output_tokens"] += u.output_tokens or 0
            usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
            usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
            usage["tool_calls"] += sum(1 for b in message.content if b.type == "tool_use")
            model_used = getattr(message, "model", model_used) or model_used
            if final:
                break
            if stopped or prog.cancelled():
                stopped = True
                break
            # Between model turns: the model writes next (or asks for more tools, which relabel themselves).
            prog.set(progress_mod.WRITING if any(n != "present_recommendations" for n in tools_used)
                     else progress_mod.THINKING)
    except progress_mod.Stopped:   # only if a checkpoint's exception escapes the runner (it normally catches it)
        stopped = True
    finally:
        pool.shutdown(wait=False)
    if stopped:
        defer_pool.shutdown(wait=False, cancel_futures=True)   # no more background pricing for "Show 3 more"
        usage.update(latency_ms=int((time.time() - t0) * 1000), model=model_used, stopped=True,
                     tool_breakdown=dict(timing["tools"]), search_ms=timing["search_ms"],
                     pricing_ms=timing["pricing_ms"], searches=timing["searches"], priced_sync=timing["priced"],
                     deferred_wait_ms=0, figure_bounced=guard["bounced"], unsupported_figures=guard["unsupported"],
                     search=slog.summary())
        return ({"stopped": True, "text": "", "cards": [], "chips": [], "more_cards": [], "search_filters": {},
                 "notes_saved": saved_notes, "widgets": [], "dropped_unverified_ids": []}, usage)
    t_def = time.time()
    try:
        _finish_deferred()
    finally:
        defer_pool.shutdown(wait=False)
    usage["deferred_wait_ms"] = int((time.time() - t_def) * 1000)
    if not final and last_text:
        # The model occasionally ends its turn with plain text instead of calling present_recommendations
        # (1 of 3 runs of eval o12, 2026-10-04). Keep its answer rather than showing the couple nothing.
        # Cards: venues from THIS request's searches that the text names (rule 1 still holds).
        named = [vid for vid, v in seen.items() if v.get("name") and v["name"].lower() in last_text.lower()][:4]
        final.update(message=last_text, vendor_ids=named, reasons=[""] * len(named), chips=[])
        usage["no_present_fallback"] = True
    usage["latency_ms"] = int((time.time() - t0) * 1000)
    usage["search_ms"] = timing["search_ms"]          # ep119 inside search_venues
    usage["search"] = slog.summary()                  # Railway search rollout (2026-10-26): engine / fallback / ms
    usage["pricing_ms"] = timing["pricing_ms"]        # ep230/231 inside search_venues, waits only (not background)
    usage["searches"] = timing["searches"]
    usage["priced_sync"] = timing["priced"]
    usage["tool_breakdown"] = dict(timing["tools"])   # ms per tool name, whole tool incl. search + pricing
    usage["figure_bounced"] = guard["bounced"]
    usage["unsupported_figures"] = guard["unsupported"]
    usage["model"] = model_used

    # Rule 1: drop anything the model did not get from a search in this request.
    cards = []
    for i, vid in enumerate(final.get("vendor_ids", [])):
        if vid not in seen and vid in prior:
            try:
                c = xano.vendor_card(vid)
            except Exception:
                c = None
            if c and c.get("category") in xano.PE_CATEGORIES:   # photographer / entertainer: no venue pricing
                seen[vid] = _pe_card(c, light)
            elif c:
                c = card(c, raw_pricing_or_none(vid))
                seen[vid] = _light_card(c) if light else c
        if vid in seen:
            reasons = final.get("reasons") or []
            cards.append(dict(seen[vid], reason=reasons[i] if i < len(reasons) else ""))
    # "Show 3 more" and the next question follow the PRIMARY search: the one most of the picks came from (the
    # latest on a tie, or the latest when no pick came from a search, e.g. a Saved-list comparison).
    picked = {c.get("vendor_id") for c in cards}
    primary = max(reversed(searches), key=lambda s: len(picked & set(s["ids"])), default=None)
    pool = primary["ids"] if primary else []
    venue_searches = [s for s in searches if not s["filters"].get("category")]
    vprimary = primary if primary in venue_searches else (venue_searches[-1] if venue_searches else None)
    text = final.get("message") or ""
    if said_peak and not free:
        text = strip_repeat_caveat(text)
    result = {"text": text, "cards": cards, "chips": final.get("chips") or [],
              "more_cards": more_cards(pool, {**extra, **seen}, cards, prior),
              # stored with this answer (hidden) - the chat's active filters from now on
              "search_filters": dict(vprimary["filters"]) if vprimary else dict(active),
              "notes_saved": saved_notes,
              "widgets": list(widgets),
              "dropped_unverified_ids": [v for v in final.get("vendor_ids", []) if v not in seen]}
    usage["repeat_bounced"] = bool(guard.get("repeat_bounced"))
    usage["repeats_shown"] = guard.get("repeats_shown") or []
    return result, usage
