---
name: project_florals_preferred_vendors_2026-10-19
description: "10-19 owner asks: florals pricing extraction (brief 10-15) + floral filters (desktop rail, mobile popup, gated); assistant-driven fields; gold-chip preferred vendors on vendor page. Planning only so far"
metadata:
  node_type: memory
  type: project
  originSessionId: d461cc1d-f540-4ec1-bcdd-000d8a6a23c5
  modified: 2026-10-19T16:56:53.891Z
---

Owner request 2026-10-19 (~04:00 UTC). Brief = "Florals Pricing Brief" (Oct 15, 2026; owner screenshots 2026-10-18 2351-2352).
1. **Florals:** extract florist quotes like P/E/venues. Goals: floral budget calculator (guest count, people/table, bridal party
   -> low/mid/high for area) + florist price tiers (min spend or item prices). Item categories: Personal (bridal/bridesmaid bouquet,
   boutonniere, corsage, flower girl), Ceremony (arch/chuppah, arch swag per-foot, ground arrangements, aisle, aisle urns),
   Reception (centerpiece low/tall, bud vases, sweetheart, cocktail, escort/signs, bar, cake, garlands), Installations (hanging),
   Fees (delivery/setup flat or %, breakdown, labor, rentals, tax) + Other (florist wording). Per item: category, florist name,
   price/range, "starting at", pricing unit, size, included, qty+line total, included vs optional. Per quote: florist+hub,
   quote/wedding date, min spend + conditions, guest count, total + coverage, fees separate.
   Then floral ADVANCED FILTERS: desktop left rail, mobile Filter popup (ed90717a), AND gated/locked version.
2. **Assistant-driven fields** (from [[project_assistant_usage_review_2026-10-19]]) for the top-3,000 re-extraction
   ([[project_pdf_rescrape_scope]]); future = full budget planning (whole budget -> vendors in price range).
3. **Gold chip preferred vendors:** pull PDFs of all gold_chip venues, extract preferred/required vendor lists, SHOW ON VENDOR PAGE;
   later pull those vendors' data (florists among them -> floral data).
Status: two read-only planning agents launched 10-19 (pipeline/cost plan; WeWeb filter + vendor page map). No spend, no writes.
**WeWeb map findings (10-19, read-only):**
- Preferred vendors card ALREADY LIVE: Partner Vendors Card 73b0079c on **Venue Vendor Page e49b4e74** (not d1d9bbf6), table 71 via
  fn56/ep231/ep230, 3,897 venues have lists; only 2.55% entries link to a Tulle vendor. Gap = DATA (category, required vs preferred).
  Suggested new categorised block: Main Content de25d763 between PI card 31d086ae and map fdf0f53b. See [[project_partner_vendors]].
- Flowers already takes ep119's P/E path ($is_pe = Category != Venue) but t36 has 0 Flowers rows (table 11 has 739 Flowers vendors)
  -> any floral package filter today = EMPTY GRID. Don't ship UI before data.
- Draft filters reuse params: min spend -> base_fee_max (Package_Price), tier -> venue_types (Service_Type), inclusions -> vibes
  (Offers_List), centerpiece -> fb_per_person_max (ep119 change); bouquet_max = only NEW param (warmer). Flowers ~0.6% of searches (09-13, stale).
- Gate = forever_access_purchased; Blur/Unblur twin panels (desktop 59c23e2a/36610e0e pattern; mobile 34901a49/6127be05). Gold filter is NOT gated.
- **WARMER BROKEN BY GOLD:** live grid calls send gold_chip_only=false; none of task 8's URLs have it -> warmed keys don't match (impact UNVERIFIED).
**Extraction plan findings (10-19, read-only; scratchpad jobs3/):**
- Florals NEVER extracted: 739 florists / 740 PDFs (652 never attempted, 88 skipped_non_venue); code SUPPORTED_CATEGORIES
  excludes Flowers. Only 13 states (TX 196, FL 120, PA 101, NJ 88, IL 79, NY 76, MA 38...) -> area tiers viable in ~7.
  Demand tiny: Flowers 0.8% of clicks, 5 searches/24h. Plan: new tables floral_quotes + floral_items (NOT t36: fn21 trigger),
  pass tag __f; est ~$17-25 full run (Sonnet 5.5 vs 4.6), pilot 30 PDFs x 2 models ~$2. Capture typical_spend (4/9 sample PDFs state it).
- Preferred vendors already extracted as NAMES (t36 Preferred_Vendors) for 886/1,760 gold PDFs (774 venues, 13,131 names);
  no category/required flag. Path A (free): split + fuzzy match (627 names match, 207 gold venues get >=1 Tulle link) + Haiku categorise <$1.
  568 florist names on gold lists = florist recruiting list.
- Job 3 facts pass: top-3,000 + floor = 3,133 vendors; UNION with gold = 3,944 vendors / 4,799 PDFs. New tables venue_pdf_facts +
  venue_preferred_vendors (tag __v), fold preferred vendors in (+~$14). Est $211-430 by model/scope; merged cost measured $0.071/PDF.
  Off-peak + F&B-min ceiling mostly TOOLING (t37 has month/day rows; flt_min_fbmin exists) - check before paying.
- Owner decisions pending: spend, model (4.6 validated vs 5.5 ~30% cheaper), 4 new tables, scope, full-text storage, pilot sizes.
**OWNER DECISIONS 10-19 (~04:40 UTC):** free step + pilots APPROVED (~$10: 30 florals + 50 venue PDFs); model = **Sonnet 5.5** (no 4.6
side-by-side); 4 new tables + write endpoints APPROVED (floral_quotes, floral_items, venue_preferred_vendors, venue_pdf_facts);
venue facts scope = decide after pilot. Full runs NOT approved. Pipeline work on worktree branch extract-florals-facts (never main).
**10-19 pipeline code BUILT, pilots BLOCKED:** worktree C:\Users\vivek\Documents\GitHub\tulle_extract_pilot, branch
extract-florals-facts, commit f8ddee6 (not pushed): extract_extras.py (__f florals, __v facts + preferred vendors, ingest w/
purge-before-insert, local JSONL fallback), 20 offline tests pass. Sonnet 5.5 params: thinking adaptive, effort low, structured
outputs; file_id-in-batch UNVERIFIED. BLOCKER: no pipeline .env locally (keys live in Railway) - needs ANTHROPIC_API_KEY +
GOOGLE_SERVICE_ACCOUNT_JSON in the worktree .env (gitignored). Pilot lists in scratchpad pilots/. Existing extract_core reads
msg.content[0].text -> breaks with thinking on; fix before moving the live pipeline to 5.5. pymupdf not installed (fitz shadowed).
10-19 ~05:00 UTC: owner logged Railway CLI into the Tulle account (gmail) - was Healthie; I wrote worktree .env (ANTHROPIC_API_KEY +
GOOGLE_SERVICE_ACCOUNT_JSON from extraction-batch, gitignored, never printed) with owner's OK ("just read from railway"). Pilots resumed.
**10-19 Xano BUILT:** tables 84 floral_quotes, 85 floral_items, **86 venue_pdf_preferred_vendors** (renamed: table 71 is already
venue_preferred_vendors = live Partner Vendors resolver), 87 venue_pdf_facts. Endpoints group 10 pdfdata/<table>: POST 319/321/323/325,
DELETE 320/322/324(optional source)/326; ?secret= [redacted] vs sha256; plaintext in ~/.tulle_pdfdata_writes.env (XANO_PDFDATA_SECRET).
Backups .claude/xano_backups/pdfdata_2026-10-19/. Path A loaded 13,080 rows (582 high/med matches, 204 gold venues w/ >=1 Tulle vendor;
plan's 627/207 had false positives). Haiku 5.5 categorised 4,882 names for $0.0575 ('planner' jumped 64->1,140 - suspect over-labelling);
reload into 86 running 10-19 ~05:30 UTC. Assistant tooling: t37 (month/day-of-week, brunch) NOT read by rec_service; F&B min + guest min ARE.
**PILOTS DONE 10-19 (~05:45 UTC), $2.60 total, Sonnet 5.5 batch + file_id WORKS** (thinking adaptive, effort low, structured outputs).
Florals 30 PDFs $0.0177/PDF: item recall 99.6%, price 99.6%, category 95%, min spend 13/13, fees 6/6; typical spend weaker (2 invented,
"$10k per 100 guests" stored flat). Facts 50 PDFs $0.041/PDF: lodging 15/15, fees 45/45, months 14/15, outside caterer 12/15 (approved-list
caterers should = yes), vendor recall 93%, relationship 100%; misses photo-credit lists at low effort. Projections: florals 740 ~$13; facts
3,944 ~$163 / 4,799 ~$199. Rows in Xano: 84=30, 85=248, 87=50, 86 +653 facts_pass. Branch commits f8ddee6, 584ff09, 6ec3df8, 377af96 (local).
Florals sample skewed to emails (only 2 proposals). Fill rates low = PDFs don't state it, not extraction misses. Full runs NOT approved yet.
**10-19 ~17:00 UTC owner:** florals = add 5 more FULL PROPOSALS to the pilot (running, typical-spend prompt fix included); venue facts
full run ON HOLD (don't raise until owner returns to it); category reload - owner asked what it is (explained; awaiting decision).
Note: new floral filters must go in BOTH ep119 and the Railway search engine ([[project_xano_upgrade_contention_2026-10-19]]); warmer param trap.

**10-27 ~00:30 UTC FLORAL FILTERS STARTED (owner: mobile + desktop, endpoints will need tweaks).** Pilot data live: t84 35 quotes / t85 357 items (35 florists, 10 states). Coverage: bridal bouquet 66% (median $225), low centerpiece 63% ($145), bridesmaid 63% ($95), boutonniere 60% ($20), arch 51% ($900), min spend 40%, any whole-wedding spend signal 60%, delivery fee 9%. Proposed filters: min spend <= X (+no-minimum toggle), bouquet <= X, centerpiece <= X, tier Budget/Mid/Luxury vs state (standard-wedding estimate), does arches; card line 'Bouquets from $X · Min $Y'. Plan: 0) full extraction ~705 PDFs ~$17-25 NEEDS OWNER OK; 1) re-runnable denorm fn -> table 11 flt_floral_* fields; 2) ep119 + Railway engine + sync (both engines - Railway at 50%); 3) WeWeb desktop rail + mobile popup ed90717a + gated twins + card line; 4) Planner priced florists, calculator later. TROUBLE known: Flowers routes through ep119 P/E path reading t36 (0 Flowers rows) -> empty grid today. Read-only mapping agent running.
