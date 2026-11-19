"""Group experiments by what they are meant to improve (FRAMEWORK.md question 2, "bucket").

A registry entry may set ``bucket`` (one of BUCKETS). Otherwise it is derived, first rule wins:

  1 reliability        performance theme; speed / cache / latency / stop button / progress labels
  2 onboarding         signup / legal / acquisition / ads themes; sign-up, T&C, welcome, landing page
  3 payment_funnel     a money primary metric (paid / offer buyers / gate payers / revenue per day)
  4 retention          email / reviews / feedback themes; NPS / Trustpilot metrics or surfaces
  4b payment_funnel    paywall / pricing / conversion themes or Stripe / plans surfaces, when no Planner theme
  5 planner_engagement planner / assistant / my-wedding / planning / cost-tools themes or Planner surfaces
  6 search             search / discovery / lookup themes; search-api, Meilisearch, ep119 surfaces
  7 data               data / data-gaps / coverage / pricing-intelligence themes; extraction, listings
  8 payment_funnel     paywall / pricing / upsell / revenue / conversion themes; Stripe or plans surfaces
  9 retention          retention theme
  9b onboarding        activation theme
  10 planner_engagement fallback
"""
from __future__ import annotations

import re

BUCKETS = (
    ("payment_funnel", "Payment funnel"),
    ("planner_engagement", "Planner engagement"),
    ("search", "Search"),
    ("data", "Data"),
    ("reliability", "Reliability"),
    ("onboarding", "Onboarding"),
    ("retention", "Retention"),
)
KEYS = tuple(k for k, _ in BUCKETS)
MONEY_METRICS = ("new_paid_within_days", "new_paid_within_n_days", "gate_hitters_paid", "paid_after_pdf_gate",
                 "paid_after_planner_gate", "offer_code_buyers", "save_offer_buyers", "paid_after_pricing_view",
                 "revenue_per_day")


def _has(text, pattern):
    return re.search(pattern, text, re.I) is not None


def derive(entry):
    """(bucket, source) for a registry entry or snapshot row; source is 'registry' or 'derived'."""
    b = str(entry.get("bucket") or "").strip().lower()
    if b in KEYS:
        return b, "registry"
    themes = {str(t).lower() for t in entry.get("themes") or []}
    surfaces = " ".join(str(s).lower() for s in entry.get("surfaces") or
                        (entry.get("impacts") or {}).get("surfaces") or [])
    metric = entry.get("metric") or ((entry.get("success_metric") or {}).get("metric")) or {}
    key = str(metric.get("key") or "")
    name = str(entry.get("name") or "")

    if "performance" in themes or _has(name, r"\bspeed\b|\bcache|latency|stop button|progress label"):
        return "reliability", "derived"
    if themes & {"signup", "legal", "acquisition", "ads"} or \
            _has(name, r"sign[- ]?up|T&C|terms|welcome|landing page") or \
            _has(surfaces, r"weweb:sign-up|weweb:lp|meta-pixel"):
        return "onboarding", "derived"
    if key in MONEY_METRICS:
        return "payment_funnel", "derived"
    if themes & {"email", "reviews", "feedback"} or key.startswith(("nps_", "trustpilot")) or \
            _has(surfaces, r"nps-survey|table77") or (_has(surfaces, r"brevo") and not themes & {"paywall", "pricing"}):
        return "retention", "derived"
    planner_theme = bool(themes & {"planner", "assistant", "my-wedding", "planning", "cost-tools"})
    if not planner_theme and (themes & {"paywall", "pricing", "upsell", "revenue", "conversion"} or
                              _has(surfaces, r"stripe|weweb:membership")):
        return "payment_funnel", "derived"
    if planner_theme or \
            _has(surfaces, r"weweb:planner|rec-engine|ask-tulle") or _has(name, r"planner"):
        return "planner_engagement", "derived"
    if themes & {"search", "discovery", "lookup"} or _has(surfaces, r"search-api|meilisearch|ep119"):
        return "search", "derived"
    if themes & {"data", "data-gaps", "coverage", "pricing-intelligence"} or \
            _has(name, r"extraction|listings|price report"):
        return "data", "derived"
    if themes & {"paywall", "pricing", "upsell", "revenue", "conversion", "free"} or \
            _has(surfaces, r"stripe|weweb:membership"):
        return "payment_funnel", "derived"
    if "retention" in themes:
        return "retention", "derived"
    if "activation" in themes:
        return "onboarding", "derived"
    return "planner_engagement", "derived"


def summary(experiments, decided):
    """snapshot['buckets']: fixed order, empty buckets included."""
    out = []
    for key, label in BUCKETS:
        running = [r["id"] for r in experiments if r.get("bucket") == key and r.get("lifecycle") in ("live", "paused")
                   and not (r.get("health") in ("needs_call", "callable_win", "callable_loss") or r.get("stale"))]
        waiting = [r["id"] for r in experiments if r.get("bucket") == key and r.get("lifecycle") in ("live", "paused")
                   and r["id"] not in running]
        dec = {"shipped": 0, "reverted": 0, "shelved": 0, "inconclusive": 0}
        for d in decided:
            if d.get("bucket") != key:
                continue
            s = str(d.get("outcome") or d.get("status") or "").lower()
            s = s if s in dec else "inconclusive"
            dec[s] += 1
        out.append({"key": key, "label": label, "running": running, "waiting": waiting, "decided": dec,
                    "planned": [r["id"] for r in experiments if r.get("bucket") == key and r.get("lifecycle") == "planned"]})
    return out
