"""Plain-English verdict and next step for every experiment row (FRAMEWORK.md "Verdicts and next steps").

:func:`build_verdict` turns one scored snapshot row (snapshot.build_snapshot, after health) into::

    {"result": {"label", "tone"},
     "tldr": one sentence: where it started, where it is now, chance it's genuinely better, the bar,
     "numbers": {"baseline": {label, value, rate, source}, "before_label", "after_label", "before",
                 "after", "before_rate", "after_rate", "p_beat", "bar", "unit"} | None,
     "recommend": {"call", "label", "why"},
     "unstick": concrete step (who, what, effort) when stale / overdue, else None,
     "confidence_note": str | None,
     "how_we_call": {"bar", "ship_if", "revert_if", "otherwise", "now"} (plain-English decision rule)}

:func:`build_decided_verdict` does the same for decided rows (lookback): result from the outcome, the
final outcome line as the TL;DR, no recommendation.

Deterministic: the same row always gives the same verdict. No I/O. Rules, in order:

  planned                         -> complete_spec (blocking issues) or "launch when ready"
  paused                          -> resume_or_shelve (shelve if paused > 14 days)
  guardrail breach                -> revert
  approximate (biased) metric     -> "Biased metric", no call on the numbers -> split_test
  site-wide count metric (purchases / revenue / ratings per day) on a before/after or benchmark read,
  past its run                    -> never ship / revert: "Keep it, close inconclusive" (call inconclusive)
                                     when leaning better, else "Close inconclusive"; why says why
  numbers + chance >= tier bar    -> ship (capped by confounding: still ship, "evidence, not proof")
  numbers + chance <= stop bar    -> revert (once the loss is callable, or past the planned days)
  numbers, between the bars:
      still inside planned days and not starved -> keep_running (days left)
      else chance >= 50% and guardrails ok       -> ship: keep the change, close as inconclusive
      else                                       -> revert unless there is a product reason
  metric failing / no new exposure -> fix_tracking (or shelve)
  no numbers (manual reporting)   -> read_out once due (review passed / overdue / stale),
                                     else keep_running until the review date
  spec incomplete, nothing to read -> complete_spec naming the top two missing fields, or shelve
"""
from __future__ import annotations

import re

CONFOUND_SENTENCE = "Other changes shipped in the same window, so treat this as evidence, not proof."
PREPOST_SENTENCE = "This is a before/after read, so treat it as evidence, not proof."
PAUSED_SHELVE_AFTER = 14
TOO_EARLY_DAYS = 7
SMALL_N = 30
CALL_STATES = ("needs_call", "callable_win", "callable_loss")

# metric key -> (who, what they did); "{days}" is filled from readout params
PHRASES = {
    "new_paid_within_days": ("new couples", "paid within {days} {dayword}"),
    "new_paid_within_n_days": ("new couples", "paid within {days} {dayword}"),
    "gate_hitters_paid": ("couples who hit the {gate}", "paid within {days} {dayword}"),
    "pdf_open_rate_new": ("new members", "opened a PDF"),
    "planner_second_question": ("new {seg}Planner couples", "asked a second question"),
    "planner_return_within_days": ("new {seg}Planner couples", "came back within {days} days"),
    "offer_code_buyers": ("offer-code holders", "bought"),
    "nps_low_share": ("ratings", "were 1-3 stars"),
    "trustpilot_completion": ("raters shown the Trustpilot ask", "left a review"),
    "paid_after_pricing_view": ("couples who saw the pricing page", "paid within {days} {dayword}"),
    "planner_pdf_open_rate": ("{seg}Planner couples", "opened a PDF"),
}
COUNT_UNITS = {
    "purchases_per_day": "all-site purchases a day",
    "revenue_per_day": "all-site revenue a day",
    "nps_ratings_per_day": "ratings a day",
    "nps_avg_stars": "average stars",
}
SITEWIDE_METRICS = {"purchases_per_day": "every purchase", "revenue_per_day": "all revenue",
                    "nps_ratings_per_day": "every rating"}
GATES = {"pdf_limit": "PDF limit", "planner": "Planner limit", "planner_limit": "Planner limit"}
DECIDED_RESULTS = {
    "shipped": ("Shipped", "good"), "reverted": ("Reverted", "bad"), "shelved": ("Shelved", "neutral"),
    "inconclusive": ("Inconclusive", "neutral"), "concluded": ("Concluded", "neutral"),
    "ended": ("Ended", "neutral"), "decided": ("Decided", "neutral"),
}
_DATE_RE = re.compile(r"\d{4}-(\d{2}-\d{2})")


# ------------------------------------------------------------------------------------ helpers

def _pct(x, nd=1):
    try:
        v = float(x) * 100
    except (TypeError, ValueError):
        return "?"
    s = f"{v:.{nd}f}"
    return (s.rstrip("0").rstrip(".") if "." in s else s) + "%" if nd else f"{v:.0f}%"


def _p(x):
    """Chance as a whole percent, never 0% or 100% (posteriors are never certain)."""
    v = round(float(x) * 100)
    return f"{min(max(v, 1), 99)}%"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _short(text, limit=220):
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip(" ,;:-") + "…"


def _name(row):
    return str(row.get("name") or row.get("id") or "this experiment")


def _metric(row):
    sm = row.get("success_metric") or {}
    return sm.get("metric") or {}


def _is_geo(row):
    """A purchases/revenue metric filtered to one wedding-location cohort: everyone it counts saw the
    change, so it is not a whole-site count."""
    return bool((_metric(row).get("params") or {}).get("cohort")) and \
        str(_metric(row).get("key") or "") in SITEWIDE_METRICS


def _sitewide(row):
    return str(_metric(row).get("key") or "") in SITEWIDE_METRICS and not _is_geo(row)


def _params(row):
    ro = row.get("readout") or {}
    p = dict(_metric(row).get("params") or {})
    p.update(ro.get("params") or {})
    return p


def _windows(row):
    """(before_window, after_window) as 'MM-DD..MM-DD' from the readout definition, else (None, None)."""
    d = str((row.get("readout") or {}).get("definition") or "")
    dates = _DATE_RE.findall(d)
    before = f"{dates[0]}..{dates[1]}" if len(dates) >= 2 else None
    after = f"{dates[2]}..{dates[3]}" if len(dates) >= 4 else None
    if row.get("assignment_type") == "benchmark":
        return None, before        # one cohort: the current offer; the benchmark has no window
    return before, after


def _phrase(row):
    key = str(_metric(row).get("key") or "")
    p = _params(row)
    if key in PHRASES:
        who, did = PHRASES[key]
        days = p.get("days") or 1
        dayword = "day" if str(days) == "1" else "days"
        gate = GATES.get(str(p.get("gate") or ""), str(p.get("gate") or "limit").replace("_", " "))
        seg = str(p.get("segment") or "").lower()
        if not seg and key == "planner_return_within_days":
            seg = "free" if p.get("free_only", True) else ""
        seg = {"free": "free ", "paid": "paid ", "forever": "Forever "}.get(seg, "")
        return (who.format(gate=gate, days=days, dayword=dayword, seg=seg).replace("  ", " "),
                did.format(gate=gate, days=days, dayword=dayword, seg=seg))
    label = _metric(row).get("label") or key or "the metric"
    return None, str(label)


def _blocking_fields(row, n=2):
    out = []
    for i in row.get("spec_issues") or []:
        if i.get("severity") == "error" and i.get("field"):
            f = re.sub(r"\[\d+\]", "", str(i["field"]))
            if f not in out:
                out.append(f)
    return out[:n]


def _guardrails_ok(row):
    return not any(g.get("status") == "breach" for g in row.get("guardrail_status") or [])


def _latest_note(row):
    res = [r for r in row.get("results") or [] if isinstance(r, dict) and (r.get("note") or r.get("text"))]
    if not res:
        return None, None
    r = res[-1]
    return r.get("date"), r.get("note") or r.get("text")


def _confounded(row):
    return "confounded" in (row.get("flags") or []) or len(row.get("overlapping_changes") or []) >= 2


def _evidence_sentence(row):
    if row.get("assignment_type") != "pre_post":
        return "Other changes shipped in the same window, so treat this as evidence, not proof." \
            if _confounded(row) else ""
    return CONFOUND_SENTENCE if _confounded(row) else PREPOST_SENTENCE


def _tier_words(row, bar):
    tier = row.get("tier_effective") or row.get("tier") or "ux"
    return f"{tier} tests need {_p(bar) if bar < 0.995 else '99%'}"


# ------------------------------------------------------------------------------------ numbers

def numbers(row):
    """Before/after counts, rates and the starting point, or None when there is nothing to show."""
    st = row.get("stats") or {}
    ro = row.get("readout") or {}
    m = _metric(row)
    rule = row.get("decision_rule") or {}
    bar = _num(rule.get("ship_p")) or 0.9
    before_w, after_w = _windows(row)
    atype = row.get("assignment_type")
    reg_base = _num(m.get("baseline"))
    out = None

    if st.get("model") == "gamma_poisson" or (ro.get("count") and not ro.get("arms")):
        cnt = ro.get("count") or {}
        pre, post = cnt.get("pre") or {}, cnt.get("post") or {}
        unit = (m.get("label") or "a day") if _is_geo(row) else \
            COUNT_UNITS.get(str(m.get("key") or ""), m.get("label") or "a day")
        b_rate = _num(st.get("control_rate"))
        a_rate = _num(st.get("treatment_rate"))
        if b_rate is None and _num(pre.get("days")):
            b_rate = _num(pre.get("count")) / _num(pre.get("days"))
        if a_rate is None and _num(post.get("days")):
            a_rate = _num(post.get("count")) / _num(post.get("days"))
        if b_rate is None and a_rate is None:
            out = None
        else:
            def fmt(c, rate):
                if rate is None:
                    return None
                n = _num(c.get("count"))
                d = _num(c.get("days"))
                core = f"{rate:.1f} {unit}"
                return core + (f" ({n:g} in {round(d, 1):g} days)" if n is not None and d else "")
            out = {"unit": unit, "kind": "count",
                   "before_label": "Before" + (f" ({before_w})" if before_w else ""),
                   "after_label": "After" + (f" ({after_w})" if after_w else ""),
                   "before": fmt(pre, b_rate), "after": fmt(post, a_rate),
                   "before_rate": None if b_rate is None else round(b_rate, 4),
                   "after_rate": None if a_rate is None else round(a_rate, 4)}
    else:
        c_n, c_x = st.get("c_n"), st.get("c_conv")
        t_n, t_x = st.get("t_n"), st.get("t_conv")
        arms = ro.get("arms") or {}
        if c_n is None and isinstance(arms.get("control"), dict):
            c_n, c_x = arms["control"].get("n"), arms["control"].get("conv")
        if t_n is None and isinstance(arms.get("treatment"), dict):
            t_n, t_x = arms["treatment"].get("n"), arms["treatment"].get("conv")
        if atype == "benchmark" and c_n is None and isinstance(ro.get("benchmark"), dict):
            c_n, c_x = ro["benchmark"].get("n"), ro["benchmark"].get("conv")

        def fmt(x, n):
            if not n or x is None:
                return None
            return f"{int(x)} of {int(n)} ({_pct(x / n)})"
        if (c_n and c_x is not None) or (t_n and t_x is not None):
            if atype == "split":
                bl, al = "Control", "New version"
            elif atype == "benchmark":
                bl, al = "Old benchmark", "Now" + (f" ({after_w})" if after_w else "")
            else:
                bl = "Before" + (f" ({before_w})" if before_w else "")
                al = "After" + (f" ({after_w})" if after_w else "")
            out = {"unit": "rate", "kind": "rate", "before_label": bl, "after_label": al,
                   "before": fmt(c_x, c_n), "after": fmt(t_x, t_n),
                   "before_rate": round(c_x / c_n, 6) if c_n and c_x is not None else None,
                   "after_rate": round(t_x / t_n, 6) if t_n and t_x is not None else None,
                   "before_n": c_n, "after_n": t_n}

    if out is None and reg_base is None:
        return None
    if out is None:
        out = {"unit": "rate" if reg_base is not None and reg_base <= 1 else "count", "kind": None,
               "before_label": None, "after_label": None, "before": None, "after": None,
               "before_rate": None, "after_rate": None}
    # starting point: the readout's control / before arm, else the registry baseline
    if out.get("before_rate") is not None:
        base = {"label": out["before_label"], "value": out["before"], "rate": out["before_rate"],
                "source": "readout control"}
    elif reg_base is not None:
        val = _pct(reg_base) if reg_base <= 1 and out.get("unit") == "rate" else f"{reg_base:g}"
        base = {"label": "Baseline (registry)", "value": val, "rate": reg_base, "source": "registry baseline"}
    else:
        base = {"label": None, "value": None, "rate": None, "source": None}
    out["baseline"] = base
    out["p_beat"] = None if st.get("p_beat") is None else round(float(st["p_beat"]), 4)
    out["bar"] = bar
    return out


def _rate_words(row, nums, which):
    """'5.1% (20 of 395, 10-20..11-03)' style fragment for before/after."""
    if not nums:
        return None
    if nums.get("kind") == "count":
        return nums.get(which)
    s = nums.get(which)
    r = nums.get(which + "_rate")
    if s is None or r is None:
        return None
    counts = s.split(" (")[0]
    lab = nums.get(which + "_label") or ""
    w = lab[lab.find("(") + 1: lab.rfind(")")] if "(" in lab else ""
    return f"{_pct(r)} ({counts}" + (f", {w})" if w else ")")


def _numbers_sentence(row, nums):
    """'Started at 5.1% (20 of 395, 10-20..11-03) of couples who hit the PDF limit paying within 1 day;
    after the change 5.9% (22 of 371, 11-03..11-19): 70% chance it's genuinely better; money tests need 95%.'"""
    who, did = _phrase(row)
    p = nums.get("p_beat")
    bar = nums.get("bar") or 0.9
    b = _rate_words(row, nums, "before")
    a = _rate_words(row, nums, "after")
    direction = str(_metric(row).get("direction") or "increase").lower()
    better = "genuinely better" + (" (lower)" if direction == "decrease" else "")
    atype = row.get("assignment_type")
    if nums.get("kind") == "count":
        lead = f"Started at {b}" if b else "No starting point recorded"
        s = f"{lead}; after the change {a}" if a else lead
    elif who:
        subject = f"{who} who {did}"
        if atype == "split":
            s = f"{a} of {who} {did} with the new version vs {b} on the old one" if (a and b) else \
                f"{a or b} of {who} {did}"
        elif atype == "benchmark":
            s = f"{a} of {who} {did}" + (f", vs {b} on the old benchmark" if b else "")
        else:
            lead = f"Started at {b} of {who} {did}" if b else f"No starting point recorded for {subject}"
            s = f"{lead}; after the change {a}" if a else lead
    else:
        if atype == "split":
            s = f"{did}: new version {a} vs old {b}"
        else:
            lead = f"{did}: started at {b}" if b else f"{did}: no starting point recorded"
            s = f"{lead}, after the change {a}" if a else lead
    if p is not None:
        s += f": {_p(p)} chance it's {better}; {_tier_words(row, bar)}"
    return s[0].upper() + s[1:] + "."


# ------------------------------------------------------------------------------------ main

def _rec(call, label, why):
    return {"call": call, "label": label, "why": why}


def _unstick(row, rec):
    """Concrete next step when the experiment is stuck (stale, overdue or long-paused)."""
    stuck = bool(row.get("stale")) or (row.get("overdue_days") or 0) > 0
    if not stuck or not rec:
        return None
    call, name = rec["call"], _name(row)
    if call in ("ship", "revert", "shelve", "inconclusive"):
        return ("Owner: make the call at Monday's weekly call, or now with a Decision note in the dash (1 min). "
                "Claude then updates the registry.")
    if call == "read_out":
        return f"Ask Claude: 'read out {name}' (~10 min from Xano). If nobody will act on the answer, shelve it."
    if call == "fix_tracking":
        err = _short((row.get("readout") or {}).get("error") or row.get("reason") or "", 90)
        return (f"Ask Claude to fix the metric ({err}; ~15 min). If it can't be measured from Xano, "
                "switch to a manual read-out or shelve it.")
    if call == "resume_or_shelve":
        return ("Owner: say resume or shelve with a Decision note. Resuming means switching it back on "
                "(Claude, ~5 min); shelving needs nothing else.")
    if call == "complete_spec":
        fields = ", ".join(_blocking_fields(row)) or "the missing spec fields"
        return f"Ask Claude to fill {fields} from the launch notes (~10 min), or shelve it if it no longer matters."
    if call == "split_test":
        return ("Ask Claude to draft a split test (~20 min) for the owner to approve; until then the numbers "
                "are evidence only. Or close it as inconclusive.")
    if call == "keep_running":
        return "Set a review date with a Next step note so it gets called; if it has no date, it never ends."
    return None


def _confidence(row, nums):
    notes = []
    ro = row.get("readout") or {}
    pend = sum(v for v in (ro.get("pending") or {}).values() if isinstance(v, (int, float)))
    if pend:
        notes.append(f"{int(pend)} recent couples are still inside their window and not counted yet.")
    if nums and nums.get("kind") == "rate":
        ns = [n for n in (nums.get("before_n"), nums.get("after_n")) if n]
        if ns and min(ns) < SMALL_N:
            notes.append(f"Small numbers (as few as {int(min(ns))}), so one couple moves the rate a lot.")
    key = str(_metric(row).get("key") or "")
    if key in SITEWIDE_METRICS and not _is_geo(row):
        notes.append(f"This counts {SITEWIDE_METRICS[key]} on the whole site, not only couples who saw this change; "
                     "many things move it, so a rise can't be credited to this change alone.")
    for c in ro.get("caveats") or []:
        if "assignment.pre is not set" in str(c):
            notes.append("No 'before' window recorded; it defaulted to the 14 days before launch.")
    return " ".join(notes) or None


def build_verdict(row, today=None):
    """Verdict for one live / paused / planned snapshot row. Never raises."""
    try:
        return _build(row or {})
    except Exception as exc:  # a verdict must never break the snapshot
        return {"result": {"label": "No data", "tone": "unknown"},
                "tldr": f"No verdict (engine error: {type(exc).__name__}).", "numbers": None,
                "recommend": None, "unstick": None, "confidence_note": None, "how_we_call": {}}


def _build(row):
    lifecycle = row.get("lifecycle")
    health = row.get("health")
    flags = row.get("flags") or []
    rule = row.get("decision_rule") or {}
    bar = _num(rule.get("ship_p")) or 0.9
    stop = _num(rule.get("stop_p")) or 0.1
    days = row.get("days_running")
    days_left = row.get("days_left")
    nums = numbers(row) if lifecycle in ("live", "paused") else None
    p = nums.get("p_beat") if nums else None
    ev = _evidence_sentence(row)
    note_date, note = _latest_note(row)
    no_start = bool(nums is None or not (nums.get("baseline") or {}).get("source"))
    result = {"label": "Not measured", "tone": "unknown"}
    rec = None
    tldr = None

    def with_ev(s):
        return f"{s} {ev}".strip() if ev else s

    # planned ------------------------------------------------------------------------------
    if lifecycle in ("planned", "draft"):
        fields = _blocking_fields(row)
        result = {"label": "Too early", "tone": "unknown"}
        tldr = "Not live yet." if lifecycle == "planned" else "Draft: not built or switched on yet."
        rec = (_rec("complete_spec", "Finish the spec before launch",
                    f"Missing {', '.join(fields)}; the launch check fails until they are set.")
               if fields or lifecycle == "draft" else
               _rec("keep_running", "Launch when ready", "The spec passes the launch check."))
        if lifecycle == "draft" and not fields:
            rec["why"] = "Drafts get a full spec and a launch check before they go live."
        return _out(result, tldr, nums, rec, row)

    # paused ---------------------------------------------------------------------------------
    if lifecycle == "paused" or health == "paused":
        pdays = row.get("paused_days") or 0
        result = {"label": "Not measured" if p is None else _label(p, days)[0],
                  "tone": "unknown" if p is None else _label(p, days)[1]}
        tldr = f"Paused for {pdays} day(s)." + (f" Last note ({note_date}): {_short(note, 180)}" if note else "")
        if pdays > PAUSED_SHELVE_AFTER:
            rec = _rec("resume_or_shelve", "Shelve it (or resume)",
                       f"Paused {pdays} days with no plan to resume; shelving keeps the list honest.")
        else:
            rec = _rec("resume_or_shelve", "Resume or shelve", "Paused recently; decide whether it comes back.")
        return _out(result, tldr, nums, rec, row)

    # guardrail breach ----------------------------------------------------------------------
    if health == "guardrail_breach" or "guardrail_breach" in flags:
        result = {"label": "Behind", "tone": "bad"}
        tldr = _numbers_sentence(row, nums) if nums and (nums.get("before") or nums.get("after")) else ""
        tldr = (f"A guardrail broke: {row.get('reason') or 'a guardrail crossed its threshold'}. " + tldr).strip()
        rec = _rec("revert", "Revert now", "A guardrail metric crossed its limit; protect it first, then investigate.")
        return _out(result, with_ev(tldr), nums, rec, row)

    # approximate metric -------------------------------------------------------------------
    if row.get("approximate_metric"):
        result = {"label": "Biased metric", "tone": "unknown"}
        tldr = ("The metric favours older cohorts (they have had longer to act), so these numbers can't say "
                "whether it worked.")
        rec = _rec("split_test", "Judge it with a fair test",
                   "Only a split test, or a metric measured over the same time for both groups, can call it.")
        return _out(result, tldr, nums, rec, row)

    has_numbers = p is not None
    pending_geo = (row.get("readout") or {}).get("pending_geo")
    if not has_numbers and pending_geo:
        result = {"label": "Measuring", "tone": "unknown"}
        done = pending_geo.get("payers", 0) - pending_geo.get("unresolved", 0)
        tldr = (f"Starting point on its way: the engine is looking up the wedding location of each payer "
                f"({done} of {pending_geo.get('payers', 0)} done) so it can count {pending_geo.get('label', 'this cohort')} only.")
        rec = _rec("keep_running", "Keep running (numbers on the way)",
                   "Locations are looked up a few dozen per run (every 6 h); the before/after appears when they are done.")
        return _out(result, tldr, nums, rec, row)
    # metric failing ------------------------------------------------------------------------
    if not has_numbers and (health == "no_data" or "no_data" in flags) and \
            str(row.get("reporting") or "").lower() == "auto":
        result = {"label": "No data", "tone": "unknown"}
        err = (row.get("readout") or {}).get("error")
        tldr = ("No numbers: the metric can't be computed yet" + (f" ({_short(err, 120)})" if err else "")
                + ".")
        rec = _rec("fix_tracking", "Fix the metric (or shelve)",
                   "Without a working metric this experiment can never be called.")
        return _out(result, tldr, nums, rec, row)

    if has_numbers:
        result = dict(zip(("label", "tone"), _label(p, days)))
        tldr = _numbers_sentence(row, nums)
        if no_start:
            tldr += " No starting point recorded; record one so the change can be judged."
        gr_ok = _guardrails_ok(row)
        if nums.get("kind") == "rate" and not nums.get("before_rate") and not nums.get("after_rate")                 and nums.get("before_rate") is not None and nums.get("after_rate") is not None:
            # nobody converted in either period: the chance figure is meaningless
            result = {"label": "No data", "tone": "unknown"}
            tldr = (f"Zero in both periods ({nums.get('before')} before, {nums.get('after')} after): either nobody "
                    "does it or it isn't being tracked.")
            rec = _rec("fix_tracking", "Check the tracking (or shelve)",
                       "With zero conversions on both sides there is nothing to compare.")
            return _out(result, tldr, nums, rec, row)
        enough = days is not None and days >= 7
        has_run = days_left is not None        # a planned run or review date exists
        past = (has_run and days_left == 0) or health in CALL_STATES or "starved" in flags or "zombie" in flags
        prepost = row.get("assignment_type") in ("pre_post", "benchmark")
        loss = st_loss(row)
        max_loss = _num(rule.get("max_loss"))
        loss_ok = (loss is None and (row.get("stats") or {}).get("model") == "gamma_poisson") or             (loss is not None and (max_loss is None or loss <= max_loss))
        sitewide = _sitewide(row) and prepost
        if sitewide and not past:
            if has_run:
                rec = _rec("keep_running", f"Keep running ({days_left} {'day' if days_left == 1 else 'days'} left)",
                           "Still inside its planned run; a whole-site count can only close it as inconclusive.")
            else:
                rec = _rec("keep_running", "Keep running (set a review date)",
                           "No planned run or review date, so it never comes up for a call; set one.")
            return _out(result, with_ev(tldr), nums, rec, row)
        if sitewide:
            # A whole-site count on a before/after read can't credit this change: never ship / revert on it.
            caveat = ("it counts the whole site, so it can't credit this change; run a split test if the "
                      "answer matters.")
            if p >= bar or (p >= 0.5 and gr_ok):
                rec = _rec("inconclusive", "Keep it, close inconclusive",
                           f"Leaning better ({_p(p)}), but {caveat}")
            else:
                rec = _rec("inconclusive", "Close inconclusive",
                           f"Leaning worse ({_p(p)} chance it's better), but {caveat}")
            return _out(result, with_ev(tldr), nums, rec, row)
        if p >= bar and enough and loss_ok:
            if _confounded(row) or row.get("capped_win"):
                rec = _rec("ship", "Keep the change (ship)",
                           f"{_p(p)} chance it's better clears the bar, but other launches overlap: evidence, not proof.")
            else:
                rec = _rec("ship", "Ship it", f"{_p(p)} chance it's better clears the {_p(bar)} bar.")
        elif p <= stop and ("callable_loss" in flags or past):
            rec = _rec("revert", "Revert it", f"Only {_p(p)} chance it's better; it is very likely worse.")
        elif not past and not ("starved" in flags) and (days_left or 0) > 0:
            rec = _rec("keep_running", f"Keep running ({days_left} {'day' if days_left == 1 else 'days'} left)",
                       "Still inside its planned run; call it at the review date.")
        elif p >= 0.5 and gr_ok:
            why = (f"Leaning better ({_p(p)}), but a before/after read can't reach the {_p(bar)} bar; "
                   "nothing it protects got worse." if prepost else
                   f"Leaning better ({_p(p)}) but won't reach the {_p(bar)} bar at this traffic; nothing got worse.")
            if "starved" in flags:
                why = (f"{_p(p)} chance it's better is the evidence we will get: at this traffic it would take far "
                       f"longer to reach {_p(bar)}. Nothing it protects got worse.")
            if p >= bar and not loss_ok:
                why = (f"{_p(p)} chance it's better, but the expected loss if it's wrong ({loss:.4f}) is above the "
                       f"{max_loss:g} limit, so it can't be called a win.") if loss is not None and max_loss else why
            rec = _rec("inconclusive", "Keep it, close inconclusive", why + " Close it as inconclusive.")
        else:
            rec = _rec("revert", "Revert (leaning worse)",
                       f"Only {_p(p)} chance it's better "
                       + ("after its planned run" if days_left is not None else
                          f"after {days} days (no planned run or review date set)")
                       + "; revert unless there's a product reason to keep it.")
        return _out(result, with_ev(tldr), nums, rec, row)

    # no numbers: manual reporting --------------------------------------------------------
    manual = str(row.get("reporting") or "manual").lower() == "manual"
    if manual and lifecycle == "live":
        result = {"label": "Not measured", "tone": "unknown"}
        start = (nums or {}).get("baseline") or {}
        if note:
            tldr = f"No structured read-out yet. Last note ({note_date}): {_short(note)}"
        else:
            tldr = "No numbers recorded yet, and no read-out since launch."
        due = health in CALL_STATES or health == "manual_overdue" or bool(row.get("stale")) or \
            "manual_overdue" in flags or (days_left == 0 and days is not None)
        blocking = _blocking_fields(row)
        if due:
            rec = _rec("read_out", "Read it out, then decide",
                       "Nobody has measured it; a 10-minute read from Xano turns it into a call.")
        elif health == "spec_incomplete" and not note and blocking:
            rec = _rec("complete_spec", "Complete the spec (or shelve)",
                       f"Missing {', '.join(blocking)}; without them it can't be read out or called.")
        else:
            left = f" ({days_left} {'day' if days_left == 1 else 'days'} left)" if days_left else ""
            rec = _rec("keep_running", f"Keep running{left}", "Read it out at the review date.")
        return _out(result, tldr, nums, rec, row)

    # spec incomplete, nothing to read ------------------------------------------------------
    fields = _blocking_fields(row)
    result = {"label": "No data", "tone": "unknown"}
    tldr = "Nothing to read yet." + (f" Last note ({note_date}): {_short(note)}" if note else "")
    rec = (_rec("complete_spec", "Complete the spec (or shelve)",
                f"Missing {', '.join(fields)}; without them it can't be read out or called.")
           if fields else _rec("read_out", "Read it out, then decide", "No numbers recorded yet."))
    return _out(result, tldr, nums, rec, row)


def st_loss(row):
    st = row.get("stats") or {}
    for k in ("expected_loss_abs", "exp_loss", "expected_loss"):
        if st.get(k) is not None:
            return _num(st.get(k))
    return None


def _label(p, days):
    if days is not None and days < TOO_EARLY_DAYS:
        return "Too early", "unknown"
    if p >= 0.8:
        return "Ahead", "good"
    if p <= 0.2:
        return "Behind", "bad"
    return "No clear difference", "neutral"


def how_we_call(row, nums=None, rec=None):
    """The decision rule in plain words: what clears the bar, what triggers a revert, what happens
    otherwise, and where it stands now. Every key optional; never raises."""
    try:
        rule = row.get("decision_rule") or {}
        bar = _num(rule.get("ship_p")) or 0.9
        stop = _num(rule.get("stop_p")) or 0.1
        lifecycle = row.get("lifecycle")
        m = _metric(row)
        manual = str(row.get("reporting") or "manual").lower() == "manual" or             str(m.get("key") or "manual") == "manual"
        what = m.get("label") or "the main measure"
        direction = "down" if str(m.get("direction") or "").lower() == "decrease" else "up"
        tier = str(row.get("tier_effective") or row.get("tier") or "ux")
        guards = [g.get("label") or g.get("key") for g in ((row.get("success_metric") or {}).get("guardrails") or row.get("guardrails") or [])
                  if isinstance(g, dict)]
        guard_txt = (" and nothing it protects (" + ", ".join(str(g) for g in guards[:3]) + ") gets worse")             if guards else ""
        out = {}
        if manual:
            out["bar"] = f"Measured by hand: {what} should move {direction}."
            out["ship_if"] = "A read-out shows it moved the right way" + guard_txt + "."
            out["revert_if"] = "A read-out shows it moved the wrong way, or something it protects got worse."
            out["otherwise"] = ("No read-out by the review date means it comes up as 'read it out, then decide'; "
                                "if nobody will measure it, shelve it.")
        elif _sitewide(row) and row.get("assignment_type") in ("pre_post", "benchmark"):
            out["bar"] = (f"{what} counts the whole site, so a before/after can show a direction but can't "
                          "credit this change.")
            out["ship_if"] = ("It can't be shipped on these numbers alone; at the end it closes as inconclusive "
                              "(keep it if leaning better).")
            out["revert_if"] = "A guardrail breaks."
            out["otherwise"] = "If the answer matters, run a split test."
        else:
            tname = {"ux": "UX", "money": "Money", "infra": "Infra"}.get(tier.lower(), tier.capitalize())
            out["bar"] = (f"{tname} tests need a {_p(bar)} chance that {what} really moved {direction}, "
                          f"after at least {TOO_EARLY_DAYS} days.")
            out["ship_if"] = f"Chance it's better reaches {_p(bar)}" + guard_txt + "."
            out["revert_if"] = f"Chance it's better falls to {_p(stop)} or below, or a guardrail breaks."
            out["otherwise"] = ("At the end of the run, leaning better (over 50%) with nothing worse means keep it and "
                                "close inconclusive; leaning worse means revert unless there's a product reason.")
        if lifecycle in ("planned", "draft"):
            out["now"] = "Not live yet."
        elif nums and nums.get("p_beat") is not None:
            d, left = row.get("days_running"), row.get("days_left")
            out["now"] = (f"Now: {_p(nums['p_beat'])} chance it's better (bar {_p(nums.get('bar') or bar)})"
                          + (f", day {d}" if d is not None else "")
                          + (f" of {d + left}" if d is not None and left else "") + ".")
        elif rec and rec.get("why"):
            out["now"] = "Now: " + rec["why"]
        return out
    except Exception:  # never break the snapshot
        return {}


def _out(result, tldr, nums, rec, row):
    if rec and rec["call"] == "complete_spec" and not rec["label"].endswith("shelve)") and row.get("lifecycle") not in ("planned", "draft"):
        rec["label"] += " (or shelve)"
    return {"result": result, "tldr": tldr, "numbers": nums, "recommend": rec,
            "unstick": _unstick(row, rec), "confidence_note": _confidence(row, nums),
            "how_we_call": how_we_call(row, nums, rec)}


def build_decided_verdict(row):
    """Lookback verdict for a decided row (snapshot['decided'])."""
    status = str(row.get("outcome") or row.get("status") or "decided").lower()
    label, tone = DECIDED_RESULTS.get(status, (status.capitalize(), "neutral"))
    take = row.get("takeaway") or row.get("decision")
    when = row.get("decided_on")
    tldr = f"{label}" + (f" on {when}" if when else "") + (f": {_short(take, 300)}" if take else
                                                            ": no outcome recorded.")
    return {"result": {"label": label, "tone": tone}, "tldr": tldr, "numbers": None, "recommend": None,
            "unstick": None, "confidence_note": None, "decided_on": when, "status": row.get("status")}
