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

from . import buckets as _buckets
from . import health as _health
from . import readlock
from . import spec
from . import verdict as _verdict

ROTATION_MAX = 3
CALL_STATES = ("needs_call", "callable_win", "callable_loss")

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
    row = {"id": e.get("id"), "name": e.get("name"), "status": e.get("status"),
           "outcome": e.get("_outcome"), "decided_on": e.get("decided_on") or e.get("end_date"),
           "decision": e.get("decision"), "takeaway": takeaway, "themes": e.get("themes") or [],
           "category": e.get("category"),
           "tier": e.get("tier"), "start_date": e.get("start_date"), "end_date": e.get("end_date"),
           "lifecycle": "decided"}
    row["bucket"], row["bucket_source"] = _buckets.derive(e)
    row.update(calendar_fields(e))
    row["verdict"] = _verdict.build_decided_verdict(row)
    return row


def calendar_fields(e):
    """Dates the dash calendar needs: start, review (registry, else start + planned_days), hard stop
    (start + max_days), decision date and paused ranges [{from, to}] (to None = still paused)."""
    sd = spec.parse_date(e.get("start_date"))
    review = spec.parse_date(e.get("review_date"))
    pdays, mdays = spec._num(e.get("planned_days")), spec._num(e.get("max_days"))
    if review is None and sd and pdays:
        review = sd + _dt.timedelta(days=int(pdays))
    max_end = sd + _dt.timedelta(days=int(mdays)) if (sd and mdays) else None
    decided = spec.parse_date(e.get("decided_on")) or (
        spec.parse_date(e.get("end_date")) if e.get("_lifecycle") == "decided" else None)
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    paused = []
    for rng in e.get("paused_ranges") or []:
        if isinstance(rng, dict) and spec.parse_date(rng.get("from")):
            paused.append({"from": iso(spec.parse_date(rng.get("from"))), "to": iso(spec.parse_date(rng.get("to")))})
    pon = spec.parse_date(e.get("paused_on") or e.get("pause_date") or e.get("paused_at"))
    if pon and not any(r["from"] == pon.isoformat() for r in paused):
        end = None if e.get("_lifecycle") == "paused" else (spec.parse_date(e.get("resumed_on")) or decided)
        paused.append({"from": pon.isoformat(), "to": iso(end)})
    return {"start_date": iso(sd), "review_date": iso(review), "max_end_date": iso(max_end),
            "decided_on": iso(decided), "paused_ranges": paused}


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
    v2on, entries = readlock.enabled(), {}
    for raw in raw_exps:
        eid = raw.get("id") if isinstance(raw, dict) else None
        try:
            if not isinstance(raw, dict):
                raise TypeError(f"registry entry is a {type(raw).__name__}, not an object")
            e = spec.normalise(raw)
            issues = spec.validate(raw)
            lock = None
            if v2on and history_store is not None and hasattr(history_store, "load_lock"):
                try:
                    lock = history_store.load_lock(eid)
                except Exception as exc:
                    errors.append({"id": eid, "stage": "read lock", "error": f"{type(exc).__name__}: {exc}"})
            split = readlock.is_split(e, lock) if v2on else e.get("_assignment_type") == "split"
            if v2on:
                e["_split"] = split
            if e["_lifecycle"] == "decided":
                decided.append(_decided_row(e))
                if v2on and e.get("family"):
                    decided[-1]["family"] = e["family"]
                if v2on and (readlock.hidden(raw, today_d, lock) or split and metric_runner is None and
                             not readlock.text_open(raw, today_d)):
                    from . import v2
                    v2.lock_decided(decided[-1], raw, lock, today_d)
                continue
            row = {"id": eid, "name": e.get("name"), "status": e.get("status"),
                   "lifecycle": e["_lifecycle"], "tier": e.get("tier"),
                   "tier_effective": spec.tier(e), "themes": e.get("themes") or [], "category": e.get("category"),
                   "framework_version": e["framework_version"], "legacy": e["_legacy"],
                   "assignment_type": e.get("_assignment_type"),
                   "spec_issues": issues, "results": _public_results(e),
                   "in_rotation": bool(e.get("in_rotation")),
                   "notes_synced_through": e.get("notes_synced_through")}
            row["bucket"], row["bucket_source"] = _buckets.derive(e)
            readout, hist, h = None, [], None
            if e["_lifecycle"] in ("live", "paused"):
                if is_manual(e):
                    readout = manual_readout(e)
                    if v2on:
                        readout = _v2_manual(raw, readout, metric_runner)
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
                if v2on and split:
                    h = _v2_evaluate(e, readout, hist, changes, today_d, issues, history_store, errors, lock)
                else:
                    h = _health.evaluate(e, readout, hist, changes, today_d, spec_issues=issues)
            plan = (h or {}).get("sample_plan") or (spec.plan(e) if e.get("_assignment_type") != "rollout" else None)
            row.update(sections(e, plan))
            row["seven"] = {k: row[k] for k in SECTION_KEYS}
            row["readout"] = readout
            if h:
                for k in ("health", "stale", "action", "reason", "days_running", "days_left",
                          "progress", "progress_basis", "flags", "overdue_days", "projected_days",
                          "stats", "guardrails", "evidence_label", "exposure_per_arm_day",
                          "decision_rule", "guardrail_status", "failed_runs", "paused_days",
                          "approximate_metric", "capped_win"):
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
            if v2on:
                entries[eid] = e
                for k in ("plain_name", "plain_line"):
                    if e.get(k):
                        row[k] = e[k]
                if split and h:
                    for k in ("read_date", "result_hidden", "result_text", "read", "checks", "light", "exposed",
                              "finished", "harm", "sample_plan"):
                        if k in h:
                            row[k] = h[k]
                elif readlock.hidden(e, today_d, lock):   # planned, or a status the engine doesn't score: still locked
                    row.update(result_hidden=True, result_text=readlock.hidden_text(e, lock))
                if split and metric_runner is None and not row.get("result_hidden") and not readlock.text_open(raw, today_d):
                    # registry-only view: it can't see the engine's copy of the lock, so it needs a recorded one
                    row.update(result_hidden=True, result_text="Result: not shown without the engine", read=None,
                               health="needs_call", flags=[], reason="Read date reached; open the engine's view",
                               action="Owner call due")
            row.update(calendar_fields(e))
            row["verdict"] = _verdict.build_verdict(row, today_d)
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
    change_rows = []
    for ch in changes:
        try:
            overlaps = [r["id"] for r in live_rows
                        if any(o is ch for o in r.get("overlapping_changes") or [])]
            change_rows.append(dict(ch, overlaps_experiments=overlaps))
        except Exception as exc:
            errors.append({"id": None, "stage": "changes", "error": str(exc)})

    snap = {"generated_at": now.isoformat().replace("+00:00", "Z"), "today": today_d.isoformat(),
            "experiments": experiments, "decided": decided, "changes": change_rows, "errors": errors}
    if v2on:
        _v2_finish(snap, entries, today_d)
    recompute_views(snap)
    return _jsonable(snap)


# -- EXP_TAB_V2 (FRAMEWORK section 14) ---------------------------------------------------------------------------

_V2_MANUAL_KEYS = ("guardrails", "payments", "exposure_by_day", "exposed", "mixing", "last_exposure_at", "overlap")


def _v2_manual(raw, readout, metric_runner):
    """Manual entry: the results[] read-out plus what the engine can compute itself (guardrails, payments and
    couples by arm). A runner that has nothing for it changes nothing."""
    if metric_runner is None:
        return readout
    try:
        ro = metric_runner(raw)
    except Exception:
        return readout
    if not isinstance(ro, dict) or (ro.get("error") and "guardrails" not in ro):
        return readout
    return dict(readout, **{k: ro[k] for k in _V2_MANUAL_KEYS if k in ro})


def _v2_evaluate(e, readout, hist, changes, today_d, issues, store, errors, lock=None):
    """health.evaluate for a split with the stored launch settings and the stored read (engine volume)."""
    from . import v2
    eid = e.get("id")
    read = look = None
    try:
        if store is not None and hasattr(store, "load_lock"):
            if e["_lifecycle"] == "live":
                good = readlock.lock_problem(e) is None
                new = dict(e["launch_lock"] if good else v2.launch_lock(e, today_d), from_registry=good)
                if lock is None:
                    lock = new
                    store.save_lock(eid, lock)
                elif good and not lock.get("from_registry") and v2.same_core(lock, new):
                    lock = new                   # first seen without a lock; now properly locked with the same test
                    store.save_lock(eid, lock, replace=True)
            read = store.load_read(eid)
            look = store.load_look(eid)
    except Exception as exc:
        errors.append({"id": eid, "stage": "read lock", "error": f"{type(exc).__name__}: {exc}"})
    h = _health.evaluate(e, readout, hist, changes, today_d, spec_issues=issues, lock=lock, read=read,
                         look=look)
    try:
        lk = h.get("look") or {}
        if store is not None and hasattr(store, "save_look") and lk.get("measures"):
            store.save_look(eid, lk)
        r = h.get("read") or {}
        if r.get("call") and read is None and store is not None and hasattr(store, "save_read"):
            store.save_read(eid, dict(r, read_at=_utcnow().isoformat().replace("+00:00", "Z")))
    except Exception as exc:
        errors.append({"id": eid, "stage": "read lock", "error": f"{type(exc).__name__}: {exc}"})
    return h


def _v2_finish(snap, entries, today_d):
    """Overlaps, cards and the family fold, then the read lock on every row whose result is hidden."""
    from . import v2, v2_cards
    snap["exp_tab_v2"] = True
    rows = [r for r in snap["experiments"] if isinstance(r, dict)]
    owner = {e.get("family"): i for i, e in entries.items() if e.get("family") and e.get("family_card")}
    for r in rows + snap["decided"]:
        fam = r.get("family") or (entries.get(r.get("id")) or {}).get("family")
        if fam in owner and owner[fam] != r.get("id"):
            r["folded_into"] = owner[fam]
    v2.attach_overlaps(rows, entries)
    v2.strip_cells(rows)
    for r in rows:
        e = entries.get(r.get("id"))
        if e is None:
            continue
        try:
            r["card"] = v2_cards.build_card(r, e, today_d)
        except Exception as exc:
            snap["errors"].append({"id": r.get("id"), "stage": "card", "error": f"{type(exc).__name__}: {exc}"})
        if r.get("result_hidden"):
            v2.lock_row(r, e, r.get("read_date"))
            r["verdict"] = v2.build_verdict(r) if r.get("lifecycle") not in ("planned", "draft") else r.get("verdict")
        if e.get("family_history"):
            r["family_history"] = readlock.public_history(e, today_d, r.get("read_date"))


def _next_monday(d):
    return d + _dt.timedelta(days=(7 - d.weekday()) % 7)


def recompute_views(snap):
    """summary, action_queue, rotation and buckets from the rows. Idempotent; re-run at serve time after
    notes set ``decision_pending`` on rows (those leave Decide now, the queue and the backlog)."""
    experiments = [r for r in snap.get("experiments") or [] if isinstance(r, dict)]
    decided = [r for r in snap.get("decided") or [] if isinstance(r, dict)]
    if snap.get("exp_tab_v2"):             # versions folded into a family card are not tests of their own
        experiments = [r for r in experiments if not r.get("folded_into")]
        decided = [r for r in decided if not r.get("folded_into")]
    today_d = spec.parse_date(snap.get("today")) or _utcnow().date()
    live_rows = [r for r in experiments if r.get("health")]
    open_rows = [r for r in live_rows if not r.get("decision_pending")]
    queue = sorted([r for r in open_rows if r["health"] != "on_track" or r.get("stale")], key=_queue_key)
    snap["action_queue"] = [{"id": r["id"], "name": r.get("name"), "health": r["health"], "stale": r.get("stale"),
                             "action": r.get("action"), "reason": r.get("reason"),
                             "overdue_days": r.get("overdue_days"),
                             "recommend": ((r.get("verdict") or {}).get("recommend") or {}).get("label"),
                             "in_rotation": bool(r.get("in_rotation"))} for r in queue]
    snap["summary"] = {
        "needs_call": sum(1 for r in open_rows if r["health"] in CALL_STATES),
        "stale": sum(1 for r in open_rows if r.get("stale")),
        "healthy": sum(1 for r in open_rows if r["health"] == "on_track" and not r.get("stale")),
        "guardrail": sum(1 for r in open_rows if r["health"] == "guardrail_breach"),
        "live": len(live_rows),
        "planned": sum(1 for r in experiments if r.get("lifecycle") == "planned"),
        "decided": len(decided),
        "spec_incomplete": sum(1 for r in open_rows if "spec_incomplete" in (r.get("flags") or [])),
        "decision_pending": sum(1 for r in experiments if r.get("decision_pending")),
        "in_rotation": sum(1 for r in experiments if r.get("in_rotation") and r.get("lifecycle") == "live"),
    }
    rot = [r["id"] for r in experiments if r.get("in_rotation") and r.get("lifecycle") in ("live", "paused")]
    backlog_rows = sorted([r for r in live_rows if not r.get("in_rotation") and not r.get("decision_pending")],
                          key=_queue_key)
    live_rot = [r for r in experiments if r.get("in_rotation") and r.get("lifecycle") == "live"]
    snap["rotation"] = {"max": ROTATION_MAX, "in_rotation": rot, "over_cap": len(live_rot) > ROTATION_MAX,
                        "backlog": [r["id"] for r in backlog_rows],
                        "next_call_date": _next_monday(today_d).isoformat()}
    snap["buckets"] = _buckets.summary([r for r in experiments if not r.get("decision_pending")], decided)
    for b in snap["buckets"]:
        b["decision_pending"] = [r["id"] for r in experiments if r.get("decision_pending") and r.get("bucket") == b["key"]]
    return snap
