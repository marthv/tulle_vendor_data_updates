---
name: project_popularity_recency_sorts
description: "2026-08-05 SHIPPED: ep119 v17 popular_desc/recent_desc sorts + ep150 now increments the previously-frozen Clicks_on_Venue counter"
metadata: 
  node_type: memory
  type: project
  originSessionId: c7ba1830-5f0a-49b7-b67d-6092e89e4108
  modified: 2026-08-06T03:57:20.586Z
---

2026-08-05 SHIPPED. Two new `sort_by` modes on ep119 (`popular_desc` → `Clicks_on_Venue` desc, `recent_desc` → `id` desc), plus the WeWeb dropdown options ("Most Popular", "Recently Added" — added at the TOP of both the desktop `e6bf34e3-3521-4c54-8a93-7a6a5e6ae3fc` and mobile `41ca2182-3604-46bd-8595-79f939e64dd9` ww-input-select on the Vendor Discovery page).

**The non-obvious part: `Clicks_on_Venue` (table 11) was DEAD DATA.** It holds real values (Brooklyn Botanic Garden 318, Prospect Park Boathouse 273) but they are a 2024-era import — the only stack in the whole workspace referencing the column was ep151, the auto-generated create-record scaffold. Nothing incremented it. The vendor-click path is `PATCH /user/update_vendor_views` (**ep150, apigroup 4, auth: user**), which wrote counters onto the *user* row only. ep150 now also does a guarded read-modify-write on table 11 by `Vendor_ID` (indexed) after the user edit, outside the response path.

**Why:** ranking by a frozen snapshot would silently decay; wiring the increment into the endpoint WeWeb already calls on every view means popularity compounds without new frontend plumbing.

**How to apply:**
- ep150's increment only fires when WeWeb actually sends `vendor_id`. Its own v-comment warns the binding was added "until its binding adds the vendor fields" — **verify the binding sends it** before trusting the counter. It counts views (not unique users) and only from logged-in users.
- ep119 sorting is POST-QUERY `|sort` on `$sorted_rows`, never in `db.query` (a var expression in a db.query clause 500s at runtime — see [[reference_xano_mcp_quirks]]). Any non-empty `sort_by` triggers the paging bypass: `per_page=10000` + manual `|slice`. That cost predates this change.
- **A new sort key must be added to BOTH output lists** — the main query and the `fallback_all` re-query, which overwrites `$model`. Miss the second and sorting silently breaks only on the empty-result path (WeWeb always sends `fallback_all=true`).
- `cache_v` default is now **15**. Warmer task 8 omits it and auto-tracks the default.
- **TRAP:** the WeWeb "Search Vendors" collection `5c91206d-2060-4f2e-b94e-7b4605f4c423` carries a stale CLIENT-SIDE sort `[{Name ASC},{Clicks_on_Venue DESC}]`. It appears inert because the response is a paged envelope object, not an array — but `Clicks_on_Venue` is now actually present in the payload, so if list order ever looks alphabetical regardless of the dropdown, clear that collection sort first.

**COVERAGE GAP (open as of 2026-08-05):** 7 vendor-card click workflows call ep150; only 4 send `vendor_id`. These 3 send just `id` + `total_vendor_views`, so the vendor gets no credit — Similar Vendors carousel on Venue Vendor Page (`105ed00c-e184-45dd-bdb2-ef4bcf336b57`), Other Vendors Page (`1a2d7645-cb64-4dbe-868e-76a7db033e68`), and one Discovery variant (`abd1ed88-bcf5-47e3-b4d0-98884b3b408d`). Fix = add body fields `vendor_id` → `context.item.data?.['Vendor_ID']` and `vendor_name` → `context.item.data?.['Name']` to action `3a77667c-d5b0-4d7c-ae64-3089270d078a`. Also note the counter fires on CARD CLICK, not vendor-page load — direct links and refreshes never count.

**2026-08-05 follow-up — sort was lost on back-navigation.** Repro: set a sort, click a vendor, click "Back to Transparency Project", list returns unsorted. Cause: `sort_by` (`185c7a24-8257-4ea9-9400-2f14f5121db7`) was the ONLY search input with **neither** `isLocalStorage` **nor** `isPersistentOnNav`. State/capacity/name-search are localStorage; ChosenCategory/venue_types/pricing_models/price maxes are nav-persistent. The Back button (`f607fc3d…` Venue Vendor Page / `cfe5e54b…` Other Vendors Page, both on workflow `96d0ecfe…` misnamed "Remove Get Vendor Data Collection") does a hardcoded `change-page` with no query string — a fresh nav, not `history.back()` — so the runtime cleared the variable and onload refetched with `sort_by=""`. **FIXED** by setting `isPersistentOnNav: true` on `sort_by` + `mobile_sort_by` (`abeed278…`) via `editVariable`. No workflow or Xano change needed. Side effect: `sort_by` is shared with the home-page and venue-calculator vendor collections, so a sort now carries into those within a session.

**STILL OPEN:** both sort selects hardcode `initValueSingle` to the formula `""` with no binding back to `sort_by`, so the dropdown renders its placeholder ("Sort Vendors" / "Sort: Featured") even when a sort is active. Must be bound by hand in the editor to `variables['185c7a24-8257-4ea9-9400-2f14f5121db7']` on `e6bf34e3…` (desktop) and `41ca2182…` (mobile).

**TRAP — `editElement` SILENTLY NO-OPS on `initValueSingle`.** It returns "Element updated" and writes nothing — verified 4x on `e6bf34e3…` across both binding-object shapes AND a plain static string. Nothing is corrupted (the diagnostic left no residue), but **the success string is a lie — always read back with `getPageElementsByUid` after an `editElement`.** By contrast `editVariable` patches correctly and preserves name/type/defaultValue when only a flag is passed. Note `searchVariables` does NOT return the persistence flags, so those can only be verified in the editor or by live behaviour.

**TRAP — these workflows CANNOT be edited via the WeWeb MCP.** `editFrontendWorkflow` validates action types against the modern registry and rejects the legacy/plugin types these use (`variable`, `fetch-collections`, `cd33cf33-…-request`, `f5856798-…-fetchUser`), and it replaces the whole action map rather than patching one field. Rewriting would mean re-authoring a live navigation path in new action types. Do it by hand in the editor. The rejection IS atomic — a failed call leaves the workflow untouched (verified).

Related: [[project_package_grain_filters]] (ep119's other live surface), [[reference_xano_cache_resolved_inputs]].
