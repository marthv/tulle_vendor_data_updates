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


# ── PROMPTS ───────────────────────────────────────────────────────────────────

SUMMARY_PROMPT = """You are an expert at extracting wedding venue pricing data from PDF brochures. Extract exactly the fields listed below and return ONLY a valid JSON object. No markdown, no explanation, just the JSON.

EXTRACTION RULES:

PRICING YEAR:
- Look in the document title, header, or any pricing list heading for the year the pricing applies to.
- Return just the 4-digit year e.g. "2027". If not found, return "Not listed".

VENUE TYPE — assign exactly one from this list using the decision logic below:
  "Dedicated Event Venue" — Fallback only. Use ONLY if no other type clearly applies. Do NOT assign based on keywords alone.
  "Hotel / Resort" — Must be a lodging-first business with guest rooms, accommodations, or a spa. Assign even if it hosts events.
  "Restaurant / Bar" — Must be food-first. Dining is the core offering. Do NOT assign just because a PDF mentions open bar.
  "Estate / Mansion" — Residential-style or mansion property, including historic estates and villa-style venues run as businesses.
  "Performing Arts Venue" — Must be designed for performances: stage, audience seating, productions.
  "Museum / Gallery" — Must be exhibition-based. "Historic building" alone is not enough.
  "Zoo / Aquarium" — Animals or marine exhibits are a core feature.
  "Garden / Botanical Garden" — Must be a plant-focused institution, not just a venue with gardens.
  "Barn / Ranch" — Assign if the venue has a barn as its primary event structure OR a ranch/agricultural setting. Barn aesthetic qualifies: timber frame barns, rustic barns, converted barns, barn-style reception halls. Does NOT require active agricultural production.
  "Winery / Brewery / Distillery" — Beverage production or tasting must be the core identity.
  "Country Club / Private Club" — Must be membership-based. Includes yacht clubs, university clubs, social clubs.
  "University / College" — Must be operated by an academic institution or on campus.
  "Religious" — Must be a place of worship regardless of event usage.
  "Civic / Public" — Must be publicly owned or government-operated, not commercial.

PRICING FIELDS:
- Admin/Service Fee %: Look in fine print, footnotes, bottom of pages. Return just the number (e.g. "22").
- Ceremony Fee: Dollar amount for ceremony add-on. If not listed, return "Not listed".
- Ceremony Fee Type: "Flat rate" or "Per person".
- Venue Space: Named room/space. Multiple spaces separated by |
- Max Capacity Seated: Maximum seated dinner guests for the largest space.
- Venue Fee (Highest/Lowest Sat): Room rental for Saturday only. Highest = most expensive Saturday season. Lowest = least expensive Saturday season.
- F&B Min (Highest/Lowest Sat): F&B minimum spend for Saturday only. Highest = most expensive Saturday season. Lowest = least expensive Saturday season.
- Guest Min (Highest/Lowest Sat): Minimum guest count for Saturday pricing.
- Per Person F&B: Combined food + bar per person, Saturday only.
- Months Highest/Lowest: Which months correspond to highest/lowest Saturday pricing.
- F&B Spend Min Type: "Per Person Min" or "Overall Min Spend".
- Base Menu Per Person: Food only, lowest tier available. Exclude cocktail hour add-ons.
- Base Bar Per Person: Standard/premium open bar with spirits (not beer/wine only packages).
- Additional_Fees: Short labels for MANDATORY fees only, semicolon-separated.
- Additional_Fees_Description: Full descriptions, semicolon-separated, matching order of Additional_Fees.
- MULTIPLE SPACES: Return an ARRAY if multiple distinct bookable spaces exist. Each space gets its own entry with its own capacity, venue fees, and F&B mins. Duplicate all shared fields across every entry.
- If a value is not present anywhere in the document, return "Not listed".

Return this JSON (or array of this JSON for multiple spaces):
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

CLASSIFICATION_PROMPT = """You are classifying a wedding venue PDF brochure. Assign exactly one Venue Offering and one or more Venue Attributes. Return ONLY a valid JSON object. No markdown, no explanation, just the JSON.

VENUE OFFERING — assign exactly one:
"Raw Space" — venue provides just space, zero included services. Negative: any tables, chairs, bar, catering included → not Raw Space.
"Semi-Inclusive" — some services included but outside catering/vendors allowed. DEFAULT for partial services.
"All-Inclusive" — all food/beverage must go through venue. Key test: can client bring outside catering? If NO → All-Inclusive.

VENUE ATTRIBUTES — assign ALL that apply, semicolon-separated:
"Historic Architecture", "Estate / Mansion", "Rooftop / Skyline Views", "Scenic / Nature Views",
"Waterfront", "Garden Setting", "Ballroom", "Industrial / Warehouse", "Greenhouse",
"Natural Light / Large Windows", "Tall / Vaulted Ceilings", "Vineyard", "Barn", "Tented"

Return: {"venue_offering":{"value":"","confidence":"high"},"venue_attributes":{"value":"","confidence":"high"}}
venue_attributes: semicolon-separated list, or "Not listed" if none match."""


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
        usage = msg.usage
        cache_read   = getattr(usage, 'cache_read_input_tokens',   0) or 0
        cache_create = getattr(usage, 'cache_creation_input_tokens', 0) or 0
        cache_note = ""
        if cache_read:
            cache_note = f" (💾 cache hit {cache_read:,} tokens)"
        elif cache_create:
            cache_note = f" (💾 cache miss {cache_create:,} tokens written)"

        raw   = msg.content[0].text.strip()
        clean = re.sub(r'```json|```', '', raw).strip()
        return json.loads(clean), cache_note
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    except Exception as e:
        return None, f"Claude error: {e}"


# ── EXTRACTION ────────────────────────────────────────────────────────────────

def _extract_summary(client, pdf_b64, pdf_id, vendor_id, venue_name):
    parsed, note = call_claude(
        client, pdf_b64, SUMMARY_PROMPT,
        f'Extract all venue pricing fields including pricing year and venue type. PDF_ID="{pdf_id}", Vendor_ID="{vendor_id}", venue="{venue_name}". Return only JSON.',
        max_tokens=4000
    )
    if not parsed:
        return None, note
    if isinstance(parsed, dict):
        parsed = [parsed]
    for e in parsed:
        e['pdf_id']     = {"value": pdf_id,     "confidence": "high"}
        e['vendor_id']  = {"value": vendor_id,  "confidence": "high"}
        e['venue_name'] = {"value": venue_name, "confidence": "high"}
    return parsed, note


def _extract_grid_structure(client, pdf_b64, venue_name):
    parsed, note = call_claude(
        client, pdf_b64, STRUCTURE_PROMPT,
        f'Map the pricing grid structure for "{venue_name}". Return only JSON.',
        max_tokens=2000
    )
    return parsed, note


def _extract_pricing_grid(client, pdf_b64, pdf_id, venue_name, structure):
    structure_context = ""
    if structure:
        structure_context = f"\n\nPricing grid structure map:\n{json.dumps(structure, indent=2)}\n"
    parsed, note = call_claude(
        client, pdf_b64, PRICING_PROMPT,
        f'Extract all pricing. Venue="{venue_name}", PDF_ID="{pdf_id}".{structure_context}Return only the JSON array.',
        max_tokens=8000
    )
    if parsed and isinstance(parsed, dict):
        parsed = [parsed]
    return parsed, note


def _extract_classification(client, pdf_b64, venue_name):
    parsed, note = call_claude(
        client, pdf_b64, CLASSIFICATION_PROMPT,
        f'Classify venue offering and attributes for "{venue_name}". Return only JSON.',
        max_tokens=1000
    )
    return parsed, note


# ── XANO POST ─────────────────────────────────────────────────────────────────

def _post_summary(entries, classification, timestamp):
    summary_endpoint = os.environ["XANO_SUMMARY_ENDPOINT"]
    ok = fail = 0
    venue_offering   = ""
    venue_attributes = ""
    if classification:
        venue_offering   = classification.get("venue_offering",  {}).get("value", "")
        venue_attributes = classification.get("venue_attributes",{}).get("value", "")

    for e in entries:
        raw_fee = e.get("admin_fee_pct", {}).get("value", "")
        fee_m   = re.search(r'(\d+(?:\.\d+)?)', str(raw_fee))
        payload = {
            "PDF_ID":                                                  e.get("pdf_id",     {}).get("value", ""),
            "VENDOR_ID":                                               e.get("vendor_id",  {}).get("value", ""),
            "VENUE_NAME":                                              e.get("venue_name", {}).get("value", ""),
            "Pricing_Year":                                            e.get("pricing_year",{}).get("value", ""),
            "Venue_Type":                                              e.get("venue_type", {}).get("value", ""),
            "Venue_Offering":                                          venue_offering,
            "Venue_Attributes":                                        venue_attributes,
            "Admin_Service_Fee":                                       fee_m.group(1) if fee_m else "",
            "Ceremony_Fee":                                            e.get("ceremony_fee",       {}).get("value", ""),
            "Ceremony_fee_Type":                                       e.get("ceremony_fee_type",  {}).get("value", ""),
            "Venue_Space_Name":                                        e.get("venue_space",        {}).get("value", ""),
            "Max_Capacity_Seated":                                     e.get("max_capacity_seated",{}).get("value", ""),
            "Venue_Fee_on_a_Peak_Season_Saturday":                     e.get("venue_fee_high_sat", {}).get("value", ""),
            "Food_and_Beverage_Min_on_a_Peak_Season_Saturday":         e.get("fb_min_high_sat",    {}).get("value", ""),
            "Guest_Min_Highest_Sat":                                   e.get("guest_min_high_sat", {}).get("value", ""),
            "Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday":  e.get("per_person_fb_high_sat", {}).get("value", ""),
            "Months__Highest_Pricing":                                 e.get("months_highest_pricing", {}).get("value", ""),
            "Venue_Fee_on_Lowest_Saturday":                            e.get("venue_fee_low_sat",  {}).get("value", ""),
            "Food_and_Beverage_Min_on_Lowest_Saturday":                e.get("fb_min_low_sat",     {}).get("value", ""),
            "Guest_Min_Lowest_Sat":                                    e.get("guest_min_low_sat",  {}).get("value", ""),
            "Per_Person_Food_and_Beverage_on_Lowest_Saturday":         e.get("per_person_fb_low_sat", {}).get("value", ""),
            "Months__Lowest_Pricing":                                  e.get("months_lowest_pricing", {}).get("value", ""),
            "FB_Spend_Min_Type":                                       e.get("fb_spend_min_type",  {}).get("value", ""),
            "Base_Menu_Fee_Per_Person":                                e.get("base_menu_per_person",{}).get("value", ""),
            "Base_Bar_Package_Per_Person":                             e.get("base_bar_per_person", {}).get("value", ""),
            "Additional_Fees":                                         e.get("additional_fees",    {}).get("value", ""),
            "Additional_Fees_Description":                             e.get("additional_fees_description", {}).get("value", ""),
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
        payload = {
            "PDF_ID": pdf_id, "Vendor_ID": vendor_id, "Venue_Name": venue_name,
            "Venue_Space_Name":            row.get("Venue_Space_Name", ""),
            "Max_Capacity_Seated":         row.get("Max_Capacity_Seated", ""),
            "Day_of_Week":                 day,
            "Month":                       month,
            "Meal_Type":                   row.get("Meal_Type", "Dinner"),
            "Guest_Min":                   row.get("Guest_Min", ""),
            "Guest_Max":                   row.get("Guest_Max", ""),
            "Venue_Fee":                   row.get("Venue_Fee", ""),
            "Venue_Fee_Type":              row.get("Venue_Fee_Type", ""),
            "FB_Min":                      row.get("FB_Min", ""),
            "FB_Min_Type":                 row.get("FB_Min_Type", ""),
            "Per_Person_FB":               row.get("Per_Person_FB", ""),
            "Base_Menu_Per_Person":        row.get("Base_Menu_Per_Person", ""),
            "Base_Bar_Per_Person":         row.get("Base_Bar_Per_Person", ""),
            "Ceremony_Fee":                row.get("Ceremony_Fee", ""),
            "Ceremony_Fee_Type":           row.get("Ceremony_Fee_Type", ""),
            "Admin_Fee_Pct":               row.get("Admin_Fee_Pct", ""),
            "Tax_Pct":                     row.get("Tax_Pct", ""),
            "Service_Fee_Pct":             row.get("Service_Fee_Pct", ""),
            "Additional_Fees":             row.get("Additional_Fees", ""),
            "Additional_Fees_Description": row.get("Additional_Fees_Description", ""),
            "Notes":                       row.get("Notes", ""),
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


def _fetch_xano_pages(endpoint, per_page=500, progress_cb=None):
    all_rows = []
    page = 1
    while True:
        resp = requests.get(endpoint, params={"page": page, "per_page": per_page}, timeout=30)
        resp.raise_for_status()
        data  = resp.json()
        batch = data if isinstance(data, list) else (data.get("items") or data.get("data") or data.get("result") or [])
        if not batch:
            break
        all_rows.extend(batch)
        if progress_cb:
            progress_cb(len(all_rows))
        if len(batch) < per_page:
            break
        page += 1
    return all_rows


# ── PUBLIC GENERATOR ──────────────────────────────────────────────────────────

def run_extraction(start_row: int, end_row: int | None):
    """
    Generator — yields log strings as extraction proceeds.
    The dashboard iterates this and displays each line in real time.

    Yields strings. Final item is always a dict:
        {"ok": int, "partial": int, "failed": int, "log": [...]}
    """
    log = []

    def emit(msg: str):
        log.append(msg)
        yield msg

    summary_endpoint = os.environ["XANO_SUMMARY_ENDPOINT"]
    get_endpoint     = os.environ["XANO_GET_ENDPOINT"]

    yield from emit("🔍 Checking already-extracted PDF IDs...")
    try:
        existing     = _fetch_xano_pages(summary_endpoint, per_page=500)
        already_done = {str(r.get('PDF_ID') or r.get('pdf_id') or '').strip() for r in existing}
        already_done.discard('')
        yield from emit(f"✓  {len(already_done)} already extracted — will skip")
    except Exception as e:
        yield from emit(f"⚠  Could not fetch existing records: {e}. Proceeding without dedup.")
        already_done = set()

    yield from emit("")
    yield from emit("🔄 Fetching PDF list from Xano (this may take ~30s for large tables)...")
    fetched_count = [0]
    progress_msgs = []
    def _progress(n):
        fetched_count[0] = n
    try:
        all_rows = _fetch_xano_pages(get_endpoint, progress_cb=_progress)
        rows = [r for r in all_rows if 'drive.google.com' in str(r.get('PDF_Link') or r.get('pdf_link') or '')]
        yield from emit(f"✓  {len(all_rows)} total rows, {len(rows)} with Drive links")
    except Exception as e:
        yield from emit(f"❌ Failed to fetch from Xano: {e}")
        yield {"ok": 0, "partial": 0, "failed": 0, "log": log}
        return

    total = len(rows)
    end   = end_row if end_row is not None else total
    batch = rows[start_row:end]
    yield from emit(f"   Processing rows {start_row + 1} → {min(end, total)} ({len(batch)} venues)")
    yield from emit("")

    client        = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    drive_service = get_drive_service()
    yield from emit("✓  Google Drive authenticated")
    yield from emit("")

    results_log = []

    for i, row in enumerate(batch):
        pdf_id     = str(row.get('PDF_ID')    or row.get('pdf_id')    or '').strip()
        vendor_id  = str(row.get('Vendor_ID') or row.get('vendor_id') or '').strip()
        venue_name = str(row.get('Name')      or row.get('name')      or '').strip()
        pdf_link   = str(row.get('PDF_Link')  or row.get('pdf_link')  or '').strip()
        row_num    = start_row + i + 1

        if pdf_id in already_done:
            yield from emit(f"[{row_num}/{total}] {pdf_id} — {venue_name} — ⏭  skipping")
            continue

        yield from emit(f"")
        yield from emit(f"[{row_num}/{total}] {pdf_id} — {venue_name}")
        yield from emit(f"  ↓  Downloading...")

        pdf_bytes, err = download_pdf(pdf_link, drive_service)
        if not pdf_bytes:
            yield from emit(f"  ⚠  Download failed: {err}")
            results_log.append({"pdf_id": pdf_id, "status": "FAILED", "reason": err})
            continue
        yield from emit(f"  ✓  Downloaded ({len(pdf_bytes)//1024}KB)")

        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        if len(pdf_b64) / 1024 / 1024 > 30:
            yield from emit(f"  ⚠  PDF too large (>30MB base64), skipping")
            results_log.append({"pdf_id": pdf_id, "status": "FAILED", "reason": "PDF too large"})
            continue

        timestamp = datetime.now(timezone.utc).isoformat()

        yield from emit(f"  🤖 [1/4] Extracting summary + pricing year + venue type...")
        summary, note = _extract_summary(client, pdf_b64, pdf_id, vendor_id, venue_name)
        if not summary:
            yield from emit(f"  ❌ Summary failed{': ' + note if note else ''}")
            results_log.append({"pdf_id": pdf_id, "status": "FAILED", "reason": "summary extraction failed"})
            continue
        yield from emit(f"  ✓  {len(summary)} space(s){note}")

        yield from emit(f"  🤖 [2/4] Mapping pricing grid structure...")
        structure, note = _extract_grid_structure(client, pdf_b64, venue_name)
        if structure:
            yield from emit(f"  ✓  {len(structure.get('spaces', []))} space(s) mapped{note}")
        else:
            yield from emit(f"  ⚠  Structure mapping failed — proceeding without it{note}")

        yield from emit(f"  🤖 [3/4] Extracting pricing grid...")
        pricing, note = _extract_pricing_grid(client, pdf_b64, pdf_id, venue_name, structure)
        if not pricing:
            yield from emit(f"  ⚠  Pricing grid failed — summary only{note}")
            pricing = []
        else:
            yield from emit(f"  ✓  {len(pricing)} pricing rows{note}")

        yield from emit(f"  🤖 [4/4] Classifying offering + attributes...")
        classification, note = _extract_classification(client, pdf_b64, venue_name)
        if classification:
            offering = classification.get('venue_offering', {}).get('value', '?')
            attrs    = classification.get('venue_attributes', {}).get('value', '?')
            yield from emit(f"  ✓  {offering} | {attrs}{note}")
        else:
            yield from emit(f"  ⚠  Classification failed{note}")

        yield from emit(f"  📤 Posting summary ({len(summary)} row(s))...")
        s_ok, s_fail = _post_summary(summary, classification, timestamp)
        yield from emit(f"  {'✓' if s_fail == 0 else '⚠'}  {s_ok} posted, {s_fail} failed")

        if pricing:
            yield from emit(f"  📤 Posting {len(pricing)} pricing rows...")
            p_ok, p_fail = _post_pricing_grid(pricing, pdf_id, vendor_id, venue_name, timestamp)
            yield from emit(f"  {'✓' if p_fail == 0 else '⚠'}  {p_ok} posted, {p_fail} failed")
        else:
            p_ok = p_fail = 0

        status = "OK" if (s_fail + p_fail) == 0 else "PARTIAL"
        results_log.append({
            "pdf_id": pdf_id, "status": status,
            "summary_rows": s_ok, "pricing_rows": p_ok,
            "failed": s_fail + p_fail,
        })

        if i < len(batch) - 1:
            time.sleep(2)

    ok_count   = sum(1 for r in results_log if r['status'] == 'OK')
    fail_count = sum(1 for r in results_log if r['status'] == 'FAILED')
    part_count = sum(1 for r in results_log if r['status'] == 'PARTIAL')

    yield from emit("")
    yield from emit("─" * 48)
    yield from emit(f"✅ Done — {ok_count} succeeded, {part_count} partial, {fail_count} failed")

    if fail_count:
        yield from emit("Failed:")
        for r in results_log:
            if r['status'] == 'FAILED':
                yield from emit(f"  {r['pdf_id']}: {r.get('reason', '')}")

    yield {"ok": ok_count, "partial": part_count, "failed": fail_count, "log": log}
