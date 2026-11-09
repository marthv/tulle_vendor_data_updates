"""
On-demand background jobs ("press a button, wait for a result").

The Streamlit tabs ran these inline and blocked the page; a slow Xano read could take a
minute and any click in the meantime restarted it. Here POST /api/jobs/<kind> starts the
job in a thread and returns at once; the page polls GET /api/jobs/<kind> until it is done.
One job per kind at a time: a second start while one is running returns the running one,
so double-clicks never launch duplicate reads against Xano.

Results are kept in memory (last run per kind) - they are views of live data, not records.
"""

import threading
import time
import traceback

_JOBS = {}            # kind -> dict(status, started, finished, result, error, by, args)
_LOCK = threading.Lock()


def start(kind, fn, args=None, by=""):
    args = args or {}
    with _LOCK:
        cur = _JOBS.get(kind)
        if cur and cur["status"] == "running":
            return dict(cur, already_running=True)
        job = {"kind": kind, "status": "running", "started": time.time(), "finished": None,
               "result": None, "error": None, "by": by, "args": args}
        _JOBS[kind] = job

    def run():
        try:
            job["result"] = fn(**args)
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001 - surfaced to the page
            job["error"] = f"{type(e).__name__}: {e}"
            job["status"] = "error"
            traceback.print_exc()
        finally:
            job["finished"] = time.time()

    threading.Thread(target=run, daemon=True, name=f"job-{kind}").start()
    return dict(job)


def get(kind):
    j = _JOBS.get(kind)
    return dict(j) if j else {"kind": kind, "status": "idle"}
