---
name: project_flt_states_denorm
description: "SHIPPED 2026-08-10 — table-11 flt_states denorm made ep 119 state filtering flat instead of linear in states selected (6 states 10.98s -> 1.21s). Includes the country: token trick and the measurements that killed the payload-trimming plan."
metadata: 
  node_type: memory
  type: project
  originSessionId: 15e56bb6-cbbd-4d5f-8c5c-e6d27694b014
  modified: 2026-08-11T01:35:46.516Z
---

**SHIPPED + verified 2026-08-10.** ep 119 **v22**. State filtering no longer scales with how many
states the user ticks.

| states[] | before | after |
|---|---|---|
| 1 (NY, 616 rows) | 2.31s | **1.06s** |
| 2 | 3.94s | **1.50s** |
| 4 | 7.77s | **0.88s** |
| 6 | 10.98s | **1.21s** |

**What it replaced:** ep 119 ran **one unpaged full-table scan of table 11 per selected state** to
collect ids, then fed the array back as `id in? [...]`. `State` is comma-separated multi-value
([[reference_table11_state_multivalue]]) so no index could serve `includes?`.

**The `country:` token trick — this is the non-obvious part.** New column `flt_states` (text list)
holds each comma-split `State` token **plus** `"country:<Country>"` when `Country` is non-empty.
Country must scope *only* the International bucket ([[project_international_filter_bug]]), which is
a union, and a Xano `where` ANDs. Swapping the request token (`International` → `country:France`
when a country is set) turns that union into a single `flt_states overlaps?` predicate — no OR
needed. `"NY + International + France"` becomes `overlaps? ["New York","country:France"]`.
Empty token list falls back to a `__NO_STATE_MATCH__` sentinel, preserving the old `[-1]` guard.

**Maintenance:** table trigger **5** `flt_states_sync_on_vendor_write` (insert+update) — table 11
had *no* trigger before. Write gate compares computed vs stored and returns early, so its own write
re-fires it exactly once and no-ops; ep 150's per-view click increment costs one lambda, no write.
**Xano table triggers are ASYNCHRONOUS** — a read in the same request still sees the old value;
it settled in under 4s.

**Deliberately NOT done:** `cache_v` not bumped (results byte-identical, a bump = 2h cold window for
nothing). `State_Input` (single, **defaults to `"New York"`** — omitting it silently filters to NY!)
left on its substring path; WeWeb always sends it empty.

## Measurements that redirected the work — don't redo these

- **Cost is independent of page size.** Returning 1 row costs the same as 48 (Venue 2.82s vs 2.95s).
  So the enrichment loop and payload/output trimming are **near-zero wins** — I had a whole plan
  built on trimming them and the measurement killed it. Cost tracks *matched* rows, not returned.
- **`overlaps?` on a list column is ~1.0s and flat** in both match count and number of values
  (`flt_venue_types`: 1,946 rows 0.97s / 4,463 rows 0.94s), vs `State includes?` substring at
  ~2.9s even when only 127 rows match. That is why this denorm works — no GIN needed.
- **The per-state loop was NOT the expensive part** — it was *faster* than the single-predicate
  path (VT: loop 1.22s vs `State includes?` 4.63s). The substring scan was the cost.
- Warm-ups can never fix ep 119: the cache keys on the resolved input tuple (category × states ×
  5 filter lists × sort × page × page_size) — tens of thousands of keys, task 8 warms 23, live hit
  rate 49%. See [[reference_xano_cache_resolved_inputs]].

## Follow-up measurements, same day — two open items resolved

**`totals: true` IS the unfiltered search, measured not guessed.** Two throwaway probes running ep
119's exact core predicate (`Category ==? && Validated_Data == 1`), page 1, 12 rows, cache-busted,
6 reps each:

| | p50 |
|---|---|
| WITH `totals: true` | **1.975s** |
| WITHOUT totals | **0.193s** |

The count costs **+1.78s — 10× the entire rest of the query**. So ~90% of the "slow when
unfiltered" complaint is counting 10,738 matching rows on every single request. Everything else
about ep 119 is already fast. Removing it needs a **WeWeb change first**: `filtered_total` feeds the
"N vendors" label (`6068088b`) which sits outside the paginator and therefore renders on every page,
so the count can only become page-1-only if the client carries it across pages. User had earlier
said approximate counts are acceptable, which keeps a capped/estimated count on the table too.

**ep 121 is NOT slow — that item is closed.** Live traffic (50 calls): cold **p50 0.10s, p95 0.56s,
max 1.06s**, and `enrich` is never sent. Real bboxes run 0.01–43.9 sq deg; the largest returned in
**0.06s**. The "5–8s wide bbox" figure I carried around all session came from a synthetic
1,534 sq-deg box — **799× the live median**, a shape no client sends. The `flt_glance` trim on ep 121
(it selects the blob for up to 2,500 rows then `unset`s it on the light path) is real waste but buys
nothing user-visible; not worth touching a working hot endpoint. `flt_states` does **not** apply to
ep 121 at all — it is bbox-based and has no state input.

## There is NO cheap exact count — three shortcuts tested and all dead (2026-08-10)

Probes on the same Venue predicate (10,738 matching rows), all deleted afterwards:

| approach | p50 | verdict |
|---|---|---|
| exact, `totals: true` | 1.92s | baseline |
| **`return = {type: "count"}`** (native) | **1.83s** | no better — counting really does cost this |
| `Category ==` literal instead of `==?` | 1.97s | the optional operator is NOT defeating the index |
| capped: fetch 500 ids, count them | 0.80s | only 1.1s saved, and the count becomes "500+" |
| capped at 1,000 | 1.11s | |
| capped at 2,000 | **3.17s** | **SLOWER than the exact count** |
| capped at 5,000 | **12.5s** | far worse |

**The capped-count idea backfires** — past ~1,000 rows the cost of materialising and serialising
the rows exceeds the cost of counting them. Same failure mode as the v20 `per_page=10000` bug.
Do not revisit "just cap the count" without re-reading this table.

So the count is a hard ~1.9s floor. The only way to make page 1 *feel* fast while keeping an exact
count is to **fetch the count in parallel** with the grid (separate `/wptp_search_count` endpoint,
non-blocking, UI fills the "N vendors" label when it lands) — rows render in ~0.2s. That needs
WeWeb work. Note "page-1-only totals" does **not** help: page 1 is where the cost lands.

**DECIDED 2026-08-10: the parallel-count fix is NOT being done.** User's call, and a sound one — a
1.9s win on the unfiltered-first-load path only, in exchange for a new endpoint, a duplicated
collection, a variable, two workflow edits and two rebinds in the layer that is hardest to test.
Don't re-propose it without new information (e.g. the count getting materially slower, or unfiltered
browse becoming a bigger share of traffic). The measurements above stand if it is ever revisited.

**Still genuinely open:** ep 52 `migrate_text_to_reference` is unauthenticated + unpaginated over
table 11 — a availability/security risk on its own merits, unrelated to search speed. Note an
unpaged read of table 11 reliably 502s (observed 2026-08-10).

Backups: `ep119_v21_pre_fltstates_2026-08-10.xs`, `ep119_v22_flt_states_2026-08-10.xs`,
`table11_pre_flt_states_2026-08-10.xs`. Builder: `scratchpad/build_ep119_v22.py`.
See [[reference_xanoscript_traps]] and [[reference_xano_mcp_quirks]] for the traps this hit.
