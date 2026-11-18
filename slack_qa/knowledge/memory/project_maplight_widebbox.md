---
name: project-maplight-widebbox
description: PINNED PERF — ep 121 wptp_map_light 8-17s on wide/zoomed-out bounding boxes; foreach over 2500 rows suspected
metadata: 
  node_type: memory
  type: project
  originSessionId: 1ee0b3e6-d423-4adb-bca4-63d59b2ef261
  modified: 2026-07-21T06:49:58.242Z
---

PINNED (open as of 2026-07-21). ep 121 wptp_map_light cold calls on wide viewports: 7.9s/4.9s (NE US), 8.8s prod (~14° span), 16.8s editor (whole US ~40° span). Narrow boxes are 0.02-0.3s. All light-path (no filters), so cost is NOT enrichment.

Suspected: XS foreach over up to 2500 rows (per_page 2500) that only does `|unset:"flt_glance"` per item on the light path + big JSON payload. Fix ideas: (1) when not enriching, skip the foreach — drop "items.flt_glance" from db output via a second light-only db.query branch; (2) cap per_page lower on wide bboxes (2500 pins unrenderable anyway); (3) WeWeb fires TWO identical map calls concurrently per move (seen 16.8s + 0.048s same second) — dedupe in WeWeb; cold viewports pay double.

v8 shipped 2026-07-21 added Venue_Type + flt_venue_types to light output (cache_v=5); bind flt_venue_types in WeWeb (raw Venue_Type holds legacy pricing-model labels). Backups: ep121_{pre,v8}_venue_type_output_2026-07-21.xs.

Related: [[project_login_speed]], [[project_flt_denorm_aggregates]].

**2026-10-19 ~22:30 UTC SLIM MAP LIVE (owner OK "lets do the quick xano win now"):** ep121 non-enrich path (all live app calls;
0/1,013 sent enrich in 24h) no longer reads flt_glance nor loops to unset it -> response = $model_slim.items. Pushed via xano_push
(.claude/xano_backups/ep121_slim_map_2026-10-19.xs); live == sent (1 blank line). Bench (forced misses, test token, same boxes):
results byte-identical (sha match); NYC 602 pins 5.4-5.6s -> 3.1-4.0s; TX 993 pins 5.8-6.2s -> 5.1-5.5s; US 2,500 pins 20-27s -> 14-15s;
small boxes ~0.6s unchanged. One 502 on the first US call after push, 3 retries fine. ROLLBACK FILE RESTORED by owner OK 10-19 (was: a post-push --dry-run
overwrote api121_prepush_ep121_slim_map_2026-10-19.xs with the NEW script; the true original is scratchpad xmap/ep121_live.xs
- copied back, header = GOLD CHIP 10-16 original). NEVER run xano_push --dry-run after a push: it rewrites the prepush backup. Railway /map port still planned ([[project_xano_upgrade_contention_2026-10-19]]).
