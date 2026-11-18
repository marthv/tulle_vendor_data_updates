"""Named, read-only Xano reports for Slack (@Tulle Bot report requests).

Each report takes already-fetched rows (so the math is testable on small fixtures) and returns a Report:
a short summary for the thread + rows for the CSV. `run()` fetches through xano_ro.ReadOnlyXano.

Conventions (from experiments_brief.py / daily_brief.py): Xano timestamps are epoch ms, days are UTC;
table 16 Amount is DOLLARS, Time_of_Payment is the payment time, Client_Reference_ID is the table 1 user id;
table 79 questions = kind opening/refine with status ok, mcp_* rows are never Planner usage.
"""
from __future__ import annotations

import csv
import io
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DAY_MS = 86400000

USERS, PAYMENTS, NPS, USAGE = 1, 16, 77, 79
VENUE_LOG = "venue_lookup_log"
EXPORT_ALLOWLIST = {1: "users", 16: "payment_log", 77: "nps_response", 79: "rec_usage", VENUE_LOG: VENUE_LOG}

# Owner list 2026-11-18 + the ids experiments_brief.py already excludes (34249, 33167, 185).
DEFAULT_INTERNAL_IDS = {14998, 30614, 32636, 31619, 118, 5622, 35757, 2333, 34429, 36173, 35866, 34249, 33167, 185}

DENY_EXACT = {"password", "token_session", "token_session_axpires", "token_session_expires", "magic_link",
              "google_oauth"}
DENY_PARTS = ("token", "secret", "password", "oauth", "hash")

REPORTS = ("payments", "signups_and_pay_rate", "planner_usage", "venue_requests", "save_offer", "nps", "export")
OFFER_HOURS = 12


def max_rows() -> int:
    try:
        return int(os.environ.get("QA_REPORT_MAX_ROWS", "50000"))
    except ValueError:
        return 50000


def internal_ids() -> set[int]:
    raw = os.environ.get("QA_INTERNAL_IDS", "")
    extra = {int(x) for x in re.findall(r"\d+", raw)}
    return DEFAULT_INTERNAL_IDS | extra


def is_internal(uid=None, email=None) -> bool:
    e = str(email or "").strip().lower()
    try:
        i = int(str(uid or 0).strip() or 0)
    except ValueError:
        i = 0
    return i in internal_ids() or e.startswith("vivek.marthi") or e.endswith("@tulletogether.com")


def denied(col: str) -> bool:
    c = str(col).lower()
    return c in DENY_EXACT or any(p in c for p in DENY_PARTS)


def safe_columns(cols) -> list[str]:
    return [c for c in cols if not denied(c)]


# --- dates --------------------------------------------------------------------------------------------------
def to_ms(day: str | None, end: bool = False) -> int | None:
    if not day:
        return None
    d = datetime.strptime(day[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end:
        d += timedelta(days=1)
    return int(d.timestamp() * 1000)


def day_of(ms) -> str:
    return datetime.fromtimestamp((ms or 0) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def week_of(ms) -> str:
    d = datetime.fromtimestamp((ms or 0) / 1000, tz=timezone.utc)
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def in_window(ms, start_ms, end_ms) -> bool:
    ms = ms or 0
    return (start_ms is None or ms >= start_ms) and (end_ms is None or ms < end_ms)


def window_label(p: dict) -> str:
    s, e = p.get("start_date"), p.get("end_date")
    if s and e:
        return f"{s} to {e} (UTC)"
    if s:
        return f"since {s} (UTC)"
    if e:
        return f"up to {e} (UTC)"
    return "all time"


def _uid(v) -> int:
    try:
        return int(str(v or "").strip() or 0)
    except ValueError:
        return 0


def plan_of(type_value) -> str:
    t = str(type_value or "").strip().lower()
    if "forever" in t:
        return "forever"
    if t in ("1 week", "1 weeks"):
        return "1 week"
    if t in ("4 weeks", "4 week"):
        return "4 weeks"
    return t or "unknown"


def pay_time(p: dict) -> int:
    return p.get("Time_of_Payment") or p.get("created_at") or 0


@dataclass
class Report:
    name: str
    summary: str
    rows: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    excluded: int = 0
    truncated: bool = False

    def csv_bytes(self) -> bytes:
        cols = safe_columns(self.columns or (list(self.rows[0].keys()) if self.rows else []))
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in self.rows[:max_rows()]:
            w.writerow({c: _cell(r.get(c)) for c in cols})
        return buf.getvalue().encode("utf-8")


def _cell(v):
    """CSV cell; strings pass through scrub() so a stray token/key in a free-text column never leaves Xano."""
    if isinstance(v, (dict, list)):
        import json
        v = json.dumps(v, ensure_ascii=False)
    if isinstance(v, str):
        from scrub import scrub
        return scrub(v)
    return v


def _emails(users: list[dict]) -> dict[int, str]:
    return {_uid(u.get("id")): str(u.get("email") or "") for u in users}


def _excl_note(n: int, include_internal: bool) -> str:
    if include_internal:
        return "internal accounts INCLUDED"
    return f"excluded {n} internal/test row(s) (team + test accounts, @tulletogether.com, vivek.marthi*)"


# --- reports --------------------------------------------------------------------------------------------------
def payments(pay_rows: list[dict], users: list[dict], p: dict) -> Report:
    s, e = to_ms(p.get("start_date")), to_ms(p.get("end_date"), end=True)
    group = p.get("group_by") or "day"
    plan_f = (p.get("plan") or "").lower()
    emails = _emails(users)
    kept, excl = [], 0
    for r in pay_rows:
        if not in_window(pay_time(r), s, e):
            continue
        uid = _uid(r.get("Client_Reference_ID"))
        if not p.get("include_internal") and is_internal(uid, emails.get(uid)):
            excl += 1
            continue
        if plan_f and plan_of(r.get("Type")) != plan_of(plan_f):
            continue
        kept.append(r)
    agg = defaultdict(lambda: [0, 0.0])
    for r in kept:
        k = {"day": day_of, "week": week_of}.get(group, None)
        key = k(pay_time(r)) if k else plan_of(r.get("Type"))
        agg[key][0] += 1
        agg[key][1] += float(r.get("Amount") or 0)
    rows = [{group if group in ("day", "week") else "plan": k, "payments": v[0], "dollars": round(v[1], 2)}
            for k, v in sorted(agg.items())]
    total_n, total_d = len(kept), round(sum(float(r.get("Amount") or 0) for r in kept), 2)
    by_plan = Counter(plan_of(r.get("Type")) for r in kept)
    summary = (f"Payments (table 16), {window_label(p)}, by {group}: {total_n} payments, ${total_d:,.2f} "
               f"(Amount is gross dollars). Plans: " + (", ".join(f"{k} {v}" for k, v in by_plan.most_common()) or "none")
               + f". {_excl_note(excl, p.get('include_internal'))}.")
    return Report("payments", summary, rows, list(rows[0].keys()) if rows else [group, "payments", "dollars"], excl)


def signups_and_pay_rate(users: list[dict], pay_rows: list[dict], p: dict, now_ms: int) -> Report:
    s, e = to_ms(p.get("start_date")), to_ms(p.get("end_date"), end=True)
    first_pay: dict[int, int] = {}
    for r in pay_rows:
        uid, t = _uid(r.get("Client_Reference_ID")), pay_time(r)
        if uid and t and (uid not in first_pay or t < first_pay[uid]):
            first_pay[uid] = t
    weeks = defaultdict(lambda: [0, 0, 0])   # signups, paid within 7d, signups old enough (>=7d)
    excl = 0
    for u in users:
        c = u.get("created_at") or 0
        if not in_window(c, s, e):
            continue
        if not p.get("include_internal") and is_internal(u.get("id"), u.get("email")):
            excl += 1
            continue
        w = weeks[week_of(c)]
        w[0] += 1
        if now_ms - c >= 7 * DAY_MS:
            w[2] += 1
        fp = first_pay.get(_uid(u.get("id")))
        if fp is not None and 0 <= fp - c <= 7 * DAY_MS:
            w[1] += 1
    rows = []
    for k, (n, paid, mature) in sorted(weeks.items()):
        rows.append({"week_starting": k, "signups": n, "paid_within_7d": paid,
                     "pay_rate_7d_pct": round(100.0 * paid / n, 1) if n else 0.0,
                     "complete": "yes" if mature == n else "no (some sign-ups younger than 7 days)"})
    tot = sum(r["signups"] for r in rows)
    paid = sum(r["paid_within_7d"] for r in rows)
    summary = (f"Sign-ups (table 1) by week, {window_label(p)}: {tot} sign-ups, {paid} paid within 7 days "
               f"({(100.0 * paid / tot if tot else 0):.1f}%), first payment from table 16 joined on "
               f"Client_Reference_ID. Weeks marked complete=no still have sign-ups inside their 7-day window. "
               f"{_excl_note(excl, p.get('include_internal'))}.")
    return Report("signups_and_pay_rate", summary, rows,
                  ["week_starting", "signups", "paid_within_7d", "pay_rate_7d_pct", "complete"], excl)


def planner_usage(usage: list[dict], users: list[dict], p: dict) -> Report:
    s, e = to_ms(p.get("start_date")), to_ms(p.get("end_date"), end=True)
    group = p.get("group_by") if p.get("group_by") in ("day", "surface", "week") else "day"
    emails = _emails(users)
    kept, excl = [], 0
    for r in usage:
        if not in_window(r.get("created_at"), s, e) or str(r.get("kind") or "").startswith("mcp_"):
            continue
        uid = _uid(r.get("user_id"))
        if not p.get("include_internal") and is_internal(uid, emails.get(uid)):
            excl += 1
            continue
        kept.append(r)

    def key(r):
        if group == "surface":
            return str(r.get("surface") or "(none)")
        if group == "week":
            return week_of(r.get("created_at"))
        return r.get("usage_day") or day_of(r.get("created_at"))
    g = defaultdict(lambda: {"couples": set(), "questions": 0, "free_counted": 0, "kinds": Counter()})
    for r in kept:
        b = g[key(r)]
        kind = str(r.get("kind") or "")
        b["kinds"][kind] += 1
        if kind in ("opening", "refine"):
            b["couples"].add(_uid(r.get("user_id")))
            if r.get("status") == "ok":
                b["questions"] += 1
        if r.get("counted_as_free"):
            b["free_counted"] += 1
    rows = []
    for k, b in sorted(g.items()):
        n = len(b["couples"])
        rows.append({group: k, "couples": n, "questions": b["questions"],
                     "questions_per_couple": round(b["questions"] / n, 2) if n else 0.0,
                     "counted_as_free": b["free_counted"],
                     "kinds": ", ".join(f"{kk} {vv}" for kk, vv in b["kinds"].most_common())})
    all_couples = {_uid(r.get("user_id")) for r in kept if r.get("kind") in ("opening", "refine")}
    q = sum(1 for r in kept if r.get("kind") in ("opening", "refine") and r.get("status") == "ok")
    summary = (f"Planner usage (table 79), {window_label(p)}, by {group}: {len(all_couples)} couples asked "
               f"{q} questions ({(q / len(all_couples) if all_couples else 0):.2f} per couple). Question = "
               f"opening/refine row with status ok; mcp_* rows left out. {_excl_note(excl, p.get('include_internal'))}.")
    return Report("planner_usage", summary, rows,
                  [group, "couples", "questions", "questions_per_couple", "counted_as_free", "kinds"], excl)


def venue_requests(log_rows: list[dict] | None, p: dict) -> Report:
    if log_rows is None:
        return Report("venue_requests", "Venue requests: the `venue_lookup_log` table isn't available in Xano yet "
                                        "(it's still being built), so there's nothing to report.")
    s, e = to_ms(p.get("start_date")), to_ms(p.get("end_date"), end=True)
    kept, excl = [], 0
    for r in log_rows:
        if not in_window(r.get("created_at"), s, e):
            continue
        if not p.get("include_internal") and is_internal(r.get("user_id")):
            excl += 1
            continue
        kept.append(r)
    outcomes = Counter(str(r.get("outcome") or "(none)") for r in kept)
    names = Counter(str(r.get("normalized_name") or r.get("typed_text") or "").strip() for r in kept)
    names.pop("", None)
    top = ", ".join(f"{k} ({v})" for k, v in names.most_common(5)) or "none"
    summary = (f"Venue requests (venue_lookup_log), {window_label(p)}: {len(kept)} lookups. Outcomes: "
               + (", ".join(f"{k} {v}" for k, v in outcomes.most_common()) or "none")
               + f". Most asked: {top}. {_excl_note(excl, p.get('include_internal'))}.")
    cols = safe_columns(sorted({c for r in kept for c in r})) if kept else []
    return Report("venue_requests", summary, kept, cols, excl)


def save_offer(users: list[dict], pay_rows: list[dict], p: dict) -> Report:
    s, e = to_ms(p.get("start_date")), to_ms(p.get("end_date"), end=True)
    pays = defaultdict(list)
    for r in pay_rows:
        pays[_uid(r.get("Client_Reference_ID"))].append(r)
    rows, excl = [], 0
    for u in users:
        c60 = str(u.get("paywall_offer_code") or "")
        c20 = str(u.get("paywall_offer_code_4w") or "")
        if not (c60.startswith("TT60-") or c20.startswith("TT20-")):
            continue
        exp = int(u.get("paywall_offer_expires_at") or 0)
        made = exp - OFFER_HOURS * 3600000 if exp else 0
        if made and not in_window(made, s, e):
            continue
        if not p.get("include_internal") and is_internal(u.get("id"), u.get("email")):
            excl += 1
            continue
        uid = _uid(u.get("id"))
        after = sorted((r for r in pays.get(uid, []) if pay_time(r) >= made), key=pay_time)
        first = after[0] if after else None
        rows.append({"user_id": uid, "email": u.get("email"), "offer_made_utc": day_of(made) if made else "",
                     "tt60_code": c60 if c60.startswith("TT60-") else "",
                     "tt20_code": c20 if c20.startswith("TT20-") else "",
                     "bought_after_offer": "yes" if first else "no",
                     "plan": plan_of(first.get("Type")) if first else "",
                     "amount_dollars": float(first.get("Amount") or 0) if first else 0.0,
                     "paid_utc": day_of(pay_time(first)) if first else ""})
    bought = [r for r in rows if r["bought_after_offer"] == "yes"]
    summary = (f"Save offer (table 1 codes + table 16), offers made {window_label(p)}: {len(rows)} couples got a "
               f"code ({sum(1 for r in rows if r['tt60_code'])} TT60 forever, {sum(1 for r in rows if r['tt20_code'])} "
               f"TT20 4-week); {len(bought)} bought after the offer, ${sum(r['amount_dollars'] for r in bought):,.2f}. "
               f"Offer time = expiry minus {OFFER_HOURS} h. {_excl_note(excl, p.get('include_internal'))}.")
    return Report("save_offer", summary, rows, list(rows[0].keys()) if rows else
                  ["user_id", "email", "offer_made_utc", "tt60_code", "tt20_code", "bought_after_offer", "plan",
                   "amount_dollars", "paid_utc"], excl)


def nps(nps_rows: list[dict], p: dict) -> Report:
    s, e = to_ms(p.get("start_date")), to_ms(p.get("end_date"), end=True)
    kept, excl = [], 0
    for r in nps_rows:
        if not in_window(r.get("created_at"), s, e) or not (r.get("score") or 0):
            continue
        if not p.get("include_internal") and is_internal(r.get("user_id"), r.get("user_email")):
            excl += 1
            continue
        kept.append(r)
    dist = Counter(int(r["score"]) for r in kept)
    avg = sum(r["score"] for r in kept) / len(kept) if kept else 0
    summary = (f"Ratings (table 77), {window_label(p)}: {len(kept)} ratings, average {avg:.2f} / 5. "
               + ", ".join(f"{k} star: {dist.get(k, 0)}" for k in range(5, 0, -1))
               + f". {_excl_note(excl, p.get('include_internal'))}.")
    cols = ["id", "created_at_utc", "user_id", "user_email", "score", "comment", "page"]
    rows = [dict(r, created_at_utc=day_of(r.get("created_at"))) for r in kept]
    return Report("nps", summary, rows, cols, excl)


def export(table, rows: list[dict], users: list[dict], p: dict) -> Report:
    key = _table_key(table)
    if key not in EXPORT_ALLOWLIST:
        raise PermissionError(f"table {table!r} is not on the export allowlist")
    s, e = to_ms(p.get("start_date")), to_ms(p.get("end_date"), end=True)
    emails = _emails(users)
    kept, excl = [], 0
    for r in rows:
        if not in_window(r.get("created_at") or (r.get("Time_of_Payment") if key == PAYMENTS else 0), s, e):
            continue
        uid = {USERS: r.get("id"), PAYMENTS: r.get("Client_Reference_ID")}.get(key, r.get("user_id"))
        email = r.get("email") if key == USERS else (r.get("user_email") or emails.get(_uid(uid)))
        if not p.get("include_internal") and is_internal(uid, email):
            excl += 1
            continue
        kept.append(r)
    all_cols = sorted({c for r in kept for c in r})
    want = [c for c in (p.get("columns") or []) if c in all_cols] or all_cols
    cols = safe_columns(want)
    trunc = len(kept) > max_rows()
    summary = (f"Export of {EXPORT_ALLOWLIST[key]} (table {key}), {window_label(p)}: {len(kept)} rows, "
               f"{len(cols)} columns" + (f" (CSV capped at {max_rows():,} rows)" if trunc else "")
               + f". Sensitive columns (passwords, tokens, secrets, oauth, hashes, magic links) are never exported. "
               f"{_excl_note(excl, p.get('include_internal'))}.")
    return Report("export", summary, kept, cols, excl, trunc)


def _table_key(table):
    if isinstance(table, int) or str(table).strip().isdigit():
        return int(table)
    t = str(table or "").strip().lower()
    for k, v in EXPORT_ALLOWLIST.items():
        if t in (str(k), v):
            return k
    return t


# --- fetch + run ----------------------------------------------------------------------------------------------
def run(name: str, params: dict, xano, now_ms: int) -> Report:
    """Fetch rows read-only and build the report. Raises PermissionError for non-allowlisted exports."""
    p = dict(params or {})
    trunc = False

    def rows(t):
        nonlocal trunc
        r, tr = xano.rows(t)
        trunc = trunc or tr
        return r

    if name == "payments":
        rep = payments(rows(PAYMENTS), rows(USERS), p)
    elif name == "signups_and_pay_rate":
        rep = signups_and_pay_rate(rows(USERS), rows(PAYMENTS), p, now_ms)
    elif name == "planner_usage":
        rep = planner_usage(rows(USAGE), rows(USERS), p)
    elif name == "venue_requests":
        tid = xano.find_table(VENUE_LOG)
        rep = venue_requests(rows(tid) if tid else None, p)
    elif name == "save_offer":
        rep = save_offer(rows(USERS), rows(PAYMENTS), p)
    elif name == "nps":
        rep = nps(rows(NPS), p)
    elif name == "export":
        key = _table_key(p.get("table"))
        if key not in EXPORT_ALLOWLIST:
            raise PermissionError(f"table {p.get('table')!r} is not on the export allowlist")
        tid = xano.find_table(VENUE_LOG) if key == VENUE_LOG else key
        if tid is None:
            return Report("export", "The `venue_lookup_log` table isn't available in Xano yet.")
        rep = export(key, rows(tid), rows(USERS) if key not in (USERS,) else [], p)
    else:
        raise ValueError(f"unknown report {name!r}")
    if trunc:
        rep.truncated = True
        rep.summary += " WARNING: a table hit the page cap while reading, so totals may be incomplete."
    if len(rep.rows) > max_rows() and not rep.truncated:
        rep.truncated = True
        rep.summary += f" CSV capped at {max_rows():,} rows."
    return rep
