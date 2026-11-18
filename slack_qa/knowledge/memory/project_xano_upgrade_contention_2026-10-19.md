---
name: project_xano_upgrade_contention_2026-10-19
description: "10-19 measured: Xano upgrade worth <=500-630s/day search wait (est, CPU unseen); contention only 1.6-2.1x (CORRECTS 2-20x); Railway python client 152.55.180.172 = 20% of Xano time + 00:04 UTC stall"
metadata:
  node_type: memory
  type: project
  originSessionId: d461cc1d-f540-4ec1-bcdd-000d8a6a23c5
  modified: 2026-10-19T04:07:21.611Z
---

Measured 2026-10-19 (24.2h request history 10-18 00:30 -> 10-19 00:40 UTC, 45,801 reqs all endpoints; history only keeps ~24h).
Scripts/data: session scratchpad perf48h/ (ephemeral).

- ep119 real users: n 4,019, p50 0.10 p90 2.22 p99 6.4; hit 62.8%; uncached p50 1.49.
- **CORRECTS earlier figures:** uncached alone p50 **1.25s** (not 2.1-2.4); concurrency slowdown p50 2.02/2.18/2.68s for
  1-2/3-5/6+ others in flight (not 4.2/9.4/48s). 56% of uncached wait runs ALONE.
- **Upgrade ceiling (estimate): ~500-630 s/day** of search wait. CPU NOT observable via MCP (plan = essential2) - user must check
  Xano Instance > Performance Insights at 10-19 00:04-00:07 UTC. Not yet recommended; triggers are cheaper to fix.
- **Stall 10-19 00:04-00:07 UTC** = 16% of the day's user Xano time (auth/me to 33s). Coincident: Railway python-requests
  **152.55.180.172** (~40 parallel admin-API table-11 searches + 30 parallel pricing_intelligence_full; ~20% of all Xano time/day,
  ep119 page_size=60 p50 4.1s) AND one iPhone panning map firing 17 ep121 calls in 30s. Which service owns the IP: UNVERIFIED.
- **CPU CHECKED (user screenshots 10-18 21:13 ET, Xano Instance CPU chart):** 2-week daily avg 10-35% (DB/API/Tasks);
  hourly Tasks spikes ~50-70% (~:10-:15, likely task 10); stall window 20:04 ET only ~50% (chart may smooth 3-min spikes);
  one DB ~90% spike ~20:45-20:52 ET = coincides with MY agents' history pulls + table-11 export (probable, unproven).
  VERDICT given to user: don't upgrade; fix bursts (Railway parallelism, map pan) + search index instead.
- v25 page>=2 path WORKS in prod: 754 calls, 0 errors, uncached p50 0.63s (was 4-5s 09-30).
- Same-client page bursts GONE (1 small in 24h). Google oauth/google/continue: 26/55 returned 400 - unexamined.
- Fix candidates (not started): throttle Railway client concurrency; debounce/cancel ep121 on map pan.
- **Meilisearch prototype 10-19 (local, scratchpad searchproto/):** v1.54.3, sync.py + search.py (ep119 inputs/output shape);
  local p50 4ms, p95 61ms, max 113ms (P/E package filter slowest). Parity vs live ep119 NOT RUN (allowed 04-12 UTC only).
  Table 11 = 18,246 rows (not ~13.4k). Table 61 all true. My export 00:44-01:07 UTC CAUSED the ~90% DB CPU spike (50-row pages, 1.5s gap too aggressive).
  Search_Input uses Xano FTS index 31e46fd2 (not substring). Prod est 5-8 days; needs auth (Xano tokens encrypted), no browser key.
- **USER DECISIONS 10-19 ~02:00 UTC (search on Railway, SHADOW only):** build on Railway now + compare alongside Xano a few days.
  New private repo marthv/tulle-search, Railway project "tulle search" (meilisearch private + search-api, auth via Xano auth/me).
  Sync = DAILY ONLY (08:30 UTC), NO table triggers (user: don't spike Xano CPU on edits); "sync now" admin cmd; refuse swap if counts drop >10%.
  Xano slim export endpoint APPROVED ("go ahead with the Xano export endpoint"), secret-header protected; backup ep_search_export_v1_2026-10-19.xs.
  Comparison = Railway replay cron ~09:00 UTC vs ep119 (NO WeWeb change). Monitoring = Slack DM to owner daily + on failure; NO frontend.
  Go-live (WeWeb switch + ep119 fallback) NOT approved yet.
- **BUILT 10-19 ~02:50 UTC (shadow, no users):** repo github.com/marthv/tulle-search (private). Railway project "tulle search"
  2efeba12-e946-401f-89f9-c106ff710315 (us-east4): meilisearch da4b4d23 (private, volume), search-api dd74f222
  https://search-api-production-66bb.up.railway.app, sync-cron bf28d76c (08:30 UTC, OFF until EXPORT_URL set), replay-cron 6e4c3fcb
  (09:00 UTC, OFF until fresh REC_TEST_TOKEN). Railway == local prototype 32/32; p50 45ms from here (~42ms is network). Est $6-8/mo.
  Xano **ep318 search_export** (group 10 api:GynP5T1B, secret = [redacted] sha256), backup ep_search_export_v1_2026-10-19.xs - NEVER CALLED yet.
  /search accepts X-Admin-Secret override (testing). Admin secret plaintext only in ephemeral scratchpad tsearch_local/admin.env (Railway stores sha256).
  BLOCKERS: REC_TEST_TOKEN in ~/.tulle_rec.env EXPIRED; Slack token must be copied BY USER (rec-engine REC_SLACK_BOT_TOKEN -> SLACK_BOT_TOKEN
  on search-api/sync-cron/replay-cron) - agent copy blocked by permission check. Undo = delete Railway project + Xano ep318.
- **10-19 ~03:40 UTC:** user fixed Slack vars (Tulle Ops DM RECEIVED by owner) + fresh REC_TEST_TOKEN (local env + replay-cron).
  ep318 verified (user ran it): 401 missing/wrong secret; t61 9/9, t11 32/33 cols identical (Clicks drift only), t36 itemsTotal 15,538.
  EXPORT_URL set on sync-cron -> nightly 08:30 UTC sync ON. First full sync via sync-now 03:34-03:39 UTC OK: vendors 18,246, packages 15,538.
  One /health timeout mid-sync (API + sync share a process) - watch. Python ssl fails on Railway cert locally (local CA store); use curl.exe.
- **PARITY 10-19 04:00-04:10 UTC (32 cases, 28 real + 4 synthetic) Railway vs live ep119:** first 27/32; fixed in tulle-search 4b073d0
  (similar-vendor ppfb band via f_ppfb facetStats; NAME_COLLATION=c var + default) -> **ids in order 32/32, itemsTotal 31/32**.
  Remaining: "Fort Worth " text 33 vs 32 (Meili splits "Dallas/Fort"; Postgres simple parser doesn't - ~70% conf). Not exercised:
  facets, fallback_all=true (WeWeb sends false), outdoor_ceremony, venue_types/vibes, P/E package filters. Railway server 7ms p50.
  Captures: scratchpad searchproto/data/cases_real.json + parity_real.json. Python TLS to Railway works via truststore.inject_into_ssl().
- Parallel track: local Meilisearch prototype of ep119 (scratchpad searchproto/) for "instant search" via dedicated index.
[[project_search_slowdown_2026-10-15]] [[project_slowness_breakdown_2026-09-30]] [[project_flt_states_denorm]]

**10-20 ~03:30 UTC search prep + /map DONE (shadow only; owner approved 10-19):** tulle-search e21a72e (app/pgts.py = Postgres
to_tsvector-style tokenizer -> text search exact vs ep119; GET /map = ep121 port; replay = 32 real + 32 synth + 12 map cases) +
9d99396 (MEILI_QUERY_TIMEOUT_S=4, no retry -> fast 502). Parity: pgts vs real PG16 32,783/32,787 strings; barn 336=336, Fort Worth 32 same
ids; 200 distinct real 24h queries -> 200/200 after re-check (6 were stale ep119 cache, 4 local DNS); map 12/12 ids+order. Speed:
search p50 12.5 / p95 35 ms; map small 7/12, NYC 40/50, TX 68/87, US 129/162 ms (ep121 now ~0.5/3.2/5.3/14 s). Cold-token auth/me adds
~470 ms once per 5 min. ep119 requires auth; 4,239/4,239 app calls had a token -> no signed-out gap. BLOCKERS: Meili stalls 2/~300 calls
(one 71.6 s; cause unknown, maybe volume I/O) -> go-live needs WeWeb fallback ~3 s + days of stall-rate data; REC_TEST_TOKEN expires
~11-02 (owner must log in as test account ~10-31). Manual sync-now inside search-api froze /health ~90 s - don't use it (nightly runs in
its own service). Owner to decide: keep whole-US map replay case (~15 s Xano/day).
