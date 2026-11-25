"""EXP_TAB_V2: one plain-English card per open test (experiments/CARDS.md). Data only; experiments_ui/cards.py draws it.

``light`` (green / amber / red) is the health of the test. Nothing else on a card has a colour.
"""
from __future__ import annotations

import re

from . import readlock, spec, v2, v2_data

_PLAIN_RED = {"guardrail_breach": "A protected measure crossed its limit", "no_data": "No data coming in"}
_PLAIN_AMBER = {"starved": "Too few couples to reach an answer", "zombie": "Running far past its plan",
                "manual_overdue": "Waiting for a read-out", "confounded": "Other changes overlap it",
                "spec_incomplete": "The plan is incomplete",
                "guardrail_unmonitored": "A protected measure is not watched",
                "needs_call": "Past its review date", "paused": "Paused"}


# words couples' team members would not use -> plain words (shown text only; the registry keeps its own)
_PLAIN = [(r"\s*\(?\bsplit by user[ _-]?id parity\)?", ", half by account number"), (r"\buser[ _-]?id parity\b", "account number"),
          (r"\bparity\b", "odd or even account number"), (r"\barms\b", "versions"), (r"\barm\b", "version"),
          (r"\brec-engine\b", "the Planner's server"), (r"\bweweb\b", "the website"), (r"\bxano\b", "the database"),
          (r"\(table \d+[^)]*\)", ""), (r"\btable \d+\b", "our records"), (r"\bhash(ed)?\b", "random"),
          (r"\bREC_[A-Z_]+(=\w+)?", ""), (r"\bvs\.?\b", "against"), (r"\bmetric\b", "measure"),
          (r"\bpublish(ed)?\b(?= |$)", "release"), (r"\bsegment\b", "group")]


def plain(text):
    s = str(text or "")
    for pat, sub in _PLAIN:
        s = re.sub(pat, sub, s, flags=re.I if pat.islower() else 0)
    s = re.sub(r"\s+([,.;:)])", r"\1", re.sub(r"\s{2,}", " ", s)).strip(" ,;:-")
    return s[:1].upper() + s[1:] if re.match(r"[a-z]+[ ,.-][a-z0-9]", s) else s


def short_name(e):
    n = str(e.get("plain_name") or "").strip()
    if n:
        return n
    n = str(e.get("name") or e.get("id") or "").strip()
    for sep in (": ", " (", " - ", ", "):
        head = n.split(sep, 1)[0]
        if len(n) > 46 and len(head) >= 14:
            n = head
            break
    n = plain(n)
    return n if len(n) <= 60 else n[:57].rsplit(" ", 1)[0] + "…"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def short_date(d):
    return f"{_MONTHS[d.month - 1]} {d.day}" if d else None


_PROBLEM = {"balance": "The two versions are not split as planned", "mixing": "Too many repeat or mixed accounts",
            "settings": "Settings changed after launch", "exposure": "Couples have stopped coming in",
            "coverage": "Too many couples can't be measured", "data": "The measure can't be read",
            "harm": "Clear harm: stop the test", "read_date": "No read date is set"}


def note_line(card, row, e):
    """The one line under the name: the problem, else the owner's plain line, else where it stands."""
    bad = [c for c in card.get("checks") or [] if c["colour"] == v2.RED]
    if bad:
        first = next((c for c in bad if c["key"] == "harm"), bad[0])
        return _PROBLEM.get(first["key"], first["label"])
    if e.get("plain_line"):
        return str(e["plain_line"])
    if card["split"] and row.get("result_hidden"):
        return "Result hidden until its read date"
    if card["split"] and row.get("read"):
        return (row.get("verdict") or {}).get("result", {}).get("label") or "Read"
    if card.get("lifecycle") in ("planned", "draft"):
        return "Not started"
    return card.get("next") or ""


def _audience(e):
    return e.get("audience") if isinstance(e.get("audience"), dict) else {}


def _who_counts(e):
    seg = _audience(e).get("segment") or (e.get("audience") if isinstance(e.get("audience"), str) else None)
    bits = [plain(seg).rstrip(". ") if seg else "Not written down"]
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
            checks.append(v2._check(f, _PLAIN_AMBER[f], v2.AMBER, ""))
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
            "measure": plain((e.get("metric") or {}).get("label")) if isinstance(e.get("metric"), dict) else None,
            "plain_name": short_name(e), "read_short": short_date(rd)}
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
    card["so_far"], card["needed"] = None, need
    if split:
        fin = row.get("finished")
        card["so_far"] = min(fin.values()) if fin else None
        share, est, ex = v2.treatment_share(e), spec._num(_audience(e).get("est_daily_exposed")), row.get("exposed")
        ran = min(days, total) if (days is not None and total) else days
        card["arms"] = []
        for arm, label, s in (("treatment", "New version", share), ("control", "Old version", 1 - share)):
            exp = est * s * max(ran, 0) if est is not None and ran is not None and running else None
            card["arms"].append({"label": label, "n": ex[arm] if ex else None, "finished": fin[arm] if fin else None,
                                 "expected": None if exp is None else int(round(exp)), "needed": need})
        if need:
            card["sample_text"] = f"{need:,} finished couples needed per version."
        else:
            card["sample_text"] = "How many couples are needed can't be worked out" + (
                f" ({plan['reason']})." if plan.get("reason") else ".")
    elif running:
        arms = (row.get("readout") or {}).get("arms") or {}
        n = (arms.get("treatment") or {}).get("n") if isinstance(arms, dict) else None
        card["so_far"] = n

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
        c = {"id": o["id"], "name": o.get("plain_name") or o.get("name"), "cells": o.get("cells"),
             "text": f"Shares couples with {o.get('plain_name') or o.get('name') or o['id']}" +
                     (" (same couples get the new version, so the two can't be told apart)"
                      if o.get("same_arms") else f" ({o['why']})" if o.get("shared") else "")}
        if v2._is_read(row) and o.get("both_split") and not o.get("cells"):
            c["note"] = "2x2 table: not until both tests are read" if not o.get("other_read") else \
                "2x2 table not available: no per-couple data for both tests."
        card["overlaps"].append(c)

    hist = readlock.public_history(e, today, row.get("read_date") if split else None)
    if hist:
        fam = e.get("family_card") if isinstance(e.get("family_card"), str) else e.get("family")
        card["family"] = fam if isinstance(fam, str) else None
        card["history"] = [{"version": r.get("version"), "from": r.get("from"), "to": r.get("to"),
                            "outcome": r.get("outcome")} for r in hist]
    card["note"] = note_line(card, row, e)
    return card
