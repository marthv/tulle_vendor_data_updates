"""Daily brief (Oct 2026) - wraps the 13:32 UTC Tulle Ops health DM in a TL;DR + Decisions list.

    python daily_brief.py --preview     # print today's brief from live data, post nothing

User ask 2026-10-04: the DM had grown long (the pricing-test section alone was ~40 lines). Shape:

    *Tulle daily - <date>*
    TL;DR            4 bullets: money, signups, endpoint health, assistant + NPS
    Decisions        only things that need a human today (from every section's alerts), else "none"
    Endpoint health  UNCHANGED (perf_digest.format_health_short) - the user asked to keep it
    Pricing test     2 lines (bnpl_watch short=True); full tables stay in the logs + dashboard
    Tulle Assistant  1 line (rec_watch) + the Railway search block (rec_watch.railway_section, 2026-10-26)
    NPS              1 line - goal is >= 1 rating/day (user, 2026-10-04)

Every section is isolated: one failing never costs the others or the endpoint report.
Numbers come from Xano (payment log 16, users 1, NPS 77, rec_usage 79), not Mixpanel - the Railway
service has no Mixpanel query secret. Trends over weeks live on the Mixpanel boards.
"""
import datetime as dt
import os
import statistics as st
import sys

import requests

META = os.environ.get("XANO_META_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta") + "/workspace/1"
DAY = 86400000
NPS_TABLE = 77
USER_TABLE = 1
NPS_GOAL_PER_DAY = 1


def _get(token, path, **params):
    r = requests.get(META + path, headers={"Authorization": "Bearer " + token}, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _all_rows(token, table, per_page=200, max_pages=30):
    out, page = [], 1
    while page <= max_pages:
        d = _get(token, f"/table/{table}/content", page=page, per_page=per_page)
        out += d.get("items", [])
        if not d.get("nextPage"):
            break
        page += 1
    return out


def signups(token, now_ms):
    """New accounts in the last 24h and the 7-day daily average before that. Table 1 comes back in
    ascending id order (checked 2026-10-04), ~100 sign-ups/day, so the last 6 pages of 200 cover
    8+ days. Reads ids/created_at only."""
    first = _get(token, f"/table/{USER_TABLE}/content", page=1, per_page=200)
    last = int(first.get("pageTotal") or 1)
    ts = []
    for page in range(max(1, last - 5), last + 1):
        d = _get(token, f"/table/{USER_TABLE}/content", page=page, per_page=200)
        ts += [i.get("created_at") or 0 for i in d.get("items", [])]
    if ts and min(ts) > now_ms - 8 * DAY:
        raise RuntimeError("fewer than 8 days of sign-ups in the last pages")
    d24 = sum(1 for t in ts if t >= now_ms - DAY)
    prev7 = sum(1 for t in ts if now_ms - 8 * DAY <= t < now_ms - DAY) / 7
    return d24, prev7


def payments(token, now_ms):
    import bnpl_watch
    rows = bnpl_watch.fetch_payments(token, now_ms - 8 * DAY)
    last = [r for r in rows if (r.get("Time_of_Payment") or 0) >= now_ms - DAY]
    prev = [r for r in rows if now_ms - 8 * DAY <= (r.get("Time_of_Payment") or 0) < now_ms - DAY]
    amt = lambda rs: sum(float(r.get("Amount") or 0) for r in rs)
    forever = sum(1 for r in last if (r.get("Type") or "").strip().lower() in ("forever weeks", "forever"))
    return {"rev24": amt(last), "n24": len(last), "forever24": forever, "rev7": amt(prev) / 7, "n7": len(prev) / 7}


def nps(token, now_ms):
    rows = _all_rows(token, NPS_TABLE)
    last = [r for r in rows if (r.get("created_at") or 0) >= now_ms - DAY and r.get("score")]
    wk = [r for r in rows if (r.get("created_at") or 0) >= now_ms - 7 * DAY and r.get("score")]
    comments = [r for r in last if (r.get("comment") or "").strip()]
    avg = st.mean(r["score"] for r in wk) if wk else None
    return {"n24": len(last), "n7": len(wk), "avg7": avg, "comments24": len(comments), "total": len(rows)}


# 2026-11-09 PDF GATE (owner: "seems like there was certainly a leak -- report on that in the tulle-ops update daily").
# Incident: from 2026-10-28 ep21 (user/update_record) treated free_views_remaining = 0 as "not sent" (0 == null is TRUE in
# XanoScript), so the website's countdown to 0 never landed and free couples kept opening PDFs at "1 left". Fixed
# 2026-11-09 04:34 UTC. This section shows the incident note for a week, then keeps watching for the same failure.
PDF_INCIDENT_UNTIL_MS = 1794787200000          # 2026-11-16 00:00 UTC
FREE_VIEWS_AT_SIGNUP = 3
PDF_FIX_MS = 1794198841000                     # 2026-11-09 04:34:01 UTC, ep21 fix live
INTERNAL_IDS = {14998, 30614, 32636, 31619, 118, 5622}


def _internal(u):
    e = str(u.get("email") or "").lower()
    return u.get("id") in INTERNAL_IDS or e.startswith("vivek.marthi") or e.endswith("@tulletogether.com")


def _has_access(u, today):
    if u.get("forever_access_purchased"):
        return True
    d = str(u.get("date_until_access") or "")[:10]
    return bool(d) and d >= today


def pdf_gate(token, now_ms):
    """Free couples (no paid/submission access, no bonus views) signed up in the last ~2 weeks, active in the last
    24h, who have opened more PDFs than they were ever given yet still show free views left = the leak signature.
    Also: Planner PDF opens counted 'free' for one couple in 24h beyond the signup allowance (table 79).
    Scans the last 10 pages of table 1 (ascending ids, ~2,000 newest accounts) - older accounts are not checked."""
    today = dt.datetime.utcfromtimestamp(now_ms / 1000).strftime("%Y-%m-%d")
    first = _get(token, f"/table/{USER_TABLE}/content", page=1, per_page=200)
    last = int(first.get("pageTotal") or 1)
    users = []
    for page in range(max(1, last - 9), last + 1):
        users += _get(token, f"/table/{USER_TABLE}/content", page=page, per_page=200).get("items", [])
    free = [u for u in users if not _internal(u) and not _has_access(u, today)
            and not (u.get("total_pdf_submissions") or 0) and not u.get("bonus_views_at")]
    since = max(now_ms - DAY, PDF_FIX_MS)   # couples who leaked BEFORE the fix keep '1 left' until their next open
    active = [u for u in free if max(u.get("last_active_at") or 0, u.get("last_viewed_at") or 0) >= since]
    stuck = [u for u in active if (u.get("total_pdf_views") or 0) > FREE_VIEWS_AT_SIGNUP
             and (u.get("FreeViewsRemaining") or 0) > 0]
    planner_over = 0
    try:
        import rec_watch
        days = {dt.datetime.utcfromtimestamp((now_ms - i * DAY) / 1000).strftime("%Y-%m-%d") for i in (0, 1)}
        rows = [r for d in days for r in rec_watch._rows_for_day(token, d)
                if r.get("kind") == "pdf" and r.get("status") == "free" and (r.get("created_at") or 0) >= now_ms - DAY]
        per = {}
        for r in rows:
            per[r.get("user_id")] = per.get(r.get("user_id"), 0) + 1
        planner_over = sum(1 for n in per.values() if n > FREE_VIEWS_AT_SIGNUP)
    except Exception:
        planner_over = None
    return {"checked": len(active), "stuck": stuck, "planner_over": planner_over}


# 2026-11-09 owner: watch purchases recover after the PDF-gate fix. Baseline = the payment log, Oct 14-27 (before the leak):
# 5.5 purchases/day, $394/day. During the leak (Oct 28 - Nov 8): 3.5/day, $306/day. Other changes shipped in the same window
# (Planner rollout, pricing tests, tours), so a recovery is evidence, not proof. Shown until 2026-11-23.
RECOVERY_BASE_N, RECOVERY_BASE_REV = 5.5, 394.0
RECOVERY_LEAK_N, RECOVERY_LEAK_REV = 3.5, 306.0
RECOVERY_UNTIL_MS = 1795392000000            # 2026-11-23 00:00 UTC


def recovery_section(token, now_ms):
    """(line, alerts) - last 7 days of purchases vs the pre-leak baseline."""
    import bnpl_watch
    since = now_ms - 7 * DAY
    rows = [r for r in bnpl_watch.fetch_payments(token, since) if (r.get("Time_of_Payment") or 0) >= since]
    n7 = len(rows) / 7
    rev7 = sum(float(r.get("Amount") or 0) for r in rows) / 7
    # Days of the window that fall after the fix (the first week mixes leak days in).
    after = max(0.0, min(7.0, (now_ms - PDF_FIX_MS) / DAY))
    pct = lambda x, base: f"{(x / base - 1) * 100:+.0f}%"
    if after < 7:
        light = ":white_circle:"          # window still includes leak days - no verdict yet
    elif n7 >= RECOVERY_BASE_N * 0.9:
        light = ":large_green_circle:"
    elif n7 > RECOVERY_LEAK_N * 1.1:
        light = ":large_yellow_circle:"
    else:
        light = ":red_circle:"
    line = (f"{light} *Purchases since the PDF fix* - last 7 days {n7:.1f}/day, ${rev7:,.0f}/day "
            f"vs {RECOVERY_BASE_N}/day, ${RECOVERY_BASE_REV:,.0f}/day before the leak "
            f"({pct(n7, RECOVERY_BASE_N)} purchases, {pct(rev7, RECOVERY_BASE_REV)} revenue) "
            f"· leak period was {RECOVERY_LEAK_N}/day, ${RECOVERY_LEAK_REV:,.0f}/day")
    if after < 7:
        line += f" · _window is {after:.1f} of 7 days post-fix_"
    alerts = []
    if after >= 7 and n7 <= RECOVERY_LEAK_N * 1.1:
        alerts.append(f"Purchases have NOT recovered a full week after the PDF fix ({n7:.1f}/day vs {RECOVERY_BASE_N} before the "
                      "leak) - something else in the Oct 28 window is also hurting conversion; worth a look.")
    return line, alerts


def pdf_gate_section(token, now_ms):
    """(line, alerts)"""
    g = pdf_gate(token, now_ms)
    n = len(g["stuck"])
    # Planner opens are counted exactly (table 79) -> red. The website check is a signature (no per-open log), and a
    # couple who leaked before the fix still shows '1 left' until their next open -> amber.
    bad = n > 0 or (g["planner_over"] or 0) > 0
    light = ":red_circle:" if (g["planner_over"] or 0) > 0 else (":large_yellow_circle:" if n else ":large_green_circle:")
    po = "n/a" if g["planner_over"] is None else g["planner_over"]
    line = (f"{light} *PDF gate* - website: {n} free couple(s) active since the fix past {FREE_VIEWS_AT_SIGNUP} PDFs"
            f" with views still left (should trend to 0; {g['checked']} checked) · Planner: {po} over-limit")
    if n:
        line += " · e.g. ids " + ", ".join(str(u.get("id")) for u in g["stuck"][:5])
    if now_ms < PDF_INCIDENT_UNTIL_MS:
        line += ("\n    _Incident 10-28 -> 11-09: the website's free-view countdown to 0 was ignored (ep21: 0 == null in "
                 "XanoScript), so free couples kept opening PDFs at '1 left' (84 ignored writes / 19 couples in 5 h on "
                 "11-09; one opened 12). Fixed 11-09 04:34 UTC; zero-writes confirmed sticking._")
    alerts = []
    if (g["planner_over"] or 0) > 0 or n >= 5:
        alerts.append("PDF gate: free couples are opening PDFs past their limit again - check ep21 (free_views_remaining "
                      "writes) and the Planner /rec/pdf gate.")
    return line, alerts


def rec_line(token):
    """One line from rec_watch + alerts. rec_watch.build returns 2 lines; keep the first."""
    import rec_watch
    text = rec_watch.build(token)
    first = text.split("\n")[0]
    alerts = []
    if ":red_circle:" in first:
        alerts.append("Tulle Assistant: errors or the spend cap/kill switch blocked requests in the last 24h - check Railway logs.")
    return first.replace("*Recommendation engine*", "*Tulle Assistant*"), alerts


def _fmt_delta(now, avg):
    if not avg:
        return ""
    d = 100 * (now - avg) / avg
    return f" ({'+' if d >= 0 else ''}{d:.0f}% vs 7-day avg)"


def _trim_health(health_text):
    """Owner 2026-10-31: the brief got long. Keep the header, every non-green line and the Details link; when every
    light is green the whole section collapses to one line. The full breakdown stays in the dashboard."""
    lines = [l for l in (health_text or "").split("\n") if l.strip()]
    if not lines:
        return health_text
    head, body = lines[0], lines[1:]
    details = [l for l in body if l.startswith("_Details")]
    keep = [l for l in body if not l.startswith("_Details") and not l.startswith(":large_green_circle:")]
    if not keep:
        return head + " - all green" + (" · " + details[0].strip("_") if details else "")
    return "\n".join([head] + keep + details)


def _trim_pricing(text):
    """Keep the revenue line(s); the 'Checks:' line is dropped - anything wrong is already in Decisions."""
    return "\n".join(l for l in (text or "").split("\n") if not l.startswith("Checks: "))


def compose(token, health_text, now_ms=None):
    """BRIEF_MODE=compact (default since 2026-11-07, owner: "cut it down a bit, and use this a/b test as one of the
    only things it sends me"): revenue + sign-ups, the A/B scoreboard, and alerts only when something needs action.
    BRIEF_MODE=full = the previous long brief (compose_full)."""
    if os.environ.get("BRIEF_MODE", "compact").strip().lower() == "full":
        return compose_full(token, health_text, now_ms)
    return compose_compact(token, health_text, now_ms)


def compose_compact(token, health_text, now_ms=None):
    now_ms = now_ms or int(dt.datetime.utcnow().timestamp() * 1000)
    today = dt.datetime.utcfromtimestamp(now_ms / 1000).strftime("%a %b %d")
    head, alerts = [], []
    try:
        p = payments(token, now_ms)
        head.append(f"${p['rev24']:,.0f} from {p['n24']} purchases{_fmt_delta(p['rev24'], p['rev7'])}")
        if p["rev7"] and p["rev24"] < 0.4 * p["rev7"] and p["rev7"] > 100:
            alerts.append(f"Revenue ${p['rev24']:,.0f} vs ${p['rev7']:,.0f}/day average - check checkout and the Stripe webhook.")
    except Exception as e:
        head.append(f"revenue unavailable ({str(e)[:60]})")
    try:
        s24, s7 = signups(token, now_ms)
        head.append(f"{s24} sign-ups{_fmt_delta(s24, s7)}")
        if s7 and s24 < 0.5 * s7:
            alerts.append(f"Sign-ups fell to {s24} vs {s7:.0f}/day - check the sign-up page and Google sign-in.")
    except Exception as e:
        head.append(f"sign-ups unavailable ({str(e)[:60]})")
    if ":red_circle:" in (health_text or ""):
        alerts.append("Endpoint health has a red line - Tulle Admin -> Health tab.")
    try:
        _, rec_alerts = rec_line(token)
        alerts += rec_alerts
    except Exception:
        pass
    try:
        import rec_watch
        _, _, light = rec_watch.railway_section(token)
        if light == ":red_circle:":
            alerts.append("Railway search: fallbacks or errors over the hold line - consider REC_RAILWAY_SEARCH_PCT=0.")
    except Exception:
        pass
    try:
        import ab_report
        ab = ab_report.block(token, now_ms)
    except Exception as e:
        ab = f":warning: _A/B scoreboard failed: {e}_"
    out = [f"*Tulle daily - {today}*", "• " + " · ".join(head)] + (["", ab] if ab else [])
    if alerts:
        out += ["", "*Needs you*"] + [f"• {a}" for a in alerts]
    return "\n".join(out)


def compose_full(token, health_text, now_ms=None):
    health_text = _trim_health(health_text)
    now_ms = now_ms or int(dt.datetime.utcnow().timestamp() * 1000)
    today = dt.datetime.utcfromtimestamp(now_ms / 1000).strftime("%a %b %d")
    tldr, decisions, sections = [], [], []

    # Endpoint health: count lights in the unchanged section.
    reds, ambers = health_text.count(":red_circle:"), health_text.count(":large_yellow_circle:")
    if reds:
        tldr.append(f":red_circle: Endpoints: {reds} red - see Endpoint health below")
        decisions.append("Endpoint health has a red line - look at it first (Tulle Admin -> Health tab).")
    elif ambers:
        tldr.append(f":large_yellow_circle: Endpoints: {ambers} amber, rest green")
    else:
        tldr.append(":large_green_circle: Endpoints: all green")

    try:
        p = payments(token, now_ms)
        tldr.insert(0, f":moneybag: ${p['rev24']:,.0f} from {p['n24']} purchases in 24h{_fmt_delta(p['rev24'], p['rev7'])}"
                       f" · {p['forever24']} Forever")
        if p["rev7"] and p["rev24"] < 0.4 * p["rev7"] and p["rev7"] > 100:
            decisions.append(f"Revenue was ${p['rev24']:,.0f} vs ${p['rev7']:,.0f}/day average - check checkout and the "
                             "Stripe webhook (one slow day is normal; two in a row is not).")
    except Exception as e:
        tldr.insert(0, f":warning: revenue unavailable ({e})")

    try:
        s24, s7 = signups(token, now_ms)
        tldr.insert(1, f":bust_in_silhouette: {s24} sign-ups{_fmt_delta(s24, s7)}")
        if s7 and s24 < 0.5 * s7:
            decisions.append(f"Sign-ups fell to {s24} vs {s7:.0f}/day - check the sign-up page and Google sign-in.")
    except Exception as e:
        tldr.insert(1, f":warning: sign-ups unavailable ({e})")

    try:
        import bnpl_watch
        sections.append(_trim_pricing(bnpl_watch.build(token, now_ms=now_ms, short=True)))
        decisions += getattr(bnpl_watch.build, "alerts", [])
    except Exception as e:
        sections.append(f":warning: _Pricing test section failed: {e}_")

    # PDF gate (2026-11-09): leak watch + the 10-28 -> 11-09 incident note for a week.
    try:
        line, alerts = pdf_gate_section(token, now_ms)
        sections.append(line)
        decisions += alerts
    except Exception as e:
        sections.append(f":warning: _PDF gate section failed: {e}_")
    if now_ms < RECOVERY_UNTIL_MS:
        try:
            line, alerts = recovery_section(token, now_ms)
            sections.append(line)
            decisions += alerts
        except Exception as e:
            sections.append(f":warning: _Purchase recovery line failed: {e}_")

    rec_tl = None
    try:
        line, alerts = rec_line(token)
        sections.append(line)
        decisions += alerts
        rec_tl = ",".join(line.split(" - ", 1)[1].split(",")[:2]) if " - " in line else None
    except Exception as e:
        sections.append(f":warning: _Tulle Assistant section failed: {e}_")

    # Daily Planner block (2026-11-02, owner: speed, accuracy misses, lookup hit rate, PDF funnel, categories, ratings).
    try:
        import rec_watch as _rw
        sections.append(_rw.planner_daily(token))
    except Exception as e:
        sections.append(f":warning: _Planner daily block failed: {e}_")

    # Railway search rollout (2026-10-26): its own block under the Tulle Assistant line + one TL;DR bullet.
    # rec_watch.railway_section never raises; the try is belt and braces so the brief always goes out.
    try:
        import rec_watch
        lines, rail_tl, light = rec_watch.railway_section(token)
        # 2026-10-31: the TL;DR bullet carries the verdict; the full block only when something is wrong.
        if light == ":red_circle:" or light is None:
            sections.append("\n".join(lines))
        tldr.append(rail_tl)
        if light == ":red_circle:":
            decisions.append("Railway search: fallbacks or errors over the hold line - check Railway search-api, "
                             "consider REC_RAILWAY_SEARCH_PCT=0 on rec-engine.")
        elif light == ":large_green_circle:":
            decisions.append("Railway search on track - decide whether to ramp REC_RAILWAY_SEARCH_PCT to 25.")
    except Exception as e:
        sections.append(f":warning: _Railway search section failed: {e}_")

    try:
        n = nps(token, now_ms)
        light = ":large_green_circle:" if n["n24"] >= NPS_GOAL_PER_DAY else ":large_yellow_circle:"
        avg = f", 7-day avg {n['avg7']:.1f}/5 from {n['n7']}" if n["avg7"] else ""
        sections.append(f"{light} *NPS* - {n['n24']} rating(s) in 24h (goal {NPS_GOAL_PER_DAY}/day){avg}"
                        f" · {n['comments24']} comment(s) in #feedback")
        tldr.append(f":speech_balloon: Assistant: {rec_tl or 'see below'} · NPS: {n['n24']} rating(s)")
        if n["n7"] == 0 and now_ms > 1791129600000 + 3 * DAY:   # 3+ days after the 2026-10-04 ~16:00 UTC modal publish
            decisions.append("NPS: 0 ratings in 7 days even with the modal back. Decide: turn on the one-click email "
                             "rating (on hold since 10-04).")
    except Exception as e:
        sections.append(f":warning: _NPS section failed: {e}_")

    # Mondays (2026-11-01): the weekly Tulle Planner digest rides under the daily brief - who used it, who came back,
    # dead ends, the check-in card, and the week's questions grouped into themes (rec_watch.weekly_planner).
    if dt.datetime.utcfromtimestamp(now_ms / 1000).weekday() == 0 or "--weekly" in sys.argv:
        try:
            import rec_watch
            sections.append("")
            sections.append(rec_watch.weekly_planner(token))
        except Exception as e:
            sections.append(f":warning: _Planner weekly digest failed: {e}_")

    out = [f"*Tulle daily - {today}*", "*TL;DR*"] + [f"• {t}" for t in tldr]
    out.append("*Decisions & suggestions*")
    out += [f"• {d}" for d in decisions] if decisions else ["• Nothing needs a decision today."]
    out += ["", health_text, ""] + sections
    return "\n".join(out)


if __name__ == "__main__":
    if "--preview" not in sys.argv:
        sys.exit("usage: python daily_brief.py --preview   (the cron path is perf_digest.py --health)")
    tok = os.environ.get("XANO_METADATA_TOKEN")
    if not tok:
        sys.exit("XANO_METADATA_TOKEN not set")
    import perf_digest
    _, _, _, health_text = perf_digest.run_health(write_history=False)
    print(compose(tok, health_text))
