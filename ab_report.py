"""A/B scoreboard for the daily brief (owner 2026-11-07: "use this a/b test as one of the only things it sends me").

Experiment EXP-2026-11-planner-my-wedding-plan: couples split by rec_service/plan.arm(user_id) into "plan" (the
"My wedding" plan) and "control". Since PLAN_AB_START (UTC day), per arm, internal accounts excluded:
  couples      users with an ok opening / question / lookup in the Planner (rec_usage, table 79)
  questions    ok refine rows per couple
  opened a PDF share of couples with a pdf row (free or paid view)
  bought       couples with a payment-log row (table 16, Client_Reference_ID) after their first Planner use
  plan adds    rows in table 90 (plan arm only), and couples with 2+ slots filled
One line per arm, a two-proportion z-test on "opened a PDF" and on "bought", and a plain read. Never raises from
block(): the brief must always go out."""
import datetime as dt
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "rec_service"))
import plan as _plan  # noqa: E402  (pure: hashlib + os only)

START = os.environ.get("PLAN_AB_START", "2026-11-08")
PLAN_TABLE = 90
MIN_DAYS = 14


def _z(c1, n1, c2, n2):
    """Two-sided p-value of a difference in two proportions (normal approximation); None if too small."""
    if min(n1, n2) < 20:
        return None
    p = (c1 + c2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 else 0
    if not se:
        return None
    z = (c1 / n1 - c2 / n2) / se
    return math.erfc(abs(z) / math.sqrt(2))


def compute(usage_rows, plan_rows, payments, internal=()):
    internal = set(internal)
    arms = {"control": {}, "plan": {}}
    first_use = {}
    for r in usage_rows:
        uid = int(r.get("user_id") or 0)
        if not uid or uid in internal or r.get("status") not in ("ok", "free", "paid"):
            continue
        kind = r.get("kind") or ""
        a = arms[_plan.arm(uid)].setdefault(uid, {"q": 0, "pdf": 0, "used": False})
        if kind in ("opening", "refine") and r.get("status") == "ok":
            a["used"] = True
            if kind == "refine":
                a["q"] += 1
            t = r.get("created_at") or 0
            first_use[uid] = min(first_use.get(uid, t), t) if t else first_use.get(uid, 0)
        elif kind == "pdf" and r.get("status") in ("free", "paid"):
            a["pdf"] += 1
    buyers = set()
    for p in payments:
        try:
            uid = int(str(p.get("Client_Reference_ID") or "").strip() or 0)
        except ValueError:
            continue
        t = p.get("Time_of_Payment") or 0
        if uid in first_use and t >= first_use[uid]:
            buyers.add(uid)
    adds = [r for r in plan_rows if int(r.get("user_id") or 0) not in internal]
    slots_by = {}
    for r in adds:
        if (r.get("status") or "active") == "active":
            slots_by.setdefault(int(r["user_id"]), set()).add(r.get("slot"))
    out = {}
    for name, users in arms.items():
        used = {u: a for u, a in users.items() if a["used"]}
        n = len(used)
        out[name] = {"n": n, "q": sum(a["q"] for a in used.values()),
                     "pdf_couples": sum(1 for a in used.values() if a["pdf"]),
                     "bought": sum(1 for u in used if u in buyers)}
    plan_users = {u for u in arms["plan"] if arms["plan"][u]["used"]}
    out["plan"]["adds"] = sum(1 for r in adds if int(r["user_id"]) in plan_users)
    out["plan"]["adders"] = len({int(r["user_id"]) for r in adds if int(r["user_id"]) in plan_users})
    out["plan"]["two_slots"] = sum(1 for u, s in slots_by.items() if u in plan_users and len(s) >= 2)
    return out


def format_block(s, day_n):
    c, p = s["control"], s["plan"]
    pct = lambda a, b: ("%d%%" % round(100 * a / b)) if b else "-"
    per = lambda a, b: ("%.1f" % (a / b)) if b else "-"
    lines = ["*A/B: My wedding plan* - day %d of %d" % (day_n, MIN_DAYS),
             "• Control: %d couples · %s questions each · %s opened a PDF · %d bought" % (
                 c["n"], per(c["q"], c["n"]), pct(c["pdf_couples"], c["n"]), c["bought"]),
             "• Plan: %d couples · %s questions each · %s opened a PDF · %d bought · %d added to their plan (%d couples, %d with 2+ slots)" % (
                 p["n"], per(p["q"], p["n"]), pct(p["pdf_couples"], p["n"]), p["bought"], p.get("adds", 0),
                 p.get("adders", 0), p.get("two_slots", 0))]
    pp = _z(p["pdf_couples"], p["n"], c["pdf_couples"], c["n"])
    pb = _z(p["bought"], p["n"], c["bought"], c["n"])
    if day_n < MIN_DAYS:
        read = "Too early to call - read on day %d." % MIN_DAYS
    elif pp is not None and pp < 0.05:
        read = "PDF opens differ (p=%.2f): %s." % (pp, "plan is ahead" if p["pdf_couples"] / max(p["n"], 1) > c["pdf_couples"] / max(c["n"], 1) else "plan is BEHIND - consider stopping")
    else:
        read = "No clear difference yet on PDF opens%s." % ((" (p=%.2f)" % pp) if pp is not None else "")
    if pb is not None and pb < 0.05:
        read += " Purchases differ (p=%.2f)." % pb
    lines.append("• Read: " + read)
    return "\n".join(lines)


def block(token, now_ms=None, fetch_day=None, fetch_table=None, fetch_payments=None, internal=None):
    try:
        now = dt.datetime.utcfromtimestamp((now_ms or dt.datetime.utcnow().timestamp() * 1000) / 1000)
        start = dt.datetime.strptime(START, "%Y-%m-%d")
        if now < start:
            return "*A/B: My wedding plan* - starts %s (not live yet)." % START
        import rec_watch
        fetch_day = fetch_day or rec_watch._rows_for_day
        fetch_table = fetch_table or rec_watch._table_rows
        if fetch_payments is None:
            import bnpl_watch
            fetch_payments = bnpl_watch.fetch_payments
        internal = rec_watch.INTERNAL_USER_IDS | {31619} if internal is None else internal
        days = [(start + dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range((now - start).days + 1)]
        usage = [r for d in days for r in fetch_day(token, d)]
        since_ms = int(start.timestamp() * 1000)
        plan_rows = fetch_table(token, PLAN_TABLE, since_ms)
        pays = fetch_payments(token, since_ms)
        return format_block(compute(usage, plan_rows, pays, internal), len(days))
    except Exception as e:
        return ":warning: _A/B scoreboard failed: %s_" % (str(e)[:160])
