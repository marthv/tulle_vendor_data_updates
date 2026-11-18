---
name: project_ep119_cache_ttl_24h
description: ep119 search cache ttl raised 7100s -> 86300s on 2026-10-03; warmer (task 8) now every 6h (4/day) as a retry loop
metadata:
  node_type: memory
  type: project
  originSessionId: d9595695-7823-4f8c-9645-96340d80aa7f
  modified: 2026-10-19T04:14:39.932Z
---

2026-10-03 ~15:12 UTC: ep119 `ttl 7100 -> 86300` pushed via `.claude/xano_backups/apply_cache_ttl_24h.py`
(backups `ep119_pre_ttl24h_2026-10-03.xs`, `task8_pre_ttl24h_2026-10-03.xs`; `--restore` puts both back).
Read-back: only ttl + comments changed. Task 8 schedule UNCHANGED (7200s); only a header note added.

**Why:** user — data changes every 2-3 weeks, 2h expiry was recomputing identical results. User ACCEPTED
that a vendor hidden via Slack bot / staged edit / whitelist edit can stay in cached results up to 24h.
To flush sooner: bump ep119 `cache_v` default (was 19).

**Why task 8 stays 2-hourly:** user asked "if any fail, run again in 2 hours, until they're all run for
the day". Fresh keys come back as ~10ms cache hits, so each 2h run only really refills expired/failed
keys = retry loop with no state table. Depends on hits NOT extending ttl — verified 10-03: 92 of 378
warmer misses were on keys a user had hit in the previous hour.

**Expected (simulated on 1 day of history, lower bound):** app hit rate ~58% -> ~64%, ~156 fewer slow
searches/day (~1.4s each). 90% of misses (1,351/1,508) are first-ever keys — no cache helps those.
**UNVERIFIED:** hit-rate gain. Check `cache_hit_pct` in table 78 for report_date >= 2026-10-05 against
~60-63% baseline (10-01..10-03). Warmer audit: 10-02 17:30 run missed 4 URLs (CT/VA), 23:30 missed Beauty;
cause unknown (no task output retained).

**UPDATE same day (user): task 8 cadence 7200 -> 21600 (4 runs/day: 01:30/07:30/13:30/19:30 UTC)** via
`apply_warmer_6h.py` (backup `task8_pre_6h_2026-10-03.xs`, `--restore`). Read-back verified; retry now within 6h.

**2026-10-19 ~04:20 UTC GOLD REALIGN (user OK "fix the warmer now"):** app sends gold_chip_only=false on every page_size=12 grid
call since the 10-16 gold publish (259/262; strip/ps24/similar don't send it) -> 28 grid URLs got &gold_chip_only=false.
Pushed via xano_push (code lines verified identical, published, active). Sample 02:37-04:12 UTC: app calls matching a warmed key
29/437 -> 61/437; app hit rate then 52% (228/437). File task8_v_goldchip_align_2026-10-19.xs; rollback
task8_prepush_task8_v_goldchip_align_2026-10-19.xs. UNVERIFIED until after the 07:30 UTC run: re-measure hit rate.

**How to apply:** the 6h cadence is the user's choice; don't change it without asking. [[reference_xano_cache_resolved_inputs]]
