---
name: project_backburner_2026-10-25
description: "Owner's backburner list 2026-10-25 (not to start until asked): tours, Railway vs Xano search, assistant as couples subscription, assistant UI w/ cost tools, experimentation infra + results registry in admin dash, admin dash speed, collaboration mode after full budget planning"
metadata:
  node_type: memory
  type: project
  originSessionId: abb664ba-4018-4524-a407-838ba2ba7236
  modified: 2026-10-25T20:53:39.264Z
---

Owner's own notes, 2026-10-25 - "keep this on the backburner"; some pieces may already be done.

1. **Tour updates** - 10-24: assistant tours reset to v2 keys + Cost tools step (editor). [[project_after_onboarding_tour]]
2. **Railway vs Xano search speed - move over now?** Shadow showed Railway p50/p95 25/92 ms vs ep119 1361/3861 ms, 72/76 parity;
   blockers = Meili stalls + WeWeb fallback. [[project_xano_upgrade_contention_2026-10-19]] [[project_maplight_widebbox]]
3. **Tulle Assistant searches -> eventually a SUBSCRIPTION product for couples** (full-fledged planning). [[project_muse_style_agent]] [[project_ai_recommendation_engine]]
4. **Tulle Assistant UI/UX with cost tools** - 10-24 pass built (editor/staging). [[project_two_pillar_consolidation]]
5. **Experimentation infrastructure** - owner expects MANY sequential experiments, mostly triggered via Claude Code. Wants an
   experiment REGISTRY + reporting: each experiment's goal, time frame, results, themes, what may have confounded it, and stat-sig
   or not; live in the Tulle Admin dash (Railway, tulle_vendor_data_updates). Existing pieces: [[project_experimentation_framework]]
   (Mixpanel flags, inert until head block), pricing test readouts in the daily brief, [[project_hub_pricing_readout_2026-10-21]].
   Known confounds to log: 10-14 price move + offer + free assistant shipped together; 10-24 checkbox removal + tours + popups + 5-star Trustpilot test.
6. **Tulle Admin dash speed** - teammates complain about usability. [[reference_railway_admin_dashboard]] [[project_dashboard_theme_modes]]
7. **Collaboration mode** - vendors/planners running a planning tool with their couples; couples sharing with family. PREREQUISITE:
   full end-to-end planning, at least budgets (Budget v2 / AI plan builder concept). [[project_vendor_portal]] [[project_two_pillar_consolidation]]

8. **'Impersonate' endpoint** - owner mention 10-25, low priority (likely admin view-as; see [[project_admin_view_as]]).

**10-25 started:** experiment log at tulletogether/.claude/experiments/experiment_log.json (9 experiments + changes/confounds). Search e2e measured 10-25 (see the log's EXP-2026-10-search-railway).

**How to apply:** don't start these unprompted; when the owner picks one up, start from the linked memories. For #5 every
experiment launched from now on should be recorded (goal, start, metric, what else shipped the same week) even before the dash exists.

**10-25 assistant latency measured (283 real requests, 10-18..10-25):** p50 ~12.6s, p90 23-24s; avg: model 43%, Xano 55%
(ep119 search ~4s/req, ep230 pricing ~3s); slow 17% (~28s) driven by ep119 (9.6s). Levers: (1) rec-engine uses Railway
search w/ ep119 fallback ~3.5-4s avg / 8-9s tail (owner YES, AFTER product items ship; raise sync to hourly first);
(2) lighter pricing 1.5-2.5s; (3) skip opening's first model turn 1.5-2.7s. Mixpanel report #erQozi5dk9gi.

**10-26 RAILWAY SEARCH FOR ASSISTANT - STARTED (owner):** 10% of users (stable sha256 bucket), ep119 fallback (3s timeout /
non-200 / bad shape), flag REC_RAILWAY_SEARCH_PCT (0 = off). Owner wants progress in the Tulle Ops DAILY BRIEF and to be
prompted regularly until solved. Done 10-26 ~00:50 UTC: table 79 +cols search_engine/search_fallback/search_ms (backup
table79_rec_usage_schema_pre_search_cols_2026-10-26.json); tulle-search sync-cron 08:30 daily -> hourly "10 * * * *"
(redeployed; sync takes ~4.4 min). Building: branch rec-railway-search (routing, logging, parity script, rec_watch brief block
with verdict). Ramp rule: >=50 railway reqs, search p50 >=30% faster, fallback <5% -> 25%.

**10-26 01:08 UTC LIVE (owner yes, 15% not 10%):** rec-engine main 292d826; Railway vars REC_RAILWAY_SEARCH_PCT=15,
REC_RAILWAY_SEARCH_FORCE_USER_IDS=14998 (owner's account always Railway; internal ids excluded from brief comparison).
Verified: /rec/status search_arm=railway pct 15; state search row 823 engine=railway search_ms 234 total 12.6s.
NOTE: city queries go through the radius path (venues_near, table 11) -> not part of the switch (engine ''). Rollback =
REC_RAILWAY_SEARCH_PCT=0 (instant) or redeploy 86f4495. Daily brief has the "Railway search (assistant)" block + verdict.
**10-26 01:20 UTC first hourly sync OK** (started 01:14:36, done 01:20:20, ~5.7 min, 19,491 vendors).
**Discovery for owner via ep119 proxy:** preview api119_ep119_railway_owner_PREVIEW_2026-10-26.xs (one block, $auth.id in [14998],
forwards Bearer via $env.$http_headers, 3s timeout, shape check, else falls through). Backup api119_prepush_ep119_railway_owner_2026-10-26.xs
(updated_at 2026-10-16 03:00:11). CAVEAT: ep119 cache key excludes auth -> Railway answers to owner's uncached searches enter the
shared 24h cache. Awaiting owner choice A (accept) / B / C before push.

**10-26 01:39 UTC DISCOVERY ON RAILWAY LIVE (owner yes, option A cache):** ep119 top block (backup
api119_prepush_ep119_railway_owner_2026-10-26.xs; live = ..._PREVIEW_v2). Gate: $auth.id == 14998 || sha256 bucket <15 (same
as rec-engine arm). Forwards Bearer ($env.$http_headers works for Authorization), 3s timeout, shape check, header
X-Search-Engine: railway | ep119-fallback;<reason>. Live checks: owner 0.76/1.03s, in-bucket 0.98/2.28s vs out 2.5-5.1s.
Forced fallback (SEARCH_ENABLED=0) -> ep119-fallback;http_503 with full result. KILL SWITCH = Railway search-api
SEARCH_ENABLED=0 (instant). INCIDENT: first push 01:33:32-01:33:47 used `$auth.id in [14998]` -> runtime "Invalid op: in",
500 for all; auto-rolled back in 15s; 2 real iPhone searches failed. LESSON (also in xanoscript traps): test hot-endpoint
changes on an uncached COPY endpoint first. TODO: daily brief Discovery split by X-Search-Engine (endpoint_health reads
request history response headers); experiment log entry.

**10-26 01:53 UTC RAMP 15% -> 20% (owner asked, both arms):** ep119 `$rw_pct` 20 pushed 01:52:54 via xano_push.py (diff = 1 line,
built from the LIVE script because Xano reformats on store; backup api119_live_15pct_backup_2026-10-26.xs). rec-engine
REC_RAILWAY_SEARCH_PCT=20, redeploy SUCCESS 01:53:30. Experiment log 601b2b6 PUSHED 01:58 (owner yes), dash deploy SUCCESS; rec-engine deploy SKIPPED (no restart).
Planner cards (venue meta line hidden, address -> 'City, ST') published ~01:51 and verified in live page data.
VERIFY TRAP: /data/<pageId>.json with no query returns a stale CloudFront copy (max-age 1y); WeWeb fetches
`?wwlang=en&_wwcv=<ver>` - add any query string to get the fresh file.

**10-26 ~21:30 UTC A/B READ (subagent, logs 01:53-21:00 UTC):** Planner search_ms median 68 ms Railway vs 3,997 ms ep119 (7 vs 45 reqs, 6 vs 21 users; p~1e-5 per-user); whole answer 9.4 s vs 14.4 s (loose, cluster CI -8.2..-1.0 s). Discovery cache MISSES 0.44 s vs 1.52 s median (p90 1.4 vs 6.6 s; IP-cluster CI -1.85..-0.64 s); cache hits identical (~55% of requests). RAMP NOT MET: only 7 Railway planner reqs (<50) and Discovery fallback 9% (31/345, all `http_0` = no response, 24 of 31 at 16:00 + 19:00 UTC) > 5%; a fallback costs ~6.3 s. Parity last run 10-25: 13 exact / 9 order-only / 1 set diff. Scripts: session scratchpad railway_ab/. NEXT: find why Railway stalls at those hours before any ramp.

**10-26 ~22:05 UTC FIX LIVE (owner OK):** root causes of the 9% fallbacks: (1) Meilisearch itself freezes 8-65 s (~11x/day; NOT memory/CPU - Meili max 1.0/8 GB, NOT the hourly sync); (2) search-api maybe_reload read sync meta with the 60 s admin client ON the user request (19.6 s hang at 19:42, nothing logged). tulle-search 9b0adb5 (main, deploy SUCCESS 22:03): in-process breaker - after a Meili timeout /search+/map return 503 X-Breaker: open for MEILI_BREAKER_S=120; meta read on fail-fast client + background whitelist reload; MEILI_QUERY_TIMEOUT_S default 4->1.5; /health shows breaker {open,trips}. ep119 Railway wait 3 s -> 2 s (push 22:04, byte-verified; backup api119_prepush_api119_ep119_timeout2s_PREVIEW_2026-10-26.xs). Fallback reasons now: http_503 = breaker (fast), http_0 = real 2 s timeout. Xano REDIS IS NOT USABLE (redis.has/set fail instantly - tested, function deleted). Ramp rule for Discovery: 1 day fallback <5% + miss median still ~70% faster -> 35% then 50%. Root cause of Meili freezes still unknown (suspects: PATCH experimental-features on each whitelist reload; Railway disk).

**10-26 ~23:25 UTC 50% READ + ROOT CAUSE:** ramp to 50% pushed ~22:20 (ep119 $rw_pct 50; rec-engine REC_RAILWAY_SEARCH_PCT=50). 22:51-23:25: 179 searches all 200, Railway misses 0.49 s vs Xano 1.57 s; breaker tripped 4x (22:50, 23:00, 23:18, 23:23) and worked (17 fast http_503 fallbacks); slow http_0 = 5/79 but 3 of those were instant (Xano-side connect fail, never reached Railway). ROOT CAUSE of Meili freezes: search-api/sync-job PATCH /experimental-features (sent 9x in 35 min) held 5-48 s by Meili during sync indexing; 3 of 4 trips sat inside one. Fix tulle-search 483029c (GET first, PATCH only if off, once per process) - COMMITTED LOCALLY, NOT PUSHED (awaiting owner OK). Do NOT ramp to 100% until it is live + 1 clean day.
**10-26 23:18 PLANNER v3 DEPLOYED** (tulle_vendor_data_updates main c4ec678, rec-engine SUCCESS; worktree ../tulle_rec_planner branch planner-v3): pricing cache (REC_PRICING_CACHE_S), per-turn timing props (turn1_model_ms...), price question -> upsell_prompt (free), thin-profile questions (REC_PROFILE_QUESTIONS), instant name lookup via venue search (REC_NAME_LOOKUP; misses -> Slack #feedback + Mixpanel rec_venue_request), listings categories Flowers/Beauty/F&B/Planner (REC_LISTINGS_IN_CHAT). Live smoke test passed (local key in ~/.tulle_rec.env replaced 10-26). ac172c7 (PDF-first for free users w/ views, wall card for 0 views) COMMITTED, NOT DEPLOYED. WeWeb upsell card + venue request being built by a helper (editor only).

**10-27 ~05:30 UTC STATE (owner):** Discovery Railway stays 50% - owner: "we'll move discovery once it looks better tomorrow". Read at ~10-27/28: fallback rate must be <5% (was 8.5% 23:50-04:49 UTC, 48/562: 21 breaker 503 from freezes 02:42/03:05, 27 http_0 mostly Xano->Railway connection, only 6 seen by Railway edge). Freeze diagnosis: hourly sync rebuilds indexes (index create blocks reads up to 63 s); fixes approved+deploying: search-api maybe_reload off request path, sync every 6 h + manual trigger, skip/in-place updates. Planner -> Railway 100% + breaker APPROVED (with prefetch cache + same-day opening reuse) - built/tested, DEPLOY needs owner's explicit "deploy the Planner changes". Map clusters/list/quick links PUBLISHED for all signed-in users 10-27 ~05:00 UTC (kill switch: WeWeb var map_cluster_preview=false + publish). fn54 peak-season fix + captions + nightly rebuild (task 15 09:40 UTC) LIVE - check first nightly run.
