"""Health and recommended action for a live experiment (FRAMEWORK.md section 6).

:func:`evaluate` returns exactly one health state - the first rule of the section 6 table
that applies, in table order:

  guardrail_breach > callable_win > callable_loss > needs_call > starved > no_data >
  zombie > manual_overdue > confounded > spec_incomplete > on_track

Notes on how the table is applied (choices where the spec is silent are marked CHOICE):

* Pre/post experiments with >= 2 overlapping ``changes[]`` are capped: they can never be
  ``callable_win`` (section 4 "capped at Confounded"); the remaining rules still run in order,
  so a confounded pre/post can be guardrail_breach / callable_loss / needs_call / etc.
* ``days_running`` = whole UTC days since start_date (start day = 0). The 7-day minimum for a
  call uses it.
* "50% of planned sample": sample progress = arm n / n_per_arm from the sample plan (split:
  smaller arm; pre/post and benchmark: the treatment arm). CHOICE: with no computable plan,
  time progress (days_running / planned_days) stands in.
* review_date missing -> start_date + planned_days when both exist. CHOICE.
* reporting missing (legacy) is read as manual, because v1 readouts are manual results[]. CHOICE.
* Count-model primary metrics (Gamma-Poisson) have no expected loss; the loss bar is treated
  as met for them. CHOICE.
* ``stale`` is true when ANY stale condition applies (needs_call >= 3 days overdue, starved,
  no_data, zombie, manual_overdue), not only when it is the winning state, so a stale
  experiment stays marked stale while e.g. a call is due. CHOICE. ``flags`` lists every
  rule that applied.
* Confounding (rule 2026-11-19, FRAMEWORK section 6): a changes[] entry inside the window counts
  against experiment E only when (a) its ``affects`` list names E, or (b) it has no ``affects``
  and its surface family overlaps E's ``surfaces`` (family from v2 ``source`` / ``surfaces``,
  else keywords in what/ref; no family on either side -> it does not count). E's own launch never
  counts: a change dated on E's start_date whose ``affects`` is exactly [E], or whose ref/what
  shares a token (commit sha, REC_* flag, epN/fnN, cacheVersion) with E's links/what_changed/
  what/dates_note/ref. The window is [pre start, today]: assignment.pre[0], else start_date -
  planned_days, else start_date - 14 days.
* Guardrails: ``guardrail_status`` lists every registry guardrail as ok | breach | unmonitored |
  manual. A guardrail the runner reported but could not compute (error, not manual) is
  ``unmonitored`` and raises the ``guardrail_unmonitored`` flag.
* A failed auto metric is ``no_data`` at once ("metric failed: ..."); it is stale only after 2
  failed runs. A cached readout (registry-only rebuild, ``cached: True``) is the last full run,
  which is already in history, so it is not counted again.
* Paused experiments: health ``paused`` ("Resume or shelve"); no starved / no_data / zombie /
  manual_overdue flags; stale after 14 days paused (paused_on / pause_date, else the last
  results[] date, else start_date).
"""
from __future__ import annotations

import datetime as _dt
import math
import re

from . import spec, stats

STATES = (
    "guardrail_breach", "callable_win", "callable_loss", "needs_call", "starved", "no_data",
    "zombie", "manual_overdue", "confounded", "spec_incomplete", "guardrail_unmonitored",
    "paused", "on_track",
)
RANK = {s: i for i, s in enumerate(STATES)}
ACTIONS = {
    "guardrail_breach": "Pause or revert now",
    "callable_win": "Call it: ship",
    "callable_loss": "Call it: revert",
    "needs_call": "Owner call due",
    "starved": "Shelve or redesign",
    "no_data": "Check tracking (is it switched on?)",
    "zombie": "Shelve",
    "manual_overdue": "Read it out",
    "confounded": "Evidence only; consider a split test",
    "spec_incomplete": "Complete the spec",
    "guardrail_unmonitored": "Fix the guardrail metric (it is not being watched)",
    "paused": "Resume or shelve",
    "on_track": "Keep running",
}
STALE_STATES = ("starved", "no_data", "zombie", "manual_overdue")
NEEDS_CALL_STALE_AFTER = 3
MIN_EXPOSURE_PER_ARM_DAY = 5
NO_DATA_FLAT_DAYS = 3
MANUAL_OVERDUE_DAYS = 7
GUARDRAIL_P = 0.9
DEFAULT_PRE_DAYS = 14
LOSS_FLOOR_P = 0.05
PAUSED_STALE_DAYS = 14
FAILED_RUNS_STALE = 2
PAUSE_SUPPRESSED = ("starved", "no_data", "zombie", "manual_overdue")


def _today(today):
    if today is None:
        return _dt.datetime.now(_dt.timezone.utc).date()
    d = spec.parse_date(today)
    if d is None:
        raise ValueError(f"bad today: {today!r}")
    return d


def _num(v):
    return spec._num(v)


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def score(entry, readout):
    """Posterior summary for the primary metric of one readout (section 4), or None.

    readout shapes: {"arms": {"control": {n, conv}, "treatment": {n, conv}}} (rate),
    {"arms": {"treatment": ...}} + assignment.benchmark (benchmark), or
    {"count": {"pre": {count, days}, "post": {count, days}}} (count, Gamma-Poisson).
    A readout may carry precomputed "stats", which are returned as-is.
    """
    if not isinstance(readout, dict):
        return None
    if isinstance(readout.get("stats"), dict):
        return readout["stats"]
    e = entry if "_assignment_type" in (entry or {}) else spec.normalise(entry)
    direction = str((e.get("metric") or {}).get("direction") or "increase").lower()
    arms = readout.get("arms") or {}
    c, t = arms.get("control"), arms.get("treatment")
    if c and t:
        return stats.beta_binomial(c.get("n"), c.get("conv"), t.get("n"), t.get("conv"),
                                   direction=direction)
    bench = readout.get("benchmark") or ((e.get("assignment") or {}).get("benchmark")
                                         if isinstance(e.get("assignment"), dict) else None)
    if t and isinstance(bench, dict):
        return stats.benchmark(t.get("n"), t.get("conv"), bench.get("n"), bench.get("conv"),
                               direction=direction)
    cnt = readout.get("count") or {}
    pre, post = cnt.get("pre"), cnt.get("post")
    if isinstance(pre, dict) and isinstance(post, dict):
        g = stats.gamma_poisson(pre.get("count"), pre.get("days"), post.get("count"), post.get("days"))
        p_up = g["p_post_gt_pre"]
        p_beat = None if p_up is None else (round(1 - p_up, 6) if direction.startswith("dec") else p_up)
        ratio = g.get("rate_ratio")
        return {
            "p_beat": p_beat, "lift_median_rel": None if ratio is None else round(ratio - 1, 6),
            "lift_ci95_rel": [None, None], "expected_loss_abs": None,
            "control_rate": g["pre_rate"], "treatment_rate": g["post_rate"], "model": "gamma_poisson",
        }
    return None


def evaluate_guardrails(entry, readout):
    """Evaluate guardrails in a readout against the entry's thresholds.

    readout["guardrails"]: list of {"key", and one of: "count": {pre, post} (Gamma-Poisson),
    "arms": {control, treatment} (Beta), "breached": bool / "p_breach": float (precomputed)}.
    Breach = P(metric moved past threshold) > guardrail "p" (default 0.9), threshold from
    the entry guardrail's max_drop_rel (default 0.25) or max_rise_rel.
    """
    out = []
    e_guards = {}
    for g in (entry or {}).get("guardrails") or []:
        if isinstance(g, dict) and g.get("key"):
            e_guards[g["key"]] = g
    for g in (readout or {}).get("guardrails") or []:
        if not isinstance(g, dict):
            continue
        key = g.get("key")
        cfg = e_guards.get(key, {})
        p_bar = _num(cfg.get("p")) or GUARDRAIL_P
        rise = _num(cfg.get("max_rise_rel"))
        drop = _num(cfg.get("max_drop_rel"))
        if drop is None and rise is None:
            drop = 0.25
        p = _num(g.get("p_breach"))
        breached = g.get("breached") if isinstance(g.get("breached"), bool) else None
        cnt, arms = g.get("count") or {}, g.get("arms") or {}
        if p is None and breached is None:
            if isinstance(cnt.get("pre"), dict) and isinstance(cnt.get("post"), dict):
                pre, post = cnt["pre"], cnt["post"]
                if rise is not None:
                    gp = stats.gamma_poisson(post.get("count"), post.get("days"),
                                             pre.get("count"), pre.get("days"),
                                             max_drop_rel=1 - 1 / (1 + rise))
                    p = gp["p_drop"]  # P(pre < post / (1+rise)) == P(post > (1+rise) pre)
                else:
                    p = stats.gamma_poisson(pre.get("count"), pre.get("days"), post.get("count"),
                                            post.get("days"), max_drop_rel=drop)["p_drop"]
            elif arms.get("control") and arms.get("treatment"):
                c, t = arms["control"], arms["treatment"]
                if rise is not None:
                    p = stats.beta_threshold_prob(c.get("n"), c.get("conv"), t.get("n"), t.get("conv"),
                                                  1 + rise, below=False, draws=20000)
                else:
                    p = stats.beta_threshold_prob(c.get("n"), c.get("conv"), t.get("n"), t.get("conv"),
                                                  1 - drop, below=True, draws=20000)
        if breached is None:
            breached = p is not None and p > p_bar
        out.append({"key": key, "breached": bool(breached), "p_breach": p, "p_bar": p_bar,
                    "max_drop_rel": drop if rise is None else None, "max_rise_rel": rise,
                    "error": g.get("error"), "manual": bool(g.get("manual"))})
    return out


def guardrail_status(entry, readout, guards, manual_reporting=False):
    """One row per registry guardrail: {key, status: ok|breach|unmonitored|manual, reason}."""
    by_key = {g.get("key"): g for g in guards or []}
    reported = isinstance((readout or {}).get("guardrails"), list)
    out = []
    for g in (entry or {}).get("guardrails") or []:
        if not isinstance(g, dict) or not g.get("key"):
            continue
        key = g["key"]
        row = by_key.get(key)
        if str(key) == "manual" or manual_reporting or (row and row.get("manual")):
            out.append({"key": key, "status": "manual",
                        "reason": "read out by hand" if not row or not row.get("error") else str(row["error"])})
        elif row is None:
            if reported:
                out.append({"key": key, "status": "unmonitored", "reason": "not computed by the engine"})
            else:
                out.append({"key": key, "status": "unmonitored" if (readout or {}).get("error") else "ok",
                            "reason": ("primary metric failed: %s" % readout.get("error"))
                            if (readout or {}).get("error") else "no guardrail data in this readout"})
        elif row.get("error"):
            out.append({"key": key, "status": "unmonitored", "reason": str(row["error"])})
        elif row.get("breached"):
            out.append({"key": key, "status": "breach", "reason": "P=%s > %s" % (row.get("p_breach"), row.get("p_bar"))})
        elif row.get("p_breach") is None:
            out.append({"key": key, "status": "unmonitored", "reason": "no data to compare"})
        else:
            out.append({"key": key, "status": "ok", "reason": "P=%s <= %s" % (row.get("p_breach"), row.get("p_bar"))})
    return out


# ---------------------------------------------------------------------------
# changes / exposure helpers
# ---------------------------------------------------------------------------

def window_start(entry):
    e = entry if "_assignment_type" in (entry or {}) else spec.normalise(entry)
    a = e.get("assignment") if isinstance(e.get("assignment"), dict) else {}
    pre = a.get("pre")
    if isinstance(pre, (list, tuple)) and pre and spec.parse_date(pre[0]):
        return spec.parse_date(pre[0])
    sd = spec.parse_date(e.get("start_date"))
    if sd is None:
        return None
    pdays = _num(e.get("planned_days"))
    return sd - _dt.timedelta(days=int(pdays) if pdays and pdays > 0 else DEFAULT_PRE_DAYS)


SOURCE_FAMILIES = {"weweb": "weweb", "rec-engine": "rec-engine", "railway-var": "rec-engine",
                   "railway": "rec-engine", "xano": "xano", "search": "search", "search-api": "search",
                   "vendor-portal": "vendor-portal", "brevo": "brevo", "stripe": "stripe",
                   "slack": "slack", "meta-pixel": "meta-pixel"}


def surface_family(surface):
    """'weweb:planner' -> 'weweb', 'xano:table79' -> 'xano', 'railway:...' -> 'rec-engine'."""
    p = str(surface or "").split(":", 1)[0].strip().lower()
    for pre in ("weweb", "rec-engine", "xano", "search", "vendor-portal"):
        if p.startswith(pre):
            return pre
    return SOURCE_FAMILIES.get(p, p or None)


def change_families(ch):
    """Surface families a changes[] entry touches: v2 source (split on '+') and surfaces; for an
    unmapped source (github, manual, none) the keyword families of what + ref."""
    from . import changefeed  # local: keeps health import-light
    fam = set()
    for src in str(ch.get("source") or "").lower().split("+"):
        src = src.strip()
        if src in SOURCE_FAMILIES:
            fam.add(SOURCE_FAMILIES[src])
    for s in ch.get("surfaces") or []:
        f = surface_family(s)
        if f:
            fam.add(f)
    if not fam:
        fam = changefeed._keyword_families(" ".join(str(ch.get(k) or "") for k in ("what", "ref")))
    return fam


_TOKEN_RE = re.compile(r"\b(?:[0-9a-f]{7,40}|REC_[A-Z0-9_]+|ep\s?\d+|fn\s?\d+|cacheVersion\s?\d+)\b", re.I)


def _tokens(text):
    return {re.sub(r"\s+", "", t).lower() for t in _TOKEN_RE.findall(str(text or ""))
            if not t.isdigit()}


def is_own_launch(ch, e, sd=None):
    sd = sd or spec.parse_date(e.get("start_date"))
    if sd is None or spec.parse_date(ch.get("date")) != sd:
        return False
    affects = ch.get("affects")
    if isinstance(affects, list) and affects == [e.get("id")]:
        return True
    mine = set()
    for k in ("links", "what_changed", "what", "dates_note", "ref"):
        v = e.get(k)
        mine |= _tokens(" ".join(map(str, v)) if isinstance(v, (list, tuple)) else v)
    return bool(mine & _tokens(" ".join(str(ch.get(k) or "") for k in ("ref", "what"))))


def overlapping_changes(entry, changes, today=None):
    """changes[] entries inside [window start, today] that confound this experiment (see the
    module docstring for the rule)."""
    today = _today(today)
    e = entry if "_assignment_type" in (entry or {}) else spec.normalise(entry)
    start = window_start(e)
    if start is None:
        return []
    sd = spec.parse_date(e.get("start_date"))
    efam = {surface_family(s) for s in e.get("surfaces") or []} - {None}
    out = []
    for ch in changes or []:
        if not isinstance(ch, dict):
            continue
        d0 = spec.parse_date(ch.get("date"))
        if d0 is None:
            continue
        d1 = spec.parse_date(ch.get("end_date")) or d0
        if not (d0 <= today and d1 >= start):
            continue
        affects = ch.get("affects")
        if isinstance(affects, list):
            if e.get("id") not in affects:
                continue
        elif not (efam & change_families(ch)):
            continue
        if is_own_launch(ch, e, sd):
            continue
        out.append(ch)
    return out


def _exposure_rows(readout):
    """exposure_by_day -> sorted [(date, {arm: n})]. Accepts a list of rows
    ({"date", "n"} or {"date", "control", "treatment"}) or a {date: n | {arm: n}} dict."""
    raw = (readout or {}).get("exposure_by_day")
    rows = []
    if isinstance(raw, dict):
        raw = [dict(v, date=k) if isinstance(v, dict) else {"date": k, "n": v} for k, v in raw.items()]
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        d = spec.parse_date(r.get("date"))
        if d is None:
            continue
        arms = {k: _num(r.get(k)) or 0 for k in ("control", "treatment") if k in r}
        if not arms:
            arms = {"total": _num(r.get("n")) or 0}
        rows.append((d, arms))
    rows.sort(key=lambda x: x[0])
    return rows


def _per_arm(arms, atype):
    if "total" in arms:
        return arms["total"] / (2.0 if atype == "split" else 1.0)
    if atype == "split" and "control" in arms and "treatment" in arms:
        return min(arms["control"], arms["treatment"])
    return arms.get("treatment", arms.get("control", 0))


def _progress_n(readout, atype):
    arms = (readout or {}).get("arms") or {}
    c, t = arms.get("control"), arms.get("treatment")
    if atype == "split" and c and t:
        return min(_num(c.get("n")) or 0, _num(t.get("n")) or 0)
    if t:
        return _num(t.get("n")) or 0
    return None


def _total_n(readout):
    arms = (readout or {}).get("arms") or {}
    if not arms:
        return None
    return sum((_num(a.get("n")) or 0) for a in arms.values() if isinstance(a, dict))


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

def evaluate(entry, readout, history=None, changes=None, today=None, spec_issues=None):
    """Health for one experiment (section 6). See module docstring for the rule details.

    entry      registry entry (raw or normalised)
    readout    {"arms"|"count", "exposure_by_day", "guardrails", "computed_at", "error", "stats"?}
    history    prior daily readout rows (history.py rows), oldest first
    changes    registry changes[]
    today      date / ISO string (default: UTC today)

    Returns {health, stale, action, reason, days_running, days_left, progress, ...extra}.
    Non-live entries get health None.
    """
    today = _today(today)
    e = entry if "_assignment_type" in (entry or {}) else spec.normalise(entry)
    readout = readout if isinstance(readout, dict) else {}
    history = [h for h in (history or []) if isinstance(h, dict)]
    atype = e.get("_assignment_type")
    rule = spec.decision_rule(e)
    issues = spec_issues if spec_issues is not None else spec.validate(e)

    out = {"health": None, "stale": False, "action": None, "reason": None,
           "days_running": None, "days_left": None, "progress": None, "progress_basis": None,
           "flags": [], "overdue_days": None, "projected_days": None, "stats": None,
           "guardrails": [], "overlapping_changes": [], "sample_plan": None,
           "decision_rule": rule, "evidence_label": None, "exposure_per_arm_day": None,
           "guardrail_status": [], "failed_runs": 0, "paused_days": None}
    if e.get("_lifecycle") not in ("live", "paused"):
        out["reason"] = f"not live ({e.get('status')})"
        return out

    sd = spec.parse_date(e.get("start_date"))
    days_running = (today - sd).days if sd else None
    out["days_running"] = days_running
    pdays = _num(e.get("planned_days"))
    mdays = _num(e.get("max_days"))
    if pdays and days_running is not None:
        out["days_left"] = max(int(pdays) - days_running, 0)

    st = None
    try:
        st = score(e, readout) if not readout.get("error") else None
    except Exception as exc:  # scoring must never break health
        readout = dict(readout, error=readout.get("error") or f"score failed: {exc}")
    out["stats"] = st
    guards = evaluate_guardrails(e, readout)
    out["guardrails"] = guards
    m0 = e.get("metric") or {}
    manual_rep = str(e.get("reporting") or "manual").lower() == "manual" or str(m0.get("key") or "") == "manual"
    gstatus = guardrail_status(e, readout, guards, manual_reporting=manual_rep)
    out["guardrail_status"] = gstatus
    overlaps = overlapping_changes(e, changes, today)
    out["overlapping_changes"] = overlaps
    confounded = atype == "pre_post" and len(overlaps) >= 2
    if atype == "pre_post":
        out["evidence_label"] = "evidence, not proof"

    # exposure
    rows = _exposure_rows(readout)
    last7 = [r for r in rows if today - _dt.timedelta(days=7) <= r[0] < today]
    # Arm counts are as of the readout, not today: a manual results[] entry can be weeks old.
    ro_day = spec.parse_date(readout.get("computed_at"))
    days_at_readout = (min(ro_day, today) - sd).days if (sd and ro_day) else days_running
    obs_rate = None
    if days_running is not None and days_running >= 7 and rows:
        obs_rate = sum(_per_arm(a, atype) for _, a in last7) / 7.0
    elif days_at_readout and days_at_readout > 0 and _progress_n(readout, atype) is not None:
        obs_rate = _progress_n(readout, atype) / float(days_at_readout)
    out["exposure_per_arm_day"] = None if obs_rate is None else round(obs_rate, 3)

    # sample plan + progress
    plan_rate = obs_rate if (obs_rate and days_running and days_running >= 7) else None
    m = e.get("metric") or {}
    baseline = m.get("baseline")
    if baseline is None and st and st.get("control_rate"):
        baseline = st["control_rate"]
    plan = stats.sample_plan(baseline, m.get("mde_rel"), m.get("mde_abs"),
                             spec.daily_exposed_per_arm(e, plan_rate))
    out["sample_plan"] = plan
    n_prog = _progress_n(readout, atype)
    if plan.get("n_per_arm") and n_prog is not None:
        out["progress"] = round(min(n_prog / plan["n_per_arm"], 1.0), 4)
        out["progress_basis"] = "sample"
    elif pdays and days_running is not None:
        out["progress"] = round(min(max(days_running / pdays, 0.0), 1.0), 4)
        out["progress_basis"] = "time"
    if plan.get("n_per_arm") and days_running is not None:
        rate = plan.get("daily_exposed_per_arm")
        remaining = max(plan["n_per_arm"] - (n_prog or 0), 0)
        if rate and rate > 0:
            base = days_at_readout if days_at_readout is not None else days_running
            out["projected_days"] = base + int(math.ceil(remaining / rate))

    flags = {}

    def flag(state, reason):
        flags.setdefault(state, reason)

    # 1 guardrail_breach
    breached = [g for g in guards if g["breached"]]
    if breached:
        g = breached[0]
        flag("guardrail_breach", f"Guardrail {g['key']} crossed its threshold (P={g['p_breach']})")

    p_beat = st.get("p_beat") if st else None
    loss = st.get("expected_loss_abs") if st else None
    loss_ok = (loss is None and st and st.get("model") == "gamma_poisson") or (
        loss is not None and loss <= rule["max_loss"])
    enough_days = days_running is not None and days_running >= spec.MIN_DAYS_TO_CALL
    comparative = atype != "rollout"
    # An approximate metric (e.g. "opened a PDF by read time", where older cohorts have had longer)
    # is biased by construction, so it can never produce a ship or revert call. Live check 2026-11-19
    # gave welcome-email-server a false "revert" (P(beat) 0.004) for exactly this reason.
    approximate = bool(isinstance(readout, dict) and readout.get("approximate"))
    if approximate:
        out["approximate_metric"] = "Metric is approximate (biased between cohorts); evidence only, never callable"
        comparative = False

    # 2 callable_win
    if comparative and p_beat is not None and p_beat >= rule["ship_p"] and loss_ok and enough_days:
        if confounded:
            out["capped_win"] = True
        else:
            flag("callable_win", f"P(beat) {p_beat:.3f} >= {rule['ship_p']}, expected loss "
                                 f"{loss if loss is not None else 'n/a'} <= {rule['max_loss']}, day {days_running}")

    # 3 callable_loss
    if comparative and p_beat is not None:
        prog = out["progress"] if out["progress"] is not None else 0
        if p_beat <= rule["stop_p"] and prog >= rule.get("futility_min_progress", 0.5):
            flag("callable_loss", f"P(beat) {p_beat:.3f} <= {rule['stop_p']} after "
                                  f"{prog:.0%} of planned {out['progress_basis'] or 'sample'}")
        elif p_beat <= LOSS_FLOOR_P and enough_days:
            flag("callable_loss", f"P(beat) {p_beat:.3f} <= {LOSS_FLOOR_P} after {days_running} days")

    # 4 needs_call
    review = spec.parse_date(e.get("review_date"))
    if review is None and sd and pdays:
        review = sd + _dt.timedelta(days=int(pdays))
    undecided = not (e.get("_outcome") or e.get("decided_on"))
    if review and undecided and today > review:
        overdue = (today - review).days
        out["overdue_days"] = overdue
        flag("needs_call", f"Review date {review.isoformat()} passed {overdue} day(s) ago, no decision")

    # 5 starved
    if mdays and out["projected_days"] is not None and out["projected_days"] > mdays:
        flag("starved", f"Projected {out['projected_days']} days to a verdict > max_days {int(mdays)}")
    elif atype == "split" and days_running is not None and days_running >= 7 and (last7 or not rows)             and obs_rate is not None and obs_rate < MIN_EXPOSURE_PER_ARM_DAY:
        # Exposure floor applies to split tests only. Benchmark / pre-post tests are low-volume by
        # design (e.g. the save offer: >= 3 buyers in ~25 offers) and are judged by projected days.
        flag("starved", f"Only {obs_rate:.1f} couples/day/arm over the last 7 days (< {MIN_EXPOSURE_PER_ARM_DAY})")

    # 6 no_data
    reporting = str(e.get("reporting") or "manual").lower()
    manual = reporting == "manual" or str(m.get("key") or "") == "manual"
    no_data_stale = True
    if not manual:
        if readout.get("error"):
            # consecutive failed runs: the trailing errored history rows, plus this readout unless
            # it is the cached copy of the last full run (registry-only rebuild)
            trailing = 0
            for h in reversed(history):
                if not h.get("error"):
                    break
                trailing += 1
            failed = max(trailing, 1) if readout.get("cached") else trailing + 1
            out["failed_runs"] = failed
            no_data_stale = failed >= FAILED_RUNS_STALE
            flag("no_data", f"metric failed: {readout.get('error')}" + (
                f" ({failed} runs in a row)" if failed >= FAILED_RUNS_STALE else ""))
        elif days_running is not None and days_running >= NO_DATA_FLAT_DAYS:
            flat = None
            if rows:
                window = [today - _dt.timedelta(days=i) for i in range(1, NO_DATA_FLAT_DAYS + 1)]
                by_date = {d: a for d, a in rows}
                flat = sum(sum(by_date.get(d, {}).values()) for d in window) == 0
            else:
                cur = _total_n(readout)
                cutoff = today - _dt.timedelta(days=NO_DATA_FLAT_DAYS)
                old = [h for h in history if (spec.parse_date(h.get("date")) or today) <= cutoff
                       and _num(h.get("n_total")) is not None]
                if cur is not None and old:
                    flat = cur <= _num(old[-1]["n_total"])
            if flat:
                flag("no_data", f"No new exposure for {NO_DATA_FLAT_DAYS} days")

    # 7 zombie
    if pdays and days_running is not None and days_running > 2 * pdays:
        flag("zombie", f"Live {days_running} days, more than 2x planned {int(pdays)}")

    # 8 manual_overdue
    if manual and days_running is not None:
        dates = [spec.parse_date(r.get("date")) for r in e.get("results") or []]
        dates = [d for d in dates if d]
        last = max(dates) if dates else None
        since = (today - last).days if last else days_running
        if since >= MANUAL_OVERDUE_DAYS:
            flag("manual_overdue", f"No readout for {since} days" if last
                 else f"No readout in {since} days live")

    # 9 confounded
    if confounded:
        flag("confounded", f"Pre/post with {len(overlaps)} overlapping changes; "
                           "a split test (or a quiet window with no other launches) would unconfound it")

    # 10 spec_incomplete
    blocking = spec.blocking(issues)
    if blocking:
        flag("spec_incomplete", f"Spec incomplete: {len(blocking)} issue(s), first: {blocking[0]['message']}")

    # 11 guardrail_unmonitored
    unmon = [g for g in gstatus if g["status"] == "unmonitored"]
    if unmon:
        flag("guardrail_unmonitored", "Guardrail not monitored: " + "; ".join(
            f"{g['key']} ({g['reason']})" for g in unmon))

    paused = e.get("_lifecycle") == "paused"
    if paused:
        for s_ in PAUSE_SUPPRESSED:
            flags.pop(s_, None)
        pdate = None
        for k in ("paused_on", "pause_date", "paused_at"):
            pdate = pdate or spec.parse_date(e.get(k))
        if pdate is None:
            rdates = [spec.parse_date(r.get("date")) for r in e.get("results") or []]
            rdates = [d for d in rdates if d]
            pdate = max(rdates) if rdates else sd
        out["paused_days"] = (today - pdate).days if pdate else None
        flag("paused", f"Paused {out['paused_days']} day(s)" if out["paused_days"] is not None else "Paused")

    ordered = [s for s in STATES if s in flags]
    out["flags"] = ordered
    state = "paused" if paused else (ordered[0] if ordered else "on_track")
    out["health"] = state
    if paused:
        out["stale"] = (out["paused_days"] or 0) >= PAUSED_STALE_DAYS
    else:
        out["stale"] = any(s in STALE_STATES and (s != "no_data" or no_data_stale) for s in ordered) or (
            "needs_call" in flags and (out["overdue_days"] or 0) >= NEEDS_CALL_STALE_AFTER)
    if state == "on_track":
        left = out["days_left"]
        out["action"] = f"Keep running, ~{left} days left" if left is not None else "Keep running"
        bits = []
        if p_beat is not None:
            bits.append(f"P(beat) {p_beat:.2f}")
        if days_running is not None:
            bits.append(f"day {days_running}" + (f" of {int(pdays)}" if pdays else ""))
        out["reason"] = ", ".join(bits) or "No issues"
    else:
        out["action"] = ACTIONS[state]
        out["reason"] = flags[state]
    return out
