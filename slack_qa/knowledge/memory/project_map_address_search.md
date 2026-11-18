---
name: project_map_address_search
description: "Map address search on Vendor Discovery - researched 2026-10-01, plan = Mapbox v6 geocode in browser -> fitBounds -> call 'Recalculate Vendors' fn d0bcf424; not built yet"
metadata:
  type: project
---
Researched 2026-10-01 (read-only agent). Map = native `ww-mapbox` element 43235d7f-769b-4ee2-8c0a-3de09cf9f3a9;
raw mapbox-gl object in `variables['43235d7f-...-instance']` (page already calls map.easeTo from custom-js in
wf 9dcd2e6b "Updated Center"). Bbox vars north 6cdcaef4 / south 7b325fab / east e3e09f81 / west bd236ce5 feed
MapLight collection a5f0a42c (ep121). Only refetch trigger = map:dragend (zoom > 7) -> function wf
"Recalculate Vendors" d0bcf424-be86-47ec-9f45-2782f50be191 (getBounds -> vars -> fetch -> sort). A
programmatic move does NOT fire dragend, so call d0bcf424 after moveend.
Search inputs: desktop d00e176b (Enter wf a6f9969d, plugin actions - MCP can't edit), mobile 40eab76a.
Mapbox public token var `mapbox_api` 41aeb07a - NOT URL-restricted (worked with no Referer).
Plan: on Enter in Map view, Mapbox v6 forward geocode (country=us, types address/postcode/place/locality/
neighborhood) -> fitBounds(bbox) with minZoom 8 (wide bbox = 8-15s ep121) -> await moveend -> d0bcf424.
Risk: a6f9969d also refetches MapLight with the OLD bbox (race); one box = two meanings (name vs place).
CLAUDE.md says Mapbox deprioritized - user asked for it anyway 2026-09-30.

## BUILT IN EDITOR 2026-10-01 (~02:00 UTC) - NOT PUBLISHED
- Function wf "Fly Map To Place" d5ce1593-38cc-4ef1-82a4-3948f72a36b9 (param query): Mapbox v6 geocode ->
  CONVINCING-MATCH rule (address: confidence exact/high; else place name must contain typed text before
  first comma/period - 21/21 real terms right; "glenford mansion" would otherwise jump to Mansion Flats,
  Sacramento) -> fitBounds animate:false (jumpTo is sync, like hub chips, so no moveend wait) -> zoom
  floor 8 -> Recalculate Vendors d0bcf424 -> Mixpanel `map_place_search` {query, place, place_type}.
- Element onEnterKey wfs (Map view only): desktop 9ddc4e49 on d00e176b, mobile 3c6f2b8e on 40eab76a.
  Existing "Update" Enter wfs untouched.
- Placeholders: Map view -> "Search a city, zip or venue name" (desktop) / "City, zip or venue..." (mobile).
- Hub quick-link chips (Geographic Hub Chip Carousel cf743107, wf "Update map position from item") KEPT:
  836 clicks / 403 users in 30d. Compare map_place_search vs used_map_hub_filter_chip ~2 weeks after
  publish before retiring them (user wanted search to replace them).
- Race is DESKTOP ONLY: desktop "Update" fetch-collections includes MapLight a5f0a42c (old bbox); mobile's
  doesn't. Hand edit: remove MapLight from it (map never used the text anyway - sends search_input "").

## TYPE-AHEAD PLACES - BUILT IN EDITOR 2026-10-01, NOT PUBLISHED
- Function "Suggest Places" d2c7c261-6cb3-4725-9aee-d86f76a8301e (param query): Map view + 3+ chars ->
  Mapbox v6 autocomplete limit 4 -> keep convincing (name contains typed head; addresses always) max 2 ->
  stale guard (query must still equal a search box value) -> suggestResults = places + venues(max 3) ->
  open flag 40a9fad8 = true. Place rows: {kind:'place', Name:'📍 <name>', State:<place_formatted minus
  country>, bbox, center, placeName, placeType, Vendor_ID:'', id:null}.
- New change wfs: desktop 90760d64 on d00e176b, mobile 7eddf35d on 40eab76a (existing ranking wfs untouched;
  they rebuild the list venues-only each keystroke, then places land ~150ms later).
- New row click wfs: desktop ebe22c79 on row 77408b13, mobile 0f3b6a2e on row 7ece0231: filter kind=='place'
  -> fitBounds/jumpTo (zoom floor 8) -> close dropdown -> Recalculate Vendors -> mixpanel map_place_search
  {via:'suggestion'}.
- REQUIRED HAND EDIT BEFORE PUBLISH (MCP can't edit - Xano plugin actions): in BOTH rows' existing
  "Select Vendor Location" (158701a9 desktop / 1c30ce1c mobile) add a Pass-through condition FIRST:
  `context.item.data?.kind != 'place'` - else clicking a place opens a blank vendor page.

## 2026-10-01 CORRECTION: the search boxes were GRID-ONLY - Map view had no box at all
Desktop box d00e176b sits in Filter Rail Sidebar > Section Basic > Basic Body 7104ac74 > Search Row 08fc9c68
(conditionalRendering Venue State=="Grid"); mobile box sits in Grid Filters Banner 4ecb1418 (Grid-only too).
I built the Map-view features without checking that - user couldn't find them. Fixed (desktop, editor only):
Search Row shows for Grid OR Map; Basic Body display = basicOpen ca8b5606 OR Map. MOBILE STILL HAS NO BOX IN
MAP VIEW (option B = new box in the map command bar where the hub chips are - user's stated intent).
WeWeb workflows do not run in the editor canvas - test in Preview.

## 2026-10-01 later: MOBILE + guards done (editor, NOT published)
- User added guard `context.item.data?.kind!= 'place'` (filter 177a5656) as first step of desktop row's
  "Select Vendor Location". User then COPIED the desktop search row into Map Filters Banner c516c996 >
  Mobile Search Row 33ecacee for mobile Map view: copy input bebbdc79, dropdown 452a3218, row c3e50051.
  WeWeb remapped the copied workflows to the copy's own -value var and the copied row kept the guard.
  Element workflow ids are SHARED across copies (158701a9, ebe22c79 appear on both rows).
- Suggest Places stale guard now checks d00e176b, 40eab76a AND bebbdc79 (else every result from the new
  mobile box was discarded).
- Old mobile row 7ece0231 (Grid Filters Banner) has NO guard - harmless: Grid-only, places only in Map view.
- Desktop dropdown clip fix: Basic Body minHeight = 90 + rows*62 px while open in Map view (dropdown stays
  absolute; making it position:relative stretched the input - container is a row, input height 100%).
- MOBILE MAP VANISHED after typing a place + Enter: map 43235d7f mobile conditionalRendering hid it when
  "Search Vendors" (5c91206d, ep119 name search, Search_Input = saved_name_search ce4c0aa9, autofetch OFF)
  had itemsTotal 0 - a place name matches no venue name. Removed that clause (kept favworking/fav "favourites
  view empty" clause) in BOTH style.mobile.conditionalRendering AND the legacy duplicate
  style.mobile.customCss['conditional-rendering']. Desktop rule uses "Updated Vendors" bee4e735 (no text) - fine.
- Recommended single hand edit replacing "remove MapLight": first step of "Update" Enter wf (a6f9969d) on
  desktop d00e176b + mobile copy bebbdc79 = Pass-through `variables['a8c02175-...']!='Map'` (name filter has no
  visible effect in Map view; also kills the old-bbox MapLight race).

## LIVE 2026-10-01 04:27:24 UTC (verified in live /data/0d69d71b...json)
All present: Fly Map To Place d5ce1593, Suggest Places d2c7c261, Enter wf 9ddc4e49, Suggest wf 90760d64, mobile copy
bebbdc79, place-row click ebe22c79, vendor-row guard; old mobile map-hide rule gone. NOT added: "Update" Enter guard
(!= 'Map') - so in Map view Enter still runs the name filter + desktop old-bbox MapLight refetch (minor race).
NEXT: Mixpanel `map_place_search` (props via 'suggestion' | none for Enter) vs `used_map_hub_filter_chip`
(baseline 836 clicks / 403 users per 30d) ~2026-10-15 before retiring hub chips. Real-device click test by user pending.

## 2026-10-11 CORRECTION: map tracking never fired
map_place_search, map_marker_clicked, searched_map = 0 events in 30d. Cause: custom-js steps contain literal "\n" -> AsyncFunction SyntaxError (the console error on every map tap/pan). Fixed in editor (UNPUBLISHED) in Recalculate Vendors d0bcf424 + Open Category Page Popup 2dca5b94. The planned ~10-15 place-search vs hub-chip comparison has NO data until published. Same bug unfixed: Search! a0e69c1d, nav Contact Us a9e09635 / 5263ee46, nav Log Out 668e40d7 (mixpanel.reset never runs).
Also 10-11 (editor only): Alert popup 89bc227b restyled (green type line, "City, ST · N pricing PDFs", CTA "See pricing"); Filter Popup State Select ba6ed637 array->string fix. Backups in .claude/xano_backups/weweb_*_2026-10-11.json.
- 10-11 night: Alert popup 89bc227b REDESIGNED in editor (360px card / mobile bottom dock, 16:9 photo, name, City ST, chips: N pricing PDFs / rating / Up to N guests / venue type, green "See pricing", no price, no purple) + map_popup_cta_clicked {vendor_id, target button|card}. Backup weweb_alert_map_popup_pre_redesign_2026-10-11.json. UNPUBLISHED; tapping a 2nd marker while open = first tap closes (unchanged).
