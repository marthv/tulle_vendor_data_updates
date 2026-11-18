---
name: project-place-id-backfill
description: "Backfilling Place_IDs for the ~4,000 vendor rows that never had one. The matcher must be strict equal-or-prefix + state; every looser rule accepted a demonstrably wrong business."
metadata:
  node_type: memory
  type: project
  originSessionId: 48f611b8-9c31-47c9-a494-e7c30f6efa52
  modified: 2026-09-11T09:20:00.000Z
---

**The gap:** ~4,000 of 16,481 rows in WPTP Updated Mappings have an empty `Place_ID`. The whole
Google chain (`google_data_batch` -> images 1/2/3) starts from Place_ID, so those rows could
never get cached data and therefore could never get a photo. Nothing in the pipeline ever
looked one up — they only existed when whoever submitted the vendor happened to include one.
See [[project_google_data_batch_401]] for the other half of why images were empty.

**Built 2026-09-11:**
- `ep300 POST admin/google/backfill_place_ids` — Google Find Place From Text on Name + Address
  + State. `dry_run: true` returns counts and the exact rows it would attempt, with **no Google
  call**, so a range can be sized before spending.
- `ep301 POST admin/google/clear_place_ids` — the undo. Takes explicit ids only, never a range.
  Also clears `google_data_cache` + `google_data_last_fetched`, because data cached from a wrong
  Place_ID is another business's.
- `ep302 POST admin/google/verify_cached_names` — compares `google_data_cache.name` to our
  `Name` after the fetch. Report-only unless `fix: true`. **This is the only step that checks
  identity against real Google data**, and it covers Place_IDs nobody ever verified, not just
  backfilled ones.

**THE MATCHER IS THE WHOLE PROBLEM. Four versions, each let something wrong through:**

| version | rule | what it wrongly accepted |
|---|---|---|
| v4 | token overlap >= 0.5 | "1st Choice Customized DJ" (NC) -> a DJ in Watsontown, **PA** |
| v5 | + state gate | "614 Mobile Entertainment" -> "Matt Ryan Mobile DJ Entertainment" (matched only *mobile*, *entertainment*) |
| v6 | + non-generic token | "A'lee" (AZ) -> **"Lee Lee International Supermarket"**, scoring 1.0 on its single token "lee" |
| v7 | **equal or whole-word prefix**, non-generic, >=4 chars, state | (current) |

**Fractional name similarity cannot express "same business" — don't reintroduce scoring.** These
rows are mostly DJs/photographers/florists whose names are `<something> Entertainment|Events|
Photography`, so overlap on trade words is pure noise. Accept only equal-or-prefix.

**Two XanoScript traps this cost, both silent:**
- **Regexes BUILT FROM STRINGS die inside a triple-quoted lambda.** `new RegExp("\b"+ab+"\b")`
  collapsed to a literal backslash-b and matched nothing, so the state gate rejected 30/30 rows
  including exact hits. Regex **literals** (`/\s+/g`) survive fine. Use a zip-anchored token test
  instead: in a US `formatted_address` the state abbreviation is the token before the 5-digit zip
  (also avoids `\bla\b` matching "La Brea Ave").
- **A PAGED `db.query` needs `items.` on every projected column** or `$todo.items` is empty and
  the foreach silently does nothing while still reporting success. See [[reference_xanoscript_traps]].

**Driver gotcha:** the endpoint always reads the FIRST N rows lacking a Place_ID, so re-calling
it with the same range re-attempts the same rejects and burns money — yield fell 50 -> 17 -> 5 ->
2 -> 1 per 200-row batch. Drive it with a **forward cursor**: take the id list from a `dry_run`,
then call per id-chunk with `starting_index`/`ending_index` set to that chunk.

**Expect a low fill rate and that's correct.** Roughly a third of these vendors have no Google
Places listing at all ("293 Band", "462 Entertainment", "A Blooming Affair" return zero
candidates) — solo operators with no storefront. They can never have a photo, and no amount of
matcher tuning changes that. An unfilled Place_ID costs a photo; a wrong one puts a stranger's
storefront and star rating on a live vendor card with nothing downstream to catch it.

**Order of operations:** backfill -> `google_data_batch` -> **verify_cached_names** -> clear
mismatches -> images 1/2/3. Never pull images before the verify step.

## RESULT of the first full run — 2026-09-11, ids 14697-16496 (1,800 rows)

| | before | after |
|---|---|---|
| has Place_ID | 378 | **682** |
| real Google data | ~42 | **346** |
| image_1 / 2 / 3 (table-wide) | 11,424 / 11,155 / 10,924 | **11,705 / 11,325 / 11,196** |

**304 Place_IDs written, 0 disagreed with Google's own business name afterwards** — the strict
equal-or-prefix + state matcher held across the whole range. All 57 verification failures were
PRE-EXISTING rows, none from the backfill; 41 were cosmetic (`&` vs `and`). Only two look
genuinely wrong and both predate this work, left untouched deliberately rather than auto-cleared:
**15055** "Christie Turner Floral Design" -> Google "Austin School of Flower Design" (already has
an image, so a wrong photo may be live) and **15590**, whose `Name` is literally
`https://rsmail.com`.

**1,118 rows in the range still have no Place_ID and that is the correct answer.** Google returns
ZERO candidates for them - solo DJs, mobile florists, home-based photographers with no Places
listing. More matcher tuning cannot help; covering them needs a different source (website OG
image, Instagram). Image counts rose less than rows processed because many listings carry fewer
than 3 photos, so slots 2/3 had nothing to fetch.

13 Google fetches failed transiently and stayed retryable by design - re-running
`google_data_batch` on the range picks them up for free.

**Not wired into the dashboard UI** - eps 300/301/302 are curl-only so far.
