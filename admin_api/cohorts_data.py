"""
Cohorts snapshot for the Cloudflare admin page (admin.tulletogether.app).

Replaces the data half of cohorts.py (the Streamlit tab). The browser does all slicing,
so this module only pulls the two secret-gated Xano exports (ep205 analytics_users_export,
ep206 analytics_hub_stats), reduces each user to the ~14 fields the tab uses, and ships
them column-wise with categorical columns dictionary-encoded (≈50k users → a few MB raw,
far less gzipped). No emails or names exist in the export, and none are sent.

Bucketing rules are copied verbatim from cohorts.py so the numbers match the old tab.
"""

import time
import datetime as _dt

import requests

from . import config

_ROLE_EXCLUDE = {"admin", "test", "staff", "superadmin", "dev"}


def _xget(url, params, tries=4, timeout=60):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                return None
        except Exception:  # noqa: BLE001 - transient network error, retry
            pass
        time.sleep(1.5 * (i + 1))
    return None


def _num(v):
    try:
        f = float(v)
        return f if f == f else 0.0  # NaN -> 0
    except (TypeError, ValueError):
        return 0.0


def _budget_bucket(v):
    v = _num(v)
    if v <= 0:      return "Unknown"
    if v < 15000:   return "<$15k"
    if v < 30000:   return "$15–30k"
    if v < 50000:   return "$30–50k"
    if v < 100000:  return "$50–100k"
    return "$100k+"


def _guest_bucket(v):
    v = _num(v)
    if v <= 0:   return "Unknown"
    if v < 50:   return "<50"
    if v < 100:  return "50–99"
    if v < 150:  return "100–149"
    if v < 250:  return "150–249"
    return "250+"


def _clean(s):
    return (str(s).strip() or "Unknown") if s is not None else "Unknown"


def _customization(v):
    s = str(v or "").strip().lower()
    if not s:
        return "Unknown"
    if "turn-key" in s or "turnkey" in s:
        return "Turn-key"
    if "customize everything" in s:
        return "Full customization"
    if "some customization" in s or "prefer some things" in s:
        return "Some customization"
    return "Other"


def _ms(v):
    try:
        return int(v) if v not in (None, "", 0) else None
    except (TypeError, ValueError):
        return None


def load_hubs():
    """Returns (tiers, hub_meta). Same tiering as cohorts.load_hubs: hubs ranked by vendor
    count, top third High, middle Medium, rest Low."""
    hub = _xget(f"{config.XANO_BASE}/analytics_hub_stats",
                {"secret": config.EXPORT_SECRET}, timeout=30, tries=6)
    rows = hub.get("items", []) if isinstance(hub, dict) else (hub or [])
    meta, hubs = {}, []
    for r in rows:
        k = str(r.get("hub_key") or "")
        if not k:
            continue
        vc = int(r.get("vendor_count") or 0)
        meta[k] = {"display_name": r.get("display_name") or k, "vendor_count": vc,
                   "venue_count": int(r.get("venue_count") or 0)}
        hubs.append((k, vc))
    hubs.sort(key=lambda x: -x[1])
    n, tiers = len(hubs), {}
    for i, (k, _c) in enumerate(hubs):
        tiers[k] = ("High density" if i < n / 3
                    else "Medium density" if i < 2 * n / 3 else "Low density")
    return tiers, meta


def load_users_raw():
    rows, page = [], 1
    while page <= 80:
        d = _xget(f"{config.XANO_BASE}/analytics_users_export",
                  {"secret": config.EXPORT_SECRET, "page": page, "per_page": 1000})
        batch = d.get("items", []) if isinstance(d, dict) else (d or [])
        if not batch:
            break
        rows.extend(batch)
        if not (isinstance(d, dict) and d.get("nextPage")):
            break
        page += 1
        time.sleep(0.25)
    return rows


# Column order is the wire contract with admin_web/js/cohorts.js.
_CAT_COLS = ["planning_phase", "age_range", "referral", "customization",
             "budget_bucket", "guest_bucket", "hub_key", "density_tier"]
_NUM_COLS = ["vendor_views", "pdf_views", "payments", "paid"]
_TS_COLS = ["signup", "last_active"]


def build_snapshot():
    """Pull + reduce. Raises RuntimeError if the users pull came back empty so the caller
    keeps serving the previous snapshot instead of an empty one."""
    t0 = time.time()
    rows = load_users_raw()
    if not rows:
        raise RuntimeError("analytics_users_export returned no rows")
    tiers, hub_meta = load_hubs()

    dicts = {c: [] for c in _CAT_COLS}
    index = {c: {} for c in _CAT_COLS}
    cols = {c: [] for c in _CAT_COLS + _NUM_COLS + _TS_COLS}

    def enc(col, val):
        m = index[col]
        if val not in m:
            m[val] = len(dicts[col])
            dicts[col].append(val)
        return m[val]

    for r in rows:
        if str(r.get("role") or "").strip().lower() in _ROLE_EXCLUDE:
            continue
        hk = r.get("hub_key")
        hk = str(hk).strip() if hk is not None and str(hk).strip() not in ("", "nan", "None") else ""
        density = (tiers.get(hk, "Low density") if hk else "Unmapped")
        vals = {
            "planning_phase": _clean(r.get("Planning_Phase")),
            "age_range": _clean(r.get("Age_Range")),
            "referral": _clean(r.get("How_did_you_hear_about_us")),
            "customization": _customization(r.get("venue_customization")),
            "budget_bucket": _budget_bucket(r.get("Wedding_Budget")),
            "guest_bucket": _guest_bucket(r.get("Wedding_Guest_Count")),
            "hub_key": hk,
            "density_tier": density,
        }
        for c in _CAT_COLS:
            cols[c].append(enc(c, vals[c]))
        cols["vendor_views"].append(int(_num(r.get("total_vendor_views"))))
        cols["pdf_views"].append(int(_num(r.get("total_pdf_views"))))
        cols["payments"].append(int(_num(r.get("total_payments"))))
        cols["paid"].append(round(_num(r.get("total_amount_paid")), 2))
        cols["signup"].append(_ms(r.get("signup_at")))
        cols["last_active"].append(_ms(r.get("last_active_at")))

    return {
        "as_of": _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "n": len(cols["signup"]),
        "raw_rows": len(rows),
        "build_seconds": round(time.time() - t0, 1),
        "dicts": dicts,
        "cols": cols,
        "hub_meta": hub_meta,
    }
