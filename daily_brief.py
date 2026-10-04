"""Daily brief (Oct 2026) - wraps the 13:32 UTC Tulle Ops health DM in a TL;DR + Decisions list.

    python daily_brief.py --preview     # print today's brief from live data, post nothing

User ask 2026-10-04: the DM had grown long (the pricing-test section alone was ~40 lines). Shape:

    *Tulle daily - <date>*
    TL;DR            4 bullets: money, signups, endpoint health, assistant + NPS
    Decisions        only things that need a human today (from every section's alerts), else "none"
    Endpoint health  UNCHANGED (perf_digest.format_health_short) - the user asked to keep it
    Pricing test     2 lines (bnpl_watch short=True); full tables stay in the logs + dashboard
    Tulle Assistant  1 line (rec_watch)
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


def compose(token, health_text, now_ms=None):
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
        sections.append(bnpl_watch.build(token, now_ms=now_ms, short=True))
        decisions += getattr(bnpl_watch.build, "alerts", [])
    except Exception as e:
        sections.append(f":warning: _Pricing test section failed: {e}_")

    rec_tl = None
    try:
        line, alerts = rec_line(token)
        sections.append(line)
        decisions += alerts
        rec_tl = ",".join(line.split(" - ", 1)[1].split(",")[:2]) if " - " in line else None
    except Exception as e:
        sections.append(f":warning: _Tulle Assistant section failed: {e}_")

    try:
        n = nps(token, now_ms)
        light = ":large_green_circle:" if n["n24"] >= NPS_GOAL_PER_DAY else ":large_yellow_circle:"
        avg = f", 7-day avg {n['avg7']:.1f}/5 from {n['n7']}" if n["avg7"] else ""
        sections.append(f"{light} *NPS* - {n['n24']} rating(s) in 24h (goal {NPS_GOAL_PER_DAY}/day){avg}"
                        f" · {n['comments24']} comment(s) - comments land in #feedback")
        tldr.append(f":speech_balloon: Assistant: {rec_tl or 'see below'} · NPS: {n['n24']} rating(s)")
        if n["n7"] == 0 and now_ms > 1791129600000 + 3 * DAY:   # 3+ days after the 2026-10-04 ~16:00 UTC modal publish
            decisions.append("NPS: 0 ratings in 7 days even with the modal back. Decide: turn on the one-click email "
                             "rating (on hold since 10-04).")
    except Exception as e:
        sections.append(f":warning: _NPS section failed: {e}_")

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
