---
name: project_planner_speed_2a_2026-10-27
description: "Planner speed 2a LIVE 10-27 05:53 UTC (prefetch caches, Railway 100% + breaker, opening reuse) - DO the 24 h comparison on/after 10-28 06:00 UTC"
metadata:
  node_type: memory
  type: project
  originSessionId: 197623b5-67b9-4b3f-ae23-e4a6497efc21
  modified: 2026-10-27T06:00:34.445Z
---

LIVE 2026-10-27: rec-engine 12add68 (deploy eeea9ac0, 05:53 UTC) + REC_RAILWAY_SEARCH_PCT 50 -> 100 (deploy 2b2322fb,
05:54 UTC; the previous value 50 comes from the experiment log, because Railway redacts variable values). Experiment log: EXP-2026-10-planner-speed-2a (8ea92b2).

**24 h comparison due on/after 2026-10-28 06:00 UTC.** Mixpanel rec_server_request, kind=opening, status ok, internal ids
excluded, split by `reused`:
- baseline (v3, 10-26 23:18 -> 10-27 05:50): opening p50 12.7 s / p90 21.0 s; paid openings p50 12.6 / p90 24.3;
  prefetch p50 3.1 s / p90 4.9 s (paid)
- expected (estimate): p50 ~9 s, p90 ~16 s; reused share ~15% of openings at ~1 s
- check: prefetch_ms + bench/card_cache_hits, reused=true count, search_engine railway vs mixed, breaker_skips,
  breaker_state, any status=error rows in table 79.
- note: on a reuse event, latency_s only times the return step after the gate. The real client wait is ~0.8 s
  (measured 874 / 777 ms).

Live check 10-27 (owner 14998): reuse 0.87 / 0.78 s, no table-79 row, counters unchanged. Warm prefetch 2 ms
(2 bench + 5 card hits). A search-api Meili freeze at 05:55 (its own 503 breaker) sent 2 searches to ep119 (12 s, 8 s);
our breaker opened as designed. One fresh opening was 19.4 s from slow model turns (8.7 s + 10.7 s); our parts
took about 0.1 s. Free-tier opening NOT verified live (no free test account).

Rollback flips: REC_OPENING_REUSE=0, REC_RAILWAY_SEARCH_PCT=50 (or 0), REC_BENCH_CACHE_S=0, REC_CARD_CACHE_S=0,
REC_RAILWAY_BREAKER_S=0; or revert 12add68. Related: [[project_backburner_2026-10-25]].

**24 h READ 2026-10-28 ~13:15 UTC (Mixpanel ET days 10-27..10-28, internal ids NOT excluded):** openings ok n=70 (66 fresh,
4 reused). Fresh p50 8.1 s / p90 18.5 s (baseline 12.7 / 21.0). Paid fresh p50 9.2 / p90 21.1 (baseline 12.6 / 24.3); free
p50 7.05 / p90 10.1. Reuse share 6% (est. 15%). Median hit the ~9 s estimate; p90 missed it (est. 16) - slow model turns.
Discovery same day (ep119 history 10-27 18:00 -> 10-28 13:13 UTC, n=3003, editor/python excluded): cache misses p50 0.45 s /
p90 1.70 s; railway misses p50 0.42 / p90 1.41; fallback 44/1372 misses = 3.2% (0.8% in first 40 min on meilisearch-b),
incl. 10 new http_502 fallbacks (p50 6.5 s) - investigate.
