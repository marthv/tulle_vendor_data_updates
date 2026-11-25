"""Registry entry normalisation and validation (FRAMEWORK.md sections 1-4).

* :func:`normalise` copies an entry and smooths over v1/v2 differences (list fields that
  are plain strings, results[].counts shapes, legacy statuses, the free-text v1 ``design``).
  It never removes keys; derived values are added under ``_``-prefixed keys.
* :func:`validate` checks an entry against the seven questions of section 1 and returns a
  list of issue dicts ``{"severity", "question", "field", "message"}``.
  - severity "error": blocks "live" (the engine shows **Spec incomplete**).
  - severity "warn": worth fixing, never blocking.
  - severity "legacy": a pre-v2 decided entry; not held to the seven questions.
  Enforcement applies to status live/planned/paused, or any entry with framework_version 2
  that is not decided/draft. Drafts are not enforced (section 3: spec may be incomplete).
* :func:`decision_rule` returns the effective thresholds: tier defaults (section 4 table)
  overridden by the entry's own ``decision_rule``.
"""
from __future__ import annotations

import copy
import datetime as _dt
import re

TIERS = ("money", "ux", "infra")
DEFAULT_TIER = "ux"
TIER_DEFAULTS = {
    "money": {"ship_p": 0.95, "stop_p": 0.10, "max_loss": 0.002, "futility_min_progress": 0.5},
    "ux": {"ship_p": 0.90, "stop_p": 0.10, "max_loss": 0.005, "futility_min_progress": 0.5},
    "infra": {"ship_p": 0.90, "stop_p": 0.10, "max_loss": 0.005, "futility_min_progress": 0.5},
}
MIN_DAYS_TO_CALL = 7

LIST_FIELDS = ("themes", "surfaces", "guardrails", "links", "confounds_noted", "risks", "results")
ASSIGNMENT_TYPES = ("pre_post", "split", "benchmark", "rollout")
SPLIT_RULES = ("user_id_parity", "hash_pct")
DIRECTIONS = ("increase", "decrease")
REPORTING = ("auto", "manual")

DECIDED_STATUSES = ("shipped", "reverted", "shelved", "inconclusive", "concluded", "ended", "decided")
KNOWN_STATUSES = ("draft", "planned", "live", "paused") + DECIDED_STATUSES
ENFORCED_STATUSES = ("live", "planned", "paused")

ID_RE = re.compile(r"^EXP-\d{4}-\d{2}-[a-z0-9][a-z0-9-]*$")


# ---------------------------------------------------------------------------
# small parsers
# ---------------------------------------------------------------------------

def parse_date(value):
    """ISO date or datetime string -> datetime.date (UTC date part), else None."""
    if value in (None, ""):
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    s = str(value).strip()
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _nonempty(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _num(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _arm(value):
    """Arm counts -> {"n": int, "conv": int} or None. Accepts dict or [n, conv]."""
    if isinstance(value, dict):
        n = value.get("n", value.get("exposed"))
        conv = value.get("conv", value.get("conversions", value.get("converted")))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        n, conv = value[0], value[1]
    else:
        return None
    n, conv = _num(n), _num(conv)
    if n is None:
        return None
    return {"n": int(n), "conv": int(conv or 0)}


def normalise_counts(counts):
    """Normalise a results[].counts / readout arms payload.

    Rate shape: {"control": {"n", "conv"}, "treatment": {...}} (either arm may be absent;
    a bare {"n", "conv"} becomes the treatment arm, for benchmark experiments).
    Count shape: {"pre": {"count", "days"}, "post": {"count", "days"}} passes through.
    Anything else returns None.
    """
    if not isinstance(counts, dict):
        return None
    if "pre" in counts or "post" in counts:
        out = {}
        for k in ("pre", "post"):
            v = counts.get(k)
            if isinstance(v, dict):
                out[k] = {"count": int(_num(v.get("count")) or 0), "days": _num(v.get("days")) or 0}
        return {"count": out} if out else None
    arms = {}
    for k in ("control", "treatment"):
        a = _arm(counts.get(k))
        if a:
            arms[k] = a
    if not arms and "n" in counts:
        a = _arm(counts)
        if a:
            arms["treatment"] = a
    return {"arms": arms} if arms else None


def lifecycle(status):
    """Map any registry status to draft/planned/live/paused/decided (section 3)."""
    s = str(status or "").strip().lower()
    if s in DECIDED_STATUSES:
        return "decided"
    if s in ("draft", "planned", "live", "paused"):
        return s
    return "unknown"


def _derive_assignment_type(entry):
    a = entry.get("assignment")
    if isinstance(a, dict) and a.get("type"):
        return str(a["type"])
    design = str(entry.get("design") or "").lower()
    if not design:
        return None
    if design.startswith("a/b") or "split" in design:
        return "split"
    if "pre/post" in design or "before/after" in design:
        return "pre_post"
    return None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def normalise(entry):
    """Return a normalised deep copy of a registry entry (section 2: v2 never renames v1).

    Adds: ``_lifecycle``, ``_outcome`` (outcome, or inferred from a decided status),
    ``_assignment_type`` (from assignment.type or the v1 ``design`` text), ``_legacy``
    (framework_version != 2). List fields become lists; each result gets ``_counts``.
    """
    e = copy.deepcopy(entry) if isinstance(entry, dict) else {}
    for f in LIST_FIELDS:
        if f in e or f in ("results",):
            e[f] = _as_list(e.get(f))
    try:
        e["framework_version"] = int(e.get("framework_version") or 1)
    except (TypeError, ValueError):
        e["framework_version"] = 1
    e["_legacy"] = e["framework_version"] != 2
    e["_lifecycle"] = lifecycle(e.get("status"))
    outcome = e.get("outcome")
    if not outcome and str(e.get("status") or "").lower() in ("shipped", "reverted", "shelved", "inconclusive"):
        outcome = str(e["status"]).lower()
    e["_outcome"] = outcome or None
    e["_assignment_type"] = _derive_assignment_type(e)
    results = []
    for r in e["results"]:
        if isinstance(r, dict):
            r = dict(r)
            r["_counts"] = normalise_counts(r.get("counts"))
        else:
            r = {"note": str(r), "_counts": None}
        results.append(r)
    e["results"] = results
    if not isinstance(e.get("audience"), dict):
        aud = e.get("audience")
        e["audience"] = {"segment": aud} if _nonempty(aud) else {}
    if not isinstance(e.get("metric"), dict):
        e["metric"] = {}
    return e


def tier(entry):
    t = str((entry or {}).get("tier") or "").lower()
    return t if t in TIERS else DEFAULT_TIER


def decision_rule(entry):
    """Effective decision thresholds: tier default (section 4) overridden per experiment."""
    rule = dict(TIER_DEFAULTS[tier(entry)])
    own = (entry or {}).get("decision_rule")
    if isinstance(own, dict):
        for k, v in own.items():
            n = _num(v)
            if n is not None:
                rule[k] = n
    return rule


def daily_exposed_per_arm(entry, observed=None):
    """Daily exposure per arm for sample planning.

    Split tests divide ``audience.est_daily_exposed`` across two arms; pre/post, benchmark
    and rollout accrue one arm at a time (the pre window / benchmark is already fixed), so
    the full daily exposure counts. ``observed`` (per arm per day) wins when given.
    """
    if observed is not None:
        return observed
    e = entry if "_assignment_type" in (entry or {}) else normalise(entry)
    total = _num((e.get("audience") or {}).get("est_daily_exposed"))
    if total is None:
        return None
    return total / 2.0 if e.get("_assignment_type") == "split" else total


def plan(entry, observed_daily_per_arm=None):
    """Sample plan for an entry (section 4), or None when there is no metric to plan for."""
    from . import stats  # local import keeps module import cheap
    e = entry if "_assignment_type" in (entry or {}) else normalise(entry)
    m = e.get("metric") or {}
    from . import readlock
    return stats.sample_plan(m.get("baseline"), m.get("mde_rel"), m.get("mde_abs"),
                             daily_exposed_per_arm(e, observed_daily_per_arm), m.get("direction"),
                             two_sided=readlock.enabled() and e.get("_assignment_type") == "split")


def is_enforced(entry):
    e = entry if "_lifecycle" in (entry or {}) else normalise(entry)
    lc = e["_lifecycle"]
    if lc in ("decided", "draft"):
        return False
    return lc in ENFORCED_STATUSES or not e["_legacy"]


def _issue(severity, question, field, message):
    return {"severity": severity, "question": question, "field": field, "message": message}


def validate(entry):
    """Validate one entry against the seven questions (section 1). Returns a list of issues.

    Questions -> fields: 1 name/what_changed/links, 2 surfaces/audience, 3 hypothesis,
    4 tests, 5 metric/guardrails/decision_rule/audience.est_daily_exposed,
    6 start_date/planned_days/review_date/max_days, 7 reporting. Plus assignment, rollback
    and tier (question None). ``links`` and the "If X then Y because W" wording are warnings.
    A plan whose days_needed exceeds max_days gets the warning "Can't reach a verdict".
    """
    e = normalise(entry)
    issues = []
    eid = e.get("id")
    if not _nonempty(eid):
        issues.append(_issue("error", None, "id", "Missing id"))
    elif not ID_RE.match(str(eid)):
        issues.append(_issue("warn", None, "id", "id should look like EXP-YYYY-MM-slug"))
    status = str(e.get("status") or "").lower()
    if status not in KNOWN_STATUSES:
        issues.append(_issue("error", None, "status", f"Unknown status {e.get('status')!r}"))

    lc = e["_lifecycle"]
    if lc == "decided" and e["_legacy"]:
        issues.append(_issue("legacy", None, None,
                             "Legacy entry (pre-v2, decided): not held to the seven questions"))
        return issues
    if not is_enforced(e):
        return issues

    err = lambda q, f, msg: issues.append(_issue("error", q, f, msg))  # noqa: E731
    warn = lambda q, f, msg: issues.append(_issue("warn", q, f, msg))  # noqa: E731
    atype = e.get("_assignment_type")
    assignment = e.get("assignment") if isinstance(e.get("assignment"), dict) else {}

    # 1. What is the experiment?
    if not _nonempty(e.get("name")):
        err(1, "name", "Missing name")
    if not _nonempty(e.get("what_changed")):
        err(1, "what_changed", "Missing what_changed (the concrete change couples see)")
    if not e.get("links"):
        warn(1, "links", "No links (commit, publish id, endpoint)")

    # 2. What does it impact?
    if not e.get("surfaces"):
        err(2, "surfaces", "Missing surfaces[] (systems touched)")
    if not _nonempty((e.get("audience") or {}).get("segment")):
        err(2, "audience.segment", "Missing audience.segment (who sees it)")

    # 3. Hypothesis
    hyp = e.get("hypothesis")
    if not _nonempty(hyp):
        err(3, "hypothesis", "Missing hypothesis")
    else:
        low = str(hyp).lower()
        if not ("if " in low and "then" in low and "because" in low):
            warn(3, "hypothesis", 'Hypothesis should read "If we X, then Y moves Z, because W"')

    # 4. What is it trying to test?
    if not _nonempty(e.get("tests")):
        err(4, "tests", "Missing tests (the single question it answers)")

    # 5. Success metric
    m = e.get("metric") or {}
    if atype == "rollout":
        if not e.get("guardrails"):
            err(5, "guardrails", "A rollout has no comparison, so it needs guardrails[]")
    else:
        if not _nonempty(m.get("key")):
            err(5, "metric.key", "Missing metric.key (catalog key or 'manual')")
        if str(m.get("direction") or "").lower() not in DIRECTIONS:
            err(5, "metric.direction", "metric.direction must be 'increase' or 'decrease'")
        if _num(m.get("mde_rel")) is None and _num(m.get("mde_abs")) is None:
            err(5, "metric.mde_rel", "Missing smallest lift worth having (metric.mde_rel or mde_abs)")
        if atype != "benchmark":
            b = _num(m.get("baseline"))
            if b is None:
                err(5, "metric.baseline", "Missing metric.baseline (rate before)")
            elif not 0 < b < 1:
                warn(5, "metric.baseline", "metric.baseline should be a rate between 0 and 1")
    for i, g in enumerate(e.get("guardrails") or []):
        if not isinstance(g, dict) or not _nonempty(g.get("key")):
            err(5, f"guardrails[{i}]", "Guardrail needs a key")
        elif _num(g.get("max_drop_rel")) is None and _num(g.get("max_rise_rel")) is None:
            err(5, f"guardrails[{i}]", "Guardrail needs max_drop_rel or max_rise_rel")
    if tier(e) == "money" and not e.get("guardrails"):
        warn(5, "guardrails", "Money-tier experiment without guardrails")
    if _num((e.get("audience") or {}).get("est_daily_exposed")) is None:
        err(5, "audience.est_daily_exposed", "Missing audience.est_daily_exposed (couples impacted per day)")
    if "decision_rule" in e and not isinstance(e.get("decision_rule"), dict):
        err(5, "decision_rule", "decision_rule must be an object")

    # 6. How long will it run?
    sd = parse_date(e.get("start_date"))
    if lc in ("live", "paused") and sd is None:
        err(6, "start_date", "Live experiment needs start_date (real UTC live date)")
    elif e.get("start_date") and sd is None:
        err(6, "start_date", "start_date is not an ISO date")
    pdays = _num(e.get("planned_days"))
    mdays = _num(e.get("max_days"))
    if pdays is None or pdays <= 0:
        err(6, "planned_days", "Missing planned_days")
    if parse_date(e.get("review_date")) is None:
        err(6, "review_date", "Missing review_date")
    if mdays is None or mdays <= 0:
        err(6, "max_days", "Missing max_days (hard stop)")
    elif pdays and mdays < pdays:
        err(6, "max_days", "max_days is shorter than planned_days")

    # 7. How is it reported?
    rep = str(e.get("reporting") or "").lower()
    if rep not in REPORTING:
        err(7, "reporting", "reporting must be 'auto' or 'manual'")
    elif rep == "auto" and str(m.get("key") or "") == "manual":
        err(7, "reporting", "reporting is 'auto' but metric.key is 'manual'")

    # Plus: assignment, rollback, tier
    if not assignment:
        err(None, "assignment", "Missing assignment (how couples are put in arms)")
    elif atype not in ASSIGNMENT_TYPES:
        err(None, "assignment.type", f"assignment.type must be one of {', '.join(ASSIGNMENT_TYPES)}")
    elif atype == "pre_post":
        pre = assignment.get("pre")
        if not (isinstance(pre, (list, tuple)) and len(pre) == 2 and all(parse_date(x) for x in pre)):
            err(None, "assignment.pre", "pre_post needs assignment.pre = [start, end]")
        if parse_date(assignment.get("post_start")) is None:
            err(None, "assignment.post_start", "pre_post needs assignment.post_start")
    elif atype == "split":
        if assignment.get("rule") not in SPLIT_RULES:
            err(None, "assignment.rule", f"split needs rule in {', '.join(SPLIT_RULES)}")
        elif assignment.get("rule") == "hash_pct" and not _nonempty(assignment.get("salt")):
            err(None, "assignment.salt", "hash_pct split needs a per-experiment salt")
    elif atype == "benchmark":
        if normalise_counts(assignment.get("benchmark") or {}) is None:
            err(None, "assignment.benchmark", 'benchmark needs assignment.benchmark = {"n", "conv"}')
    if not _nonempty(e.get("rollback")):
        err(None, "rollback", "Missing rollback (how to undo it)")
    if str(e.get("tier") or "").lower() not in TIERS:
        err(None, "tier", "tier must be money, ux or infra")

    # Sample plan: can it ever reach a verdict?
    if atype != "rollout":
        p = plan(e)
        if p.get("days_needed") is not None and mdays and p["days_needed"] > mdays:
            warn(5, "metric", f"Can't reach a verdict: needs ~{p['days_needed']} days "
                              f"({p['n_per_arm']} per arm) but max_days is {int(mdays)}")
    return issues


def blocking(issues):
    """Issues that make an experiment **Spec incomplete** (severity "error")."""
    return [i for i in issues or [] if i.get("severity") == "error"]
