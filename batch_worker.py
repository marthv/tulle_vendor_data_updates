#!/usr/bin/env python
"""
batch_worker.py — Railway cron worker for automatic batch ingestion.

Polls Anthropic for extraction batches that have ENDED and ingests their results
into Xano automatically — so batch results land WITHOUT anyone clicking
"Check Batch Results" in the dashboard. It is idempotent (a batch whose PDFs have
already left 'batch_submitted' status is skipped), so running it repeatedly never
duplicates rows.

Railway setup (one time):
  1. In the Railway project, create a NEW service from this same repo/branch.
  2. Settings → set the Start Command to:        python batch_worker.py
  3. Settings → Cron Schedule:                   */15 * * * *   (every 15 min)
  4. It inherits the project's env vars (ANTHROPIC_API_KEY, XANO_* endpoints) —
     no Google Drive creds needed; ingestion only reads Anthropic + writes Xano.

A cron service runs the command, then sleeps until the next scheduled time, so it
costs almost nothing between runs. Logs appear in that service's Railway logs.
"""
from datetime import datetime, timezone

from extract_core import poll_and_ingest_batches


def main():
    stamp = datetime.now(timezone.utc).isoformat()
    print(f"[batch_worker] {stamp} — polling Anthropic for ended batches…", flush=True)
    summary = poll_and_ingest_batches(log=lambda m: print(f"[batch_worker]   {m}", flush=True))
    print(
        f"[batch_worker] done — checked={summary['checked']} "
        f"ingested={summary['ingested']} still_processing={summary['still_processing']} "
        f"skipped={summary['skipped']} errors={summary['errors']}",
        flush=True,
    )
    for b in summary["batches"]:
        print(f"[batch_worker]   {b}", flush=True)


if __name__ == "__main__":
    main()
