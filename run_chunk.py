#!/usr/bin/env python3
"""
run_chunk.py — run ONE row-range chunk of the PDF extraction.

Usage:
    python run_chunk.py <start_row> <end_row> [--all]

    start_row : 0-based inclusive index into the Drive-linked PDF list
    end_row   : exclusive end index (use 0 or omit for "to the end")
    --all     : force re-extraction of every row, ignoring the already-extracted
                dedup (full refresh). Venue pre-filter still applies. Posts to
                tables 36/37 APPEND — clear 37 first, prune 36 after.

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
from extract_core import run_extraction


def main():
    args = [a for a in sys.argv[1:] if a != "--all"]
    force_all = "--all" in sys.argv
    if len(args) < 1:
        print("usage: python run_chunk.py <start_row> [end_row] [--all]")
        sys.exit(1)

    start_row = int(args[0])
    end_row = int(args[1]) if len(args) > 1 and int(args[1]) > 0 else None

    final = None
    for item in run_extraction(start_row=start_row, end_row=end_row, force_all=force_all):
        if isinstance(item, dict):
            final = item            # last yield is the summary dict
        else:
            print(item, flush=True)  # progress line

    if final:
        print("─" * 48, flush=True)
        print(
            f"CHUNK {start_row}:{end_row} DONE — "
            f"ok={final.get('ok')} partial={final.get('partial')} "
            f"skipped_non_venue={final.get('skipped_non_venue')} "
            f"failed={final.get('failed')} "
            f"cost=${final.get('cost_usd', 0):.2f}",
            flush=True,
        )


if __name__ == "__main__":
    main()
