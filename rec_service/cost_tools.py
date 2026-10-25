"""Cost tools inside the Tulle Assistant (owner decision 2026-10-25: the Cost tools tab goes away; cost questions
are answered in the chat with inline widgets). Four tools, each returning {"widget": {...}, "facts": {...}}:

  widget = what the front end draws (one Vue component, keyed on widget["type"]). Small and stable: add keys,
           never rename them.
  facts  = the numbers the model may quote. They reach the model as a tool result, so the figure check in
           agent.present_recommendations (unsupported_figures) accepts them and nothing else.

The quick actions ("Estimate my cost", "Split my budget", "Compare my venues", "Budget check") call these through
POST /rec/tool with NO model call; typed follow-ups go through the chat, where the model calls the same tools.

The maths is the WeWeb Cost tools tab's, read from the editor 2026-10-25 (page dae212b5) - the tab was the source
of truth, so the chat and the old tab give the same answer:

ESTIMATE (Calculate button 671f6a07 -> workflow 8a4f6c12, tier texts 6ce42de6 / ab650cee / 0b9110f3, verdict 8f069599)
    g       = Math.round(guests); r = Math.round(g / 5) * 5; lookup = clamp(r, 5, 300)
    mod     = table 3 Modifier at lookup (GET /number_of_guests?Guest_count=lookup)
              r > 300: mod = Math.round((mod + (r - 300) / 5 * 0.06) * 100) / 100   (rough extrapolation)
    base    = table 4 Cost_of_Average_Wedding[state] * mod * (peak ? 1.2 : 1) * 1.1 * (major city ? 1.3 : 1)
    tiers   = Math.round(base * 0.3) / Math.round(base) / Math.round(base * 1.7)  (Minimalist / Average / Luxury)
    verdict = budget < base -> "under" (gap% = Math.round(|budget - base| / base * 100)); else gap 0 -> "near";
              else "over". No budget -> null.
    (The 1.1 is applied unconditionally in the tab - kept.)

SPLIT (list 75949ea5, row % 6193ccfc, row cost 290d2de8, footer bb8b09e4; table 19 via collection 53868be4)
    field   = Budget_Focus_{Normal|Venue|Food_Bev|Flowers|Planner|Entertainment} by focus (typical = Normal)
    active  = sum(field) over INCLUDED categories
    pct     = Math.round(row[field] / active * 100); amount = Math.round(total * row[field] / active)
    order   = Budget_Focus_Normal descending (stable). Excluded rows: pct 0, amount 0, included false.

WIDGET SCHEMAS (all carry "type" and "v": 1)

cost_estimate
    {type, v, state, guests, guest_lookup, major_city, peak_season, budget,
     tiers: [{label: "Minimalist"|"Average"|"Luxury", multiplier, amount}],
     verdict: "under"|"near"|"over"|null, gap_pct: int|null, note: str|null, missing: [str]}
    missing lists what blocked the estimate ("state", "guests", "state_average", "guest_modifier"); tiers is []
    then.
budget_split
    {type, v, total, focus, categories: [{id, name, pct, amount, included}], included_count, category_count}
venue_compare
    {type, v, guests, budget, locked: bool, reason: "unlock_pdf_pricing"|null,
     rows: [{name, vendor_id|null, source: "pdf"|"manual", guests, all_in,
             breakdown: {rental, ceremony, food, service_pct}, estimated_lines: [str]}],   sorted by all_in
     unpriced: [{name, vendor_id}],   saved venues with no PDF breakdown (paid) - names only
     saved_venue_count: int}
    all_in is BEFORE tax and gratuity for both sources (pdf: estimate.compute, the venue page's formula; manual:
    rental + ceremony + other fees + per guest x the quote's guest count + the quote's saved service charge).
    Free users (no plan): rows are manual quotes only, locked true, reason "unlock_pdf_pricing".
budget_status
    {type, v, budget, added, paid, owed, remaining, pct_of_budget, item_count, examples_excluded,
     by_category: [{category, added, paid, owed}]}   (by_category sorted by added, descending)
"""
import math
import re
from concurrent.futures import ThreadPoolExecutor

import estimate

V = 1
TIERS = (("Minimalist", 0.3), ("Average", 1.0), ("Luxury", 1.7))
PEAK, ALWAYS, MAJOR = 1.2, 1.1, 1.3
MAX_TABLE_GUESTS, STEP, EXTRA_PER_STEP = 300, 5, 0.06
FOCUS_FIELDS = {"normal": "Budget_Focus_Normal", "typical": "Budget_Focus_Normal", "venue": "Budget_Focus_Venue",
                "food": "Budget_Focus_Food_Bev", "flowers": "Budget_Focus_Flowers",
                "planner": "Budget_Focus_Planner", "entertainment": "Budget_Focus_Entertainment"}
TOOLS = ("estimate_cost", "split_budget", "compare_venues", "budget_status")


def js_round(x):
    """JavaScript Math.round (halves go UP), not Python's banker's rounding: Math.round(12.5) = 13."""
    return int(math.floor(x + 0.5))


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _money(n):
    return "$" + format(int(round(n)), ",")


# ---------------------------------------------------------------- profile defaults

def profile_defaults(user):
    """What the tools use when an argument is left out: the couple's profile (auth/me)."""
    loc = (user or {}).get("Wedding_Location_Updated") or []
    loc = [loc] if isinstance(loc, str) else loc
    states = [x.strip() for l in loc if l for x in str(l).split(",") if x.strip() and x.strip() != "Not Sure"]
    return {"state": states[0] if states else "", "guests": int(_num((user or {}).get("Wedding_Guest_Count"))),
            "budget": int(_num((user or {}).get("Wedding_Budget"))),
            "peak_season": bool((user or {}).get("Peak_Season")), "major_city": bool((user or {}).get("Major_City"))}


# ---------------------------------------------------------------- a. estimate

def guest_lookup(guests):
    """(rounded, lookup) exactly as workflow 8a4f6c12: round to the nearest 5, ask table 3 for 5..300."""
    r = js_round(js_round(_num(guests)) / float(STEP)) * STEP
    return r, min(max(r, STEP), MAX_TABLE_GUESTS)


def estimate_cost(state, guests, major_city, peak_season, budget, *, state_rows, modifier_at):
    """modifier_at(lookup) -> table 3 Modifier (float) or None. state_rows = table 4 rows."""
    guests = js_round(_num(guests))
    budget = _num(budget)
    w = {"type": "cost_estimate", "v": V, "state": state or "", "guests": guests, "guest_lookup": None,
         "major_city": bool(major_city), "peak_season": bool(peak_season), "budget": int(budget) or None,
         "tiers": [], "verdict": None, "gap_pct": None, "note": None, "missing": []}
    if not state:
        w["missing"].append("state")
    if guests <= 0:
        w["missing"].append("guests")
    if w["missing"]:
        return {"widget": w, "facts": {"missing": w["missing"]}}
    row = next((r for r in state_rows or [] if r.get("State") == state), None)
    avg = _num((row or {}).get("Cost_of_Average_Wedding"))
    rounded, lookup = guest_lookup(guests)
    w["guest_lookup"] = lookup
    mod = modifier_at(lookup)
    if mod and rounded > MAX_TABLE_GUESTS:
        mod = js_round((mod + (rounded - MAX_TABLE_GUESTS) / float(STEP) * EXTRA_PER_STEP) * 100) / 100.0
        w["note"] = "Above 300 guests this is a rough extrapolation."
    if not avg:
        w["missing"].append("state_average")
    if not mod:
        w["missing"].append("guest_modifier")
    if w["missing"]:
        return {"widget": w, "facts": {"missing": w["missing"]}}
    # Same multiplication order as the tab, so the floats (and the rounding) come out identical.
    base = avg * mod * (PEAK if peak_season else 1) * ALWAYS * (MAJOR if major_city else 1)
    w["tiers"] = [{"label": lab, "multiplier": m, "amount": js_round(base * m)} for lab, m in TIERS]
    if budget > 0:
        gap = js_round(abs(budget - base) / base * 100)
        w["verdict"] = "under" if budget < base else "near" if gap == 0 else "over"
        w["gap_pct"] = gap
    facts = {"state": state, "guests": guests, "major_city": bool(major_city), "peak_season": bool(peak_season),
             **{t["label"].lower(): t["amount"] for t in w["tiers"]},
             "source": "Tulle's state average wedding cost x a guest-count factor (the Cost tools estimate)"}
    if w["verdict"]:
        facts.update(budget=int(budget), verdict=w["verdict"], gap_pct=w["gap_pct"])
    if w["note"]:
        facts["note"] = w["note"]
    return {"widget": w, "facts": facts}


# ---------------------------------------------------------------- b. split

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def split_budget(total, focus="normal", excluded_categories=(), *, categories):
    """categories = table 19 rows. excluded_categories: row ids (what the tab stores) or category names."""
    focus = (focus or "normal").strip().lower()
    focus = focus if focus in FOCUS_FIELDS else "normal"
    field = FOCUS_FIELDS[focus]
    total = _num(total)
    ex = {str(x) for x in excluded_categories or ()} | {_norm(x) for x in excluded_categories or ()}
    rows = sorted(categories or [], key=lambda r: -_num(r.get("Budget_Focus_Normal")))
    off = lambda r: str(r.get("id")) in ex or _norm(r.get("Category")) in ex
    active = sum(_num(r.get(field)) for r in rows if not off(r))
    cats = []
    for r in rows:
        inc = not off(r)
        share = _num(r.get(field))
        cats.append({"id": r.get("id"), "name": r.get("Category") or "", "included": inc,
                     "pct": js_round(share / active * 100) if inc and active > 0 else 0,
                     "amount": js_round(total * share / active) if inc and active > 0 else 0})
    w = {"type": "budget_split", "v": V, "total": int(total), "focus": focus, "categories": cats,
         "included_count": sum(1 for c in cats if c["included"]), "category_count": len(cats)}
    facts = {"total": int(total), "focus": focus,
             "categories": {c["name"]: {"pct": c["pct"], "amount": c["amount"]} for c in cats if c["included"]},
             "excluded": [c["name"] for c in cats if not c["included"]],
             "source": "Tulle's typical budget split for this focus, re-scaled over the included categories"}
    return {"widget": w, "facts": facts}


# ---------------------------------------------------------------- c. compare venues

def _line(e, item):
    return next((l for l in (e or {}).get("lines") or [] if l.get("item") == item), {})


def pdf_row(card, cost_inputs, guests):
    """One saved venue priced from its PDF with estimate.compute (the venue page's all-in), or None."""
    e = estimate.compute(cost_inputs, guests)
    if not e:
        return None
    return {"name": card.get("name") or "", "vendor_id": card.get("vendor_id"), "source": "pdf",
            "guests": e["guests"], "all_in": e["total_before_tax_and_gratuity"],
            "breakdown": {"rental": _line(e, "Rental").get("amount"), "ceremony": _line(e, "Ceremony on site").get("amount"),
                          "food": _line(e, "Food and drink").get("amount"),
                          "service_pct": _line(e, "Service charge").get("pct")},
            "estimated_lines": list(e.get("estimated_lines") or [])}


def quote_row(q):
    """One manual quote (Venue Calculator) -> a row, before tax and gratuity like the PDF rows. final_total is NOT
    used: it includes tax and tip, and is stale on some rows (seen 2026-10-25: 47,115 on a $8,850 quote)."""
    g = int(_num(q.get("guest_count")))
    food = _num(q.get("per_person_cost")) * g
    all_in = (_num(q.get("base_venue_fee")) + _num(q.get("ceremony_fee")) + _num(q.get("other_fees")) + food
              + _num(q.get("total_admin_cost")))
    if all_in <= 0:
        return None
    vid = (q.get("vendor_id") or "").strip() or None
    return {"name": (q.get("venue_name") or "").strip() or "Your quote", "vendor_id": vid, "source": "manual",
            "guests": g or None, "all_in": int(round(all_in)),
            "breakdown": {"rental": int(_num(q.get("base_venue_fee"))), "ceremony": int(_num(q.get("ceremony_fee"))),
                          "food": int(round(food)), "service_pct": _num(q.get("admin_fee")) or None},
            "estimated_lines": []}


def compare_venues(user, guests, *, priced, quotes, locked, budget=0, saved_venue_count=0):
    """priced = [(card, cost_inputs or None)] for the couple's saved VENUES (empty when locked - the caller must
    not even fetch PDF pricing for a free user); quotes = their Venue Calculator rows.
    locked = no active plan: manual quotes only (free mode shows no vendor PDF prices)."""
    rows, unpriced = [], []
    if not locked:
        for card, inp in priced or []:
            r = pdf_row(card, inp, guests) if inp else None
            if r:
                rows.append(r)
            else:
                unpriced.append({"name": card.get("name") or "", "vendor_id": card.get("vendor_id")})
    rows += [r for r in (quote_row(q) for q in quotes or []) if r]
    rows.sort(key=lambda r: r["all_in"])
    w = {"type": "venue_compare", "v": V, "guests": int(_num(guests)) or None, "budget": int(_num(budget)) or None,
         "locked": bool(locked), "reason": "unlock_pdf_pricing" if locked else None, "rows": rows,
         "unpriced": [] if locked else unpriced, "saved_venue_count": int(saved_venue_count or 0)}
    facts = {"guests": w["guests"], "rows": [{"name": r["name"], "source": r["source"], "guests": r["guests"],
                                              "all_in_before_tax_and_gratuity": r["all_in"],
                                              "estimated_lines": r["estimated_lines"]} for r in rows],
             "note": "pdf rows = the venue page's all-in from its pricing PDF; manual rows = the couple's own quotes."}
    if locked:
        facts["locked"] = "PDF pricing of their saved venues needs a Tulle plan - only their own quotes are shown."
    if unpriced and not locked:
        facts["no_pdf_breakdown"] = [u["name"] for u in unpriced]
    if budget:
        facts["budget"] = int(_num(budget))
    return {"widget": w, "facts": facts}


# ---------------------------------------------------------------- d. budget status

def is_example(p):
    """The two rows every account is seeded with ("Example 1: Venue", "Example 2: Photography")."""
    return str(p.get("vendor_name") or "").strip().lower().startswith("example")


def budget_status(user, *, packages):
    """Selected packages that aren't the seeded examples, against the couple's Wedding_Budget. owed = total -
    paid (Balance_Left is stale on most rows, 2026-10-19 note)."""
    budget = int(_num((user or {}).get("Wedding_Budget")))
    examples = [p for p in packages or [] if is_example(p)]
    rows = [p for p in packages or [] if not is_example(p) and p.get("Selected") is True]
    by = {}
    for p in rows:
        c = (p.get("Category") or "Other").strip() or "Other"
        b = by.setdefault(c, {"category": c, "added": 0, "paid": 0, "owed": 0})
        t, paid = int(_num(p.get("total_cost"))), int(_num(p.get("Balance_Paid")))
        b["added"] += t
        b["paid"] += paid
        b["owed"] += max(0, t - paid)
    added = sum(b["added"] for b in by.values())
    paid = sum(b["paid"] for b in by.values())
    owed = sum(b["owed"] for b in by.values())
    w = {"type": "budget_status", "v": V, "budget": budget or None, "added": added, "paid": paid, "owed": owed,
         "remaining": (budget - added) if budget else None,
         "pct_of_budget": js_round(100.0 * added / budget) if budget else None,
         "item_count": len(rows), "examples_excluded": len(examples),
         "by_category": sorted(by.values(), key=lambda b: -b["added"])}
    facts = {k: w[k] for k in ("budget", "added", "paid", "owed", "remaining", "pct_of_budget", "item_count")}
    facts["by_category"] = {b["category"]: {"added": b["added"], "paid": b["paid"], "owed": b["owed"]}
                            for b in w["by_category"]}
    facts["source"] = "the couple's own Budget tab (selected items, the seeded examples left out)"
    return {"widget": w, "facts": facts}


# ---------------------------------------------------------------- one line of text per widget (quick actions)

def summary_text(widget):
    """The assistant line stored with a quick-action widget (no model): it is what later turns of the chat read."""
    t = widget.get("type")
    if t == "cost_estimate":
        if widget.get("missing"):
            need = {"state": "your wedding state", "guests": "your guest count"}
            asks = [need[m] for m in widget["missing"] if m in need]
            return ("Add %s in Edit details and I can estimate your wedding cost." % " and ".join(asks) if asks else
                    "I couldn't calculate an estimate right now - please try again.")
        tiers = {x["label"]: x["amount"] for x in widget["tiers"]}
        s = "A typical wedding in %s for %d guests comes to about %s (Minimalist %s, Luxury %s)." % (
            widget["state"], widget["guests"], _money(tiers["Average"]), _money(tiers["Minimalist"]), _money(tiers["Luxury"]))
        if widget.get("verdict") == "under":
            s += " Your %s budget is %d%% under that." % (_money(widget["budget"]), widget["gap_pct"])
        elif widget.get("verdict") == "over":
            s += " Your %s budget is %d%% over that." % (_money(widget["budget"]), widget["gap_pct"])
        elif widget.get("verdict") == "near":
            s += " Your %s budget is right at it." % _money(widget["budget"])
        return s
    if t == "budget_split":
        if not widget.get("total"):
            return "Add your total budget in Edit details and I can split it across categories."
        top = [c for c in widget["categories"] if c["included"]][:2]
        return "Here's how %s splits across %d categories%s." % (
            _money(widget["total"]), widget["included_count"],
            " - " + " and ".join("%s %s" % (c["name"], _money(c["amount"])) for c in top) if top else "")
    if t == "venue_compare":
        rows = widget.get("rows") or []
        if not rows:
            return ("Save venues with the heart (or add a quote in the venue calculator) and I'll compare them here."
                    if not widget.get("locked") else
                    "Add a venue quote and I'll compare it here. Pricing from your saved venues' PDFs comes with a Tulle plan.")
        s = "%d venue%s compared, lowest first: %s at about %s before tax and gratuity." % (
            len(rows), "" if len(rows) == 1 else "s", rows[0]["name"], _money(rows[0]["all_in"]))
        if widget.get("locked"):
            s += " These are your own quotes - pricing from your saved venues' PDFs comes with a Tulle plan."
        return s
    if t == "budget_status":
        if not widget.get("item_count"):
            return "Your budget has no items yet - add a vendor package and I'll keep track of it here."
        s = "You've added %s across %d item%s: %s paid, %s still owed." % (
            _money(widget["added"]), widget["item_count"], "" if widget["item_count"] == 1 else "s",
            _money(widget["paid"]), _money(widget["owed"]))
        if widget.get("budget"):
            s += " That's %d%% of your %s budget." % (widget["pct_of_budget"], _money(widget["budget"]))
        return s
    return ""


# ---------------------------------------------------------------- run one tool (shared by /rec/tool and the agent)

def run_tool(name, args, token, user, *, access, xano):
    """Fetch what the tool needs (read-only GETs with the user's token) and compute it. args override the
    profile defaults. access = any active plan (has_paid_access); False -> compare is locked."""
    if name not in TOOLS:
        raise ValueError("unknown tool")
    a = dict(args or {})
    d = profile_defaults(user)
    def pick(k):   # None / "" / a numeric 0 = "use the profile"; an explicit False (no peak season) is kept
        v = a.get(k)
        empty = v is None or v == "" or (isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0)
        return d[k] if empty else v
    if name == "estimate_cost":
        return estimate_cost(pick("state"), pick("guests"), pick("major_city"), pick("peak_season"), pick("budget"),
                             state_rows=xano.state_average_rows(token),
                             modifier_at=lambda n: xano.guest_modifier(token, n))
    if name == "split_budget":
        total = a.get("total") or d["budget"]
        return split_budget(total, a.get("focus") or "normal", a.get("excluded_categories") or [],
                            categories=xano.budget_categories(token))
    if name == "budget_status":
        return budget_status(user, packages=xano.user_packages(token, user["id"]))
    guests = int(_num(a.get("guests"))) or d["guests"]
    quotes = xano.venue_quotes(token, (user or {}).get("email"))
    with ThreadPoolExecutor(max_workers=8) as pool:   # one table-11 read + one ep230 call per saved vendor
        cards = [c for c in pool.map(lambda v: _safe(xano.vendor_card, v), xano.saved_vendor_ids(user)) if c]
        venues = [c for c in cards if (c.get("category") or "Venue") not in xano.PE_CATEGORIES]
        priced = []
        if access:   # free users: never fetch PDF pricing at all (free mode shows no vendor prices)
            pays = pool.map(lambda c: _safe(xano.venue_pricing, token, c["vendor_id"], True), venues)
            priced = [(c, (p or {}).get("cost_inputs")) for c, p in zip(venues, pays)]
    return compare_venues(user, guests, priced=priced, quotes=quotes, locked=not access, budget=d["budget"],
                          saved_venue_count=len(venues))


def _safe(f, *a):
    try:
        return f(*a)
    except Exception:
        return None
