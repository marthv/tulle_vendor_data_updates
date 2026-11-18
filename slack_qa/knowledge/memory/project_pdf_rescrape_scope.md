---
name: project_pdf_rescrape_scope
description: "Venue PDF re-scrape scope (user decision 2026-10-04) - top 3,000 venues by clicks PLUS at least 10 per state/country (all if fewer); field list lives in DATA_WISHLIST.md"
metadata:
  node_type: memory
  type: project
  originSessionId: 0986d40e-51f7-447b-b965-aa7961b8f918
  modified: 2026-10-05T01:59:15.501Z
---

**Scope of the planned venue PDF re-extraction (user, 2026-10-04):**
- Top **3,000** venues by clicks (supersedes earlier 2,000 and 5,000 figures).
- PLUS a **coverage floor**: at least **10 venues in every state and country**, the most-clicked ones there.
  If a state/country has **fewer than 10, pull all of them**.
- Final count = 3,000 + floor top-ups; compute it at run time. The old "$100-300" estimate was for 2,000 and
  must be redone. Pilot 50 first.
- Table 11 `State` is multi-value: count a venue toward EACH state it lists, never `==` ([[reference_table11_state_multivalue]]).
- Store full PDF text in that run so future fields don't need another re-scrape.
- Which fields to extract: `tulle_vendor_data_updates/DATA_WISHLIST.md` (culture/religion, LGBTQ+, accessibility,
  overnight rooms, small weddings/elopements, venues asked for that we lack, real-user question log).

**Why:** the assistant needs some coverage everywhere; the top 3,000 by clicks alone would skip smaller states.

**How to apply:** when building the re-scrape venue list, select top-3,000 by clicks, then per state/country
top up to 10 (or all). Don't start the paid run without the user's go (ask before paid).
Related: [[project_ai_recommendation_engine]] [[project_batch_extraction_filesapi]] [[reference_extraction_pipeline]]
