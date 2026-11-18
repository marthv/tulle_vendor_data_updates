---
name: project_category_breadth_culture_filters
description: "Sizing for (A) more vendor categories for retention and (B) culture/religion filters - measured 2026-09-27, user said DO NOTHING YET; don't start work unprompted"
metadata:
  node_type: memory
  type: project
  originSessionId: b1ed54d5-9ec2-453d-9b07-d84f6865eeef
  modified: 2026-09-28T01:22:50.891Z
---

**2026-09-27: user asked for sizing only — "i don't want to do anything about it yet."** Do not
start work unprompted; bring back only if the user raises it or after the Oct 1 BNPL read
([[project_geo_pricing_bnpl]]). Parked alongside [[project_ask_someone_to_pay]].

User ideas: (A) more vendor categories to keep couples through the 12-18 month planning period
(pairs with Forever/BNPL); (B) culture/religion-oriented filters.

## A. Categories — supply exists for 2 categories, demand is discovery-limited
- Visible vendors (t11 `Category`, single messy text: lowercase `entertainment` x13, `Both`,
  one combined value): Venue 11,231 · Entertainment ~2,143 · Photography 2,079 · Flowers 739 ·
  F&B 149 · Beauty 73 · Planner 9.
- Extracted (t36, deduped): Photography 1,835 · Entertainment ~2,090 · **Flowers 83 · F&B 82 ·
  Beauty 51** (pipeline marks these `skipped_non_venue`). t36 `Vendor_Category` only filled for
  Entertainment/Photography. 511 venue PDFs still `batch_submitted`.
- Demand (`viewed_vendor_page`, 9/22-27 — `category` prop only exists from 2026-09-21): venue
  97.7%, non-venue 2.3%; 13/637 viewers (2%) opened any non-venue. 20% of views untagged.
  `viewed_vendor_pdf` has NO category.
- Tenure signal (tiny N): non-venue share 1.1% (<12d) -> 1.3% -> 3.6% -> 6.3% (>95d). Paid users
  past 30d: 6/37 (16%) viewed non-venue.
- `changed_search_category` 90d: 52% of picks non-venue (824/1,581) but used in only ~2% of
  searches.
- Read: couples arrive for venues and are rarely pointed elsewhere.
- Idea if resumed: post-venue-save / paid-30d nudge to photographers + DJs/bands; fix category
  tracking first; un-skip Flowers/F&B extraction only if the nudge moves engagement.

## B. Culture — no measurable demand in search, no structured supply
- Search (`search_vendors.search_input`, 90d): **15 of 20,498 (0.07%)** genuine culture intent
  (Indian wedding 7, kosher 4, halal/chuppah/church/chinese 1). Most "indian"/"church"/"fire"
  hits are place/venue names. Likely understates need (people don't type it into vendor search).
- No culture/religion field on Mixpanel profile or Xano user (nearest: `wedding_vibes`).
- Supply: NO structured fields for kosher/halal/open flame/baraat/dry/outside-caterer allowed or
  fee/multi-day/end time. No raw PDF text stored — only LLM summaries (kosher 31 PDFs, halal 0).
  Usable today: `Outside_Ceremony_Space` (74% fill), `Max_Capacity_Seated` (71%),
  `Venue_Offering` (All-Inclusive/Semi/Raw Space, 99%), and "outside catering" mentioned in
  ~1,737 venue PDFs (~15%).
- Building filters = new extraction pass over ~11.5k venue PDFs. Idea if resumed: size first with
  an optional onboarding "traditions" multi-select; add culture fields to the NEXT routine
  extraction pass rather than a dedicated re-run.

Keyword counts are unreviewed regex matches; Mixpanel filtered to tulletogether.app.
