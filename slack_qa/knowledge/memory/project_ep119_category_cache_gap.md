---
name: project_ep119_category_cache_gap
description: "Switching Discovery category is slow because task 8 warms no non-Venue category key; ep119 hit 68ms vs miss 1.34s. Measured 2026-09-13."
metadata:
  type: project
---

Measured 2026-09-13 over 24h of ep119 app traffic (786 successful calls, scoped to
`Referer=tulletogether.app`). `X-Query-Cache` in the response headers is the hit/miss flag —
that is the cheapest way to measure this and it is not mentioned anywhere else in these notes.

## The cliff

| | n | p50 | p90 | p95 | max |
|---|---|---|---|---|---|
| cache **hit** | 404 | **0.068s** | 0.251 | 0.298 | 0.390 |
| cache **miss** | 382 | **1.344s** | 2.840 | 4.297 | **8.525** |

**20x.** ep119 is either instant or it is seconds; there is no middle. Overall hit rate 51%.
(ep121 map: miss p50 0.137 but max **13.18s**. ep258 type-ahead is 718/721 hits, a non-issue.)

## Why switching CATEGORY specifically is slow

Hit rate by `Category_Input`:

| Category | hit | miss | hit% |
|---|---|---|---|
| Venue | 354 | 356 | 50% |
| (none) | 47 | 15 | 76% |
| Beauty | 1 | 5 | **17%** |
| Flowers | 1 | 4 | **20%** |
| Food and Beverage | 1 | 1 | 50% |
| Entertainment | 0 | 1 | **0%** (that one call: **5.83s**) |

**Task 8's 9 warm buckets contain no non-Venue category.** Its own header explains why it was
narrowed to 9 (only keys 2+ visitors request are worth warming) — correct reasoning, but it was
derived from a day whose traffic was overwhelmingly Venue, so the low-volume categories were
filtered out by the very rule that makes the list efficient. They are still the ones a browsing
user *feels*, because a category switch is a deliberate click that then stalls.

**The bare category-switch key is deterministic and therefore warmable**, unlike the long tail:
it is bucket 2's URL with `Category_Input` swapped. That is a handful of extra buckets, no ep119
change, no auth change. This is the cheap fix.

Long tail for contrast: **400 distinct keys in 24h, only 31 requested by more than one device.**
369 are unwarmable in principle. Warming cannot move p95 — only a faster uncached path can.

Note `Photography` alone is **2,083 vendors** in table 11, so these categories are not niche data,
just niche *traffic* on the day sampled.

Related: [[project_image_delivery_cost]] (the other half of the felt latency),
[[reference_xano_cache_resolved_inputs]], [[project_location_whitelist_ep119]],
[[project_perf_baseline_digest]].

## SHIPPED 2026-09-13 — task 8 now has 15 buckets

Buckets 10-15 added: one bare default grid (`page_size=12`, every other param default) per
non-Venue category — **Photography, Entertainment, Flowers, Beauty, Food and Beverage,
Wedding Planner**. `$total` 9 -> 15. No ep119 change, no auth change, no frontend change.

- Backup of the live pre-change script: `.claude/xano_backups/task8_pre_category_buckets_2026-09-13.xs`
- Deployed script: `.claude/xano_backups/task8_v_category_buckets_2026-09-13.xs`

**The seven category values are settled**, from ep119's own whitelist `COLS` map plus table 61's
columns: `Venue, Photography, Entertainment, Flowers, Beauty, Food and Beverage, Wedding Planner`.
`COLS` also accepts the fragmented duplicates `Catering` (-> F&B) and `Wedding Planning`
(-> Wedding Planner) — **they resolve identically but are DIFFERENT CACHE KEYS**, so if the
picker emits "Wedding Planning" that bucket warms nothing. Four of the six were copied from real
requests; Photography and Wedding Planner are inferred and are the ones to re-check.

## `.claude/xano_push.py` NOW SUPPORTS `kind=task`

`python .claude/xano_push.py task 8 <file>` (also `--dry-run`). It pulls the live script, backs it
up, diffs, pushes, reads back and compares.

**Expect a "MISMATCH" on any task push and do not panic.** Xano re-emits the leading comment
block with one extra space of indentation (`//   ` -> `//    `), so the read-back differs on every
comment line. On this push that was **98 differing lines and 0 differing non-comment lines** —
check that number before treating a mismatch as a failure:

```python
diff = [(a,b) for a,b in zip(sent.split('\n'), live.split('\n')) if a != b]
noncomment = [(a,b) for a,b in diff if not a.strip().startswith('//')]
```

## Country_Input FRAGMENTS THE CACHE — open, not fixed here

`Country_Input` arrives as `""` on 728 of 846 app requests and as the literal string `"null"` on
92. ep119's sanitiser lambda maps `"null"` to blank, so **results are identical** — but the
response cache keys on the **resolved input, before the lambda runs**, so one logical query
occupies two cache entries and ~11% of traffic can never hit a warmed one. The buckets use the
empty spelling (matching bucket 2 and the ~88% majority). **The real fix is the WeWeb binding that
emits "null", not more buckets.**

## VERIFIED 19:30 UTC — AND THE PREMISE WAS PARTLY WRONG. READ THIS BEFORE REUSING THE NUMBERS ABOVE.

All 15 buckets ran 19:30:04 -> 19:30:16 (14.9s vs 14.0s for 9). All six new categories returned
**200**, so the inferred spellings were right — **`Photography` and `Wedding Planner` are CONFIRMED
correct**, the open question from the deploy is closed.

**But the six new buckets took 0.02-0.23s each, not the 1-3s the change assumed.** Uncached cost
of a bare category switch, measured at that run: Flowers 0.23s, Beauty 0.18s, Food and Beverage
0.17s, Wedding Planner 0.11s. (Photography and Entertainment came back `X-Query-Cache: 1` —
already warm from real traffic inside the 7100s TTL.)

**The error: a low hit rate was read as a high cost.** Those categories really do miss cache
80-100% of the time, but the miss behind it is ~0.2s because the category filter narrows to a
small result set. The **5.83s "Entertainment" call cited as the smoking gun carried
`states[]=Washington` + `State_Input=Washington`** — it was a state-filtered query, not a bare
category switch. Mis-attributed.

Bare default-grid requests (no state, no search, no filters), by category, over 24h:

| Category_Input | hits | misses | miss p50 | miss max |
|---|---|---|---|---|
| Venue | 53 | 12 | **2.127s** | 4.297s |
| (no category param) | 47 | 2 | **4.241s** | 4.241s |
| Beauty | 0 | 1 | 0.310s | 0.310s |

So the expensive bare grids are **Venue and the no-category default** — and those were already
buckets 1 and 2. Where ep119's miss time actually lives:

| shape | n | p50 | p95 | max | share of all miss time |
|---|---|---|---|---|---|
| similar-vendors seed | 183 | 1.421 | 3.238 | **8.525** | **46%** |
| state / location filter | 136 | 1.295 | **5.832** | 8.440 | **41%** |
| bare grid (mostly Venue) | 15 | 2.127 | 4.297 | 4.297 | 5% |
| advanced filter | 19 | 1.170 | 5.728 | 5.728 | 5% |
| text search | 29 | 0.257 | 0.993 | 1.061 | 2% |

**Honest value of buckets 10-15: a CLEANUP, not an improvement.** It turns a ~0.2s miss into a
~0.02s hit for the first visitor to each non-Venue category per 2h window. Keep it (it costs ~1s
of Xano compute per run and guarantees a warm first hit) but do not credit it with fixing
"switching categories is slow". **That felt delay is the images** — [[project_image_delivery_cost]]
— because the Xano half of a category switch is already ~0.2s.

**87% of remaining miss time is similar-vendors seeds and state/location filters**, and both are
long-tail keys no warmer can anticipate. Moving them means making the uncached path faster, which
is an ep119 change.
