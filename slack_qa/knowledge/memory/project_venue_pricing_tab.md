---
name: project_venue_pricing_tab
description: Venue Pricing tab in the Railway Streamlit dashboard — approximate all-in venue cost + State×Venue-type matrix
metadata: 
  node_type: memory
  type: project
  originSessionId: d846aec4-97a0-4767-b1ed-49dd4a3832b8
---

Built **2026-06-19**: a new "💰 Venue Pricing" tab in the **Railway dashboard**, which is a **Streamlit** app: `dashboard.py` in the **`tulle_vendor_data_updates`** repo (`C:\Users\vivek\Documents\GitHub\tulle_vendor_data_updates`, deploys from `main` via `railway.toml`: `streamlit run dashboard.py`). NOT Flask — an earlier attempt mistakenly built it in `tulletogether/pipeline/app.py` (a stale Flask fork); those edits were reverted. See [[feedback_pipeline_ignore]] and [[reference_extraction_pipeline]].

**What it does:** pulls table 36 (`all_extracted_pdf_data`) + table 11 (`wptp_updated_mappings`) via unauthenticated `requests.get` (paginated, `_vp_fetch_all`), dedups table 36 by latest `PDF_ID`/`last_extracted_at`, inner-joins venues-only on `VENDOR_ID`, behind `@st.cache_data(ttl=600)` (`_vp_load`; refresh button calls `_vp_load.clear()`). Streamlit reruns recompute estimates per widget change. UI = `st.selectbox` guest count + state/venue-type/year filters, `st.number_input` min-quotes, `st.metric` summary cards, `st.dataframe` vendor table, and a `pivot_table` styled with a manual green→amber RGB `Styler.applymap` (NO matplotlib — not in requirements, so `background_gradient` would fail).

**Cost equation (venues only, NO tax — none in Xano):** `base(Venue_Fee_on_a_Peak_Season_Saturday) + max(pp_fb×G, fb_min) + admin%(Admin_Service_Fee) + ceremony(flat or ×G per Ceremony_fee_Type)`. All `_vp_*` helpers are framework-agnostic plain Python appended at the end of `dashboard.py`; `st.tabs(...)` line edited to add `tab_vp`.

**Tunable choices:** representative estimate = MIN of qualifying spaces ("from" price); peak-season-Saturday columns only; capacity gate `guest_min ≤ G ≤ Max_Capacity_Seated`; matrix column dim = `Venue_Type` (Category collapses to one value under venues-only). Uses `import datetime` → `datetime.datetime.fromisoformat`.

**Shipped to `main`** (auto-deploys to Railway) 2026-06-19, commits incl. the tab + a same-tab Google sign-in fix (`st.link_button` forced new-tab → replaced with an `<a target="_self">`).

**Xano endpoint quirks (important):**
- `api:GynP5T1B` list endpoints `all_extracted_pdf_data` (~18.8 MB, ~32 s) and `wptp_updated_mappings` IGNORE `page`/`per_page` and return the FULL table as a bare JSON list. Do NOT paginate (loop never terminates → hammered Xano into 503s, the first live bug). Single `requests.get(url, timeout=120)`. Fixed in commit 9a17635.
- **`/wptp_updated_mappings` and `/wptp_updated_mappings_search` default `State_Input` to "New York"** → return only 574 NY venues even with no args (the raw table has 7,774 rows). To get ALL states use **`/wptp_map_light`** (geo bbox `north/south/east/west`, NOT state-filtered); it returns `Vendor_ID, State, Name, Category, lat/lng` (no Venue_Type/pricing) and **caps at 2,500 rows/call** — pull in longitude bands (`_VP_MAP_BANDS`, 5 bands, none caps) and union → ~6,076 venues across 50 states. Venue_Type/capacity/pricing still come from table 36. Fixed in commit ffdd523.
- Search endpoint 119 with explicit `State_Input=Texas` → 826, California 709, Florida 576, etc. — multi-state data is real.
- `venue_pricing` (table 37) is **64,148 rows** (not ~789 — old note was stale), 4,999 vendors, with `Month` (All+Jan–Dec) and `Day_of_Week` (Saturday/Weekday/Friday/Sunday/Everyday) + per-row pricing incl `Tax_Pct`. No State/Type (join table for those).

**Still unverified against live data:** `Venue_Type` population (sparse → matrix collapses, flip column dim), distinct `Ceremony_fee_Type` values, and that estimates look sane.

## 2026-08-27 — "Venue vibes" filter added (shipped, commit 27d0216)

A vibes cut next to the Venue type filter, plus a **Vibes** column on the venue table.

**Source: table 36 `Venue_Attributes`** — a single TEXT column, **semicolon**-separated, both
`A;B` and `A; B` occur, and it can literally read `"Not listed"`. Vocabulary is a closed list
in `extract_core.py` (~line 559): Historic Architecture · Rooftop / Skyline Views · Scenic /
Nature Views · Waterfront · Ballroom · Industrial / Warehouse · Greenhouse · Natural Light /
Large Windows · Tall / Vaulted Ceilings · Tented. (Table 11's `flt_venue_attributes` is the
denormed token list of the same thing, and is what ep119's WeWeb vibes filter used.)

**ep199 `venue_pricing_dashboard` gained `items.Venue_Attributes`** on the `pricing` section —
the slim feed did not carry it. Its **response cache is 600s**, so a column addition is
invisible to callers for up to 10 minutes after deploy.

**THE FILTER IS APPLIED PER VENUE, NOT PER ROW — do not "simplify" it to match the state/type
filters directly above it.** A vibe describes the venue but is stored per extracted PDF, and
only ~65% of rows carry one, so a venue's rows routinely disagree. Filtering rows would
silently change the displayed number: the estimate is the cheapest qualifying SPACE, so a
row-level gate would price the cheapest *waterfront-tagged PDF* rather than the venue. The
implementation unions the tags, gates the venue, and keeps every row feeding the estimate.
**Regression check: 0 venues change estimate when a vibe filter is applied** (verified over
8,687 joined rows, keyed on vendor_id — key on NAME and you get 12 false positives, because
123 venue names are duplicated across vendor ids).

Options are ordered by **row count, not alphabetically** (for a cut, volume is the useful
signal), with a **20-row floor** to drop extraction strays: the ten real vibes all have 306+
rows, the next token down has 14 (`Barn & Rustic` — a CATEGORY value leaking into an attribute
field, which the prompt explicitly forbids). A floor rather than a hardcoded list so it
survives the vocabulary being extended. Matching is **ANY/OR**.

Measured 2026-08-27: 64.8% of ROWS carry a usable attribute, but **87% of VENUES** do — the
help text quotes the venue-level figure, since the filter is venue-level.

⚠️ **`@st.cache_data(ttl=600)` sits immediately above `_vp_load`.** Inserting a function there
steals the decorator and silently un-caches the expensive Xano load (and breaks
`_vp_load.clear()`, the refresh button). Caught in review here; put new helpers ABOVE the
decorator.
