#!/usr/bin/env python3
"""
ingest_batch.py — ingest one Batch API extraction result into Xano, by batch id.

Usage:
    python ingest_batch.py <batch_id> [--wait SECONDS] [--all] [--strict]

    <batch_id>  the id printed by `run_chunk.py --batch` (or the dashboard)
    --wait N    poll up to N seconds for the batch to reach "ended" before
                ingesting (default 0 = ingest now, fail if still processing)
    --all       re-ingest EVERY PDF in the batch, including ones already written.
                Off by default: normally only PDFs still in 'batch_submitted' are
                ingested, so an interrupted ingest can be resumed without appending
                duplicate venue rows to table 36.
    --strict    exit non-zero even when there is simply nothing to ingest. For a
                human running a recovery who wants "the batch is gone" to be a
                failure. The Railway service must NOT use this.

EXIT CODES - why this matters on Railway
    This runs as the one-shot `batch-ingest` service in the "tulle admin dash"
    project (railway.ingest.toml, restartPolicyType = NEVER). Railway re-runs it on
    EVERY deploy of the repo, including deploys that have nothing to do with
    extraction. So "there is no batch to ingest right now" is the normal steady
    state, and it must not be reported as a crash.

      0  ingested, or there was nothing to ingest (no batch id; batch still
         processing; batch cancelled or past Anthropic's result-retention window)
      1  something is genuinely wrong and a human should look: the batch was not
         found (wrong API key / wrong workspace), Xano was unreachable, or the
         ingest started and did not finish

    Before 2026-09-09 every one of those exited 1, so the service sat permanently
    CRASHED in Railway after any unrelated deploy. Note that clearing
    INGEST_BATCH_ID did not help either: an empty $INGEST_BATCH_ID arrives as no
    argument, which also exited 1.

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
    strict = "--strict" in argv
    if not positional:
        # Railway starts this as `python ingest_batch.py $INGEST_BATCH_ID`, so an
        # unset INGEST_BATCH_ID arrives as no argument at all. That is the parked
        # state for the service, not an error.
        print(__doc__.strip(), file=sys.stderr)
        print("\nNo batch id given (INGEST_BATCH_ID is unset) - nothing to ingest.",
              file=sys.stderr)
        sys.exit(1 if strict else 0)

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
        print("No summary returned - ingest did not complete.", flush=True)
        sys.exit(1)

    if final.get("error"):
        err = str(final["error"])
        low = err.lower()

        # A batch the key cannot see. Message Batches are workspace-scoped, so this
        # is nearly always the wrong ANTHROPIC_API_KEY - a real misconfiguration,
        # and worth going red for.
        if "not_found" in low or "not found" in low or "404" in low:
            print(f"INGEST FAILED - {err}", flush=True)
            print("The batch was not found. ANTHROPIC_API_KEY is probably not the key "
                  "that SUBMITTED the batch - Message Batches are workspace-scoped.",
                  flush=True)
            sys.exit(1)

        # Nothing to ingest, and re-running will never change that: the batch is
        # still processing, was cancelled, or its results have aged out of
        # Anthropic's retention window. Terminal, but not a fault.
        nothing_to_do = ("results_url" in low
                         or "finished processing" in low
                         or low == "no_pdf_ids")
        if nothing_to_do:
            print(f"NOTHING TO INGEST - {err}", flush=True)
            print("The batch has no results to read: it is still processing, or it was "
                  "cancelled, or its results have passed Anthropic's retention window.",
                  flush=True)
            print("Re-running will not change this. Clear INGEST_BATCH_ID on the "
                  "batch-ingest service to park it until the next recovery.", flush=True)
            sys.exit(1 if strict else 0)

        # Anything else - Xano unreachable, network, an unexpected SDK error.
        print(f"INGEST FAILED - {err}", flush=True)
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
