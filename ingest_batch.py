#!/usr/bin/env python3
"""
ingest_batch.py — ingest one Batch API extraction result into Xano, by batch id.

Usage:
    python ingest_batch.py <batch_id> [--wait SECONDS] [--all]

    <batch_id>  the id printed by `run_chunk.py --batch` (or the dashboard)
    --wait N    poll up to N seconds for the batch to reach "ended" before
                ingesting (default 0 = ingest now, fail if still processing)
    --all       re-ingest EVERY PDF in the batch, including ones already written.
                Off by default: normally only PDFs still in 'batch_submitted' are
                ingested, so an interrupted ingest can be resumed without appending
                duplicate venue rows to table 36.

WHY THIS EXISTS
    Message Batches are scoped to the workspace of the API key that submitted them.
    The Railway `batch_worker.py` cron polls with the Railway project's key, so it
    can only see batches submitted by that key. A batch submitted locally with the
    `pdf_extractor` key is invisible to it and would sit un-ingested with its PDFs
    stuck in 'batch_submitted' status. Run this from the same shell (same
    ANTHROPIC_API_KEY) you submitted from.

    Ingest is idempotent — extract_core's guard skips a batch whose PDFs have
    already left 'batch_submitted', so re-running this never duplicates
    table-36/37 rows.

Env vars: ANTHROPIC_API_KEY plus the same XANO_* endpoints extraction uses.
"""
import os
import sys

from extract_core import ingest_batch_by_id


def main():
    argv = sys.argv[1:]
    positional = [a for a in argv if not a.startswith("--")]
    if not positional:
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(1)

    batch_id = positional[0]
    wait_secs = 0
    if "--wait" in argv:
        i = argv.index("--wait")
        if i + 1 < len(argv):
            wait_secs = int(argv[i + 1])
    only_pending = "--all" not in argv
    if not only_pending:
        print("--all: re-ingesting every PDF in the batch, including already-written "
              "ones. This APPENDS duplicate venue rows to table 36.", file=sys.stderr)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. It must be the SAME key that submitted "
              "the batch - batches are workspace-scoped.", file=sys.stderr)
        sys.exit(1)

    final = None
    for item in ingest_batch_by_id(batch_id, wait_secs=wait_secs, only_pending=only_pending):
        if isinstance(item, dict):
            final = item
        else:
            print(item, flush=True)

    print("-" * 48, flush=True)
    if not final:
        print("No summary returned — ingest did not complete.", flush=True)
        sys.exit(1)

    if final.get("error"):
        # The most common cause is a key from a different workspace than the
        # submitting one, which reads as "batch not found".
        print(f"INGEST FAILED - {final['error']}", flush=True)
        print("If this says the batch was not found, check that ANTHROPIC_API_KEY is "
              "the key that submitted it.", flush=True)
        sys.exit(1)

    print(
        f"INGESTED batch {batch_id} - "
        f"ok={final.get('ok')} partial={final.get('partial')} "
        f"failed={final.get('failed')} skipped={final.get('skipped')} "
        f"cost=${final.get('cost_usd', 0):.4f} (already batch-discounted)",
        flush=True,
    )


if __name__ == "__main__":
    main()
