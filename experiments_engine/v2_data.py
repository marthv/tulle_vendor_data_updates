"""EXP_TAB_V2: per-couple data for experiments (FRAMEWORK.md section 14). Only used when readlock.enabled().

* exposure rule: which couples a test counts (registry ``exposure``, else derived from the metric and surfaces);
* arm switchers and repeat accounts: found, counted and left out of the read;
* guardrails and payments by arm, also for manual entries; nothing inside a split is compared before/after;
* the read window of a split ends at its read date, so a later run can't change the numbers;
* shared couples and the 2x2 cells of two splits.

No extra tables or services: the same read-only, budgeted table views as metrics.py.
"""
from __future__ import annotations

import copy
import re

from . import metrics as _m
from . import readlock
from . import spec
from . import xano as _x

DAY = _m.DAY
WHO_LOOKBACK_DAYS = 30          # accounts created up to this long before the start are checked for repeats
PAYMENT_COUNTS = ("purchases_per_day", "revenue_per_day")
USAGE_METRICS = ("planner_second_question", "planner_return_within_days", "planner_pdf_open_rate")
ARM_FIELDS = ("ctx_arm", "exp_arm", "arm")
ARM_WORDS = {"treatment": "treatment", "context": "treatment", "compare": "treatment", "v2": "treatment",
             "plan": "treatment", "new": "treatment", "on": "treatment", "b": "treatment",
             "control": "control", "v1": "control", "old": "control", "off": "control", "a": "control"}

# A test read from page events (Mixpanel) follows event_read.py: version from the account id, every view kept, members
# out by purchase log at first view, late-id couples out by account.

# Exposure of entries written before the ``exposure`` key existed and whose ``surfaces`` list more than the
# place the test really ran. Move each to a registry ``exposure`` key, then delete it here.
KNOWN_EXPOSURE = {
    # live on the Planner page only (registry dates_note: page instance 77034263, published 2026-11-17)
    "EXP-2026-11-planner-context-first": {"source": "xano:table79", "surfaces": ["planner"]},
}


# ---------------------------------------------------------------------------
# exposure rule
# ---------------------------------------------------------------------------

def _canon(entry):
    key = str((entry.get("metric") or {}).get("key") or "") if isinstance(entry.get("metric"), dict) else ""
    return _m.ALIASES.get(key, key)


def _planner_surfaces(entry):
    names = {str(s).lower() for s in spec._as_list(entry.get("surfaces"))}
    out = []
    if any(n == "weweb:planner" for n in names):
        out.append("planner")
    if any(n.startswith(("weweb:bubble", "weweb:ask-tulle")) for n in names):
        out.append("bubble")
    return out or None


_SOURCE = re.compile(r"^(xano:table(1|79)|(mixpanel|brevo):(?P<event>[a-z0-9]+(?:[_.\-][a-z0-9]+)+))$")
_PLACEHOLDER = re.compile(r"\b(todo|tbd|tbc|none|na|null|unknown|event|everyone|everybody|all|any|default|placeholder|fixme|xxx|"
                          r"test|later|nothing|yet|pending|missing|soon|no|not|never|undecided|coming|here|something)\b")


def valid_source(source):
    """A real record of who saw the test: xano:table1 (sign-ups), xano:table79 (Planner use), or a named event,
    mixpanel:<event_name> / brevo:<event_name> (one token of two words or more, no placeholder words)."""
    g = _SOURCE.match(str(source or "").strip().lower())
    return bool(g) and not (g.group("event") and _PLACEHOLDER.search(re.sub(r"[_.\-]", " ", g.group("event"))))


def explicit_source(entry):
    ex = (entry or {}).get("exposure")
    return ex if isinstance(ex, str) else (ex or {}).get("source") if isinstance(ex, dict) else None


def exposure_rule(entry):
    """{"source", "surfaces", "kinds", "explicit"} or None when nothing says who was exposed ("none", "todo",
    "everyone by default" and the like say nothing)."""
    e = entry if isinstance(entry, dict) else {}
    ex = e.get("exposure")
    if isinstance(ex, str):
        ex = {"source": ex}
    explicit = ex is not None and ex != {}
    if explicit and not (isinstance(ex, dict) and valid_source(ex.get("source"))):
        return None
    if not explicit and e.get("id") in KNOWN_EXPOSURE:
        ex = KNOWN_EXPOSURE[e["id"]]
    if isinstance(ex, dict) and str(ex.get("source") or "").strip():
        return {"source": str(ex["source"]).strip().lower(),
                "surfaces": [str(s).strip().lower() for s in spec._as_list(ex.get("surfaces"))] or None,
                "kinds": [str(s).strip().lower() for s in spec._as_list(ex.get("kinds"))] or None,
                "arm_field": ex.get("arm_field"), "explicit": explicit}
    key = _canon(e)
    if str(e.get("reporting") or "").lower() != "auto" or key in ("", "manual"):
        return None
    if key in USAGE_METRICS or (key == "gate_hitters_paid" and
                                ((e.get("metric") or {}).get("params") or {}).get("gate") == "planner"):
        return {"source": "xano:table79", "surfaces": _planner_surfaces(e), "kinds": None, "arm_field": None,
                "explicit": False}
    return {"source": "xano", "surfaces": None, "kinds": None, "arm_field": None, "explicit": False}


def is_usage_source(rule):
    return bool(rule) and rule["source"] in ("xano:table79", "xano:79", "table79")


def is_signup_source(rule):
    return bool(rule) and rule["source"] in ("xano:table1", "xano:1", "table1", "xano:signup")


# ---------------------------------------------------------------------------
# which entries run, which views they need
# ---------------------------------------------------------------------------

def wants_run(entry):
    """Manual entries are computed for their guardrails (any type) and for exposure / payments (splits)."""
    return spec.normalise(entry).get("_assignment_type") == "split" or any(
        isinstance(g, dict) and g.get("key") and g["key"] != "manual" for g in entry.get("guardrails") or []
        if isinstance(entry.get("guardrails"), list))


def _window(ctx, entry):
    a = entry.get("assignment") if isinstance(entry.get("assignment"), dict) else {}
    if not a.get("type") and spec.normalise(entry).get("_assignment_type") == "split":
        entry = dict(entry, assignment=dict(a, type="split"))
    return _m.build_window(entry, ctx.now_ms)


def want_views(ctx, entry):
    w = _window(ctx, entry)
    for g in entry.get("guardrails") or []:
        if isinstance(g, dict) and not _m.is_auto(entry):
            need = _m.NEEDS.get(g.get("key"))
            if need and not (g.get("params") or {}).get("cohort"):
                for view, since in need(g.get("params") or {}, w).items():
                    ctx.want(view, since)
    if w["type"] == "split":
        ctx.want("payments", w["start_ms"])
        ctx.want("users", w["start_ms"] - WHO_LOOKBACK_DAYS * DAY)
        rule = exposure_rule(entry)
        if is_usage_source(rule):
            ctx.want("usage", w["start_ms"])


# ---------------------------------------------------------------------------
# units: exposure, forced ids, repeats
# ---------------------------------------------------------------------------

def _forced(assignment):
    a = assignment or {}
    return {_x.to_int(i) for i in (a.get("force_ids") or a.get("forced_ids") or [])}


def who_map(ctx):
    """{uid: in-memory person key} for every account row loaded this run."""
    out = {}
    for name in ("users", "offer_holders", "pricing_viewers"):
        for r in (getattr(ctx, "_loaded", {}).get(name) or (None, {}))[1].values():
            if r.get("_who"):
                out[_x.to_int(r.get("id"))] = r["_who"]
    for r in (getattr(ctx, "_loaded", {}).get("usage") or (None, {}))[1].values():
        if r.get("_who"):
            out.setdefault(_x.to_int(r.get("user_id")), r["_who"])
    return out


def recorded_arms(ctx, window):
    """{uid: set of arms the product recorded} from usage rows that carry an arm field (none today)."""
    out = {}
    fields = ([window["exposure"]["arm_field"]] if (window.get("exposure") or {}).get("arm_field") else ARM_FIELDS)
    for r in (getattr(ctx, "_loaded", {}).get("usage") or (None, {}))[1].values():
        t = _x.to_ms(r.get("created_at"))
        if t is None or not (window["start_ms"] <= t < window["end_ms"]):
            continue
        surf = (window.get("exposure") or {}).get("surfaces")
        if surf and not _m.surface_allowed(r.get("surface"), surf):
            continue
        for f in fields:
            arm = ARM_WORDS.get(str(r.get(f) or "").strip().lower())
            if arm:
                out.setdefault(_x.to_int(r.get("user_id")), set()).add(arm)
    return out


def prepare_units(units, window, mark=True):
    """Units of one split metric -> (units that may be read, extras).

    extras: ``exposure_by_day`` / ``exposed`` (every exposed couple by arm, before exclusions, for the balance
    check), ``mixing`` (arm switchers + repeat accounts, all left out of the read) and ``last_exposure_at``.
    """
    ctx = window.get("_ctx")
    forced = _forced(window.get("assignment"))
    only = window.get("_only")                  # guardrails: the primary metric's exposed couples
    seen, kept, forced_n, rows = {}, [], 0, 0
    for u in sorted(units, key=lambda x: x["anchor"]):
        arm = _m._arm_for(u, window)
        if arm is None or (only is not None and u["uid"] not in only):
            continue
        if u["uid"] in forced:
            forced_n += 1
            continue
        rows += 1
        if u["uid"] in seen:                    # one couple, one unit: the first one counts
            continue
        seen[u["uid"]] = arm
        kept.append(u)
    expo, exposed, last = {}, {"control": 0, "treatment": 0}, None
    for u in kept:
        d = _m.day_of(u["anchor"])
        expo.setdefault(d, {"control": 0, "treatment": 0})[seen[u["uid"]]] += 1
        exposed[seen[u["uid"]]] += 1
        last = u["anchor"] if last is None else max(last, u["anchor"])

    switch = set()
    rec = recorded_arms(ctx, window) if ctx is not None else {}
    for uid, arm in seen.items():
        r = rec.get(uid)
        if r and (len(r) > 1 or arm not in r):
            switch.add(uid)
    who = who_map(ctx) if ctx is not None else {}
    groups = {}
    for uid in seen:
        if who.get(uid):
            groups.setdefault(who[uid], []).append(uid)
    repeat, across = set(), 0
    for uids in groups.values():
        if len(uids) > 1:
            repeat.update(uids)
            across += 1 if len({seen[u] for u in uids}) > 1 else 0
    bad = switch | repeat
    mixing = {"exposed": len(seen), "switched": len(switch), "repeat_accounts": len(repeat),
              "repeat_couples_both_arms": across, "both_arms": len(switch) + across, "excluded": len(bad),
              "share": round(len(bad) / len(seen), 6) if seen else 0.0,
              "checked": sum(1 for u in seen if who.get(u)), "arm_recorded": sum(1 for u in seen if rec.get(u)),
              "extra_rows": rows - len(seen), "forced": forced_n}
    if mark and window.get("primary"):
        window["_exposed"] = {u["uid"]: (u["anchor"], seen[u["uid"]]) for u in kept if u["uid"] not in bad}
        window["_units"] = {u["uid"]: (seen[u["uid"]], bool(u.get("conv")), bool(u.get("pending")))
                            for u in kept if u["uid"] not in bad}
    extras = {"exposure_by_day": dict(sorted(expo.items())), "exposed": exposed, "mixing": mixing,
              "last_exposure_at": _m._dt.datetime.fromtimestamp(last / 1000, _m._dt.timezone.utc).isoformat()
              if last else None}
    return [u for u in kept if u["uid"] not in bad], extras


def exposure_units(ctx, window, rule):
    """First exposure per couple for a rule the engine can read itself, else None."""
    first = {}
    if is_usage_source(rule):
        kinds = set(rule["kinds"] or _m.PLANNER_USE_KINDS)
        for uid, t, kind, status, _r in _m._usage_rows(ctx, window["start_ms"]):
            if kind in kinds and status == "ok" and window["start_ms"] <= t < window["end_ms"]:
                first.setdefault(uid, t)
    elif is_signup_source(rule):
        for uid, t, _u in _m._new_users(ctx, window):
            first[uid] = t
    else:
        return None
    return [{"uid": u, "anchor": t, "pending": False, "conv": False} for u, t in first.items()]


# ---------------------------------------------------------------------------
# payments by arm
# ---------------------------------------------------------------------------

def payments_by_arm(ctx, window, exposed=None, params=None):
    """Distinct paying couples and payments per arm since exposure (or since the start, by assigned arm, when the
    engine can't see who was exposed). Never a before/after."""
    params = params or {}
    types = [str(t).lower() for t in (params.get("types") or ([params["plan"]] if params.get("plan") else []))]
    internal = ctx.internal_uids()
    forced = _forced(window["assignment"])
    payers = {"control": set(), "treatment": set()}
    count = {"control": 0, "treatment": 0}
    unassigned = 0
    for p in ctx.view("payments", window["start_ms"]):
        uid, t = _x.to_int(p.get("Client_Reference_ID")), _x.to_ms(p.get("Time_of_Payment"))
        if t is None or not (window["start_ms"] <= t < window["end_ms"]) or p.get("is_internal") or uid in internal:
            continue
        if types and not any(str(p.get("Type") or "").lower().startswith(x) for x in types):
            continue
        if exposed is not None:
            hit = exposed.get(uid)
            if not hit or t < hit[0]:
                continue
            arm = hit[1]
        else:
            arm = _m.split_arm(uid, window["assignment"]) if uid and uid not in forced else None
            if arm is None:
                unassigned += 1
                continue
        payers[arm].add(uid)
        count[arm] += 1
    out = {"basis": "exposed" if exposed is not None else "assigned",
           "arms": {k: {"payers": len(payers[k]), "payments": count[k]} for k in payers},
           "unassigned": unassigned}
    if exposed is not None:
        for k in out["arms"]:
            out["arms"][k]["n"] = sum(1 for _t, a in exposed.values() if a == k)
    pct = window["assignment"].get("pct")
    out["treatment_share"] = (float(pct) / 100.0 if pct is not None and
                              window["assignment"].get("rule") == "hash_pct" else 0.5)
    return out


# ---------------------------------------------------------------------------
# one experiment
# ---------------------------------------------------------------------------

def _guard_row(g, gr):
    row = {"key": g["key"]}
    for k in ("count", "arms", "error", "manual", "definition"):
        if k in gr:
            row[k] = gr[k]
    return row


def run_experiment(ctx, entry):
    split = spec.normalise(entry).get("_assignment_type") == "split"
    auto = bool(_m.is_auto(entry))
    m = entry.get("metric") if isinstance(entry.get("metric"), dict) else {}
    rule = exposure_rule(entry)
    w = _window(ctx, entry)
    cctx = ctx
    if split:
        rd = readlock.read_date(entry)
        end = _m.parse_ms(rd.isoformat()) if rd else None
        if end and end < ctx.now_ms:                       # the read is as of its date, whenever the run happens
            cctx = copy.copy(ctx)
            cctx.now_ms = max(end, w["start_ms"])
            w = _window(cctx, entry)
        w.update(v2=True, _ctx=cctx, primary=True, exposure=rule)
    surfaces = rule["surfaces"] if rule and is_usage_source(rule) and (split or rule["explicit"]) else None
    cctx.exposure_surfaces = surfaces
    try:
        if split and not _safe_rule(w):
            return {"error": "unsupported split rule %r (supported: %s)" % (
                w["assignment"].get("rule"), ", ".join(_m.SPLIT_RULES)), "metric": m.get("key"), "guardrails": []}
        _load_people(cctx, w, split)
        ro = {}
        if auto:
            if split and _canon(entry) in PAYMENT_COUNTS + ("nps_ratings_per_day",):
                ro = {"error": "a whole-site daily count can't be read inside a split (use a per-couple metric)",
                      "metric": m.get("key")}
            else:
                if split and rule and rule["explicit"] and not _matches_metric(entry, rule):
                    ex = exposure_units(cctx, w, rule)
                    if ex is not None:
                        w["_only"] = {u["uid"] for u in ex if _m._arm_for(u, w)}
                ro = _m.compute(cctx, m.get("key"), m.get("params") or {}, w)
        elif split:
            ex = exposure_units(cctx, w, rule) if rule else None
            ro = {"source": "manual", "metric": "manual"}
            if ex is not None:
                _u, more = prepare_units(ex, w)
                ro.update(more)
        else:
            ro = {"source": "manual", "metric": "manual"}
        ro.setdefault("computed_at", _m._dt.datetime.fromtimestamp(
            cctx.now_ms / 1000, _m._dt.timezone.utc).isoformat())
        exposed = w.get("_exposed")
        if split:
            ro["payments"] = _payments(cctx, w, exposed)
        guards = []
        for g in entry.get("guardrails") or []:
            if not isinstance(g, dict) or not g.get("key"):
                continue
            p = g.get("params") or {}
            if split and g["key"] in PAYMENT_COUNTS:
                row = {"key": g["key"], "payment": True}
                try:
                    if p.get("cohort"):
                        raise _m.UnsupportedParam("a wedding-location cohort inside a split")
                    pay = payments_by_arm(cctx, w, exposed, p)
                    row["payments"] = pay
                    if exposed is not None:
                        row["arms"] = {k: {"n": v["n"], "conv": v["payers"]} for k, v in pay["arms"].items()}
                except _x.BudgetExceeded:
                    raise
                except Exception as exc:
                    row["error"] = "%s: %s" % (type(exc).__name__, exc)
                guards.append(row)
                continue
            gw = dict(w, primary=False, _only=set(exposed) if exposed is not None else None) if split else w
            row = _guard_row(g, _m.compute(cctx, g["key"], p, gw))
            if g["key"] in PAYMENT_COUNTS:
                row["payment"] = True
            if split and "count" in row:                   # never before/after inside a split
                row.pop("count")
                row["error"] = "a whole-site daily count can't be read inside a split"
            guards.append(row)
        ro["guardrails"] = guards
        if split and w.get("_units") is not None:
            if not hasattr(ctx, "v2_units"):
                ctx.v2_units = {}
            ctx.v2_units[entry.get("id")] = w["_units"]
        return ro
    finally:
        cctx.exposure_surfaces = None
        ctx.exposure_surfaces = None


def _safe_rule(w):
    return w["assignment"].get("rule") in _m.SPLIT_RULES


def _matches_metric(entry, rule):
    """The metric's own cohort already is the exposure rule (usage metric + usage rule)."""
    return is_usage_source(rule) and (_canon(entry) in USAGE_METRICS) and not rule["kinds"] or (
        is_signup_source(rule) and _canon(entry) in ("new_paid_within_days", "pdf_open_rate_new",
                                                     "pdf_open_within_1d"))


def _load_people(ctx, w, split):
    """Accounts for the repeat check (and internal flags). Never fails a metric."""
    if not split:
        return
    try:
        ctx.view("users", w["start_ms"] - WHO_LOOKBACK_DAYS * DAY)
    except _x.BudgetExceeded:
        raise
    except Exception:
        pass


def _payments(ctx, w, exposed):
    try:
        return payments_by_arm(ctx, w, exposed)
    except _x.BudgetExceeded:
        raise
    except Exception as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def attach_overlaps(ctx, entries, readouts):
    """Couples two splits share, and the 2x2 cells (hidden by v2.py until that test's read)."""
    units = getattr(ctx, "v2_units", {})
    ids = [e.get("id") for e in entries if e.get("id") in units]
    for a in ids:
        ro = readouts.get(a)
        if not isinstance(ro, dict):
            continue
        out = {}
        for b in ids:
            if a == b:
                continue
            shared = set(units[a]) & set(units[b])
            if not shared:
                continue
            cells = {}
            for uid in shared:
                arm, conv, pend = units[a][uid]
                if pend:
                    continue
                c = cells.setdefault("%s|%s" % (arm, units[b][uid][0]), {"n": 0, "conv": 0})
                c["n"] += 1
                c["conv"] += 1 if conv else 0
            out[b] = {"shared": len(shared), "cells": cells}
        if out:
            ro["overlap"] = out
