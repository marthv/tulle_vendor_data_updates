"""Build the dash snapshot (FRAMEWORK.md sections 5 and 7).

:func:`build_snapshot` turns the registry plus injected readouts into one JSON-serialisable
dict for the Experiments tab and the alert cron. It never does I/O itself: metrics come from
``metric_runner(entry) -> readout`` and history from ``history_store.load(exp_id, days)``.
One experiment failing (bad entry, runner raising, scoring bug) is caught and recorded in
``errors[]``; the rest of the snapshot is still built.

Readout contract (returned by metric_runner)::

    {"arms": {"control": {"n", "conv"}, "treatment": {"n", "conv"}}   # rate / benchmark
     | "count": {"pre": {"count", "days"}, "post": {"count", "days"}},  # count metric
     "exposure_by_day": [{"date", "n"} | {"date", "control", "treatment"}],
     "guardrails": [{"key", "count"|"arms"|"breached"}],
     "computed_at": iso, "error": None | str}

For manual reporting (``reporting == "manual"``, missing reporting on legacy entries, or
``metric.key == "manual"``) the runner is not called; the latest ``results[].counts`` is used.
"""
from __future__ import annotations

import datetime as _dt
import json
import traceback

from . import health as _health
from . import spec

SECTION_KEYS = ("what", "impacts", "hypothesis", "tests", "success_metric", "timeline", "reporting")


def _utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


def _jsonable(obj):
    """Round-trip through json with a str fallback so the snapshot is always serialisable."""
    return json.loads(json.dumps(obj, default=str))


def is_manual(entry):
    e = entry
    return str(e.get("reporting") or "manual").lower() == "manual" or \
        str((e.get("metric") or {}).get("key") or "") == "manual"


def manual_readout(entry):
    """Readout from the latest results[] entry that has counts (section 10 "Read out")."""
    e = entry if "_lifecycle" in entry else spec.normalise(entry)
    for r in reversed(e.get("results") or []):
        c = r.get("_counts")
        if c:
            out = {"source": "manual", "computed_at": r.get("date"), "error": None,
                   "exposure_by_day": None}
            out.update(c)
            return out
    return {"source": "manual", "computed_at": None, "error": None, "arms": None,
            "note": "no results[] entry with counts"}


def sections(e, plan=None):
    """The seven sections of section 1, in dash order (section 7.4)."""
    return {
        "what": {"name": e.get("name"), "what_changed": e.get("what_changed") or e.get("what"),
                 "links": e.get("links") or []},
        "impacts": {"surfaces": e.get("surfaces") or [], "audience": e.get("audience") or {}},
        "hypothesis": e.get("hypothesis"),
        "tests": e.get("tests"),
        "success_metric": {"metric": e.get("metric") or {}, "guardrails": e.get("guardrails") or [],
                           "decision_rule": spec.decision_rule(e), "sample_plan": plan,
                           "primary_metric_v1": e.get("primary_metric"), "goal_v1": e.get("goal")},
        "timeline": {"start_date": e.get("start_date"), "planned_days": e.get("planned_days"),
                     "review_date": e.get("review_date"), "max_days": e.get("max_days"),
                     "end_date": e.get("end_date")},
        "reporting": e.get("reporting"),
    }


def _public_results(e):
    out = []
    for r in e.get("results") or []:
        r = {k: v for k, v in r.items() if not k.startswith("_")}
        out.append(r)
    return out


def _decided_row(e):
    results = e.get("results") or []
    takeaway = e.get("decision") or (results[-1].get("note") if results else None)
    return {"id": e.get("id"), "name": e.get("name"), "status": e.get("status"),
            "outcome": e.get("_outcome"), "decided_on": e.get("decided_on") or e.get("end_date"),
            "decision": e.get("decision"), "takeaway": takeaway, "themes": e.get("themes") or [],
            "tier": e.get("tier"), "start_date": e.get("start_date"), "end_date": e.get("end_date")}


def _queue_key(row):
    state = row.get("health")
    urgent = 0 if state == "guardrail_breach" else (1 if row.get("stale") else 2)
    return (urgent, _health.RANK.get(state, 99), -(row.get("overdue_days") or 0), row.get("id") or "")


def build_snapshot(registry_dict, metric_runner=None, history_store=None, today=None):
    """Build the snapshot dict (section 5 steps 1-5, section 7 layout).

    registry_dict   parsed experiment_log.json ({"experiments": [...], "changes": [...]})
    metric_runner   callable(entry) -> readout dict (auto reporting); may raise
    history_store   object with ``load(exp_id, days=90)`` (history.HistoryStore) or None
    today           date / ISO date (default UTC today); also sets the reference day

    Returns {generated_at, today, summary{needs_call, stale, healthy, guardrail, live, ...},
    action_queue[], experiments[], decided[], changes[], errors[]}.
    summary.needs_call counts needs_call + callable_win + callable_loss (all need the owner).
    """
    now = _utcnow()
    today_d = spec.parse_date(today) if today is not None else now.date()
    errors = []
    reg = registry_dict if isinstance(registry_dict, dict) else {}
    raw_exps = reg.get("experiments") if isinstance(reg.get("experiments"), list) else []
    changes = [c for c in (reg.get("changes") or []) if isinstance(c, dict)]

    experiments, decided = [], []
    for raw in raw_exps:
        eid = raw.get("id") if isinstance(raw, dict) else None
        try:
            if not isinstance(raw, dict):
                raise TypeError(f"registry entry is a {type(raw).__name__}, not an object")
            e = spec.normalise(raw)
            issues = spec.validate(raw)
            if e["_lifecycle"] == "decided":
                decided.append(_decided_row(e))
                continue
            row = {"id": eid, "name": e.get("name"), "status": e.get("status"),
                   "lifecycle": e["_lifecycle"], "tier": e.get("tier"),
                   "tier_effective": spec.tier(e), "themes": e.get("themes") or [],
                   "framework_version": e["framework_version"], "legacy": e["_legacy"],
                   "assignment_type": e.get("_assignment_type"),
                   "spec_issues": issues, "results": _public_results(e)}
            readout, hist, h = None, [], None
            if e["_lifecycle"] in ("live", "paused"):
                if is_manual(e):
                    readout = manual_readout(e)
                elif metric_runner is None:
                    readout = {"error": "no metric runner configured", "computed_at": None}
                else:
                    try:
                        readout = metric_runner(raw)
                        if not isinstance(readout, dict):
                            raise TypeError(f"metric runner returned {type(readout).__name__}")
                    except Exception as exc:
                        readout = {"error": f"{type(exc).__name__}: {exc}", "computed_at": None}
                        errors.append({"id": eid, "stage": "metric", "error": readout["error"]})
                    if readout.get("error") and not any(x["id"] == eid and x["stage"] == "metric"
                                                        for x in errors):
                        errors.append({"id": eid, "stage": "metric", "error": str(readout["error"])})
                if history_store is not None:
                    try:
                        try:
                            hist = history_store.load(eid, days=90, today=today_d) or []
                        except TypeError:  # a store whose load() has no ``today`` kwarg
                            hist = history_store.load(eid, days=90) or []
                    except Exception as exc:
                        errors.append({"id": eid, "stage": "history", "error": f"{type(exc).__name__}: {exc}"})
                        hist = []
                hist = sorted((x for x in hist if isinstance(x, dict)), key=lambda x: str(x.get("date")))
                h = _health.evaluate(e, readout, hist, changes, today_d, spec_issues=issues)
            plan = (h or {}).get("sample_plan") or (spec.plan(e) if e.get("_assignment_type") != "rollout" else None)
            row.update(sections(e, plan))
            row["seven"] = {k: row[k] for k in SECTION_KEYS}
            row["readout"] = readout
            if h:
                for k in ("health", "stale", "action", "reason", "days_running", "days_left",
                          "progress", "progress_basis", "flags", "overdue_days", "projected_days",
                          "stats", "guardrails", "evidence_label", "exposure_per_arm_day",
                          "decision_rule", "guardrail_status", "failed_runs", "paused_days"):
                    row[k] = h.get(k)
                for g in h.get("guardrail_status") or []:
                    # real computation errors only (not manual guardrails, not "no data yet")
                    gro = next((x for x in (readout or {}).get("guardrails") or []
                                if isinstance(x, dict) and x.get("key") == g["key"]), None)
                    if g["status"] == "unmonitored" and gro and gro.get("error") and not gro.get("manual"):
                        errors.append({"id": eid, "stage": "guardrail", "key": g["key"],
                                       "error": str(gro["error"])})
                row["overlapping_changes"] = h.get("overlapping_changes") or []
            else:
                row.update({"health": None, "stale": False, "action": None, "reason": None,
                            "overlapping_changes": []})
            experiments.append(row)
        except Exception as exc:
            errors.append({"id": eid, "stage": "evaluate", "error": f"{type(exc).__name__}: {exc}",
                           "trace": traceback.format_exc(limit=3)})
            experiments.append({"id": eid, "name": (raw or {}).get("name") if isinstance(raw, dict) else None,
                                "status": (raw or {}).get("status") if isinstance(raw, dict) else None,
                                "health": None, "stale": False, "action": None,
                                "reason": f"engine error: {exc}", "spec_issues": [],
                                "overlapping_changes": [], "results": []})

    live_rows = [r for r in experiments if r.get("health")]
    queue = sorted([r for r in live_rows if r["health"] != "on_track" or r.get("stale")], key=_queue_key)
    action_queue = [{"id": r["id"], "name": r.get("name"), "health": r["health"], "stale": r.get("stale"),
                     "action": r.get("action"), "reason": r.get("reason"),
                     "overdue_days": r.get("overdue_days")} for r in queue]

    change_rows = []
    for ch in changes:
        try:
            overlaps = [r["id"] for r in live_rows
                        if any(o is ch for o in r.get("overlapping_changes") or [])]
            change_rows.append(dict(ch, overlaps_experiments=overlaps))
        except Exception as exc:
            errors.append({"id": None, "stage": "changes", "error": str(exc)})

    summary = {
        "needs_call": sum(1 for r in live_rows if r["health"] in ("needs_call", "callable_win", "callable_loss")),
        "stale": sum(1 for r in live_rows if r.get("stale")),
        "healthy": sum(1 for r in live_rows if r["health"] == "on_track" and not r.get("stale")),
        "guardrail": sum(1 for r in live_rows if r["health"] == "guardrail_breach"),
        "live": len(live_rows),
        "planned": sum(1 for r in experiments if r.get("lifecycle") == "planned"),
        "decided": len(decided),
        "spec_incomplete": sum(1 for r in live_rows if "spec_incomplete" in (r.get("flags") or [])),
    }
    snap = {"generated_at": now.isoformat().replace("+00:00", "Z"), "today": today_d.isoformat(),
            "summary": summary, "action_queue": action_queue, "experiments": experiments,
            "decided": decided, "changes": change_rows, "errors": errors}
    return _jsonable(snap)
