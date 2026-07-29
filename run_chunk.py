#!/usr/bin/env python3
"""
run_chunk.py — run ONE row-range chunk of the PDF extraction.

Usage:
    python run_chunk.py <start_row> <end_row> [--all]
    python run_chunk.py --ids "P1,P2,..." [--batch]

    start_row : 0-based inclusive index into the Drive-linked PDF list
    end_row   : exclusive end index (use 0 or omit for "to the end")
    --all     : force re-extraction of every row, ignoring the already-extracted
                dedup (full refresh). Venue pre-filter still applies. Posts to
                tables 36/37 APPEND — clear 37 first, prune 36 after.
    --batch   : submit via the Anthropic Batch API instead of extracting inline.
                Bills at 50% of the sequential rate. --ids MODE ONLY (see below).

WHY --batch IS --ids ONLY
    run_extraction() treats start_row/end_row as 0-based POSITIONS in the fetched
    list; run_extraction_batch() treats them as inclusive Xano `id` bounds
    (_select_by_id_range). The same two numbers therefore select DIFFERENT PDFs on
    the two paths, so routing a range run to batch would silently extract the wrong
    set. In --ids mode both paths take the same explicit PDF_ID list, so the switch
    is safe. For batched range runs, use the dashboard (which passes id bounds).

BATCH RESULTS ARE NOT IMMEDIATE
    --batch submits and exits; nothing is in Xano yet. Message Batches are scoped to
    the workspace of the key that submitted them, so ingest with THAT key:
        python ingest_batch.py <batch_id> --wait 60
    The Railway batch_worker cron only sees batches submitted by its own key.

Each chunk is fully independent and idempotent: it fetches the full PDF
list, then processes only rows [start_row:end_row], skipping any PDF_ID
already present in the Extracted PDF Data table (dedup). Safe to run many
of these in parallel — they share nothing but the Xano tables and the
Anthropic rate limit.

Env vars required (same as the dashboard):
    ANTHROPIC_API_KEY, XANO_GET_ENDPOINT, XANO_SUMMARY_ENDPOINT,
    XANO_PRICING_ENDPOINT, XANO_PATCH_PDF_ENDPOINT, plus Google Drive creds.
"""
import sys
from extract_core import run_extraction, run_extraction_batch


def _drain(gen):
    """Print progress lines, return the final summary dict (last dict yielded)."""
    final = None
    for item in gen:
        if isinstance(item, dict):
            final = item
        else:
            print(item, flush=True)
    return final


def _run_batch(pdf_ids):
    """Submit ONE Batch API job for an explicit PDF_ID set. Returns an exit code."""
    final = _drain(run_extraction_batch(start_row=0, end_row=None, pdf_ids=pdf_ids))
    print("-" * 48, flush=True)
    if final and final.get("batch_submitted"):
        batch_id = final.get("batch_id")
        print(f"BATCH SUBMITTED - id={batch_id} pdfs={final.get('pdf_count')}", flush=True)
        print("Nothing is in Xano yet. Ingest with the SAME key that submitted:", flush=True)
        print(f"    python ingest_batch.py {batch_id} --wait 60", flush=True)
        return 0
    print(f"BATCH SUBMIT FAILED - {(final or {}).get('error', 'unknown')}", flush=True)
    return 1


def main():
    argv = sys.argv[1:]
    force_all = "--all" in argv
    use_batch = "--batch" in argv

    # Targeted mode: --ids "P1,P2,..." runs exactly those PDF_IDs (bypasses dedup,
    # ignores start/end). Used to re-run a specific failed set in parallel.
    pdf_ids = None
    if "--ids" in argv:
        idx = argv.index("--ids")
        if idx + 1 < len(argv):
            pdf_ids = [s.strip() for s in argv[idx + 1].replace("\n", ",").split(",") if s.strip()]

    if pdf_ids:
        if use_batch:
            sys.exit(_run_batch(pdf_ids))
        label = f"IDS[{len(pdf_ids)}]"
        gen = run_extraction(start_row=0, end_row=None, pdf_ids=pdf_ids)
    else:
        if use_batch:
            # Refuse rather than silently extract a different set — see the module
            # docstring: run_extraction slices by POSITION, run_extraction_batch
            # selects by inclusive Xano `id`.
            print("--batch is only supported with --ids.\n"
                  "  Range args mean different things on the two paths: run_extraction\n"
                  "  slices by 0-based POSITION, run_extraction_batch selects by inclusive\n"
                  "  Xano `id`. Batching a range here would extract the wrong PDFs.\n"
                  "  Use --ids \"P1,P2,...\" --batch, or run the range from the dashboard.",
                  file=sys.stderr)
            sys.exit(2)
        pos = [a for a in argv if not a.startswith("--")]
        if not pos:
            print("usage: python run_chunk.py <start_row> [end_row] [--all]  |  "
                  "--ids \"P1,P2,...\" [--batch]")
            sys.exit(1)
        start_row = int(pos[0])
        end_row = int(pos[1]) if len(pos) > 1 and int(pos[1]) > 0 else None
        label = f"{start_row}:{end_row}"
        gen = run_extraction(start_row=start_row, end_row=end_row, force_all=force_all)

    final = _drain(gen)

    if final:
        print("─" * 48, flush=True)
        print(
            f"CHUNK {label} DONE — "
            f"ok={final.get('ok')} partial={final.get('partial')} "
            f"skipped_non_venue={final.get('skipped_non_venue')} "
            f"failed={final.get('failed')} "
            f"cost=${final.get('cost_usd', 0):.2f}",
            flush=True,
        )


if __name__ == "__main__":
    main()
