---
name: project_search_slowdown_2026-10-15
description: "10-15 search slowness: Xano admin table-11 Search box stalls ep119 for all users (live-tested with Kate); concurrency multiplies miss time; baseline uncached ~2.4s; ep119 code unchanged since 10-03"
metadata:
  node_type: memory
  type: project
  originSessionId: 70188b18-5752-4922-953f-10a3b5b3724b
  modified: 2026-10-16T00:42:22.935Z
---

Measured 10-15/16 (3,000 ep119 calls ~04:50 UTC 10-15 -> 00:38 UTC 10-16 + table 78 trend).

- **Xano admin global Search on table 11 (WPTP Updated Mappings) stalls site search for everyone.** Live A/B with Kate
  00:29-00:37 UTC 10-16: Ohio uncached pages 2.1-3.0s baseline -> 9-15s (one 64.9s) while her search ran; a real
  Rhode Island user hit 8.5-22s at the same time. Back to ~3s once it finished. Name-filter step was confounded (global
  search possibly still running) - UNVERIFIED whether Filter is safe.
- Uncached ep119 running alone: p50 2.36s, 27% >3s. Overlap multiplies: 1-2 concurrent misses p50 4.2s, 3-5 9.4s, 6+ 48s.
  Cache hits fine (~0.025s every hour).
- The 52.5s "worst" in the 10-15 DM = one iPhone (70.127.x) firing Florida pages 5-22 at once (05:05 UTC). Same-client
  bursts = 32% of slow time. Cause in WeWeb not yet found (infinite scroll?).
- python-requests from 152.55.181.241 (probably the rec engine on Railway, UNVERIFIED): 28 uncached popular_desc calls/20h = 8% of slow time.
- Table 78 trend: p95 2.0-2.8s 10-02..10-08 -> 3.4-5.2s from 10-09; similar_to_vendor slow_n ~100/day -> ~300/day from 10-12.
  ep119 live == ep119_post_ttl24h_2026-10-03.xs (code unchanged). Cause of step UNKNOWN; candidate = admin activity
  (multi-user slow windows 18:10-18:40, 20:20-20:40, 22:10 UTC 10-15).
- I briefly claimed cached calls were 4x slower (report p50 0.02->0.08) - WRONG, hits measured ~0.025s.
- Side find: blank table-11 row id 18262 (V6424, only gold_chip=true) - likely a gold-chip upload creating a row.
[[project_slowness_breakdown_2026-09-30]] [[project_gold_chip_vendors]]

## 10-16 ~01:15 UTC: option #1 (page cache) BUILT, NOT LIVE
- Probe (Michigan/Georgia/NY): page query WITH count 2.2-3.0s; same WITHOUT count 0.3-1.6s; full id list 2.3-4.8s
  (scales with matches, ~4ms/row - fat JSONB rows); 12 rows by id 0.07-0.4s. Count alone 1.3-1.6s.
- Redis = "Access Denied" on Essentials plan (a concrete thing an upgrade buys). Xano FUNCTION cache works on Essentials (warm 10ms).
- Built: fn83 `ep119_search_idlist` (cached, inert until called). Release file .claude/xano_backups/ep119_v25_pagecache_2026-10-16.xs
  (pages >=2 with a state or text search slice cached id list; + id tiebreaker on all sorts). Rollback ep119_pre_v25_pagecache_2026-10-16.xs.
- FOUND: today's sorted pages are non-deterministic on ties (16 NY venues at exactly 300 seats) -> same venue can repeat/vanish across pages. Tiebreaker fixes.
- Parity (temp fns 84 staging / 85 harness): 8/8 exact match (Ohio, NY cap_desc, FL popular, NJ name, TX range, CA fee, text "new york", Italy).
  Untested: P/E Photography, fallback_all, last/beyond page, recent_desc, pricing_models, gold chip. No-state "all venues" list build hung >120s -> guarded off.
- Tests caused contention + one nginx error at 9:15pm ET peak -> STOPPED. Run the rest at a quiet hour. Delete fns 84/85 after.
- Cold list build makes page 2 SLOWER (4-6s vs 2.5-3s) for unique shapes; pages 3+ ~0.3-0.5s warm (2.4-3.8s when instance congested).

## 10-16 01:53:51 UTC: ep119 v25 LIVE (user OK'd "fine with this update if it speeds up searches")
- Fallback case also built giant lists -> fn83 now returns needs_fallback (no list) and ep119 uses old path. No-state browse also old path.
- Parity 9/9 incl. Photography NY (P/E pages ~10.5s on BOTH paths - package pre-pass, separate problem).
- First 39 live calls after push: all 200, but ZERO page>=2 calls yet -> new path UNVERIFIED in prod. Check page>=2 durations + errors next session.
- Temp fns 84/85/87 deleted; fn83 ep119_search_idlist is now a LIVE dependency (don't delete).
- Gold chip 10-16 (user request): 1,542 Vendor_IDs set true via temp fn (patchTableContentBySearch silently caps at 100 rows while reporting 400 - DON'T trust it).
  Now 1,542 true after row 18262 DELETED 10-16 (user OK; backup table11_row18262_pre_delete_2026-10-16.json). Was 1,543 (1,540 found + blank dup row 18262 + V2919 + V4002 which were true before and NOT in list). Missing from table: V422, V3323.
  Backup of prior state: .claude/xano_backups/table11_gold_chip_pre_bulkset_2026-10-16.json; list: gold_chip_list_2026-10-16.txt.
  ep119 responses cached 24h -> gold flags on cards stale until expiry or cache_v bump.

## 10-16 03:00 UTC: ep119 v26 LIVE + gold chips + WeWeb editor changes
- v26: P/E no full package read (cached fn91 ep119_pkg_vendor_ids when filtered; page-vendor package read) Photography 12.8->3.0s,
  Entertainment 11.9->3.4s; similar_to_vendor_id skips count (2.5->0.9s; itemsTotal now 0 there, WeWeb reads only itemsReceived);
  sort_by gold_first. Rollback ep119_pre_v26_2026-10-16.xs. Live code == ep119_v26_2026-10-16.xs (pushed header comments condensed).
  fn83 + fn91 are LIVE dependencies. Filtered P/E still slow: main query `Vendor_ID in? [~1200 ids]` (p95 81s pre-existing).
- Gold-first DEFAULT order NOT done: any non-id sort ~3.5-4s vs ~2s default uncached; composite index (gold_chip, Clicks) untested - needs user OK (table-11 schema).
- fn56 (subagent) gold partners first; ep231 public cache 4h (cache_v 22), ep230 paid uncached. Only 30 venues affected.
- WeWeb EDITOR (unpublished): paginator 178295e2/29a96fed dim+pointer-events none while collection 5c91206d isFetching;
  sort selects e6bf34e3/41ca2182 got "Gold Chips First"=gold_first. Backups weweb_discovery_*_2026-10-16.json.
- Fix B (manual): remove Similar_Vendors 26a214bf from fetch action d60cb590 in "Set Vendor View Page" 76817e7b (early fetch may block PI panels; UNVERIFIED that fetch-collections awaits).
- 10-16 gold filter (user: sort option only, NO gold-first default; add left-rail filter like outdoor ceremony): built in editor by subagent.
  var filter_gold_chip_only d23b31c5 (persistence flags GUESSED - match outdoor var 0e53ba66), paid row 14facd6f/toggle 0ec39b7a, locked row a9316d09,
  chip 43799da6. MANUAL before publish: (A) Search Vendors collection param gold_chip_only = wwFormulas.if(variables['d23b31c5-...']==true,true,false);
  (B) add reset of filter_gold_chip_only=false in wfs 9bd3002e, cf8b0e97, 8ff68bcf (+check button 5e4a2327). After publish check warmer hit rate unchanged.
- 10-16 Assistant page redesign (Des: couldn't see where to type; survey card dominated): user chose small feedback strip under latest answer
  + fresh chat on open (past chats in "Past chats" menu). Subagent working in editor.
- 10-16 user decisions: paid users NOT required to give assistant feedback; everyone gets a free chat to start (fresh chat on open).
  rec-engine commit 3b31303 (branch rec-optional-feedback, worktree ../tulle_rec_optfb, off origin/main a2e1052): openings always free
  (never counted), paid check-in non-blocking with new status field feedback_suggested. 149 tests pass. NOT PUSHED - push = Railway deploy;
  pair with WeWeb publish (strip visible on feedback_required OR feedback_suggested; input lock only on feedback_required).
  Rollback = redeploy a2e1052. Cost note: free openings now on every visit (~$0.04 each), free daily cap still applies.

## 10-16 ~04:00 UTC: more EDITOR edits (all UNPUBLISHED; editFrontendWorkflow DOES accept old-style actions)
- Rec Refine 0fa17e11 read back OK: send blocked only when feedback_required && tier!=='paid'; 200 for paid copies feedback_required (strip shows, non-blocking).
- ce0daac9 i4c_pick_chat: fresh chat every visit (reopens only ?chat= or chat created <2 min on this tab). Input lock bindings paid-exempt.
- Fix B DONE: Set Vendor View Page 76817e7b action d60cb590 no longer fetches Similar_Vendors 26a214bf (venue page fetches it last via 36ddb94a);
  Other Vendors page d1d9bbf6 onload 362c3d59 ov_refetch now includes 26a214bf. Backups weweb_set_vendor_view_page_76817e7b_pre_fixB / weweb_ov_load_extracted_362c3d59_pre_fixB (2026-10-16).
- Gold reset: maps built (scratchpad reset_<elem>.json) inserting gold_reset_2026-10-16 after the outdoor reset, before fetch, in
  8a3d9197 & 9152c270 (both wf id 9bd3002e, DIFFERENT chains), 2656e5e7 (cf8b0e97), 5e4a2327 (5e3781cd), bf0cf738 (8ff68bcf). Backup weweb_discovery_reset_buttons_pre_gold_reset_2026-10-16.json.
  DONE + READ BACK 10-16 (all 5 chains: outdoor reset -> gold_reset -> ... -> fetch). Element-scope edit with elementUid only touches that element even when wf ids are shared.
- Collection param gold_chip_only (A) still MANUAL. Gold-row move to top of advanced filters NOT done.

## 10-16 04:46 UTC: ep121 wptp_map_light gold LIVE (user OK "lets do that")
- New input gold_chip_only (bool; true -> gold_chip == true, else no filter) + items.gold_chip in output; cache_v default 9 -> 10 (purge).
  Backup ep121_pre_goldchip_2026-10-16.xs; release file ep121_goldchip_2026-10-16.xs (pushed header condensed).
- Tested via temp fn 96 (deleted): NYC bbox 559 venues / 107 gold unfiltered; filtered = exactly 107, 0.38s.
- MANUAL (user): MapLight collection a5f0a42c param gold_chip_only = same formula as Search Vendors.
- Mobile Filter Popup (component ed90717a): gold row 053d9a28 / toggle 7c541943 / wf 8e2b7c14; popup Reset all fea2c496 got SEPARATE wf 3f7c9a21
  (gold=false) - ordering vs main reset's fetch UNVERIFIED; proper fix = hand-insert into main reset wf + delete 3f7c9a21.
- Gold map pins + legend: subagent working (10-16 ~04:50 UTC).

## 10-16 ~05:30 UTC END OF SESSION (user: "ok this works, lets quit now")
- Map gold pins (ww-mapbox 43235d7f markers formula: non-hover color gold #D4A017 when item.gold_chip===true) + legend item 15ee38f7. Backup weweb_map_pins_pre_gold_2026-10-16.json.
- User added gold_chip_only to MapLight collection; user hand-fixed mobile popup Reset all (said "ok done").
- Assistant header: profile card 09fb557e hidden desktop/tablet (mobile kept); header summary 2fca9ec6, Edit details cfc21e03 (Rec Details Open, source header_edit), Prefs link 61be4be2. Backup weweb_assistant_header_pre_compact_profile_2026-10-16.json. User previewed: "this works".
- PUBLISH STATUS UNKNOWN (user said they'd publish). NEXT SESSION: verify live /data + main.js for all 10-16 editor changes;
  then ASK before pushing rec-engine rec-optional-feedback (3b31303/a5e95f5) to Railway (paid feedback non-blocking needs it); check ep119/ep121 timings + cache hit rate.
- 10-16 05:17 UTC: rec-engine a5e95f5 (rec-optional-feedback) LIVE on Railway at user's request ("push the assistant server change live now"); 149 tests; all 4 services SUCCESS; rec /health 200. Rollback = redeploy a2e1052. No real assistant traffic checked yet.
- 10-16 ~05:25 UTC: live /data still pre-publish (Discovery last-mod 10-15 05:15 UTC; no gold_first). User publishing after; VERIFY TOMORROW:
  Discovery /data 0d69d71b contains gold_first, d23b31c5, gold_reset_2026-10-16, #D4A017, "Gold Chip vendor"; Assistant /data dae212b5 contains
  header_edit, "Rec Header Profile Summary", "status.tier !== 'paid'". Fetch via PowerShell Invoke-WebRequest (Bash curl has no DNS in sandbox; WebFetch truncates at 100k).
  Then: first real chats on a5e95f5 (paid not blocked, free opening not counted), ep119/ep121 timings + cache hit rate.

## 2026-10-17 empty map / '0 vendors shown' on load (owner screenshot, editor preview)
- Server was fine: last 12h ep119 1130/1130 and ep121 391/391 returned 200, none empty; owner's own search 0.018s.
- CAUSE (map): Discovery onload 'Updated Collections' fetches MapLight a5f0a42c ONLY if var mapLightFetch 9755e85b (default false,
  localStorage) is true, and the map's map:load 'Updated Center' wf resets it to false -> map view loads with no pins until a drag or Search.
- FIX (editor, unpublished): NEW page onload wf f3c9d5e4 'Map pins on load' (existing wfs untouched): waits <=6s for map instance + 0.7s,
  if view=Map and MapLight empty -> execute global 'Recalculate Vendors' d0bcf424. Unwind: delete f3c9d5e4.
- Count label 6068088b now says 'Loading vendors…' while search/pins load (pins only when map instance exists) instead of '0 vendors shown'.
  Backup .claude/xano_backups/weweb_vendor_count_label_pre_loading_text_2026-10-17.json.
- ep119 app (12h): p50 0.18s p90 2.27s p99 7.1s; ep121 p50 0.05 p90 2.9 p99 15.9s.
