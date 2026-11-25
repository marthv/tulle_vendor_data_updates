"""EXP_TAB_V2: one plain-English card per open test (experiments/CARDS.md). Data only; experiments_ui/cards.py draws it.

``light`` (green / amber / red) is the health of the test. Nothing else on a card has a colour.
"""
from __future__ import annotations

from . import readlock, spec, v2, v2_data

_PLAIN_RED = {"guardrail_breach": "A protected measure crossed its limit", "no_data": "No data coming in"}
_PLAIN_AMBER = {"starved": "Too few couples to reach an answer", "zombie": "Running far past its plan",
                "manual_overdue": "Waiting for a read-out", "confounded": "Other changes overlap it",
                "spec_incomplete": "The plan is incomplete",
                "guardrail_unmonitored": "A protected measure is not watched",
                "needs_call": "Past its review date", "paused": "Paused"}


def _audience(e):
    return e.get("audience") if isinstance(e.get("audience"), dict) else {}


def _who_counts(e):
    seg = _audience(e).get("segment") or (e.get("audience") if isinstance(e.get("audience"), str) else None)
    bits = [str(seg).rstrip(". ") if seg else "Not written down"]
    rule = v2_data.exposure_rule(e)
    if readlock.is_split(e) and rule and rule["surfaces"]:
        bits.append("Only couples seen on: " + ", ".join(v2.SURFACE_NAMES.get(s, s) for s in rule["surfaces"]))
    return ". ".join(bits)


def plain_light(row):
    """Health of a test that is not a split, from its health flags. callable_* are results: they get no colour."""
    checks = []
    for f in row.get("flags") or []:
        why = str(row.get("reason") or "") if row.get("health") == f else ""
        if f in _PLAIN_RED:
            checks.append(v2._check(f, _PLAIN_RED[f], v2.RED, why))
        elif f in _PLAIN_AMBER:
            checks.append(v2._check(f, _PLAIN_AMBER[f], v2.AMBER, why))
    if not checks:
        checks.append(v2._check("ok", "Running normally", v2.GREEN, ""))
    return v2.worst(checks), checks


def build_card(row, e, today):
    """Everything the dash shows for one open test, in plain words (before lock_row, so counts are complete)."""
    split = readlock.is_split(e)
    life = row.get("lifecycle")
    running = life in ("live", "paused")
    sd = spec.parse_date(e.get("start_date"))
    if split:
        rd = spec.parse_date(row.get("read_date")) or readlock.read_date(e)
    else:
        rd = spec.parse_date(e.get("review_date")) or readlock.registry_read_date(e)
    planned = spec._num(e.get("planned_days"))
    total = (rd - sd).days if rd and sd else (int(planned) if planned else None)
    days = row.get("days_running")
    read_label = "Read date" if split else "Review date"
    card = {"id": row.get("id"), "name": row.get("name"), "status": row.get("status"), "lifecycle": life,
            "split": split, "changed": e.get("what_changed") or e.get("what") or e.get("goal"),
            "who": _who_counts(e), "read_date": rd.isoformat() if rd else None, "read_label": read_label,
            "started": sd.isoformat() if sd else None,
            "measure": (e.get("metric") or {}).get("label") if isinstance(e.get("metric"), dict) else None}
    if not running:
        card["day_text"] = "Not started" + (f". Planned for {total} days" if total else "")
    elif days is None:
        card["day_text"] = "Start date missing"
    else:
        card["day_text"] = f"Day {days}" + (f" of {total}" if total else "")
        if rd and today > rd:
            card["day_text"] += f" (past its {read_label.lower()})"

    plan = row.get("sample_plan") or (row.get("success_metric") or {}).get("sample_plan") or {}
    need = plan.get("n_per_arm")
    if split:
        share, est, ex = v2.treatment_share(e), spec._num(_audience(e).get("est_daily_exposed")), row.get("exposed")
        ran = min(days, total) if (days is not None and total) else days
        card["arms"] = []
        for arm, label, s in (("treatment", "New version", share), ("control", "Old version", 1 - share)):
            exp = est * s * max(ran, 0) if est is not None and ran is not None and running else None
            card["arms"].append({"label": label, "n": ex[arm] if ex else None,
                                 "expected": None if exp is None else int(round(exp)), "needed": need})
        if need:
            card["sample_text"] = f"Plan: {need:,} couples per version (two-sided test, 80% power)"
        else:
            card["sample_text"] = "Plan: couples needed can't be worked out" + (
                f" ({plan['reason']})" if plan.get("reason") else "")

    if not running:
        card["result_text"] = f"Result: hidden until {rd.isoformat()}" if split and rd else \
            ("Result: hidden until a read date is set" if split else "Result: not started")
    elif split and row.get("result_hidden"):
        card["result_text"] = row.get("result_text")
    else:
        card["result_text"] = "Result: " + ((row.get("verdict") or {}).get("tldr") or "nothing to read yet.")
    rec = (row.get("verdict") or {}).get("recommend") or {}
    card["next"] = rec.get("label") or row.get("action")
    card["next_why"] = rec.get("why") if row.get("health") != "on_track" else None

    if split and running:
        card["payments_text"] = v2.payments_view(row.get("readout"),
                                                 bool(row.get("result_hidden")) and v2.is_money_metric(e))
        card["checks"], card["light"] = row.get("checks") or [], row.get("light") or v2.GREEN
    elif running:
        card["light"], card["checks"] = plain_light(row)
        watch = [g for g in row.get("guardrail_status") or [] if g.get("status") == "watch"]
        if watch:
            card["payments_text"] = "Payments, watch only: " + "; ".join(str(g.get("reason")) for g in watch)
    else:
        card["light"], card["checks"] = None, []
        if split:
            card["payments_text"] = "Payments, watch only: counted by version once it starts. They can't be read."

    card["overlaps"] = []
    for o in row.get("overlaps") or []:
        c = {"id": o["id"], "name": o.get("name"), "cells": o.get("cells"),
             "text": f"Shares couples with {o.get('name') or o['id']} ({o['why']})" +
                     ("; same versions go to the same couples, so the two can't be told apart"
                      if o.get("same_arms") else "")}
        if row.get("read") and not row.get("result_hidden") and o.get("both_split") and not o.get("cells"):
            c["note"] = "2x2 table not available: no per-couple data for both tests."
        card["overlaps"].append(c)

    hist = readlock.public_history(e, today, row.get("read_date") if split else None)
    if hist:
        fam = e.get("family_card") if isinstance(e.get("family_card"), str) else e.get("family")
        card["family"] = fam if isinstance(fam, str) else None
        card["history"] = [{"version": r.get("version"), "from": r.get("from"), "to": r.get("to"),
                            "outcome": r.get("outcome")} for r in hist]
    return card
