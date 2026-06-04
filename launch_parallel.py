#!/usr/bin/env python3
"""
launch_parallel.py — fan the extraction out into N parallel chunks on ONE machine.

Usage:
    python launch_parallel.py <num_workers> [total_rows] [global_start] [--all]

    num_workers  : how many chunks to run at once (start with 8-12; raise only
                   if your Anthropic rate-limit tier has headroom)
    total_rows   : size of the Drive-linked PDF list (default 6781)
    global_start : first row to cover across all workers (default 0)
    --all        : force full re-extraction in every worker (ignores dedup).
                   Clear table 37 first, prune table 36 after — posts append.

Splits [global_start, total_rows) into num_workers contiguous ranges and
spawns one `run_chunk.py` subprocess per range. Each worker logs to
logs/chunk_<start>_<end>.log. Workers are independent and dedup against
the Extracted PDF Data table, so overlap or restarts never double-extract.

For Railway/cloud: skip this launcher and instead run ONE `run_chunk.py`
per instance with that instance's START_ROW/END_ROW — same effect, but the
platform supervises each worker.
"""
import os
import subprocess
import sys

PYTHON = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 2:
        print("usage: python launch_parallel.py <num_workers> [total_rows] [global_start]")
        sys.exit(1)

    force_all = "--all" in sys.argv
    pos = [a for a in sys.argv[1:] if a != "--all"]
    workers = int(pos[0])
    total = int(pos[1]) if len(pos) > 1 else 6781
    g_start = int(pos[2]) if len(pos) > 2 else 0

    span = total - g_start
    size = -(-span // workers)  # ceil division → even-ish chunks

    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    procs = []

    for w in range(workers):
        start = g_start + w * size
        end = min(start + size, total)
        if start >= end:
            break
        log_path = os.path.join(HERE, "logs", f"chunk_{start}_{end}.log")
        logf = open(log_path, "w", encoding="utf-8")
        print(f"▶ worker {w}: rows {start}:{end}  → {log_path}{'  [--all]' if force_all else ''}")
        cmd = [PYTHON, os.path.join(HERE, "run_chunk.py"), str(start), str(end)]
        if force_all:
            cmd.append("--all")
        p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, cwd=HERE)
        procs.append((p, logf, start, end))

    print(f"\n{len(procs)} workers launched. Tailing nothing — check logs/ for progress.")
    print("Waiting for all workers to finish...\n")

    for p, logf, start, end in procs:
        code = p.wait()
        logf.close()
        print(f"✓ worker rows {start}:{end} exited (code {code})")

    print("\nAll workers done.")


if __name__ == "__main__":
    main()
