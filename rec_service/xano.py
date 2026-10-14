"""Everything that talks to Xano. Read-only against app data; the only write is one rec_usage row
per request. Data calls go out WITH THE USER'S OWN TOKEN, so Xano's existing auth and paywall
rules apply unchanged - the service can never see more than the user could in the app."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import re

import requests

import config
import estimate
import prefs

TIMEOUT = 20


class XanoError(Exception):
    pass


def _get(url, token=None, params=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    if r.status_code == 401:
        raise XanoError("unauthorized")
    if not r.ok:
        raise XanoError("%s %s" % (r.status_code, r.text[:200]))
    return r.json()


def _meta(method, path, **kw):
    if not config.XANO_METADATA_TOKEN:
        raise XanoError("XANO_METADATA_TOKEN not set")
    r = requests.request(method, config.XANO_META + path, timeout=TIMEOUT,
                         headers={"Authorization": "Bearer " + config.XANO_METADATA_TOKEN}, **kw)
    if not r.ok:
        raise XanoError("meta %s %s: %s %s" % (method, path, r.status_code, r.text[:200]))
    return r.json() if r.text else None


# ---------------------------------------------------------------- auth + entitlement

def verify_user(token):
    """The user's own Xano auth token -> their user row, or None. This is the ONLY identity check."""
    if not token:
        return None
    try:
        return _get(config.API_AUTH_GROUP + "/auth/me", token)
    except XanoError:
        return None


def _as_dt(v):
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, dt.timezone.utc)
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    # date_until_access is a Xano DATE ("2026-10-20"): naive. Comparing it with an aware now() raised
    # TypeError (found 2026-10-08 in tests). A bare date is read at FACE VALUE (00:00 UTC that day), like
    # ep230 and the WeWeb gates: the free-PDF-limit handler writes TODAY into date_until_access to expire a
    # free user, so "end of day" would hand them the full assistant until midnight. Cost (accepted app-wide
    # since 2026-08-16): a buyer's access ends at the start of their last day.
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def has_paid_access(user):
    """Entitlement model (memory reference_access_entitlement_model): forever_access_purchased OR
    date_until_access in the future. Read from auth/me, which reads live."""
    if user.get("forever_access_purchased"):
        return True
    until = _as_dt(user.get("date_until_access"))
    return bool(until and until > dt.datetime.now(dt.timezone.utc))


def has_forever(user):
    """User decision 2026-10-03: the Planning Assistant is UNLIMITED only on Forever. Free users and
    1-week / 4-week buyers get the opening picks + 3 questions for life, then a Forever upsell.
    (1w/4w buyers still get exact prices in answers - has_paid_access - since they can open PDFs.)"""
    return bool(user.get("forever_access_purchased"))


def beta_member(user):
    """Full assistant: Forever always; any active paid plan when REC_BETA_AUDIENCE=paid (2026-10-08)."""
    if has_forever(user):
        return True
    return config.BETA_AUDIENCE == "paid" and has_paid_access(user)


def plan_label(user):
    """'Forever', or the Type of the user's latest payment-log row (table 16, e.g. '4 weeks', '1 week'),
    so beta feedback says who it came from. 'free' without access; 'unknown' if the log can't be read."""
    if has_forever(user):
        return "Forever"
    if not has_paid_access(user):
        return "free"
    try:
        uid = str(user.get("id"))
        rows = [r for r in _search_all(16, [{"Client_Reference_ID": uid}])
                if str(r.get("Client_Reference_ID") or "").strip() == uid]
        if rows:
            return str(max(rows, key=lambda r: r.get("Time_of_Payment") or 0).get("Type") or "unknown").strip()
    except Exception:
        pass
    return "unknown"


PROFILE_FIELDS = ["first_name", "Planning_Phase", "Wedding_Location_Updated", "Wedding_Guest_Count",
                  "Wedding_Budget", "Wedding_Date", "Peak_Season", "Major_City", "Age_Range",
                  "wedding_vibes", "venue_customization"]


def profile_of(user):
    return {k: user.get(k) for k in PROFILE_FIELDS if user.get(k) not in (None, "", [], 0)}


# ---------------------------------------------------------------- usage log (table 79)

def today():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _search_all(table_id, search):
    """search = list of single-key dicts, ANDed. MEASURED 2026-10-03: the metadata search honours
    [{"a": 1}, {"b": 2}] and {"a": 1}, but a dict with TWO keys is silently ignored and returns the
    WHOLE table, and a value containing '|' is parsed as an operator (400). Callers still re-filter
    rows in code, so an ignored filter can never leak other users' rows into a limit check."""
    rows, page = [], 1
    while page <= 50:
        d = _meta("POST", "/table/%d/content/search" % table_id,
                  json={"search": search, "page": page, "per_page": 500})
        items = (d or {}).get("items", [])
        rows += items
        if not d or not d.get("nextPage"):
            break
        page += 1
    return rows


def user_usage(user_id):
    """All of this user's rec_usage rows (small: a few per user)."""
    uid = int(user_id)
    return [r for r in _search_all(config.USAGE_TABLE_ID, [{"user_id": uid}]) if int(r.get("user_id") or 0) == uid]


def spend_today_usd():
    return spend_today()[0]


def spend_today():
    """(all spend today, free users' spend today) in USD, from one read of today's rec_usage rows.
    Free = tier "free" (dropped by Xano until table 79 gets a tier column) or counted_as_free."""
    d = today()
    rows = [r for r in _search_all(config.USAGE_TABLE_ID, [{"usage_day": d}]) if r.get("usage_day") == d]
    total = sum(float(r.get("cost_usd") or 0) for r in rows)
    free = sum(float(r.get("cost_usd") or 0) for r in rows if r.get("tier") == "free" or r.get("counted_as_free"))
    return total, free


def log_usage(row):
    row = dict(row, usage_day=today())
    _meta("POST", "/table/%d/content" % config.USAGE_TABLE_ID, json=row)


# ---------------------------------------------------------------- data the tools read

# Exact vocabularies of the denormalized tag columns ep119 filters on (measured 2026-10-03 over 12,323
# validated venues). venue_types -> flt_venue_types (96% filled), vibes -> flt_venue_attributes (85%),
# pricing_models -> flt_venue_offerings (96%). Values must match EXACTLY or the overlap matches nothing.
VENUE_TYPES = ["Dedicated Event Venue", "Hotel / Resort", "Estate / Mansion", "Barn / Ranch", "Restaurant / Bar",
               "Country Club / Private Club", "Winery / Brewery / Distillery", "Museum / Gallery", "Civic / Public",
               "Garden / Botanical Garden", "Performing Arts Venue", "Religious", "University / College",
               "Zoo / Aquarium"]   # same 14 as Discovery's Venue Type filter (variable e7c238b6/11c6a903)
VIBES = ["Scenic / Nature Views", "Natural Light / Large Windows", "Historic Architecture", "Ballroom",
         "Tall / Vaulted Ceilings", "Waterfront", "Tented", "Rooftop / Skyline Views", "Industrial / Warehouse",
         "Greenhouse"]
PRICING_MODELS = ["All-Inclusive", "Semi-Inclusive", "Raw Space"]


def search_venues(token, *, states, page_size=8, **filters):
    """Several states -> one ep119 search PER STATE, results interleaved. MEASURED 2026-10-04: a single
    multi-state search sorted by popularity hands every slot to the most-clicked state (Georgia+Illinois
    top 12 = 12 Illinois; Louisiana+California+Florida top 12 = 0 Louisiana), so couples comparing states
    got picks from one of them and the model told them they hadn't picked a state."""
    return _interleave(_search_one, token, states, page_size, "venues", **filters)


def _interleave(search_one, token, states, page_size, key, **filters):
    """One search per state, results interleaved round-robin (see search_venues for why)."""
    states = [s for s in (states or []) if s]
    if len(states) <= 1:
        return search_one(token, states=states, page_size=page_size, **filters)
    per = max(3, -(-int(page_size) // len(states)))
    def one(s):   # one state failing (Xano blip) must not sink the others
        try:
            return search_one(token, states=[s], page_size=per, **filters)
        except Exception as e:
            return e
    with ThreadPoolExecutor(max_workers=len(states)) as ex:
        got = list(ex.map(one, states))
    if all(isinstance(r, Exception) for r in got):
        raise got[0]
    states, results = zip(*[(s, r) for s, r in zip(states, got) if not isinstance(r, Exception)])
    venues, seen = [], set()
    for i in range(per):
        for r in results:
            if i < len(r[key]) and r[key][i].get("vendor_id") not in seen:
                seen.add(r[key][i].get("vendor_id"))
                venues.append(r[key][i])
    out = {"total_matches": sum(r["total_matches"] or 0 for r in results),
           "matches_by_state": {s: r["total_matches"] for s, r in zip(states, results)}, key: venues}
    for extra in ("keyword_matches",):
        if any(extra in r for r in results):
            out[extra] = sum(r.get(extra) or 0 for r in results)
    return out


def _search_one(token, *, states, guests=0, max_venue_fee=0, max_food_per_person=0,
                venue_types=None, vibes=None, pricing_models=None, keyword="", sort_by="popular_desc",
                outdoor_ceremony=False, page_size=8):
    """ep119 - the same search the Vendor Discovery grid uses. `states` match the multi-value State
    field server-side (never equality). max_capacity means 'seats AT LEAST N'."""
    params = {"Category_Input": "Venue", "page": 1, "page_size": max(1, min(int(page_size), 20)),
              "states[]": list(states or []), "State_Input": (states or [""])[0],
              "max_capacity": int(guests or 0), "capacity_ceiling": 10000,
              "base_fee_max": int(max_venue_fee or 0), "fb_per_person_max": int(max_food_per_person or 0),
              "Search_Input": keyword or "", "sort_by": sort_by or "", "fallback_all": "false"}
    vt = [v for v in (venue_types or []) if v in VENUE_TYPES]
    vb = [v for v in (vibes or []) if v in VIBES]
    pm = [v for v in (pricing_models or []) if v in PRICING_MODELS]
    if vt:
        params["venue_types[]"] = vt
    if vb:
        params["vibes[]"] = vb
    if pm:
        params["pricing_models[]"] = pm
    if outdoor_ceremony:
        params["outdoor_ceremony"] = "yes"   # ep119 -> flt_outdoor_ceremony == true (from table 36 Outside_Ceremony_Space)
    d = _get(config.API_SEARCH_GROUP + "/wptp_updated_mappings_search", token, params)
    out = []
    for it in d.get("items", []):
        out.append({
            "vendor_id": it.get("Vendor_ID"), "vendor_idx": it.get("id"),  # page route needs both
            "name": it.get("Name"), "state": it.get("State"),
            "address": it.get("Address"), "venue_type": it.get("Venue_Type"),
            "max_capacity_seated": it.get("Max_Capacity_Seated"),
            "venue_fee_range": [it.get("flt_min_venue_fee"), it.get("flt_max_venue_fee")],
            # lowest peak-Saturday food & drink minimum across the venue's spaces; 0 = none OR not known
            "food_minimum": it.get("min_fb_min_peak_sat") or 0,
            "at_a_glance": it.get("flt_glance"), "image": it.get("image_1"),
            "description": (it.get("Description") or "")[:300],
        })
    return {"total_matches": d.get("itemsTotal"), "venues": out}


def venue_pricing(token, vendor_id, paid):
    """Pricing Intelligence panel payload. Paid users get ep230 (exact figures); everyone else gets
    ep231 (ranges only). Xano decides what is in the payload - the paywall is not ours to enforce here."""
    path = "/venue/pricing_intelligence_full" if paid else "/venue/pricing_intelligence"
    d = _get(config.API_SEARCH_GROUP + path, token if paid else None, {"vendor_id": vendor_id})
    sp = d.get("space") or {}
    rows = [{"line": c.get("title"), "detail": c.get("sentence"), "source": c.get("source"),
             "vs_market": c.get("badge_label"), "amount": c.get("amount"), "pct": c.get("pct")}
            for c in sp.get("components", []) if c.get("key") != "total"]
    band = sp.get("band") or {}
    return {"vendor_id": vendor_id, "headline": sp.get("headline_prefix"), "guest_minimum": sp.get("guest_floor"),
            "verdict": band.get("sentence"), "lines": rows, "source_note": sp.get("footer"),
            # raw figures for estimate.compute (paid payload only; ep231 has ranges, not these)
            "cost_inputs": estimate.inputs_from_panel(d) if paid else None}


# table 63 capacity bands (venue seated capacity). The pricing panel compares each venue with ITS OWN band, so
# two venues in one chat quoted different "Connecticut averages" ($205 vs $146 a head, user 1437, 2026-10-13).
# The assistant states ONE set of typical figures per state: the band a wedding of the couple's size books.
CAPACITY_BANDS = (("cap_0_75", 0, 75), ("cap_76_150", 76, 150), ("cap_151_250", 151, 250),
                  ("cap_251_400", 251, 400), ("cap_401_plus", 401, 10 ** 6))
MIN_BAND_SPACES = 8     # a band metric from fewer spaces than this falls back to the whole state


def capacity_band(guests):
    g = int(guests or 0)
    if g <= 0:
        return None
    return next((b for b in CAPACITY_BANDS if b[1] <= g <= b[2]), None)


def band_label(state, band):
    if not band:
        return "all %s venues" % state
    lo, hi = band[1], band[2]
    size = "up to %d" % hi if lo == 0 else "%d or more" % lo if hi >= 10 ** 6 else "%d-%d" % (lo, hi)
    return "%s venues that seat %s" % (state, size)


def market_benchmarks(state, guests=0):
    """table 63: what venues cost in a state (per-guest all-in at 75/125/200, fees, F&B). Medians and
    quartiles only - never the mean (long right tail). With guests: the capacity band a wedding that size
    books (each metric falls back to the whole state when the band has under MIN_BAND_SPACES spaces)."""
    band = capacity_band(guests)
    rows = _search_all(config.BENCHMARK_TABLE_ID, [{"State": state}])
    overall, banded = {}, {}
    for r in rows:
        if r.get("State") != state:
            continue
        m = {"p25": r.get("p25"), "median": r.get("median"), "p75": r.get("p75"),
             "n_spaces": r.get("n_spaces"), "n_vendors": r.get("n_vendors")}
        if r.get("Dimension") == "overall" and r.get("Bucket") == "all":
            overall[r["Metric"]] = m
        elif band and r.get("Dimension") == "capacity_band" and r.get("Bucket") == band[0]:
            banded[r["Metric"]] = m
    out, scope = {}, {}
    for k, m in overall.items():
        b = banded.get(k)
        use_band = bool(b and (b.get("n_spaces") or 0) >= MIN_BAND_SPACES)
        out[k] = dict(b if use_band else m, scope=band_label(state, band if use_band else None))
    return {"state": state, "metrics": out,
            "note": "all_in_pg_N = all-in cost per guest at N guests, before tax and gratuity. Each metric's "
                    "scope says which venues it covers - quote it with that scope."}


# ---------------------------------------------------------------- photographers + entertainment (ep119 P/E path)
# ep119 routes its venue params to a PACKAGE pre-pass on table 36 when Category_Input is Photography or
# Entertainment (memory project_package_grain_filters): base_fee_max -> Package_Price, venue_types[] ->
# Package_Type (photo) / Service_Type (ent), pricing_models[] -> Timing_List (photo hour bands + Engagement/
# Elopement), vibes[] -> Offers_List. Vocab below = ep119's own facet keys, read live 2026-10-11 (CA photo,
# NY ent). Values must match EXACTLY or the overlap matches nothing.
PE_CATEGORIES = ("Photography", "Entertainment")
PHOTO_TYPES = ["Wedding Day", "Engagement", "Elopement", "Other"]
PHOTO_HOUR_BANDS = ["6 Hours or Less", "7-10 Hours", "11+ Hours"]
PHOTO_OFFERS = ["Full Gallery Rights", "Prints", "Engagement Session", "Second Shooter", "Album", "Videography",
                "Drone", "Rehearsal Dinner", "Content Creation"]
ENT_TYPES = ["DJ", "Live Band", "Ensemble", "Solo Performer", "Other Entertainment"]
ENT_OFFERS = ["Sound & Lighting Provided", "Ceremony Sound", "Photo Booth", "Monogram / Projection", "Special Effects"]
_TYPE_ALIAS = {"other": "Other Entertainment", "band": "Live Band", "soloist": "Solo Performer",
               "solo": "Solo Performer", "dj": "DJ"}

PE_FETCH = 20          # ep119 page_size cap; keyword ranking happens over these
_KW_SKIP = {"the", "and", "for", "with", "wedding", "music", "musician", "player", "our", "ceremony", "reception"}


def _pe_vocab(category):
    if category == "Photography":
        return PHOTO_TYPES, PHOTO_OFFERS, PHOTO_HOUR_BANDS
    return ENT_TYPES, ENT_OFFERS, []


def _clean_types(category, types):
    allowed, _, _ = _pe_vocab(category)
    out = []
    for t in types or []:
        t = t if t in allowed else _TYPE_ALIAS.get(str(t).strip().lower(), "")
        if t in allowed and t not in out:
            out.append(t)
    return out


def _stems(keyword):
    """'pianist' -> 'pian' (matches piano/pianist), 'harpist' -> 'harp', 'string quartet' -> string, quartet.
    ep119's Search_Input does NOT search packages or blurbs (measured 2026-10-11: NY Solo Performer +
    'piano' -> 0 results, while 7 of the top 20 Solo Performers list a piano package), so keywords are
    matched HERE, over name + blurb + description + package names."""
    out = []
    for w in re.findall(r"[a-z]+", (keyword or "").lower()):
        if len(w) < 3 or w in _KW_SKIP:
            continue
        if len(w) > 5:
            w = re.sub(r"(?:ists?|s)$", "", w)
        if w not in out:
            out.append(w)
    return out


def _kw_text(it):
    return " ".join([it.get("Name") or "", it.get("vendor_blurb") or "", it.get("pdf_description") or "",
                     it.get("Type_of_Entertainment") or "", it.get("Type_of_Photography") or ""]
                    + [p.get("name") or "" for p in it.get("matched_packages") or []])


def _kw_hit(text, stems):
    return bool(stems) and any(re.search(r"\b" + re.escape(st), text or "", re.I) for st in stems)


def _kw_score(text, stems):
    """How many of the keyword's words a text mentions ('String Quartet' beats 'String Trio' for "string quartet")."""
    return sum(1 for st in stems or [] if re.search(r"\b" + re.escape(st), text or "", re.I))


def _from_price(packages, stems, matched_min):
    """The ONE starting price the card shows and the model is told to quote. With a keyword it is the cheapest
    package that matches it (2026-10-11 eval pe02: a pianist card said "From $500" - a violin package - while
    the reply quoted the $800 piano package; same class of bug as the Village Big Sur card, 2026-10-04)."""
    if stems:
        best = max((_kw_score(p.get("name") or "", stems) for p in packages), default=0)
        priced = [p["price"] for p in packages if p.get("price") and _kw_score(p.get("name") or "", stems) == best]
        if best and priced:
            return min(priced)
    return matched_min or None


def _pe_state(raw, searched):
    """P/E vendors list every state they serve ('Georgia, Massachusetts, ... California'); the card shows the
    state the couple searched when the vendor serves it (memory reference_table11_state_multivalue)."""
    toks = [t.strip() for t in str(raw or "").split(",") if t.strip()]
    if searched and searched in toks:
        return searched
    return toks[0] if toks else (searched or "")


def _pe_package(p):
    return {"name": p.get("name"), "price": p.get("price") or None, "hours": p.get("hours") or None,
            "type": p.get("package_type") or p.get("service_type") or None,
            "team": p.get("team_size") or None,
            "included": [x for x in (p.get("included") or []) if x][:6],
            "addons": [x for x in (p.get("addons") or []) if x][:4]}


def _pe_item(it, category, searched_state, stems):
    pk = [_pe_package(p) for p in it.get("matched_packages") or []]
    # Keyword packages first (a pianist's piano package, not their violin one), then cheapest first, so the
    # "from $X" the card shows is always among the top packages the model reads.
    pk.sort(key=lambda p: (-_kw_score(p.get("name") or "", stems), p.get("price") or float("inf")))
    hrs = sorted({p["hours"] for p in pk if p.get("hours")})
    types = []
    for p in pk:
        if p.get("type") and p["type"] not in types:
            types.append(p["type"])
    return {
        "vendor_id": it.get("Vendor_ID"), "vendor_idx": it.get("id"),
        "name": it.get("Name"), "state": _pe_state(it.get("State"), searched_state), "category": category,
        "vendor_tags": (it.get("Type_of_Photography") if category == "Photography"
                        else it.get("Type_of_Entertainment")) or "",
        "package_types": types[:4],
        "package_count": int(it.get("matched_package_count") or len(pk)),
        "price_range": [it.get("matched_min_price"), it.get("matched_max_price")],
        "from_price": _from_price(pk, stems, it.get("matched_min_price")),
        "hours_range": [hrs[0], hrs[-1]] if hrs else None,
        "offers": [o for o in (it.get("venue_attributes") or []) if o and ";" not in o][:8],
        "packages": pk, "image": it.get("image_1"),
        "blurb": (it.get("vendor_blurb") or it.get("pdf_description") or it.get("Description") or "")[:400],
        "keyword_match": _kw_hit(_kw_text(it), stems) if stems else None,
    }


def _search_pe_one(token, *, states, category, max_price=0, types=None, offers=None, hour_bands=None,
                   keyword="", sort_by="popular_desc", page_size=8):
    _, offer_vocab, band_vocab = _pe_vocab(category)
    base = {"Category_Input": category, "states[]": list(states or []), "State_Input": (states or [""])[0],
            "max_capacity": 0, "capacity_ceiling": 10000, "base_fee_max": int(max_price or 0),
            "fb_per_person_max": 0, "Search_Input": "", "sort_by": sort_by or "", "fallback_all": "false"}
    tp = _clean_types(category, types)
    of = [v for v in (offers or []) if v in offer_vocab]
    hb = [v for v in (hour_bands or []) if v in band_vocab]
    if tp:
        base["venue_types[]"] = tp
    if of:
        base["vibes[]"] = of
    if hb:
        base["pricing_models[]"] = hb
    stems = _stems(keyword)
    items, total = [], 0
    for page in (1, 2) if stems else (1,):
        d = _get(config.API_SEARCH_GROUP + "/wptp_updated_mappings_search", token,
                 dict(base, page=page, page_size=PE_FETCH if stems else max(1, min(int(page_size), PE_FETCH))))
        items += d.get("items") or []
        total = d.get("itemsTotal") or 0
        hits = sum(1 for it in items if _kw_hit(_kw_text(it), stems))
        if not stems or hits >= 3 or not d.get("nextPage"):
            break
    st = (states or [""])[0]
    out = [_pe_item(it, category, st, stems) for it in items]
    res = {"total_matches": total}
    if stems:   # keyword matches first, in popularity order; the rest stay as closest alternatives
        out.sort(key=lambda v: not v["keyword_match"])
        res["keyword_matches"] = sum(1 for v in out if v["keyword_match"])
    res["vendors"] = out[:max(1, int(page_size))]
    return res


def search_pe(token, category, *, states, max_price=0, types=None, offers=None, hour_bands=None, keyword="",
              sort_by="popular_desc", page_size=8):
    """Photographers or entertainers via ep119 (the Discovery search, user's own token). One search per
    state, interleaved, like venues. Returns vendors with their MATCHED packages (price, hours, type, team,
    included, add-ons). ep119 returns EXACT package prices to any signed-in user (no paid check), so
    DETAIL: LIGHT is enforced by the caller (agent._light_pe)."""
    if category not in PE_CATEGORIES:
        raise ValueError("category must be Photography or Entertainment")
    return _interleave(_search_pe_one, token, states, page_size, "vendors", category=category,
                       max_price=max_price, types=types, offers=offers, hour_bands=hour_bands, keyword=keyword,
                       sort_by=sort_by)


def _bucket(b):
    if not b:
        return None
    return {k: b.get(k) for k in ("bucket", "scope", "n_packages", "n_vendors", "p25", "median", "p75")
            if b.get(k) not in (None, "")}


def pe_benchmarks(category, state):
    """ep207 (public, precomputed nightly from table 59): package-price quartiles for a state, per package
    type / coverage band (photo) or service type (ent). A bucket under 5 packages / 3 vendors falls back to
    the national pool PER ROW - each row carries its own scope ("state" or "US-wide")."""
    d = _get(config.API_SEARCH_GROUP + "/pe_benchmarks", None,
             {"category": category, "state": state or "", "state_fallback": ""})
    if not d or d.get("overall") is None:
        return {"category": category, "state": state, "available": False}
    h = d.get("headline") or {}
    return {"category": category, "state": d.get("resolved_state") or state, "available": True,
            "headline": dict(_bucket(h) or {}, basis=h.get("basis")) if h else _bucket(d.get("overall")),
            "overall": _bucket(d.get("overall")),
            "by_package_type": [b for b in map(_bucket, d.get("by_package_type") or []) if b],
            "by_coverage_hours": [b for b in map(_bucket, d.get("by_coverage") or []) if b],
            "by_service_type": [b for b in map(_bucket, d.get("by_service_type") or []) if b],
            "note": "Package prices from vendors' pricing PDFs: p25-p75 = the middle half. scope 'US-wide' = too "
                    "few packages in the state, national figure used. Medians, never averages."}


def pe_bucket_for(bench, package_type):
    """The benchmark row a package should be compared with: its own package/service type when the market
    has one, else the headline."""
    if not bench or not bench.get("available"):
        return None
    for b in (bench.get("by_package_type") or []) + (bench.get("by_service_type") or []):
        if package_type and b.get("bucket") == package_type:
            return b
    return bench.get("headline")


def saved_vendor_ids(user):
    """The couple's saved vendors = user.favorited_vendors (what the Saved tab shows). 2026-10-03: the old
    wptp_updated_mappings_favorites GET returned [] for a user with 4 saved vendors, so the assistant said
    'I can't see a saved list'. auth/me already carries the list - read it from there."""
    ids, seen = [], set()
    for v in user.get("favorited_vendors") or []:
        v = str(v).strip()
        if v and v not in seen:
            seen.add(v)
            ids.append(v)
    return ids[:30]


def vendor_card(vendor_id):
    """One vendor as a card (same shape search_venues returns), looked up by Vendor_ID in table 11."""
    rows = [r for r in _search_all(11, [{"Vendor_ID": vendor_id}]) if r.get("Vendor_ID") == vendor_id]
    if not rows:
        return None
    it = rows[0]
    img = it.get("image_1")
    if it.get("Category") in PE_CATEGORIES:   # photographer / entertainer shown earlier in this chat
        cat = it["Category"]
        lo, hi = it.get("flt_min_venue_fee"), it.get("flt_max_venue_fee")   # P/E: primary PDF package prices
        return {"vendor_id": it.get("Vendor_ID"), "vendor_idx": it.get("id"), "name": it.get("Name"),
                "state": _pe_state(it.get("State"), ""), "category": cat,
                "venue_type": (it.get("Type_of_Photography") if cat == "Photography"
                               else it.get("Type_of_Entertainment")) or cat,
                "price_range": [lo, hi] if lo else None, "image": img,
                "description": (it.get("Description") or "")[:300]}
    return {"vendor_id": it.get("Vendor_ID"), "vendor_idx": it.get("id"), "name": it.get("Name"),
            "state": it.get("State"), "address": it.get("Address"), "category": it.get("Category"),
            "venue_type": it.get("flt_venue_types") or it.get("Venue_Type"),
            "max_capacity_seated": it.get("Max_Capacity_Seated"),
            "venue_fee_range": [it.get("flt_min_venue_fee"), it.get("flt_max_venue_fee")],
            "image": img, "description": (it.get("Description") or "")[:300]}


_WEDDING_WORDS = re.compile(r"wedding|reception|ceremony|bride|groom|married|our event|rehearsal", re.I)


def venue_details(vendor_id, with_amounts):
    """Facts we already extracted but the PI panel doesn't show: per-space menu/bar per person, off-peak
    (lowest Saturday) prices, peak months, other fees, preferred vendors, outdoor ceremony space - from
    table 36 (latest extraction per PDF) - plus Google rating/reviews from table 11's google_data_cache.

    Reads tables directly with the metadata token, so the paywall is enforced HERE: amounts only when
    with_amounts (Forever, DETAIL: FULL)."""
    rows = [r for r in _search_all(36, [{"VENDOR_ID": vendor_id}]) if r.get("VENDOR_ID") == vendor_id]
    latest = {}
    for r in rows:   # dedupe: latest extraction per (PDF, space)
        k = (r.get("PDF_ID"), r.get("Venue_Space_Name") or "")
        if k not in latest or (r.get("last_extracted_at") or "") > (latest[k].get("last_extracted_at") or ""):
            latest[k] = r
    money = ("Venue_Fee_on_a_Peak_Season_Saturday", "Venue_Fee_on_Lowest_Saturday",
             "Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday", "Per_Person_Food_and_Beverage_on_Lowest_Saturday",
             "Food_and_Beverage_Min_on_a_Peak_Season_Saturday", "Food_and_Beverage_Min_on_Lowest_Saturday",
             "Base_Menu_Fee_Per_Person", "Base_Bar_Package_Per_Person", "Ceremony_Fee", "Admin_Service_Fee")
    plain = ("Months__Highest_Pricing", "Months__Lowest_Pricing", "FB_Spend_Min_Type", "Additional_Fees",
             "Outside_Ceremony_Space", "Preferred_Vendors", "Guest_Min_Highest_Sat", "Guest_Min_Lowest_Sat",
             "Max_Capacity_Seated", "Pricing_Year", "Venue_Offering")
    spaces = []
    for r in list(latest.values())[:6]:
        sp = {"space": r.get("Venue_Space_Name") or ""}
        for f in plain:
            if r.get(f) not in (None, "", 0):
                sp[f] = r[f]
        desc = r.get("Additional_Fees_Description") or ""
        if with_amounts:
            for f in money:
                if r.get(f) not in (None, "", 0):
                    sp[f] = r[f]
            if desc:
                sp["Additional_Fees_Description"] = desc[:300]
        spaces.append(sp)
    out = {"vendor_id": vendor_id, "spaces": spaces,
           "note": "Peak = peak-season Saturday; 'Lowest' = the cheapest Saturday rate in the PDF. 0/blank = not in the PDF."}
    t11 = [r for r in _search_all(11, [{"Vendor_ID": vendor_id}]) if r.get("Vendor_ID") == vendor_id]
    g = (t11[0].get("google_data_cache") or {}) if t11 else {}
    if g.get("rating"):
        revs = []
        for rv in (g.get("reviews") or [])[:5]:
            text = (rv.get("text") or "").strip()
            if text:
                revs.append({"rating": rv.get("rating"), "when": rv.get("relative_time_description"),
                             "about_a_wedding": bool(_WEDDING_WORDS.search(text)), "text": text[:400]})
        out["google"] = {"rating": g.get("rating"), "review_count": g.get("user_ratings_total"),
                         "note": "Rating covers ALL Google reviews of the place, not only weddings; we hold at most 5 review texts.",
                         "reviews": revs}
    return out


# ---------------------------------------------------------------- conversations (tables 42/43) + memory (80)
# Written by the SERVICE with the metadata token, never by the browser, and every read checks ownership,
# so one couple can never load another couple's chat by guessing an id.

CHATS_TABLE, MESSAGES_TABLE, MEMORY_TABLE = 42, 43, 80


def create_chat(user_id, title):
    return _meta("POST", "/table/%d/content" % CHATS_TABLE, json={"user_id": int(user_id), "title": title[:120]})


def _put_full(table_id, row, changes):
    """Metadata PATCH silently no-ops (memory: reference_xano_write_access) - PUT the full record."""
    body = {k: v for k, v in row.items() if k not in ("id",)}
    body.update(changes)
    _meta("PUT", "/table/%d/content/%d" % (table_id, int(row["id"])), json=body)


def touch_chat(chat_row):
    _put_full(CHATS_TABLE, chat_row, {"updated_at": int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)})


def list_chats(user_id):
    uid = int(user_id)
    rows = [r for r in _search_all(CHATS_TABLE, [{"user_id": uid}]) if int(r.get("user_id") or 0) == uid]
    rows.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or 0, reverse=True)
    return [{"id": r["id"], "title": r.get("title") or "", "updated_at": r.get("updated_at"),
             "created_at": r.get("created_at")} for r in rows]


def get_chat(user_id, chat_id):
    """Chat row if it belongs to this user, else None."""
    try:
        r = _meta("GET", "/table/%d/content/%d" % (CHATS_TABLE, int(chat_id)))
    except XanoError:
        return None
    return r if r and int(r.get("user_id") or 0) == int(user_id) else None


def add_message(chat_id, role, content, cards=None, chips=None):
    _meta("POST", "/table/%d/content" % MESSAGES_TABLE,
          json={"chat_id": int(chat_id), "role": role, "content": content or "",
                "cards": cards or [], "chips": chips or []})


def get_messages(chat_id):
    cid = int(chat_id)
    rows = [r for r in _search_all(MESSAGES_TABLE, [{"chat_id": cid}]) if int(r.get("chat_id") or 0) == cid]
    rows.sort(key=lambda r: (r.get("created_at") or 0, r.get("id") or 0))
    # "Show 3 more" cards are stored in the same cards column flagged more=True (no schema change) and
    # split back out here, so `cards` is exactly what it was before the feature for any older client.
    # A turn written by POST /rec/more (the couple paid a question for 3 more) has its cards flagged
    # via_more=True; it is reported as is_more so it never becomes the source of a new "more" pool.
    out = []
    for r in rows:
        allc = r.get("cards") or []
        strip = lambda c: {k: v for k, v in c.items() if k not in ("more", "via_more")}
        out.append({"id": r.get("id"), "role": r["role"], "text": r.get("content") or "",
                    "cards": [strip(c) for c in allc if not c.get("more")],
                    "more_cards": [strip(c) for c in allc if c.get("more")],
                    "is_more": any(c.get("via_more") for c in allc),
                    "chips": r.get("chips") or []})
    return out


def get_memory(user_id):
    uid = int(user_id)
    rows = [r for r in _search_all(MEMORY_TABLE, [{"user_id": uid}]) if int(r.get("user_id") or 0) == uid]
    return rows[0] if rows else None


def add_memory_note(user_id, note, cap=20):
    row = get_memory(user_id)
    notes = list((row or {}).get("notes") or [])
    note = note.strip()[:200]
    if not note or any(n.get("note", "").lower() == note.lower() for n in notes):
        return
    # The cap trims the model's own notes only - never the couple's preference answers (kind pref/pref_skipped).
    kept = [n for n in notes if n.get("kind") in ("pref", "pref_skipped")]
    own = [n for n in notes if n.get("kind") not in ("pref", "pref_skipped")]
    notes = kept + (own + [{"note": note, "at": today()}])[-cap:]
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    if row:
        _put_full(MEMORY_TABLE, row, {"notes": notes, "updated_at": now_ms})
    else:
        _meta("POST", "/table/%d/content" % MEMORY_TABLE, json={"user_id": int(user_id), "notes": notes})


def set_preferences(user_id, answers, skipped=False):
    """Replace the couple's preference answers in rec_memory.notes (kind="pref", one entry per question).
    skipped=True with no answers stores a hidden pref_skipped marker so the questions aren't pushed again.
    Returns the new pref entries (what the page tells the couple was added)."""
    row = get_memory(user_id)
    notes = [n for n in list((row or {}).get("notes") or []) if n.get("kind") not in ("pref", "pref_skipped")]
    new = [dict(n, at=today()) for n in prefs.to_notes(answers)]
    if not new and skipped:
        new = [{"kind": "pref_skipped", "at": today()}]
    notes = new + notes
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    if row:
        _put_full(MEMORY_TABLE, row, {"notes": notes, "updated_at": now_ms})
    else:
        _meta("POST", "/table/%d/content" % MEMORY_TABLE, json={"user_id": int(user_id), "notes": notes})
    return new


# ---------------------------------------------------------------- Mixpanel (server side)

def mp_track(user_id, event, props):
    """Fire-and-forget Mixpanel event, distinct_id = String(user.id) like the client. "ip": "0" so
    Mixpanel doesn't geolocate the user to the Railway server. Never raises."""
    if not config.MIXPANEL_TOKEN:
        return
    try:
        body = [{"event": event, "properties": dict(props, token=config.MIXPANEL_TOKEN,
                                                    distinct_id=str(user_id), ip="0",
                                                    time=int(dt.datetime.now(dt.timezone.utc).timestamp()))}]
        requests.post("https://api.mixpanel.com/track?ip=0", json=body, timeout=10)
    except Exception:
        pass



CONTEXT_MAX = 1000


def set_user_context(user_id, text):
    """The couple's own free-text context (max 1,000 chars), read into every conversation."""
    text = (text or "").strip()[:CONTEXT_MAX]
    row = get_memory(user_id)
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    if row:
        _put_full(MEMORY_TABLE, row, {"user_context": text, "updated_at": now_ms})
    else:
        _meta("POST", "/table/%d/content" % MEMORY_TABLE,
              json={"user_id": int(user_id), "notes": [], "user_context": text})
    return text


def has_beta_feedback(user_id):
    uid = int(user_id)
    return any(int(r.get("user_id") or 0) == uid for r in _search_all(config.FEEDBACK_TABLE_ID, [{"user_id": uid}]))


def add_beta_feedback(row):
    return _meta("POST", "/table/%d/content" % config.FEEDBACK_TABLE_ID, json=row)


def delete_memory_note(user_id, index):
    row = get_memory(user_id)
    notes = list((row or {}).get("notes") or [])
    # `index` counts VISIBLE notes (the panel lists only entries with a "note"); hidden markers such as
    # pref_skipped must not shift which one gets deleted.
    visible = [i for i, n in enumerate(notes) if n.get("note")]
    if not row or not (0 <= int(index) < len(visible)):
        return notes
    notes.pop(visible[int(index)])
    _put_full(MEMORY_TABLE, row, {"notes": notes})
    return notes
