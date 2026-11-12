"""Experiments block for the daily tulle-ops brief (owner 2026-11-11: "report on the experiments we're running ...
send all of this in the daily tulle-ops update").

Four lines, all from Xano (the cron has no Mixpanel query secret), internal accounts excluded:

  1. Free Planner question a day (EXP-2026-11-free-daily-question). Since 2026-11-09 01:11 UTC (rec e29f61a) free
     couples get 3 questions for life, then 1 per UTC day that never accumulates. The bet: the daily allowance brings
     them BACK. Read = share of NEW free Planner couples who come back on a later day within 3 days, couples whose
     first day is after the change vs couples who started Oct 31 - Nov 5 (the open-planner daily-allowance period,
     whose 3-day follow-up ends before the change). Plus yesterday's daily-question use and limit hits.
  2. "What stopped you" save offer (EXP-2026-11-save-offer-60-forever). Since 2026-11-11 05:14 UTC, "too expensive"
     -> 60% off Forever for 12 h + email (ep313 v3, codes TT60-). Offers, buyers, revenue. Old 20% offer: 1 of 33.
  3. NPS + Trustpilot (table 77): ratings and average stars, 7 days vs the 7 before, low scores, and the Trustpilot
     prompt (5-star raters: offered / clicked / completed). The TrustBox widget's own clicks happen inside
     Trustpilot's iframe and are NOT recorded here.
  4. PDF opens + questions: share of new members who have opened a pricing PDF (table 1 total_pdf_views, cohorts
     by sign-up week) and Planner questions per couple (table 79), last 7 days vs the 7 before.

Plus 5. Ask Tulle bubble and 6. Gates -> paid (new members paid within 1 day; PDF limit and Planner limit
hitters; owner 2026-11-12). All are pre/post reads with other launches in the same windows - evidence, not proof. block() never raises.
"""
import datetime as dt
import os

import requests

META = os.environ.get("XANO_META_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta") + "/workspace/1"
DAY = 86400000
USER_TABLE, NPS_TABLE = 1, 77

FREE_DAILY_START_MS = 1794186665000      # 2026-11-09 01:11:05 UTC, rec e29f61a (3 for life, then 1/day)
PRE_COHORT_DAYS = ("2026-10-31", "2026-11-05")   # first-use days of the comparison cohort (follow-up ends 11-08)
FOLLOW_DAYS = 3
SCAN_FROM_DAY = "2026-10-24"             # a couple seen before their "first" day in this window is not new

OFFER_START_MS = 1794377684000           # 2026-11-11 05:14:44 UTC, ep313 v3 live
OFFER_PREFIX = "TT60-"
OFFER_HOURS = 12
OLD_OFFER_NOTE = "old 20% offer: 1 of 33 bought"

INTERNAL_IDS = {14998, 30614, 32636, 31619, 118, 5622, 34249, 33167, 185, 35757}   # 35757 = +offer60test


def internal(uid=None, email=""):
    e = str(email or "").lower()
    return (int(uid or 0) in INTERNAL_IDS) or e.startswith("vivek.marthi") or e.endswith("@tulletogether.com")


def _day(ms):
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def _pct(a, b):
    return ("%d%%" % round(100.0 * a / b)) if b else "-"


# ---------------------------------------------------------------- 1. free daily question (table 79)
def free_daily(rows, now_ms):
    """rows = table-79 rows from SCAN_FROM_DAY to today (any order). Returns a dict of counts."""
    by_user = {}
    free = set()
    for r in rows:
        uid = int(r.get("user_id") or 0)
        if not uid or uid in INTERNAL_IDS:
            continue
        day = r.get("usage_day") or _day(r.get("created_at") or 0)
        if r.get("counted_as_free") or r.get("status") == "blocked_free_limit":
            free.add(uid)
        if r.get("status") in ("ok", "free", "paid", "blocked_free_limit"):
            by_user.setdefault(uid, set()).add(day)

    def follow(cohort_uids, first_of):
        n = back = 0
        for u in cohort_uids:
            d0 = dt.datetime.strptime(first_of[u], "%Y-%m-%d")
            later = {(d0 + dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, FOLLOW_DAYS + 1)}
            n += 1
            back += 1 if by_user[u] & later else 0
        return n, back

    first = {u: min(ds) for u, ds in by_user.items() if u in free}
    first = {u: d for u, d in first.items() if d > SCAN_FROM_DAY}          # seen on the scan's first day = not new
    pre = [u for u, d in first.items() if PRE_COHORT_DAYS[0] <= d <= PRE_COHORT_DAYS[1]]
    start_day = _day(FREE_DAILY_START_MS + DAY)                              # first full UTC day of the new rule
    last_full = _day(now_ms - (FOLLOW_DAYS + 1) * DAY)                       # cohorts with all 3 follow-up days done
    post = [u for u, d in first.items() if start_day <= d <= last_full]
    pre_n, pre_back = follow(pre, first)
    post_n, post_back = follow(post, first)

    yday = _day(now_ms - DAY)
    y_rows = [r for r in rows if (r.get("usage_day") or "") == yday and int(r.get("user_id") or 0) not in INTERNAL_IDS]
    y_free_q = {int(r["user_id"]) for r in y_rows if r.get("kind") == "refine" and r.get("counted_as_free")
                and r.get("status") == "ok"}
    y_blocked = {int(r["user_id"]) for r in y_rows if r.get("status") == "blocked_free_limit"}
    y_returning = {u for u in y_free_q if first.get(u, yday) < yday}
    return {"pre_n": pre_n, "pre_back": pre_back, "post_n": post_n, "post_back": post_back,
            "post_pending": len([u for u, d in first.items() if d > last_full and d >= start_day]),
            "y_free_q": len(y_free_q), "y_returning": len(y_returning), "y_blocked": len(y_blocked)}


def free_daily_line(s):
    if s["post_n"]:
        read = "came back within %d days: %s of %d new free couples since the change vs %s of %d before" % (
            FOLLOW_DAYS, _pct(s["post_back"], s["post_n"]), s["post_n"], _pct(s["pre_back"], s["pre_n"]), s["pre_n"])
    else:
        read = ("first cohort still inside its %d-day window (%d couples) - before: %s of %d came back" % (
            FOLLOW_DAYS, s["post_pending"], _pct(s["pre_back"], s["pre_n"]), s["pre_n"]))
    return ("• *1 free Planner question a day* (since 11-09) - %s · yesterday: %d free couples used a free question "
            "(%d of them returning), %d hit the daily limit" % (read, s["y_free_q"], s["y_returning"], s["y_blocked"]))


# ---------------------------------------------------------------- 2. save offer (table 1 + payment log)
def offers(users, payments, now_ms):
    offered = []
    for u in users:
        code = str(u.get("paywall_offer_code") or "")
        if not code.startswith(OFFER_PREFIX) or internal(u.get("id"), u.get("email")):
            continue
        exp = int(u.get("paywall_offer_expires_at") or 0)
        offered.append({"id": int(u["id"]), "made": exp - OFFER_HOURS * 3600000, "exp": exp})
    made = {o["id"]: o["made"] for o in offered}
    bought, rev = set(), 0.0
    for p in payments:
        try:
            uid = int(str(p.get("Client_Reference_ID") or "").strip() or 0)
        except ValueError:
            continue
        if uid in made and (p.get("Time_of_Payment") or 0) >= made[uid] and uid not in bought:
            bought.add(uid)
            rev += float(p.get("Amount") or 0)
    return {"n": len(offered), "n24": sum(1 for o in offered if o["made"] >= now_ms - DAY),
            "live": sum(1 for o in offered if o["exp"] > now_ms), "bought": len(bought), "rev": rev}


def offers_line(o, source_note=""):
    return ("• *\"Too expensive\" -> 60%% off Forever, 12 h + email* (since 11-11) - %d offers (%d in 24h, %d still live)"
            " · %d bought (%s) · $%.0f · %s%s" % (o["n"], o["n24"], o["live"], o["bought"], _pct(o["bought"], o["n"]),
                                                o["rev"], OLD_OFFER_NOTE, source_note))


# ---------------------------------------------------------------- 3. NPS + Trustpilot (table 77)
def nps(rows, now_ms):
    real = [r for r in rows if not internal(r.get("user_id"), r.get("user_email")) and (r.get("score") or 0) > 0]
    wk = [r for r in real if (r.get("created_at") or 0) >= now_ms - 7 * DAY]
    prev = [r for r in real if now_ms - 14 * DAY <= (r.get("created_at") or 0) < now_ms - 7 * DAY]
    avg = lambda rs: (sum(r["score"] for r in rs) / len(rs)) if rs else None
    fives = [r for r in wk if r.get("score") == 5]
    st = lambda s: sum(1 for r in wk if (r.get("review_prompt_state") or "") == s)
    ever = lambda s: sum(1 for r in real if (r.get("review_prompt_state") or "") == s)
    return {"n": len(wk), "avg": avg(wk), "prev_n": len(prev), "prev_avg": avg(prev),
            "low": sum(1 for r in wk if r["score"] <= 3), "fives": len(fives),
            "asked": sum(1 for r in wk if (r.get("review_prompt_state") or "") not in ("", "not_offered")),
            "clicked": st("clicked") + st("completed"), "clicked_ever": ever("clicked") + ever("completed"),
            "asked_ever": sum(1 for r in real if (r.get("review_prompt_state") or "") not in ("", "not_offered"))}


def nps_line(n):
    f = lambda v: ("%.1f" % v) if v is not None else "-"
    return ("• *NPS + Trustpilot* - %d ratings in 7 days, avg %s/5 (prior 7 days: %d, %s/5) · %d scored 3 or less · "
            "Trustpilot ask shown to %d, clicked %d (all time: %d of %d)" % (
                n["n"], f(n["avg"]), n["prev_n"], f(n["prev_avg"]), n["low"], n["asked"], n["clicked"],
                n["clicked_ever"], n["asked_ever"]))


# ---------------------------------------------------------------- 4. PDF opens + questions
def engagement(users, usage_rows, now_ms):
    def cohort(lo, hi):
        c = [u for u in users if lo <= (u.get("created_at") or 0) < hi and not internal(u.get("id"), u.get("email"))]
        opened = [u for u in c if (u.get("total_pdf_views") or 0) > 0]
        return len(c), len(opened), sum(int(u.get("total_pdf_views") or 0) for u in c)
    # Cohorts end 1 day ago so every member has had at least a day.
    a = cohort(now_ms - 8 * DAY, now_ms - DAY)
    b = cohort(now_ms - 15 * DAY, now_ms - 8 * DAY)

    def qs(lo, hi):
        rr = [r for r in usage_rows if lo <= (r.get("created_at") or 0) < hi and int(r.get("user_id") or 0) not in INTERNAL_IDS
              and r.get("status") == "ok"]
        couples = {int(r["user_id"]) for r in rr}
        q = sum(1 for r in rr if r.get("kind") == "refine")
        return len(couples), q
    qa = qs(now_ms - 7 * DAY, now_ms)
    qb = qs(now_ms - 14 * DAY, now_ms - 7 * DAY)
    return {"new": a[0], "opened": a[1], "views": a[2], "p_new": b[0], "p_opened": b[1], "p_views": b[2],
            "couples": qa[0], "q": qa[1], "p_couples": qb[0], "p_q": qb[1]}


def engagement_line(e):
    per = lambda a, b: ("%.1f" % (a / b)) if b else "-"
    return ("• *PDF opens + questions* - new members who opened a PDF: %s of %d signed up last week (%s PDFs each) vs %s "
            "of %d the week before (%s each) · Planner: %s questions per couple from %d couples vs %s from %d the week "
            "before" % (_pct(e["opened"], e["new"]), e["new"], per(e["views"], e["new"]), _pct(e["p_opened"], e["p_new"]),
                        e["p_new"], per(e["p_views"], e["p_new"]), per(e["q"], e["couples"]), e["couples"],
                        per(e["p_q"], e["p_couples"]), e["p_couples"]))


# ---------------------------------------------------------------- 6. Gates -> paid (owner 2026-11-12: "#1 metric")
# Owner: "check the rate of users hitting gates ... and then seeing the payment rates from that to make sure that's
# increasing". Xano-only proxies (the cron has no Mixpanel secret; the full weekly view is the Mixpanel board
# GATES_BOARD). Cohort = new accounts created 8..1 days ago vs 15..8 days ago. Paid = a payment-log row for the
# account within 1 day of sign-up (71% of 60-day buyers buy by then, so cohorts are comparable after 1 day).
# Gates visible in Xano: PDF limit = used all free views (total_pdf_views >= 3); Planner limit = any non-ok
# rec_usage row (paywall/limit hit). The PI / filters gates live only in Mixpanel.
GATES_BOARD = "https://mixpanel.com/project/3963130/app/boards#id=11645547"
PDF_FREE_VIEWS = 3


def gates(users, usage_rows, payments, now_ms):
    paid_at = {}
    for p in payments:
        try:
            uid = int(str(p.get("Client_Reference_ID") or "").strip() or 0)
        except ValueError:
            continue
        t = p.get("Time_of_Payment") or 0
        if uid and t and (uid not in paid_at or t < paid_at[uid]):
            paid_at[uid] = t
    limited = {int(r.get("user_id") or 0) for r in usage_rows if r.get("status") not in (None, "", "ok")}

    def cohort(lo, hi):
        c = [u for u in users if lo <= (u.get("created_at") or 0) < hi and not internal(u.get("id"), u.get("email"))]
        paid = lambda u: int(u["id"]) in paid_at and paid_at[int(u["id"])] - (u.get("created_at") or 0) <= DAY
        pdf = [u for u in c if int(u.get("total_pdf_views") or 0) >= PDF_FREE_VIEWS]
        chat = [u for u in c if int(u["id"]) in limited]
        return {"n": len(c), "paid": sum(1 for u in c if paid(u)),
                "pdf": len(pdf), "pdf_paid": sum(1 for u in pdf if paid(u)),
                "chat": len(chat), "chat_paid": sum(1 for u in chat if paid(u))}
    return {"a": cohort(now_ms - 8 * DAY, now_ms - DAY), "b": cohort(now_ms - 15 * DAY, now_ms - 8 * DAY)}


def gates_line(g):
    a, b = g["a"], g["b"]
    return ("• *Gates -> paid* (new members, paid within 1 day) - %s of %d signed up last week vs %s of %d the week "
            "before · hit the PDF limit: %s of members (%d), %s of them paid vs %s · hit the Planner limit: %d, %d paid "
            "vs %d, %d paid · PI/filters gates: <%s|Mixpanel board>" % (
                _pct(a["paid"], a["n"]), a["n"], _pct(b["paid"], b["n"]), b["n"],
                _pct(a["pdf"], a["n"]), a["pdf"], _pct(a["pdf_paid"], a["pdf"]), _pct(b["pdf_paid"], b["pdf"]),
                a["chat"], a["chat_paid"], b["chat"], b["chat_paid"], GATES_BOARD))


# ---------------------------------------------------------------- 5. Ask Tulle bubble (table 79 'surface', 2026-11-11)
# The planner-chat component tags every rec-engine call ?surface=planner | bubble:<path>; rec-engine writes it on each
# usage row. Adoption = couples with a bubble row; 'more messages?' = questions per day (all surfaces) since the first
# bubble row vs the 7 days before it. Pre/post, other launches in the same week.
def bubble(rows, now_ms):
    real = [r for r in rows if int(r.get("user_id") or 0) not in INTERNAL_IDS]
    bub = [r for r in real if str(r.get("surface") or "").startswith("bubble")]
    if not bub:
        return None
    first_day = min(r.get("usage_day") or _day(r.get("created_at") or 0) for r in bub)
    yday = _day(now_ms - DAY)
    yb = [r for r in bub if r.get("usage_day") == yday]
    q = lambda rr: sum(1 for r in rr if r.get("kind") == "refine" and r.get("status") == "ok")
    yp = [r for r in real if r.get("usage_day") == yday and str(r.get("surface") or "") == "planner"]
    fd = dt.datetime.strptime(first_day, "%Y-%m-%d")
    pre_days = {(fd - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 8)}
    today = _day(now_ms)
    post_days = sorted({r.get("usage_day") for r in real if first_day <= (r.get("usage_day") or "") < today})
    pre_q = q([r for r in real if r.get("usage_day") in pre_days]) / 7.0
    post_q = (q([r for r in real if r.get("usage_day") in set(post_days)]) / len(post_days)) if post_days else None
    pages = {}
    for r in bub:
        pg = str(r.get("surface")).split(":", 1)[1] if ":" in str(r.get("surface")) else "?"
        pages[pg] = pages.get(pg, 0) + 1
    return {"first": first_day, "y_couples": len({r.get("user_id") for r in yb}), "y_q_bubble": q(yb), "y_q_planner": q(yp),
            "all_couples": len({r.get("user_id") for r in bub}), "pre_q": pre_q, "post_q": post_q, "post_days": len(post_days),
            "top_page": max(pages.items(), key=lambda kv: kv[1])[0] if pages else ""}


def bubble_line(b):
    if not b:
        return "• *Ask Tulle bubble* - no requests tagged from the bubble yet (live after the component is activated + published)"
    trend = ("questions/day since launch %.0f vs %.0f the week before (%d day%s in)" % (b["post_q"], b["pre_q"], b["post_days"], "" if b["post_days"] == 1 else "s")
             if b["post_q"] is not None else "first full day not done yet")
    return ("• *Ask Tulle bubble* (since %s) - yesterday %d couples used it, %d questions from the bubble vs %d on the Planner page · "
            "%d couples so far, most from %s · %s" % (b["first"][5:], b["y_couples"], b["y_q_bubble"], b["y_q_planner"], b["all_couples"],
                                                       b["top_page"] or "-", trend))


# ---------------------------------------------------------------- calls to make (registry)
# Owner 2026-11-11: "experiment registry is getting stale ... suggest that we make a call on them when they go live."
# Reads experiments/experiment_log.json (deployed with this service - it is in the cron's watch patterns).
REGISTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments", "experiment_log.json")
NO_DATE_AFTER_DAYS = 14
MAX_CALLS = 6


def calls(registry, today):
    """registry = parsed experiment_log.json, today = 'YYYY-MM-DD'. Returns (lines, total)."""
    t = dt.datetime.strptime(today, "%Y-%m-%d")
    days = lambda d: (t - dt.datetime.strptime(d[:10], "%Y-%m-%d")).days
    out = []          # (priority, sort key, text)
    for e in registry.get("experiments", []):
        st, name = (e.get("status") or "").lower(), (e.get("name") or e.get("id") or "?")[:70]
        start, review, bar = e.get("start_date") or "", e.get("review_date") or "", (e.get("goal") or "").strip()
        bar_txt = ("bar: " + bar[:90]) if bar else "*no success bar set*"
        if st in ("live", "running"):
            if review and review[:10] <= today:
                out.append((0, -days(review), "Call overdue %dd - %s (%s)" % (days(review), name, bar_txt)))
            elif start and 0 <= days(start) <= 2:
                out.append((1, days(start), "Went live %s - %s: call on %s (%s)" % (
                    start[5:10], name, review[5:10] if review else "*set a date*", bar_txt)))
            elif not review and start and days(start) > NO_DATE_AFTER_DAYS:
                out.append((2, -days(start), "Live %dd with no call date - %s: keep, kill or set a date" % (days(start), name)))
        elif st == "planned" and start and start[:10] <= today:
            out.append((3, 0, "Marked planned but start date %s has passed - %s: is it live?" % (start[5:10], name)))
    out.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in out[:MAX_CALLS]], len(out)


def calls_lines(now_ms):
    import json
    with open(REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    lines, total = calls(reg, _day(now_ms))
    if not lines:
        return []
    head = "*Calls to make* (%d)" % total
    more = ["• _+%d more in the 🧪 Experiments tab_" % (total - len(lines))] if total > len(lines) else []
    return [head] + ["• " + l for l in lines] + more


# ---------------------------------------------------------------- fetchers + block
def _get(token, path, **params):
    r = requests.get(META + path, headers={"Authorization": "Bearer " + token}, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def recent_users(token, pages=10):
    """Last `pages` pages of table 1 (ascending ids, ~200 per page = the newest ~2,000 accounts, ~20 days)."""
    first = _get(token, "/table/%d/content" % USER_TABLE, page=1, per_page=200)
    last = int(first.get("pageTotal") or 1)
    out = []
    for page in range(max(1, last - pages + 1), last + 1):
        out += _get(token, "/table/%d/content" % USER_TABLE, page=page, per_page=200).get("items", [])
    return out


def offer_users(token, fallback):
    """Accounts holding a save-offer code. Tries the metadata search with a not-null filter and only trusts it if
    every row it returns really has a code (the metadata search is documented as equality-only); otherwise falls
    back to the newest accounts already loaded, and says so."""
    try:
        out, page = [], 1
        while page <= 10:
            r = requests.post(META + "/table/%d/content/search" % USER_TABLE, timeout=60,
                              headers={"Authorization": "Bearer " + token},
                              json={"search": {"paywall_offer_code|!=": None}, "page": page, "per_page": 500})
            r.raise_for_status()
            d = r.json()
            items = d.get("items", [])
            if any(not i.get("paywall_offer_code") for i in items):
                raise ValueError("filter ignored")
            out += items
            if not d.get("nextPage") or not items:
                break
            page += 1
        return out, ""
    except Exception:
        return fallback, " _(newest ~2,000 accounts only)_"


def block(token, now_ms=None):
    now_ms = now_ms or int(dt.datetime.utcnow().timestamp() * 1000)
    lines = ["*Experiments*"]
    usage, users = None, None
    try:
        import rec_watch
        start = dt.datetime.strptime(SCAN_FROM_DAY, "%Y-%m-%d")
        n_days = (dt.datetime.utcfromtimestamp(now_ms / 1000) - start).days + 1
        usage = [r for i in range(n_days) for r in rec_watch._rows_for_day(token, (start + dt.timedelta(days=i)).strftime("%Y-%m-%d"))]
        lines.append(free_daily_line(free_daily(usage, now_ms)))
        lines.append(bubble_line(bubble(usage, now_ms)))
    except Exception as e:
        lines.append(":warning: _free-question line failed: %s_" % str(e)[:120])
    try:
        users = recent_users(token)
    except Exception as e:
        lines.append(":warning: _could not read new accounts: %s_" % str(e)[:120])
    try:
        import bnpl_watch
        holders, note = offer_users(token, users or [])
        pays = bnpl_watch.fetch_payments(token, OFFER_START_MS)
        lines.append(offers_line(offers(holders, pays, now_ms), note))
    except Exception as e:
        lines.append(":warning: _save-offer line failed: %s_" % str(e)[:120])
    try:
        rows, page = [], 1
        while page <= 10:
            d = _get(token, "/table/%d/content" % NPS_TABLE, page=page, per_page=200)
            rows += d.get("items", [])
            if not d.get("nextPage"):
                break
            page += 1
        lines.append(nps_line(nps(rows, now_ms)))
    except Exception as e:
        lines.append(":warning: _NPS line failed: %s_" % str(e)[:120])
    try:
        if users is None or usage is None:
            raise RuntimeError("inputs unavailable")
        lines.append(engagement_line(engagement(users, usage, now_ms)))
    except Exception as e:
        lines.append(":warning: _PDF/questions line failed: %s_" % str(e)[:120])
    try:
        if users is None or usage is None:
            raise RuntimeError("inputs unavailable")
        import bnpl_watch
        gpays = bnpl_watch.fetch_payments(token, now_ms - 16 * DAY)
        lines.append(gates_line(gates(users, usage, gpays, now_ms)))
    except Exception as e:
        lines.append(":warning: _gates line failed: %s_" % str(e)[:120])
    lines.append("_Pre/post reads with other launches in the same windows - evidence, not proof._")
    try:
        c = calls_lines(now_ms)
        if c:
            lines += [""] + c
    except Exception as e:
        lines.append(":warning: _calls list failed: %s_" % str(e)[:120])
    return "\n".join(lines)
