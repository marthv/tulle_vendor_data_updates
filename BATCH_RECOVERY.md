# Batch recovery — finishing an ingest that died partway

Written 2026-08-06 after the 3,174-PDF batch `msgbatch_01H898ryzjKiSh8T42YmdWxV`
stalled at 984 ingested rows, leaving 2,190 PDFs stuck in `batch_submitted`.

---

## 1. The failure mode

Batch extraction is two independent phases:

1. **Submit** — `run_extraction_batch` uploads every PDF to Anthropic's Batch API,
   gets one `msgbatch_…` id back, and immediately marks all of that batch's PDFs
   `batch_submitted` in `wptp_pdfs` (table 10).
2. **Ingest** — once Anthropic reports the batch `ended`, `process_batch_results`
   walks the results and writes each PDF's rows to Xano (tables 36/37), flipping
   its status to `extracted` / `partial` / `failed` / `skipped_non_venue`.

Anthropic finishing the batch does **not** write anything to Xano. Phase 2 is
ours, and if it dies — browser tab closed, Streamlit session reaped, Railway
redeploy, Xano outage — every PDF it hadn't reached yet stays `batch_submitted`
forever. The data is not lost: **Anthropic retains batch results for 29 days**
from creation.

So `extraction_status = 'batch_submitted'` older than a day means exactly one
thing: *the results exist upstream and nobody has ingested them.*

### Why it isn't self-healing

`batch_worker.py` was written to be a Railway cron that polls Anthropic and
auto-ingests ended batches. **As of 2026-08-06 that service was never created.**
The Railway project `tulle admin dash` has only:

| Service | Start command |
|---|---|
| `Tulle Admin Dash` | Streamlit dashboard |
| `extraction-batch` | `python launch_parallel.py` (the *non*-batch parallel path) |

Nothing runs `batch_worker.py`. Until that service exists, every batch needs a
human to ingest it. See §5.

---

## 2. Diagnose: what's actually stuck

Query table 10 grouped by `extraction_status`, then group the stuck rows by the
batch id — the submit path stashes it in `last_error` as `batch=msgbatch_…`.

```python
# needs the Xano meta API bearer token
import json, urllib.request, collections
BASE = 'https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta/workspace/1/table/10/content/search'
TOKEN = '<meta api bearer>'

rows, page = {}, 1
while True:
    body = {'page': page, 'per_page': 200, 'sort': {'id': 'desc'}}
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(),
                                 headers={'Authorization': TOKEN,
                                          'Content-Type': 'application/json'})
    d = json.load(urllib.request.urlopen(req, timeout=180))
    for x in d['items']:
        rows[x['id']] = (x.get('extraction_status'), (x.get('last_error') or '')[:45])
    if not d.get('nextPage'):
        break
    page += 1

stuck = {i: v for i, v in rows.items() if v[0] == 'batch_submitted'}
print(len(stuck), 'stuck')
print(collections.Counter(v[1] for v in stuck.values()))   # → which batch(es)
```

Note `per_page` above 200 returns HTTP 400 on this endpoint.

State at the time of writing:

| Batch | Submitted | Stuck rows | Results expire |
|---|---|---|---|
| `msgbatch_01H898ryzjKiSh8T42YmdWxV` | 2026-08-05/06 | 2,190 (`wptp_pdfs.id` 11969–14226) | ~2026-09-04 |
| `msgbatch_01FztfNKpzpebAYMu9X854WC` | 2026-07-31 | 517 | **~2026-08-29** |

---

## 3. Ingest the remainder

### The duplicate-row trap (read before re-running anything)

`process_batch_results` has **no per-row "already written" guard** — it posts
every PDF in the map it's handed. And table 36 **appends venue rows with no
dedupe**. Only Photography/Entertainment rows are idempotent, via
`_purge_package_rows` (delete-then-rewrite, `extract_core.py:1793`).

So a naive full re-ingest of a half-finished batch duplicates a venue row for
every PDF that already landed — 984 of them, in the case above. Queries survive
this (table 36 is deduped at read time by `PDF_ID` + latest `last_extracted_at`),
but it dirties the table and inflates counts.

**Fix, shipped 2026-08-06:** `ingest_batch_by_id(..., only_pending=True)` is now
the default. It joins the batch's result `custom_id`s to `wptp_pdfs` and keeps
only rows still in `batch_submitted`, so recovery is naturally resumable and can
be re-run as many times as needed. `only_pending=False` (CLI `--all`, or the
dashboard checkbox) restores the old force-everything behaviour.

### Option A — headless on Railway (recommended for large batches)

2,190 PDFs is a long ingest. Do **not** run it in a browser tab; a Streamlit
session dying mid-run is what caused this in the first place.

In Railway project `tulle admin dash`, create a service from
`marthv/tulle_vendor_data_updates` @ `main`:

- **Start command:** `python ingest_batch.py msgbatch_01H898ryzjKiSh8T42YmdWxV`
- **Restart policy:** `NEVER` (one-shot; it should not loop)
- **Env vars:** `ANTHROPIC_API_KEY` plus `XANO_GET_ENDPOINT`,
  `XANO_PATCH_PDF_ENDPOINT`, `XANO_PRICING_ENDPOINT`, `XANO_SUMMARY_ENDPOINT`,
  `XANO_VENUE_CATEGORIES_ENDPOINT`. No Google Drive creds needed — ingest only
  reads Anthropic and writes Xano.

> **The key must be the one that submitted the batch.** Message Batches are
> scoped to the API key's workspace: a different key reports the batch as *not
> found*. The batch above was submitted from the dashboard, so use **`Tulle Admin
> Dash`'s** `ANTHROPIC_API_KEY` — `extraction-batch` carries a separately-scoped
> key (commit `68a60e9`).

Watch that service's logs; re-run it if it dies — the `only_pending` filter makes
that safe.

### Option B — the dashboard

Pipeline tab → **🔧 Ingest a batch by ID (recovery)** → paste the batch id →
**⤓ Ingest this batch ID**. Leave the *"Force re-ingest"* checkbox unticked.

Fine for a few hundred PDFs. For thousands, use Option A.

### Option C — locally

```bash
export ANTHROPIC_API_KEY=<the submitting key>
export XANO_GET_ENDPOINT=... XANO_PATCH_PDF_ENDPOINT=... \
       XANO_PRICING_ENDPOINT=... XANO_SUMMARY_ENDPOINT=... \
       XANO_VENUE_CATEGORIES_ENDPOINT=...
python ingest_batch.py msgbatch_01H898ryzjKiSh8T42YmdWxV
```

Add `--wait 600` to poll for a batch that hasn't ended yet. Add `--all` only to
deliberately force a full re-ingest, accepting duplicate venue rows.

---

## 4. Verify

Re-run the §2 query. The batch's rows should be gone from `batch_submitted`,
redistributed into `extracted` / `partial` / `failed` / `skipped_non_venue`.
Anything still stuck means the ingest died again — just re-run it.

A residue of `failed` rows is normal (dead Drive links, oversized PDFs). Those
are a *re-extraction* problem, not an ingest problem: use the dashboard's
re-run-failed mode, which sweeps `RERUNNABLE_STATUSES` (`extract_core.py:74`).

---

## 5. Stop this recurring

Create the auto-ingest cron that `batch_worker.py`'s docstring already specifies:

- New service in `tulle admin dash`, same repo/branch
- **Start command:** `python batch_worker.py`
- **Cron schedule:** `*/15 * * * *`
- Same env vars as Option A, using the **dashboard's** `ANTHROPIC_API_KEY`

It is idempotent by design: `_batch_already_ingested` samples a few of each
batch's PDFs and skips the batch if they've left `batch_submitted`, and it fails
*closed* — if Xano reads fail it skips and retries next cycle rather than risking
a duplicate ingest.

Caveat worth knowing: that guard is **all-or-nothing per batch**. It samples 3
PDFs, so on a *partially* ingested batch it may sample 3 already-written rows and
skip the whole thing, leaving the remainder stuck. The cron prevents the common
case (nobody ever ingested); a partial failure like 2026-08-06 still needs the
manual §3 path.

---

## 6. Open item, unrelated to ingest

As of 2026-08-06 `extract_core.py` carries **uncommitted** local changes: the
2026-08-02 `Timing_List` work (`_TIMING_PACKAGE_TYPES`, the `timing_list` field
in `_build_package_entries`, the `Timing_List` write in `_post_packages`,
`_hours_band` indexed off `PHOTO_HOUR_BANDS`).

Railway deploys from `main`, so **production has never written `Timing_List`**,
even though the Xano side (ep119 v16, table 36 column) shipped 2026-08-02. Any
P/E rows ingested before that lands will have an empty `Timing_List`.

These changes sit in the same file as the ingest fix — commit them deliberately,
as separate commits, not with a blanket `git add extract_core.py`.
