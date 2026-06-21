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
import random
import requests
import anthropic
from datetime import datetime, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

# Job progress tracking helper
def _post_extraction_progress(job_type: str = "extraction", current_pdf: str = "", ok: int = 0,
                              failed: int = 0, pending: int = 0, total: int = 0,
                              start_row: int = 0, end_row: int = 0, pdf_ids: list = None,
                              vendor_ids: list = None) -> dict:
    """
    Post extraction progress to the job status endpoint.
    Returns the result summary dict for dashboard display.
    Called periodically from the extraction loop to show live progress.
    """
    result_summary = {
        "current_pdf": current_pdf,
        "ok": ok,
        "failed": failed,
        "pending": pending,
        "total": total,
        "start_row": start_row,
        "end_row": end_row,
        "pdf_count": len(pdf_ids) if pdf_ids else 0,
        "vendor_sample": pdf_ids[:3] if pdf_ids else [],  # First 3 PDFs in job
        "vendor_ids": vendor_ids[:5] if vendor_ids else [],  # First 5 vendors
    }
    return result_summary

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
DAYS   = ["Weekday", "Everyday", "Friday", "Saturday", "Sunday"]

# Claude Sonnet 4 pricing (per token)
_COST_INPUT       = 3.00  / 1_000_000
_COST_OUTPUT      = 15.00 / 1_000_000
_COST_CACHE_WRITE = 3.75  / 1_000_000
_COST_CACHE_READ  = 0.30  / 1_000_000


class CreditExhausted(Exception):
    """Raised when Anthropic reports the credit balance is too low. Signals the
    run to HALT immediately rather than marking every remaining PDF 'failed'
    (which is what torched the 2026-06-04 run — ~761 false failures)."""
    pass


# Substrings that mark a transient API error worth retrying with backoff.
_TRANSIENT_HINTS = (
    "overloaded", "rate_limit", "rate limit", "429", "529", "500", "502", "503",
    "timeout", "timed out", "connection", "temporarily", "getaddrinfo",
    "unable to find the server", "service unavailable",
)


def _slack_alert(text):
    """Best-effort Slack post (run-level alerts like credit-halt). Never raises.
    Uses SLACK_WEBHOOK_URL / slack_webhook_url env var if present; else no-ops."""
    url = os.environ.get("SLACK_WEBHOOK_URL") or os.environ.get("slack_webhook_url") or ""
    if not url:
        return
    try:
        requests.post(url, json={"text": text}, timeout=10)
    except Exception:
        pass


# ── PROMPTS ───────────────────────────────────────────────────────────────────

SUMMARY_PROMPT = """You are an expert at extracting wedding venue pricing data from PDF brochures. Extract exactly the fields listed below and return ONLY a valid JSON object. No markdown, no explanation, just the JSON.

EXTRACTION RULES:

VENDOR TYPE — determine this FIRST, before extracting any other field:
- "venue": the business rents PHYSICAL EVENT SPACE the couple occupies for their wedding (mansion, hotel, restaurant private room, garden, museum, ballroom, barn, vineyard, club, catering hall, estate, etc.). The pricing in the PDF is for renting a space and/or hosting an event there (room fees, F&B minimums, per-person dinner pricing, seasonal Saturday rates, capacity-based pricing).
- "non-venue": the business provides a SERVICE OR PRODUCT, not space. Categories include: planner, photographer, videographer, florist, DJ, band, musician, caterer (mobile/off-site), baker, stationery, transportation, hair/makeup, officiant, rentals, lighting, calligraphy, etc. The pricing is for service packages, hourly rates, package tiers, or per-product fees — NOT room rental or per-person dinner.

Boundary rules:
- Hotels and restaurants that rent rooms/private dining for weddings → "venue".
- A catering company that operates only off-site (no venue space of its own) → "non-venue".
- An all-inclusive estate or planning service tied to a specific physical property → "venue".
- A wedding planner with no physical venue → "non-venue".
- If the PDF shows package tiers like "Day-Of Coordination $2,800" or "Full Planning $8,500" with NO room rental fees and NO F&B minimums → "non-venue" (planner).

IF "non-venue": return ONLY this JSON and STOP. Do NOT extract any other fields:
{"vendor_type":{"value":"non-venue","confidence":"high"},"non_venue_category":{"value":"<one-word category like planner/photographer/florist/dj/band/caterer/baker/stationery/transportation/hair-makeup/officiant/rentals/other>","confidence":"high"}}

IF "venue": include "vendor_type":{"value":"venue","confidence":"high"} at the top of the response and continue with ALL the rules and fields below.

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

- Additional_Fees: Short labels for MANDATORY fees only (fees the
  client MUST pay regardless of choices), semicolon-separated.
  ALWAYS populate this field if mandatory fees exist — even if those
  fees are also captured as numeric values in admin_fee_pct or other
  fields above. This field provides human-readable display labels
  (e.g. 'Administrative Fee; Sales Tax; Security Fee'), not numeric
  amounts. Common examples: administrative fees, service charges,
  gratuity, state tax, mandatory valet, required security. If truly
  no mandatory fees exist anywhere in the document, return empty string.

- Additional_Fees_Description: Full descriptions, semicolon-separated,
  matching order of Additional_Fees.

- Outside Ceremony Space: "yes" if the PDF describes a named bookable
  outdoor area (garden, terrace, lawn, rooftop, beach, courtyard, patio,
  veranda, pergola, etc.) that supports a wedding ceremony — either
  explicitly priced as a ceremony space or explicitly mentioned as
  available for ceremonies.
  "no" if the PDF says ceremonies are indoor-only or makes clear that no
  outdoor space exists at the property.
  "" (empty string) if the PDF does not address outdoor ceremony space
  at all.
  Lowercase only — return exactly "yes", "no", or "".

- Contact Information: The VENUE BUSINESS's own official email address that a
  couple would use to inquire about booking an event (typically an events,
  sales, catering, or info inbox on the venue's OWN company domain, e.g.
  events@thevenuename.com).
  CRITICAL — this is a couples-facing contact, so be extremely conservative:
  * Return an email ONLY if it clearly belongs to the venue itself, on the
    venue's own business/company domain.
  * NEVER return a personal or free-provider email. If the only email in the
    PDF is on a consumer provider — gmail, googlemail, yahoo (any country),
    hotmail/outlook/live/msn, aol, icloud/me/mac, proton/protonmail/pm.me,
    gmx, mail.com, yandex, or any ISP domain (comcast, verizon, att,
    sbcglobal, cox, btinternet, orange.fr, etc.) — return "". A personal
    address is worse than no address.
  * NEVER return an email belonging to a couple/client, a testimonial, a
    photographer/designer credit, or any third-party / preferred vendor —
    only the venue's own inquiry contact.
  * If several venue emails exist, prefer a general events/sales/info inbox
    over a specific person's address.
  * Lowercase the address. Return "" if no suitable venue business email is
    found anywhere in the document.

- Confidentiality / Sharing Restrictions: Scan the ENTIRE document — cover,
  headers, footers, fine print, watermarks, and back matter — for any language
  indicating the PDF is confidential or not meant to be shared publicly.
  Triggers include: "confidential", "proprietary and confidential",
  "do not distribute", "not for distribution", "not for public release",
  "for internal use only", "private and confidential", "do not reproduce/share",
  an explicit NDA / non-disclosure reference, or a stated prohibition on sharing
  pricing with third parties.
  * If ANY such language exists: set confidentiality_risk to "yes" and copy the
    exact triggering phrase (verbatim, trimmed to ~200 chars) into
    confidentiality_evidence.
  * If NONE exists: set confidentiality_risk to "no" and
    confidentiality_evidence to "".
  * A generic copyright line alone (e.g. "© 2026 Venue Name") is NOT a
    confidentiality restriction — do not flag on copyright by itself.
  * Lowercase exactly "yes" or "no" for confidentiality_risk.

- MULTIPLE SPACES: Before returning, count every distinct bookable event
  space in this document. A bookable space is any named room, hall,
  garden, terrace, or area that can be reserved independently and has
  its own pricing listed.
  * If exactly 1 bookable space exists: return a single JSON object.
  * If 2 or more bookable spaces exist: you MUST return a JSON array
    with one entry per space. Returning a single object when multiple
    spaces exist is an error — each space is a separate bookable unit
    with its own pricing and must have its own entry in the array.
  * Never consolidate multiple spaces into one by picking the largest
    or most prominent space.
  * Each entry gets its own Venue_Space_Name, capacity, venue fees,
    and F&B mins specific to that space.
  * Duplicate all shared fields (admin_fee_pct, ceremony_fee,
    additional_fees, pricing_year, venue_type, contact_information)
    across every entry.

- NEVER leave any field blank. Numeric fields get "" if absent.
  Text fields get "" if absent.

Return this JSON for VENUES (or array of these for multiple spaces). For non-venues, return only the 2-field short-circuit shape described above.
{"vendor_type":{"value":"venue","confidence":"high"},"venue_name":{"value":"","confidence":"high"},"pricing_year":{"value":"","confidence":"high"},"venue_type":{"value":"","confidence":"high"},"admin_fee_pct":{"value":"","confidence":"high"},"ceremony_fee":{"value":"","confidence":"high"},"ceremony_fee_type":{"value":"","confidence":"high"},"venue_space":{"value":"","confidence":"high"},"max_capacity_seated":{"value":"","confidence":"high"},"venue_fee_high_sat":{"value":"","confidence":"high"},"fb_min_high_sat":{"value":"","confidence":"high"},"guest_min_high_sat":{"value":"","confidence":"high"},"per_person_fb_high_sat":{"value":"","confidence":"high"},"months_highest_pricing":{"value":"","confidence":"high"},"venue_fee_low_sat":{"value":"","confidence":"high"},"fb_min_low_sat":{"value":"","confidence":"high"},"guest_min_low_sat":{"value":"","confidence":"high"},"per_person_fb_low_sat":{"value":"","confidence":"high"},"months_lowest_pricing":{"value":"","confidence":"high"},"fb_spend_min_type":{"value":"","confidence":"high"},"base_menu_per_person":{"value":"","confidence":"high"},"base_bar_per_person":{"value":"","confidence":"high"},"additional_fees":{"value":"","confidence":"high"},"additional_fees_description":{"value":"","confidence":"high"},"outside_ceremony_space":{"value":"","confidence":"high"},"contact_information":{"value":"","confidence":"high"},"confidentiality_risk":{"value":"no","confidence":"high"},"confidentiality_evidence":{"value":"","confidence":"high"}}"""

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

PRICING_PROMPT_DIRECT = """You are an expert at extracting wedding venue pricing data from PDF brochures.

No structure map has been provided. Find ALL core pricing data in this document and return ONLY a valid JSON array. No markdown, no explanation, just the JSON array.

THE ONLY REASON TO CREATE A NEW ROW:
A new row is created ONLY when at least one of these four dimensions
differs from an existing row:
  1. Venue Space (which room/area)
  2. Day of Week
  3. Month / Season
  4. Meal Type (Dinner / Lunch / Brunch)

If the only thing that differs between two items is the Notes field,
they are NOT separate rows — they are the same row. Do not create
duplicate rows that share the same Space + Day + Month + Meal Type.

A SINGLE-SPACE VENUE with no seasonal variation and no day-of-week
variation should produce AT MOST 3-5 rows (one per named package
tier if tiered packages exist, otherwise just 1 row). If you are
producing 10+ rows for a single space, you are extracting menu
items — stop and reconsider.

WHAT COUNTS AS A ROW:
✓ A named wedding package at a specific price point (e.g. Sprouting
  Love Package at $450/pp = 1 row)
✓ A room rental fee that varies by day (Saturday vs Friday = 2 rows)
✓ A room rental fee that varies by season (peak vs off-peak = 2 rows)
✓ A F&B minimum that varies by day or month
✓ A per-person price that varies by guest count tier

WHAT DOES NOT COUNT AS A ROW — ignore these entirely:
✗ Menu upgrades (lobster upgrade, premium entrée selection)
✗ Add-on food stations (taco bar, flatbread, gyro bar, bao-bun bar)
✗ Late night stations or snacks (sliders, NY stop, cookie shop)
✗ Bar add-ons or extra bar hours (wine & beer bar, kids bar)
✗ Display items or décor add-ons
✗ Staffing fees (chef attendant, bartender fee)
✗ Anything described as "additional", "optional", "upgrade", or
  "enhancement"
✗ Any item where the only unique information would go in Notes

FIELDS TO EXTRACT per row:
- Venue_Space_Name: exact name of the bookable space
- Max_Capacity_Seated: max seated guests for this space
- Day_of_Week: exactly one of "Weekday", "Friday", "Saturday",
  "Sunday", "Everyday". Use "Everyday" if the PDF does not specify
  a particular day or the price applies to all days equally.
- Month: full month name e.g. "January", or "All" only if pricing
  truly does not vary by month at all
- Meal_Type: "Dinner" unless explicitly stated otherwise
- Guest_Min / Guest_Max: minimum and maximum guest counts if specified
- Venue_Fee: room rental fee if applicable
- Venue_Fee_Type: "Flat" or "Per Person"
- FB_Min: food & beverage minimum spend if applicable
- FB_Min_Type: "Overall Min Spend" or "Per Person Min"
- Per_Person_FB: combined per-person food + bar cost if applicable
- Base_Menu_Per_Person: lowest-tier food package per person
- Base_Bar_Per_Person: standard open bar per person
- Ceremony_Fee: ceremony add-on fee
- Ceremony_Fee_Type: "Flat" or "Per person"
- Admin_Fee_Pct: administrative/service fee percentage (number only)
- Tax_Pct: tax percentage (number only)
- Service_Fee_Pct: service charge percentage (number only)
- Additional_Fees: mandatory fee labels, semicolon-separated
- Additional_Fees_Description: full descriptions, semicolon-separated
- Notes: brief note about what this package/tier includes

Return array with these exact keys:
[{"Venue_Space_Name":"","Max_Capacity_Seated":"","Day_of_Week":"","Month":"","Meal_Type":"","Guest_Min":"","Guest_Max":"","Venue_Fee":"","Venue_Fee_Type":"","FB_Min":"","FB_Min_Type":"","Per_Person_FB":"","Base_Menu_Per_Person":"","Base_Bar_Per_Person":"","Ceremony_Fee":"","Ceremony_Fee_Type":"","Admin_Fee_Pct":"","Tax_Pct":"","Service_Fee_Pct":"","Additional_Fees":"","Additional_Fees_Description":"","Notes":""}]"""

CLASSIFICATION_PROMPT = """You are classifying a wedding venue PDF brochure. Assign exactly one Venue Offering, one or more Venue Attributes, one Category, a brief Description, and any Preferred Vendors listed. Return ONLY a valid JSON object. No markdown, no explanation, just the JSON.

VENUE OFFERING — assign exactly one. This field is used as the proxy for "outside catering allowed" on the consumer front-end, so the test below MUST be applied rigorously.
"Raw Space" — venue provides just space, zero included services. Negative test: if the venue includes ANY of tables, chairs, bar, catering, then it is NOT Raw Space.
"Semi-Inclusive" — some services included AND the couple CAN bring outside catering or outside vendors (possibly subject to a fee, an approved-vendor / preferred-vendor list, or a licensed-caterer requirement). DEFAULT when the PDF allows outside vendors OR is silent on whether outside catering is permitted.
"All-Inclusive" — all food and beverage MUST go through the venue. Key test: outside catering is EXPLICITLY prohibited, OR the PDF makes clear that food and beverage cannot be brought in from outside. If you cannot confirm this restriction from the PDF text, choose Semi-Inclusive instead (do not default to All-Inclusive just because the venue offers in-house catering).

VENUE ATTRIBUTES — assign ALL that apply, semicolon-separated. These describe aesthetic / architectural FEATURES only — never the venue's structural identity, which is already captured by VENUE TYPE above. Do NOT emit an attribute that merely restates the venue type: an estate/mansion, a garden/botanical venue, a vineyard/winery, or a barn must NOT be tagged here (use VENUE TYPE for those). Choose only from this list:
"Historic Architecture", "Rooftop / Skyline Views", "Scenic / Nature Views",
"Waterfront", "Ballroom", "Industrial / Warehouse", "Greenhouse",
"Natural Light / Large Windows", "Tall / Vaulted Ceilings", "Tented"

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

DESCRIPTION — one sentence (~25 words max) describing the venue for a couple shopping for wedding vendors.
- Focus on: style + setting + 1 distinguishing feature (e.g. all-inclusive vs flexible, an unusual restriction, a signature offering).
- NEVER mention guest count, capacity, or how many guests the venue hosts/seats/accommodates. Do not include any number of guests, even if the PDF states it.
- Plain factual prose. No marketing fluff, no exclamation points, no "perfect" / "stunning" / "dream".
- Do not repeat the venue name in the sentence.
- Example: "Waterfront mansion in Newport with sweeping ocean views, indoor ballroom, and manicured gardens, with required in-house catering."
- Return "" if the PDF lacks enough information to describe the venue.

PREFERRED VENDORS — extract EVERY third-party business name that the venue partners with, recommends, requires, or includes as part of a package. Be EXHAUSTIVE, not conservative — for a couples-facing filter, capturing every named vendor matters more than being selective.

SCAN EVERY PAGE of the PDF, including the cover, body, appendix/back matter, fine print, captions, and any list-style sections. Wedding venue PDFs frequently bury vendor names in non-obvious places.

INCLUDE business names that appear in ANY of the following contexts:

1. **Dedicated vendor-list sections**, regardless of exact section title:
   "Preferred Vendors", "Recommended Vendors", "Approved Vendors", "Our Partners",
   "Vendor List", "Vendors We Love", "Trusted Partners", "Vendor Directory",
   "Preferred Partners", "Vendors We Recommend", "Approved Vendor List",
   "Frequently Used Vendors", "Our Curated Vendors", "Suggested Vendors",
   "Partner Network", and any synonymous heading.

2. **Package-inclusion lines** that name a vendor delivering the service:
   "Wedding cake by [X]", "Floral arrangements by [Y]", "Bar service provided by [Z]",
   "Photography included from [W]", "DJ services by [V]". The vendor is named because
   they ARE the vendor for that included service — capture them.

3. **Required / in-house / sole-source vendors** that the venue mandates:
   "Use of [X] catering required", "All food must come from our partner [Y]",
   "[Z] is our exclusive caterer", "Bar service through [W]". Even though these
   are required rather than "preferred", they are still named venue partners
   that couples filtering on "venue requires Catering X" would want to find.

4. **"We work with" / "frequently work with" / "have worked with" mentions** that name specific businesses, even outside a formal list.

5. **Photo captions and styled-shoot credits** that name vendors:
   "Photo: [Studio Name]", "Florals: [Florist Name]", "Cake: [Bakery Name]".
   These are typically the venue's go-to vendors for marketing material and are
   reliable indicators of partnership.

EXCLUDE:
- The venue itself, its parent hotel/restaurant chain, its in-house event team, or any name that is clearly the venue's own brand.
- Generic category labels without a business name ("our florist", "the DJ", "the bakery").
- Pricing-PDF design credits (e.g. "Designed by Studio X" at the bottom of a layout-design page — that's the graphic designer, not a wedding vendor).
- Couples / clients named in testimonials or "Mr. & Mrs. Smith" wedding stories.

FORMATTING:
- Return as a single comma-separated string of business names EXACTLY as written in the PDF (preserve capitalization, punctuation, ampersands, "&", "LLC", "Inc.", etc.).
  Example: "Stems Florist, DJ Mike Events, Sweet Cakes Bakery, Le Basque Catering, Ron Ben-Israel Cakes"
- Deduplicate — if the same business name appears multiple times, include it only once.
- Do NOT include vendor category labels in the string — just the business names.
- Do NOT invent, guess, or hallucinate vendors. Only list what is literally named in the PDF.
- If you find 0-2 vendors in a multi-page PDF for a major venue (hotel, mansion, country club), pause and re-scan the entire document including the appendix and back pages — luxury venues almost always partner with multiple vendors. Missing them is a more common error than over-including.

Return "" only if the PDF truly names no third-party vendors anywhere in any of the contexts above.

Return: {"venue_offering":{"value":"","confidence":"high"},"venue_attributes":{"value":"","confidence":"high"},"category":{"value":"","confidence":"high"},"description":{"value":"","confidence":"high"},"preferred_vendors":{"value":"","confidence":"high"}}
venue_attributes: semicolon-separated list, or "Not listed" if none match.
category: one of the listed values, or "" if not confident.
description: one sentence, or "" if insufficient info.
preferred_vendors: comma-separated business names, or "" if none listed."""


# ── MERGED PROMPT (cost-saving: 1 PDF send instead of 3) ──────────────────────
# Composes the three prompts above VERBATIM into one request so the PDF is sent
# once, not three times — the dominant input-token cost. The model performs all
# three tasks reading the PDF once and returns one object {summary, pricing,
# classification}, whose values are byte-for-byte the same shapes the separate
# p1/p3/p4 passes return, so all downstream parsing/posting is unchanged.
_MERGE_INTRO = (
    "You will perform THREE extraction tasks on the SAME wedding-venue PDF, reading "
    "it only ONCE. Return ONE valid JSON object and nothing else (no markdown, no "
    "explanation). It MUST have exactly these three top-level keys:\n"
    '  "summary"        -> the result of TASK A (a JSON object), per TASK A\'s spec\n'
    '  "pricing"        -> the result of TASK B (a JSON array),  per TASK B\'s spec\n'
    '  "classification" -> the result of TASK C (a JSON object), per TASK C\'s spec\n'
    "\n"
    "Each task below says 'return ONLY a JSON object/array'. For THIS combined "
    "request, do NOT return them separately — place each task's JSON as the value of "
    "its key above, all inside one object. Perform all three; never omit one. Apply "
    "every rule in each task's spec exactly as written.\n"
    "\n"
    "If TASK A determines the vendor is NON-VENUE, set \"summary\" to the exact "
    "non-venue JSON TASK A specifies, set \"pricing\" to [], and \"classification\" to {}.\n"
    "\n"
    "================= TASK A — SUMMARY =================\n"
)
MERGED_PROMPT = (
    _MERGE_INTRO
    + SUMMARY_PROMPT
    + "\n\n================= TASK B — PRICING GRID =================\n"
    + PRICING_PROMPT_DIRECT
    + "\n\n================= TASK C — CLASSIFICATION =================\n"
    + CLASSIFICATION_PROMPT
)


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


def _downsample_pdf(pdf_bytes, target_b64_mb=25):
    """Shrink an oversized PDF under Anthropic's ~32MB inline limit by rasterizing
    each page to a JPEG at progressively lower DPI. Lossy (text → image) so only
    used when a PDF is otherwise too large to send. Returns smaller bytes, or
    None if PyMuPDF is unavailable / it can't be shrunk."""
    try:
        import fitz  # PyMuPDF
    except Exception:
        return None
    target_raw = int(target_b64_mb * 1024 * 1024 * 0.74)  # raw ≈ 74% of its base64
    best = None
    # Lower DPI + free each pixmap immediately to bound memory — at 120 DPI with
    # many concurrent workers this OOM-killed the container (exit -9).
    for dpi in (72, 60, 50):
        src = out = None
        try:
            src = fitz.open(stream=pdf_bytes, filetype="pdf")
            out = fitz.open()
            for page in src:
                pix = page.get_pixmap(dpi=dpi)
                img = pix.tobytes(output="jpeg", jpg_quality=60)
                pix = None  # release the raster immediately
                newp = out.new_page(width=page.rect.width, height=page.rect.height)
                newp.insert_image(newp.rect, stream=img)
            data = out.tobytes(deflate=True, garbage=4)
            best = data
            if len(data) <= target_raw:
                return data
        except Exception:
            return best
        finally:
            if src is not None: src.close()
            if out is not None: out.close()
    return best


# ── CLAUDE ────────────────────────────────────────────────────────────────────

# Sonnet 4.6 — same price as Sonnet 4 ($3/$15), better quality, and (unlike the
# older claude-sonnet-4-20250514) supports Files-API file_id document references,
# which is what big batches require. The older model errored on file_id passes.
_MODEL_SONNET = "claude-sonnet-4-6"
_MODEL_HAIKU  = "claude-haiku-4-5-20251001"


def _call_claude_messages(client, content_blocks, system_prompt, max_tokens=6000,
                          model=None):
    """Content-agnostic core of the Claude call. `content_blocks` is the full
    messages[0]['content'] list — the caller decides whether it holds a PDF
    `document` block (call_claude) or a `text` block (call_claude_text).
    Pass `model` to override the default (e.g. _MODEL_HAIKU for classification).

    Returns (parsed_json, cache_note, usage_dict). usage_dict keys: input, output,
    cache_read, cache_create. On error parsed_json is None and usage_dict is {}.
    Retries transient API errors (overload / rate-limit / 5xx / network) and
    empty/garbled JSON with jittered backoff. Raises CreditExhausted on an
    out-of-credits error so the caller can halt the whole run.
    """
    model = model or _MODEL_SONNET
    last_err = ""
    for attempt in range(5):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": content_blocks}]
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
            # Empty/garbled response — often transient; retry a couple times.
            last_err = f"JSON parse error: {e}"
            if attempt < 2:
                time.sleep(2 + random.uniform(0, 1.5))
                continue
            return None, last_err, {}
        except Exception as e:
            last_err = str(e)
            low = last_err.lower()
            if "credit balance is too low" in low:
                raise CreditExhausted(last_err)
            if attempt < 4 and any(h in low for h in _TRANSIENT_HINTS):
                time.sleep(min(30, 4 * (attempt + 1)) + random.uniform(0, 2))
                continue
            return None, f"Claude error: {e}", {}
    return None, f"Claude error: {last_err}", {}


def call_claude(client, pdf_b64, system_prompt, user_text, max_tokens=6000, model=None):
    """PDF extraction call. Sends a cached PDF document block + the instruction text.
    Pass model=_MODEL_HAIKU for cheaper passes (e.g. classification)."""
    blocks = [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
            "cache_control": {"type": "ephemeral"}
        },
        {"type": "text", "text": user_text}
    ]
    return _call_claude_messages(client, blocks, system_prompt, max_tokens, model=model)


def call_claude_text(client, source_text, system_prompt, user_text, max_tokens=6000, model=None):
    """Text variant for scraped web pages / Reddit threads. Same retry / credit /
    return contract as call_claude."""
    blocks = [
        {"type": "text", "text": source_text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": user_text}
    ]
    return _call_claude_messages(client, blocks, system_prompt, max_tokens, model=model)


# ── EXTRACTION ────────────────────────────────────────────────────────────────

def _extract_summary(client, pdf_b64, pdf_id, vendor_id, venue_name):
    parsed, note, usage = call_claude(
        client, pdf_b64, SUMMARY_PROMPT,
        f'First determine vendor_type (venue vs non-venue). If non-venue, return only the 2-field short-circuit. If venue, extract all pricing fields. PDF_ID="{pdf_id}", Vendor_ID="{vendor_id}", venue="{venue_name}". Return only JSON.',
        max_tokens=4000
    )
    if not parsed:
        return None, note, usage
    if isinstance(parsed, dict):
        parsed = [parsed]

    first = parsed[0] if parsed else {}
    vtype_field = first.get('vendor_type', {})
    vtype_val = vtype_field.get('value', '') if isinstance(vtype_field, dict) else str(vtype_field or '')
    if vtype_val.strip().lower() == 'non-venue':
        cat_field = first.get('non_venue_category', {})
        cat_val = cat_field.get('value', '') if isinstance(cat_field, dict) else str(cat_field or '')
        return {"__non_venue__": True, "category": cat_val.strip() or 'unknown'}, note, usage

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
    # Classification is 4 categorical fields — Haiku handles this reliably at 3x lower cost.
    parsed, note, usage = call_claude(
        client, pdf_b64, CLASSIFICATION_PROMPT,
        f'Classify venue offering and attributes for "{venue_name}". Return only JSON.',
        max_tokens=1000, model=_MODEL_HAIKU
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


def _is_yes(value):
    """True only for an explicit affirmative ('yes'/'true'/'1'/'y'); else False.
    Used for the confidentiality_risk flag — anything ambiguous defaults to False
    so we never flag a venue on a malformed value."""
    return str(value or "").strip().lower() in {"yes", "true", "1", "y"}


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
    Returns None if the value cannot be parsed as a number, so Xano
    stores NULL instead of coercing "" → 0.
    """
    if not value:
        return None
    s = str(value).strip()
    if s.lower() in {"not listed", "n/a", "na", "none", "null", "-", ""}:
        return None
    s = re.sub(r'[$€£¥%\s]', '', s)
    s = s.replace(',', '')
    s = re.split(r'[-–—]', s)[0].strip()
    m = re.search(r'\d+(?:\.\d+)?', s)
    if not m:
        return None
    try:
        float(m.group())
        return m.group()
    except ValueError:
        return None


# ── VENDOR EMAIL GATE ─────────────────────────────────────────────────────────
# A vendor contact on a free / personal provider is almost certainly an
# individual's address, not the business — we must never store or surface those.
# This denylist is a hard safety net behind the prompt: even if Claude returns a
# personal address, _to_vendor_email() drops it before it reaches Xano.

_FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.ca", "yahoo.com.au", "yahoo.fr",
    "yahoo.de", "yahoo.es", "yahoo.it", "ymail.com", "rocketmail.com",
    "hotmail.com", "hotmail.co.uk", "hotmail.fr", "hotmail.it", "hotmail.es",
    "outlook.com", "outlook.fr", "outlook.es", "outlook.com.br",
    "live.com", "live.co.uk", "live.nl", "msn.com",
    "aol.com", "aim.com",
    "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "pm.me",
    "gmx.com", "gmx.net", "gmx.de",
    "mail.com", "email.com", "usa.com",
    "yandex.com", "yandex.ru",
    "zoho.com",
    "tutanota.com", "tuta.com", "tutanota.de",
    "hey.com", "fastmail.com",
    # Consumer ISP / telco mailboxes
    "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "bellsouth.net",
    "cox.net", "charter.net", "earthlink.net", "frontier.com", "windstream.net",
    "optonline.net", "roadrunner.com", "rcn.com", "juno.com", "netzero.net",
    "btinternet.com", "sky.com", "talktalk.net", "virginmedia.com", "ntlworld.com",
    "orange.fr", "free.fr", "wanadoo.fr", "laposte.net", "sfr.fr", "neuf.fr",
    "web.de", "t-online.de", "freenet.de",
    "libero.it", "virgilio.it", "alice.it", "tin.it",
}

# Country-TLD variants of the big consumer hosts (hotmail.com.mx, yahoo.com.ph,
# outlook.de, live.com.au, …) — match the family by domain prefix.
_FREEMAIL_PREFIXES = (
    "gmail.", "yahoo.", "hotmail.", "outlook.", "live.",
    "msn.", "gmx.", "yandex.", "ymail.",
)

_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')


def _to_vendor_email(value):
    """
    Validate and gate a candidate VENDOR contact email.

    Returns a lowercased email string ONLY if it is well-formed AND its domain
    is not a known free / personal / ISP provider. Returns None otherwise, so
    Xano stores NULL rather than a personal address we must never expose to
    users. This is the hard backstop to the prompt-level instruction.
    """
    if not value:
        return None
    s = str(value).strip().strip("<>").strip()
    if s.lower() in _NOT_LISTED:
        return None
    m = _EMAIL_RE.search(s)
    if not m:
        return None
    email  = m.group(0).lower()
    domain = email.rsplit("@", 1)[-1]
    if domain in _FREEMAIL_DOMAINS:
        return None
    if any(domain.startswith(p) for p in _FREEMAIL_PREFIXES):
        return None
    return email


# ── XANO STATUS WRITEBACK ─────────────────────────────────────────────────────

def _update_pdf_status(xano_id, status, error="", cost_usd=0.0,
                       confidentiality_flag=False, confidentiality_evidence="",
                       bump_attempts=True):
    """
    PATCH wptp_pdfs/{xano_id} with the new extraction status fields.
    Returns a status string for logging. Never raises.

    Uses XANO_PATCH_PDF_ENDPOINT if set (preferred — dedicated PATCH route).
    Falls back to XANO_GET_ENDPOINT for backwards compatibility.

    confidentiality_flag/evidence are written for the human triage queue. They
    do NOT hide anything from the front-end — flagged venues stay live until
    someone reviews the list. Early-exit callers (download fail, non-venue, etc.)
    leave these at their False/"" defaults since detection happens in Pass 1.
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
        "extraction_status":        status,
        "last_extracted_at":        datetime.now(timezone.utc).isoformat(),
        "last_error":               error[:1000] if error else "",
        "extraction_cost_usd":      round(float(cost_usd), 6),
        "confidentiality_flag":     bool(confidentiality_flag),
        "confidentiality_evidence": (confidentiality_evidence or "")[:1000],
    }
    # The submit-time "batch_submitted" marker passes bump_attempts=False — the real
    # attempt is counted once at ingest, so the marker must not inflate the counter.
    if bump_attempts:
        payload["extraction_attempts"] = 1  # Xano increments server-side (current + 1)
    # Retry on transient 5xx / network errors so a Xano blip doesn't drop the status
    # write (the "Marked 0/N batch_submitted" symptom) or lose an ingest status. 4xx
    # is returned immediately — it won't self-heal.
    last = "no attempt"
    for i in range(3):
        try:
            r = requests.patch(url, json=payload, timeout=10)
            if r.status_code in (200, 201, 204):
                return f"ok ({r.status_code}) → {url}"
            if r.status_code < 500:
                return f"err {r.status_code}: {r.text[:200]}"
            last = f"err {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last = f"exception: {e}"
        if i < 2:
            time.sleep(1.0 * (i + 1))
    return last


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
    venue_offering    = ""
    venue_attributes  = ""
    category          = ""
    description       = ""
    preferred_vendors = ""
    if classification:
        venue_offering    = classification.get("venue_offering",    {}).get("value", "")
        venue_attributes  = classification.get("venue_attributes",  {}).get("value", "")
        category          = classification.get("category",          {}).get("value", "")
        description       = classification.get("description",       {}).get("value", "")
        preferred_vendors = classification.get("preferred_vendors", {}).get("value", "")

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
            "Description":                                             _clean(description),
            "Preferred_Vendors":                                       _clean(preferred_vendors),
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
            "Outside_Ceremony_Space":                                  v("outside_ceremony_space"),
            "Contact_Information":                                     _to_vendor_email(e.get("contact_information", {}).get("value", "")),
            "confidentiality_flag":                                    _is_yes(e.get("confidentiality_risk", {}).get("value", "")),
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
        if not day or day.lower() in {"all", "any", "everyday", "all days", "any day"}:
            day = "Everyday"
        elif day not in DAYS:
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
        # Retry with jittered backoff: Xano throws transient 502/503 Bad Gateway
        # under load, and a single bad page must not abort the whole run. 8 attempts
        # with up to ~30s backoff rides out blips lasting a couple of minutes.
        for attempt in range(8):
            try:
                resp = requests.get(endpoint, params={"page": page, "per_page": per_page}, timeout=30)
                resp.raise_for_status()
                break
            except Exception:
                if attempt == 7:
                    raise
                time.sleep(min(30, 2 * (attempt + 1)) + random.uniform(0, 2))
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


def _fetch_venue_vendor_ids():
    """Return the set of Vendor_IDs whose mapping Category == 'Venue'.

    Reads the slim XANO_VENUE_CATEGORIES_ENDPOINT (Vendor_ID + Category only).
    Returns an EMPTY set if the endpoint is unset or the fetch fails — callers
    MUST treat an empty set as "filter unavailable, do not skip anything"
    (otherwise a bad fetch would skip every PDF). Non-venue vendors are still
    caught downstream by the LLM non-venue short-circuit, so the empty-set
    fallback is safe, just less cost-efficient.
    """
    endpoint = os.environ.get("XANO_VENUE_CATEGORIES_ENDPOINT", "").strip()
    if not endpoint:
        return set()
    try:
        all_rows = []
        for all_rows, _ in _fetch_xano_pages(endpoint):
            pass
        return {
            str(r.get('Vendor_ID') or r.get('vendor_id') or '').strip()
            for r in all_rows
            if str(r.get('Category') or r.get('category') or '').strip().lower() == 'venue'
            and str(r.get('Vendor_ID') or r.get('vendor_id') or '').strip()
        }
    except Exception:
        return set()


# ── BATCH API HELPERS ────────────────────────────────────────────────────────
# 50% cost reduction by submitting via the Batch API (async, up to 24hr).
# Pass 4 (classification) uses Haiku. Pass 2 (grid structure) is skipped —
# PRICING_PROMPT_DIRECT handles it directly. Net: ~55-60% cheaper.

def _parse_batch_text(text):
    """JSON-parse a raw Claude batch response text (same cleanup as _call_claude_messages)."""
    if not text:
        return None
    try:
        return json.loads(re.sub(r'```json|```', '', text.strip()).strip())
    except Exception:
        return None


def _upload_pdf_file(client, pdf_bytes, name):
    """Upload PDF bytes to the Files API → (file_id, error). Retries on the beta
    rate limit (~100 uploads/min) and transient errors."""
    last = None
    for attempt in range(4):
        try:
            up = client.beta.files.upload(
                file=(name, io.BytesIO(pdf_bytes), "application/pdf"))
            return up.id, None
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))   # back off (rate limit / transient)
    return None, str(last)


def _build_batch_requests(file_id, pdf_id, vendor_id, venue_name):
    """Return 3 batch request dicts for one PDF: Pass 1 (summary/Sonnet), Pass 3
    (pricing/Sonnet), Pass 4 (classification/Haiku).

    The PDF is referenced by Files-API file_id (no base64), so each request is a
    few KB — big batches stay well under the 256MB cap. Otherwise this matches the
    known-good base64 path exactly (plain string system prompts, no cache_control)
    so file_id is the only variable vs the batch that processed with 0 errors."""
    doc_block = {
        "type": "document",
        "source": {"type": "file", "file_id": file_id},
    }
    return [
        {
            "custom_id": f"{pdf_id}__p1",
            "params": {
                "model": _MODEL_SONNET, "max_tokens": 4000, "system": SUMMARY_PROMPT,
                "messages": [{"role": "user", "content": [
                    doc_block,
                    {"type": "text", "text": (
                        f'PDF_ID="{pdf_id}", Vendor_ID="{vendor_id}", venue="{venue_name}". '
                        'Return only JSON.')},
                ]}],
            },
        },
        {
            "custom_id": f"{pdf_id}__p3",
            "params": {
                "model": _MODEL_SONNET, "max_tokens": 8000, "system": PRICING_PROMPT_DIRECT,
                "messages": [{"role": "user", "content": [
                    doc_block,
                    {"type": "text", "text": (
                        f'Extract all pricing. Venue="{venue_name}", PDF_ID="{pdf_id}". '
                        'Return only the JSON array.')},
                ]}],
            },
        },
        {
            "custom_id": f"{pdf_id}__p4",
            "params": {
                "model": _MODEL_HAIKU, "max_tokens": 1000, "system": CLASSIFICATION_PROMPT,
                "messages": [{"role": "user", "content": [
                    doc_block,
                    {"type": "text", "text": (
                        f'Classify venue offering and attributes for "{venue_name}". '
                        'Return only JSON.')},
                ]}],
            },
        },
    ]


def _build_merged_batch_request(file_id, pdf_id, vendor_id, venue_name):
    """ONE request per PDF (vs 3) — summary + pricing + classification in a single
    Sonnet response, so the PDF is sent once. ~half the per-PDF input cost. custom_id
    ends in __m; process_batch_results splits the response back into p1/p3/p4."""
    return [{
        "custom_id": f"{pdf_id}__m",
        "params": {
            # Headroom for all three sections (pricing alone can need ~8k) so the
            # combined JSON isn't truncated. Unused tokens aren't billed.
            "model": _MODEL_SONNET, "max_tokens": 16000, "system": MERGED_PROMPT,
            "messages": [{"role": "user", "content": [
                {"type": "document", "source": {"type": "file", "file_id": file_id}},
                {"type": "text", "text": (
                    f'PDF_ID="{pdf_id}", Vendor_ID="{vendor_id}", venue="{venue_name}". '
                    'Return ONE JSON object with keys summary, pricing, classification.')},
            ]}],
        },
    }]


def _select_by_id_range(rows, start_row, end_row):
    """Select rows whose `id` falls in [start_row, end_row] (inclusive). end_row of
    0/None means no upper bound; start_row of 0/None means no lower bound. Matches the
    PDF Status Table's `id` column — what the user types — instead of a list position
    in Xano's (non-id) return order. Result is sorted by id for predictable ordering."""
    lo = int(start_row) if start_row else None
    hi = int(end_row) if end_row else None
    out = []
    for r in rows:
        try:
            rid = int(r.get('id'))
        except (TypeError, ValueError):
            continue
        if lo is not None and rid < lo:
            continue
        if hi is not None and rid > hi:
            continue
        out.append(r)
    out.sort(key=lambda r: int(r.get('id')))
    return out


def run_extraction_batch(
    start_row: int = 0,
    end_row: int | None = None,
    pdf_ids: list | None = None,
    rerun_failed: bool = False,
    merged: bool = None,
):
    """Submit a Batch API job for PDF extraction (50% cost discount, async).
    Downloads PDFs sequentially with a streaming log, then submits all Claude
    requests in one batch. Yields log strings; final item is a dict:
      {batch_submitted: True, batch_id, pdf_count, pdf_map}  — on success
      {batch_submitted: False, error}                         — on failure

    merged=True sends ONE request per PDF (summary+pricing+classification in a single
    Sonnet call) instead of 3 — ~half the input cost. Defaults to the EXTRACT_MERGED
    env flag when not passed explicitly.
    """
    if merged is None:
        merged = os.environ.get("EXTRACT_MERGED", "").strip().lower() in ("1", "true", "yes")
    log = []

    def emit(line):
        log.append(line)
        return (line,)

    yield from emit("Fetching PDF work list...")
    try:
        rows_raw = []
        for rows_raw, _ in _fetch_xano_pages(os.environ.get("XANO_GET_ENDPOINT", "")):
            pass
    except Exception as e:
        yield from emit(f"Failed to fetch PDF list: {e}")
        yield {"batch_submitted": False, "error": str(e)}
        return

    rows_with_links = [r for r in rows_raw
                       if str(r.get('PDF_Link') or r.get('pdf_link') or '').strip()]

    if pdf_ids:
        want = {p.strip() for p in pdf_ids}
        batch = [r for r in rows_with_links
                 if str(r.get('PDF_ID') or r.get('pdf_id') or '').strip() in want]
    elif rerun_failed:
        batch = [r for r in rows_with_links
                 if str(r.get('extraction_status') or '').strip().lower()
                 in ('failed', 'batch_submitted')]
    else:
        # Select by row id (matches the PDF Status Table's `id` column): start_row /
        # end_row are inclusive id bounds. end_row 0/None = no upper bound. This is
        # what the user reads in the table — NOT a position in Xano's return order.
        batch = _select_by_id_range(rows_with_links, start_row, end_row)
        try:
            done_rows = []
            for done_rows, _ in _fetch_xano_pages(os.environ.get("XANO_SUMMARY_ENDPOINT", "")):
                pass
            already = {str(r.get('PDF_ID') or r.get('pdf_id') or '').strip()
                       for r in done_rows if r.get('PDF_ID') or r.get('pdf_id')}
            batch = [r for r in batch
                     if str(r.get('PDF_ID') or r.get('pdf_id') or '').strip() not in already]
        except Exception:
            pass

    venue_vendor_ids = _fetch_venue_vendor_ids()
    if venue_vendor_ids:
        batch = [r for r in batch
                 if str(r.get('Vendor_ID') or r.get('vendor_id') or '').strip() in venue_vendor_ids]

    _mode_txt = "merged 1-call/PDF (~50% cheaper)" if merged else "3 passes/PDF"
    yield from emit(f"{len(batch)} PDF(s) to submit (Batch API — 50% discount · {_mode_txt})")
    if not batch:
        yield from emit("Nothing to do.")
        yield {"batch_submitted": False, "error": "empty_batch"}
        return

    # Download each PDF, upload it once to the Files API, and reference it by
    # file_id in all 3 passes. Requests are now ~KB each (no embedded base64), so
    # the 256MB per-batch cap is effectively a non-issue and big ranges go in one
    # batch. Files-API uploads need the anthropic client, so build it up front.
    drive_service = get_drive_service()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    all_requests, pdf_map = [], {}
    timestamp = datetime.now(timezone.utc).isoformat()

    for i, row in enumerate(batch):
        pdf_id    = str(row.get('PDF_ID')    or row.get('pdf_id')    or '').strip()
        vendor_id = str(row.get('Vendor_ID') or row.get('vendor_id') or '').strip()
        venue_name = str(row.get('Name')     or row.get('name')      or '').strip()
        pdf_link  = str(row.get('PDF_Link')  or row.get('pdf_link')  or '').strip()
        xano_id   = row.get('id')

        yield from emit(f"  [{i+1}/{len(batch)}] {pdf_id} — {venue_name}")
        pdf_bytes, err = download_pdf(pdf_link, drive_service)
        if not pdf_bytes:
            yield from emit(f"    Download failed: {err} — skipping")
            continue
        # Downsample very large PDFs (Files API allows 500MB, but huge PDFs blow
        # past Claude's page limits and slow uploads).
        raw_mb = len(pdf_bytes) / 1024 / 1024
        if raw_mb > 38:
            smaller = _downsample_pdf(pdf_bytes, target_b64_mb=25)
            if smaller and len(smaller) < len(pdf_bytes):
                pdf_bytes = smaller
                raw_mb = len(pdf_bytes) / 1024 / 1024
        if raw_mb > 40:
            yield from emit(f"    Too large ({raw_mb:.0f}MB) — skipping")
            continue

        file_id, up_err = _upload_pdf_file(client, pdf_bytes, f"{pdf_id}.pdf")
        if not file_id:
            yield from emit(f"    Upload failed: {up_err} — skipping")
            continue

        _builder = _build_merged_batch_request if merged else _build_batch_requests
        all_requests.extend(_builder(file_id, pdf_id, vendor_id, venue_name))
        pdf_map[pdf_id] = {"vendor_id": vendor_id, "venue_name": venue_name,
                           "xano_id": xano_id, "timestamp": timestamp}

    if not all_requests:
        yield from emit("No valid PDFs to submit.")
        yield {"batch_submitted": False, "error": "no_valid_pdfs"}
        return

    yield from emit(f"Submitting {len(all_requests)} requests ({len(pdf_map)} PDFs)...")
    # Retry the batch-create on transient errors (Anthropic/Cloudflare 502/503).
    # The PDFs are already uploaded and referenced by file_id in all_requests, so a
    # retry costs nothing extra — without this, a single 502 throws away the whole
    # upload phase. No Claude processing/credits happen until create succeeds.
    batch_obj = None
    last_err = None
    for _attempt in range(4):
        try:
            # Beta path — required for requests that reference Files-API file_ids.
            batch_obj = client.beta.messages.batches.create(
                requests=all_requests, betas=["files-api-2025-04-14"])
            break
        except Exception as e:
            last_err = e
            if _attempt < 3:
                yield from emit(f"  Submit attempt {_attempt + 1} failed ({e}); retrying…")
                time.sleep(min(20, 3 * (_attempt + 1)))
    if batch_obj is None:
        yield from emit(f"Batch submission failed after retries: {last_err}")
        yield from emit("  (PDFs were uploaded but no batch was created — no credits spent. Re-submit to retry.)")
        yield {"batch_submitted": False, "error": str(last_err)}
        return

    batch_id = batch_obj.id
    # Persist batch_id + pdf_map to Xano the instant Anthropic accepts the batch,
    # so an interrupted submit can't lose the only handle to the in-flight batch.
    if pdf_ids:
        submitted_as = f"{len(pdf_map)} PDFs (by id)"
    elif rerun_failed:
        submitted_as = f"{len(pdf_map)} PDFs (rerun-failed)"
    else:
        submitted_as = f"rows {start_row}–{end_row if end_row else 'end'} ({len(pdf_map)} PDFs)"
    try:
        from dashboard import _post_job_status
        _post_job_status(
            "extraction", "completed",
            os.environ.get("LOGGED_IN_USER", "extraction-batch"),
            result_summary={"batch_submitted": True, "batch_id": batch_id,
                            "pdf_count": len(pdf_map), "pdf_map": pdf_map,
                            "submitted_as": submitted_as,
                            "start_row": start_row, "end_row": end_row},
            batch_id=batch_id,
        )
    except Exception:
        pass

    # Mark every PDF in this batch as "batch_submitted" in wptp_pdfs immediately, so
    # the status table reflects the attempt (and when) before results are ingested.
    # bump_attempts=False — the attempt is counted once at ingest. rerun_failed also
    # re-selects rows stuck here, so a batch that's never checked isn't silently lost.
    marked = 0
    for _meta in pdf_map.values():
        if str(_update_pdf_status(_meta.get("xano_id"), "batch_submitted",
                                  error=f"batch={batch_id}", cost_usd=0,
                                  bump_attempts=False)).startswith("ok"):
            marked += 1
    yield from emit(f"Marked {marked}/{len(pdf_map)} PDFs as 'batch_submitted' in Xano.")

    yield from emit(f"Batch submitted — ID: {batch_id}")
    yield from emit("Processing takes up to 24 hours. Use 'Check Batch Results' to poll status.")
    yield {"batch_submitted": True, "batch_id": batch_id,
           "pdf_count": len(pdf_map), "pdf_map": pdf_map}


def list_recent_batches(limit: int = 20):
    """List recent Anthropic Message Batches with live status — the source of
    truth for what's actually queued/processing/done on Anthropic's side,
    independent of whatever we tracked in Xano. Returns a list of plain dicts."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Show times in US Eastern (handles EST/EDT automatically) instead of UTC.
    try:
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")
    except Exception:
        _ET = None

    def _fmt(dt):
        if not dt:
            return ""
        try:
            if _ET is not None:
                return dt.astimezone(_ET).strftime("%Y-%m-%d %I:%M %p ET")
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return str(dt)

    out = []
    # NOTE: iterating the SDK list object auto-paginates through EVERY batch on the
    # account — so cap it at `limit` to avoid a slow crawl through hundreds of old
    # batches (which made "Load batches" appear to hang).
    for b in client.messages.batches.list(limit=limit):
        if len(out) >= limit:
            break
        rc = getattr(b, "request_counts", None)
        succeeded = getattr(rc, "succeeded", 0) if rc else 0
        errored   = getattr(rc, "errored", 0) if rc else 0
        canceled  = getattr(rc, "canceled", 0) if rc else 0
        expired   = getattr(rc, "expired", 0) if rc else 0
        processing = getattr(rc, "processing", 0) if rc else 0
        total = succeeded + errored + canceled + expired + processing
        out.append({
            "id":          b.id,
            "status":      getattr(b, "processing_status", ""),  # in_progress|canceling|ended
            "processing":  processing,
            "succeeded":   succeeded,
            "errored":     errored,
            "canceled":    canceled,
            "expired":     expired,
            # Each PDF = 3 requests (summary + pricing + classification), so the
            # PDF count is the quickest way to tell a 9-PDF test from a 200-PDF run.
            "pdfs":        round(total / 3) if total else 0,
            "created_at":  _fmt(getattr(b, "created_at", None)),
            "ended_at":    _fmt(getattr(b, "ended_at", None)),
            "expires_at":  _fmt(getattr(b, "expires_at", None)),
        })
    return out


def process_batch_results(batch_id: str, pdf_map: dict, wait_secs: int = 30):
    """Poll a submitted batch and post results to Xano when complete.
    Yields log strings; final item is the same contract as run_extraction:
      {batch_done, ok, partial, failed, skipped, cost_usd, results, credit_exhausted, log}
    If still in_progress after wait_secs, yields {batch_done: False} — try again later.
    """
    log = []

    def emit(line):
        log.append(line)
        return (line,)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    yield from emit(f"Checking batch {batch_id}...")

    try:
        batch_obj = client.messages.batches.retrieve(batch_id)
        status = getattr(batch_obj, "processing_status", "")
    except Exception as e:
        yield from emit(f"Could not retrieve batch: {e}")
        yield {"batch_done": False, "error": str(e)}
        return

    yield from emit(f"Status: {status}")
    waited = 0
    while status != "ended" and waited < wait_secs:
        time.sleep(5); waited += 5
        try:
            batch_obj = client.messages.batches.retrieve(batch_id)
            status = getattr(batch_obj, "processing_status", "")
            yield from emit(f"  Still {status} ({waited}s)...")
        except Exception:
            break

    if status != "ended":
        yield from emit("Still processing — check again later.")
        yield {"batch_done": False, "batch_id": batch_id}
        return

    # Collect results grouped by pdf_id
    yield from emit("Batch complete — posting results to Xano...")
    p1: dict = {}; p3: dict = {}; p4: dict = {}; errors: dict = {}
    try:
        for result in client.messages.batches.results(batch_id):
            cid = result.custom_id or ""
            if "__" not in cid:
                continue
            pdf_id_key, pass_tag = cid.rsplit("__", 1)
            if result.result.type != "succeeded":
                # Capture the ACTUAL error, not just "errored" — the detail lives in
                # result.result.error (shape varies: {type,message} or nested .error.error).
                detail = result.result.type
                try:
                    err_obj = getattr(result.result, "error", None)
                    if err_obj is not None:
                        inner = getattr(err_obj, "error", err_obj)
                        etype = getattr(inner, "type", "") or ""
                        emsg = getattr(inner, "message", "") or ""
                        if etype or emsg:
                            detail = f"{result.result.type}/{etype}: {emsg}"[:240]
                except Exception:
                    pass
                errors.setdefault(pdf_id_key, []).append(f"{pass_tag} {detail}")
                continue
            raw_content = (result.result.message.content or [None])[0]
            text = getattr(raw_content, "text", "") if raw_content else ""
            parsed = _parse_batch_text(text)
            if pass_tag == "p1":   p1[pdf_id_key] = parsed
            elif pass_tag == "p3": p3[pdf_id_key] = parsed
            elif pass_tag == "p4": p4[pdf_id_key] = parsed
            elif pass_tag == "m":
                # Merged single-call response → split into the same p1/p3/p4 shapes
                # so all downstream posting is identical to the 3-pass path.
                if isinstance(parsed, dict):
                    p1[pdf_id_key] = parsed.get("summary")
                    p3[pdf_id_key] = parsed.get("pricing")
                    p4[pdf_id_key] = parsed.get("classification")
                else:
                    errors.setdefault(pdf_id_key, []).append("m parse_failed (truncated?)")
    except Exception as e:
        yield from emit(f"Failed to iterate results: {e}")
        yield {"batch_done": False, "error": str(e)}
        return

    results_log = []
    for pdf_id, meta in pdf_map.items():
        vendor_id  = meta["vendor_id"]
        venue_name = meta["venue_name"]
        xano_id    = meta["xano_id"]
        timestamp  = meta["timestamp"]

        summary_data = p1.get(pdf_id)
        pricing_data = p3.get(pdf_id)
        classif_data = p4.get(pdf_id)
        errs         = errors.get(pdf_id, [])

        yield from emit(f"  {pdf_id} — {venue_name}")

        if not summary_data:
            reason = "; ".join(errs) or "p1 missing"
            yield from emit(f"    Failed: {reason}")
            _update_pdf_status(xano_id, "failed", error=reason)
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name,
                                 "status": "FAILED", "reason": reason, "cost_usd": 0})
            continue

        if isinstance(summary_data, dict) and summary_data.get('__non_venue__'):
            cat = summary_data.get('category', 'unknown')
            yield from emit(f"    non-venue ({cat})")
            _update_pdf_status(xano_id, "skipped_non_venue", error=f"non-venue: {cat}")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name,
                                 "status": "SKIPPED", "reason": f"non-venue: {cat}", "cost_usd": 0})
            continue

        if isinstance(summary_data, dict):
            summary_data = [summary_data]
        for e in summary_data:
            e.setdefault('pdf_id',     {"value": pdf_id,    "confidence": "high"})
            e.setdefault('vendor_id',  {"value": vendor_id, "confidence": "high"})
            e.setdefault('venue_name', {"value": venue_name,"confidence": "high"})

        ok_s, fail_s = _post_summary(summary_data, classif_data, timestamp)
        ok_p = fail_p = 0
        if pricing_data:
            if isinstance(pricing_data, dict):
                pricing_data = [pricing_data]
            ok_p, fail_p = _post_pricing_grid(pricing_data, pdf_id, vendor_id, venue_name, timestamp)

        status = ("extracted" if (ok_s and not fail_s and not fail_p)
                  else ("partial" if ok_s else "failed"))
        _update_pdf_status(xano_id, status, cost_usd=0)
        result_status = "OK" if status == "extracted" else ("PARTIAL" if status == "partial" else "FAILED")
        yield from emit(f"    {ok_s} summary rows, {ok_p} pricing rows -> {status}")
        if errs:
            yield from emit(f"    ⚠ pass errors: {'; '.join(errs)}")
        results_log.append({"pdf_id": pdf_id, "venue_name": venue_name,
                             "status": result_status, "summary_rows": ok_s,
                             "pricing_rows": ok_p, "cost_usd": 0, "category": "Venue"})

    ok_c   = sum(1 for r in results_log if r["status"] == "OK")
    part_c = sum(1 for r in results_log if r["status"] == "PARTIAL")
    fail_c = sum(1 for r in results_log if r["status"] == "FAILED")
    skip_c = sum(1 for r in results_log if r["status"] == "SKIPPED")
    yield from emit(f"Done — {ok_c} extracted, {part_c} partial, {skip_c} skipped, {fail_c} failed")
    yield from emit("Cost: ~50% of sequential rate (Batch API discount)")
    yield {"batch_done": True, "ok": ok_c, "partial": part_c, "failed": fail_c,
           "skipped": skip_c, "cost_usd": 0.0, "tokens": {},
           "results": results_log, "credit_exhausted": False, "log": log}


# ── BACKGROUND AUTO-INGEST (Railway cron worker) ──────────────────────────────
# Lets batch results land in Xano WITHOUT a manual "Check Batch Results" click.
# A scheduled worker (batch_worker.py) calls poll_and_ingest_batches() every few
# minutes; any batch that has ENDED on Anthropic and isn't already ingested is
# pulled and posted to Xano. These helpers talk to Xano directly (no Streamlit) so
# the worker can run headless.

def _xano_get_json(url, timeout=20, attempts=5):
    """GET a Xano URL with retry on 5xx/exceptions. Returns parsed JSON or None.
    4xx is returned as None immediately (not retried — it won't self-heal)."""
    for i in range(attempts):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                return None
        except Exception:
            pass
        time.sleep(min(15, 2 * (i + 1)) + random.uniform(0, 1))
    return None


def _list_submitted_batches(limit=40):
    """Return [(batch_id, pdf_map, job_id), …] for extraction jobs that submitted a
    batch, newest first, de-duplicated by batch_id. Mirrors the dashboard's resume
    logic but reads XANO_JOBS_ENDPOINT directly so it has no Streamlit dependency."""
    jobs_endpoint = os.environ.get("XANO_JOBS_ENDPOINT", "").strip()
    if not jobs_endpoint:
        return []
    data = _xano_get_json(
        f"{jobs_endpoint}?job_type=extraction&is_active=false&limit={limit}")
    out, seen = [], set()
    for job in (data or []):
        summary = job.get("result_summary") or {}
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except (json.JSONDecodeError, TypeError):
                summary = {}
        bid = summary.get("batch_id") or job.get("batch_id")
        pmap = summary.get("pdf_map")
        if bid and bid not in seen and isinstance(pmap, dict) and pmap:
            seen.add(bid)
            out.append((bid, pmap, job.get("id")))
    return out


def _batch_already_ingested(pdf_map):
    """True if this batch's PDFs have already left 'batch_submitted' status — i.e.
    it was already ingested (manually or by a prior worker run). Used as the
    idempotency guard so the worker never re-ingests and duplicates table-36 rows.
    Samples a few of the batch's PDFs against wptp_pdfs. Conservative: if it can't
    verify, returns False (allow ingest) — process_batch_results is the backstop."""
    patch_base = (os.environ.get("XANO_PATCH_PDF_ENDPOINT", "").rstrip("/")
                  or os.environ.get("XANO_GET_ENDPOINT", "").rstrip("/"))
    if not patch_base:
        return False
    ids = [m.get("xano_id") for m in pdf_map.values() if m.get("xano_id")][:3]
    if not ids:
        return False
    checked = still_submitted = 0
    for xid in ids:
        row = _xano_get_json(f"{patch_base}/{xid}")
        if row is None:
            continue
        checked += 1
        if str(row.get("extraction_status") or "").strip().lower() == "batch_submitted":
            still_submitted += 1
    if checked == 0:
        return False
    return still_submitted == 0


def poll_and_ingest_batches(log=print):
    """Find submitted batches and ingest any that have ENDED on Anthropic and not
    yet been ingested — posting results to Xano via process_batch_results. Idempotent:
    a batch whose PDFs already left 'batch_submitted' is skipped, so table-36 rows
    aren't duplicated. Returns a summary dict. Safe to run on a schedule."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    summary = {"checked": 0, "ingested": 0, "still_processing": 0,
               "skipped": 0, "errors": 0, "batches": []}
    for bid, pmap, _job_id in _list_submitted_batches():
        summary["checked"] += 1
        if _batch_already_ingested(pmap):
            summary["skipped"] += 1
            summary["batches"].append({"batch_id": bid, "result": "already_ingested"})
            continue
        try:
            obj = client.messages.batches.retrieve(bid)
            status = getattr(obj, "processing_status", "")
        except Exception as e:
            summary["errors"] += 1
            summary["batches"].append({"batch_id": bid, "result": f"retrieve_error: {e}"})
            continue
        if status != "ended":
            summary["still_processing"] += 1
            summary["batches"].append({"batch_id": bid, "result": f"status:{status}"})
            continue
        log(f"Ingesting ended batch {bid} ({len(pmap)} PDFs)…")
        final = None
        try:
            for item in process_batch_results(bid, pmap, wait_secs=0):
                if isinstance(item, dict):
                    final = item
        except Exception as e:
            summary["errors"] += 1
            summary["batches"].append({"batch_id": bid, "result": f"ingest_error: {e}"})
            continue
        if final and final.get("batch_done"):
            summary["ingested"] += 1
            summary["batches"].append({
                "batch_id": bid, "result": "ingested",
                "ok": final.get("ok"), "partial": final.get("partial"),
                "failed": final.get("failed"), "skipped": final.get("skipped")})
            log(f"  ✓ {bid}: {final.get('ok')} ok, {final.get('failed')} failed")
        else:
            summary["batches"].append({"batch_id": bid, "result": "ingest_incomplete"})
    return summary


def ingest_batch_by_id(batch_id, wait_secs=0):
    """Recovery ingest using ONLY a batch id — no saved job record / pdf_map needed.
    Reconstructs the pdf_map from the batch's OWN result custom_ids (each is
    "<PDF_ID>__<pass>") joined to wptp_pdfs for vendor_id / venue_name / xano_id, then
    runs the normal ingest. This rescues a batch whose job record was lost (e.g. a
    Xano outage at submit time, where the dashboard never persisted batch_id+pdf_map).
    Yields log strings; final item is the process_batch_results contract dict."""
    log = []

    def emit(line):
        log.append(line)
        return (line,)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    yield from emit(f"Reconstructing batch {batch_id} from its results…")

    # 1) Pull the authoritative PDF_IDs straight out of the batch result custom_ids.
    pdf_ids = set()
    try:
        for result in client.messages.batches.results(batch_id):
            cid = result.custom_id or ""
            if "__" in cid:
                pdf_ids.add(cid.rsplit("__", 1)[0])
    except Exception as e:
        yield from emit(f"Could not read batch results (is it ended yet?): {e}")
        yield {"batch_done": False, "error": str(e)}
        return
    if not pdf_ids:
        yield from emit("No PDF ids found in this batch's results.")
        yield {"batch_done": False, "error": "no_pdf_ids"}
        return
    yield from emit(f"{len(pdf_ids)} PDFs in batch — looking up vendors in wptp_pdfs…")

    # 2) Join to wptp_pdfs for vendor_id / venue_name / xano_id.
    rows = []
    try:
        for rows, _ in _fetch_xano_pages(os.environ.get("XANO_GET_ENDPOINT", "")):
            pass
    except Exception as e:
        yield from emit(f"Could not fetch wptp_pdfs (Xano down?): {e}")
        yield {"batch_done": False, "error": str(e)}
        return
    by_pdf = {}
    for r in rows:
        pid = str(r.get('PDF_ID') or r.get('pdf_id') or '').strip()
        if pid:
            by_pdf[pid] = r

    timestamp = datetime.now(timezone.utc).isoformat()
    pdf_map, missing = {}, []
    for pid in pdf_ids:
        r = by_pdf.get(pid)
        if not r:
            missing.append(pid)
            continue
        pdf_map[pid] = {
            "vendor_id":  str(r.get('Vendor_ID') or r.get('vendor_id') or '').strip(),
            "venue_name": str(r.get('Name') or r.get('name') or '').strip(),
            "xano_id":    r.get('id'),
            "timestamp":  timestamp,
        }
    if missing:
        yield from emit(f"⚠ {len(missing)} PDF id(s) not in wptp_pdfs: {', '.join(sorted(missing)[:10])}")
    if not pdf_map:
        yield from emit("No PDFs could be mapped to vendors — aborting.")
        yield {"batch_done": False, "error": "no_mapping"}
        return
    yield from emit(f"Mapped {len(pdf_map)} PDFs — ingesting to Xano…")

    # 3) Hand off to the normal ingest path.
    yield from process_batch_results(batch_id, pdf_map, wait_secs=wait_secs)


def validate_merge(pdf_ids, log=None):
    """Run BOTH the 3-pass path and the merged 1-call path on the same PDFs
    (synchronously) and report whether the merged output matches — so the cost-saving
    merge can be trusted BEFORE switching real batches to it. Yields log strings;
    final item is {results:[…], recommend: bool}. Costs a few PDFs' worth of tokens."""
    def emit(line):
        if log:
            log(line)
        return (line,)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    drive_service = get_drive_service()

    want = [str(p).strip() for p in pdf_ids if str(p).strip()]
    yield from emit(f"Validating merge on {len(want)} PDF(s): {', '.join(want)}")
    rows = []
    try:
        for rows, _ in _fetch_xano_pages(os.environ.get("XANO_GET_ENDPOINT", "")):
            pass
    except Exception as e:
        yield from emit(f"Could not fetch wptp_pdfs: {e}")
        yield {"results": [], "recommend": False, "error": str(e)}
        return
    by_pdf = {str(r.get('PDF_ID') or '').strip(): r for r in rows if r.get('PDF_ID')}

    def _sync(model, max_tokens, system, file_id, user_text):
        msg = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": [
                {"type": "document", "source": {"type": "file", "file_id": file_id}},
                {"type": "text", "text": user_text}]}])
        raw = (msg.content or [None])[0]
        return _parse_batch_text(getattr(raw, "text", "") if raw else "")

    def _val(d, k):
        v = d.get(k) if isinstance(d, dict) else None
        return v.get("value") if isinstance(v, dict) else v

    def _rows(x):
        return len(x) if isinstance(x, list) else (1 if isinstance(x, dict) else 0)

    results = []
    for pid in want:
        r = by_pdf.get(pid)
        if not r:
            yield from emit(f"{pid}: not found in wptp_pdfs — skipping")
            continue
        vname = str(r.get('Name') or '').strip()
        vid = str(r.get('Vendor_ID') or '').strip()
        link = str(r.get('PDF_Link') or '').strip()
        yield from emit(f"── {pid} — {vname} ──")
        pdf_bytes, err = download_pdf(link, drive_service)
        if not pdf_bytes:
            yield from emit(f"  download failed: {err}")
            continue
        file_id, up_err = _upload_pdf_file(client, pdf_bytes, f"{pid}.pdf")
        if not file_id:
            yield from emit(f"  upload failed: {up_err}")
            continue
        try:
            yield from emit("  running 3-pass…")
            s1 = _sync(_MODEL_SONNET, 4000, SUMMARY_PROMPT, file_id, f'PDF_ID="{pid}". Return only JSON.')
            s3 = _sync(_MODEL_SONNET, 8000, PRICING_PROMPT_DIRECT, file_id, 'Extract all pricing. Return only the JSON array.')
            s4 = _sync(_MODEL_HAIKU, 1000, CLASSIFICATION_PROMPT, file_id, f'Classify "{vname}". Return only JSON.')
            yield from emit("  running merged (1 call)…")
            m = _sync(_MODEL_SONNET, 16000, MERGED_PROMPT, file_id,
                      f'PDF_ID="{pid}". Return ONE JSON object with keys summary, pricing, classification.')
        except Exception as e:
            yield from emit(f"  API error: {e}")
            continue
        m_sum = m.get("summary") if isinstance(m, dict) else None
        m_pri = m.get("pricing") if isinstance(m, dict) else None
        m_cls = m.get("classification") if isinstance(m, dict) else None

        cmp = {
            "pdf_id": pid, "venue": vname,
            "merged_parsed": isinstance(m, dict) and m_sum is not None,
            "vendor_type":  (_val(s1, 'vendor_type'),  _val(m_sum, 'vendor_type')),
            "pricing_year": (_val(s1, 'pricing_year'), _val(m_sum, 'pricing_year')),
            "pricing_rows": (_rows(s3), _rows(m_pri)),
            "offering":     (_val(s4, 'venue_offering'), _val(m_cls, 'venue_offering')),
            "category":     (_val(s4, 'category'),       _val(m_cls, 'category')),
        }
        results.append(cmp)
        yield from emit(f"  merged parsed:  {cmp['merged_parsed']}")
        yield from emit(f"  vendor_type:    3pass={cmp['vendor_type'][0]} | merged={cmp['vendor_type'][1]}")
        yield from emit(f"  pricing rows:   3pass={cmp['pricing_rows'][0]} | merged={cmp['pricing_rows'][1]}")
        yield from emit(f"  offering:       3pass={cmp['offering'][0]} | merged={cmp['offering'][1]}")
        yield from emit(f"  category:       3pass={cmp['category'][0]} | merged={cmp['category'][1]}")

    def _rows_close(a, b):
        return abs(a - b) <= max(1, 0.25 * max(a, 1))

    ok = bool(results) and all(
        c["merged_parsed"]
        and c["vendor_type"][0] == c["vendor_type"][1]
        and _rows_close(c["pricing_rows"][0], c["pricing_rows"][1])
        for c in results)
    yield from emit("")
    yield from emit("✅ Merged matches the 3-pass output — safe to enable." if ok
                    else "⚠ Differences found — review above before enabling merged mode.")
    yield {"results": results, "recommend": ok}


# ── PUBLIC GENERATOR ──────────────────────────────────────────────────────────

def run_extraction(
    start_row: int,
    end_row: int | None,
    pdf_ids: list[str] | None = None,
    rerun_failed: bool = False,
    force_all: bool = False,
):
    """
    Generator — yields log strings as extraction proceeds.
    The dashboard iterates this and displays each line in real time.

    Modes (mutually exclusive, checked in order):
      pdf_ids      — run only the specified PDF_ID strings
      rerun_failed — run only rows where extraction_status == "failed"
      start_row / end_row — original row-range behaviour (default)

    force_all — within the default row-range mode, re-extract every row even if
      it is already present in table 36 (skips the dedup check). Used for a full
      refresh. The venue pre-filter still applies (non-venues are skipped). NOTE:
      table 36 / 37 posts APPEND, so a forced re-run leaves a fresh generation of
      rows behind older ones — clear table 37 beforehand and prune table 36's
      stale rows afterward (see the run runbook).

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
        # Re-run anything previously marked failed, plus rows stuck in batch_submitted
        # (a batch was fired but its results were never checked/ingested).
        batch = [
            r for r in rows_with_links
            if str(r.get('extraction_status') or '').strip().lower()
            in ('failed', 'batch_submitted')
        ]
        yield from emit(f"   Mode: re-run failed — {len(batch)} rows")

    else:
        # Default: select by row id (matches the PDF Status Table `id` column),
        # inclusive bounds; end_row 0/None = no upper bound. Skips already-extracted.
        batch = _select_by_id_range(rows_with_links, start_row, end_row)
        _hi_txt = end_row if end_row else "end"
        yield from emit(f"   Mode: id {start_row} → {_hi_txt} ({len(batch)} venues)")

    # ── For default mode: skip already-extracted (dedup by PDF_ID in summary table) ──
    already_done: set[str] = set()
    if force_all and not pdf_ids and not rerun_failed:
        yield from emit("")
        yield from emit("♻️  FORCE-ALL mode — dedup disabled; every venue row will be re-extracted (table 36/37 posts append)")
    if not pdf_ids and not rerun_failed and not force_all:
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

    # ── Venue pre-filter: load Vendor_IDs categorized 'Venue' in wptp_updated_mappings ──
    # Non-venue vendors are skipped BEFORE any download or model call (cost saver).
    venue_vendor_ids = _fetch_venue_vendor_ids()
    if venue_vendor_ids:
        yield from emit(f"✓  Venue filter active — {len(venue_vendor_ids)} venue vendors loaded; non-venue PDFs skip download + extraction")
    else:
        yield from emit("⚠  Venue filter inactive (XANO_VENUE_CATEGORIES_ENDPOINT unset or fetch failed) — relying on LLM non-venue detection only")
    yield from emit("")

    client        = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    drive_service = get_drive_service()
    yield from emit("✓  Google Drive authenticated")
    yield from emit("")

    results_log  = []
    credit_halted = False   # set True if Anthropic reports the credit balance is too low
    total_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    total_rows   = len(rows_with_links)

    # Progress tracking for job status updates
    processed_count = 0
    current_pdf  = ""
    last_progress_update = time.time()
    progress_update_interval = 30  # seconds between progress updates

    def _add_usage(u):
        for k in total_tokens:
            total_tokens[k] += u.get(k, 0)

    def _maybe_post_progress():
        """Post progress update if enough time has passed."""
        nonlocal last_progress_update
        now = time.time()
        if now - last_progress_update >= progress_update_interval:
            try:
                from dashboard import _post_job_status
                user_email = os.environ.get("LOGGED_IN_USER", "extraction-batch")
                # Count current results by status
                ok = sum(1 for r in results_log if r.get('status') == 'OK')
                failed = sum(1 for r in results_log if r.get('status') == 'FAILED')
                pending = len(batch) - len(results_log)
                # Extract job params
                batch_pdfs = [str(r.get('PDF_ID') or r.get('pdf_id') or '').strip() for r in batch[:5]]
                batch_vendors = [str(r.get('Vendor_ID') or r.get('vendor_id') or '').strip() for r in batch[:5]]
                progress = _post_extraction_progress(
                    current_pdf=current_pdf,
                    ok=ok,
                    failed=failed,
                    pending=pending,
                    total=len(batch),
                    start_row=start_row,
                    end_row=min(start_row + len(batch), total_rows),
                    pdf_ids=batch_pdfs,
                    vendor_ids=batch_vendors
                )
                _post_job_status("extraction", "running", user_email, progress)
            except Exception:
                pass  # Fail silently — don't interrupt extraction
            last_progress_update = now

    for i, row in enumerate(batch):
        pdf_id    = str(row.get('PDF_ID')    or row.get('pdf_id')    or '').strip()
        vendor_id = str(row.get('Vendor_ID') or row.get('vendor_id') or '').strip()
        venue_name = str(row.get('Name')     or row.get('name')      or '').strip()
        pdf_link  = str(row.get('PDF_Link')  or row.get('pdf_link')  or '').strip()
        xano_id   = row.get('id')   # Xano integer primary key — used for PATCH
        row_num   = i + 1           # position within this batch (selection is by id now)

        # Update progress tracking
        current_pdf = pdf_id
        pending_count = len(batch) - i
        _maybe_post_progress()

        # Default mode dedup
        if not pdf_ids and not rerun_failed and pdf_id in already_done:
            yield from emit(f"[{row_num}/{len(batch)}] {pdf_id} — {venue_name} — ⏭  skipping (already extracted)")
            continue

        # Venue pre-filter: skip non-venue vendors entirely (no download, no model calls).
        # Only fires when the venue set loaded successfully; an empty set disables the gate.
        if venue_vendor_ids and vendor_id and vendor_id not in venue_vendor_ids:
            yield from emit(f"[{row_num}/{len(batch)}] {pdf_id} — {venue_name} — ⏭  skipping (vendor not categorized 'Venue')")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name, "status": "SKIPPED", "reason": "non-venue: mapping category != Venue", "cost_usd": 0})
            _update_pdf_status(xano_id, "skipped_non_venue", error="non-venue: mapping category != Venue")
            _maybe_post_progress()
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
            _maybe_post_progress()
            continue
        yield from emit(f"  ✓  Downloaded ({len(pdf_bytes)//1024}KB)")

        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        b64_mb = len(pdf_b64) / 1024 / 1024
        if b64_mb > 50:
            msg = "PDF too large (>50MB base64)"
            yield from emit(f"  ⚠  {msg}, skipping")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name, "status": "FAILED", "reason": msg})
            patch_result = _update_pdf_status(xano_id, "failed", error=msg)
            yield from emit(f"  📝 Status writeback: {patch_result}")
            _maybe_post_progress()
            continue
        if b64_mb > 30:
            # Over Anthropic's ~32MB inline request limit — downsample to fit.
            smaller = _downsample_pdf(pdf_bytes, target_b64_mb=25)
            if smaller and len(smaller) < len(pdf_bytes):
                pdf_b64 = base64.standard_b64encode(smaller).decode("utf-8")
                yield from emit(f"  ⬇  Downsampled {b64_mb:.1f}MB → {len(pdf_b64)/1024/1024:.1f}MB base64 to fit API limit")
            else:
                yield from emit(f"  ⚠  {b64_mb:.1f}MB over API inline limit, downsample unavailable — may fail")

        timestamp    = datetime.now(timezone.utc).isoformat()
        run_usage    = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

        def _track(u):
            _add_usage(u)
            for k in run_usage:
                run_usage[k] += u.get(k, 0)

        # ── Pass 1: Summary ───────────────────────────────────────────────────
        yield from emit(f"  🤖 [1/4] Extracting summary + pricing year + venue type...")
        try:
            summary, note, usage = _extract_summary(client, pdf_b64, pdf_id, vendor_id, venue_name)
        except CreditExhausted as ce:
            credit_halted = True
            yield from emit(f"  🛑 CREDIT BALANCE EXHAUSTED at [{row_num}] — halting this worker to avoid mass false-failures.")
            yield from emit(f"     ({ce})")
            yield from emit("  ℹ  Remaining rows left PENDING (not marked failed) — add credits, then rerun_failed / re-run the range.")
            _slack_alert(f"🛑 Tulle extraction HALTED — Anthropic credit balance too low (stopped near row {row_num}/{total_rows}, pdf {pdf_id}). Add credits + re-run.")
            break
        _track(usage)
        if not summary:
            msg = f"Summary extraction failed: {note}"
            yield from emit(f"  ❌ {msg}")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name, "status": "FAILED", "reason": msg})
            patch_result = _update_pdf_status(xano_id, "failed", error=msg, cost_usd=_compute_cost(run_usage))
            yield from emit(f"  📝 Status writeback: {patch_result}")
            continue

        # ── Non-venue short-circuit ───────────────────────────────────────────
        if isinstance(summary, dict) and summary.get('__non_venue__'):
            cat = summary.get('category', 'unknown')
            run_cost = _compute_cost(run_usage)
            yield from emit(f"  ⏭  Non-venue vendor detected ({cat}) — skipping pricing extraction{note}")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name, "status": "SKIPPED", "reason": f"non-venue: {cat}", "cost_usd": run_cost})
            patch_result = _update_pdf_status(xano_id, "skipped_non_venue", error=f"non-venue: {cat}", cost_usd=run_cost)
            yield from emit(f"  📝 Status → skipped_non_venue (${run_cost:.4f}) · writeback: {patch_result}")
            if i < len(batch) - 1:
                time.sleep(1)
            continue

        yield from emit(f"  ✓  {len(summary)} space(s){note}")

        # ── Confidentiality flag (PDF-level; any space flagging = PDF flagged) ──
        conf_flag = any(_is_yes(e.get("confidentiality_risk", {}).get("value", "")) for e in summary)
        conf_evidence = next(
            (str(e.get("confidentiality_evidence", {}).get("value", "")).strip()
             for e in summary
             if str(e.get("confidentiality_evidence", {}).get("value", "")).strip()),
            "",
        )
        if conf_flag:
            yield from emit(f"  🚩 Confidentiality risk flagged — for review (stays live): \"{conf_evidence[:140]}\"")

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
            yield from emit(f"  ⚠  Pricing grid with structure failed{note} — trying direct extraction...")
            # Fallback: attempt extraction without structure map using format-agnostic prompt
            try:
                msg = client.messages.create(
                    model=_MODEL_SONNET,
                    max_tokens=8000,
                    system=PRICING_PROMPT_DIRECT,
                    messages=[{"role": "user", "content": [
                        {
                            "type": "document",
                            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
                            "cache_control": {"type": "ephemeral"}
                        },
                        {"type": "text", "text": f'Extract all pricing data from this venue PDF. Venue="{venue_name}", PDF_ID="{pdf_id}". Return only the JSON array.'}
                    ]}]
                )
                fb_usage = msg.usage
                fb_usage_dict = {
                    "input":        getattr(fb_usage, 'input_tokens',                0) or 0,
                    "output":       getattr(fb_usage, 'output_tokens',               0) or 0,
                    "cache_read":   getattr(fb_usage, 'cache_read_input_tokens',     0) or 0,
                    "cache_create": getattr(fb_usage, 'cache_creation_input_tokens', 0) or 0,
                }
                _track(fb_usage_dict)
                raw   = msg.content[0].text.strip()
                clean = re.sub(r'```json|```', '', raw).strip()
                pricing = json.loads(clean)
                if isinstance(pricing, dict):
                    pricing = [pricing]
                if pricing:
                    yield from emit(f"  ✓  Direct extraction: {len(pricing)} pricing rows (💾 cache hit)" if fb_usage_dict['cache_read'] else f"  ✓  Direct extraction: {len(pricing)} pricing rows")
                else:
                    yield from emit(f"  ⚠  Direct extraction returned empty — summary only")
                    pricing = []
            except Exception as fb_err:
                yield from emit(f"  ⚠  Direct extraction failed: {fb_err} — summary only")
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
            desc_val  = classification.get('description',     {}).get('value', '') or ''
            prefs_val = classification.get('preferred_vendors',{}).get('value', '') or ''
            n_prefs   = len([p for p in prefs_val.split(',') if p.strip()]) if prefs_val else 0
            yield from emit(f"  ✓  {offering} | {category} | {attrs}{note}")
            yield from emit(f"  ✓  desc {len(desc_val)} chars · {n_prefs} preferred vendor(s)")
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
            status                   = status,
            error                    = error_msg,
            cost_usd                 = run_cost,
            confidentiality_flag     = conf_flag,
            confidentiality_evidence = conf_evidence,
        )
        yield from emit(f"  📝 Status → {status} (${run_cost:.4f}){' · 🚩 confidential' if conf_flag else ''} · writeback: {patch_result}")

        results_log.append({
            "pdf_id":       pdf_id,
            "venue_name":   venue_name,
            "status":       "OK" if all_failed == 0 else "PARTIAL",
            "summary_rows": s_ok,
            "pricing_rows": p_ok,
            "failed":       all_failed,
            "cost_usd":     run_cost,
            "confidentiality_flag":     conf_flag,
            "confidentiality_evidence": conf_evidence,
            "offering":     classification.get('venue_offering',  {}).get('value', '') if classification else '',
            "category":     classification.get('category',        {}).get('value', '') if classification else '',
            "attributes":   classification.get('venue_attributes',{}).get('value', '') if classification else '',
        })

        _maybe_post_progress()

        if i < len(batch) - 1:
            time.sleep(2)

    # Final progress update
    current_pdf = ""
    _maybe_post_progress()

    # ── Summary ───────────────────────────────────────────────────────────────
    ok_count   = sum(1 for r in results_log if r['status'] == 'OK')
    fail_count = sum(1 for r in results_log if r['status'] == 'FAILED')
    part_count = sum(1 for r in results_log if r['status'] == 'PARTIAL')
    skip_count = sum(1 for r in results_log if r['status'] == 'SKIPPED')

    cost_usd = (
        total_tokens["input"]        * _COST_INPUT       +
        total_tokens["output"]       * _COST_OUTPUT      +
        total_tokens["cache_create"] * _COST_CACHE_WRITE +
        total_tokens["cache_read"]   * _COST_CACHE_READ
    )

    yield from emit("")
    yield from emit("─" * 48)
    if credit_halted:
        yield from emit("🛑 HALTED — Anthropic credit balance too low. Add credits, then re-run.")
    yield from emit(f"✅ Done — {ok_count} succeeded, {part_count} partial, {skip_count} skipped (non-venue), {fail_count} failed")
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
        "ok":                 ok_count,
        "partial":            part_count,
        "skipped_non_venue":  skip_count,
        "failed":             fail_count,
        "log":                log,
        "cost_usd":           cost_usd,
        "tokens":             total_tokens,
        "results":            results_log,
        "credit_exhausted":   credit_halted,
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
    Statuses: pending | extracted | partial | failed | skipped | skipped_non_venue | (blank → pending)
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
        status = raw_status if raw_status in ('extracted', 'partial', 'failed', 'skipped', 'skipped_non_venue', 'batch_submitted') else 'pending'
        counts[status] = counts.get(status, 0) + 1

    return {
        "rows":      all_rows,
        "counts":    counts,
        "total":     len(all_rows),
        "with_link": with_link,
    }
