"""Read a split whose exposure is a page event the engine can't see itself (Mixpanel export), e.g. Price Report v2.

Rules (ww-price-report-v2 README "Reading the test"; FRAMEWORK section 14). None of them depends on the outcome:

* the version comes from ``metrics.split_arm(account id)``, never from the ``arm`` on the event;
* every card-seen view is kept. ``access_settled`` and ``locked`` are never filters;
* members are left out by account: entitlement at their first card-seen, from the purchase log;
* a couple with a ``late-id`` event is left out by account, in both arms. ``arm != late_arm`` is never a filter;
* an account recorded seeing both versions is left out; repeat accounts too when person keys are given;
* the ``access_settled`` share per arm is a health diagnostic only.

Local files in, numbers out: no network. Before the read date the command prints couples per version only.

    python -m experiments_engine.event_read --id EXP-2026-11-price-report-v2 --events ev.json --payments pay.json

events: [{"event", "user_id", "time", "arm"?, "access_settled"?}], payments: table-16 rows (Client_Reference_ID,
Time_of_Payment, Type).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from . import metrics as _m
from . import readlock, spec
from . import xano as _x

SEEN = ("card-seen", "card_seen", "price_report_card_seen", "price_report_seen")
CLICK = ("unlock-clicked", "unlock_clicked", "clicked_membership_for_pricing_venue_price_report")
LATE = ("late-id", "late_id", "price_report_late_id")
VERSIONS = {"v1": "control", "v2": "treatment", "control": "control", "treatment": "treatment"}
_WEEKS = re.compile(r"(\d+)\s*week")


def plan_days(kind):
    """Days of access a purchase gives: None = forever. 0 = not a known plan."""
    t = str(kind or "").strip().lower()
    if t.startswith("forever") or "lifetime" in t:
        return None
    g = _WEEKS.search(t)
    if g:
        return int(g.group(1)) * 7
    g = re.search(r"(\d+)\s*w\b", t)
    return int(g.group(1)) * 7 if g else 0


def entitled_at(purchases, t_ms):
    """True when the purchases BEFORE t_ms give access at t_ms (time left carries over to the next purchase)."""
    until = None
    for t, kind in sorted(purchases, key=lambda p: p[0]):
        if t >= t_ms:
            break
        d = plan_days(kind)
        if d is None:
            return True
        until = max(until or t, t) + d * _m.DAY
    return until is not None and until > t_ms


def _rows(events, names):
    out = []
    for e in events or []:
        if isinstance(e, dict) and str(e.get("event") or "") in names:
            uid, t = _x.to_int(e.get("user_id", e.get("distinct_id"))), _x.to_ms(e.get("time", e.get("created_at")))
            if uid > 0 and t is not None:
                out.append((t, uid, e))
    out.sort(key=lambda r: r[0])
    return out


def read_events(entry, events, payments, now_ms=None, days=7, internal=(), who=None,
                seen=SEEN, click=CLICK, late=LATE):
    """Readout for one split (arms, exposed, exposure_by_day, mixing, members, settled, last_exposure_at)."""
    now_ms = now_ms or _m._utcnow_ms()
    rd = readlock.read_date(entry)
    cap = _m.parse_ms(rd.isoformat()) if rd else None
    if cap:
        now_ms = min(now_ms, cap)
    w = _m.build_window(entry, now_ms)
    internal = set(_x.INTERNAL_IDS) | {_x.to_int(i) for i in internal}
    forced = {_x.to_int(i) for i in (w["assignment"].get("force_ids") or w["assignment"].get("forced_ids") or [])}
    buys = {}
    for p in payments or []:
        uid, t = _x.to_int(p.get("Client_Reference_ID")), _x.to_ms(p.get("Time_of_Payment"))
        if uid and t is not None:
            buys.setdefault(uid, []).append((t, p.get("Type")))
    first, recorded, views = {}, {}, []
    for t, uid, e in _rows(events, seen):
        if not (w["start_ms"] <= t < w["end_ms"]) or uid in internal or uid in forced:
            continue
        arm = _m.split_arm(uid, w["assignment"])
        if arm is None:
            continue
        first.setdefault(uid, t)
        views.append((uid, arm, e.get("access_settled")))
        v = VERSIONS.get(str(e.get("arm") or "").lower())
        if v:
            recorded.setdefault(uid, set()).add(v)
    late_ids = {uid for t, uid, _e in _rows(events, late) if w["start_ms"] <= t < w["end_ms"]}
    clicks = {}
    for t, uid, _e in _rows(events, click):
        clicks.setdefault(uid, []).append(t)

    members = {u for u, t in first.items() if entitled_at(buys.get(u, ()), t)}
    pool = {u: _m.split_arm(u, w["assignment"]) for u in first if u not in members}
    late_hit = {u for u in pool if u in late_ids}
    both = {u for u in pool if len(recorded.get(u, ())) > 1}
    groups = {}
    for u in pool:
        k = (who or {}).get(u)
        if k:
            groups.setdefault(k, []).append(u)
    repeat = {u for g in groups.values() if len(g) > 1 for u in g}
    across = sum(1 for g in groups.values() if len({pool[u] for u in g}) > 1)
    bad = late_hit | both | repeat
    follow = int(days * _m.DAY)
    arms = {"control": {"n": 0, "conv": 0}, "treatment": {"n": 0, "conv": 0}}
    pending, expo = {}, {}
    exposed = {"control": 0, "treatment": 0}
    for u, arm in pool.items():
        t0 = first[u]
        exposed[arm] += 1
        expo.setdefault(_m.day_of(t0), {"control": 0, "treatment": 0})[arm] += 1
        if u in bad:
            continue
        if t0 + follow > now_ms:
            pending[arm] = pending.get(arm, 0) + 1
            continue
        arms[arm]["n"] += 1
        arms[arm]["conv"] += 1 if any(t0 <= t <= t0 + follow for t in clicks.get(u, ())) else 0
    settled = {k: {"settled": 0, "views": 0} for k in arms}
    for u, arm, s in views:
        if u in pool and s is not None:
            settled[arm]["views"] += 1
            settled[arm]["settled"] += 1 if s is True or str(s).lower() == "true" else 0
    last = max((first[u] for u in pool), default=None)
    mismatch = sum(1 for u, a in pool.items() if recorded.get(u) and recorded[u] != {a} and u not in both)
    return {"arms": arms, "pending": pending, "exposed": exposed, "exposure_by_day": dict(sorted(expo.items())),
            "mixing": {"exposed": len(pool), "both_arms": len(both) + across, "repeat_accounts": len(repeat),
                       "late_id": len(late_hit), "excluded": len(bad), "other_version_recorded": mismatch,
                       "share": round(len(bad) / len(pool), 6) if pool else 0.0,
                       "checked": len(who) if who is not None else 0,
                       "arm_recorded": sum(1 for u in pool if recorded.get(u))},
            "members_left_out": len(members), "settled": settled, "error": None, "source": "events",
            "last_exposure_at": _m._dt.datetime.fromtimestamp(last / 1000, _m._dt.timezone.utc).isoformat() if last else None,
            "computed_at": _m._dt.datetime.fromtimestamp(now_ms / 1000, _m._dt.timezone.utc).isoformat()}


def exposure_log(ro, read=False):
    """Registry ``exposure_log`` rows: couples per version per day and setup counts, no outcome. The settled share is
    added only once the test has been read."""
    out = [{"date": d, **v} for d, v in ro["exposure_by_day"].items()]
    if out:
        m = ro["mixing"]
        out[-1].update(late_id=m["late_id"])
        if m.get("arm_recorded"):
            out[-1]["both_arms"] = m["both_arms"]
        if m.get("checked"):
            out[-1]["repeat_accounts"] = m["repeat_accounts"]
        if read:
            out[-1]["settled"] = ro["settled"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Couples per version (and, on the read date, the one read) from an event export")
    ap.add_argument("--id", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--payments", required=True)
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--registry", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                       "experiments", "experiment_log.json"))
    a = ap.parse_args(argv)

    def load(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    entry = next((e for e in load(a.registry).get("experiments") or [] if e.get("id") == a.id), None)
    if entry is None or not readlock.is_split(entry) or not spec.parse_date(entry.get("start_date")):
        print("not a started split: " + a.id)
        return 1
    ro = read_events(entry, load(a.events), load(a.payments), days=a.days)
    rd = readlock.read_date(entry)
    closed = readlock.hidden(entry) or rd is None
    out = {"exposure_log": exposure_log(ro, read=not closed), "members_left_out": ro["members_left_out"],
           "mixing": ro["mixing"]}
    if closed:
        out["result"] = readlock.hidden_text(entry)
    else:
        out["results_entry"] = {"date": rd.isoformat(), "counts": ro["arms"]}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
