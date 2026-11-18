---
name: project_preferred_vendors
description: "Venue PDFs' Preferred_Vendors lists (t36) - sized 2026-10-12: 4,363 venues have lists; only 638 have a matched PRICED photo/ent vendor on Tulle (median 1); 1,605 unmatched P/E names on 2+ venue lists = recruiting list. No required-vs-preferred distinction."
metadata:
  type: project
---
Measured 2026-10-12 (subagent, full scan of t36 45,179 rows + t11 18,246; scripts scratchpad/pv/ of session 5e6f753b):
- 13,010 venue PDFs, 4,623 (36%) have Preferred_Vendors -> 4,363 venues; 51,830 names (37,088 distinct), median 3/venue, p90 35.
- CORRECTION: I first told the user ~11% / ~600 venues from a row sample; true = 36% of PDFs / 4,363 venues.
- Matches to t11 (exact + rapidfuzz ratio>=92, same state): 1,991 names (3.8%); P/E 1,401 names / 720 vendors, 645 with priced packages. Hand check 15/15 P/E plausible (name+state only), fuzzy ~8/10.
- Per venue: 638 of 4,363 (14.6%) have >=1 matched priced P/E vendor (5.2% of all venues with PDFs); median 1. Traffic weighting NOT measured.
- Recruiting list: ~8,400 distinct unmatched P/E-looking names; 1,605 on 2+ venue lists, 553 on 3+ (top: Sam Hill Ent 28, East Coast Ent 27, Absolute Ent 15, Cleveland Music Group 15, Bialek's Music 15...). Non-P/E top: Encore AV 56, Bright Event Rentals 48.
- Required vs preferred NOT distinguishable in Preferred_Vendors; only 72 PDFs tie exclusive/required wording to photo/music (catering/F&B "required" common in other fields).
- Verdict: link feature real but thin (exact matches only if built); recruiting list is the bigger asset. Next: weight the 638 venues by venue-page views before building.
