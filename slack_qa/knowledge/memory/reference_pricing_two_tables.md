---
name: reference-pricing-two-tables
description: Two separate venue pricing data models in Xano — table 36 peak-Saturday columns (what filters use) vs table 37 venue_pricing day/month rows (raw passthrough only)
metadata: 
  node_type: memory
  type: reference
  originSessionId: b9dade59-037b-4ccc-b85e-6b88e7988603
---

There are TWO distinct venue pricing data models in Xano workspace 1, and the search filters do NOT read from the granular one:

**Table 36 "Extracted PDF Data"** — has dedicated peak-Saturday columns: `Venue_Fee_on_a_Peak_Season_Saturday`, `Food_and_Beverage_Min_on_a_Peak_Season_Saturday`, `Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday`. The advanced price filters (`base_fee_min/max`, `fb_per_person_min/max`) on endpoints 119 (`wptp_updated_mappings_search`) and 121 (`wptp_map_light`) filter against these columns — so filtering is **Saturday-peak by design**. The displayed min/max range aggregates (`min/max_venue_fee_peak_sat`, etc.) also derive from these table-36 columns.

**Table 37 `venue_pricing`** — newer granular table, one row per Space × Day_of_Week × Month (789 rows as of 2026-05-31). Has `Day_of_Week` (Saturday/Friday/Sunday/Weekday) and `Month` columns. Distribution: Weekday 403, Saturday 155, Friday 84, Sunday 77, ~70 other. Only ~20% is Saturday. The extractor (extract_core.py, see [[reference_extraction_pipeline]]) already populates day/month granularity. BUT endpoints 119/121 only attach these rows raw via `include_pricing=true` (`set_conditional:"venue_pricing"`) — nothing filters or aggregates on Day_of_Week/Month.

**Implication:** "Filter by other days / more nuanced in time" (Des's ask) is NOT a data-collection problem — the data exists in table 37. It's a downstream gap: filters + range displays would need to read table 37 day/month rows instead of (or alongside) table 36's peak-Saturday columns. The current Saturday-peak filter behavior is intentional and correct per Vivek's stated intent. See [[project_wptp_search_joins]].
