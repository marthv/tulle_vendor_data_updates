"""EXP_TAB_V2: how a split test is read (FRAMEWORK.md section 14). Only used when readlock.enabled().

* one read, on the read date, at 97.5% (``read_call``); before that no result leaves this module (``lock_row``);
* a daily harm stop: P(the new version is within 10% of the old one, or better) <= 0.5% (``harm_p``);
* setup health: balance, arm switching / repeat accounts, locked settings, exposure, coverage (``check_*``);
* ``settings_hash``: the launch settings, stored at launch and compared on every run;
* ``shares_with``: live tests that touch the same couples or outcome.

The cards themselves are built in v2_cards.py. Health colours (green / amber / red) describe the TEST, never the
result or a price.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re

from . import health as _h
from . import readlock, spec, stats, v2_data

READ_P = 0.975
HARM_P = 0.005
HARM_MARGIN = 0.10          # "clear" harm: worse by more than this share of the old rate
HARM_MIN_N = 40             # couples in each arm before a harm stop can fire
HARM_MIN_CONV = 12          # conversions in both arms together
BALANCE_P = 0.001
BALANCE_STRONG_P = 1e-5     # red at any size
BALANCE_AMBER_P = 0.01
BALANCE_MIN_N = 100         # nothing is judged below this
BALANCE_FULL_N = 1500       # red at p < 0.001 from here, or from 75% of the planned couples when that is fewer
BALANCE_FULL_MIN = 250
MIX_RED = 0.01
COVERAGE_RED = 0.98
EXPOSURE_GAP_H = 48
GREEN, AMBER, RED = "green", "amber", "red"
_ORDER = {GREEN: 0, AMBER: 1, RED: 2}
SURFACE_NAMES = {"planner": "Planner page", "bubble": "Ask Tulle bubble"}
MONEY_KEYS = ("new_paid_within_days", "gate_hitters_paid", "offer_code_buyers", "paid_after_pricing_view",
              "purchases_per_day", "revenue_per_day")


# ---------------------------------------------------------------------------
# launch settings
# ---------------------------------------------------------------------------

def _f(v):
    n = spec._num(v)
    return None if n is None else round(n, 6)


def _when(v):
    from . import metrics
    ms = metrics.parse_ms(v)
    return None if ms is None else _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def settings(entry):
    """The settings that may not change after launch, normalised so that writing a default out changes nothing."""
    e = entry if isinstance(entry, dict) else {}
    a = e.get("assignment") if isinstance(e.get("assignment"), dict) else {}
    m = e.get("metric") if isinstance(e.get("metric"), dict) else {}
    rule = a.get("rule")
    manual = str(m.get("key") or "") in ("", "manual") or str(e.get("reporting") or "manual").lower() == "manual"
    rd = readlock.registry_read_date(e)
    ex = v2_data.exposure_rule(e)

    def ids(*keys):
        for k in keys:
            if a.get(k):
                return sorted(str(i) for i in a[k])
        return []

    return {"type": a.get("type") or ("split" if readlock.is_split(e) else None), "rule": rule,
            "salt": str(a.get("salt") or "").rstrip(":") or None,
            "percent": (_f(a.get("pct")) if a.get("pct") is not None else 50.0) if rule == "hash_pct" else None,
            "treatment": str(a.get("treatment") or "even").lower() if rule == "user_id_parity" else None,
            "hash digits": a.get("hash_digits"), "forced ids": ids("force_ids", "forced_ids"),
            "excluded ids": ids("exclude_ids"),
            "start": _when(a.get("post_start")) or _when(e.get("start_date")),
            "metric": m.get("key"), "metric params": m.get("params") or {}, "direction": m.get("direction"),
            "metric wording": (m.get("label") or e.get("primary_metric")) if manual else None,
            "read_date": rd.isoformat() if rd else None,
            "read date as written": str(readlock.to_date(e.get("read_date")) or readlock.to_date(e.get("review_date")) or ""),
            "exposure": {k: ex[k] for k in ("source", "surfaces", "kinds")} if ex else None}


def settings_hash(entry_or_settings):
    s = entry_or_settings
    if not (isinstance(s, dict) and "metric wording" in s):
        s = settings(s)
    return hashlib.sha256(json.dumps(s, sort_keys=True, default=str).encode()).hexdigest()[:16]


def launch_lock(entry, today=None):
    """The ``launch_lock`` object to paste into the registry at launch (validate.py --lock <id>)."""
    s = settings(entry)
    return {"hash": settings_hash(s), "settings": s,
            "locked_on": (readlock.to_date(today) or readlock.utc_today()).isoformat()}


def settings_changes(entry, stored=None):
    """(changed field names, where the lock came from) against the registry lock and the engine's stored copy."""
    now = settings(entry)
    out, src = [], []
    for lock, name in ((entry.get("launch_lock"), "registry"), (stored, "engine")):
        if not isinstance(lock, dict) or not lock.get("hash"):
            continue
        src.append(name)
        old = lock.get("settings")
        if isinstance(old, dict) and settings_hash(old) != lock["hash"] and "launch_lock" not in out:
            out.append("launch_lock")                # the stored lock itself was edited
        if lock["hash"] == settings_hash(now):
            continue
        diff = [k for k in now if not isinstance(old, dict) or
                json.dumps(old.get(k), sort_keys=True, default=str) != json.dumps(now[k], sort_keys=True, default=str)]
        if isinstance(old, dict) and diff:
            out += [d for d in diff if d not in out]
        elif "settings" not in out:
            out.append("settings")
    reg = entry.get("launch_lock") if isinstance(entry.get("launch_lock"), dict) else {}
    if isinstance(stored, dict) and stored.get("hash") and reg.get("hash") and reg["hash"] != stored["hash"] \
            and "launch_lock" not in out:
        out.append("launch_lock")                    # replaced after the engine first saw it
    return out, src


# ---------------------------------------------------------------------------
# statistics: read, harm, balance
# ---------------------------------------------------------------------------

def p_better(c, t, direction="increase"):
    """P(new version is better), exact Beta posterior."""
    cn, cx = stats._clean(c.get("n"), c.get("conv"))
    tn, tx = stats._clean(t.get("n"), t.get("conv"))
    p = stats.prob_beta_greater(1 + tx, 1 + tn - tx, 1 + cx, 1 + cn - cx)
    return 1.0 - p if str(direction or "").lower().startswith("dec") else p


def read_call(c, t, direction="increase"):
    """The one read: ("ship" | "revert" | "inconclusive", p). Nothing is called without couples in both arms."""
    if not (c and t and c.get("n") and t.get("n")):
        return None, None
    p = p_better(c, t, direction)
    return ("ship" if p >= READ_P else "revert" if p <= 1 - READ_P else "inconclusive"), p


def harm_p(c, t, direction="increase", margin=HARM_MARGIN):
    """P(the new version is NOT clearly worse): within ``margin`` of the old rate, or better. Small = clear harm.
    None when there is too little to judge (HARM_MIN_N per arm, HARM_MIN_CONV conversions)."""
    cn, cx = stats._clean((c or {}).get("n"), (c or {}).get("conv"))
    tn, tx = stats._clean((t or {}).get("n"), (t or {}).get("conv"))
    if min(cn, tn) < HARM_MIN_N or cx + tx < HARM_MIN_CONV:
        return None
    dec = str(direction or "").lower().startswith("dec")
    if (tx / tn <= (1 + margin) * cx / cn) if dec else (tx / tn >= (1 - margin) * cx / cn):
        return 1.0                                  # observed on the safe side: never clear harm
    p = stats.prob_ratio_above(1 + tx, 1 + tn - tx, 1 + cx, 1 + cn - cx, (1 + margin) if dec else (1 - margin))
    return 1.0 - p if dec else p


def harm_p_counts(c_x, t_x, share=0.5, margin=HARM_MARGIN, direction="increase"):
    """Same question when only payers per arm are known (arm sizes follow the assigned share)."""
    c_x, t_x = int(c_x or 0), int(t_x or 0)
    if c_x + t_x < HARM_MIN_CONV or not 0 < share < 1:
        return None
    dec = str(direction or "").lower().startswith("dec")
    k = (1 + margin) if dec else (1 - margin)
    q = k * share / (1 - share)
    below = stats.inc_beta(q / (1 + q), 1 + t_x, 1 + c_x)      # P(new rate < k * old rate)
    return below if dec else 1.0 - below


def balance_p(n_control, n_treatment, share=0.5):
    """Two-sided exact binomial p-value that the arms came from the planned split (``share`` = new version's part)."""
    n, t = int(n_control + n_treatment), int(n_treatment)
    if n <= 0 or not 0 < share < 1:
        return 1.0
    if t <= n * share:
        tail = 1.0 if t >= n else stats.inc_beta(1 - share, n - t, t + 1)      # P(X <= t)
    else:
        tail = stats.inc_beta(share, t, n - t + 1)                             # P(X >= t)
    return min(1.0, 2 * tail)


def treatment_share(entry):
    a = entry.get("assignment") if isinstance(entry.get("assignment"), dict) else {}
    p = _f(a.get("pct"))
    if a.get("rule") == "hash_pct" and p is not None:
        return p / 100.0
    return 0.5


# ---------------------------------------------------------------------------
# exposure counts
# ---------------------------------------------------------------------------

def _log_rows(entry):
    """Registry ``exposure_log`` (manual splits): [{date, control, treatment, both_arms?, repeat_accounts?}], couples
    newly exposed that day. It holds no outcome, so it can be shown while the result is hidden."""
    out = [r for r in entry.get("exposure_log") or [] if isinstance(r, dict) and spec.parse_date(r.get("date"))]
    return sorted(out, key=lambda r: str(r["date"]))


def exposure_rows(entry, readout):
    rows = _h._exposure_rows(readout)
    if not rows:
        rows = _h._exposure_rows({"exposure_by_day": _log_rows(entry)})
    by = {}
    for d, arms in rows:
        cur = by.setdefault(d, {})
        for k, v in arms.items():
            cur[k] = cur.get(k, 0) + v
    return sorted(by.items())


def exposed(entry, readout):
    """{"control": n, "treatment": n} couples exposed so far, or None."""
    ro = readout or {}
    ex = ro.get("exposed")
    if isinstance(ex, dict) and "control" in ex and "treatment" in ex:
        return {k: int(ex[k] or 0) for k in ("control", "treatment")}
    rows = [a for _d, a in exposure_rows(entry, ro) if "control" in a or "treatment" in a]
    if rows:
        return {k: int(sum(a.get(k, 0) for a in rows)) for k in ("control", "treatment")}
    arms = ro.get("arms") or {}
    if isinstance(arms.get("control"), dict) and isinstance(arms.get("treatment"), dict):
        pend = ro.get("pending") or {}
        return {k: int((_h._num(arms[k].get("n")) or 0) + (_h._num(pend.get(k)) or 0))
                for k in ("control", "treatment")}
    return None


# ---------------------------------------------------------------------------
# setup checks
# ---------------------------------------------------------------------------

def _check(key, label, colour, text):
    return {"key": key, "label": label, "colour": colour, "text": text}


def blocks_read(entry, check):
    """A red check that makes the numbers themselves untrustworthy. Exposure that went quiet is red on the card, but
    the couples already counted can still be read."""
    return check["colour"] == RED and (check["key"] not in ("exposure", "harm") or v2_data.exposure_rule(entry) is None)


def check_balance(entry, readout, need=None, final=False):
    """``need`` = planned couples per version. Red at once at p < 0.001 once 75% of the plan (at most 1,500 couples)
    is in, and on the read date; at p < 0.00001 from 100 couples. Earlier p < 0.001 is amber: looked at every day from
    the first couple, a true 50/50 crosses 0.001 in 0.6-0.9% of runs."""
    ex = exposed(entry, readout)
    if not ex:
        return _check("balance", "Balance", AMBER, "Couples per version are not recorded yet.")
    share = treatment_share(entry)
    n = ex["control"] + ex["treatment"]
    if n < BALANCE_MIN_N:
        return _check("balance", "Balance", GREEN, f"Only {n} couples so far; checked from {BALANCE_MIN_N}.")
    p = balance_p(ex["control"], ex["treatment"], share)
    want = f"{share * 100:g}/{(1 - share) * 100:g}"
    got = f"{ex['treatment']} new / {ex['control']} old"
    if p < BALANCE_P:
        full = BALANCE_MIN_N if final else max(BALANCE_FULL_MIN, min(BALANCE_FULL_N, int(1.5 * (need or BALANCE_FULL_N))))
        if n >= full or p < BALANCE_STRONG_P:          # 1.5 x need per version = 75% of the planned couples
            return _check("balance", "Balance", RED, f"The versions are not the planned {want}: {got}. A real {want} "
                          "split would almost never look like this, so the read can't be trusted.")
        return _check("balance", "Balance", AMBER, f"{got}: off the planned {want}, but early; red if it is still off "
                      f"at {full:,} couples.")
    if p < BALANCE_AMBER_P:
        return _check("balance", "Balance", AMBER, f"{got}: a little off the planned {want}; watching.")
    return _check("balance", "Balance", GREEN, f"{got}, in line with the planned {want}.")


def mixing(entry, readout):
    m = (readout or {}).get("mixing")
    if isinstance(m, dict):
        return m
    late = 0
    rows = [r for r in _log_rows(entry) if any(r.get(k) is not None for k in ("both_arms", "repeat_accounts", "late_id"))]
    res = [r for r in spec._as_list(entry.get("results")) if isinstance(r, dict) and isinstance(r.get("counts"), dict)
           and ("both_arms" in r["counts"] or "repeat_accounts" in r["counts"])]
    if rows:
        both = sum(int(_h._num(r.get("both_arms")) or 0) for r in rows)
        rep = sum(int(_h._num(r.get("repeat_accounts")) or 0) for r in rows)
        late = sum(int(_h._num(r.get("late_id")) or 0) for r in rows)
    elif res:
        both = int(_h._num(res[-1]["counts"].get("both_arms")) or 0)
        rep = int(_h._num(res[-1]["counts"].get("repeat_accounts")) or 0)
    else:
        return None
    ex = exposed(entry, readout)
    n = (ex["control"] + ex["treatment"]) if ex else 0
    bad = both + rep + late
    logged = {k: any(r.get(k) is not None for r in rows) if rows else True for k in ("both_arms", "repeat_accounts")}
    return {"by_log": True, "arm_recorded": int(logged["both_arms"]), "checked": int(logged["repeat_accounts"]), "exposed": n, "both_arms": both, "repeat_accounts": rep, "late_id": late, "excluded": bad,
            "share": round(bad / n, 6) if n else (1.0 if bad else 0.0)}


def check_mixing(entry, readout):
    m = mixing(entry, readout)
    label = "Both versions / repeat accounts"
    if not m:
        return _check("mixing", label, AMBER, "Not checked: no per-couple data here.")
    bad = int(m.get("excluded") or 0)
    share = _h._num(m.get("share"))
    if share is None:
        share = bad / m["exposed"] if m.get("exposed") else (1.0 if bad else 0.0)
    both, rep = int(m.get("both_arms") or 0), int(m.get("repeat_accounts") or 0)
    can_arm = bool(m.get("arm_recorded"))
    can_rep = bool(m.get("checked")) or rep > 0
    bits = [f"{both} couples in both versions" if can_arm else
            (f"{both} couples found in both versions; beyond that " if both else "") + "couples in both versions can't be checked (the version shown is not stored per couple)",
            f"{rep} repeat accounts" if can_rep else "repeat accounts can't be checked (no account e-mails loaded)"]
    if m.get("late_id"):
        bits.append(f"{int(m['late_id'])} whose account arrived after the card was on screen")
    txt = "; ".join(bits) + "."
    txt = txt[:1].upper() + txt[1:]
    if bad:
        txt += f" {bad} left out of the read ({share * 100:.1f}%)."
    if not m.get("by_log") and m.get("checked") and m.get("exposed") and m["checked"] < m["exposed"]:
        txt += f" Repeat check covered {int(m['checked'])} of {int(m['exposed'])} accounts."
    return _check("mixing", label, RED if share > MIX_RED else (GREEN if can_arm and can_rep else AMBER), txt)


def check_settings(entry, stored):
    diff, src = settings_changes(entry, stored)
    if diff:
        return _check("settings", "Settings", RED, "Changed after launch: " + ", ".join(diff) +
                      ". A test that changes while it runs can't be read; start a new one.")
    if not src:
        return _check("settings", "Settings", AMBER, "No stored launch settings to compare with.")
    on = next((lk.get("locked_on") for lk in (entry.get("launch_lock"), stored) if isinstance(lk, dict)
               and lk.get("locked_on")), None)
    sd, lo = spec.parse_date(entry.get("start_date")), spec.parse_date(on)
    late = f" (after the {sd.isoformat()} launch, so earlier changes can't be seen)" if sd and lo and lo > sd else ""
    return _check("settings", "Settings", GREEN, "Unchanged since they were locked" + (f" on {on}" if on else "") + late + ".")


def _p0(expected):
    return math.exp(-max(expected, 0.0))


def _aware(v):
    try:
        d = _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v and "T" in str(v) else None
    except ValueError:
        return None
    return d.replace(tzinfo=_dt.timezone.utc) if d is not None and d.tzinfo is None else d


def _midnight(d):
    return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc)


def check_exposure(entry, readout, today, now=None):
    label = "Exposure"
    rule = v2_data.exposure_rule(entry)
    if rule is None:
        return _check("exposure", label, RED, "Nothing records which couples saw the test, so it can't be read.")
    sd = spec.parse_date(entry.get("start_date"))
    rows = exposure_rows(entry, readout)
    est = spec._num((entry.get("audience") or {}).get("est_daily_exposed")
                    if isinstance(entry.get("audience"), dict) else None)
    counts = f" Counts only: {', '.join(SURFACE_NAMES.get(s, s) for s in rule['surfaces'])}." if rule["surfaces"] else ""
    if now is None:
        now = _aware((readout or {}).get("computed_at"))
        if now is None or now.date() > today:
            now = _midnight(today)
    live_days = [(d, sum(a.values())) for d, a in rows if sum(a.values()) > 0]
    if not rows and not (readout or {}).get("last_exposure_at"):
        ex = exposed(entry, readout)
        if ex and sum(ex.values()):        # couples, but no days: nothing to time a gap with
            return _check("exposure", label, AMBER, f"{sum(ex.values()):,} couples so far, with no daily record." + counts)
    last = _aware((readout or {}).get("last_exposure_at"))
    if last is None and live_days:
        last = _midnight(live_days[-1][0]) + _dt.timedelta(days=1)
    readable = rule["source"].startswith(("xano", "table")) or bool(rows)
    if not readable:
        return _check("exposure", label, AMBER, f"Recorded in {rule['source']}; the engine can't see it. Log "
                      "couples per version in exposure_log." + counts)
    ref = last or (_midnight(sd) if sd else None)
    if ref is None:
        return _check("exposure", label, AMBER, "No start date, so exposure can't be checked." + counts)
    gap_h = (now - ref).total_seconds() / 3600.0
    total = sum(n for _d, n in live_days)
    rate = est
    if live_days and sd:
        span = (live_days[-1][0] - sd).days + 1
        if span >= 2:
            rate = total / span
    if gap_h >= EXPOSURE_GAP_H:
        exp = (rate or 0) * gap_h / 24.0
        what = f"No new couple for {gap_h / 24:.1f} days" + (f" (about {exp:.0f} expected)" if rate else "")
        if rate and _p0(exp) < 0.001:
            return _check("exposure", label, RED, what + ". Is the test still switched on?" + counts)
        if rate is None or _p0(exp) < 0.05:
            return _check("exposure", label, AMBER, what + ". Check it is still switched on." + counts)
        return _check("exposure", label, GREEN, what + ", which is normal at this volume." + counts)
    # one version silent for two full days while the other keeps going
    if any("control" in a or "treatment" in a for _d, a in rows) and (sd is None or (today - sd).days >= 2):
        cut = {today - _dt.timedelta(days=i) for i in range(3)}
        recent = {k: sum(a.get(k, 0) for d, a in rows if d in cut) for k in ("control", "treatment")}
        share = treatment_share(entry)
        for arm, name, s in (("treatment", "new", share), ("control", "old", 1 - share)):
            exp = recent["control" if arm == "treatment" else "treatment"] * s / max(1 - s, 1e-9)
            if recent[arm] == 0 and _p0(exp) < 0.05:
                return _check("exposure", label, RED if _p0(exp) < 0.001 else AMBER,
                              f"The {name} version reached nobody in the last 2 days while the other one did." + counts)
    return _check("exposure", label, GREEN, (f"{total:g} couples so far." if total else "Waiting for the first "
                                             "couples.") + counts)


def _flat_history(entry, readout, history, today):
    """No daily exposure record: compare couples so far with the stored total from 48 h ago or earlier."""
    cur = _h._total_n(readout)
    cut = today - _dt.timedelta(days=EXPOSURE_GAP_H // 24)
    old = [x for x in history if (spec.parse_date(x.get("date")) or today) <= cut and _h._num(x.get("n_total")) is not None]
    if cur is None or not old or cur > _h._num(old[-1]["n_total"]):
        return None
    est = spec._num((entry.get("audience") or {}).get("est_daily_exposed") if isinstance(entry.get("audience"), dict) else None)
    p0 = _p0(est * 2) if est is not None else None
    if p0 is not None and p0 >= 0.05:
        return None
    return _check("exposure", "Exposure", RED if p0 is not None and p0 < 0.001 else AMBER,
                  "No new couple in the last 2 days. Is the test still switched on?")


def check_coverage(readout):
    cov = (readout or {}).get("coverage")
    if not isinstance(cov, dict) or not cov:
        return None
    bits, red = [], False
    for arm, name in (("treatment", "new"), ("control", "old")):
        c = cov.get(arm)
        if not isinstance(c, dict) or not c.get("total"):
            continue
        share = c["covered"] / c["total"]
        red = red or share < COVERAGE_RED
        bits.append(f"{name} {share * 100:.1f}% ({c['covered']} of {c['total']})")
    if not bits:
        return None
    return _check("coverage", "Coverage", RED if red else GREEN,
                  "PDF opens can be placed in time for: " + ", ".join(bits) +
                  (". Under 98%: the read can't be trusted." if red else ". The rest are left out of the read."))


FOLLOW_DEFAULT = {"new_paid_within_days": 1, "gate_hitters_paid": 1, "pdf_open_rate_new": 1, "pdf_open_within_1d": 1.25,
                  "planner_second_question": 1, "planner_return_within_days": 4, "paid_after_pricing_view": 1,
                  "planner_pdf_open_rate": 7, "offer_code_buyers": 1.5}
_WITHIN = re.compile(r"within\s+(\d+(?:\.\d+)?)\s*(h|hour|hours|d|day|days|week|weeks)\b", re.I)


def follow_days(entry):
    """Days a couple needs after joining before it can be counted: metric.follow_days, params.days, the measure's
    wording ("within 7 days"), else the metric's default."""
    m = entry.get("metric") if isinstance(entry.get("metric"), dict) else {}
    for v in (m.get("follow_days"), (m.get("params") or {}).get("days")):
        n = spec._num(v)
        if n is not None:
            return n + (1 if v2_data._canon(entry) == "planner_return_within_days" else 0)
    g = _WITHIN.search(str(m.get("label") or ""))
    if g:
        u = g.group(2).lower()
        return float(g.group(1)) * (1 / 24 if u.startswith("h") else 7 if u.startswith("w") else 1)
    return FOLLOW_DEFAULT.get(v2_data._canon(entry), 0)


def finished(entry, readout, today):
    """Couples per version whose follow-up is over: the readout's counted couples, else exposure older than the
    follow-up. None when unknown."""
    arms = (readout or {}).get("arms") or {}
    if isinstance(arms.get("control"), dict) and isinstance(arms.get("treatment"), dict):
        return {k: int(_h._num(arms[k].get("n")) or 0) for k in ("control", "treatment")}
    cut = today - _dt.timedelta(days=int(math.ceil(follow_days(entry))))
    rows = [(d, a) for d, a in exposure_rows(entry, readout) if "control" in a or "treatment" in a]
    if not rows:
        return None
    return {k: int(sum(a.get(k, 0) for d, a in rows if d <= cut)) for k in ("control", "treatment")}


def check_settled(entry, readout):
    """``access_settled`` share per version: a diagnostic. Never red and never a filter, because leaving or clicking
    early (the outcome) makes a view unsettled."""
    s = (readout or {}).get("settled")
    if not isinstance(s, dict):
        rows = [r["settled"] for r in _log_rows(entry) if isinstance(r.get("settled"), dict)]
        s = rows[-1] if rows else None
    if not isinstance(s, dict):
        return None
    t, c = s.get("treatment") or {}, s.get("control") or {}
    tv, cv = _h._num(t.get("views")) or 0, _h._num(c.get("views")) or 0
    if not tv or not cv:
        return None
    a, b = (_h._num(t.get("settled")) or 0) / tv, (_h._num(c.get("settled")) or 0) / cv
    p = (a * tv + b * cv) / (tv + cv)
    se = math.sqrt(p * (1 - p) * (1 / tv + 1 / cv)) if 0 < p < 1 else 0
    off = se and math.erfc(abs(a - b) / se / math.sqrt(2)) < 0.001
    return _check("settled", "Access known in time", AMBER if off else GREEN,
                  f"New version {a * 100:.1f}% of views, old version {b * 100:.1f}%. Diagnostic only: every view counts.")


def check_sample(entry, plan, ex, days_running, rd, sd):
    """Amber when the test will not have the planned couples by its read date (it is still read that day)."""
    need = (plan or {}).get("n_per_arm")
    if not need or not rd or not sd:
        return None
    follow = follow_days(entry)
    total_days = max((rd - sd).days - follow, 0)           # only couples whose follow-up ends by the read date count
    ran = (days_running or 0) - follow
    if ex and ran >= 7:
        pace = min(ex["control"], ex["treatment"]) / ran
    else:
        est = spec._num((entry.get("audience") or {}).get("est_daily_exposed"))
        if est is None:
            return None
        pace = est * min(treatment_share(entry), 1 - treatment_share(entry))
    by = int(pace * total_days)
    if by < need:
        return _check("sample", "Sample", AMBER, f"Heading for about {by:,} couples per version by the read date; "
                      f"{need:,} are needed to see a change worth having, so expect no clear answer.")
    return _check("sample", "Sample", GREEN, f"On pace: about {by:,} couples per version by the read date, "
                  f"{need:,} needed.")


def worst(checks):
    return max((c["colour"] for c in checks), key=lambda c: _ORDER[c], default=GREEN)


# ---------------------------------------------------------------------------
# payments (watch-only) and guardrails of a split
# ---------------------------------------------------------------------------

_MONEY_WORDS = re.compile(r"\b(paid|pay|pays|paying|payer|payers|payments?|purchas\w*|buy\w*|bought|revenue|upgrad\w*|subscri\w*|"
                          r"plans?|checkout\w*|unlock\w*|order\w*|convert\w* to paid|members?hip\w*)\b")


def is_money_metric(entry):
    key = v2_data._canon(entry)
    m = entry.get("metric") if isinstance(entry.get("metric"), dict) else {}
    txt = " ".join([str(m.get("label") or ""), str(m.get("key") or ""), str(entry.get("primary_metric") or "")]).lower()
    return key in MONEY_KEYS or (key in ("", "manual") and bool(_MONEY_WORDS.search(txt)))


def payments_view(readout, hide_arms=False):
    p = (readout or {}).get("payments")
    if not isinstance(p, dict) or p.get("error") or not isinstance(p.get("arms"), dict):
        why = f" ({p['error']})" if isinstance(p, dict) and p.get("error") else ""
        return "Payments, watch only: not counted yet" + why + ". They can't be read in a test this size."
    t, c = p["arms"].get("treatment") or {}, p["arms"].get("control") or {}
    if hide_arms:
        tot = int(t.get("payers") or 0) + int(c.get("payers") or 0)
        return (f"Payments, watch only: {tot} couples paid in all. By version they are this test's result, so they "
                "can't be read before the read date.")
    who = "of the couples who saw it" if p.get("basis") == "exposed" else \
        "of all accounts given that version, not only those who saw it"

    def base(a):
        return f"{int(a.get('payers') or 0)} of {int(a['n']):,}" if a.get("n") is not None else f"{int(a.get('payers') or 0)}"
    return f"Payments, watch only: new version {base(t)} paid, old version {base(c)} paid ({who}). Too few to compare; can't be read."


def guardrail_rows(entry, readout):
    """(status rows without numbers, keys with clear harm)."""
    cfg = {g["key"]: g for g in entry.get("guardrails") or [] if isinstance(g, dict) and g.get("key")}
    reported = {g.get("key"): g for g in (readout or {}).get("guardrails") or [] if isinstance(g, dict)}
    manual_entry = str(entry.get("reporting") or "manual").lower() == "manual"
    bar = harm_bar(entry)
    out, harmed = [], []
    for key, g in cfg.items():
        r = reported.get(key)
        direction = "decrease" if _h._num(g.get("max_rise_rel")) is not None else "increase"
        payment = bool(r and r.get("payment")) or key in v2_data.PAYMENT_COUNTS
        row = {"key": key, "label": g.get("label") or key}
        if str(key) == "manual" or (r and r.get("manual")):
            out.append(dict(row, status="manual", reason="read by hand"))
            continue
        if r is None:
            out.append(dict(row, status="watch" if payment else ("manual" if manual_entry else "unmonitored"),
                            reason="not computed yet" if payment else
                            ("read by hand" if manual_entry else "not computed by the engine")))
            continue
        if r.get("error"):
            out.append(dict(row, status="unmonitored", reason=str(r["error"])))
            continue
        hit, checked = False, True
        arms = r.get("arms") or {}
        if isinstance(r.get("breached"), bool) or _h._num(r.get("p_breach")) is not None:
            hit = bool(r.get("breached")) or (_h._num(r.get("p_breach")) or 0) >= 1 - bar
        elif isinstance(arms.get("control"), dict) and isinstance(arms.get("treatment"), dict):
            p = harm_p(arms["control"], arms["treatment"], direction)
            hit, checked = p is not None and p <= bar, p is not None
        elif isinstance((r.get("payments") or {}).get("arms"), dict):
            pa = r["payments"]["arms"]
            p = harm_p_counts((pa.get("control") or {}).get("payers"), (pa.get("treatment") or {}).get("payers"),
                              r["payments"].get("treatment_share") or treatment_share(entry), direction=direction)
            hit, checked = p is not None and p <= bar, p is not None
        elif isinstance(r.get("count"), dict):
            out.append(dict(row, status="unmonitored", reason="a whole-site before/after is not used inside a split"))
            continue
        if hit:
            harmed.append(key)
        why = "clear harm" if hit else ("too few couples to check for harm" if not checked else
                                        "watch only, can't be read" if payment else "no clear harm")
        out.append(dict(row, checked=checked, reason=why,
                        status="breach" if hit else ("watch" if payment else ("ok" if checked else "unchecked"))))
    return out, harmed


def harm_bar(entry):
    """P threshold per measure: 0.5% shared by the primary and every guardrail that can stop the test."""
    n = 1 + sum(1 for g in entry.get("guardrails") or [] if isinstance(g, dict) and g.get("key") not in (None, "", "manual"))
    return HARM_P / n


def harm_look(entry, readout, hide):
    """Today's look: {"fired": [labels], "checked": [labels], "not_checked": [(label, why)], "status": guardrail rows}."""
    ro = readout or {}
    m = entry.get("metric") or {}
    gstatus, harmed = guardrail_rows(entry, ro)
    fired = [next(g["label"] for g in gstatus if g["key"] == k) for k in harmed]
    done = [g["label"] for g in gstatus if g.get("checked") and g["key"] not in harmed]
    missing = [(g["label"], g["reason"]) for g in gstatus if not g.get("checked") and g["status"] != "breach"]
    name = m.get("label") or "the main measure"
    arms = ro.get("arms") or {}
    if hide:
        if ro.get("error"):
            missing.insert(0, (name, "the metric failed"))
        elif isinstance(arms.get("control"), dict) and isinstance(arms.get("treatment"), dict) and \
                arms["control"].get("conv") is not None and arms["treatment"].get("conv") is not None:
            p = harm_p(arms["control"], arms["treatment"], str(m.get("direction") or "increase"))
            if p is None:
                missing.insert(0, (name, "too few couples so far"))
            elif p <= harm_bar(entry):
                fired.insert(0, name)
            else:
                done.insert(0, name)
        else:
            missing.insert(0, (name, "no outcome feed: the engine can't see this measure"))
    return {"fired": fired, "checked": done, "not_checked": [list(x) for x in missing], "status": gstatus}


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

def manual_read(entry, rd):
    """The one read of a manual split: the first results[] with counts dated on or after the read date."""
    if rd is None:
        return None
    e = entry if "_lifecycle" in entry else spec.normalise(entry)
    rows = [r for r in e.get("results") or [] if isinstance(r, dict) and r.get("_counts") and spec.parse_date(r.get("date"))]
    for r in sorted(rows, key=lambda r: str(r.get("date"))):
        if spec.parse_date(r["date"]) >= rd:
            return r
    return None


def _is_manual(e):
    return str(e.get("reporting") or "manual").lower() == "manual" or \
        str((e.get("metric") or {}).get("key") or "") == "manual"


def evaluate_split(entry, readout, history=None, changes=None, today=None, spec_issues=None, lock=None, read=None,
                   look=None):
    """Same output keys as health.evaluate, for a live / paused split, plus the v2 keys. Before the read date the
    output holds no result: no chance, lift, rate, winner, leaning or guardrail probability."""
    today = _h._today(today)
    e = entry if "_assignment_type" in (entry or {}) else spec.normalise(entry)
    ro = dict(readout) if isinstance(readout, dict) else {}
    history = [x for x in (history or []) if isinstance(x, dict)]
    issues = spec_issues if spec_issues is not None else spec.validate(e)
    manual = _is_manual(e)
    rd = readlock.read_date(e, lock)
    hide = readlock.hidden(e, today, lock)
    still_split = e.get("_assignment_type") == "split"
    sd = spec.parse_date(e.get("start_date"))
    days = (today - sd).days if sd else None
    m = e.get("metric") or {}
    direction = str(m.get("direction") or "increase").lower()

    out = {"health": None, "stale": False, "action": None, "reason": None, "days_running": days,
           "days_left": max((rd - today).days, 0) if rd else None, "progress": None, "progress_basis": None,
           "flags": [], "overdue_days": None, "projected_days": None, "stats": None, "guardrails": [],
           "overlapping_changes": _h.overlapping_changes(e, changes, today), "sample_plan": None,
           "decision_rule": {"ship_p": READ_P, "stop_p": round(1 - READ_P, 6), "harm_p": HARM_P},
           "evidence_label": None, "exposure_per_arm_day": None, "guardrail_status": [],
           "failed_runs": 0, "paused_days": None, "read_date": rd.isoformat() if rd else None,
           "result_hidden": hide, "result_text": readlock.hidden_text(e, lock) if hide else None,
           "read": None, "checks": [], "light": GREEN, "exposed": None, "finished": None, "look": None}

    ex = exposed(e, ro)
    out["exposed"] = ex
    if ex and days:
        out["exposure_per_arm_day"] = round(min(ex.values()) / days, 3)
    fin = finished(e, ro, min(today, rd) if rd else today)
    out["finished"] = fin
    ran = (days or 0) - follow_days(e)
    seen_pace = round(min(fin.values()) / ran, 3) if fin and ran >= 7 else None
    plan = stats.sample_plan(m.get("baseline"), m.get("mde_rel"), m.get("mde_abs"),
                             seen_pace or spec.daily_exposed_per_arm(e), direction, two_sided=True)
    out["sample_plan"] = plan
    if plan.get("n_per_arm") and fin:
        out["progress"] = round(min(min(fin.values()) / plan["n_per_arm"], 1.0), 4)
        out["progress_basis"] = "sample"
    elif rd and sd and days is not None:
        out["progress"] = round(min(max(days / max((rd - sd).days, 1), 0.0), 1.0), 4)
        out["progress_basis"] = "time"
    out["projected_days"] = plan.get("days_needed")

    flags = {}

    # setup checks
    as_of = _midnight(rd) if rd and not hide else None         # after the read, setup is judged as of the read
    checks = [check_balance(e, ro, plan.get("n_per_arm"), final=bool(as_of)), check_mixing(e, ro), check_settings(e, lock),
              check_exposure(e, ro, rd if as_of else today, as_of)]
    if hide and not exposure_rows(e, ro):
        flat = _flat_history(e, ro, history, today)
        if flat:
            checks[-1] = flat
    cov = check_coverage(ro)
    if cov:
        checks.append(cov)
    if hide:
        s = check_sample(e, plan, fin, days, rd, sd)
        if s:
            checks.append(s)
    else:
        st_ = check_settled(e, ro)          # not before the read date
        if st_:
            checks.append(st_)
    if ro.get("error"):
        checks.append(_check("data", "Data", RED, f"The last run could not compute this test: {ro['error']}"))
    if ro.get("approximate"):
        checks.append(_check("data", "Data", RED, "The measure is approximate (older couples had longer to act), so it "
                             "can't be read."))
    if rd is None:
        checks.append(_check("read_date", "Read date", RED, "No read date is set, so the result can never be opened."))
    if not still_split:
        checks.append(_check("data", "Data", RED, "It was launched as a split and is now something else; not read."))
    red = [c for c in checks if c["colour"] == RED]
    block = [c for c in red if blocks_read(e, c)]

    # guardrails + harm stop
    # (one look per UTC day: a look already taken today, or a stop that has fired, is reused)
    arms = ro.get("arms") or {}
    if not (isinstance(look, dict) and (look.get("fired") or look.get("date") == today.isoformat())):
        look = dict(harm_look(e, ro, hide), date=today.isoformat())
    out["look"] = look
    gstatus, labels = look["status"], list(look["fired"])
    out["guardrail_status"] = gstatus
    out["guardrails"] = [{"key": g["key"], "breached": g["status"] == "breach"} for g in gstatus]
    if labels:
        out["harm"] = labels
        checks.append(_check("harm", "Harm stop", RED, "Clear harm to: " + ", ".join(labels) + ". Stop the test."))
        flags["guardrail_breach"] = ("Clear harm to " + ", ".join(labels) + " (daily safety check). Stop the test" +
                                     (f"; the numbers stay hidden until {rd.isoformat()}" if hide and rd else ""))
    else:
        miss = "; ".join(f"{n} ({w})" for n, w in look["not_checked"])
        done = ", ".join(look["checked"])
        if not hide and look["not_checked"] and not done:
            checks.append(_check("harm", "Harm stop", AMBER, "Not checked: " + miss + "."))
        elif look["not_checked"]:
            checks.append(_check("harm", "Harm stop", AMBER, (f"Checked today, no clear harm: {done}. " if done else "") +
                                 f"Not checked: {miss}."))
        elif done:
            checks.append(_check("harm", "Harm stop", GREEN, f"Checked today, no clear harm: {done}."))
        else:
            checks.append(_check("harm", "Harm stop", AMBER if hide else GREEN, "Not checked: nothing to check."
                                 if hide else "The test has been read; the daily check is over."))

    # the read
    if rd and not hide and e["_lifecycle"] == "live":
        stored = (read or {}).get("arms") or {}
        c, t, when = stored.get("control"), stored.get("treatment"), (read or {}).get("date")
        if not stored and ro.get("cached") and not manual and (_aware(ro.get("computed_at")) or as_of) < as_of:
            pass                                   # cached numbers from before the read date: wait for the next run
        elif not stored:
            if manual:
                r = manual_read(e, rd)
                a = ((r or {}).get("_counts") or {}).get("arms") or {}
                c, t, when = a.get("control"), a.get("treatment"), (r or {}).get("date")
            elif not ro.get("error"):
                c, t, when = arms.get("control"), arms.get("treatment"), rd.isoformat()
        call, p = read_call(c, t, direction) if still_split else (None, None)
        if call is None:
            out["read"] = {"call": None, "date": None}
        else:
            out["stats"] = stats.beta_binomial(c.get("n"), c.get("conv"), t.get("n"), t.get("conv"),
                                               direction=direction)
            # a stored read keeps the trust judgement it was taken with
            bad = read.get("untrusted_because") or [] if stored else [x["label"] for x in block]
            out["read"] = {"call": "untrusted" if bad else call, "raw_call": call, "p_beat": round(p, 6), "date": when,
                           "arms": {"control": dict(c), "treatment": dict(t)}, "bar": READ_P,
                           "untrusted_because": bad}
            if call in ("ship", "revert") and not bad:
                flags["callable_win" if call == "ship" else "callable_loss"] = (
                    f"Read on {when}: {p:.3f} chance it's better, bar {READ_P}")
        undecided = not (e.get("_outcome") or e.get("decided_on"))
        if undecided and not ({"callable_win", "callable_loss"} & set(flags)):
            if call is None:
                why = "its type was changed after launch, so it is not read" if not still_split else \
                    "read it out (first results entry with counts)" if manual else "no couples in both versions to read"
            elif out["read"]["call"] == "untrusted":
                why = "don't call a winner, setup is red: " + ", ".join(out["read"]["untrusted_because"])
            else:
                why = "no clear difference at the 97.5% bar"
            flags["needs_call"] = f"Read date {rd.isoformat()} reached: {why}"
        if today > rd and undecided:
            out["overdue_days"] = (today - rd).days

    # engine / setup states
    no_data_stale = False
    if ro.get("error") and not manual and hide:
        trailing = 0
        for x in reversed(history):
            if not x.get("error"):
                break
            trailing += 1
        out["failed_runs"] = max(trailing, 1) if ro.get("cached") else trailing + 1
        no_data_stale = out["failed_runs"] >= _h.FAILED_RUNS_STALE
        flags["no_data"] = f"metric failed: {ro.get('error')}"
    if red and hide:
        flags["setup_red"] = "; ".join(f"{c['label']}: {c['text']}" for c in red)
    blocking = spec.blocking(issues)
    if blocking:
        flags["spec_incomplete"] = f"Spec incomplete: {len(blocking)} issue(s), first: {blocking[0]['message']}"
    unmon = [g for g in gstatus if g["status"] == "unmonitored"]
    if unmon:
        flags["guardrail_unmonitored"] = "Guardrail not monitored: " + "; ".join(
            f"{g['key']} ({g['reason']})" for g in unmon)
    out["checks"], out["light"] = checks, worst(checks)

    order = ("guardrail_breach", "callable_win", "callable_loss", "needs_call", "no_data", "setup_red",
             "spec_incomplete", "guardrail_unmonitored")
    out["flags"] = [s for s in order if s in flags]
    if e["_lifecycle"] == "paused":
        pdate = None
        for k in ("paused_on", "pause_date", "paused_at"):
            pdate = pdate or spec.parse_date(e.get(k))
        rdates = [spec.parse_date(r.get("date")) for r in e.get("results") or [] if isinstance(r, dict)]
        pdate = pdate or max([d for d in rdates if d], default=None) or sd
        out["paused_days"] = (today - pdate).days if pdate else None
        out.update(health="paused", action=_h.ACTIONS["paused"], flags=out["flags"] + ["paused"],
                   reason=f"Paused {out['paused_days']} day(s)" if out["paused_days"] is not None else "Paused",
                   stale=(out["paused_days"] or 0) >= _h.PAUSED_STALE_DAYS)
        return out
    state = out["flags"][0] if out["flags"] else "on_track"
    out["health"] = state
    out["stale"] = no_data_stale or (out["overdue_days"] or 0) >= _h.NEEDS_CALL_STALE_AFTER
    if state == "on_track":
        left = out["days_left"]
        out["action"] = f"Keep running, ~{left} days left" if left is not None else "Keep running"
        out["reason"] = (f"day {days}" + (f" of {(rd - sd).days}" if rd and sd else "")) if days is not None else "No issues"
    else:
        out["action"] = {"guardrail_breach": "Stop the test now", "setup_red": "Fix the test before reading it"}.get(
            state, _h.ACTIONS.get(state))
        out["reason"] = flags[state]
    return out


# ---------------------------------------------------------------------------
# verdict + public row
# ---------------------------------------------------------------------------

def _plural(n, w):
    return f"{n} {w}" + ("" if n == 1 else "s")


def build_verdict(row):
    """verdict for a split row (same keys as verdict.build_verdict)."""
    rd = row.get("read_date")
    health = row.get("health")
    why = row.get("reason") or ""
    how = {"bar": f"One read, on {rd or 'its read date'}. The new version ships only if we are 97.5% sure it is better.",
           "ship_if": "97.5% sure or more on the read date, with no red setup check.",
           "revert_if": "97.5% sure it is worse on the read date, or the daily harm stop fires.",
           "otherwise": "Anything between is 'no clear difference': close it as inconclusive."}
    out = {"result": {"label": "Hidden", "tone": "unknown"}, "tldr": "", "numbers": None, "recommend": None,
           "unstick": None, "confidence_note": None, "how_we_call": how}

    def rec(call, label, text):
        out["recommend"] = {"call": call, "label": label, "why": text}
        return out

    if row.get("result_hidden"):
        left = row.get("days_left")
        out["tldr"] = (row.get("result_text") or "Result: hidden") + "."
        how["now"] = out["tldr"]
        if health == "guardrail_breach":
            return rec("revert", "Stop the test now", why)
        if health == "paused":
            return rec("resume_or_shelve", "Resume or shelve", why)
        if health == "no_data":
            return rec("fix_tracking", "Fix the metric (or shelve)", why)
        if health == "setup_red":
            return rec("fix_tracking", "Fix the test before reading it", why)
        if health == "spec_incomplete":
            return rec("complete_spec", "Complete the spec (or shelve)", why)
        return rec("keep_running", "Keep running" + (f" ({_plural(left, 'day')} to the read)" if left is not None else ""),
                   "Nothing is read before the read date.")
    if health == "paused":
        out["result"] = {"label": "Paused", "tone": "unknown"}
        out["tldr"] = "Paused; nothing new is read while it is paused."
        return rec("resume_or_shelve", "Resume or shelve", why)
    read = row.get("read") or {}
    if not read.get("call"):
        out["result"] = {"label": "Not read", "tone": "unknown"}
        out["tldr"] = "The read date has come, but there is nothing to read yet."
        return rec("read_out", "Read it out, then decide", why)
    c, t = read["arms"]["control"], read["arms"]["treatment"]
    cr = (c["conv"] / c["n"]) if c.get("n") else None
    tr = (t["conv"] / t["n"]) if t.get("n") else None
    label = str((((row.get("success_metric") or {}).get("metric")) or {}).get("label") or "The main measure")
    label = label[:1].upper() + label[1:]

    def pct(v):
        return "?" if v is None else f"{v * 100:.1f}%"
    old, new = f"{c['conv']} of {c['n']} ({pct(cr)})", f"{t['conv']} of {t['n']} ({pct(tr)})"
    p = read["p_beat"]
    out["numbers"] = {"unit": "rate", "kind": "rate", "before_label": "Old version", "after_label": "New version",
                      "before": old, "after": new, "before_rate": cr, "after_rate": tr, "before_n": c["n"],
                      "after_n": t["n"], "p_beat": p, "bar": READ_P,
                      "baseline": {"label": "Old version", "value": old, "rate": cr, "source": "readout control"}}
    word = {"ship": "New version is better", "revert": "New version is worse", "inconclusive": "No clear difference",
            "untrusted": "Can't be trusted"}[read["call"]]
    out["result"] = {"label": word, "tone": "neutral"}
    out["tldr"] = (f"Read once, on {read.get('date')}. {label}: new version {new}, old version {old}. "
                   f"{min(max(p * 100, 0.1), 99.9):.1f}% chance the new version is better; the bar is 97.5%. {word}.")
    ci = (row.get("stats") or {}).get("lift_ci95_rel")
    if ci and None not in ci:
        out["confidence_note"] = f"The true change is likely between {ci[0] * 100:+.0f}% and {ci[1] * 100:+.0f}%."
    how["now"] = out["tldr"]
    if read["call"] == "untrusted":
        return rec("inconclusive", "Don't call a winner", "Setup is red (" +
                   ", ".join(read.get("untrusted_because") or []) + "), so the numbers can't be trusted.")
    call, text = {"ship": ("ship", "Ship it"), "revert": ("revert", "Revert it"),
                  "inconclusive": ("inconclusive", "Close inconclusive")}[read["call"]]
    return rec(call, text, why or word)


_PUBLIC_RO = ("computed_at", "error", "metric", "params", "definition", "caveats", "exposure_by_day", "exposed",
              "pending", "coverage", "mixing", "last_exposure_at", "cached", "source", "members_left_out")


def public_readout(ro, entry=None):
    """Readout of a hidden split: couples and setup only, no outcome in any field."""
    if not isinstance(ro, dict):
        return ro
    out = {k: ro[k] for k in _PUBLIC_RO if k in ro}
    arms = ro.get("arms")
    if isinstance(arms, dict) and arms:
        out["arms"] = {k: {"n": v.get("n")} for k, v in arms.items() if isinstance(v, dict)}
    out["guardrails"] = [{k: g[k] for k in ("key", "manual", "error") if k in g}
                         for g in ro.get("guardrails") or [] if isinstance(g, dict)]
    if isinstance(ro.get("overlap"), dict):
        out["overlap"] = {k: {"shared": v.get("shared")} for k, v in ro["overlap"].items() if isinstance(v, dict)}
    if isinstance(ro.get("payments"), dict) and not is_money_metric(entry or {}):
        out["payments"] = ro["payments"]
    return out


def lock_row(row, entry, locked=None):
    """Strip everything that could carry a result from a hidden split's snapshot row (in place)."""
    row["readout"] = public_readout(row.get("readout"), entry)
    row["stats"] = None
    n = len([r for r in row.get("results") or [] if isinstance(r, dict)])
    row["results"] = [{"note": f"{n} read-out note(s). {readlock.hidden_text(entry, locked)}"}] if n else []
    row.pop("history", None)
    for k in ("evidence_label", "approximate_metric", "capped_win"):
        row[k] = None
    return row


def lock_decided(row, entry, locked=None):
    """A split closed before its read date: no outcome, take-away or verdict leaves (in place)."""
    text = readlock.hidden_text(entry, locked)
    row.update(result_hidden=True, result_text=text, status="stopped early", outcome=None, decision=None,
               takeaway=text, results=[])
    row["verdict"] = {"result": {"label": "Not read", "tone": "unknown"}, "tldr": text + ".", "numbers": None,
                      "recommend": None, "unstick": None, "confidence_note": None,
                      "decided_on": row.get("decided_on"), "status": "stopped early"}
    return row


def lock_snapshot(snap, registry, today=None):
    """Apply the read lock to a snapshot built before the switch was on (the volume copy at start-up)."""
    if not isinstance(snap, dict) or snap.get("exp_tab_v2"):
        return snap
    by = {e.get("id"): e for e in (registry or {}).get("experiments") or [] if isinstance(e, dict)}
    out = dict(snap)
    rows = []
    for r in snap.get("experiments") or []:
        e = by.get(r.get("id")) if isinstance(r, dict) else None
        if e is None and isinstance(r, dict):
            e = r                                # not in the registry any more: judge the row itself
        if e is not None and readlock.hidden(e, today):
            rd = readlock.read_date(e)
            r = dict(r, read_date=rd.isoformat() if rd else None, result_hidden=True,
                     result_text=readlock.hidden_text(e))
            if r.get("health"):
                r.update(health="on_track", flags=[], reason="", action="Keep running", stale=False, guardrails=[],
                         guardrail_status=[], days_left=None, decision_rule=None)
                r["verdict"] = build_verdict(r)
            lock_row(r, e)
        rows.append(r)
    out["experiments"] = rows
    out["decided"] = [lock_decided(dict(d), by[d["id"]]) if isinstance(d, dict) and d.get("id") in by and
                      readlock.hidden(by[d["id"]], today) else d for d in snap.get("decided") or []]
    out.pop("history_by_id", None)
    from . import snapshot as _s
    return _s.recompute_views(out)


# ---------------------------------------------------------------------------
# overlap
# ---------------------------------------------------------------------------

def _pages(e):
    return {str(s).lower() for s in spec._as_list(e.get("surfaces")) if str(s).lower().startswith("weweb:")}


def shares_with(a, b, shared=None):
    """Why two live tests touch the same couples or outcome, or None."""
    if shared:
        return f"{int(shared)} couples are in both"
    if same_arms(a, b):
        return "both use the same split of accounts"
    pages = _pages(a) & _pages(b)
    if pages:
        return "both on: " + ", ".join(sorted(p.split(":", 1)[1].replace("-", " ") for p in pages))
    ka, kb = v2_data._canon(a), v2_data._canon(b)
    la, lb = (str((x.get("metric") or {}).get("label") or "").strip().lower() for x in (a, b))
    if (ka and ka == kb and ka != "manual") or (ka == kb == "manual" and la and la == lb):
        return "both judged on the same measure"
    ra, rb = v2_data.exposure_rule(a), v2_data.exposure_rule(b)
    if ra and rb and ra["source"] == rb["source"] and ra["source"] != "xano":
        sa, sb = set(ra["surfaces"] or []), set(rb["surfaces"] or [])
        if not sa or not sb or sa & sb:
            return "both count couples from the same place"
    return None


def strip_cells(rows):
    """Cells of one test split by another test's versions say something about both: they leave a row only when both
    have been read."""
    read = {r.get("id") for r in rows if _is_read(r)}
    for r in rows:
        ov = (r.get("readout") or {}).get("overlap")
        if isinstance(ov, dict):
            r["readout"] = dict(r["readout"], overlap={
                k: v if r.get("id") in read and k in read else {"shared": (v or {}).get("shared")}
                for k, v in ov.items()})


def same_arms(a, b):
    """Two splits whose arms are the same couples: their effects can't be told apart."""
    sa, sb = settings(a), settings(b)
    return readlock.is_split(a) and readlock.is_split(b) and \
        all(sa[k] == sb[k] for k in ("rule", "salt", "percent", "treatment"))


def _is_read(row):
    return bool((row.get("read") or {}).get("call")) and not row.get("result_hidden")


def attach_overlaps(rows, entries):
    from . import v2_cards
    live = [r for r in rows if r.get("lifecycle") == "live" and r.get("id") in entries]
    for r in live:
        a = entries[r["id"]]
        out = []
        ov = (r.get("readout") or {}).get("overlap") or {}
        for o in live:
            if o["id"] == r["id"]:
                continue
            b = entries[o["id"]]
            hit = ov.get(o["id"]) if isinstance(ov.get(o["id"]), dict) else {}
            why = shares_with(a, b, hit.get("shared"))
            if not why:
                continue
            row = {"id": o["id"], "name": o.get("name"), "plain_name": b.get("plain_name") or v2_cards.short_name(b), "why": why, "shared": hit.get("shared"),
                   "same_arms": same_arms(a, b), "both_split": readlock.is_split(a) and readlock.is_split(b),
                   "other_read": _is_read(o)}
            if _is_read(r) and _is_read(o):                        # only when BOTH tests have been read
                row["cells"] = hit.get("cells")
            out.append(row)
        r["overlaps"] = out
