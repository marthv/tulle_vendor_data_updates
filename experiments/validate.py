"""Validate the experiment registry (FRAMEWORK.md sections 1, 4 and 10).

Usage:
    python experiments/validate.py            # live / planned / paused / v2 entries
    python experiments/validate.py --all      # every entry, including decided ones
    python experiments/validate.py --id EXP-2026-11-slug   # one entry, strictly (launch check)
    python experiments/validate.py --registry path/to/experiment_log.json

Known gaps (policy 2026-11-19): an entry's ``spec_gaps`` list ("<field>: why") acknowledges
issues already recorded at migration. In a full-file run, a blocking issue whose field matches a
gap (same field, a sub-field such as guardrails[0] or assignment.pre, or review_date under a
planned_days gap) is printed as a "known gap" warning and does not fail the run. ``--id`` never
excuses spec_gaps: a new launch must pass strictly.

Prints a per-experiment report with spec issues and the sample plan (days needed vs
max_days; warns "Can't reach a verdict").

Exit codes:
    0  ok (legacy entries may still show issues; known gaps never fail the run)
    1  a live/planned/paused framework_version 2 entry has unacknowledged blocking issues, ids are
       duplicated, or (--id) the entry is missing or has any blocking issue
    2  the registry is missing or not valid JSON / not the expected shape
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from experiments_engine import spec  # noqa: E402

DEFAULT_REGISTRY = os.path.join(HERE, "experiment_log.json")


def _out(s=""):
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("ascii", "replace").decode("ascii"))


def load_registry(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("experiments"), list):
        raise ValueError('registry must be an object with an "experiments" list')
    return data


def _plan_line(e):
    if e.get("_assignment_type") == "rollout":
        return "sample plan: n/a (rollout, guardrails only)"
    p = spec.plan(e)
    if p.get("n_per_arm") is None:
        return f"sample plan: not computable ({p.get('reason')})"
    line = f"sample plan: {p['n_per_arm']} per arm (d={p['d_abs']}, baseline={p['baseline']})"
    if p.get("days_needed") is None:
        return line + f"; days needed unknown ({p.get('reason')})"
    line += f"; ~{p['days_needed']} days at {p['daily_exposed_per_arm']:g}/day/arm"
    mdays = spec._num(e.get("max_days"))
    if mdays:
        line += f" vs max_days {int(mdays)}"
        if p["days_needed"] > mdays:
            line += "  -> WARNING: Can't reach a verdict"
    return line


GAP_ALIASES = {"planned_days": ("review_date",)}   # the migration wrote "no review date / run length"


def gap_fields(entry):
    """Field names acknowledged by the entry's spec_gaps ("<field>: why" strings)."""
    out = set()
    for g in entry.get("spec_gaps") or []:
        f = str(g).split(":", 1)[0].strip() if ":" in str(g) else ""
        if f:
            out.add(f)
            out.update(GAP_ALIASES.get(f, ()))
    return out


def is_known_gap(issue, fields):
    f = str(issue.get("field") or "")
    return bool(f) and any(f == g or f.startswith(g + ".") or f.startswith(g + "[") for g in fields)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate experiments/experiment_log.json (FRAMEWORK.md)")
    ap.add_argument("--all", action="store_true", help="also report decided / draft entries")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--id", dest="only_id", default=None,
                    help="validate one entry strictly (spec_gaps not excused); use before a launch")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        reg = load_registry(args.registry)
    except (OSError, ValueError) as exc:
        _out(f"ERROR: cannot load registry {args.registry}: {exc}")
        return 2

    exps = [x for x in reg["experiments"] if isinstance(x, dict)]
    failures = []
    dupes = [i for i, c in Counter(x.get("id") for x in exps).items() if c > 1]
    strict = args.only_id is not None
    if strict:
        dupes = [d for d in dupes if d == args.only_id]
        exps = [x for x in exps if x.get("id") == args.only_id]
        if not exps:
            failures.append(f"no entry with id {args.only_id}")
    for d in dupes:
        failures.append(f"duplicate id {d}")

    counts = Counter()
    known_total = 0
    for raw in exps:
        e = spec.normalise(raw)
        issues = spec.validate(raw)
        enforced = spec.is_enforced(e)
        v2 = not e["_legacy"]
        if not strict:
            fields = gap_fields(raw)
            for i in issues:
                if i.get("severity") == "error" and is_known_gap(i, fields):
                    i["severity"] = "known gap"
                    known_total += 1
        blocking = spec.blocking(issues)
        if (enforced and v2 or strict) and blocking:
            failures.append(f"{e.get('id')}: {len(blocking)} blocking issue(s): "
                            + "; ".join(sorted({str(b.get('field')) for b in blocking})))
        counts[(e["_lifecycle"], "v2" if v2 else "legacy",
                "incomplete" if blocking else "ok")] += 1
        if not args.all and not enforced and not strict:
            continue
        tag = "v2" if v2 else "legacy"
        verdict = "OK" if not blocking else ("FAIL" if (v2 and enforced) or strict
                                              else "incomplete (legacy, not failing)")
        _out(f"\n{e.get('id')}  [{e.get('status')}, {tag}, tier={e.get('tier') or '-'}]  {verdict}")
        if e.get("name"):
            _out(f"  {e.get('name')}")
        for i in issues:
            q = f"Q{i['question']}" if i.get("question") else "--"
            _out(f"  {i['severity']:<9} {q:<3} {i.get('field') or '':<26} {i['message']}")
        if enforced or strict:
            _out("  " + _plan_line(e))

    _out("\nSummary (lifecycle, version, spec):")
    for k in sorted(counts):
        _out(f"  {k[0]:<8} {k[1]:<7} {k[2]:<11} {counts[k]}")
    if not strict:
        _out(f"  known gaps: {known_total} (acknowledged in spec_gaps; warnings, not failures)")
    if failures:
        _out("\nFAILED:")
        for f in failures:
            _out(f"  {f}")
        return 1
    if strict:
        _out(f"\nPASS ({args.only_id}: no blocking issues, strict mode)")
    else:
        _out("\nPASS (no live/planned framework_version 2 entry has unacknowledged blocking issues; ids unique)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
