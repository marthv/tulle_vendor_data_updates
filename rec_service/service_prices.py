"""Catering / hair & makeup / planner prices from the vendors' own pricing PDFs (owner 2026-11-18).

Source: Xano tables 92 vendor_price_quotes + 93 vendor_price_items (extract_services.py, Sonnet 5.5, pilot-graded
177/180 headline prices right), read through the secret-gated GET pdfdata/vendor_prices into one in-memory cache.
OFF unless REC_WW_SERVICE_PRICES=1 and REC_VENDOR_PRICES_SECRET is set (config.py).

typical(slot, vendor_id, guests, target) -> {"amount", "based_on", "note"} or None. The amount is Tulle's working
from that vendor's sheet (a per-guest price x the couple's guests, a bride + an assumed party, a planning tier), so
every row built from it is an ESTIMATE that says what it was worked out from - never shown as a price on the sheet.
Nothing is returned when the sheet does not support a number (non-USD, no per-guest price, implausible values).

Paid data: whole_wedding only puts the amount in a row at detail "full"; everyone else gets the locked row.
"""
import json
import statistics
import threading
import time
import urllib.parse
import urllib.request

import config

SLOT_CAT = {"Food and Beverage": "catering", "Beauty": "beauty", "Wedding Planner": "planner"}
PARTY_N = 4               # bridal-party hair & makeup services assumed when the couple has not said (stated in the row)
PLANNER_ORDER = ("month_of", "partial_planning", "full_planning", "day_of")
PLANNER_LABEL = {"month_of": "month-of coordination", "partial_planning": "partial planning",
                 "full_planning": "full planning", "day_of": "day-of coordination"}
OK_CONF = ("high", "medium", "")
TTL_S = 6 * 3600
FAIL_RETRY_S = 300

_LOCK = threading.Lock()
_CACHE = {"at": 0.0, "failed_at": 0.0, "by_vendor": {}}


def _money(n):
    return "$" + format(int(round(n or 0)), ",")


def index(payload):
    """{quotes, items} -> vendor_id -> {"cat", "quote", "items"}. One vendor with several PDFs: the newest quote row
    wins for the quote fields; items from every PDF are kept (more lines to read a price from)."""
    out = {}
    for q in sorted((payload or {}).get("quotes") or [], key=lambda q: q.get("created_at") or 0):
        vid = str(q.get("Vendor_ID") or "")
        if vid:
            out.setdefault(vid, {"cat": q.get("category") or "", "quote": {}, "items": []})["quote"] = q
    for it in (payload or {}).get("items") or []:
        vid = str(it.get("Vendor_ID") or "")
        if vid in out and (it.get("confidence") or "") in OK_CONF:
            out[vid]["items"].append(it)
    return out


def _fetch():
    url = config.VENDOR_PRICES_URL + "?" + urllib.parse.urlencode({"secret": config.VENDOR_PRICES_SECRET})
    with urllib.request.urlopen(urllib.request.Request(url), timeout=20) as r:
        return json.loads(r.read() or b"{}")


def vendors(now=None):
    """The cached index. Refreshes every TTL_S; a failed read keeps the last good copy and retries after FAIL_RETRY_S."""
    if not (config.WW_SERVICE_PRICES and config.VENDOR_PRICES_SECRET):
        return {}
    now = time.time() if now is None else now
    with _LOCK:
        fresh = now - _CACHE["at"] < TTL_S
        backoff = now - _CACHE["failed_at"] < FAIL_RETRY_S
        if fresh or backoff:
            return _CACHE["by_vendor"]
    try:
        idx = index(_fetch())
        with _LOCK:
            _CACHE.update(at=now, by_vendor=idx)
    except Exception as e:
        print("vendor prices read failed:", repr(e)[:200])
        with _LOCK:
            _CACHE["failed_at"] = now
    with _LOCK:
        return _CACHE["by_vendor"]


def _low(items, keys, units, lo=0, hi=10 ** 7):
    """Lowest-priced lines among keys (in that key order of preference is NOT applied here) -> [price_low ...]."""
    out = []
    for it in items:
        p = it.get("price_low")
        if it.get("item_key") in keys and (it.get("pricing_unit") or "") in units and isinstance(p, (int, float)) \
                and lo <= p <= hi:
            out.append(float(p))
    return out


def _pct(q, k, cap=35):
    v = q.get(k)
    return float(v) if isinstance(v, (int, float)) and 0 < v <= cap else 0.0


def _fees(q, sub):
    """Service charge + gratuity + tax as the sheet states them. -> (total, [phrases])."""
    svc = _pct(q, "service_charge_pct") + _pct(q, "gratuity_pct")
    tax = _pct(q, "tax_rate_pct", 15)
    words = []
    if svc:
        words.append("%g%% service" % svc)
    if tax:
        words.append("%g%% tax" % tax)
    return sub * (1 + svc / 100) * (1 + tax / 100), words


def _with_min(q, amount, words):
    m = q.get("minimum_spend")
    if isinstance(m, (int, float)) and m > amount and m < 10 ** 6:
        words.append("raised to their %s minimum" % _money(m))
        return float(m)
    return amount


def _catering(q, items, guests):
    if not guests:
        return None
    pp = _low(items, ("package_per_person",), ("per_person",), 5, 500) or \
        _low(items, ("entree_per_person",), ("per_person",), 5, 500)
    if not pp:
        return None
    food = statistics.median(pp)
    bar = _low(items, ("bar_package",), ("per_person",), 3, 200)
    bar_pp = min(bar) if bar else 0
    sub = (food + bar_pp) * guests
    amount, fees = _fees(q, sub)
    words = ["%s a guest for food" % _money(food)]
    if bar_pp:
        words.append("%s a guest for their lowest bar package" % _money(bar_pp))
    words = ["%s x %d guests" % (" + ".join(words), guests)] + (["plus " + " and ".join(fees)] if fees else [])
    amount = _with_min(q, amount, words)
    return {"amount": int(round(amount)), "note": "about %s a guest" % _money(food + bar_pp),
            "based_on": "Worked out from this caterer's own price sheet: %s. Rentals, staffing and anything else on "
                        "their sheet are not in it. Not a quote." % ", ".join(words)}


def _beauty(q, items):
    bride = _low(items, ("bride_hair_makeup",), ("flat", "per_service", "per_person"), 50, 5000)
    if not bride:
        h = _low(items, ("bride_hair",), ("flat", "per_service", "per_person"), 30, 3000)
        m = _low(items, ("bride_makeup",), ("flat", "per_service", "per_person"), 30, 3000)
        bride = [min(h) + min(m)] if h and m else []
    if not bride:
        return None
    b = min(bride)
    words = ["%s for the bride's hair and makeup" % _money(b)]
    total = b
    trial = _low(items, ("bride_trial",), ("flat", "per_service", "per_person"), 20, 2000)
    if trial:
        total += min(trial)
        words.append("%s trial" % _money(min(trial)))
    party = _low(items, ("party_hair_makeup",), ("per_service", "per_person", "flat"), 20, 2000)
    if not party:
        ph = _low(items, ("party_hair",), ("per_service", "per_person", "flat"), 20, 1500)
        pm = _low(items, ("party_makeup",), ("per_service", "per_person", "flat"), 20, 1500)
        party = [min(ph) + min(pm)] if ph and pm else (ph or pm)
    if party:
        total += PARTY_N * min(party)
        words.append("%d bridal-party services at %s (an assumption - tell me your number)" % (PARTY_N, _money(min(party))))
    svc = _pct(q, "service_charge_pct") + _pct(q, "gratuity_pct")
    if svc:
        total *= 1 + svc / 100
        words.append("plus %g%% service" % svc)
    total = _with_min(q, total, words)
    return {"amount": int(round(total)), "note": "bride from %s" % _money(b),
            "based_on": "Worked out from this artist's own price sheet: %s. Travel and early-start fees are not in it. "
                        "Not a quote." % ", ".join(words)}


def _planner(q, items, target):
    for key in PLANNER_ORDER:
        p = _low(items, (key,), ("flat", "per_service", "unknown"), 200, 100000)
        if p:
            return {"amount": int(round(min(p))), "note": "%s from %s" % (PLANNER_LABEL[key], _money(min(p))),
                    "based_on": "This planner's own price sheet: %s from %s. Not a quote." % (PLANNER_LABEL[key],
                                                                                             _money(min(p)))}
    pct = [it["price_low"] for it in items if it.get("item_key") in ("percentage_fee", "full_planning")
           and it.get("pricing_unit") == "percent" and isinstance(it.get("price_low"), (int, float))
           and 3 <= it["price_low"] <= 25]
    if pct and target:
        amount = target * min(pct) / 100
        return {"amount": int(round(amount)), "note": "%g%% of the budget" % min(pct),
                "based_on": "This planner charges %g%% of the wedding budget on their own sheet; worked out on your "
                            "%s. Not a quote." % (min(pct), _money(target))}
    return None


def typical(slot, vendor_id, guests=None, target=None, by_vendor=None):
    cat = SLOT_CAT.get(slot)
    v = (vendors() if by_vendor is None else by_vendor).get(str(vendor_id or ""))
    if not cat or not v or v.get("cat") != cat:
        return None
    q = v.get("quote") or {}
    if (q.get("currency") or "USD").upper() != "USD":
        return None
    items = v.get("items") or []
    if cat == "catering":
        return _catering(q, items, guests)
    if cat == "beauty":
        return _beauty(q, items)
    return _planner(q, items, target)


def priced_ids(slot, by_vendor=None):
    """Vendor ids in this slot's category that typical() could price (guest count aside) - lets the listing search
    prefer them."""
    cat = SLOT_CAT.get(slot)
    bv = vendors() if by_vendor is None else by_vendor
    return {vid for vid, v in bv.items() if v.get("cat") == cat}
