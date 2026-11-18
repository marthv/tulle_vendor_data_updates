---
name: project_session_state_2026-07
description: "Where the multi-day WeWeb/Xano/Railway build stands and what's left (alternate vendor categorization)"
metadata: 
  node_type: memory
  type: project
  originSessionId: d7089329-158e-4f31-ad48-85816be92d12
  modified: 2026-07-27T04:53:47.505Z
---

Big build session 2026-07-26/27. **Shipped & live:** ep119 dynamic-filter facets + empty-state fallback + filtered_total ([[project_ep119_facets]]); ep119/121 dead-fallback removal + min-side input trims + warmer realign ([[project_119_121_dropfallback]]); the Railway Streamlit **Cohorts analytics tab** with hub/customization filters, auto-insights, density scatter, retention, heatmap ([[project_cohort_analytics]], [[reference_railway_admin_dashboard]]). All Xano changes are live; dashboard is pushed to marthv/tulle_vendor_data_updates main.

**#4 "alternate vendor categorization" is now DONE and deployed** — it resolved to multi-category
PDF extraction for Photography + Entertainment. Full detail, pickup order and gotchas in
[[project_multicategory_extraction]]. Backend shipped 2026-07-27; remaining work is the Railway
accuracy pilot, the 698-PDF backfill, and the WeWeb filter UI.

Note the earlier guess in this file about category values was WRONG: table 11 `Category` actually
holds `Venue` / `Photography` / `Entertainment` (plus Flowers/Beauty/Catering), NOT
"photographer"/"band"/"dj". DJ vs Band lives in `Type_of_Entertainment`.

**Open follow-ups (flagged, not owned):** Des's intermittent blank Cohorts view is most likely CLIENT-side WS blocking — do NOT re-add a `.streamlit/config.toml` (it broke the app). `What_are_you_most_interested_in_` onboarding field is 100% empty (WeWeb/Xano capture bug). WeWeb sends `include_facets`/`sort_by` always-empty (facets feature not wired into UI yet). ep121 enrich-up-to-2500-rows on any filter is the remaining higher-risk perf trim. `.claude/xano_backups/*.xs` snapshots are local (uncommitted) on tulletogether branch xano-cache-optimization.
