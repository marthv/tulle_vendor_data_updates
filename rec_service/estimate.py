"""All-in venue cost at the couple's guest count, computed in CODE - never by the model.

Why (2026-10-13, user 1437, 3/5 "Want accurate costs"): the model did its own arithmetic and quoted per-venue
market figures that came from different benchmark groups. Each venue's pricing panel (ep230) compares it with
venues of ITS OWN capacity band, so Farmington Gardens (seats 342) said "Connecticut average about $205 a head"
and Wadsworth Mansion (seats 200) said "$146 a head" in the same chat. Both are real medians (CT cap_251_400 vs
cap_151_250), both were labelled "Connecticut".

The total is the venue page's own formula, so the assistant and the page always agree. Verified against
ep230's all_in.at_default on 7 live venues (tests/test_estimate.py):
    subtotal = rental + ceremony + max(food per guest x guests, food & drink minimum)
    total    = subtotal x (1 + service charge %)          <- "before tax and gratuity", like the page
A line the venue's PDF doesn't give is filled with the panel's own estimate (all_in.estimates) and marked
estimate. Tax is shown separately, only when the venue states its rate.
"""

import config

DEFAULT_SERVICE_PCT = 20


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def inputs_from_panel(d):
    """The cost inputs of one ep230 payload's selected space, or None (gated / not a venue / no space)."""
    sp = (d or {}).get("space") or {}
    comps = {c.get("key"): c for c in sp.get("components") or []}
    all_in = sp.get("all_in") or {}
    if not comps or all_in.get("gated"):
        return None
    food = comps.get("food_and_drink") or {}
    return {
        "space": sp.get("label") or "",
        "guest_floor": int(sp.get("guest_floor") or 0),
        "rental": _num((comps.get("venue_fee") or {}).get("amount")),
        "ceremony": _num((comps.get("ceremony") or {}).get("amount")),
        "pp_fb": _num(food.get("pp_fb")) if food.get("source") == "venue" else None,
        "fb_min": _num(food.get("fb_min")) if food.get("source") == "venue" else None,
        "fb_min_per_person": bool(food.get("fb_min_is_per_person")),
        # fn56 v26: the rental is a package price that already covers food and drink for
        # `included_guests`; pp_fb is then the extra-guest rate. Costs the package does not
        # state come back with source 'package' and must add nothing (no market stand-in).
        # Read whatever the payload says; compute() only USES these when config.PACKAGE_TOTALS is on, so a payload
        # cached before a flag flip still prices the new way (and flag off = the old total exactly).
        "fee_includes_fb": bool(food.get("fee_includes_fb")),
        "included_guests": int(food.get("included_guests") or 0),
        # fn56 v29 keeps the extra-guest rate on the food line even when its source is 'market' (no F&B rows of its
        # own, e.g. Celestine's evening space): the card's pkgFood uses it, so we must too.
        "pkg_extra_pp": _num(food.get("pp_fb")) if food.get("fee_includes_fb") else None,
        # fn56: ceremony fee_type 'included' = $0 from the PDF (the card adds nothing; _num(0) used to fall back to
        # the market ceremony estimate).
        "ceremony_included": (comps.get("ceremony") or {}).get("fee_type") == "included",
        "ceremony_per_person": (comps.get("ceremony") or {}).get("fee_type") == "per_person",
        "ceremony_in_package": (comps.get("ceremony") or {}).get("source") == "package",
        "service_in_package": (comps.get("service_charge") or {}).get("source") == "package",
        "service_pct": _num((comps.get("service_charge") or {}).get("pct")),
        "tax_pct": _num((comps.get("tax") or {}).get("pct")),
        "estimates": all_in.get("estimates") or {},
    }


def _money(n):
    return "$" + format(int(round(n)), ",")


def compute(inp, guests, override=None):
    """inp: inputs_from_panel(); guests: the couple's count (0 = the panel's default 100).
    override: replaces venue figures (off-peak prices), keys rental / pp_fb / fb_min.
    Returns the itemized estimate, or None without inputs."""
    if not inp:
        return None
    inp = dict(inp, **{k: v for k, v in (override or {}).items() if v})
    est = inp.get("estimates") or {}
    asked = int(guests or 0) or 100
    g = max(asked, inp.get("guest_floor") or 0)
    lines = []

    def pick(own, est_key, fallback=None):
        if own:
            return own, "this venue's PDF"
        e = _num(est.get(est_key)) or fallback
        return (e, "estimate") if e else (0, "not listed")

    rental, rs = pick(inp.get("rental"), "venue_fee")
    lines.append({"item": "Rental", "amount": rental, "source": rs})
    pkg = config.PACKAGE_TOTALS   # REC_PACKAGE_TOTALS (2026-11-22): off = exactly the pre-package formula
    if pkg and inp.get("ceremony_included") and not inp.get("ceremony"):
        ceremony, cs = 0, "this venue's PDF"
    elif pkg and inp.get("ceremony_in_package") and not inp.get("ceremony"):
        ceremony, cs = 0, "not stated - package"
    else:
        ceremony, cs = pick(inp.get("ceremony"), "ceremony")
    cline = {"item": "Ceremony on site", "amount": ceremony, "source": cs}
    if config.CEREMONY_PER_PERSON and inp.get("ceremony_per_person") and inp.get("ceremony"):
        ceremony = inp["ceremony"] * g
        cline.update(amount=ceremony, per_guest=inp["ceremony"], math="%s x %d guests = %s" % (_money(inp["ceremony"]), g, _money(ceremony)))
    lines.append(cline)

    if pkg and inp.get("fee_includes_fb"):
        # Inclusive package: food is inside the rental. Never add a per-head estimate on top.
        pp = inp.get("pp_fb") or inp.get("pkg_extra_pp") or 0
        n = inp.get("included_guests") or 0
        if pp and n:
            food = pp * max(0, g - n)
            math_s = "in the package for %d guests; %d extra x %s = %s" % (n, max(0, g - n), _money(pp), _money(food))
        elif pp:
            food = max(0, pp * g - rental)
            math_s = "package covers up to %s; %s x %d guests above that = %s" % (_money(rental), _money(pp), g, _money(food))
        else:
            food = 0
            math_s = "included in the package price"
        lines.append({"item": "Food and drink", "amount": food, "source": "this venue's PDF",
                      "included_in_rental": True, "math": math_s})
        return _finish(inp, lines, rental, ceremony, food, g, asked)

    pp, ps = pick(inp.get("pp_fb"), "pp_fb")
    fb_min = inp.get("fb_min") or 0
    min_only = bool(config.MIN_FOOD_ONLY and fb_min and not inp.get("pp_fb"))
    if min_only:
        pp, ps = 0, "this venue's PDF"
    food = pp * g
    food_line = {"item": "Food and drink", "per_guest": pp, "guests": g, "amount": food, "source": ps,
                 "math": "%s x %d guests = %s" % (_money(pp), g, _money(food))}
    if fb_min:
        floor = fb_min * g if inp.get("fb_min_per_person") else fb_min
        food_line["minimum"] = floor
        if floor > food:
            food = floor
            food_line.update(amount=floor, minimum_applies=True,
                             math=("%s minimum; food may cost more" % _money(floor)) if min_only else
                             "%s minimum (above %s x %d guests)" % (_money(floor), _money(pp), g))
            if min_only:
                food_line["minimum_only"] = True
    lines.append(food_line)
    return _finish(inp, lines, rental, ceremony, food, g, asked)


def _finish(inp, lines, rental, ceremony, food, g, asked):
    est = inp.get("estimates") or {}
    subtotal = rental + ceremony + food
    pct = inp.get("service_pct")
    psrc = "this venue's PDF"
    if not pct and config.PACKAGE_TOTALS and inp.get("service_in_package"):
        pct, psrc = 0, "not stated - package"
    elif not pct:
        pct = _num(est.get("service_pct")) or DEFAULT_SERVICE_PCT
        psrc = "estimate"
    service = subtotal * pct / 100.0
    lines.append({"item": "Service charge", "pct": pct, "amount": round(service), "source": psrc,
                  "math": "%s%% of %s = %s" % (_fmt_pct(pct), _money(subtotal), _money(service))})
    total = round(subtotal + service)
    out = {"space": inp.get("space"), "guests": g, "total_before_tax_and_gratuity": total,
           "per_guest": round(total / g), "lines": lines,
           "from_venue": sum(1 for l in lines if l["source"] == "this venue's PDF"),
           "estimated_lines": [l["item"] for l in lines if l["source"] == "estimate"],
           "note": "Same formula as this venue's page. Gratuity (often 15-20%) is not included."}
    if g != asked:
        out["priced_at_guest_minimum"] = "This space's minimum is %d guests, so it is priced at %d." % (g, g)
    tax = inp.get("tax_pct")
    if tax:
        out["tax"] = {"pct": tax, "amount": round(total * tax / 100.0), "source": "this venue's PDF"}
        out["total_with_tax"] = round(total * (1 + tax / 100.0))
    else:
        out["tax"] = {"source": "not stated by this venue - added on top"}
    return out


def _fmt_pct(p):
    return ("%g" % p)


def label(e, budget=0):
    """One line for a card / reason: 'About $37,300 for 160 guests before tax and gratuity'."""
    if not e:
        return None
    s = "About %s for %d guests before tax and gratuity" % (_money(e["total_before_tax_and_gratuity"]), e["guests"])
    if any(l.get("minimum_only") for l in e.get("lines") or []):
        s += ", with food at its minimum spend (it may cost more)"
    if budget:
        s += " (%d%% of your budget)" % round(100.0 * e["total_before_tax_and_gratuity"] / budget)
    return s


def summary(e, budget=0):
    """The short version a search row carries (the model quotes these figures, it doesn't compute them)."""
    if not e:
        return None
    out = {"total_before_tax_and_gratuity": e["total_before_tax_and_gratuity"], "per_guest": e["per_guest"],
           "guests": e["guests"], "estimated_lines": e["estimated_lines"]}
    if any(l.get("minimum_only") for l in e.get("lines") or []):
        out["food_is_only_the_minimum"] = "The PDF gives a food minimum and no per-guest price: say food may cost more."
    if budget:
        out["share_of_budget_pct"] = round(100.0 * e["total_before_tax_and_gratuity"] / budget)
    if e.get("priced_at_guest_minimum"):
        out["priced_at_guest_minimum"] = e["priced_at_guest_minimum"]
    if e.get("total_with_tax"):
        out["total_with_tax"] = e["total_with_tax"]
    return out


# ---------------------------------------------------------------- off-peak (cheapest Saturday) from table 36

def off_peak_override(details, space_label):
    """Lowest-Saturday prices for the panel's space from venue_details() spaces, or None if the PDF lists none.
    Matches the space by name; falls back to the only space when the venue has one."""
    spaces = (details or {}).get("spaces") or []
    lab = (space_label or "").strip().lower()
    sp = next((s for s in spaces if (s.get("space") or "").strip().lower() == lab), None)
    if sp is None and len(spaces) == 1:
        sp = spaces[0]
    if not sp:
        return None
    o = {"rental": _num(sp.get("Venue_Fee_on_Lowest_Saturday")),
         "pp_fb": _num(sp.get("Per_Person_Food_and_Beverage_on_Lowest_Saturday")),
         "fb_min": _num(sp.get("Food_and_Beverage_Min_on_Lowest_Saturday"))}
    if not any(o.values()):
        return None
    o["months"] = sp.get("Months__Lowest_Pricing")
    return o
