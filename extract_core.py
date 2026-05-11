"""
extract_core.py — Tulle PDF Extraction Core
--------------------------------------------
Extraction logic shared by the dashboard and CLI.
All config comes from environment variables — no hardcoded keys or file paths.

Required env vars:
    ANTHROPIC_API_KEY
    GOOGLE_SERVICE_ACCOUNT_JSON   (full JSON string of service account key)
    XANO_SUMMARY_ENDPOINT
    XANO_PRICING_ENDPOINT
    XANO_GET_ENDPOINT
"""

import re
import os
import json
import base64
import time
import requests
import anthropic
from datetime import datetime, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
DAYS   = ["Weekday", "Friday", "Saturday", "Sunday"]

# Claude Sonnet 4 pricing (per token)
_COST_INPUT       = 3.00  / 1_000_000
_COST_OUTPUT      = 15.00 / 1_000_000
_COST_CACHE_WRITE = 3.75  / 1_000_000
_COST_CACHE_READ  = 0.30  / 1_000_000


# ── PROMPTS ───────────────────────────────────────────────────────────────────

SUMMARY_PROMPT = """You are an expert at extracting wedding venue pricing data from PDF brochures. Extract exactly the fields listed below and return ONLY a valid JSON object. No markdown, no explanation, just the JSON.

EXTRACTION RULES:

PRICING YEAR:
- Search the entire document — title, header, footer, copyright line,
  any pricing list heading — for the year the pricing applies to.
- Also look for phrases like "2026 Pricing", "Effective January 2027",
  copyright years, or date stamps.
- Return just the 4-digit integer e.g. 2027. If not found, return "".

VENUE TYPE — assign exactly one:
  "Dedicated Event Venue" — Fallback only. Use ONLY if no other type fits.
  "Hotel / Resort" — Lodging-first: guest rooms, accommodations, or spa.
  "Restaurant / Bar" — Food-first. Dining is the core offering.
  "Estate / Mansion" — Residential-style or mansion property.
  "Performing Arts Venue" — Stage, audience seating, productions.
  "Museum / Gallery" — Exhibition-based.
  "Zoo / Aquarium" — Animals or marine exhibits are a core feature.
  "Garden / Botanical Garden" — Plant-focused institution.
  "Barn / Ranch" — Barn as primary structure or ranch/agricultural setting.
  "Winery / Brewery / Distillery" — Beverage production is core identity.
  "Country Club / Private Club" — Membership-based.
  "University / College" — Academic institution or on campus.
  "Religious" — Place of worship.
  "Civic / Public" — Publicly owned or government-operated.

PRICING MODEL — determine this first, it affects which fields apply:
  ROOM RENTAL MODEL: Venue charges a separate room/space rental fee.
  F&B MINIMUM MODEL: No separate room rental. Revenue from food &
    beverage minimum spend only.
  HYBRID: Both a venue fee AND a F&B minimum exist.
  If F&B MINIMUM MODEL with no room rental: set venue_fee_high_sat
    and venue_fee_low_sat to "" — do NOT leave them blank.

NUMERIC FIELD RULES — CRITICAL:
All dollar amounts, percentages, guest counts, and years must be
returned as plain numbers only. No exceptions.
- No $ signs, commas, % signs, currency symbols (€ £), or text
- 55000 not $55,000 — 24.5 not 24.5% — 2027 not "2027 pricing year"
- For ranges like "$2,500–$5,000": return the higher value for peak,
  lower for off-peak
- If a numeric field is not present anywhere in the document: return ""
- Never return "Not listed", "N/A", or any text for a numeric field

PRICING FIELDS:
Search the ENTIRE document for each value — it may appear in a table,
list, header, footnote, image caption, or inline sentence. Do not
assume a grid layout exists.

- Admin/Service Fee %: Look everywhere including fine print, footnotes,
  bottom of pages. May appear as "administrative fee", "service charge",
  or "gratuity". Return the NUMBER ONLY e.g. 22 or 24.5. Return "" if
  not found.

- Ceremony Fee: Dollar amount for a ceremony add-on. Search the full
  document. Return NUMBER ONLY e.g. 4000. Return "" if not found.

- Ceremony Fee Type: "Flat rate" or "Per person". Return "" if no
  ceremony fee.

- Venue Space: Named room/space. Multiple spaces separated by |

- Max Capacity Seated: Maximum seated dinner guests for the largest
  space. Return INTEGER ONLY e.g. 230. Look for "seated", "banquet",
  or "dinner" capacity. Return "" if not found.

- Venue Fee Highest Saturday: The highest Saturday room rental fee
  in the document. Search everywhere — tables, prose, headers.
  "Highest" means the most expensive Saturday season or time of year.
  If only one fee exists, use it for both highest and lowest.
  Return NUMBER ONLY e.g. 12000. Return "" if venue uses F&B model only.

- Venue Fee Lowest Saturday: The lowest Saturday room rental fee.
  Return NUMBER ONLY. Return "" if no seasonal variation or F&B model.

- F&B Min Highest Saturday: The highest Saturday food & beverage
  minimum in the document. Search everywhere. Return NUMBER ONLY
  e.g. 55000. Return "" if no F&B minimum exists.

- F&B Min Lowest Saturday: The lowest Saturday F&B minimum.
  Return NUMBER ONLY. Return "" if no F&B minimum or no variation.

- Guest Min Highest Saturday: Minimum guest count required for the
  highest Saturday pricing. Return INTEGER ONLY. Return "" if not
  specified.

- Guest Min Lowest Saturday: Minimum guest count for lowest Saturday
  pricing. Return INTEGER ONLY. Return "" if not specified.

- Per Person F&B Highest Saturday: Combined food + bar per person
  cost for the highest Saturday season. Return NUMBER ONLY e.g. 225.
  Return "" if not applicable.

- Per Person F&B Lowest Saturday: Per person F&B for lowest Saturday.
  Return NUMBER ONLY. Return "" if not applicable.

- Months Highest Pricing: Which months correspond to the HIGHEST
  Saturday pricing.
  * If only one pricing tier: list all 12 months individually.
  * If document labels low season but not high: infer high = remaining
    months.
  * If document labels high season explicitly: use those months.
  * Always list each month individually, comma-separated e.g.
    "April, May, June, September, October, November"
  * NEVER use ranges like "April to November". NEVER return "".

- Months Lowest Pricing: Months for the LOWEST Saturday pricing tier.
  * Return "" if only one pricing tier exists.
  * List each month individually, comma-separated. No ranges.

- F&B Spend Min Type: "Per Person Min" or "Overall Min Spend".
  Return "" if no F&B minimum applies.

- Base Menu Per Person: The lowest-tier plated/buffet food package
  per person. Return NUMBER ONLY. Return "" if venue uses overall
  min-spend model or no per-person menu pricing exists.

- Base Bar Per Person: Standard/premium open bar with spirits per
  person. Return NUMBER ONLY. Return "" if no per-person bar package.

- Additional_Fees: Short labels for MANDATORY fees only,
  semicolon-separated.

- Additional_Fees_Description: Full descriptions, semicolon-separated,
  matching order of Additional_Fees.

- MULTIPLE SPACES: Return an ARRAY if multiple distinct bookable spaces
  exist. Each space gets its own entry. Duplicate all shared fields
  across every entry.

- NEVER leave any field blank. Numeric fields get "" if absent.
  Text fields get "" if absent.

Return this JSON (or array for multiple spaces):
{"venue_name":{"value":"","confidence":"high"},"pricing_year":{"value":"","confidence":"high"},"venue_type":{"value":"","confidence":"high"},"admin_fee_pct":{"value":"","confidence":"high"},"ceremony_fee":{"value":"","confidence":"high"},"ceremony_fee_type":{"value":"","confidence":"high"},"venue_space":{"value":"","confidence":"high"},"max_capacity_seated":{"value":"","confidence":"high"},"venue_fee_high_sat":{"value":"","confidence":"high"},"fb_min_high_sat":{"value":"","confidence":"high"},"guest_min_high_sat":{"value":"","confidence":"high"},"per_person_fb_high_sat":{"value":"","confidence":"high"},"months_highest_pricing":{"value":"","confidence":"high"},"venue_fee_low_sat":{"value":"","confidence":"high"},"fb_min_low_sat":{"value":"","confidence":"high"},"guest_min_low_sat":{"value":"","confidence":"high"},"per_person_fb_low_sat":{"value":"","confidence":"high"},"months_lowest_pricing":{"value":"","confidence":"high"},"fb_spend_min_type":{"value":"","confidence":"high"},"base_menu_per_person":{"value":"","confidence":"high"},"base_bar_per_person":{"value":"","confidence":"high"},"additional_fees":{"value":"","confidence":"high"},"additional_fees_description":{"value":"","confidence":"high"}}"""

STRUCTURE_PROMPT = """You are reading a wedding venue PDF brochure. Your ONLY job is to map out the pricing grid structure — do not extract any dollar amounts.

Find every pricing table in the document and return ONLY a valid JSON object. No markdown, no explanation, just the JSON.

For each distinct bookable space identify:
1. Exact space name
2. Every season/date column in the pricing table, IN EXACT LEFT-TO-RIGHT ORDER as they appear on the page
3. Which months each column covers
4. Which days of the week have rows (Friday, Saturday, Sunday, Weekday)
5. What row types exist — e.g. "Room Rental", "F&B Minimum", "Per Person F&B"

Return this exact structure:
{
  "spaces": [
    {
      "name": "exact space name",
      "capacity": "max seated guests",
      "pricing_row_types": ["Room Rental", "F&B Minimum"],
      "days": ["Friday", "Saturday", "Sunday"],
      "season_columns": [
        {"column_index": 1, "label": "exact label from PDF", "months": ["July", "August"]},
        {"column_index": 2, "label": "exact label from PDF", "months": ["May", "June", "September", "October"]}
      ]
    }
  ]
}

CRITICAL: column_index must reflect the true left-to-right visual order of columns as printed in the PDF."""

PRICING_PROMPT = """You are an expert at extracting wedding venue pricing data from PDF brochures.

You have been given a JSON structure map describing every pricing table in this document. Extract all dollar values following the structure map exactly. Return ONLY a valid JSON array. No markdown, no explanation, just the JSON array.

EXTRACTION METHOD:
PASS 1 — VENUE FEES: For each space → each season column → each day: record Venue_Fee. Complete ALL before Pass 2.
PASS 2 — F&B MINIMUMS: Same order. Record FB_Min. Do not mix with Pass 1.
PASS 3 — PER PERSON (if present): Same order.

After all passes, combine into one row per space + day + month. Expand season groups into individual months.

MULTI-YEAR PRICING RULE: If multiple years shown for same months, extract ONLY the most future/recent year.

OUTPUT FIELD RULES:
- Day_of_Week: exactly one of "Weekday", "Friday", "Saturday", "Sunday"
- Month: full month name e.g. "January"
- Meal_Type: "Dinner" unless explicitly stated. Ignore breakfast.
- Venue_Fee / FB_Min / Per_Person_FB: "Not listed" if absent.
- Venue_Fee_Type: "Flat" or "Per Person"
- FB_Min_Type: "Overall Min Spend" or "Per Person Min"
- Admin_Fee_Pct / Tax_Pct / Service_Fee_Pct: number only. "Not listed" if absent.
- All repeated fields (fees, ceremony, admin): same value on every row.
- Use "Not listed" for any absent value.

Return array with these exact keys:
[{"Venue_Space_Name":"","Max_Capacity_Seated":"","Day_of_Week":"","Month":"","Meal_Type":"","Guest_Min":"","Guest_Max":"","Venue_Fee":"","Venue_Fee_Type":"","FB_Min":"","FB_Min_Type":"","Per_Person_FB":"","Base_Menu_Per_Person":"","Base_Bar_Per_Person":"","Ceremony_Fee":"","Ceremony_Fee_Type":"","Admin_Fee_Pct":"","Tax_Pct":"","Service_Fee_Pct":"","Additional_Fees":"","Additional_Fees_Description":"","Notes":""}]"""

CLASSIFICATION_PROMPT = """You are classifying a wedding venue PDF brochure. Assign exactly one Venue Offering, one or more Venue Attributes, and one Category. Return ONLY a valid JSON object. No markdown, no explanation, just the JSON.

VENUE OFFERING — assign exactly one:
"Raw Space" — venue provides just space, zero included services. Negative: any tables, chairs, bar, catering included → not Raw Space.
"Semi-Inclusive" — some services included but outside catering/vendors allowed. DEFAULT for partial services.
"All-Inclusive" — all food/beverage must go through venue. Key test: can client bring outside catering? If NO → All-Inclusive.

VENUE ATTRIBUTES — assign ALL that apply, semicolon-separated:
"Historic Architecture", "Estate / Mansion", "Rooftop / Skyline Views", "Scenic / Nature Views",
"Waterfront", "Garden Setting", "Ballroom", "Industrial / Warehouse", "Greenhouse",
"Natural Light / Large Windows", "Tall / Vaulted Ceilings", "Vineyard", "Barn", "Tented"

CATEGORY — assign exactly one from this list ONLY if you are at least 90% confident. If not, return "":
"Museum" — primary identity is a museum or cultural institution.
"Forest" — primary setting is forested, woodland, or heavily treed natural landscape.
"Barn & Rustic" — primary structure is a barn, or venue has a distinctly rustic/farm aesthetic.
"Mansions & Estates" — residential-scale mansion, historic estate, villa, or private manor.
"Botanic Gardens" — primary identity is a botanical garden, arboretum, or conservatory.
"Coastal" — venue is directly on the ocean, bay, beach, or waterfront with water as the primary setting.
"Restaurants" — venue is primarily a restaurant or food-service business.
"Hotel" — venue is primarily a hotel, resort, or lodging property.
"Vineyards & Wineries" — venue is a winery, vineyard, or beverage-production estate.
"Iconic & Expensive" — recognizable landmark venue with pricing at the extreme high end (top 5% nationally).
"Country Club" — membership-based country club, golf club, or private social club.
Leave blank ("") if no category fits at 90%+ confidence, or if multiple categories apply equally.

Return: {"venue_offering":{"value":"","confidence":"high"},"venue_attributes":{"value":"","confidence":"high"},"category":{"value":"","confidence":"high"}}
venue_attributes: semicolon-separated list, or "Not listed" if none match.
category: one of the listed values, or "" if not confident."""


# ── GOOGLE DRIVE ──────────────────────────────────────────────────────────────

def get_drive_service():
    """Build Drive service using service account credentials from env var."""
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not creds_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is not set")
    creds_info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds)


def extract_drive_id(url):
    for pattern in [r'/file/d/([a-zA-Z0-9_-]+)', r'id=([a-zA-Z0-9_-]+)', r'/d/([a-zA-Z0-9_-]+)']:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def download_pdf(url, drive_service):
    file_id = extract_drive_id(url)
    if not file_id:
        return None, "Could not parse Drive URL"
    try:
        request = drive_service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        data = buffer.getvalue()
        if len(data) < 1000:
            return None, f"File too small ({len(data)} bytes)"
        if data[:4] != b'%PDF':
            return None, "Not a valid PDF"
        return data, None
    except Exception as e:
        return None, str(e)


# ── CLAUDE ────────────────────────────────────────────────────────────────────

def call_claude(client, pdf_b64, system_prompt, user_text, max_tokens=6000):
    """Returns (parsed_json, cache_note, usage_dict).
    usage_dict keys: input, output, cache_read, cache_create (all token counts).
    On error parsed_json is None and usage_dict is {}.
    """
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
                    "cache_control": {"type": "ephemeral"}
                },
                {"type": "text", "text": user_text}
            ]}]
        )
        usage        = msg.usage
        input_tok    = getattr(usage, 'input_tokens',                0) or 0
        output_tok   = getattr(usage, 'output_tokens',               0) or 0
        cache_read   = getattr(usage, 'cache_read_input_tokens',     0) or 0
        cache_create = getattr(usage, 'cache_creation_input_tokens', 0) or 0
        cache_note = ""
        if cache_read:
            cache_note = f" (💾 cache hit {cache_read:,} tokens)"
        elif cache_create:
            cache_note = f" (💾 cache miss {cache_create:,} tokens written)"

        usage_dict = {
            "input":        input_tok,
            "output":       output_tok,
            "cache_read":   cache_read,
            "cache_create": cache_create,
        }
        raw   = msg.content[0].text.strip()
        clean = re.sub(r'```json|```', '', raw).strip()
        return json.loads(clean), cache_note, usage_dict
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}", {}
    except Exception as e:
        return None, f"Claude error: {e}", {}


# ── EXTRACTION ────────────────────────────────────────────────────────────────

def _extract_summary(client, pdf_b64, pdf_id, vendor_id, venue_name):
    parsed, note, usage = call_claude(
        client, pdf_b64, SUMMARY_PROMPT,
        f'Extract all venue pricing fields including pricing year and venue type. PDF_ID="{pdf_id}", Vendor_ID="{vendor_id}", venue="{venue_name}". Return only JSON.',
        max_tokens=4000
    )
    if not parsed:
        return None, note, usage
    if isinstance(parsed, dict):
        parsed = [parsed]
    for e in parsed:
        e['pdf_id']     = {"value": pdf_id,     "confidence": "high"}
        e['vendor_id']  = {"value": vendor_id,  "confidence": "high"}
        e['venue_name'] = {"value": venue_name, "confidence": "high"}
    return parsed, note, usage


def _extract_grid_structure(client, pdf_b64, venue_name):
    parsed, note, usage = call_claude(
        client, pdf_b64, STRUCTURE_PROMPT,
        f'Map the pricing grid structure for "{venue_name}". Return only JSON.',
        max_tokens=2000
    )
    return parsed, note, usage


def _extract_pricing_grid(client, pdf_b64, pdf_id, venue_name, structure):
    structure_context = ""
    if structure:
        structure_context = f"\n\nPricing grid structure map:\n{json.dumps(structure, indent=2)}\n"
    parsed, note, usage = call_claude(
        client, pdf_b64, PRICING_PROMPT,
        f'Extract all pricing. Venue="{venue_name}", PDF_ID="{pdf_id}".{structure_context}Return only the JSON array.',
        max_tokens=8000
    )
    if parsed and isinstance(parsed, dict):
        parsed = [parsed]
    return parsed, note, usage


def _extract_classification(client, pdf_b64, venue_name):
    parsed, note, usage = call_claude(
        client, pdf_b64, CLASSIFICATION_PROMPT,
        f'Classify venue offering and attributes for "{venue_name}". Return only JSON.',
        max_tokens=1000
    )
    return parsed, note, usage


# ── HELPERS ───────────────────────────────────────────────────────────────────

_NOT_LISTED = {"not listed", "n/a", "na", "none", "null", "-"}

def _clean(value):
    """Return empty string for any 'not listed' / absent sentinel values."""
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() in _NOT_LISTED else s


NUMERIC_FIELDS = {
    "admin_fee_pct",
    "ceremony_fee",
    "max_capacity_seated",
    "venue_fee_high_sat",
    "fb_min_high_sat",
    "guest_min_high_sat",
    "per_person_fb_high_sat",
    "venue_fee_low_sat",
    "fb_min_low_sat",
    "guest_min_low_sat",
    "per_person_fb_low_sat",
    "base_menu_per_person",
    "base_bar_per_person",
    "pricing_year",
}

def _to_number(value):
    """
    Strip currency symbols, commas, percent signs and return a clean
    numeric string suitable for posting to Xano integer/decimal fields.
    Returns "" if the value cannot be parsed as a number.
    Handles:
      - "$55,000"  -> "55000"
      - "24.5%"   -> "24.5"
      - "£8,300"  -> "8300"
      - "approx 4000" -> "4000"
      - "2,500-5,000" -> "2500"  (takes first number in a range)
      - "Not listed" -> ""
    """
    if not value:
        return ""
    s = str(value).strip()
    if s.lower() in {"not listed", "n/a", "na", "none", "null", "-", ""}:
        return ""
    # Strip currency symbols, percent, spaces
    s = re.sub(r'[$€£¥%\s]', '', s)
    # Remove commas used as thousands separators
    s = s.replace(',', '')
    # Handle ranges (e.g. "2500-5000" or "2500–5000") — take first value
    s = re.split(r'[-–—]', s)[0].strip()
    # Strip any remaining non-numeric prefix/suffix (e.g. "approx")
    m = re.search(r'\d+(?:\.\d+)?', s)
    if not m:
        return ""
    try:
        float(m.group())
        return m.group()
    except ValueError:
        return ""


# ── XANO STATUS WRITEBACK ─────────────────────────────────────────────────────

def _update_pdf_status(xano_id, status, error="", cost_usd=0.0):
    """
    PATCH wptp_pdfs/{xano_id} with the new extraction status fields.
    Returns a status string for logging. Never raises.

    Uses XANO_PATCH_PDF_ENDPOINT if set (preferred — dedicated PATCH route).
    Falls back to XANO_GET_ENDPOINT for backwards compatibility.
    """
    patch_base = (
        os.environ.get("XANO_PATCH_PDF_ENDPOINT", "").rstrip("/")
        or os.environ.get("XANO_GET_ENDPOINT", "").rstrip("/")
    )
    if not patch_base:
        return "skip: neither XANO_PATCH_PDF_ENDPOINT nor XANO_GET_ENDPOINT is set"
    if not xano_id:
        return "skip: xano_id is None"

    url = f"{patch_base}/{xano_id}"
    payload = {
        "extraction_status":   status,
        "last_extracted_at":   datetime.now(timezone.utc).isoformat(),
        "last_error":          error[:1000] if error else "",
        "extraction_cost_usd": round(float(cost_usd), 6),
        "extraction_attempts": 1,  # Xano increments server-side (current value + 1)
    }
    try:
        r = requests.patch(url, json=payload, timeout=10)
        if r.status_code in (200, 201, 204):
            return f"ok ({r.status_code}) → {url}"
        return f"err {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return f"exception: {e}"


def _compute_cost(usage_dict):
    """Convert a usage dict → USD float."""
    return (
        usage_dict.get("input",        0) * _COST_INPUT       +
        usage_dict.get("output",       0) * _COST_OUTPUT       +
        usage_dict.get("cache_create", 0) * _COST_CACHE_WRITE  +
        usage_dict.get("cache_read",   0) * _COST_CACHE_READ
    )


# ── XANO POST ─────────────────────────────────────────────────────────────────

def _post_summary(entries, classification, timestamp):
    summary_endpoint = os.environ["XANO_SUMMARY_ENDPOINT"]
    ok = fail = 0
    venue_offering   = ""
    venue_attributes = ""
    category         = ""
    if classification:
        venue_offering   = classification.get("venue_offering",  {}).get("value", "")
        venue_attributes = classification.get("venue_attributes",{}).get("value", "")
        category         = classification.get("category",        {}).get("value", "")

    for e in entries:
        def v(key):
            raw = _clean(e.get(key, {}).get("value", ""))
            if key in NUMERIC_FIELDS:
                return _to_number(raw)
            return raw

        payload = {
            "PDF_ID":                                                  e.get("pdf_id",     {}).get("value", ""),
            "VENDOR_ID":                                               e.get("vendor_id",  {}).get("value", ""),
            "VENUE_NAME":                                              e.get("venue_name", {}).get("value", ""),
            "Pricing_Year":                                            v("pricing_year"),
            "Venue_Type":                                              v("venue_type"),
            "Venue_Offering":                                          _clean(venue_offering),
            "Venue_Attributes":                                        _clean(venue_attributes),
            "CATEGORY":                                                _clean(category),
            "Admin_Service_Fee":                                       v("admin_fee_pct"),
            "Ceremony_Fee":                                            v("ceremony_fee"),
            "Ceremony_fee_Type":                                       v("ceremony_fee_type"),
            "Venue_Space_Name":                                        v("venue_space"),
            "Max_Capacity_Seated":                                     v("max_capacity_seated"),
            "Venue_Fee_on_a_Peak_Season_Saturday":                     v("venue_fee_high_sat"),
            "Food_and_Beverage_Min_on_a_Peak_Season_Saturday":         v("fb_min_high_sat"),
            "Guest_Min_Highest_Sat":                                   v("guest_min_high_sat"),
            "Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday":  v("per_person_fb_high_sat"),
            "Months__Highest_Pricing":                                 v("months_highest_pricing"),
            "Venue_Fee_on_Lowest_Saturday":                            v("venue_fee_low_sat"),
            "Food_and_Beverage_Min_on_Lowest_Saturday":                v("fb_min_low_sat"),
            "Guest_Min_Lowest_Sat":                                    v("guest_min_low_sat"),
            "Per_Person_Food_and_Beverage_on_Lowest_Saturday":         v("per_person_fb_low_sat"),
            "Months__Lowest_Pricing":                                  v("months_lowest_pricing"),
            "FB_Spend_Min_Type":                                       v("fb_spend_min_type"),
            "Base_Menu_Fee_Per_Person":                                v("base_menu_per_person"),
            "Base_Bar_Package_Per_Person":                             v("base_bar_per_person"),
            "Additional_Fees":                                         v("additional_fees"),
            "Additional_Fees_Description":                             v("additional_fees_description"),
            "last_extracted_at":                                       timestamp[:10],
        }
        try:
            r = requests.post(summary_endpoint, json=payload, timeout=15)
            if r.status_code in (200, 201):
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    return ok, fail


def _post_pricing_grid(rows, pdf_id, vendor_id, venue_name, timestamp):
    pricing_endpoint = os.environ["XANO_PRICING_ENDPOINT"]
    ok = fail = 0
    for row in rows:
        day   = row.get("Day_of_Week", "")
        month = row.get("Month", "")
        if day not in DAYS:
            day = "Weekday"
        if month not in MONTHS and month != "All":
            month = "All"
        def r(key, default=""):
            return _clean(row.get(key, default))

        payload = {
            "PDF_ID": pdf_id, "Vendor_ID": vendor_id, "Venue_Name": venue_name,
            "Venue_Space_Name":            r("Venue_Space_Name"),
            "Max_Capacity_Seated":         r("Max_Capacity_Seated"),
            "Day_of_Week":                 day,
            "Month":                       month,
            "Meal_Type":                   r("Meal_Type") or "Dinner",
            "Guest_Min":                   r("Guest_Min"),
            "Guest_Max":                   r("Guest_Max"),
            "Venue_Fee":                   r("Venue_Fee"),
            "Venue_Fee_Type":              r("Venue_Fee_Type"),
            "FB_Min":                      r("FB_Min"),
            "FB_Min_Type":                 r("FB_Min_Type"),
            "Per_Person_FB":               r("Per_Person_FB"),
            "Base_Menu_Per_Person":        r("Base_Menu_Per_Person"),
            "Base_Bar_Per_Person":         r("Base_Bar_Per_Person"),
            "Ceremony_Fee":                r("Ceremony_Fee"),
            "Ceremony_Fee_Type":           r("Ceremony_Fee_Type"),
            "Admin_Fee_Pct":               r("Admin_Fee_Pct"),
            "Tax_Pct":                     r("Tax_Pct"),
            "Service_Fee_Pct":             r("Service_Fee_Pct"),
            "Additional_Fees":             r("Additional_Fees"),
            "Additional_Fees_Description": r("Additional_Fees_Description"),
            "Notes":                       r("Notes"),
            "last_extracted_at":           timestamp,
        }
        try:
            r = requests.post(pricing_endpoint, json=payload, timeout=15)
            if r.status_code in (200, 201):
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    return ok, fail


def _fetch_xano_pages(endpoint, per_page=500):
    """Fetch all pages from a Xano endpoint. Yields (all_rows_so_far, page_num) tuples for progress."""
    all_rows = []
    page = 1
    while True:
        for attempt in range(3):
            try:
                resp = requests.get(endpoint, params={"page": page, "per_page": per_page}, timeout=30)
                resp.raise_for_status()
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        data  = resp.json()
        batch = data if isinstance(data, list) else (data.get("items") or data.get("data") or data.get("result") or [])
        if not batch:
            break
        all_rows.extend(batch)
        yield all_rows, page
        if len(batch) >= per_page * 2 or len(batch) < per_page:
            break
        page += 1
        time.sleep(0.3)


# ── PUBLIC GENERATOR ──────────────────────────────────────────────────────────

def run_extraction(
    start_row: int,
    end_row: int | None,
    pdf_ids: list[str] | None = None,
    rerun_failed: bool = False,
):
    """
    Generator — yields log strings as extraction proceeds.
    The dashboard iterates this and displays each line in real time.

    Modes (mutually exclusive, checked in order):
      pdf_ids      — run only the specified PDF_ID strings
      rerun_failed — run only rows where extraction_status == "failed"
      start_row / end_row — original row-range behaviour (default)

    Yields strings. Final item is always a dict:
        {"ok": int, "partial": int, "failed": int, "log": [...]}
    """
    log = []

    def emit(msg: str):
        log.append(msg)
        yield msg

    get_endpoint = os.environ["XANO_GET_ENDPOINT"]

    yield from emit("🔄 Fetching PDF list from Xano...")
    try:
        all_rows = []
        for all_rows, pg in _fetch_xano_pages(get_endpoint):
            yield from emit(f"   page {pg} — {len(all_rows)} rows fetched so far...")
        rows_with_links = [
            r for r in all_rows
            if 'drive.google.com' in str(r.get('PDF_Link') or r.get('pdf_link') or '')
        ]
        yield from emit(f"✓  {len(all_rows)} total rows, {len(rows_with_links)} with Drive links")
    except Exception as e:
        yield from emit(f"❌ Failed to fetch from Xano: {e}")
        yield {"ok": 0, "partial": 0, "failed": 0, "log": log}
        return

    # ── Build the work batch depending on run mode ────────────────────────────
    if pdf_ids:
        # Specific PDF IDs requested — look them up regardless of current status
        pdf_id_set = {str(p).strip() for p in pdf_ids if str(p).strip()}
        batch = [
            r for r in rows_with_links
            if str(r.get('PDF_ID') or r.get('pdf_id') or '').strip() in pdf_id_set
        ]
        yield from emit(f"   Mode: specific PDF IDs — {len(batch)} matched of {len(pdf_id_set)} requested")
        not_found = pdf_id_set - {str(r.get('PDF_ID') or r.get('pdf_id') or '').strip() for r in batch}
        if not_found:
            yield from emit(f"   ⚠  Not found: {', '.join(sorted(not_found))}")

    elif rerun_failed:
        # Re-run anything previously marked failed
        batch = [
            r for r in rows_with_links
            if str(r.get('extraction_status') or '').strip().lower() == 'failed'
        ]
        yield from emit(f"   Mode: re-run failed — {len(batch)} rows")

    else:
        # Default: row-range, skipping already-extracted
        total = len(rows_with_links)
        end   = end_row if end_row is not None else total
        batch = rows_with_links[start_row:end]
        yield from emit(f"   Mode: rows {start_row + 1} → {min(end, total)} ({len(batch)} venues)")

    # ── For default mode: skip already-extracted (dedup by PDF_ID in summary table) ──
    already_done: set[str] = set()
    if not pdf_ids and not rerun_failed:
        yield from emit("")
        yield from emit("🔍 Checking already-extracted PDF IDs...")
        try:
            summary_endpoint = os.environ["XANO_SUMMARY_ENDPOINT"]
            existing = []
            for existing, _ in _fetch_xano_pages(summary_endpoint):
                pass
            already_done = {str(r.get('PDF_ID') or r.get('pdf_id') or '').strip() for r in existing}
            already_done.discard('')
            yield from emit(f"✓  {len(already_done)} already extracted — will skip")
        except Exception as e:
            yield from emit(f"⚠  Could not fetch existing records: {e}. Proceeding without dedup.")

    yield from emit("")

    client        = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    drive_service = get_drive_service()
    yield from emit("✓  Google Drive authenticated")
    yield from emit("")

    results_log  = []
    total_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    total_rows   = len(rows_with_links)

    def _add_usage(u):
        for k in total_tokens:
            total_tokens[k] += u.get(k, 0)

    for i, row in enumerate(batch):
        pdf_id    = str(row.get('PDF_ID')    or row.get('pdf_id')    or '').strip()
        vendor_id = str(row.get('Vendor_ID') or row.get('vendor_id') or '').strip()
        venue_name = str(row.get('Name')     or row.get('name')      or '').strip()
        pdf_link  = str(row.get('PDF_Link')  or row.get('pdf_link')  or '').strip()
        xano_id   = row.get('id')   # Xano integer primary key — used for PATCH
        row_num   = (start_row + i + 1) if (not pdf_ids and not rerun_failed) else (i + 1)

        # Default mode dedup
        if not pdf_ids and not rerun_failed and pdf_id in already_done:
            yield from emit(f"[{row_num}/{total_rows}] {pdf_id} — {venue_name} — ⏭  skipping (already extracted)")
            continue

        yield from emit(f"")
        yield from emit(f"[{row_num}] {pdf_id} — {venue_name}")
        yield from emit(f"  ↓  Downloading...")

        pdf_bytes, err = download_pdf(pdf_link, drive_service)
        if not pdf_bytes:
            msg = f"Download failed: {err}"
            yield from emit(f"  ⚠  {msg}")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name, "status": "FAILED", "reason": msg})
            patch_result = _update_pdf_status(xano_id, "failed", error=msg)
            yield from emit(f"  📝 Status writeback: {patch_result}")
            continue
        yield from emit(f"  ✓  Downloaded ({len(pdf_bytes)//1024}KB)")

        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        if len(pdf_b64) / 1024 / 1024 > 30:
            msg = "PDF too large (>30MB base64)"
            yield from emit(f"  ⚠  {msg}, skipping")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name, "status": "FAILED", "reason": msg})
            patch_result = _update_pdf_status(xano_id, "failed", error=msg)
            yield from emit(f"  📝 Status writeback: {patch_result}")
            continue

        timestamp    = datetime.now(timezone.utc).isoformat()
        run_usage    = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

        def _track(u):
            _add_usage(u)
            for k in run_usage:
                run_usage[k] += u.get(k, 0)

        # ── Pass 1: Summary ───────────────────────────────────────────────────
        yield from emit(f"  🤖 [1/4] Extracting summary + pricing year + venue type...")
        summary, note, usage = _extract_summary(client, pdf_b64, pdf_id, vendor_id, venue_name)
        _track(usage)
        if not summary:
            msg = f"Summary extraction failed: {note}"
            yield from emit(f"  ❌ {msg}")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name, "status": "FAILED", "reason": msg})
            patch_result = _update_pdf_status(xano_id, "failed", error=msg, cost_usd=_compute_cost(run_usage))
            yield from emit(f"  📝 Status writeback: {patch_result}")
            continue
        yield from emit(f"  ✓  {len(summary)} space(s){note}")

        # ── Pass 2: Grid structure ────────────────────────────────────────────
        yield from emit(f"  🤖 [2/4] Mapping pricing grid structure...")
        structure, note, usage = _extract_grid_structure(client, pdf_b64, venue_name)
        _track(usage)
        if structure:
            yield from emit(f"  ✓  {len(structure.get('spaces', []))} space(s) mapped{note}")
        else:
            yield from emit(f"  ⚠  Structure mapping failed — proceeding without it{note}")

        # ── Pass 3: Pricing grid ──────────────────────────────────────────────
        yield from emit(f"  🤖 [3/4] Extracting pricing grid...")
        pricing, note, usage = _extract_pricing_grid(client, pdf_b64, pdf_id, venue_name, structure)
        _track(usage)
        if not pricing:
            yield from emit(f"  ⚠  Pricing grid failed — summary only{note}")
            pricing = []
        else:
            yield from emit(f"  ✓  {len(pricing)} pricing rows{note}")

        # ── Pass 4: Classification ────────────────────────────────────────────
        yield from emit(f"  🤖 [4/4] Classifying offering + attributes + category...")
        classification, note, usage = _extract_classification(client, pdf_b64, venue_name)
        _track(usage)
        if classification:
            offering  = classification.get('venue_offering',  {}).get('value', '?')
            attrs     = classification.get('venue_attributes',{}).get('value', '?')
            category  = classification.get('category',        {}).get('value', '') or '—'
            yield from emit(f"  ✓  {offering} | {category} | {attrs}{note}")
        else:
            yield from emit(f"  ⚠  Classification failed{note}")

        # ── Post to Xano ──────────────────────────────────────────────────────
        yield from emit(f"  📤 Posting summary ({len(summary)} row(s))...")
        s_ok, s_fail = _post_summary(summary, classification, timestamp)
        yield from emit(f"  {'✓' if s_fail == 0 else '⚠'}  {s_ok} posted, {s_fail} failed")

        if pricing:
            yield from emit(f"  📤 Posting {len(pricing)} pricing rows...")
            p_ok, p_fail = _post_pricing_grid(pricing, pdf_id, vendor_id, venue_name, timestamp)
            yield from emit(f"  {'✓' if p_fail == 0 else '⚠'}  {p_ok} posted, {p_fail} failed")
        else:
            p_ok = p_fail = 0

        # ── Write status back to wptp_pdfs ────────────────────────────────────
        run_cost   = _compute_cost(run_usage)
        all_failed = s_fail + p_fail
        status     = "extracted" if all_failed == 0 else "partial"
        error_msg  = f"{s_fail} summary row(s) failed to post" if s_fail else (
                     f"{p_fail} pricing row(s) failed to post" if p_fail else "")
        patch_result = _update_pdf_status(
            xano_id,
            status   = status,
            error    = error_msg,
            cost_usd = run_cost,
        )
        yield from emit(f"  📝 Status → {status} (${run_cost:.4f}) · writeback: {patch_result}")

        results_log.append({
            "pdf_id":       pdf_id,
            "venue_name":   venue_name,
            "status":       "OK" if all_failed == 0 else "PARTIAL",
            "summary_rows": s_ok,
            "pricing_rows": p_ok,
            "failed":       all_failed,
            "cost_usd":     run_cost,
            "offering":     classification.get('venue_offering',  {}).get('value', '') if classification else '',
            "category":     classification.get('category',        {}).get('value', '') if classification else '',
            "attributes":   classification.get('venue_attributes',{}).get('value', '') if classification else '',
        })

        if i < len(batch) - 1:
            time.sleep(2)

    # ── Summary ───────────────────────────────────────────────────────────────
    ok_count   = sum(1 for r in results_log if r['status'] == 'OK')
    fail_count = sum(1 for r in results_log if r['status'] == 'FAILED')
    part_count = sum(1 for r in results_log if r['status'] == 'PARTIAL')

    cost_usd = (
        total_tokens["input"]        * _COST_INPUT       +
        total_tokens["output"]       * _COST_OUTPUT      +
        total_tokens["cache_create"] * _COST_CACHE_WRITE +
        total_tokens["cache_read"]   * _COST_CACHE_READ
    )

    yield from emit("")
    yield from emit("─" * 48)
    yield from emit(f"✅ Done — {ok_count} succeeded, {part_count} partial, {fail_count} failed")
    yield from emit(
        f"💰 Claude cost: ${cost_usd:.4f}  "
        f"({total_tokens['input']:,} input · {total_tokens['output']:,} output · "
        f"{total_tokens['cache_read']:,} cache reads · {total_tokens['cache_create']:,} cache writes)"
    )

    if fail_count:
        yield from emit("Failed:")
        for r in results_log:
            if r['status'] == 'FAILED':
                yield from emit(f"  {r['pdf_id']}: {r.get('reason', '')}")

    yield {
        "ok":       ok_count,
        "partial":  part_count,
        "failed":   fail_count,
        "log":      log,
        "cost_usd": cost_usd,
        "tokens":   total_tokens,
        "results":  results_log,
    }


# ── PIPELINE STATUS QUERY ─────────────────────────────────────────────────────

def get_pipeline_status() -> dict:
    """
    Fetch all wptp_pdfs rows and return a status summary dict:
      {
        "rows":      [...],        # full list of dicts
        "counts":    {status: n},  # e.g. {"pending": 12, "extracted": 340, ...}
        "total":     int,
        "with_link": int,
      }
    Statuses: pending | extracted | partial | failed | skipped | (blank → pending)
    """
    get_endpoint = os.environ.get("XANO_GET_ENDPOINT", "")
    if not get_endpoint:
        return {"rows": [], "counts": {}, "total": 0, "with_link": 0}

    all_rows = []
    try:
        for all_rows, _ in _fetch_xano_pages(get_endpoint):
            pass
    except Exception as e:
        return {"rows": [], "counts": {"error": str(e)}, "total": 0, "with_link": 0}

    counts: dict[str, int] = {}
    with_link = 0
    for r in all_rows:
        has_link = 'drive.google.com' in str(r.get('PDF_Link') or r.get('pdf_link') or '')
        if has_link:
            with_link += 1
        raw_status = str(r.get('extraction_status') or '').strip().lower()
        status = raw_status if raw_status in ('extracted', 'partial', 'failed', 'skipped') else 'pending'
        counts[status] = counts.get(status, 0) + 1

    return {
        "rows":      all_rows,
        "counts":    counts,
        "total":     len(all_rows),
        "with_link": with_link,
    }
