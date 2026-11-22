"""
extract_venue_v3.py - v3 VENUE pricing extraction (item rows + packages + minimums + fees)
-----------------------------------------------------------------------------------------
Why: the 2026-11-22 extraction audit (30 PDFs) found the venue grid schema in extract_core
(SUMMARY_PROMPT + STRUCTURE/PRICING 3-pass grid -> t36/t37/t62) stores only ~45% of venue
price facts, while the item-row schemas (extract_services / extract_extras -> t93/t85) store
94-100%. Top venue misses: in-house menu/bar per-guest lines, package bundles and "typical
total" lines, add-on fees, overtime, day/time-slot grids, deposits/policies. Most invented
values came from the grid schema forcing data into fields that do not fit (Months = Jan-Dec
when the PDF has no seasons, Friday prices in "Saturday" fields, optional bar summed into a
per-person price, admission stored as an F&B minimum, capacity stored as a guest minimum).

What this module is:
  * PROMPT + JSON SCHEMA (structured outputs) for one call per PDF.
  * build_params(): one Messages request (usable directly or inside a Batch).
  * parse_message(): message -> (status, parsed, usage).
  * full_text(): the document's text layer (pypdf, free) merged with the model's
    transcription of image-only pages, so a future new field needs no PDF re-read.

What it is NOT: it is not wired into extract_core / batch_worker / ingest, and it never
writes to Xano. The production path is unchanged. Rollout needs an adapter (see the
"Rollout notes" in the 2026-11-22 v3 results.md) before any table is fed from it.

CLI (pilot only, no Xano):
  python extract_venue_v3.py show-prompt            # print prompt
  python extract_venue_v3.py show-schema            # print JSON schema
"""

import json
import re
import sys

MODEL = "claude-sonnet-5-5"
PRICES = {"claude-sonnet-5-5": (2.00e-6, 10.00e-6)}  # USD/token, standard rate; batch = 50%

# ── vocab ────────────────────────────────────────────────────────────────────
VENDOR_TYPES = ["venue", "caterer", "photographer", "videographer", "florist", "band_dj",
                "beauty", "planner", "rentals", "other_wedding_vendor", "not_wedding"]
ITEM_TYPES = {
    "venue_rental": "rent for a space / site fee / room fee / buyout (NOT a package that bundles food)",
    "food_per_person": "a menu, entree, meal tier or buffet priced per guest",
    "appetizer": "hors d'oeuvres, cocktail-hour food, displays",
    "station": "food stations (carving, pasta, raw bar, taco)",
    "dessert_cake": "dessert, dessert table, wedding cake",
    "late_night": "late-night snack",
    "kids_meal": "children's meal",
    "vendor_meal": "vendor / crew meal",
    "bar_package": "a bar / beverage package (open bar, beer & wine, hosted bar), any unit",
    "bar_consumption": "bar billed per drink / on consumption / cash bar prices",
    "nonalcoholic_beverage": "coffee, soda, juice, champagne toast, non-alcoholic service",
    "menu_item": "any other a-la-carte food/drink line",
    "lodging": "rooms / overnight stay / suite",
    "rental_equipment": "linens, chairs, tables, tent, AV, lighting, decor rentals",
    "service_package": "a non-venue service package (photo/video/DJ/florals/beauty/planning)",
    "service_item": "a non-venue a-la-carte service line or add-on",
    "other_item": "anything priced that fits nothing above",
}
FEE_KINDS = {
    "service_charge": "service charge / production fee stated as a % or amount",
    "admin_fee": "administrative fee",
    "gratuity": "gratuity / tip",
    "tax": "sales / occupancy tax",
    "ceremony_fee": "ceremony fee / ceremony site fee (per space if the PDF splits it)",
    "overtime": "additional hour / overtime / extension charge",
    "coordination": "coordinator / day-of coordination / planning fee",
    "staffing": "bartender, attendant, chef, server, security, valet, police labour fees",
    "cleaning": "cleaning / trash / damage-waiver fee",
    "setup_early_access": "early access, decorating time, rehearsal access, setup/breakdown",
    "outside_vendor": "fee for an outside caterer / bar / vendor, bring-your-own fee",
    "cake_cutting_corkage": "cake-cutting or corkage fee",
    "removal_storage": "removal / storage of venue furniture when outside rentals are used",
    "travel_delivery": "travel, delivery or mileage",
    "deposit_retainer": "a deposit / retainer / booking fee stated as a fee line",
    "other_fee": "any other fee",
}
UNITS = ["flat", "per_person", "per_hour", "per_item", "per_table", "per_staff", "per_night",
         "per_drink", "percent", "other"]
MANDATORY = ["mandatory", "optional", "unknown"]
TRI = ["yes", "no", "unknown"]
MIN_TYPES = ["fb_minimum", "event_minimum", "guest_count_minimum", "admission_or_donation",
             "other_minimum"]


# ── schema helpers ───────────────────────────────────────────────────────────
def _s():
    return {"type": "string"}


# Structured outputs allow at most 16 union-typed (nullable) parameters per schema, so
# "not stated" is the empty string "" on the wire and normalize() turns it into None.
# _nn / _ni fields carry a plain number as a string ("8500", "22.5") or "".
def _ns():
    return {"type": "string"}


_nn = _ni = _ns


def _e(v):
    return {"type": "string", "enum": list(v)}


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


def _arr(props):
    return {"type": "array", "items": _obj(props)}


# condition columns shared by every priced row
_WHEN = {
    "space": _ns(),
    "day_of_week": _ns(),
    "season_months": _ns(),
    "year": _ns(),
    "time_window": _ns(),
    "guest_min": _ni(),
    "guest_max": _ni(),
}


def schema():
    return _obj({
        "is_wedding_document": {"type": "boolean"},
        "gate_reason": _s(),
        "vendor_type": _e(VENDOR_TYPES),
        "vendor_name": _ns(),
        "currency": _ns(),
        "pricing_years": _ns(),
        "pricing_model_summary": _s(),
        "spaces": _arr({
            "space_name": _s(),
            "capacity_seated": _ni(),
            "capacity_max": _ni(),
            "capacity_text": _ns(),
            "page": _ni(),
        }),
        "items": _arr({
            "item_type": _e(ITEM_TYPES),
            "item_name": _s(),
            "price_low": _nn(),
            "price_high": _nn(),
            "unit": _e(UNITS),
            "currency": _ns(),
            "starting_at": {"type": "boolean"},
            "min_quantity": _nn(),
            "min_quantity_unit": _ns(),
            "hours": _nn(),
            **_WHEN,
            "included_text": _ns(),
            "mandatory": _e(MANDATORY),
            "page": _ni(),
        }),
        "packages": _arr({
            "package_name": _s(),
            "price_low": _nn(),
            "price_high": _nn(),
            "price_unit": _e(["flat", "per_person", "other"]),
            "currency": _ns(),
            "starting_at": {"type": "boolean"},
            "included_guests": _ni(),
            "extra_guest_pp": _nn(),
            "hours_included": _nn(),
            "overtime_per_hour": _nn(),
            "includes_rental": _e(TRI),
            "includes_food": _e(TRI),
            "includes_bar": _e(TRI),
            "includes_ceremony": _e(TRI),
            "includes_lodging": _e(TRI),
            "includes_service_tax": _e(TRI),
            "is_all_in_total": _e(TRI),
            **_WHEN,
            "included_text": _ns(),
            "page": _ni(),
        }),
        "minimums": _arr({
            "min_type": _e(MIN_TYPES),
            "amount": _nn(),
            "unit": _e(["flat", "per_person", "guests"]),
            "currency": _ns(),
            "counts_toward": _ns(),
            "excludes": _ns(),
            "is_package": _e(TRI),
            **_WHEN,
            "page": _ni(),
        }),
        "fees": _arr({
            "fee_kind": _e(FEE_KINDS),
            "fee_name": _s(),
            "amount_low": _nn(),
            "amount_high": _nn(),
            "unit": _e(UNITS),
            "currency": _ns(),
            "mandatory": _e(MANDATORY),
            "applies_to": _ns(),
            **_WHEN,
            "page": _ni(),
        }),
        "typical_totals": _arr({
            "guest_count": _ni(),
            "total_low": _nn(),
            "total_high": _nn(),
            "unit": _e(["flat", "per_person"]),
            "currency": _ns(),
            "covers_text": _ns(),
            "page": _ni(),
        }),
        "policies": _obj({
            "outside_caterer_allowed": _e(TRI),
            "outside_caterer_fee": _nn(),
            "outside_caterer_fee_unit": _ns(),
            "outside_caterer_quote": _ns(),
            "end_time": _ns(),
            "end_time_quote": _ns(),
            "deposit_pct": _nn(),
            "deposit_amount": _nn(),
            "deposit_quote": _ns(),
            "payment_schedule": _ns(),
            "multi_day": _e(TRI),
            "multi_day_quote": _ns(),
            "other": _arr({"topic": _s(), "quote": _s(), "page": _ni()}),
        }),
        "image_page_text": _arr({"page": {"type": "integer"}, "text": _s()}),
    })


def _bullets(d):
    return "\n".join(f"    {k}: {v}" for k, v in d.items())


PROMPT = f"""You extract wedding PRICING from one vendor PDF (usually a venue: price list, brochure, proposal, menu or contract). Code parses your JSON into a database couples use to estimate what their wedding will cost, so exact numbers and honest blanks matter more than tidy summaries.

THE ONE RULE: record only what the PDF states. A field the PDF does not state is the empty string "" (or "unknown" for yes/no/unknown fields). Never fill a default, never compute a value the PDF does not print, never move a value into a field that does not describe it. A wrong number is worse than a missing one.

STEP 1 - GATE.
- is_wedding_document: true if this document prices weddings or events a couple could book (a venue, caterer, photographer, florist, band/DJ, beauty, planner, rentals...). Generic event / party venues that host weddings count. false for anything else: kids' birthday parties, trampoline/play parks, memberships, gyms, restaurants' everyday menus with no event offer, real-estate, tickets, unrelated businesses.
- gate_reason: one short sentence on why.
- vendor_type: what the business sells. "not_wedding" when the gate is false.
- If is_wedding_document is false: every array empty, every policy field ""/"unknown", and STOP.

STEP 2 - DOCUMENT FIELDS.
- vendor_name; currency: the ISO code of the printed prices (USD for $ in the US, CAD, GBP, EUR, AUD, MXN ...). If the PDF mixes currencies, give the main one here and set currency on each row. pricing_years: the year(s) the prices APPLY to as printed ("2025-2026"), else "". pricing_model_summary: 1-2 sentences in plain words.
- spaces: every named bookable space with its capacities exactly as printed. capacity_seated = seated/dinner/banquet capacity; capacity_max = the largest number printed (standing/cocktail/ceremony). A capacity is NEVER a guest minimum.

STEP 3 - ROWS. Read every page, including tables, sidebars, footnotes and fine print. One row per distinct printed price. Each different space, day of week, season/month range, year, time slot or guest-count band is a SEPARATE row - keep grids and tiered guest-count ladders as one row per cell/tier, never collapse them into "high/low". Conditions go in the condition columns:
  space ("" = whole venue / not stated), day_of_week (exactly as the PDF groups them: "Saturday", "Friday & Sunday", "Monday-Thursday", "weekday"), season_months (as printed: "May-October", "January, February, August"; "" if the PDF gives no season), year, time_window ("11:30am-2:30pm", "evening", "Friday 12pm - Sunday 12pm"), guest_min / guest_max (the guest band THIS price applies to).
  A Friday or Sunday price is a Friday or Sunday price - never label it Saturday. No season printed -> season_months "" (never "January-December").

Where each price goes:
  a) packages - any bundle sold as one price that combines several things (rental + food, rental + lodging, "all-inclusive", "elopement package", "wedding package", weekend packages). Fields: price_low/high + price_unit (flat or per_person); included_guests (guests covered by a flat price); extra_guest_pp (price per additional guest); hours_included; overtime_per_hour; includes_rental / includes_food / includes_bar / includes_ceremony / includes_lodging / includes_service_tax: "yes" only when the PDF says the package includes it, "no" when it says it is extra/excluded, else "unknown"; is_all_in_total "yes" only when the PDF presents the price as the whole cost (fees/tax included), "no" if fees/tax are added on top, else "unknown"; included_text: short summary of what is included.
     A package price is ONE row here. Do NOT also store it as a venue_rental item or as a minimum - that double-counts it.
  b) items - everything else bought by the couple, with item_type:
{_bullets(ITEM_TYPES)}
     price_low/price_high: one printed number -> both equal; a printed range -> low and high. unit: flat | per_person (per guest) | per_hour | per_item | per_table | per_staff | per_night | per_drink | percent | other. starting_at true for "from / starting at / +".
     EVERY per-guest menu, entree, meal tier, buffet, station, kids' meal and bar package line is its own item row, with its own price, even when there are many (a menu with 40 priced lines gets 40 rows). Entree choices at different prices are separate rows. Bar packages by hour (e.g. 3-hour / 4-hour, "each additional hour") are separate rows with hours set.
     min_quantity + min_quantity_unit: a minimum attached to that line ("minimum 50 guests", "2 dozen minimum"). hours: hours the line covers.
     mandatory: "mandatory" if the PDF says it is required, "optional" for add-ons/upgrades/enhancements, "unknown" for a plain price list.
     Never add an optional line into another price (a $155 menu and a $25 optional bar stay two rows, never $180).
  c) minimums - a spend or headcount the couple must reach: min_type fb_minimum (food & beverage minimum), event_minimum (total-spend minimum incl. rental or anything), guest_count_minimum (a minimum number of guests; unit "guests"), admission_or_donation (an admission charge, museum/garden donation or entrance fee that is not a food minimum), other_minimum. counts_toward: what spending counts toward it as printed ("food and beverage", "food, bar and rentals"); excludes: what does not count ("excludes tax and service charge"); is_package "yes" if the minimum is reached by buying a package.
  d) fees - charges added to the bill, with fee_kind:
{_bullets(FEE_KINDS)}
     amount_low/high + unit (percent for "22% service charge" -> 22), mandatory/optional/unknown, applies_to (what the fee is charged on or for, e.g. "food and beverage", "beach ceremony", "events over 150 guests"). A ceremony fee that differs by space is one row per space. Overtime ("$300 per additional hour") is a fee row with unit per_hour (also set overtime_per_hour on a package when it belongs to one).
  e) typical_totals - statements of what a wedding typically or estimatedly costs in total: "a 175-guest wedding typically ranges from $65,000-$80,000", "typical range $375-$450 per guest", a printed estimate/proposal total for N guests. guest_count ("" if not given), total_low/high, unit flat or per_person, covers_text. Also record printed sub-totals of an estimate here (covers_text says which part). Never compute one yourself.

STEP 4 - POLICIES (yes / no / unknown + the verbatim sentence, max 200 chars; "" quote when unknown):
- outside_caterer_allowed: "yes" if outside/your own caterer may be used (fee and unit if printed), "no" if all food & beverage must come from the venue or its exclusive caterer, else "unknown".
- end_time: the time events/music/bar must end ("10:00 pm"), with quote.
- deposit_pct / deposit_amount: the booking deposit or first installment; payment_schedule: the schedule in a few words ("50% to book, balance 30 days prior").
- multi_day: "yes" if multi-day / weekend rentals are offered.
- other: up to 8 other cost-relevant rules (cancellation, minimum hours, required insurance, required vendors, noise limits, vendor-meal requirements) as topic + verbatim quote.

STEP 5 - IMAGE PAGES. The user message lists pages that have NO text layer. For each listed page that shows any price, fee, package, menu, capacity or policy text, add an image_page_text entry with that page's pricing-relevant text transcribed verbatim (keep line order; skip pages that are only photos or decoration; never include personal names of individuals, emails or phone numbers - write [redacted]).

NUMBER RULES: number fields hold a plain number as a string - "8500", not "$8,500"; "22" for 22%; "$8.5K" -> "8500" - or "" when not stated. One number per field: a range goes in the _low and _high fields. Every priced row gets page (1-based) and currency. Never put a personal name, email or phone number anywhere.

OUTPUT: return ONLY one JSON object (no markdown, no prose) that matches this JSON schema exactly - every key present, enums spelled exactly, "" for anything not stated:
""" + json.dumps(schema(), separators=(",", ":"))


# ── request / response ───────────────────────────────────────────────────────
def build_params(pdf_id, vendor_name, image_pages, file_id=None, pdf_b64=None,
                 model=MODEL, max_tokens=32000, effort="medium"):
    if file_id:
        src = {"type": "file", "file_id": file_id}
    else:
        src = {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}
    pages = ", ".join(str(p) for p in image_pages) if image_pages else "none"
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": PROMPT,
        "thinking": {"type": "adaptive"},
        # Not output_config.format: this schema is over the structured-outputs limits
        # (tested 2026-11-22: 79 nullable params > 16, then "compiled grammar is too large").
        # The schema is in the prompt instead and validate() checks the reply in code.
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": [
            {"type": "document", "source": src},
            {"type": "text", "text": f'PDF_ID="{pdf_id}", vendor="{vendor_name}". '
                                     f'Pages with no text layer: {pages}. '
                                     'Extract every price, package, minimum, fee, typical total and policy.'},
        ]}],
    }


def usage_of(msg):
    u = msg.usage
    return {"input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}


def cost_usd(usage, model=MODEL, batch=False):
    pin, pout = PRICES.get(model, (0, 0))
    c = (usage["input_tokens"] * pin + usage["output_tokens"] * pout
         + usage.get("cache_read_input_tokens", 0) * pin * 0.1
         + usage.get("cache_creation_input_tokens", 0) * pin * 1.25)
    return c * (0.5 if batch else 1)


def parse_message(msg):
    """-> (status, parsed_or_None, usage). status: ok | not_wedding | failed:<why>"""
    usage = usage_of(msg)
    if msg.stop_reason in ("refusal", "max_tokens"):
        return f"failed:stop_reason={msg.stop_reason}", None, usage
    txt = "".join(b.text for b in (msg.content or []) if getattr(b, "type", None) == "text")
    try:
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", txt, re.S)
        d = json.loads(m.group(1) if m else txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception:
        return "failed:json", None, usage
    problems = validate(d)
    d = normalize(d)
    d["_schema_problems"] = problems
    return ("ok" if d.get("is_wedding_document") else "not_wedding"), d, usage


def validate(d, sch=None, path="$"):
    """Light JSON-schema check (types, required keys, enums). -> list of problems."""
    sch = sch or schema()
    out = []
    t = sch.get("type")
    if t == "object":
        if not isinstance(d, dict):
            return [f"{path}: not an object"]
        for k in sch["required"]:
            if k not in d:
                out.append(f"{path}.{k}: missing")
        for k, v in d.items():
            if k in sch["properties"]:
                out += validate(v, sch["properties"][k], f"{path}.{k}")
            else:
                out.append(f"{path}.{k}: unexpected key")
    elif t == "array":
        if not isinstance(d, list):
            return [f"{path}: not an array"]
        for i, x in enumerate(d):
            out += validate(x, sch["items"], f"{path}[{i}]")
    elif t == "string":
        if not isinstance(d, (str, int, float)) or isinstance(d, bool):
            if d is not None:
                out.append(f"{path}: not a string")
        elif "enum" in sch and d not in sch["enum"]:
            out.append(f"{path}: {d!r} not in enum")
    elif t == "boolean" and not isinstance(d, bool):
        out.append(f"{path}: not a boolean")
    elif t == "integer" and not isinstance(d, int):
        out.append(f"{path}: not an integer")
    return out


# ── normalize: "" -> None, numeric strings -> numbers ────────────────────────
_NUM_FIELDS = {"price_low", "price_high", "min_quantity", "hours", "extra_guest_pp",
               "hours_included", "overtime_per_hour", "amount", "amount_low", "amount_high",
               "total_low", "total_high", "outside_caterer_fee", "deposit_pct",
               "deposit_amount"}
_INT_FIELDS = {"capacity_seated", "capacity_max", "guest_min", "guest_max", "included_guests",
               "guest_count", "page"}


def _to_num(v):
    s = str(v).strip()
    s = re.sub(r"[$€£%\s,]|usd", "", s, flags=re.I)
    if not s:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)[kK]", s)
    if m:
        return float(m.group(1)) * 1000
    try:
        return float(s)
    except ValueError:
        return None


def normalize(d):
    """Model JSON -> same shape with "" -> None and number fields as int/float.
    Unparseable number strings become None and are listed in d['_unparsed']."""
    bad = []

    def fix(o, path=""):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    out[k] = fix(v, f"{path}.{k}")
                elif v is None or (isinstance(v, str) and v.strip() == ""):
                    out[k] = None
                elif k in _NUM_FIELDS or k in _INT_FIELDS:
                    n = v if isinstance(v, (int, float)) and not isinstance(v, bool) else _to_num(v)
                    if n is None:
                        bad.append(f"{path}.{k}={v!r}")
                    elif k in _INT_FIELDS:
                        n = int(round(n))
                    out[k] = n
                else:
                    out[k] = v
            return out
        if isinstance(o, list):
            return [fix(x, f"{path}[{i}]") for i, x in enumerate(o)]
        return o

    r = fix(d)
    r["_unparsed"] = bad
    return r


# ── full text ────────────────────────────────────────────────────────────────
def text_layer(pdf_path, min_chars=80):
    """-> (pages_text list, image_only_page_numbers) using the PDF's own text layer."""
    import pypdf
    r = pypdf.PdfReader(pdf_path)
    pages, img = [], []
    for i, p in enumerate(r.pages, 1):
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        pages.append(t)
        if len(t.strip()) < min_chars:
            img.append(i)
    return pages, img


def full_text(pages_text, parsed):
    """Merge text layer + model transcription of image-only pages, page by page."""
    tr = {}
    for e in (parsed or {}).get("image_page_text") or []:
        try:
            tr[int(e.get("page"))] = e.get("text") or ""
        except (TypeError, ValueError):
            pass
    out = []
    for i, t in enumerate(pages_text, 1):
        body = t.strip() if len(t.strip()) >= 80 else tr.get(i, t.strip())
        out.append(f"--- page {i} ---\n{body}")
    s = "\n".join(out)
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", s)
    s = re.sub(r"\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b", "[phone]", s)
    return s


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "show-prompt":
        print(PROMPT)
    elif cmd == "show-schema":
        print(json.dumps(schema(), indent=1))
    else:
        print(__doc__)
