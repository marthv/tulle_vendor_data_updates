"""Live smoke test: run the full metric pass for every live auto experiment against real Xano.

Read-only (experiments_engine.xano refuses anything but GET and POST content/search), budget 40
requests by default. Needs XANO_METADATA_TOKEN in the environment; it is never printed.

    python -m experiments_engine.smoke_live [--budget 40] [--registry experiments/experiment_log.json]
                                            [--status live,planned] [--record]

``--record`` also writes the pdf first-open history to EXP_DATA_DIR (off by default so a laptop
run does not create /data/experiments). Prints aggregate readouts only (no rows, no e-mails).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import metrics, xano

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=40)
    ap.add_argument("--registry", default=os.path.join(HERE, "experiments", "experiment_log.json"))
    ap.add_argument("--status", default="live")
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args(argv)

    if not os.environ.get("XANO_METADATA_TOKEN"):
        print("XANO_METADATA_TOKEN is not set: nothing run.", file=sys.stderr)
        return 2
    with open(a.registry, encoding="utf-8") as f:
        reg = json.load(f)
    entries = reg.get("experiments", reg) if isinstance(reg, dict) else reg
    statuses = tuple(s.strip() for s in a.status.split(",") if s.strip())
    auto = [e for e in entries if metrics.is_auto(e) and str(e.get("status")) in statuses]
    print("auto experiments with status %s: %d" % ("/".join(statuses), len(auto)))

    client = xano.XanoReadOnly(budget=a.budget)
    out, ctx = metrics.run_all(entries, client, statuses=statuses, record_pdf_history=a.record)
    for eid, ro in out.items():
        print("\n=== %s" % eid)
        print(json.dumps(ro, indent=1, default=str)[:4000])
    print("\nloads:", json.dumps([{"view": v, "since": s, "rows": n} for v, s, n in ctx.load_log]))
    print("xano:", json.dumps(client.stats))
    return 0 if all(not (r or {}).get("error") or (r or {}).get("manual") for r in out.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
