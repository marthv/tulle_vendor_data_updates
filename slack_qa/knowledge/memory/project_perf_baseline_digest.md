---
name: project_perf_baseline_digest
description: 2026-09-12 production latency baseline and the perf_digest health-check that enforces it; race-401s and the warmer key mismatch are the two open problems
metadata: 
  node_type: memory
  type: project
  originSessionId: 1c55a874-744a-4113-9ca4-188a29690ccd
  modified: 2026-09-27T19:18:53.396Z
---

**CORRECTION 2026-09-27: perf_digest was NEVER DEPLOYED.** Railway project "tulle admin dash"
has only batch-worker-cron / batch-ingest / extraction-batch / Tulle Admin Dash; Slack has zero
"Tulle performance" posts. "Runs 2-hourly" below was the design, not reality. A `--health`
daily mode (ep119/121 failure reasons, top queries, slow drivers, warmer audit, Xano history
table) + `railway.health.toml` (13:32 UTC -> #tulle-users) were written 2026-09-27, tested
offline, NOT deployed yet. See [[project_ep119_warmer_timeouts]].

2026-09-12. First real endpoint-performance baseline, measured from Xano request history
(2,997 requests, 05:58–15:32 UTC; 2,379 of them browser traffic on the live app from ~61
client IPs). **No 5xx and no 429s anywhere** — availability is genuinely fine. The happy
path is hurt by two specific things instead.

## Scope traffic to the app before quoting any number

The single biggest analysis trap here. Request history mixes real users with the pipeline,
the Streamlit dashboard, the cache warmer and your own meta-API calls. Filter on
`Referer contains tulletogether.app` or every figure is wrong:

| ep119 search | p50 | p95 | cache hit |
|---|---|---|---|
| all traffic | 1.46s | 3.84s | 28.5% |
| **real app users** | **0.30s** | **3.71s** | **45.7%** |
| warmer only | 2.77s | 3.84s | 0.9% |

I first reported 1.2s p50 / 28.5% and had to correct both. Search is *fast at the median*
for real users; what is wrong is the **tail** (p95 3.7s, max 6.6s).

## Problem 1 — the pre-auth race is the normal case, not an edge case

Splitting 401s by whether the same client IP also got an authenticated 200 within 120s:
**250 race vs 213 genuinely signed-out.** 55 of 61 sessions (90%) saw at least one 401.

Worst: **ep216 `my_access_summary` — 46% race-401s; 40 sessions got ONLY 401s**, i.e. the
access badge never rendered at all. Then ep74 favorites 22%, ep284 upgrade_offer 32%,
ep34 admin_choices 30%, ep3 auth/me 15%.

The raw 401 rate (15.75%) is useless as an alert — signed-out visitors on public pages
produce those forever. **The race subset is the actionable metric.** Heuristic, not proof:
carrier NAT puts many users behind one IP, so treat it as directional (~15% slop).

Fix is the token-wait guards, client-side — see [[project_preauth_race_signs_users_out]].

## Problem 2 — the ep119 warmer warms keys NOBODY requests (zero overlap)

The warmer is alive and healthy post-[[project_endpoint_lockdown_401s]] fix (23 buckets,
every 2h, 200s, with a token). But measured against real traffic:

```
warmer distinct cache keys : 23
app distinct cache keys    : 89
overlap                    : 0      ← app calls served by a warmed key: 0 / 188
```

Xano keys the cache on **resolved** inputs, so absent ≠ `""` ≠ `"0"`, and the two callers
spell every empty filter differently:

| param | warmer sends | app sends |
|---|---|---|
| `fallback_all` | `1` / `true` | `false` / absent |
| `max_capacity` | absent | `0` / `""` |
| `base_fee_max` | absent | `0` / absent |
| `Country_Input` | absent | `""` (also literal `"null"` 7x) |

So task 8 burns 60–90s every 2 hours populating entries no user can ever hit. **The fix is
to make the warmer replay the app's exact spellings — no ep119 change**, which keeps the
standing "no ep119 changes" decision intact. Done the same day — see the realignment
section at the bottom.

⚠️ I initially also recommended normalizing the app's params client-side, on the theory that
89 keys for 188 calls meant the frontend was fragmenting its own cache. **That was wrong and
is not worth doing** — collapsing every empty param merges 89 keys into only 83. The app is
internally consistent; the key space is genuinely long-tail. Measure before recommending it.

## The health check that enforces this

`perf_digest.py` + `perf_slo.json` in **tulle_vendor_data_updates** (the Railway repo, NOT
tulletogether/pipeline — see [[reference_railway_admin_dashboard]]). Passive, not synthetic:
it re-reads request history, so it measures the real query mix and real cache state, which a
prober cannot. Runs 2-hourly via `railway.perfdigest.toml`; Slack on breach only, plus one
summary at `PERF_DIGEST_HOUR` (default 08:00 UTC). Also a collapsed dashboard panel next to
`render_endpoint_health`. Exit 1 = breach, exit 2 = check could not run (louder — you are
blind). Modelled on `security_audit.py`'s baseline-diff philosophy: an alert nobody can act
on gets muted, and a muted alert is worse than none.

**Latency is only judged once an endpoint has ≥20 calls in the window** — a p95 over three
requests is noise.

Three budgets start red on purpose (ep119 p95, ep119 cache hit, global race-401) so that
fixing the two problems above is what turns the check green.

A synthetic probe is still worth adding as a second layer for one thing this cannot do:
notice an outage at 3am when there is no traffic to measure.

## Reading request history without burning context

The MCP `searchApiRequestHistory` returns ~1.4KB per item (full headers). `per_page=500`
overflows to a file on disk — then aggregate with a local python script and print only the
summary. Do NOT page it into context. A script reading `XANO_METADATA_TOKEN` out of
`.mcp.json` is blocked by the sandbox classifier; the MCP tool itself works fine.

## Smaller findings

- **ep258 suggest_index** p50 41ms but p99 6.4s / max 11.5s — cache misses only; self-heals.
  It IS firing in production (195 app calls), contrary to
  [[project_search_typeahead_dropdown]] saying the UI does not exist.
- **ep199 venue_pricing_dashboard** p50 6.7s, max 24.3s — admin/Streamlit only, worst
  latency anywhere.
- **ep146 wptp_pdfs** single call at 8.2s (~8.6MB payload).
- `auth/signup` threw 3×400 and 3×403 of 14 calls — unexamined, possibly real signups
  bouncing.

## 2026-09-12 — warmer REALIGNED and published (task 8)

Rewritten from 3 template-built loops (23 buckets, 0 real keys) to **one array of 9 LITERAL
QUERY STRINGS copied verbatim from real requests** — aligned by construction, so it cannot
drift the way a hand-maintained params map did. Published + verified in the run stack;
`active: true`, schedule unchanged (7200s at :30).

**The selection rule that matters:** ep119 has `cache.auth = false`, so warming only pays for
a key **more than one person** requests. 80 of 89 daily keys were single-visitor and are
unwarmable in principle. The 9 kept are every key 2+ distinct visitors hit. The old version's
mistake was warming a *guessed popular-states list* (TX/IL/CA/FL) — today's shared keys are
International / New York / Not Sure, so even the guess had gone stale.

**Falsifiable prediction:** app cache hit rate 45.7% → ~58%; task duration 58-69s → ~25s;
log line "Warmed 9/9". If duration stays ~65s the old script is still live. Remember a
**suspiciously FAST run is also a failure signal** (3s = everything 401ing).

Budget set to **55%, not 60%** — ~58% is the ceiling from warming alone, and an unreachable
budget is one you learn to ignore. Beating it needs a faster UNCACHED path = an ep119 change.

Backups: `task8_pre_key_align_2026-09-12.xs` (full rollback) and
`task8_v_key_aligned_2026-09-12.xs` (as shipped), both in `.claude/xano_backups/`.

**Corrections to the first pass of this analysis, both worth remembering:**
- Param-spelling normalization is NOT a win — collapsing empty params merges 89 keys into
  only 83. The "4 spellings of fallback_all" was warmer-vs-app, not app-internal.
- The MCP transport intermittently fails `getaddrinfo ENOTFOUND` while DNS and curl are fine.
  Just retry — it succeeded on the 2nd-3rd attempt every time.
