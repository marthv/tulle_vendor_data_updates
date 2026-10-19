"""
extract_extras.py - florist quotes (pass __f) and venue PDF facts (pass __v)
-----------------------------------------------------------------------------
Two extraction jobs that write to their OWN Xano tables and never to table 36/37:

  job="florals" -> floral_quotes (1 row per PDF) + floral_items (1 row per line item)
  job="facts"   -> venue_pdf_facts (1 row per PDF) + venue_preferred_vendors (1 row per vendor)

Why not table 36 / PROMPT_BY_CATEGORY / __k: table 36 is the package table that ep119 and
the fn21 trigger read. A florist's line items (bouquet $250 each, arch swag $45/ft) are not
packages, and routing Flowers through __k would put them in the P/E filter grid. Venue facts
are policy answers (rooms, outside caterer, vendor lists), not pricing rows. Both get their own
tables so a bad pass can be purged by PDF_ID without touching live pricing.

Both jobs are Batch API only (50% price) and reference PDFs by Files-API file_id. Neither job
runs by default: facts needs an explicit pdf_ids list, florals too (full runs are not approved).

Model: Claude Sonnet 5.5 (`claude-sonnet-5-5`). Request shape (checked against the claude-api
skill, 2026-10-19): adaptive thinking (`thinking: {"type": "adaptive"}`) with
`output_config.effort` (default "low", the documented starting point for extraction on this
model) and structured outputs via `output_config.format` (json_schema) so every response is
valid JSON. `{"type": "disabled"}` is a 400 on Sonnet 5.5 - the lowest setting is
`{"type": "between_tools"}`, selectable with EXTRA_THINKING=between_tools. Server-side
`fallbacks` is NOT sent: it is rejected on the Batches API.

Env:
  ANTHROPIC_API_KEY, GOOGLE_SERVICE_ACCOUNT_JSON  (submit)
  XANO_FLORAL_QUOTES_ENDPOINT, XANO_FLORAL_ITEMS_ENDPOINT,
  XANO_VENUE_FACTS_ENDPOINT, XANO_VENUE_PREFERRED_VENDORS_ENDPOINT   (ingest; if a table's
      endpoint is unset, its rows are written to local JSON instead and nothing is posted)
  EXTRA_MODEL (default claude-sonnet-5-5), EXTRA_EFFORT (default low),
  EXTRA_THINKING (adaptive | between_tools, default adaptive)

CLI (see main()):
  python extract_extras.py submit --job florals --ids ids.json [--pdf-dir DIR] [--env-file F]
  python extract_extras.py status BATCH_ID
  python extract_extras.py ingest BATCH_ID --map map.json [--out-dir DIR] [--local-only]
"""

import io
import json
import os
import re
import sys
import time
import random
from datetime import datetime, timezone

CAT_FLOWERS = "Flowers"

JOB_FLORALS = "florals"
JOB_FACTS = "facts"
PASS_TAG = {JOB_FLORALS: "f", JOB_FACTS: "v"}
JOB_BY_TAG = {v: k for k, v in PASS_TAG.items()}

MODEL = os.environ.get("EXTRA_MODEL", "claude-sonnet-5-5")
# Sonnet 5.5 Batch API rate = 50% of $2 / $10 per MTok.
_PRICE_IN = 2.00 / 1_000_000
_PRICE_OUT = 10.00 / 1_000_000
_BATCH_DISCOUNT = 0.5

FLORAL_MAX_TOKENS = 24000
FACTS_MAX_TOKENS = 16000

# Batch custom_id rule ^[a-zA-Z0-9_-]{1,64}$; we append "__f" / "__v".
_CUSTOM_ID_SAFE = re.compile(r'^[a-zA-Z0-9_-]{1,60}$')

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

# ── FLORAL VOCABULARY ─────────────────────────────────────────────────────────
# item_category -> (group, display label). The GROUP is derived here, never asked of the
# model, so an item can't be "Ceremony" in one field and "centerpiece" in the other.
FLORAL_CATEGORIES = {
    "bridal_bouquet":        ("Personal", "Bridal bouquet"),
    "bridesmaid_bouquet":    ("Personal", "Bridesmaid bouquet"),
    "boutonniere":           ("Personal", "Boutonniere"),
    "corsage":               ("Personal", "Corsage"),
    "flower_girl":           ("Personal", "Flower girl"),
    "arch_backdrop":         ("Ceremony", "Arch / chuppah / backdrop"),
    "arch_swag":             ("Ceremony", "Arch swag"),
    "ground_arrangement":    ("Ceremony", "Ground arrangements"),
    "aisle_arrangement":     ("Ceremony", "Aisle arrangements"),
    "aisle_entrance_urns":   ("Ceremony", "Aisle entrance / urns"),
    "centerpiece_low":       ("Reception", "Centerpiece low / small"),
    "centerpiece_tall":      ("Reception", "Centerpiece tall / large"),
    "bud_vases":             ("Reception", "Bud vases"),
    "sweetheart_head_table": ("Reception", "Sweetheart / head table"),
    "cocktail_table":        ("Reception", "Cocktail tables"),
    "escort_signs":          ("Reception", "Escort / seating / signs"),
    "bar_flowers":           ("Reception", "Bar"),
    "cake_flowers":          ("Reception", "Cake flowers"),
    "garland_runner":        ("Reception", "Garlands / runners"),
    "hanging_installation":  ("Installations", "Hanging / ceiling"),
    "delivery_setup":        ("Fees", "Delivery & setup"),
    "breakdown":             ("Fees", "Breakdown"),
    "candle_labor":          ("Fees", "Candle setup / labor"),
    "rentals":               ("Fees", "Rentals"),
    "tax_service_fee":       ("Fees", "Tax / service fee"),
    "package":               ("Other", "Package / bundle"),
    "other":                 ("Other", "Other"),
}
FLORAL_UNITS = ["each", "per_table", "per_foot", "flat", "bundle", "per_person", "per_hour",
                "percent", "unknown"]
FLORAL_INCLUSION = ["included", "optional", "not_included_at_this_time", "unknown"]
FLORAL_DOC_TYPES = ["email_quote", "price_list", "proposal", "brochure_packages", "other"]
CONFIDENCE = ["high", "medium", "low"]
TRI = ["yes", "no", "unknown"]

# ── VENUE FACTS VOCABULARY ────────────────────────────────────────────────────
PV_CATEGORIES = ["catering", "bar", "florals", "photography", "videography", "music_dj",
                 "music_band", "officiant", "planner", "cake_dessert", "hair_makeup",
                 "rentals", "lighting_av", "transportation", "stationery", "lodging",
                 "other"]
PV_RELATIONSHIPS = ["required", "exclusive", "preferred", "recommended", "approved_list",
                    "package_inclusion", "credit_only", "unknown"]
OUTSIDE_VENDOR_POLICY = ["any_vendor_allowed", "approved_list_only", "preferred_list_suggested",
                         "some_categories_exclusive", "all_in_house", "unknown"]
LODGING_INCLUDED = ["included", "extra_cost", "mixed", "unknown"]

# Tri-state venue fact fields: (field, question). The question text is used in the prompt so
# the prompt and the schema can't drift.
FACT_TRI_FIELDS = [
    ("lodging_onsite", "On-site overnight rooms / cottages / suites guests can sleep in (not just a getting-ready room)"),
    ("room_block_offered", "Hotel room block or discounted guest rate offered"),
    ("outside_caterer_allowed", "Couple may bring an outside caterer (any, or from an approved list)"),
    ("kosher_option", "Kosher catering available (in-house kosher kitchen or approved kosher caterer)"),
    ("halal_option", "Halal catering available"),
    ("weekday_discount", "Lower pricing for weekdays / Fridays / Sundays than Saturdays"),
    ("ceremony_onsite", "Ceremony can be held on site"),
    ("outdoor_ceremony", "Outdoor ceremony space"),
    ("rain_plan", "Indoor / tented backup space for bad weather"),
    ("elopement_package", "Elopement / micro-wedding package offered"),
    ("ceremony_only_option", "Ceremony-only or short-slot booking (no full reception) offered"),
    ("multi_day_events", "Multi-day events or weekend rental (rehearsal dinner, welcome party, brunch)"),
    ("open_flame_allowed", "Open flame / real candles allowed"),
    ("sparklers_allowed", "Sparklers allowed"),
    ("outside_decorator_allowed", "Outside decorator / florist allowed"),
    ("wheelchair_accessible", "Wheelchair accessible (stated by the venue)"),
    ("parking_onsite", "On-site parking"),
    ("shuttle_offered", "Shuttle / guest transportation offered"),
]
# Free-text / numeric-as-string venue fact fields: (field, instruction).
FACT_TEXT_FIELDS = [
    ("lodging_room_count", "Number of rooms/units as a plain number, e.g. \"12\". \"\" if not stated."),
    ("lodging_sleeps", "Number of PEOPLE the lodging sleeps as a plain number. Different from room count. \"\" if not stated."),
    ("lodging_price_text", "Lodging price / what is included, verbatim-ish, max 200 chars."),
    ("lodging_min_stay_text", "Required minimum stay or room buyout rule, max 200 chars."),
    ("outside_caterer_fee_text", "Outside caterer fee / kitchen fee / buyout, max 200 chars."),
    ("outside_caterer_conditions", "Conditions on outside caterers (licensed, insured, approved list), max 200 chars."),
    ("outside_vendor_buyout_fee_text", "Fee to use a vendor not on the venue's list, max 200 chars."),
    ("cheapest_months", "Months with the LOWEST pricing, semicolon-separated full month names. \"\" if the PDF gives no seasonal pricing."),
    ("peak_months", "Months with the HIGHEST pricing, semicolon-separated full month names."),
    ("off_peak_discount_text", "Off-peak / weekday / winter discount wording, max 200 chars."),
    ("admin_service_fee_pct", "Service charge / admin fee percent as a plain number, e.g. \"22\"."),
    ("tax_rate_pct", "Sales tax percent as a plain number, e.g. \"8.875\"."),
    ("ceremony_fee", "On-site ceremony fee in dollars as a plain number."),
    ("overtime_fee_text", "Extra-hour / overtime cost wording, max 200 chars."),
    ("end_time_text", "Latest end time / music cutoff, e.g. \"11pm, amplified music until 10pm\"."),
    ("cake_cutting_fee", "Cake-cutting fee per person (or flat) as a plain number."),
    ("corkage_fee", "Corkage fee per bottle as a plain number."),
    ("security_fee_text", "Security / police detail requirement or fee, max 200 chars."),
    ("other_fees_text", "Any other mandatory fees not captured above, semicolon-separated \"name: amount\", max 400 chars."),
    ("elopement_price", "Lowest elopement / micro-wedding price in dollars as a plain number."),
    ("elopement_max_guests", "Max guests for that elopement package as a plain number."),
]


# ── JSON SCHEMAS (structured outputs) ─────────────────────────────────────────
# All values are flat strings/enums: no nullable unions (structured outputs limit those),
# numbers come back as plain-number strings and are parsed in code (_num), which is the
# same convention table 36 extraction already uses.

def _s():
    return {"type": "string"}


def _e(values):
    return {"type": "string", "enum": list(values)}


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


FLORAL_QUOTE_FIELDS = [
    "florist_name", "quote_date", "wedding_date", "event_location", "guest_count",
    "minimum_spend", "minimum_spend_conditions", "subtotal", "total",
    "delivery_fee_flat", "delivery_fee_pct", "delivery_fee_text", "setup_fee",
    "breakdown_fee", "labor_fee", "rentals_fee", "tax_rate_pct", "service_fee_pct",
    "service_fee_flat", "total_notes", "typical_spend_text", "typical_spend_low",
    "typical_spend_high", "pricing_year", "currency", "contact_email",
    "confidentiality_evidence",
]
FLORAL_ITEM_FIELDS = [
    "item_name", "price", "price_low", "price_high", "size_style", "included_text",
    "quantity", "line_total", "notes",
]

FLORAL_SCHEMA = _obj({
    "vendor_type": _e(["florist", "other"]),
    "actual_category": _s(),
    "document_type": _e(FLORAL_DOC_TYPES),
    "quote": _obj({
        **{f: _s() for f in FLORAL_QUOTE_FIELDS},
        "total_covers_everything": _e(TRI),
        "confidentiality_risk": _e(["yes", "no"]),
        "confidence": _e(CONFIDENCE),
    }),
    "items": {"type": "array", "items": _obj({
        "item_category": _e(list(FLORAL_CATEGORIES)),
        **{f: _s() for f in FLORAL_ITEM_FIELDS},
        "starting_at": _e(["yes", "no"]),
        "pricing_unit": _e(FLORAL_UNITS),
        "inclusion": _e(FLORAL_INCLUSION),
        "confidence": _e(CONFIDENCE),
    })},
})

FACTS_SCHEMA = _obj({
    "vendor_type": _e(["venue", "other"]),
    "actual_category": _s(),
    "facts": _obj({
        **{f: _e(TRI) for f, _ in FACT_TRI_FIELDS},
        **{f: _s() for f, _ in FACT_TEXT_FIELDS},
        "lodging_included": _e(LODGING_INCLUDED),
        "outside_vendor_policy": _e(OUTSIDE_VENDOR_POLICY),
        "pricing_year": _s(),
        "evidence": _s(),
        "confidence": _e(CONFIDENCE),
    }),
    "preferred_vendors": {"type": "array", "items": _obj({
        "vendor_name": _s(),
        "vendor_category": _e(PV_CATEGORIES),
        "relationship": _e(PV_RELATIONSHIPS),
        "contact_text": _s(),
        "notes": _s(),
        "confidence": _e(CONFIDENCE),
    })},
})


# ── PROMPTS ───────────────────────────────────────────────────────────────────

_CAT_LINES = "\n".join(f"  {k} -> {g}: {lbl}" for k, (g, lbl) in FLORAL_CATEGORIES.items())

FLORAL_PROMPT = f"""You extract WEDDING FLORIST pricing from a PDF (an emailed quote, a price list, a proposal with quantities, or a brochure of packages). Your output is parsed by code into a database couples use to budget for flowers, so exact numbers matter more than coverage of prose.

STEP 1 - VENDOR TYPE. "florist" if this business sells wedding flowers / floral design. "other" for anything else (venue, planner, rentals-only, photographer). If "other": set actual_category to one word (venue/planner/rentals/photographer/caterer/other), document_type "other", every quote field "" (enums: total_covers_everything "unknown", confidentiality_risk "no", confidence "low") and items []. Stop.

STEP 2 - DOCUMENT TYPE: email_quote (a letter/email quote to one couple), price_list (a menu of unit prices), proposal (an itemised proposal with quantities and line totals), brochure_packages (marketing brochure or named packages), other.

STEP 3 - ITEMS. One entry per priced or listed line item, in document order. Read every page, including tables, sidebars and fine print. Do not merge different items; do not split one line into several.
- item_category - choose the closest:
{_CAT_LINES}
  A named bundle ("Bridal party package: bouquet + 4 bridesmaids") is "package". Use "other" only when nothing fits.
- item_name: the florist's own wording for the line, verbatim (short).
- price: the unit price as a plain number ("250", not "$250"). If the line gives a range, put the ends in price_low / price_high and leave price "". If it gives one number, price_low and price_high are "".
- starting_at: "yes" when the price is a minimum ("starting at", "from", "+", "beginning at", "prices start"), else "no".
- pricing_unit: each | per_table | per_foot (swag, garland per linear foot) | flat | bundle (packages) | per_person | per_hour | percent (fees stated as a % of the subtotal) | unknown.
- size_style: size / style / stems / vessel wording if given ("hand-tied, 12in, garden roses"), else "".
- included_text: what the line says it includes, else "".
- quantity and line_total: only for proposals/quotes that state them; plain numbers, else "".
- inclusion: "included" when the line is part of the quoted order or package; "optional" when offered as an add-on / upgrade / "available upon request"; "not_included_at_this_time" when the quote lists it but marks it removed, declined, "not included at this time", struck through, or $0 pending; "unknown" for a plain price list with no order context. Still extract excluded lines - just flag them.
- FEES are items too: delivery, setup, breakdown/strike, candle labor, rentals, tax, service fee each get an item with their category and unit (flat or percent). Also copy them to the quote fields below.
- notes: anything about that line a budget would need (seasonal, "per arrangement", minimum quantity), else "".
- confidence: high when the number is printed clearly next to the item; medium when you had to associate a price to an item across layout; low when inferred.
- NEVER invent a price. A listed item with no price gets price/price_low/price_high "".

STEP 4 - QUOTE (one object for the whole PDF):
- florist_name; quote_date and wedding_date as YYYY-MM-DD when a full date is printed (else the printed text); event_location (venue/city named for the event); guest_count (plain number).
- minimum_spend: the florist's minimum order / minimum floral spend as a plain number; minimum_spend_conditions: what it depends on (date, season, "Saturdays in peak season", "for full-service weddings", "excludes tax and delivery"), verbatim-ish, else "".
- subtotal and total: as printed, plain numbers. total_covers_everything: "yes" if the printed total already includes all fees and tax, "no" if fees/tax are added on top or excluded, "unknown" if not clear. total_notes: what the total covers or excludes.
- delivery_fee_flat (dollars) OR delivery_fee_pct (percent of subtotal) - whichever the PDF states; delivery_fee_text: the verbatim fee wording. setup_fee, breakdown_fee, labor_fee, rentals_fee: dollars. tax_rate_pct, service_fee_pct: percent numbers; service_fee_flat: dollars.
- typical_spend_text: any "typical / average / most couples spend" statement verbatim (e.g. "Most of our couples invest $8,000-$15,000"); typical_spend_low / typical_spend_high: its numbers.
- pricing_year (4 digits), currency (ISO code, default "USD"), contact_email (the florist's business email).
- confidentiality_risk "yes" only if the PDF says its pricing is confidential / not to be shared; confidentiality_evidence: that sentence, max 200 chars.
- confidence: your overall confidence in the quote fields.

NUMBER RULES: plain numbers only - no $, commas, % or words. "" when not stated. Do not compute values the PDF does not print (no summing line totals into a total, no deriving a percent)."""


_TRI_LINES = "\n".join(f"- {f}: {q}" for f, q in FACT_TRI_FIELDS)
_TEXT_LINES = "\n".join(f"- {f}: {q}" for f, q in FACT_TEXT_FIELDS)

FACTS_PROMPT = f"""You read a wedding VENUE pricing PDF and record policy facts and the venue's vendor list. The answers power a wedding assistant that tells couples what a venue allows, so a wrong "no" is worse than "unknown".

STEP 1 - VENDOR TYPE. "venue" if this PDF is from a business renting event space. Otherwise "other": set actual_category to one word (florist/planner/caterer/photographer/other), every facts enum "unknown" (confidence "low"), every facts text "", preferred_vendors []. Stop.

STEP 2 - TRI-STATE FACTS. Each is "yes", "no" or "unknown".
- "yes" only when the PDF states it or clearly shows it (a priced lodging package, a listed ceremony fee for an on-site lawn).
- "no" only when the PDF explicitly rules it out ("no outside catering", "candles must be enclosed" -> open_flame_allowed "no", "sparklers are not permitted").
- Silence is "unknown". Never infer "no" from absence. Never infer "yes" from marketing adjectives.
{_TRI_LINES}

STEP 3 - DETAIL FIELDS ("" when the PDF does not say):
{_TEXT_LINES}
- lodging_included: included (rooms come with the rental) | extra_cost | mixed | unknown.
- outside_vendor_policy: any_vendor_allowed | approved_list_only (must pick from the venue's list) | preferred_list_suggested (a list is offered but outside vendors are fine) | some_categories_exclusive (e.g. catering exclusive, others open) | all_in_house | unknown.
- pricing_year: 4-digit year the pricing applies to.
- evidence: up to 3 short verbatim quotes, separated by " | ", supporting the most consequential answers (lodging, outside caterer, vendor policy). Max 400 chars.
- confidence: overall confidence in the facts.

STEP 4 - PREFERRED VENDORS. Every third-party business the venue names as a partner, in any context: vendor lists ("Preferred / Recommended / Approved / Exclusive vendors", "Vendors we love"), package lines ("cake by X"), required or sole-source vendors ("X is our exclusive caterer"), "we work with" mentions, and photo credits. Scan every page including back matter.
- vendor_name: exactly as written. One entry per business, deduplicated.
- vendor_category: catering | bar | florals | photography | videography | music_dj | music_band | officiant | planner | cake_dessert | hair_makeup | rentals | lighting_av | transportation | stationery | lodging | other. Use the section heading or the business's own description; when only a name is given and the category is not evident, use "other".
- relationship - what the PDF says about using them:
  required: the couple must use this vendor (also "in-house" third parties that are mandatory);
  exclusive: the only permitted vendor in its category;
  preferred: on a "preferred vendors" list;
  recommended: "recommended" / "we love" / "suggested" with no restriction implied;
  approved_list: listed under "approved vendors" where only approved vendors may work;
  package_inclusion: delivers a service included in a venue package;
  credit_only: appears only as a photo / styled-shoot credit;
  unknown: named with no context.
- contact_text: phone / email / website printed with the vendor, else "".
- notes: any fee or condition attached to that vendor ("$500 fee for non-list caterers"), else "".
- confidence per vendor.
- EXCLUDE: the venue itself and its own in-house team or parent brand, generic labels ("our florist"), the PDF's graphic designer, couples named in testimonials.

Plain numbers only in numeric fields (no $, %, commas). Do not invent anything not in the PDF."""


# ── REQUEST BUILDERS ──────────────────────────────────────────────────────────

def _thinking_params():
    """thinking/effort for Sonnet 5.5. adaptive + effort is the documented default control;
    between_tools is the 'thinking off' setting (only valid at effort <= high, no other
    thinking fields). {"type":"disabled"} 400s on this model, so it is never sent."""
    effort = os.environ.get("EXTRA_EFFORT", "low").strip().lower() or "low"
    mode = os.environ.get("EXTRA_THINKING", "adaptive").strip().lower()
    if mode == "between_tools":
        if effort in ("xhigh", "max"):
            effort = "high"
        return {"type": "between_tools"}, effort
    return {"type": "adaptive"}, effort


def build_extra_request(job, file_id, pdf_id, vendor_id, vendor_name, model=None):
    """One Batch API request for one PDF. custom_id = <PDF_ID>__f (florals) / __v (facts)."""
    if job not in PASS_TAG:
        raise ValueError(f"unknown job {job!r}")
    if not _CUSTOM_ID_SAFE.match(str(pdf_id)):
        raise ValueError(f"PDF_ID {pdf_id!r} can't be a batch custom_id")
    thinking, effort = _thinking_params()
    if job == JOB_FLORALS:
        system, schema, max_tokens = FLORAL_PROMPT, FLORAL_SCHEMA, FLORAL_MAX_TOKENS
        ask = (f'PDF_ID="{pdf_id}", Vendor_ID="{vendor_id}", florist="{vendor_name}". '
               'Extract every floral line item and the quote fields.')
    else:
        system, schema, max_tokens = FACTS_PROMPT, FACTS_SCHEMA, FACTS_MAX_TOKENS
        ask = (f'PDF_ID="{pdf_id}", Vendor_ID="{vendor_id}", venue="{vendor_name}". '
               'Record the venue facts and every named vendor.')
    return {
        "custom_id": f"{pdf_id}__{PASS_TAG[job]}",
        "params": {
            "model": model or MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "thinking": thinking,
            "output_config": {"effort": effort,
                              "format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": [
                {"type": "document", "source": {"type": "file", "file_id": file_id}},
                {"type": "text", "text": ask},
            ]}],
        },
    }


# ── VALUE HELPERS ─────────────────────────────────────────────────────────────

_NOT_LISTED = {"", "not listed", "n/a", "na", "none", "null", "-", "unknown", "not stated"}


def _txt(v, limit=None):
    s = "" if v is None else str(v).strip()
    if s.lower() in _NOT_LISTED:
        return ""
    return s[:limit] if limit else s


def _num(v):
    """Plain-number string -> float, else None. Unlike extract_core._to_number this does NOT
    take the low end of a range: ranges have their own low/high fields, so a range in a
    single-number field is a model error and is dropped rather than silently halved."""
    s = _txt(v)
    if not s:
        return None
    s = re.sub(r'[$€£¥%\s]|usd', '', s, flags=re.I).replace(',', '')
    if re.search(r'\d\s*[-–—]\s*\d', s) or s.count('-') > (1 if s.startswith('-') else 0):
        return None
    m = re.fullmatch(r'-?\d+(?:\.\d+)?', s)
    if not m:
        m2 = re.fullmatch(r'(\d+(?:\.\d+)?)[kK]', s)
        return float(m2.group(1)) * 1000 if m2 else None
    return float(m.group())


def _int(v):
    n = _num(v)
    return int(round(n)) if n is not None else None


def normalize_vocab(value, vocab, default=""):
    """Snap a model string to an exact member of vocab (case/punctuation-insensitive),
    else `default`. Same contract as extract_core._normalize_vocab, plus a default so
    enum fields fall back to "unknown" rather than "" when the model drifts."""
    s = _txt(value)
    if not s:
        return default
    key = re.sub(r'[^a-z0-9]', '', s.lower())
    for canon in vocab:
        if re.sub(r'[^a-z0-9]', '', canon.lower()) == key:
            return canon
    alt = key.replace('and', '')
    for canon in vocab:
        if re.sub(r'[^a-z0-9]', '', canon.lower()).replace('and', '') == alt:
            return canon
    return default


def normalize_months(value):
    """'Jan; February, march' -> 'January;February;March' (calendar order, deduped)."""
    s = _txt(value)
    if not s:
        return ""
    found = set()
    for part in re.split(r'[;,/]|\band\b', s):
        p = part.strip().lower()[:3]
        for m in MONTHS:
            if p and m.lower().startswith(p) and len(p) == 3:
                found.add(m)
    return ";".join(m for m in MONTHS if m in found)


def _date(v):
    """YYYY-MM-DD if the model returned one, else None (raw text kept in *_text)."""
    s = _txt(v)
    return s if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s) else None


# ── PARSERS ───────────────────────────────────────────────────────────────────

def parse_floral(parsed, meta):
    """Model JSON -> {"quote": row, "items": [rows]} for floral_quotes / floral_items.
    Returns {"__wrong_category__": True, "category": ...} when the PDF isn't a florist's,
    or {"__error__": reason} when there is nothing to write."""
    if not isinstance(parsed, dict):
        return {"__error__": "unparseable response"}
    if normalize_vocab(parsed.get("vendor_type"), ["florist", "other"]) != "florist":
        return {"__wrong_category__": True,
                "category": _txt(parsed.get("actual_category")) or "unknown"}
    pdf_id, vendor_id = meta["pdf_id"], meta["vendor_id"]
    ts = meta.get("timestamp") or datetime.now(timezone.utc).isoformat()
    q = parsed.get("quote") if isinstance(parsed.get("quote"), dict) else {}
    quote = {
        "PDF_ID": pdf_id,
        "Vendor_ID": vendor_id,
        "florist_name": _txt(q.get("florist_name"), 200) or meta.get("vendor_name", ""),
        "state": meta.get("state", ""),
        "hub": meta.get("hub", ""),
        "document_type": normalize_vocab(parsed.get("document_type"), FLORAL_DOC_TYPES, "other"),
        "quote_date": _date(q.get("quote_date")),
        "quote_date_text": _txt(q.get("quote_date"), 60),
        "wedding_date": _date(q.get("wedding_date")),
        "wedding_date_text": _txt(q.get("wedding_date"), 60),
        "event_location": _txt(q.get("event_location"), 200),
        "guest_count": _int(q.get("guest_count")),
        "minimum_spend": _num(q.get("minimum_spend")),
        "minimum_spend_conditions": _txt(q.get("minimum_spend_conditions"), 400),
        "subtotal": _num(q.get("subtotal")),
        "total": _num(q.get("total")),
        "total_covers_everything": normalize_vocab(q.get("total_covers_everything"), TRI, "unknown"),
        "total_notes": _txt(q.get("total_notes"), 400),
        "delivery_fee_flat": _num(q.get("delivery_fee_flat")),
        "delivery_fee_pct": _num(q.get("delivery_fee_pct")),
        "delivery_fee_text": _txt(q.get("delivery_fee_text"), 300),
        "setup_fee": _num(q.get("setup_fee")),
        "breakdown_fee": _num(q.get("breakdown_fee")),
        "labor_fee": _num(q.get("labor_fee")),
        "rentals_fee": _num(q.get("rentals_fee")),
        "tax_rate_pct": _num(q.get("tax_rate_pct")),
        "service_fee_pct": _num(q.get("service_fee_pct")),
        "service_fee_flat": _num(q.get("service_fee_flat")),
        "typical_spend_text": _txt(q.get("typical_spend_text"), 400),
        "typical_spend_low": _num(q.get("typical_spend_low")),
        "typical_spend_high": _num(q.get("typical_spend_high")),
        "pricing_year": _int(q.get("pricing_year")),
        "currency": (_txt(q.get("currency"))[:3].upper() or "USD"),
        "contact_email": _vendor_email(q.get("contact_email")),
        "confidentiality_flag": _txt(q.get("confidentiality_risk")).lower() == "yes",
        "confidentiality_evidence": _txt(q.get("confidentiality_evidence"), 1000),
        "confidence": normalize_vocab(q.get("confidence"), CONFIDENCE, "low"),
        "item_count": 0,
        "model": meta.get("model", MODEL),
        "extracted_at": ts,
    }
    items = []
    for i, it in enumerate(parsed.get("items") or []):
        if not isinstance(it, dict):
            continue
        name = _txt(it.get("item_name"), 300)
        cat = normalize_vocab(it.get("item_category"), list(FLORAL_CATEGORIES), "other")
        group, label = FLORAL_CATEGORIES[cat]
        price, lo, hi = _num(it.get("price")), _num(it.get("price_low")), _num(it.get("price_high"))
        if price is None and lo is not None and hi is not None and lo == hi:
            price, lo, hi = lo, None, None
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        if not name and price is None and lo is None:
            continue
        inclusion = normalize_vocab(it.get("inclusion"), FLORAL_INCLUSION, "unknown")
        items.append({
            "PDF_ID": pdf_id,
            "Vendor_ID": vendor_id,
            "item_index": len(items),
            "item_group": group,
            "item_category": cat,
            "item_category_label": label,
            "item_name": name or label,
            "price": price,
            "price_low": lo,
            "price_high": hi,
            "starting_at": _txt(it.get("starting_at")).lower() == "yes",
            "pricing_unit": normalize_vocab(it.get("pricing_unit"), FLORAL_UNITS, "unknown"),
            "size_style": _txt(it.get("size_style"), 300),
            "included_text": _txt(it.get("included_text"), 400),
            "quantity": _num(it.get("quantity")),
            "line_total": _num(it.get("line_total")),
            "inclusion": inclusion,
            "is_optional": inclusion in ("optional", "not_included_at_this_time"),
            "notes": _txt(it.get("notes"), 300),
            "confidence": normalize_vocab(it.get("confidence"), CONFIDENCE, "low"),
            "extracted_at": ts,
        })
    quote["item_count"] = len(items)
    if not items and quote["minimum_spend"] is None and quote["total"] is None \
            and quote["typical_spend_low"] is None:
        return {"__error__": "florist PDF but no items, minimum, total or typical spend parsed",
                "quote": quote, "items": []}
    return {"quote": quote, "items": items}


def parse_facts(parsed, meta):
    """Model JSON -> {"facts": row, "preferred_vendors": [rows]}."""
    if not isinstance(parsed, dict):
        return {"__error__": "unparseable response"}
    if normalize_vocab(parsed.get("vendor_type"), ["venue", "other"]) != "venue":
        return {"__wrong_category__": True,
                "category": _txt(parsed.get("actual_category")) or "unknown"}
    pdf_id, vendor_id = meta["pdf_id"], meta["vendor_id"]
    ts = meta.get("timestamp") or datetime.now(timezone.utc).isoformat()
    f = parsed.get("facts") if isinstance(parsed.get("facts"), dict) else {}
    row = {"PDF_ID": pdf_id, "Vendor_ID": vendor_id}
    for k, _ in FACT_TRI_FIELDS:
        row[k] = normalize_vocab(f.get(k), TRI, "unknown")
    numeric = {"lodging_room_count", "lodging_sleeps", "admin_service_fee_pct", "tax_rate_pct",
               "ceremony_fee", "cake_cutting_fee", "corkage_fee", "elopement_price",
               "elopement_max_guests"}
    for k, _ in FACT_TEXT_FIELDS:
        if k in numeric:
            row[k] = _num(f.get(k))
        elif k in ("cheapest_months", "peak_months"):
            row[k] = normalize_months(f.get(k))
        else:
            row[k] = _txt(f.get(k), 400)
    for k in ("lodging_room_count", "lodging_sleeps", "elopement_max_guests"):
        row[k] = int(row[k]) if row[k] is not None else None
    row["lodging_included"] = normalize_vocab(f.get("lodging_included"), LODGING_INCLUDED, "unknown")
    row["outside_vendor_policy"] = normalize_vocab(f.get("outside_vendor_policy"),
                                                   OUTSIDE_VENDOR_POLICY, "unknown")
    # A lodging count/price without lodging_onsite=yes is a contradiction; keep the facts,
    # don't silently upgrade the tri-state (the reviewer sees it in the pilot report).
    row["pricing_year"] = _int(f.get("pricing_year"))
    row["evidence"] = _txt(f.get("evidence"), 400)
    row["confidence"] = normalize_vocab(f.get("confidence"), CONFIDENCE, "low")
    row["model"] = meta.get("model", MODEL)
    row["extracted_at"] = ts
    vendors, seen = [], set()
    own = re.sub(r'[^a-z0-9]', '', str(meta.get("vendor_name", "")).lower())
    for v in parsed.get("preferred_vendors") or []:
        if not isinstance(v, dict):
            continue
        name = _txt(v.get("vendor_name"), 200)
        key = re.sub(r'[^a-z0-9]', '', name.lower())
        if not key or key in seen or (own and key == own):
            continue
        seen.add(key)
        vendors.append({
            "PDF_ID": pdf_id,
            "Venue_Vendor_ID": vendor_id,
            "vendor_name": name,
            "vendor_category": normalize_vocab(v.get("vendor_category"), PV_CATEGORIES, "other"),
            "relationship": normalize_vocab(v.get("relationship"), PV_RELATIONSHIPS, "unknown"),
            "contact_text": _txt(v.get("contact_text"), 300),
            "notes": _txt(v.get("notes"), 300),
            "confidence": normalize_vocab(v.get("confidence"), CONFIDENCE, "low"),
            "matched_vendor_id": "",
            "extracted_at": ts,
        })
    row["preferred_vendor_count"] = len(vendors)
    return {"facts": row, "preferred_vendors": vendors}


_FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com",
             "me.com", "msn.com", "live.com", "comcast.net", "verizon.net", "att.net"}


def _vendor_email(v):
    """Business email only - personal/freemail addresses are dropped (same rule as the
    table 36 path). A florist on gmail is common, but we don't store personal addresses."""
    m = re.search(r'[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})', _txt(v))
    if not m:
        return ""
    return "" if m.group(1).lower() in _FREEMAIL else m.group(0)


def response_text(message):
    """Text of the first `text` block. With adaptive thinking the first block can be a
    `thinking` block, so never read content[0] blindly."""
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    for b in content or []:
        btype = getattr(b, "type", None) or (b.get("type") if isinstance(b, dict) else None)
        if btype == "text":
            return getattr(b, "text", None) or (b.get("text") if isinstance(b, dict) else "")
    return ""


def usage_of(message):
    u = getattr(message, "usage", None)
    if u is None and isinstance(message, dict):
        u = message.get("usage") or {}
    g = (lambda k: getattr(u, k, None)) if not isinstance(u, dict) else u.get
    return {"input": g("input_tokens") or 0, "output": g("output_tokens") or 0,
            "cache_read": g("cache_read_input_tokens") or 0,
            "cache_create": g("cache_creation_input_tokens") or 0}


def batch_cost(usage):
    """USD at the Sonnet 5.5 Batch API rate. Cache tokens are priced as input (none are
    expected: batch requests here carry no cache_control)."""
    tin = usage.get("input", 0) + usage.get("cache_read", 0) + usage.get("cache_create", 0)
    return (tin * _PRICE_IN + usage.get("output", 0) * _PRICE_OUT) * _BATCH_DISCOUNT


def parse_result(result, pdf_map):
    """One batch result (SDK object or plain dict) -> (pdf_id, job, outcome dict).
    outcome: {"status": ok|wrong_category|failed, "rows": {...}, "usage": {...}, "error": str}.
    Pure function of its inputs - this is what the offline tests drive."""
    get = (lambda o, k: o.get(k) if isinstance(o, dict) else getattr(o, k, None))
    cid = get(result, "custom_id") or ""
    if "__" not in cid:
        return None, None, None
    pdf_id, tag = cid.rsplit("__", 1)
    job = JOB_BY_TAG.get(tag)
    if job is None:
        return None, None, None
    meta = dict(pdf_map.get(pdf_id) or {})
    meta.setdefault("pdf_id", pdf_id)
    meta.setdefault("vendor_id", meta.get("Vendor_ID", ""))
    res = get(result, "result")
    rtype = get(res, "type")
    if rtype != "succeeded":
        err = get(res, "error")
        inner = get(err, "error") if err is not None else None
        detail = f"{rtype}"
        for o in (inner, err):
            if o is not None and (get(o, "type") or get(o, "message")):
                detail = f"{rtype}/{get(o, 'type') or ''}: {get(o, 'message') or ''}"[:300]
                break
        return pdf_id, job, {"status": "failed", "error": detail, "usage": {}, "rows": {}}
    msg = get(res, "message")
    usage = usage_of(msg)
    stop = get(msg, "stop_reason")
    if stop in ("refusal", "max_tokens"):
        return pdf_id, job, {"status": "failed", "usage": usage, "rows": {},
                             "error": f"stop_reason={stop}"}
    text = response_text(msg)
    try:
        parsed = json.loads(re.sub(r'```json|```', '', text or '').strip())
    except Exception:
        return pdf_id, job, {"status": "failed", "usage": usage, "rows": {},
                             "error": "json parse failed"}
    out = parse_floral(parsed, meta) if job == JOB_FLORALS else parse_facts(parsed, meta)
    if out.get("__wrong_category__"):
        return pdf_id, job, {"status": "wrong_category", "usage": usage, "rows": {},
                             "error": f"PDF looks like {out.get('category')}", "raw": parsed}
    if out.get("__error__"):
        return pdf_id, job, {"status": "failed", "usage": usage, "rows": out,
                             "error": out["__error__"], "raw": parsed}
    return pdf_id, job, {"status": "ok", "usage": usage, "rows": out, "error": "", "raw": parsed}


# ── INGEST (purge-before-insert by PDF_ID) ────────────────────────────────────

TABLE_ENDPOINT_ENV = {
    "floral_quotes": "XANO_FLORAL_QUOTES_ENDPOINT",
    "floral_items": "XANO_FLORAL_ITEMS_ENDPOINT",
    "venue_pdf_facts": "XANO_VENUE_FACTS_ENDPOINT",
    "venue_preferred_vendors": "XANO_VENUE_PREFERRED_VENDORS_ENDPOINT",
}
JOB_TABLES = {
    JOB_FLORALS: (("floral_quotes", "quote"), ("floral_items", "items")),
    JOB_FACTS: (("venue_pdf_facts", "facts"), ("venue_preferred_vendors", "preferred_vendors")),
}


def rows_by_table(job, rows):
    """{'floral_quotes': [..], 'floral_items': [..]} from a parse_floral/parse_facts result."""
    out = {}
    for table, key in JOB_TABLES[job]:
        v = rows.get(key)
        out[table] = [v] if isinstance(v, dict) else list(v or [])
    return out


class XanoWriter:
    """Writes one PDF's rows to one table: GET existing rows for that PDF_ID, DELETE them,
    then POST the new rows. The selection of rows to delete happens in Python and is
    asserted per row (PDF_ID must match exactly), so a mis-scoped list endpoint can never
    delete another PDF's rows. `http` is injectable for offline tests (needs .get/.post/
    .delete returning objects with .status_code and .json())."""

    def __init__(self, endpoint, secret="", http=None, sleep=time.sleep):
        import requests as _requests
        self.endpoint = endpoint.rstrip("/")
        self.secret = secret
        self.http = http or _requests
        self.sleep = sleep

    def _params(self, extra=None):
        p = dict(extra or {})
        if self.secret:
            p["secret"] = self.secret
        return p

    def purge(self, pdf_id):
        r = self.http.get(self.endpoint, params=self._params({"pdf_id": pdf_id}), timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"purge lookup {r.status_code}")
        data = r.json()
        if isinstance(data, dict):
            data = data.get("items", [])
        if not isinstance(data, list):
            raise RuntimeError("purge lookup: unexpected shape")
        doomed = [row for row in data if isinstance(row, dict) and row.get("id")
                  and str(row.get("PDF_ID") or "").strip() == str(pdf_id).strip()]
        deleted = 0
        for row in doomed:
            d = self.http.delete(f"{self.endpoint}/{row['id']}", params=self._params(), timeout=30)
            if d.status_code not in (200, 201, 204):
                raise RuntimeError(f"purge delete {row['id']} -> {d.status_code}")
            deleted += 1
        return deleted

    def post(self, row, attempts=4):
        delay = 1.0
        for a in range(attempts):
            try:
                r = self.http.post(self.endpoint, json=row, params=self._params(), timeout=20)
                if r.status_code in (200, 201):
                    return True
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    return False
            except Exception:
                pass
            if a < attempts - 1:
                self.sleep(delay + random.uniform(0, 0.3))
                delay = min(delay * 2, 8)
        return False

    def replace(self, pdf_id, rows):
        """Purge then insert. Returns (deleted, posted, failed). If the purge fails nothing
        is posted (a re-run must not duplicate rows)."""
        deleted = self.purge(pdf_id)
        ok = fail = 0
        for row in rows:
            if self.post(row):
                ok += 1
            else:
                fail += 1
        return deleted, ok, fail


def ingest_outcomes(outcomes, out_dir=None, local_only=False, http=None, secret=None,
                    env=None, log=print):
    """outcomes: {pdf_id: (job, outcome)} -> writes rows. Tables whose endpoint env var is
    unset (or local_only=True) are written to <out_dir>/<table>.jsonl instead. Returns a
    summary dict. Rows for failed / wrong_category PDFs are never written."""
    env = os.environ if env is None else env
    secret = env.get("XANO_MACHINE_SECRET", "") if secret is None else secret
    writers, local = {}, {}
    summary = {"posted": 0, "failed_posts": 0, "deleted": 0, "local_rows": 0,
               "pdfs_ok": 0, "pdfs_failed": 0, "pdfs_wrong_category": 0, "errors": {}}
    for pdf_id, (job, oc) in sorted(outcomes.items()):
        if oc["status"] != "ok":
            summary["pdfs_wrong_category" if oc["status"] == "wrong_category"
                    else "pdfs_failed"] += 1
            summary["errors"][pdf_id] = oc.get("error", "")
            continue
        summary["pdfs_ok"] += 1
        for table, rows in rows_by_table(job, oc["rows"]).items():
            endpoint = "" if local_only else (env.get(TABLE_ENDPOINT_ENV[table]) or "")
            if not endpoint:
                local.setdefault(table, []).extend(rows)
                continue
            w = writers.get(table) or writers.setdefault(
                table, XanoWriter(endpoint, secret=secret, http=http))
            try:
                d, ok, fail = w.replace(pdf_id, rows)
            except Exception as e:
                summary["errors"][pdf_id] = f"{table}: {e}"
                summary["failed_posts"] += len(rows)
                log(f"  {pdf_id} {table}: NOT written ({e})")
                continue
            summary["deleted"] += d
            summary["posted"] += ok
            summary["failed_posts"] += fail
    if local:
        out_dir = out_dir or "."
        os.makedirs(out_dir, exist_ok=True)
        for table, rows in local.items():
            path = os.path.join(out_dir, f"{table}.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            summary["local_rows"] += len(rows)
            log(f"  {len(rows)} {table} rows -> {path} (endpoint unset: not posted)")
    return summary


# ── SUBMIT / FETCH (network) ──────────────────────────────────────────────────

def _client(env=None):
    import anthropic
    env = os.environ if env is None else env
    return anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])


def _upload(client, pdf_bytes, name):
    """Files API upload. SDK 1.x has the stable client.files; 0.x only the beta one."""
    files = getattr(client, "files", None) or client.beta.files
    last = None
    for a in range(4):
        try:
            return files.upload(file=(name, io.BytesIO(pdf_bytes), "application/pdf")).id
        except Exception as e:
            last = e
            time.sleep(1.5 * (a + 1))
    raise RuntimeError(f"upload failed: {last}")


def _create_batch(client, requests_):
    if getattr(client, "files", None) is not None:
        return client.messages.batches.create(requests=requests_)
    return client.beta.messages.batches.create(requests=requests_, betas=["files-api-2025-04-14"])


def load_pdf_bytes(row, pdf_dir=None, drive=None):
    """Local <pdf_dir>/<PDF_ID>.pdf if present, else download from Drive."""
    pid = row["PDF_ID"]
    if pdf_dir:
        p = os.path.join(pdf_dir, f"{pid}.pdf")
        if os.path.exists(p):
            with open(p, "rb") as fh:
                return fh.read(), None
    if drive is None:
        return None, "no local copy and no Drive service"
    from extract_core import download_pdf
    return download_pdf(row["PDF_Link"], drive)


def submit_job(job, rows, pdf_dir=None, save_pdfs=False, env=None, log=print, client=None):
    """rows: [{PDF_ID, Vendor_ID, Name, PDF_Link, state?, hub?}] - an explicit list; there is
    no implicit 'all PDFs' selection. Returns (batch_id, pdf_map)."""
    if job not in PASS_TAG:
        raise ValueError(job)
    if not rows:
        raise ValueError("explicit pdf list required")
    env = os.environ if env is None else env
    client = client or _client(env)
    drive = None
    reqs, pdf_map = [], {}
    ts = datetime.now(timezone.utc).isoformat()
    for i, row in enumerate(rows):
        pid = str(row["PDF_ID"]).strip()
        if not _CUSTOM_ID_SAFE.match(pid):
            log(f"  skip {pid!r}: not a valid custom_id")
            continue
        data, err = load_pdf_bytes(row, pdf_dir, drive)
        if data is None and drive is None and env.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
            from extract_core import get_drive_service
            drive = get_drive_service()
            data, err = load_pdf_bytes(row, pdf_dir, drive)
        if not data:
            log(f"  [{i+1}/{len(rows)}] {pid} download failed: {err}")
            continue
        if save_pdfs and pdf_dir:
            os.makedirs(pdf_dir, exist_ok=True)
            p = os.path.join(pdf_dir, f"{pid}.pdf")
            if not os.path.exists(p):
                with open(p, "wb") as fh:
                    fh.write(data)
        if len(data) > 30 * 1024 * 1024:
            # Same remedy as the main pipeline: rasterise to fit under the 32MB PDF limit.
            from extract_core import _downsample_pdf
            smaller = _downsample_pdf(data, target_b64_mb=25)
            if smaller and len(smaller) < len(data):
                log(f"  [{i+1}/{len(rows)}] {pid} downsampled {len(data)/1e6:.0f}MB -> "
                    f"{len(smaller)/1e6:.0f}MB")
                data = smaller
        if len(data) > 30 * 1024 * 1024:
            log(f"  [{i+1}/{len(rows)}] {pid} too large ({len(data)/1e6:.0f}MB) - skipped")
            continue
        fid = _upload(client, data, f"{pid}.pdf")
        reqs.append(build_extra_request(job, fid, pid, row.get("Vendor_ID", ""), row.get("Name", "")))
        pdf_map[pid] = {"pdf_id": pid, "vendor_id": row.get("Vendor_ID", ""),
                        "vendor_name": row.get("Name", ""), "xano_id": row.get("id"),
                        "state": ";".join(row.get("state") or []) if isinstance(row.get("state"), list)
                        else (row.get("state") or ""),
                        "hub": row.get("hub", ""), "file_id": fid, "job": job,
                        "model": MODEL, "timestamp": ts}
        log(f"  [{i+1}/{len(rows)}] {pid} uploaded")
    if not reqs:
        raise RuntimeError("no requests built")
    b = _create_batch(client, reqs)
    log(f"batch {b.id} submitted: {len(reqs)} requests ({job})")
    return b.id, pdf_map


def fetch_outcomes(batch_id, pdf_map, client=None, env=None):
    """Ended batch -> {pdf_id: (job, outcome)}. Raises if the batch hasn't ended."""
    client = client or _client(env)
    b = client.messages.batches.retrieve(batch_id)
    if b.processing_status != "ended":
        raise RuntimeError(f"batch {batch_id} is {b.processing_status}")
    out = {}
    for r in client.messages.batches.results(batch_id):
        pid, job, oc = parse_result(r, pdf_map)
        if pid:
            out[pid] = (job, oc)
    return out


def load_env_file(path):
    """KEY=VALUE lines -> os.environ (values never echoed)."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--env-file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("--job", required=True, choices=list(PASS_TAG))
    s.add_argument("--ids", required=True, help="JSON list of {PDF_ID, Vendor_ID, Name, PDF_Link}")
    s.add_argument("--pdf-dir")
    s.add_argument("--save-pdfs", action="store_true")
    s.add_argument("--map-out", required=True)
    st = sub.add_parser("status")
    st.add_argument("batch_id")
    ig = sub.add_parser("ingest")
    ig.add_argument("batch_id")
    ig.add_argument("--map", required=True)
    ig.add_argument("--out-dir", default=".")
    ig.add_argument("--local-only", action="store_true")
    a = ap.parse_args(argv)
    if a.env_file:
        load_env_file(a.env_file)
    if a.cmd == "submit":
        with open(a.ids, encoding="utf-8") as fh:
            rows = json.load(fh)
        bid, pm = submit_job(a.job, rows, pdf_dir=a.pdf_dir, save_pdfs=a.save_pdfs)
        with open(a.map_out, "w", encoding="utf-8") as fh:
            json.dump({"batch_id": bid, "pdf_map": pm}, fh, indent=1)
        print(bid)
    elif a.cmd == "status":
        b = _client().messages.batches.retrieve(a.batch_id)
        print(b.processing_status, b.request_counts)
    elif a.cmd == "ingest":
        with open(a.map, encoding="utf-8") as fh:
            pm = json.load(fh)["pdf_map"]
        outcomes = fetch_outcomes(a.batch_id, pm)
        with open(os.path.join(a.out_dir, f"outcomes_{a.batch_id}.json"), "w", encoding="utf-8") as fh:
            json.dump({k: [j, o] for k, (j, o) in outcomes.items()}, fh, ensure_ascii=False)
        print(json.dumps(ingest_outcomes(outcomes, out_dir=a.out_dir, local_only=a.local_only),
                         indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
