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


def has_forever(user):
    """User decision 2026-10-03: the Planning Assistant is UNLIMITED only on Forever. Free users and
    1-week / 4-week buyers get the opening picks + 3 questions for life, then a Forever upsell.
    (1w/4w buyers still get exact prices in answers - has_paid_access - since they can open PDFs.)"""
    return bool(user.get("forever_access_purchased"))


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
    return [{"id": r["id"], "title": r.get("title") or "", "updated_at": r.get("updated_at")} for r in rows]


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
    return [{"role": r["role"], "text": r.get("content") or "", "cards": r.get("cards") or [],
             "chips": r.get("chips") or []} for r in rows]


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
    notes = (notes + [{"note": note, "at": today()}])[-cap:]
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    if row:
        _put_full(MEMORY_TABLE, row, {"notes": notes, "updated_at": now_ms})
    else:
        _meta("POST", "/table/%d/content" % MEMORY_TABLE, json={"user_id": int(user_id), "notes": notes})


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
