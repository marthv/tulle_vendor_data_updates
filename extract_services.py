"""
extract_services.py - pricing extraction for catering, beauty and wedding-planner PDFs
--------------------------------------------------------------------------------------
These three categories are dropped by extract_core (SUPPORTED_CATEGORIES = Venue,
Photography, Entertainment), so the Planner and "Plan my wedding" have no prices for them.

One schema for all three: a quote object (one per PDF) plus line items, each with an
item_key from a fixed per-category list so code can roll items up without reading prose.
Like extract_extras (florals), results are meant for their OWN tables, never table 36
(fn21 trigger -> ep119 filter grid).

PILOT STATUS (2026-11-18): no Xano writes exist in this module. `pilot` runs direct
(non-batch) calls for a list of PDFs on one or more models and writes raw JSON outcomes
locally for hand-checking. Full runs are NOT approved.

CLI:
  python extract_services.py pilot --rows rows.json --out-dir DIR --pdf-dir DIR
      [--models claude-sonnet-5-5,claude-haiku-5-5] [--env-file F] [--workers 4]
Env: ANTHROPIC_API_KEY, GOOGLE_SERVICE_ACCOUNT_JSON (only for PDFs not already in --pdf-dir)
"""

import base64
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

CATEGORIES = ("catering", "beauty", "planner")

PRICES = {  # USD per token, standard (non-batch) rate, prompts <= 100K tokens
    "claude-sonnet-5-5": (2.00e-6, 10.00e-6),
    "claude-haiku-5-5": (0.10e-6, 0.50e-6),
}

ITEM_KEYS = {
    "catering": {
        "package_per_person": "a full-meal / wedding package priced per guest",
        "entree_per_person": "a single entree or meal course priced per guest",
        "appetizers": "passed or stationary hors d'oeuvres, cocktail hour food",
        "stations": "food stations (carving, pasta, taco, raw bar)",
        "dessert": "dessert, dessert table, wedding cake",
        "late_night": "late-night snack",
        "bar_package": "a bar / beverage package (open bar, beer & wine)",
        "bar_consumption": "bar billed on consumption, per drink, bartender-only setups",
        "nonalcoholic_beverage": "coffee, soda, juice, non-alcoholic beverage service",
        "staffing": "servers, captains, bartenders, chefs - labor charges",
        "rentals": "linens, china, glassware, tables, chairs, tents",
        "cake_cutting": "cake-cutting fee",
        "tasting": "tasting fee",
        "delivery_travel": "delivery, travel or mileage fee",
        "service_charge": "service charge / admin fee",
        "gratuity": "gratuity / tip",
        "tax": "sales tax",
        "minimum": "food & beverage minimum or minimum order",
        "menu_item": "an a-la-carte menu item that fits nothing above",
        "other": "anything else",
    },
    "beauty": {
        "bride_hair_makeup": "bridal hair AND makeup together",
        "bride_hair": "bridal hair only",
        "bride_makeup": "bridal makeup only",
        "bride_trial": "bridal trial (hair, makeup or both)",
        "party_hair_makeup": "bridesmaid / family / guest hair AND makeup",
        "party_hair": "bridesmaid / family / guest hair only",
        "party_makeup": "bridesmaid / family / guest makeup only",
        "child_service": "flower girl / junior services",
        "airbrush": "airbrush makeup upgrade",
        "lashes": "false lashes / lash application",
        "extensions_styling": "extensions, veil placement, accessory styling",
        "touch_up": "on-site touch-up / stay-on time, hourly stay",
        "travel_fee": "travel / on-site / mileage fee",
        "early_start_fee": "early-morning start fee",
        "package": "a named bundle covering several services",
        "minimum": "minimum spend or minimum number of services/heads",
        "service_charge": "service charge / gratuity / admin fee",
        "other": "anything else",
    },
    "planner": {
        "full_planning": "full-service planning (from engagement / 12+ months out)",
        "partial_planning": "partial planning (joins mid-way, a few months out)",
        "month_of": "month-of / wedding management (starts ~4-8 weeks out)",
        "day_of": "day-of coordination only",
        "design_only": "design / styling only",
        "destination": "destination wedding planning",
        "elopement": "elopement / micro-wedding planning",
        "hourly": "hourly consulting / coordination rate",
        "rehearsal": "rehearsal coordination add-on",
        "assistant": "additional assistant / coordinator",
        "travel": "travel / accommodation fee",
        "package": "a named package that fits nothing above",
        "add_on": "any other add-on service",
        "percentage_fee": "fee stated as a % of the wedding budget",
        "other": "anything else",
    },
}

UNITS = ["flat", "per_person", "per_hour", "per_service", "per_item", "per_dozen", "per_staff",
         "percent", "unknown"]
INCLUSION = ["included", "optional", "unknown"]
CONF = ["high", "medium", "low"]
SERVICE_STYLE = ["plated", "buffet", "family_style", "stations", "cocktail_reception",
                 "drop_off", "mixed", "not_applicable", "unknown"]


def _s():
    return {"type": "string"}


def _e(v):
    return {"type": "string", "enum": list(v)}


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


QUOTE_FIELDS = [
    "vendor_name", "pricing_year", "currency", "minimum_spend", "minimum_spend_conditions",
    "guest_minimum", "service_charge_pct", "gratuity_pct", "tax_rate_pct", "travel_fee_text",
    "deposit_text", "typical_spend_text", "typical_spend_low", "typical_spend_high",
    "total", "total_notes", "contact_email", "confidentiality_evidence",
]
ITEM_FIELDS = ["item_name", "price", "price_low", "price_high", "hours", "min_quantity",
               "included_text", "notes"]


def schema_for(cat):
    return _obj({
        "vendor_type": _e([cat, "other"]),
        "actual_category": _s(),
        "document_type": _e(["price_list", "packages_brochure", "proposal_or_quote",
                             "menu", "contract", "other"]),
        "quote": _obj({**{f: _s() for f in QUOTE_FIELDS},
                       "confidentiality_risk": _e(["yes", "no"]),
                       "confidence": _e(CONF)}),
        "items": {"type": "array", "items": _obj({
            "item_key": _e(list(ITEM_KEYS[cat])),
            **{f: _s() for f in ITEM_FIELDS},
            "service_style": _e(SERVICE_STYLE),
            "starting_at": _e(["yes", "no"]),
            "pricing_unit": _e(UNITS),
            "inclusion": _e(INCLUSION),
            "confidence": _e(CONF),
        })},
    })


_LABEL = {"catering": "WEDDING CATERER (food and/or bar service)",
          "beauty": "WEDDING HAIR & MAKEUP artist or salon",
          "planner": "WEDDING PLANNER / coordinator"}

_CAT_HINTS = {
    "catering": ("- service_style: plated | buffet | family_style | stations | cocktail_reception "
                 "| drop_off | mixed when the line names one, else not_applicable (fees, rentals) or unknown.\n"
                 "- hours: bar packages and staff charges often state hours - copy the number.\n"
                 "- min_quantity: a guest minimum attached to the line (\"minimum 100 guests\")."),
    "beauty": ("- service_style is always not_applicable.\n"
               "- min_quantity: a minimum number of services/heads attached to the line.\n"
               "- hours: for touch-up / stay-on time."),
    "planner": ("- service_style is always not_applicable.\n"
                "- hours: hours of coverage on the wedding day or consulting hours included.\n"
                "- included_text: what the package covers (vendor referrals, timeline, number of "
                "meetings, assistants on the day) - short, verbatim-ish."),
}


def prompt_for(cat):
    keys = "\n".join(f"  {k}: {d}" for k, d in ITEM_KEYS[cat].items())
    return f"""You extract {_LABEL[cat]} pricing from a PDF (a price list, package brochure, proposal/quote, menu or contract). Your output is parsed by code into a database couples use to budget their wedding, so exact numbers matter more than coverage of prose.

STEP 1 - VENDOR TYPE. "{cat}" if this business sells that service. "other" for anything else (venue, photographer, florist, rentals-only, a venue's in-house catering menu is still "{cat}" if it is a catering price list). If "other": set actual_category to one word, document_type "other", every quote string "" (confidentiality_risk "no", confidence "low") and items []. Stop.

STEP 2 - ITEMS. One entry per priced or listed line, in document order. Read every page including tables, sidebars and fine print. Do not merge different lines; do not split one line into several. Packages offered at several guest counts or tiers are separate items.
- item_key - choose the closest:
{keys}
- item_name: the vendor's own wording, verbatim (short).
- price: the unit price as a plain number ("85", not "$85"). A printed range goes in price_low / price_high with price "". One number -> price_low and price_high "".
- starting_at: "yes" when the price is a minimum ("starting at", "from", "+"), else "no".
- pricing_unit: flat | per_person (per GUEST / per head of the wedding guest count) | per_hour | per_service (per person serviced, e.g. per bridesmaid) | per_item | per_dozen | per_staff (per server / bartender / staff member) | percent (a % fee - price then holds the percent number, never a dollar amount) | unknown.
  A fee line that prints a DOLLAR amount (gratuity $2,161, tax $6,192.20) is "flat" with that amount; put its percent, if printed, in notes and in the quote fields. When a line is ambiguous between per guest and per staff member ("we suggest $50 per person for the staff"), use per_staff and say so in notes - never turn a staff tip into a per-guest price.
{_CAT_HINTS[cat]}
- inclusion: "included" when the line is part of a package or quoted order; "optional" for add-ons/upgrades; "unknown" for a plain price list.
- FEES are items too (service charge, gratuity, tax, travel, early start, cake cutting): give each its item_key and unit, and also copy them to the quote fields.
- notes: anything a budget needs about that line (seasonal, weekday only, "plus tax"), else "".
- confidence: high when the number is printed next to the item; medium when you associated a price across layout; low when inferred.
- NEVER invent a price. A listed item with no price gets price/price_low/price_high "".

STEP 3 - QUOTE (one object for the whole PDF):
- vendor_name; pricing_year: 4 digits, the year the prices APPLY to (the wedding / event year or the printed "2026 pricing"), NOT the date the quote or email was written; "" if no year is printed or clearly implied by an event date. currency (ISO, default "USD").
- minimum_spend: a minimum order / minimum spend as a plain number (a "minimum package" counts); minimum_spend_conditions: what it depends on (day, season, guest count), else "". A booking fee, retainer or deposit is NOT a minimum - it goes in deposit_text.
- guest_minimum: ONLY a hard minimum the PDF states ("minimum 100 guests", "6 services minimum per artist"). A surcharge below some count ("under 100 guests add 10%") is NOT a minimum: leave guest_minimum "" and record the surcharge in minimum_spend_conditions and as an item.
- service_charge_pct, gratuity_pct, tax_rate_pct: percent numbers as printed. An operations / admin / production fee stated as a % counts as service_charge_pct. A credit-card processing fee is not a service charge (notes only). travel_fee_text and deposit_text: verbatim wording, else "".
- typical_spend_text: a statement of what couples TYPICALLY spend in total ("most couples invest $4,000-$7,000"), verbatim, else "". typical_spend_low / typical_spend_high: its numbers (one number -> both). Only from such a printed statement; a minimum, a package price or anything you would add up is NOT typical spend.
- total and total_notes: ONLY the vendor's own printed GRAND total for the whole order, including its fees and tax as printed. A subtotal, the low end of a range, or an "approximate estimate" for part of the services is NOT a total: leave total "" and describe it in total_notes.
- Packages offered in tiers, artist levels or named levels (Gold / Platinum / Owner): put the tier name in item_name so each price is distinguishable.
- contact_email: the vendor's business email. confidentiality_risk "yes" only if the PDF says its pricing is confidential / not to be shared; confidentiality_evidence: that sentence (max 200 chars).
- confidence: overall confidence in the quote fields.

NUMBER RULES: plain numbers only - no $, commas, % or words. "" when not stated. Never compute values the PDF does not print."""


def build_params(cat, model, pdf_b64, pdf_id, vendor_name, effort="low"):
    return {
        "model": model,
        "max_tokens": 16000,
        "system": prompt_for(cat),
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort,
                          "format": {"type": "json_schema", "schema": schema_for(cat)}},
        "messages": [{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": pdf_b64}},
            {"type": "text", "text": f'PDF_ID="{pdf_id}", vendor="{vendor_name}". '
                                     'Extract every priced line item and the quote fields.'},
        ]}],
    }


def _text(msg):
    for b in msg.content or []:
        if getattr(b, "type", None) == "text":
            return b.text
    return ""


def cost_of(model, usage):
    pin, pout = PRICES.get(model, (0, 0))
    return usage["input"] * pin + usage["output"] * pout


def run_one(client, cat, model, pdf_bytes, row):
    params = build_params(cat, model, base64.b64encode(pdf_bytes).decode(), row["PDF_ID"],
                          row.get("Name", ""))
    t0 = time.time()
    last = None
    for attempt in range(3):
        try:
            msg = client.messages.create(**params)
            break
        except Exception as e:  # retries are already done by the SDK for 429/5xx
            last = e
            time.sleep(3 * (attempt + 1))
    else:
        return {"status": "failed", "error": str(last)[:300]}
    usage = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
    out = {"usage": usage, "cost_usd": round(cost_of(model, usage), 5),
           "stop_reason": msg.stop_reason, "seconds": round(time.time() - t0, 1)}
    if msg.stop_reason in ("refusal", "max_tokens"):
        return {**out, "status": "failed", "error": f"stop_reason={msg.stop_reason}"}
    try:
        parsed = json.loads(_text(msg))
    except Exception:
        return {**out, "status": "failed", "error": "json parse failed"}
    status = "wrong_category" if parsed.get("vendor_type") == "other" else "ok"
    return {**out, "status": status, "raw": parsed}


def load_pdf(row, pdf_dir, drive_holder):
    p = os.path.join(pdf_dir, f"{row['PDF_ID']}.pdf")
    if os.path.exists(p):
        with open(p, "rb") as fh:
            return fh.read(), None
    if drive_holder.get("svc") is None:
        from extract_core import get_drive_service
        drive_holder["svc"] = get_drive_service()
    from extract_core import download_pdf
    data, err = download_pdf(row["PDF_Link"], drive_holder["svc"])
    if data:
        os.makedirs(pdf_dir, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    return data, err


def pilot(rows, out_dir, pdf_dir, models, workers=4, log=print):
    import anthropic
    client = anthropic.Anthropic()
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "outcomes.jsonl")
    done = set()
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("status") != "failed":
                    done.add((r["PDF_ID"], r["model"]))
    drive = {}
    pdfs = {}
    for row in rows:  # downloads are sequential: the Drive client is not thread-safe
        data, err = load_pdf(row, pdf_dir, drive)
        if not data:
            log(f"  {row['PDF_ID']} download failed: {err}")
            with open(out_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"PDF_ID": row["PDF_ID"], "cat": row["cat"], "model": "-",
                                     "status": "download_failed", "error": str(err)[:200]}) + "\n")
            continue
        if len(data) > 30 * 1024 * 1024:
            log(f"  {row['PDF_ID']} too large ({len(data)/1e6:.0f}MB) - skipped")
            continue
        pdfs[row["PDF_ID"]] = data
    jobs = [(row, m) for row in rows if row["PDF_ID"] in pdfs for m in models
            if (row["PDF_ID"], m) not in done]
    log(f"{len(pdfs)} PDFs ready, {len(jobs)} calls to make")
    total = 0.0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_one, client, row["cat"], m, pdfs[row["PDF_ID"]], row): (row, m)
                for row, m in jobs}
        for f in as_completed(futs):
            row, m = futs[f]
            res = f.result()
            total += res.get("cost_usd", 0)
            rec = {"PDF_ID": row["PDF_ID"], "Vendor_ID": row["Vendor_ID"], "Name": row["Name"],
                   "cat": row["cat"], "model": m, "bytes": len(pdfs[row["PDF_ID"]]), **res}
            with open(out_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n = len((res.get("raw") or {}).get("items") or [])
            log(f"  {row['cat']:8} {row['PDF_ID']:>8} {m:18} {res['status']:14} items={n:3} "
                f"${res.get('cost_usd', 0):.4f}  running ${total:.2f}")
    return total


# ── BATCH (50% price) + INGEST into Xano tables 92 / 93 ───────────────────────
TAG = {"catering": "c", "beauty": "b", "planner": "p"}
CAT_BY_TAG = {v: k for k, v in TAG.items()}
XANO_BASE = "https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B/pdfdata/"
BATCH_MODEL = "claude-sonnet-5-5"


def submit_batch(rows, pdf_dir, model=BATCH_MODEL, log=print):
    """Upload each PDF to the Files API and submit one Batch API job. Returns (batch_id, pdf_map)."""
    import anthropic
    from datetime import datetime, timezone
    from extract_extras import _upload
    client = anthropic.Anthropic()
    drive, reqs, pm = {}, [], {}
    ts = datetime.now(timezone.utc).isoformat()
    for i, row in enumerate(rows):
        pid = row["PDF_ID"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", pid):
            log(f"  skip {pid!r}: bad custom_id")
            continue
        data, err = load_pdf(row, pdf_dir, drive)
        if not data:
            log(f"  [{i+1}/{len(rows)}] {pid} download failed: {err}")
            continue
        if len(data) > 30 * 1024 * 1024:
            log(f"  [{i+1}/{len(rows)}] {pid} too large - skipped")
            continue
        fid = _upload(client, data, f"{pid}.pdf")
        p = build_params(row["cat"], model, "", pid, row.get("Name", ""))
        p["messages"][0]["content"][0] = {"type": "document",
                                          "source": {"type": "file", "file_id": fid}}
        reqs.append({"custom_id": f"{pid}__{TAG[row['cat']]}", "params": p})
        pm[pid] = {"PDF_ID": pid, "Vendor_ID": row["Vendor_ID"], "Name": row.get("Name", ""),
                   "state": row.get("state", ""), "cat": row["cat"], "file_id": fid,
                   "model": model, "timestamp": ts}
        log(f"  [{i+1}/{len(rows)}] {pid} uploaded")
    b = client.messages.batches.create(requests=reqs)
    log(f"batch {b.id}: {len(reqs)} requests")
    return b.id, pm


def fetch_batch(batch_id, pdf_map):
    """Ended batch -> {pdf_id: outcome} using the same outcome shape as run_one."""
    import anthropic
    client = anthropic.Anthropic()
    b = client.messages.batches.retrieve(batch_id)
    if b.processing_status != "ended":
        raise RuntimeError(f"batch {batch_id} is {b.processing_status} {b.request_counts}")
    out = {}
    for r in client.messages.batches.results(batch_id):
        pid, tag = r.custom_id.rsplit("__", 1)
        meta = pdf_map.get(pid, {})
        if r.result.type != "succeeded":
            out[pid] = {"status": "failed", "error": str(getattr(r.result, "error", r.result.type))[:300]}
            continue
        msg = r.result.message
        usage = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
        oc = {"usage": usage, "cost_usd": round(cost_of(meta.get("model", BATCH_MODEL), usage) * 0.5, 5),
              "stop_reason": msg.stop_reason}
        if msg.stop_reason in ("refusal", "max_tokens"):
            out[pid] = {**oc, "status": "failed", "error": f"stop_reason={msg.stop_reason}"}
            continue
        try:
            parsed = json.loads(_text(msg))
        except Exception:
            out[pid] = {**oc, "status": "failed", "error": "json parse failed"}
            continue
        out[pid] = {**oc, "status": "wrong_category" if parsed.get("vendor_type") == "other" else "ok",
                    "raw": parsed}
    return out


def _num(v):
    s = str(v if v is not None else "").strip()
    s = re.sub(r"[$€£%\s,]|usd", "", s, flags=re.I)
    if not s or re.search(r"\d-\d", s):
        return None
    try:
        return float(s)
    except ValueError:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)[kK]", s)
        return float(m.group(1)) * 1000 if m else None


def _int(v):
    n = _num(v)
    return int(round(n)) if n is not None else None


def _t(v, limit=2000):
    return str(v or "").strip()[:limit]


def to_rows(meta, raw):
    """Model output -> (quote_row, item_rows) keyed by table 92 / 93 column names."""
    q = raw.get("quote") or {}
    base = {"PDF_ID": meta["PDF_ID"], "Vendor_ID": meta["Vendor_ID"], "category": meta["cat"]}
    yr = _int(q.get("pricing_year"))
    quote = {**base,
             "vendor_name": _t(q.get("vendor_name")) or _t(meta.get("Name")),
             "state": _t(meta.get("state")),
             "document_type": _t(raw.get("document_type")),
             "pricing_year": yr if yr and 2000 <= yr <= 2035 else None,
             "currency": _t(q.get("currency")) or "USD",
             "minimum_spend": _num(q.get("minimum_spend")),
             "minimum_spend_conditions": _t(q.get("minimum_spend_conditions")),
             "guest_minimum": _int(q.get("guest_minimum")),
             "service_charge_pct": _num(q.get("service_charge_pct")),
             "gratuity_pct": _num(q.get("gratuity_pct")),
             "tax_rate_pct": _num(q.get("tax_rate_pct")),
             "travel_fee_text": _t(q.get("travel_fee_text")),
             "deposit_text": _t(q.get("deposit_text")),
             "typical_spend_text": _t(q.get("typical_spend_text")),
             "typical_spend_low": _num(q.get("typical_spend_low")),
             "typical_spend_high": _num(q.get("typical_spend_high")),
             "total": _num(q.get("total")),
             "total_notes": _t(q.get("total_notes")),
             "contact_email": _t(q.get("contact_email"), 200),
             "confidentiality_risk": _t(q.get("confidentiality_risk")),
             "confidentiality_evidence": _t(q.get("confidentiality_evidence"), 200),
             "confidence": _t(q.get("confidence")),
             "model": meta.get("model", BATCH_MODEL),
             "extracted_at": meta.get("timestamp"),
             "raw_json": raw}
    items = []
    for it in raw.get("items") or []:
        p, lo, hi = _num(it.get("price")), _num(it.get("price_low")), _num(it.get("price_high"))
        if p is not None and lo is None and hi is None:
            lo = hi = p
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        items.append({**base,
                      "item_key": _t(it.get("item_key")),
                      "item_name": _t(it.get("item_name"), 300),
                      "price_low": lo, "price_high": hi,
                      "is_starting_at": it.get("starting_at") == "yes",
                      "pricing_unit": _t(it.get("pricing_unit")),
                      "service_style": "" if it.get("service_style") in ("not_applicable", None)
                      else _t(it.get("service_style")),
                      "hours": _num(it.get("hours")),
                      "min_quantity": _num(it.get("min_quantity")),
                      "inclusion": _t(it.get("inclusion")),
                      "included_text": _t(it.get("included_text"), 1000),
                      "notes": _t(it.get("notes"), 1000),
                      "confidence": _t(it.get("confidence"))})
    return quote, items


def _xano(method, table, secret, body=None, q=None):
    import urllib.request
    import urllib.parse
    qs = urllib.parse.urlencode({**(q or {}), **({"secret": secret} if method == "DELETE" else {})})
    url = XANO_BASE + table + (f"?{qs}" if qs else "")
    data = json.dumps({**body, "secret": secret}).encode() if body is not None else None
    for a in range(4):
        try:
            req = urllib.request.Request(url, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"null")
        except Exception as e:
            if a == 3:
                raise
            time.sleep(2 * (a + 1))


def ingest(outcomes, pdf_map, secret, log=print):
    """Purge-then-insert per PDF. Only status ok rows are written."""
    stats = {"pdfs": 0, "items": 0, "skipped": 0}
    for pid, oc in outcomes.items():
        if oc.get("status") != "ok":
            stats["skipped"] += 1
            continue
        quote, items = to_rows(pdf_map[pid], oc["raw"])
        for t in ("vendor_price_items", "vendor_price_quotes"):
            _xano("DELETE", t, secret, q={"pdf_id": pid})
        _xano("POST", "vendor_price_quotes", secret, body={"rows": [quote]})
        for i in range(0, len(items), 200):
            r = _xano("POST", "vendor_price_items", secret, body={"rows": items[i:i + 200]})
            if (r or {}).get("inserted") != len(items[i:i + 200]):
                raise RuntimeError(f"{pid}: items insert returned {r}")
        stats["pdfs"] += 1
        stats["items"] += len(items)
    log(json.dumps(stats))
    return stats


def load_env_file(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pilot")
    p.add_argument("--rows", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--pdf-dir", required=True)
    p.add_argument("--models", default="claude-sonnet-5-5,claude-haiku-5-5")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    s = sub.add_parser("submit")
    s.add_argument("--rows", required=True)
    s.add_argument("--pdf-dir", required=True)
    s.add_argument("--map-out", required=True)
    st = sub.add_parser("status")
    st.add_argument("batch_id")
    ig = sub.add_parser("ingest")
    ig.add_argument("--map", required=True)
    ig.add_argument("--out-dir", required=True)
    ig.add_argument("--secret-env-file", required=True)
    ig.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.env_file:
        load_env_file(a.env_file)
    if a.cmd == "submit":
        with open(a.rows, encoding="utf-8") as fh:
            rows = json.load(fh)
        bid, pm = submit_batch(rows, a.pdf_dir)
        with open(a.map_out, "w", encoding="utf-8") as fh:
            json.dump({"batch_id": bid, "pdf_map": pm}, fh, indent=1)
        print(bid)
        return 0
    if a.cmd == "status":
        import anthropic
        b = anthropic.Anthropic().messages.batches.retrieve(a.batch_id)
        print(b.processing_status, b.request_counts)
        return 0
    if a.cmd == "ingest":
        with open(a.map, encoding="utf-8") as fh:
            m = json.load(fh)
        outcomes = fetch_batch(m["batch_id"], m["pdf_map"])
        os.makedirs(a.out_dir, exist_ok=True)
        with open(os.path.join(a.out_dir, f"outcomes_{m['batch_id']}.json"), "w", encoding="utf-8") as fh:
            json.dump(outcomes, fh, ensure_ascii=False)
        cnt = {}
        for o in outcomes.values():
            cnt[o["status"]] = cnt.get(o["status"], 0) + 1
        cost = sum(o.get("cost_usd", 0) for o in outcomes.values())
        print(f"outcomes {cnt}  batch cost ${cost:.2f}")
        if a.dry_run:
            return 0
        env = {}
        with open(a.secret_env_file, encoding="utf-8") as fh:
            for line in fh:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k] = v.strip().strip('"').strip("'")
        ingest(outcomes, m["pdf_map"], env["XANO_PDFDATA_SECRET"])
        return 0
    with open(a.rows, encoding="utf-8") as fh:
        rows = json.load(fh)
    if a.limit:
        rows = rows[:a.limit]
    total = pilot(rows, a.out_dir, a.pdf_dir, [m.strip() for m in a.models.split(",")], a.workers)
    print(f"TOTAL ${total:.2f}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
