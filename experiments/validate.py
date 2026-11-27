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

Rotation (policy 2026-11-19): at most ROTATION_MAX (3) live entries carry ``in_rotation: true``, each
with a run of at most 14 days (planned_days, or review_date - start_date). ``--id`` fails an in-rotation
entry that breaks either rule; a full-file run prints a warning. A v2 live/planned/paused entry without a
``bucket`` gets a warning showing the derived bucket.

Category (owner 2026-11-27): every entry, decided ones included, needs ``category`` set to one of
experiments_engine/categories.py CATEGORIES (the Experiments tab's category filter). A missing or unknown category
fails the run.

Exit codes:
    0  ok (legacy entries may still show issues; known gaps never fail the run)
    1  a live/planned/paused framework_version 2 entry has unacknowledged blocking issues, ids are
       duplicated, an entry has no valid category, or (--id) the entry is missing or has any blocking issue
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

from experiments_engine import buckets, categories, readlock, spec  # noqa: E402
from experiments_engine.snapshot import ROTATION_MAX  # noqa: E402

ROTATION_MAX_DAYS = 14

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


GRANDFATHERED = ("EXP-2026-11-planner-context-first",)     # live before launch_lock existed; needs one before 12-01


def exposure_issues(entry, e, strict):
    """EXP_TAB_V2 section 14: a test with no record of who was exposed cannot go live.

    Whatever the switch says: a live or paused split needs a written ``exposure`` and a ``launch_lock`` that matches its
    settings, in every run (GRANDFATHERED entries: only in the launch check). The launch check (--id) also refuses a planned
    split with no written exposure, and any other test with no exposure at all, except a rollout."""
    from experiments_engine import v2, v2_data
    life, atype = e["_lifecycle"], e.get("_assignment_type")
    split = readlock.is_split(entry)
    running = life not in ("decided", "planned", "draft")
    if not running and life != "planned" or (atype == "rollout" and not split):
        return []
    out = []

    def add(sev, field, msg):
        out.append({"severity": sev, "question": 2 if field == "exposure" else 6, "field": field, "message": msg})
    old = entry.get("id") in GRANDFATHERED and not strict
    written = v2_data.valid_source(v2_data.explicit_source(entry))
    ok = written if split else bool(v2_data.exposure_rule(entry))
    if not ok:
        said = v2_data.explicit_source(entry)
        msg = (f"exposure source {said!r} is not a record of who saw the test" if said else "No exposure source") + \
            ': set exposure = {"source": "xano:table79" | "xano:table1" | "mixpanel:<event name>", "surfaces": [...]}, ' \
            "or it cannot go live"
        if (split and running and not old) or strict:
            add("error", "exposure", msg)
        elif readlock.enabled():
            add("warn", "exposure", msg)
    if split and running:
        if not readlock.has_lock(entry):
            msg = "No launch_lock: run validate.py --lock <id> at launch and paste it into the entry"
            if not old:
                add("error", "launch_lock", msg)
            elif readlock.enabled():
                add("warn", "launch_lock", msg)
        else:
            why = readlock.lock_problem(entry)
            if why:
                add("error", "launch_lock", "RED: launch lock " + why)
    return out


def print_lock(entry, write=False):
    """The launch_lock to paste into the entry; with --write also record it in experiments_engine/launch_locks.json."""
    from experiments_engine import v2
    lock = v2.launch_lock(entry)
    _out(json.dumps({"launch_lock": lock}, indent=2, ensure_ascii=False))
    if not write:
        return 0
    with open(readlock.LEDGER, encoding="utf-8") as f:
        data = json.load(f)
    locks = data.setdefault("locks", {})
    if entry["id"] in locks and locks[entry["id"]].get("hash") != lock["hash"]:
        _out(f"ERROR: {entry['id']} already has a different recorded lock; a recorded lock is never replaced")
        return 1
    locks.setdefault(entry["id"], {"hash": lock["hash"], "read_date": lock["settings"]["read_date"],
                                   "name": readlock.fingerprint(entry), "locked_on": lock["locked_on"]})
    with open(readlock.LEDGER, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    _out(f"recorded in {readlock.LEDGER}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate experiments/experiment_log.json (FRAMEWORK.md)")
    ap.add_argument("--all", action="store_true", help="also report decided / draft entries")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--id", dest="only_id", default=None,
                    help="validate one entry strictly (spec_gaps not excused); use before a launch")
    ap.add_argument("--lock", dest="lock_id", default=None,
                    help="print the launch_lock object (hash of the launch settings) to paste into the entry at launch")
    ap.add_argument("--write", action="store_true", help="with --lock: also record it in experiments_engine/launch_locks.json")
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
    if args.lock_id:
        hit = [x for x in exps if x.get("id") == args.lock_id]
        if not hit:
            _out(f"ERROR: no entry with id {args.lock_id}")
            return 1
        return print_lock(hit[0], args.write)
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

    all_exps = [x for x in reg["experiments"] if isinstance(x, dict)]
    live_rot = [x.get("id") for x in all_exps if x.get("in_rotation") and str(x.get("status")) == "live"]
    if len(live_rot) > ROTATION_MAX:
        msg = f"{len(live_rot)} live experiments in rotation (max {ROTATION_MAX}): {', '.join(live_rot)}"
        if strict and args.only_id in live_rot:
            failures.append(msg)
        elif not strict:
            _out(f"WARNING: {msg}")

    counts = Counter()
    known_total = 0
    for raw in exps:
        e = spec.normalise(raw)
        issues = spec.validate(raw) + exposure_issues(raw, e, strict)
        enforced = spec.is_enforced(e)
        v2 = not e["_legacy"]
        if not strict:
            fields = gap_fields(raw)
            for i in issues:
                if i.get("severity") == "error" and is_known_gap(i, fields):
                    i["severity"] = "known gap"
                    known_total += 1
        if raw.get("in_rotation"):
            sd, rd = spec.parse_date(raw.get("start_date")), spec.parse_date(raw.get("review_date"))
            run = spec._num(raw.get("planned_days")) or ((rd - sd).days if (sd and rd) else None)
            if run is None or run > ROTATION_MAX_DAYS:
                issues.append({"severity": "error" if strict else "warn", "question": 6, "field": "planned_days",
                               "message": f"In rotation: the run must be {ROTATION_MAX_DAYS} days or less "
                                          f"(got {run if run is not None else 'none'})"})
        if v2 and enforced and not buckets.derive(raw)[1] == "registry":
            issues.append({"severity": "warn", "question": 2, "field": "bucket",
                           "message": f"No bucket; derived '{buckets.derive(raw)[0]}' (one of {', '.join(buckets.KEYS)})"})
        cat_why = categories.problem(raw)
        if cat_why:
            issues.append({"severity": "error", "question": None, "field": "category", "message": cat_why})
        blocking = spec.blocking(issues)
        if (enforced and v2 or strict) and blocking:
            failures.append(f"{e.get('id')}: {len(blocking)} blocking issue(s): "
                            + "; ".join(sorted({str(b.get('field')) for b in blocking})))
        elif cat_why:   # a category is required on every entry, decided ones too
            failures.append(f"{e.get('id')}: {cat_why}")
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
