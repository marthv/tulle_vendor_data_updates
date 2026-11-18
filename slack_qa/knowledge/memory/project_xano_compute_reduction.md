---
name: xano-compute-reduction-2026-07
description: "Xano DB CPU reduction effort (111% peaks) — Phase A shipped 2026-07-19, Phase B (enrichment denorm) in progress"
metadata: 
  node_type: memory
  type: project
  originSessionId: d9864838-9801-45f4-ad0b-6bb294105672
  modified: 2026-07-19T15:14:23.897Z
---

Xano DB CPU peaked ~111% on Essential plan. Plan file: `~/.claude/plans/tulletogether-xano-compute-cached-shannon.md`. Branch: xano-cache-optimization.

**Phase A SHIPPED 2026-07-19** (backups in .claude/xano_backups/*2026-07-19*):
- EP192 (test_recompute_vendor_aggregates) DELETED — was public+write-triggering, zero retained traffic
- table 11 btree index on flt_synced_at (task 10 was full-scanning every 5 min)
- task 10: 5min → hourly at :15; task 7: 02:00 → 02:30 UTC
- task 8 warmer: realigned to real traffic (23 buckets: grid states pages 1-2, states[]-multiselect top-4, page_size=4 shape; slider steps dropped — zero usage), freq 7200 at odd-hours:30; ep 119 cache ttl 3600 → 7100 (must stay < warmer freq, see [[xano-cache-resolved-inputs]])

**fn21 trigger graph** (recompute_vendor_filter_aggregates, ~128k calls/30d): table-36 trigger #3 (insert/update), task 10, (EP192 deleted). 24h staleness roll via flt_synced_at drives the volume.

**Phase B1 SHIPPED 2026-07-19:** table 11 `flt_glance` json column (17 glance fields, 119's EXACT expressions — raw min/max no >0 filters, Venue_Attributes NOT ; split, pricing_years no filter_empty — deliberately different from flt_*, don't "fix"); fn21 v5 writes it; 119 v7 + 121 v7 read it with v6-identical live fallback when null; include_* passthrough gated behind flags. Parity 7/7 (119) + 3/3 (121) byte-identical. **The decisive win was a composite btree (Category, Validated_Data, id) on table 11** — main query was seq-scanning the blob-heavy table (~1.3s/pass, ×2 with totals:true); index → count 19ms, page+totals 64ms, cold NY search 4-6s → ~50ms. Per-state substring `State includes` still costs ~1s worst-case (heap filter of venue subset) — semantics load-bearing (comma-delimited State, Virginia⊂West Virginia), intentionally kept, as was the rarely-used sort_by fetch-10000 path (0/300 requests).

**COMPLETE 2026-07-19 ~15:15 UTC.** Backfill done (10,557 rows; junk row 4838 blank-Vendor_ID excluded via task-10 guard). Task 10 final: hourly :15, staleness **7d** (was 24h — daily full-table rewrite churn was bloating the blob-heavy table and degrading scans), flt_glance-null self-heal, Vendor_ID != "" guard. Audit extras: ep 74 favorites fixed (blob leak + N+1 → allowlist + glance; has_outdoor_ceremony now 119-semantics), ep 40 blob columns dropped (zero-traffic, deprecation candidate), Packages.user_id btree added. **OPEN: ep 112 packages no-user_id path returns ALL users' packages (5.5-7.2s, fired by app on every load) — needs product decision on WeWeb binding intent.** Cold timings dip to ~2.5s right after mass rewrites (index-only scans degrade until autovacuum) — fresh-index cold was 50ms; check Performance Insights next day.

**Gotcha:** intermittent `getaddrinfo ENOTFOUND xqtb-2ma7-ijfy...` DNS failures in waves (MCP + curl exit 6) while google resolves — retry after ~10-60s, it passes.
