"""
💰 Venue Pricing snapshot for the Cloudflare admin page.

The data half of the Streamlit tab (dashboard.py _vp_*): page through Xano ep199 (pricing +
pdfs sections), keep the latest extraction per PDF, shape rows, build the PDF link map and the
filter options. Copied verbatim from dashboard.py so the numbers match the old tab; the browser
does every filter / estimate / grid computation (admin_web/public/js/pricing.js).

Built on a background timer (the pull is ~75 s cold), so nobody waits for it on page load.
Rows ship as arrays in FIELDS order (≈20k rows); gzip takes care of the repetition.
"""

import datetime
import os
import time

import requests

from . import config

FIELDS = ["vendor_id", "name", "state", "venue_type", "vibes", "year", "space", "pdf_id",
          "guest_min", "max_cap", "venue_fee", "pp_fb", "fb_min", "admin_pct", "cer_fee", "cer_type"]


def _num(x):
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _ts(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def _fetch_paged(section, breaker, per_page=5000):
    """dashboard._vp_fetch_paged: paged ep199, retries gateway errors, trips a breaker when Xano is down."""
    rows, page = [], 1
    while True:
        if breaker.get("down"):
            return rows, "503"
        url = (f"{config.XANO_BASE}/venue_pricing_dashboard?section={section}&page={page}"
               f"&per_page={per_page}&secret={config.EXPORT_SECRET}")
        data, last = None, ""
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=60)
            except Exception as e:  # noqa: BLE001
                last = str(e)
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                data = r.json()
                break
            last = str(r.status_code)
            if r.status_code in (502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        if data is None:
            if last in ("502", "503", "504"):
                breaker["down"] = True
            return rows, last
        if not isinstance(data, dict):
            return rows, ""
        items = data.get("items") or []
        rows.extend(items)
        nxt = data.get("nextPage")
        if not nxt or not items:
            return rows, ""
        page = nxt


def _dedup_latest(rows):
    best, passthrough = {}, []
    for r in rows:
        pid = r.get("PDF_ID")
        if not pid:
            passthrough.append(r)
            continue
        prev = best.get(pid)
        if prev is None or _ts(r.get("last_extracted_at")) >= _ts(prev.get("last_extracted_at")):
            best[pid] = r
    return list(best.values()) + passthrough


_VIBE_NOT_A_VALUE = {"", "not listed", "none", "n/a", "na"}


def _vibes(raw):
    out = []
    for tok in str(raw or "").split(";"):
        tok = tok.strip()
        if tok and tok.lower() not in _VIBE_NOT_A_VALUE and tok not in out:
            out.append(tok)
    return out


def _join(t36_dedup):
    out = []
    for r in t36_dedup:
        vid = str(r.get("VENDOR_ID") or "").strip()
        if not vid:
            continue
        out.append({
            "vendor_id": vid,
            "name": str(r.get("venue_name") or "").strip() or (r.get("VENUE_NAME") or "").strip(),
            "state": str(r.get("venue_state") or "").strip(),
            "venue_type": (r.get("Venue_Type") or "").strip(),
            "vibes": _vibes(r.get("Venue_Attributes")),
            "year": str(r.get("Pricing_Year") or "").strip(),
            "space": (r.get("Venue_Space_Name") or "").strip(),
            "pdf_id": r.get("PDF_ID"),
            "guest_min": _num(r.get("Guest_Min_Highest_Sat")),
            "max_cap": _num(r.get("Max_Capacity_Seated")),
            "venue_fee": _num(r.get("Venue_Fee_on_a_Peak_Season_Saturday")),
            "pp_fb": _num(r.get("Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday")),
            "fb_min": _num(r.get("Food_and_Beverage_Min_on_a_Peak_Season_Saturday")),
            "admin_pct": _num(r.get("Admin_Service_Fee")),
            "cer_fee": _num(r.get("Ceremony_Fee")),
            "cer_type": (r.get("Ceremony_fee_Type") or "").strip(),
        })
    return out


def _pdf_map(pdf_rows):
    m = {}
    for r in pdf_rows:
        vid = str(r.get("Vendor_ID") or "").strip()
        link = str(r.get("PDF_Link") or "").strip()
        if not vid or not link:
            continue
        show = r.get("Show_PDF")
        if show is False or str(show).strip() in ("0", "false", "False"):
            continue
        m.setdefault(vid, []).append({
            "name": (str(r.get("Name") or r.get("Current_Assignment") or "PDF")).strip() or "PDF",
            "link": link,
            "year": str(r.get("Year_of_Pricing") or "").strip(),
        })
    return m


def _vibe_options(joined, min_rows=20):
    counts = {}
    for r in joined:
        for v in r["vibes"]:
            counts[v] = counts.get(v, 0) + 1
    keep = [(n, v) for v, n in counts.items() if n >= min_rows]
    keep.sort(key=lambda t: (-t[0], t[1]))
    return [v for _, v in keep]


def build_snapshot():
    breaker = {}
    t36, e36 = _fetch_paged("pricing", breaker)
    pdfs, ep = _fetch_paged("pdfs", breaker)
    if not t36:
        raise RuntimeError(f"no pricing rows from ep199 ({e36 or 'empty'})")
    t36d = _dedup_latest(t36)
    joined = _join(t36d)
    pmap = _pdf_map(pdfs)
    return {
        "as_of": datetime.datetime.utcnow().isoformat() + "Z",
        "fields": FIELDS,
        "rows": [[r[f] for f in FIELDS] for r in joined],
        "pdfs": pmap,
        "meta": {
            "errors": [x for x in (f"pricing:{e36}" if e36 else "", f"pdfs:{ep}" if ep else "") if x],
            "counts": {"t36_raw": len(t36), "t36_deduped": len(t36d),
                       "venues": len({r["vendor_id"] for r in joined}), "joined": len(joined),
                       "pdf_vendors": len(pmap)},
            "states": sorted({r["state"] for r in joined if r["state"]}),
            "venue_types": sorted({r["venue_type"] for r in joined if r["venue_type"]}),
            "vibes": _vibe_options(joined),
            "years": sorted({r["year"] for r in joined if r["year"]}),
        },
    }


# ── AI summary: on demand (a button), not on every filter change like the Streamlit tab ──────
# Sonnet 5.5 per the owner's "Sonnet 5.5 for everything" rule (the old tab named a retired model).
INSIGHT_MODEL = os.environ.get("ADMIN_INSIGHT_MODEL", "claude-sonnet-5-5")


def insight(s):
    """s = summary the page computed from the filtered venues (same fields the old prompt used)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set on the admin API service.")
    from anthropic import Anthropic
    c, e, t = s.get("cheapest", {}), s.get("expensive", {}), s.get("top_quoted", {})
    row = lambda v: f"{v.get('Venue')} ({v.get('State')}, {v.get('Type')}) @ ${float(v.get('Estimate') or 0):.0f} ({v.get('Quotes')} PDFs)"
    prompt = f"""You are analyzing venue pricing data from Tulle Together (a wedding vendor pricing platform).

Data summary ({int(s.get('n') or 0)} venues, {int(s.get('guests') or 0)} guests):
- Pricing mode: {s.get('mode')}
- Average estimate: ${float(s.get('avg') or 0):.0f}, std dev: ${float(s.get('std') or 0):.0f}
- Range: ${float(s.get('min') or 0):.0f} – ${float(s.get('max') or 0):.0f}
- States represented: {', '.join(s.get('states') or []) or 'N/A'}
- Venue types: {', '.join(s.get('types') or []) or 'N/A'}

Notable outliers:
- Cheapest: {row(c)}
- Most expensive: {row(e)}
- Most documented: {t.get('Venue')} ({t.get('State')}, {t.get('Type')}) with {t.get('Quotes')} pricing PDFs

Provide a VERY SHORT (1–2 sentences max) insight: identify one striking pattern, anomaly, or trend.
Focus on: extreme price gaps, unusual F&B/admin ratios, sparse data coverage, or state-level quirks.
Ignore missing/null fields. If nothing stands out, say "No major anomalies detected."
Do NOT explain what we did. Format as plain text, no markdown."""
    resp = Anthropic(api_key=key).messages.create(model=INSIGHT_MODEL, max_tokens=150,
                                                  messages=[{"role": "user", "content": prompt}])
    return {"text": resp.content[0].text.strip(), "model": INSIGHT_MODEL}
