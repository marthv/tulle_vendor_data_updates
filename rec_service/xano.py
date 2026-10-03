"""Everything that talks to Xano. Read-only against app data; the only write is one rec_usage row
per request. Data calls go out WITH THE USER'S OWN TOKEN, so Xano's existing auth and paywall
rules apply unchanged - the service can never see more than the user could in the app."""
import datetime as dt

import requests

import config

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
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def has_paid_access(user):
    """Entitlement model (memory reference_access_entitlement_model): forever_access_purchased OR
    date_until_access in the future. Read from auth/me, which reads live."""
    if user.get("forever_access_purchased"):
        return True
    until = _as_dt(user.get("date_until_access"))
    return bool(until and until > dt.datetime.now(dt.timezone.utc))


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
    d = today()
    return sum(float(r.get("cost_usd") or 0) for r in _search_all(config.USAGE_TABLE_ID, [{"usage_day": d}])
               if r.get("usage_day") == d)


def log_usage(row):
    row = dict(row, usage_day=today())
    _meta("POST", "/table/%d/content" % config.USAGE_TABLE_ID, json=row)


# ---------------------------------------------------------------- data the tools read

def search_venues(token, *, states, guests=0, max_venue_fee=0, max_food_per_person=0,
                  venue_types=None, keyword="", sort_by="popular_desc", page_size=8):
    """ep119 - the same search the Vendor Discovery grid uses. `states` match the multi-value State
    field server-side (never equality). max_capacity means 'seats AT LEAST N'."""
    params = {"Category_Input": "Venue", "page": 1, "page_size": max(1, min(int(page_size), 12)),
              "states[]": list(states or []), "State_Input": (states or [""])[0],
              "max_capacity": int(guests or 0), "capacity_ceiling": 10000,
              "base_fee_max": int(max_venue_fee or 0), "fb_per_person_max": int(max_food_per_person or 0),
              "Search_Input": keyword or "", "sort_by": sort_by or "", "fallback_all": "false"}
    if venue_types:
        params["venue_types[]"] = list(venue_types)
    d = _get(config.API_SEARCH_GROUP + "/wptp_updated_mappings_search", token, params)
    out = []
    for it in d.get("items", []):
        out.append({
            "vendor_id": it.get("Vendor_ID"), "vendor_idx": it.get("id"),  # page route needs both
            "name": it.get("Name"), "state": it.get("State"),
            "address": it.get("Address"), "venue_type": it.get("Venue_Type"),
            "max_capacity_seated": it.get("Max_Capacity_Seated"),
            "venue_fee_range": [it.get("flt_min_venue_fee"), it.get("flt_max_venue_fee")],
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
            "verdict": band.get("sentence"), "lines": rows, "source_note": sp.get("footer")}


def market_benchmarks(state):
    """table 63: what venues cost in a state (per-guest all-in at 75/125/200, fees, F&B). Medians and
    quartiles only - never the mean (long right tail)."""
    rows = _search_all(config.BENCHMARK_TABLE_ID, [{"State": state}, {"Dimension": "overall"}])
    out = {}
    for r in rows:
        if r.get("State") != state or r.get("Dimension") != "overall" or r.get("Bucket") != "all":
            continue
        out[r["Metric"]] = {"p25": r.get("p25"), "median": r.get("median"), "p75": r.get("p75"),
                            "n_spaces": r.get("n_spaces"), "n_vendors": r.get("n_vendors")}
    return {"state": state, "metrics": out,
            "note": "all_in_pg_N = all-in cost per guest at N guests, before tax and gratuity."}


def saved_vendors(token):
    try:
        d = _get(config.API_SEARCH_GROUP + "/wptp_updated_mappings_favorites", token)
    except XanoError:
        return []
    items = d if isinstance(d, list) else d.get("items", [])
    return [{"vendor_id": i.get("Vendor_ID"), "name": i.get("Name"), "category": i.get("Category")} for i in items][:30]
