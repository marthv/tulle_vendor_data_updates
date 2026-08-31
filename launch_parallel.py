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


def _require_explicit_confirmation():
    """Refuse to spend money unless this run was actually asked for.

    This launcher starts a PAID extraction the moment it runs, and on Railway it
    runs on every deploy of the `extraction-batch` service. There is no inert
    configuration: with no env vars at all it still defaults to BATCH_START=0 /
    BATCH_SIZE=500 / BATCH_WORKERS=8 and extracts the first 500 rows. So "clearing
    the env vars to make it safe" makes it MORE dangerous, not less.

    On 2026-08-06 an unrelated docs-only commit auto-deployed this service and it
    began submitting a batch off leftover BATCH_*/FORCE_ALL vars from a previous
    run. It aborted only because Xano happened to be down that minute — luck, not a
    safeguard. Railway watch patterns help, but they do not survive someone clearing
    them and do not block a manual redeploy.

    Hence a deliberate, code-level interlock: set CONFIRM_EXTRACTION_RUN=1 (or pass
    --confirm) when you actually intend to spend. Anything else exits 0 without
    touching Anthropic. Exit 0, not 1, so a no-op deploy is not reported as a crash.
    """
    if _env_flag("CONFIRM_EXTRACTION_RUN") or "--confirm" in sys.argv:
        return
    print(
        "REFUSING TO RUN — this launcher starts a paid extraction and was not "
        "explicitly confirmed.\n"
        "  Nothing was uploaded and no credits were spent.\n"
        "  To run deliberately: set CONFIRM_EXTRACTION_RUN=1 on the service (or pass "
        "--confirm locally),\n"
        "  check BATCH_START / BATCH_SIZE / BATCH_PDF_IDS / FORCE_ALL first, and "
        "UNSET it again afterwards\n"
        "  so the next deploy cannot re-fire the same run.",
        flush=True,
    )
    sys.exit(0)


def _prefetch_shared_state():
    """One set of Xano scans for the whole launch. Workers read the disk cache
    instead of each full-scanning wptp_pdfs + summary + venue categories at
    startup (N workers x 3 tables was the concurrent-read 502 trigger)."""
    try:
        from extract_core import prefetch_shared_xano_state
        print("Prefetching shared Xano state (one scan for all workers)...", flush=True)
        print("✓ " + prefetch_shared_xano_state(), flush=True)
    except Exception as e:
        print(f"⚠ prefetch failed ({e}) — workers will fetch individually (staggered)", flush=True)


def main():
    # Before ANY Xano scan or Anthropic call — an unconfirmed deploy must be a no-op,
    # not a cheaper version of the same accident.
    _require_explicit_confirmation()
    _prefetch_shared_state()
    # ── Targeted IDs mode: rerun an explicit PDF_ID set in parallel. ──
    # Source: --ids-file <path> (CLI) or BATCH_PDF_IDS env (comma/newline list).
    ids_src = ""
    if "--ids-file" in sys.argv:
        i = sys.argv.index("--ids-file")
        if i + 1 < len(sys.argv):
            ids_src = open(sys.argv[i + 1], encoding="utf-8").read()
    if not ids_src:
        ids_src = os.environ.get("BATCH_PDF_IDS", "")
    ids = [s.strip() for s in ids_src.replace("\n", ",").split(",") if s.strip()]
    if ids:
        # ── Batch API route (50% of sequential cost) ──────────────────────────
        # Safe in ids mode only: both extraction paths take the same explicit
        # PDF_ID list here (in range mode they disagree — see run_chunk.py).
        # No worker fan-out: the Batch API parallelises server-side, so N local
        # workers would only fragment one job into N smaller ones.
        use_batch = "--batch" in sys.argv or _env_flag("EXTRACT_BATCH")
        if not use_batch and not _env_flag("NO_AUTO_BATCH"):
            threshold = int(os.environ.get("BATCH_MIN_IDS", "20"))
            if len(ids) >= threshold:
                use_batch = True
                print(f"[auto-batch] {len(ids)} ids >= BATCH_MIN_IDS={threshold} -> one "
                      f"Batch API job at 50% cost instead of {os.environ.get('BATCH_WORKERS', '12')} "
                      f"live workers. Set NO_AUTO_BATCH=1 to force the sequential path.",
                      flush=True)
        if use_batch:
            code = subprocess.call(
                [PYTHON, os.path.join(HERE, "run_chunk.py"), "--batch",
                 "--ids", ",".join(ids)], cwd=HERE)
            sys.exit(code)

        workers = int(os.environ.get("BATCH_WORKERS", "12"))
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        size = -(-len(ids) // workers)  # ceil → even-ish chunks
        print(f"[ids mode] {len(ids)} PDF_IDs across {workers} workers "
              f"(staggered {STAGGER_SECONDS}s)", flush=True)
        procs = []
        for w in range(workers):
            chunk = ids[w * size:(w + 1) * size]
            if not chunk:
                break
            log_path = os.path.join(HERE, "logs", f"ids_{w}.log")
            logf = open(log_path, "w", encoding="utf-8")
            print(f"▶ worker {w}: {len(chunk)} ids → {log_path}")
            p = subprocess.Popen(
                [PYTHON, os.path.join(HERE, "run_chunk.py"), "--ids", ",".join(chunk)],
                stdout=logf, stderr=subprocess.STDOUT, cwd=HERE)
            procs.append((p, logf, w))
            if STAGGER_SECONDS > 0 and (w + 1) * size < len(ids):
                time.sleep(STAGGER_SECONDS)
        print(f"\n{len(procs)} ids-workers launched. Waiting...\n", flush=True)
        for p, logf, w in procs:
            code = p.wait()
            logf.close()
            print(f"✓ ids worker {w} exited (code {code})")
        import glob, re
        tot = {"ok": 0, "partial": 0, "failed": 0, "cost": 0.0}
        for f in glob.glob(os.path.join(HERE, "logs", "ids_*.log")):
            txt = open(f, encoding="utf-8", errors="replace").read()
            m = re.search(r"DONE — ok=(\d+) partial=(\d+) \S+ failed=(\d+) cost=\$([\d.]+)", txt)
            if m:
                tot["ok"] += int(m.group(1)); tot["partial"] += int(m.group(2))
                tot["failed"] += int(m.group(3)); tot["cost"] += float(m.group(4))
        print(f"BATCH SUMMARY (ids mode) — {len(ids)} requested — "
              f"ok={tot['ok']} partial={tot['partial']} failed={tot['failed']} "
              f"cost=${tot['cost']:.2f}", flush=True)
        return

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
