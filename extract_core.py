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

def _call_claude_messages(client, content_blocks, system_prompt, max_tokens=6000):
    """Content-agnostic core of the Claude call. `content_blocks` is the full
    messages[0]['content'] list — the caller decides whether it holds a PDF
    `document` block (call_claude) or a `text` block (call_claude_text).

    Returns (parsed_json, cache_note, usage_dict). usage_dict keys: input, output,
    cache_read, cache_create. On error parsed_json is None and usage_dict is {}.
    Retries transient API errors (overload / rate-limit / 5xx / network) and
    empty/garbled JSON with jittered backoff. Raises CreditExhausted on an
    out-of-credits error so the caller can halt the whole run.
    """
    last_err = ""
    for attempt in range(5):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-20250514",
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


def call_claude(client, pdf_b64, system_prompt, user_text, max_tokens=6000):
    """PDF extraction call (unchanged public contract). Sends a cached PDF document
    block + the instruction text. See _call_claude_messages for return shape."""
    blocks = [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
            "cache_control": {"type": "ephemeral"}
        },
        {"type": "text", "text": user_text}
    ]
    return _call_claude_messages(client, blocks, system_prompt, max_tokens)


def call_claude_text(client, source_text, system_prompt, user_text, max_tokens=6000):
    """Text variant for scraped web pages / Reddit threads. Same retry / credit /
    return contract as call_claude. `source_text` (the scraped corpus) goes first
    with ephemeral cache_control so repeated passes over the same page are cheap;
    `user_text` is the extraction instruction."""
    blocks = [
        {"type": "text", "text": source_text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": user_text}
    ]
    return _call_claude_messages(client, blocks, system_prompt, max_tokens)


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
                       confidentiality_flag=False, confidentiality_evidence=""):
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
        "extraction_attempts":      1,  # Xano increments server-side (current value + 1)
        "confidentiality_flag":     bool(confidentiality_flag),
        "confidentiality_evidence": (confidentiality_evidence or "")[:1000],
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
        # Retry with jittered backoff: under parallel load Xano can return
        # transient 502/503s, and a single bad page must not abort the whole run.
        for attempt in range(6):
            try:
                resp = requests.get(endpoint, params={"page": page, "per_page": per_page}, timeout=30)
                resp.raise_for_status()
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(min(20, 2 * (attempt + 1)) + random.uniform(0, 2))
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

        # Venue pre-filter: skip non-venue vendors entirely (no download, no model calls).
        # Only fires when the venue set loaded successfully; an empty set disables the gate.
        if venue_vendor_ids and vendor_id and vendor_id not in venue_vendor_ids:
            yield from emit(f"[{row_num}/{total_rows}] {pdf_id} — {venue_name} — ⏭  skipping (vendor not categorized 'Venue')")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name, "status": "SKIPPED", "reason": "non-venue: mapping category != Venue", "cost_usd": 0})
            _update_pdf_status(xano_id, "skipped_non_venue", error="non-venue: mapping category != Venue")
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
        b64_mb = len(pdf_b64) / 1024 / 1024
        if b64_mb > 50:
            msg = "PDF too large (>50MB base64)"
            yield from emit(f"  ⚠  {msg}, skipping")
            results_log.append({"pdf_id": pdf_id, "venue_name": venue_name, "status": "FAILED", "reason": msg})
            patch_result = _update_pdf_status(xano_id, "failed", error=msg)
            yield from emit(f"  📝 Status writeback: {patch_result}")
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
                    model="claude-sonnet-4-20250514",
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

        if i < len(batch) - 1:
            time.sleep(2)

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
        status = raw_status if raw_status in ('extracted', 'partial', 'failed', 'skipped', 'skipped_non_venue') else 'pending'
        counts[status] = counts.get(status, 0) + 1

    return {
        "rows":      all_rows,
        "counts":    counts,
        "total":     len(all_rows),
        "with_link": with_link,
    }
