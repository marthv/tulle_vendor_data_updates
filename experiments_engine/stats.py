"""Bayesian statistics for Tulle experiments (FRAMEWORK.md section 4).

Pure stdlib (math, random). Every function is deterministic: Monte Carlo parts use a
private ``random.Random(seed)`` with a fixed default seed, and the headline probabilities
are computed exactly where that is cheap (closed forms for integer posterior shapes,
which is always the case with the flat priors used here and integer counts).

Models
------
* Rates (conversion metrics): Beta-Binomial with a flat Beta(1, 1) prior.
* Counts per day (purchases/day guardrails): Gamma-Poisson with a Gamma(1, 0) prior
  (posterior rate ~ Gamma(count + 1, rate=days)).

No function raises on zero or odd counts: conversions are clamped to [0, n], negative
inputs are treated as 0, and quantities that cannot be computed come back as ``None``.
"""
from __future__ import annotations

import math
import random

DEFAULT_SEED = 20261119
DEFAULT_DRAWS = 40000
# Above this posterior alpha the exact P(T > C) sum gets long; fall back to Monte Carlo.
_EXACT_MAX_ALPHA = 200000

Z_ALPHA = 1.645  # one-sided 95%
Z_BETA = 0.84    # 80% power
Z_TWO_SIDED = 1.959964   # EXP_TAB_V2: two-sided 5%
Z_POWER = 0.841621       # 80% power


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _clean(n, conv):
    """Return integer (n, conv) with 0 <= conv <= n; junk becomes 0."""
    try:
        n = int(round(float(n or 0)))
    except (TypeError, ValueError):
        n = 0
    try:
        conv = int(round(float(conv or 0)))
    except (TypeError, ValueError):
        conv = 0
    n = max(n, 0)
    conv = min(max(conv, 0), n)
    return n, conv


def _lbeta(a, b):
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = q * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _round(x, nd=6):
    return None if x is None else round(float(x), nd)


def prob_beta_greater(a_t, b_t, a_c, b_c):
    """Exact P(T > C) for T ~ Beta(a_t, b_t), C ~ Beta(a_c, b_c); a_t must be an integer.

    Closed form (Evan Miller, "Formulas for Bayesian A/B testing"):
    sum_{i=0}^{a_t-1} B(a_c+i, b_t+b_c) / ((b_t+i) B(1+i, b_t) B(a_c, b_c)).
    """
    a_t = int(a_t)
    total = 0.0
    base = -_lbeta(a_c, b_c)
    for i in range(a_t):
        total += math.exp(
            _lbeta(a_c + i, b_t + b_c) - math.log(b_t + i) - _lbeta(1 + i, b_t) + base
        )
    return min(max(total, 0.0), 1.0)


def _reg_inc_beta_int(x, a, b):
    """Regularised incomplete beta I_x(a, b) for positive integers a, b.

    Uses I_x(a, b) = P(Binomial(a+b-1, x) >= a), summed in log space.
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    a, b = int(a), int(b)
    n = a + b - 1
    lx, l1x = math.log(x), math.log1p(-x)
    lgn = math.lgamma(n + 1)
    total = 0.0
    for j in range(a, n + 1):
        total += math.exp(lgn - math.lgamma(j + 1) - math.lgamma(n - j + 1) + j * lx + (n - j) * l1x)
    return min(max(total, 0.0), 1.0)


# ---------------------------------------------------------------------------
# rate metrics
# ---------------------------------------------------------------------------

def beta_binomial(c_n, c_conv, t_n, t_conv, draws=DEFAULT_DRAWS, seed=DEFAULT_SEED,
                  direction="increase"):
    """Compare two rates with a Beta-Binomial model, flat Beta(1,1) prior (section 4).

    Returns a dict:
      p_beat            P(treatment is better than control) - exact when cheap, else MC.
                        "Better" means higher for direction="increase", lower for "decrease".
      lift_median_rel   median of (T - C) / C over posterior draws.
      lift_ci95_rel     [2.5%, 97.5%] of the same.
      expected_loss_abs E[max(C - T, 0)] (increase) / E[max(T - C, 0)] (decrease): what we
                        lose in absolute rate if we ship treatment and are wrong.
      control_rate, treatment_rate  observed conv/n (None when n == 0).
      plus c_n, c_conv, t_n, t_conv, draws, method.
    """
    c_n, c_conv = _clean(c_n, c_conv)
    t_n, t_conv = _clean(t_n, t_conv)
    a_c, b_c = 1 + c_conv, 1 + c_n - c_conv
    a_t, b_t = 1 + t_conv, 1 + t_n - t_conv
    try:
        draws = max(int(draws), 100)
    except (TypeError, ValueError):
        draws = DEFAULT_DRAWS
    decrease = str(direction or "increase").lower().startswith("dec")

    rng = random.Random(seed)
    lifts = []
    loss_sum = 0.0
    wins = 0
    for _ in range(draws):
        c = rng.betavariate(a_c, b_c)
        t = rng.betavariate(a_t, b_t)
        if (t < c) if decrease else (t > c):
            wins += 1
        loss_sum += max(t - c, 0.0) if decrease else max(c - t, 0.0)
        if c > 0:
            lifts.append((t - c) / c)
    lifts.sort()

    if a_t <= _EXACT_MAX_ALPHA:
        p_gt = prob_beta_greater(a_t, b_t, a_c, b_c)
        p_beat = 1.0 - p_gt if decrease else p_gt
        method = "exact"
    else:
        p_beat = wins / draws
        method = "monte_carlo"

    return {
        "p_beat": _round(p_beat),
        "lift_median_rel": _round(_quantile(lifts, 0.5)),
        "lift_ci95_rel": [_round(_quantile(lifts, 0.025)), _round(_quantile(lifts, 0.975))],
        "expected_loss_abs": _round(loss_sum / draws, 8),
        "control_rate": _round(c_conv / c_n) if c_n else None,
        "treatment_rate": _round(t_conv / t_n) if t_n else None,
        "c_n": c_n, "c_conv": c_conv, "t_n": t_n, "t_conv": t_conv,
        "draws": draws, "method": method, "model": "beta_binomial",
    }


def benchmark(n, conv, bench_n, bench_conv, draws=DEFAULT_DRAWS, seed=DEFAULT_SEED,
              direction="increase"):
    """One group against a fixed past rate (assignment type "benchmark", section 2).

    The benchmark is treated as the control arm with its own Beta posterior, so its
    sampling uncertainty is carried into P(beat) rather than treating it as exact.
    Same return shape as :func:`beta_binomial`.
    """
    out = beta_binomial(bench_n, bench_conv, n, conv, draws=draws, seed=seed, direction=direction)
    out["model"] = "benchmark"
    return out


def beta_threshold_prob(c_n, c_conv, t_n, t_conv, factor, below=True,
                        draws=DEFAULT_DRAWS, seed=DEFAULT_SEED):
    """P(T < factor * C) (below=True) or P(T > factor * C), Monte Carlo.

    Used for rate guardrails, e.g. factor=0.75 for "max_drop_rel": 0.25.
    """
    c_n, c_conv = _clean(c_n, c_conv)
    t_n, t_conv = _clean(t_n, t_conv)
    rng = random.Random(seed)
    hits = 0
    draws = max(int(draws or DEFAULT_DRAWS), 100)
    for _ in range(draws):
        c = rng.betavariate(1 + c_conv, 1 + c_n - c_conv)
        t = rng.betavariate(1 + t_conv, 1 + t_n - t_conv)
        if (t < factor * c) if below else (t > factor * c):
            hits += 1
    return _round(hits / draws)


# ---------------------------------------------------------------------------
# count metrics (guardrails)
# ---------------------------------------------------------------------------

def gamma_poisson(pre_count, pre_days, post_count, post_days, max_drop_rel=0.2):
    """Compare a daily count (e.g. purchases/day) before vs after, Gamma-Poisson (section 4).

    Posterior rate ~ Gamma(count + 1, rate=days). Probabilities are exact: for integer
    shapes, P(post < k * pre) = I_x(a_post, a_pre) with x = k*d_post / (d_pre + k*d_post).

    Returns {p_post_gt_pre, p_drop, drop_threshold_rel, pre_rate, post_rate, rate_ratio}.
    ``p_drop`` = P(post rate < (1 - max_drop_rel) * pre rate); with the default 0.2 that is
    P(post < 0.8 * pre). Values are None when either period has no days.
    """
    try:
        pre_days = float(pre_days or 0)
        post_days = float(post_days or 0)
    except (TypeError, ValueError):
        pre_days = post_days = 0.0
    pre_count, _ = _clean(pre_count, 0)
    post_count, _ = _clean(post_count, 0)
    try:
        drop = float(max_drop_rel if max_drop_rel is not None else 0.2)
    except (TypeError, ValueError):
        drop = 0.2
    out = {
        "p_post_gt_pre": None, "p_drop": None, "drop_threshold_rel": drop,
        "pre_rate": _round(pre_count / pre_days) if pre_days > 0 else None,
        "post_rate": _round(post_count / post_days) if post_days > 0 else None,
        "rate_ratio": None, "model": "gamma_poisson",
    }
    if pre_days <= 0 or post_days <= 0:
        return out
    a_post, a_pre = post_count + 1, pre_count + 1

    def p_post_below(k):
        x = k * post_days / (pre_days + k * post_days)
        return _reg_inc_beta_int(x, a_post, a_pre)

    out["p_post_gt_pre"] = _round(1.0 - p_post_below(1.0))
    out["p_drop"] = _round(p_post_below(max(1.0 - drop, 0.0)))
    if out["pre_rate"]:
        out["rate_ratio"] = _round(out["post_rate"] / out["pre_rate"])
    return out


# ---------------------------------------------------------------------------
# sample planning
# ---------------------------------------------------------------------------

def two_sided_n(p_control, p_treatment, z_alpha=Z_TWO_SIDED, z_beta=Z_POWER):
    """Couples per arm for a two-sided 5% test at 80% power, with the variance of BOTH arms:
    (z_a + z_b)^2 * [p1(1-p1) + p2(1-p2)] / (p2 - p1)^2 (FRAMEWORK section 14)."""
    p1, p2 = float(p_control), float(p_treatment)
    return (z_alpha + z_beta) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p2 - p1) ** 2


def prob_ratio_above(a_t, b_t, a_c, b_c, k=1.0, steps=800):
    """P(T > k * C) for T ~ Beta(a_t, b_t), C ~ Beta(a_c, b_c), integer shapes. Deterministic: exact for k = 1
    (and the smaller of the two arms is summed), else Simpson integration over C."""
    if k == 1.0:
        if a_t <= _EXACT_MAX_ALPHA:
            return prob_beta_greater(a_t, b_t, a_c, b_c)
    mean, n = a_c / (a_c + b_c), a_c + b_c
    sd = math.sqrt(mean * (1 - mean) / (n + 1))
    lo, hi = max(0.0, mean - 10 * sd), min(1.0, mean + 10 * sd)
    if a_c == 1 or b_c == 1:              # skewed against an edge: cover it all
        lo, hi = (0.0, hi) if a_c == 1 else (lo, 1.0)
    h = (hi - lo) / steps
    lb = _lbeta(a_c, b_c)
    mt, nt = a_t / (a_t + b_t), a_t + b_t
    sdt = math.sqrt(mt * (1 - mt) / (nt + 1))
    total = norm = 0.0
    for i in range(steps + 1):
        x = lo + i * h
        if x <= 0:
            pdf = math.exp(-lb) if a_c == 1 else 0.0
        elif x >= 1:
            pdf = math.exp(-lb) if b_c == 1 else 0.0
        else:
            pdf = math.exp((a_c - 1) * math.log(x) + (b_c - 1) * math.log1p(-x) - lb)
        w = 1 if i in (0, steps) else (4 if i % 2 else 2)
        norm += w * pdf
        if pdf:
            y = k * x
            if y <= 0:
                sf = 1.0
            elif y >= 1:
                sf = 0.0
            elif abs(y - mt) > 12 * sdt and nt > 50:
                sf = 1.0 if y < mt else 0.0
            else:
                sf = 1.0 - inc_beta(y, a_t, b_t)
            total += w * pdf * sf
    return min(max(total / norm, 0.0), 1.0) if norm else 0.5


def inc_beta(x, a, b):
    """Regularised incomplete beta I_x(a, b) (continued fraction, Lentz)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log1p(-x) - _lbeta(a, b))
    if x < (a + 1) / (a + b + 2):
        return front * _beta_cf(x, a, b) / a
    return 1.0 - front * _beta_cf(1 - x, b, a) / b


def _beta_cf(x, a, b, eps=1e-12, tiny=1e-300):
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    out = d
    for m in range(1, 1000):
        m2 = 2 * m
        for num in (m * (b - m) * x / ((a + m2 - 1) * (a + m2)),
                    -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1))):
            d = 1.0 + num * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + num / c
            c = c if abs(c) > tiny else tiny
            delta = c * d
            out *= delta
        if abs(delta - 1.0) < eps:
            break
    return out


def sample_plan(baseline, mde_rel=None, mde_abs=None, daily_exposed_per_arm=None, direction="increase",
                two_sided=None):
    """Planning estimate of sample size (section 4).

    two_sided: None = on when EXP_TAB_V2 is on. Two-sided plans use both arms' variance (section 14).

    n per arm = (1.645 + 0.84)^2 * 2p(1-p) / d^2, d = mde_abs or baseline * mde_rel.
    days_needed = ceil(n / daily_exposed_per_arm).

    Returns {n_per_arm, days_needed, d_abs, baseline, daily_exposed_per_arm, reason};
    n_per_arm / days_needed are None (with a reason) when inputs are missing or invalid.
    """
    out = {"n_per_arm": None, "days_needed": None, "d_abs": None, "baseline": None,
           "daily_exposed_per_arm": None, "reason": None}
    try:
        p = float(baseline)
    except (TypeError, ValueError):
        out["reason"] = "no baseline rate"
        return out
    out["baseline"] = p
    if not (0 < p < 1):
        out["reason"] = "baseline must be between 0 and 1"
        return out
    d = None
    try:
        if mde_abs not in (None, ""):
            d = abs(float(mde_abs))
        elif mde_rel not in (None, ""):
            d = abs(float(mde_rel)) * p
    except (TypeError, ValueError):
        d = None
    if not d:
        out["reason"] = "no smallest-lift-worth-having (mde_rel or mde_abs)"
        return out
    out["d_abs"] = _round(d, 8)
    if two_sided is None:
        from . import readlock
        two_sided = readlock.enabled()
    if two_sided:
        p2 = p - d if str(direction or "").lower().startswith("dec") else p + d
        if not 0 < p2 < 1:
            p2 = p - d if p2 >= 1 else p + d       # no room that way: plan the same distance the other way
        if not 0 < p2 < 1:
            out["reason"] = "the lift worth having is bigger than the rate allows"
            return out
        n = two_sided_n(p, p2)
        out["target"] = _round(p2, 8)
        out["two_sided"] = True
    else:
        n = (Z_ALPHA + Z_BETA) ** 2 * 2 * p * (1 - p) / d ** 2
    out["n_per_arm"] = int(math.ceil(n))
    try:
        rate = float(daily_exposed_per_arm) if daily_exposed_per_arm is not None else None
    except (TypeError, ValueError):
        rate = None
    if rate is None:
        out["reason"] = "no daily exposure estimate"
        return out
    out["daily_exposed_per_arm"] = rate
    if rate <= 0:
        out["reason"] = "daily exposure is zero"
        return out
    out["days_needed"] = int(math.ceil(out["n_per_arm"] / rate))
    return out
