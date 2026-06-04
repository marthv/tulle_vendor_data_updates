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
import time

PYTHON = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))

# Each worker fetches the full PDF list from Xano at startup. Launching all
# workers at once makes Xano 502 under the concurrent read load, so we space the
# startups out. Override with WORKER_STAGGER_SECONDS=0 to disable.
STAGGER_SECONDS = float(os.environ.get("WORKER_STAGGER_SECONDS", "12"))


def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def main():
    force_all = "--all" in sys.argv or _env_flag("FORCE_ALL")
    pos = [a for a in sys.argv[1:] if a != "--all"]

    if pos:
        # CLI mode: workers [total] [global_start]
        workers = int(pos[0])
        total   = int(pos[1]) if len(pos) > 1 else 6781
        g_start = int(pos[2]) if len(pos) > 2 else 0
    else:
        # Env-driven batch mode (Railway): process one window
        # [BATCH_START, BATCH_START + BATCH_SIZE). Bump BATCH_START between
        # batches and redeploy — the start command never changes.
        workers = int(os.environ.get("BATCH_WORKERS", "8"))
        g_start = int(os.environ.get("BATCH_START", "0"))
        size    = int(os.environ.get("BATCH_SIZE", "500"))
        total   = g_start + size
        print(f"[batch mode] workers={workers} rows=[{g_start},{total}) "
              f"(size {size}) force_all={force_all}", flush=True)

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
        # Space out startups so their PDF-list fetches don't all hit Xano at once.
        if STAGGER_SECONDS > 0 and w < workers - 1:
            time.sleep(STAGGER_SECONDS)

    print(f"\n{len(procs)} workers launched (staggered {STAGGER_SECONDS}s). Check logs/ for progress.")
    print("Waiting for all workers to finish...\n")

    for p, logf, start, end in procs:
        code = p.wait()
        logf.close()
        print(f"✓ worker rows {start}:{end} exited (code {code})")

    print("\nAll workers done.")

    # ── Aggregate batch result to stdout (Railway captures this in its log view;
    #    per-worker detail lives in logs/chunk_*.log inside the container). ──
    import glob, re
    tot = {"ok": 0, "partial": 0, "failed": 0, "cost": 0.0}
    dead = 0
    for f in glob.glob(os.path.join(HERE, "logs", "chunk_*.log")):
        txt = open(f, encoding="utf-8", errors="replace").read()
        if "Failed to fetch from Xano" in txt:
            dead += 1
        m = re.search(r"DONE — ok=(\d+) partial=(\d+) \S+ failed=(\d+) cost=\$([\d.]+)", txt)
        if m:
            tot["ok"]     += int(m.group(1))
            tot["partial"] += int(m.group(2))
            tot["failed"]  += int(m.group(3))
            tot["cost"]    += float(m.group(4))
    flag = f"  !! {dead} workers DEAD-FETCH (502)" if dead else ""
    print(f"BATCH SUMMARY rows=[{g_start},{total}) workers={len(procs)} — "
          f"ok={tot['ok']} partial={tot['partial']} failed={tot['failed']} "
          f"cost=${tot['cost']:.2f}{flag}", flush=True)


if __name__ == "__main__":
    main()
