"""Recommendation engine watch - one short section of the daily health DM (Oct 2026).

    python rec_watch.py              # print the section (needs XANO_METADATA_TOKEN)

Reads Xano table 79 rec_usage (one row per AI request, written by rec_service). Reports the last
24h: users, opening vs refine requests, spend vs the $/day cap, free-limit hits (= paywall
shown), errors, and median latency. Silent ("no usage yet") until the page is live.
railway_section (2026-10-26): the Railway search rollout block (per arm, fallbacks, index freshness, verdict).

Metadata search: single-key filters only - a two-key dict is silently ignored and returns the
whole table (measured 2026-10-03) - so rows are fetched by usage_day and re-filtered here.
"""
import datetime as dt
import os
import statistics as st
import sys

import requests

META = "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta/workspace/1"
TABLE = int(os.environ.get("REC_USAGE_TABLE_ID", "79"))
CAP = float(os.environ.get("REC_GLOBAL_DAILY_USD", "60"))        # owner 2026-10-31: $60/day for the whole service
FREE_CAP = float(os.environ.get("REC_FREE_DAILY_USD", "30"))    # owner 2026-10-31: $30/day for free traffic


def _rows_for_day(token, day):
    rows, page = [], 1
    while page <= 50:
        r = requests.post(META + "/table/%d/content/search" % TABLE, timeout=60,
                          headers={"Authorization": "Bearer " + token},
                          json={"search": [{"usage_day": day}], "page": page, "per_page": 500})
        r.raise_for_status()
        d = r.json()
        rows += [x for x in d.get("items", []) if x.get("usage_day") == day]
        if not d.get("nextPage"):
            break
        page += 1
    return rows


def build(token, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    since_ms = (now - dt.timedelta(hours=24)).timestamp() * 1000
    days = {(now - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in (0, 1)}
    rows = [r for d in days for r in _rows_for_day(token, d) if (r.get("created_at") or 0) >= since_ms]
    head = "*Recommendation engine* (last 24h)"
    if not rows:
        return head + " - no usage yet."
    ok = [r for r in rows if r.get("status") == "ok"]
    users = {r.get("user_id") for r in rows}
    spend = sum(float(r.get("cost_usd") or 0) for r in ok)
    opening = sum(1 for r in ok if r.get("kind") == "opening")
    refine = sum(1 for r in ok if r.get("kind") == "refine")
    paywall = sum(1 for r in rows if r.get("status") == "blocked_free_limit")
    daily_cap = sum(1 for r in rows if r.get("status") == "blocked_user_cap")
    # Free spend = rows the engine flagged as free (counted_as_free, or tier "free" once table 79 has the column).
    free_spend = sum(float(r.get("cost_usd") or 0) for r in ok if r.get("counted_as_free") or r.get("tier") == "free")
    free_users = {r.get("user_id") for r in rows if r.get("counted_as_free") or r.get("tier") == "free"}
    errors = sum(1 for r in rows if r.get("status") == "error")
    capped = sum(1 for r in rows if r.get("status") in ("blocked_global_cap", "killed"))
    monthly = len({r.get("user_id") for r in rows if r.get("status") == "blocked_monthly_cap"})
    lat = [r.get("latency_ms") or 0 for r in ok if r.get("latency_ms")]
    light = ":red_circle:" if errors > max(2, 0.1 * len(rows)) or capped else ":large_green_circle:"
    lines = [
        "%s %s - %d users (%d free), %d opening + %d refine · spent $%.2f (free $%.2f of $%.0f pool · $%.0f/day cap) · $%.3f per user"
        % (light, head, len(users), len(free_users), opening, refine, spend, free_spend, FREE_CAP, CAP, spend / max(len(users), 1)),
        "Paywall shown: %d · daily-cap hits: %d · errors: %d · blocked by cap/kill switch: %d · Forever users at the "
        "300/month cap: %d · median %.1fs" % (paywall, daily_cap, errors, capped, monthly, (st.median(lat) / 1000) if lat else 0),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- Railway search rollout (2026-10-26)
# Owner decision 2026-10-26: REC_RAILWAY_SEARCH_PCT=10 of assistant users get venue searches from the Railway
# search-api, ep119 as the fallback. rec_service writes search_engine ('railway' | 'ep119' | 'mixed' = a Railway-arm
# request where a search fell back | '' = no venue search), search_fallback (first reason) and search_ms on every
# table-79 row. Arms here are by search_engine: railway = 'railway' + 'mixed'; ep119 = 'ep119'. Rows without a venue
# search ('') have no engine and are left out. "Since start" = since the first railway/mixed row (ep119 rows before
# it are pre-rollout baseline, written since the deploy with the flag still at 0).

HEALTH_URL = os.environ.get("REC_RAILWAY_SEARCH_HEALTH_URL", "https://search-api-production-66bb.up.railway.app/health")
LOOKBACK_DAYS = int(os.environ.get("REC_RAILWAY_SEARCH_LOOKBACK_DAYS", "45"))
MIN_REQUESTS = 20          # fewer railway requests since start -> "collecting data"
RAMP_REQUESTS = 50         # at least this many before "ready to ramp"
HOLD_FALLBACK = 0.15       # railway fallback rate above this -> hold
HOLD_ERROR_PP = 0.03       # railway error rate above ep119's + 3pp -> hold
RAMP_FALLBACK = 0.05       # railway fallback rate below this (and search p50 >= 30% faster) -> ramp
RAMP_SPEEDUP = 0.30
BLOCK = "*Railway search (assistant)*"


INTERNAL_USER_IDS = {14998, 30614, 32636, 118, 5622, 34249, 33167, 185}   # owner / test / team accounts (118 Desiree, 5622 Kate + their test accts, owner 2026-10-27) (memory: exclude internal accounts)


def search_rows(token, now=None, fetch=None):
    """Rows with search_engine set, today backwards, stopping at the first earlier day whose rows carry no
    search_engine at all (= before the rec_service deploy that writes the column) or after LOOKBACK_DAYS.
    One metadata search per day."""
    now = now or dt.datetime.now(dt.timezone.utc)
    fetch = fetch or (lambda d: _rows_for_day(token, d))
    out = []
    for i in range(LOOKBACK_DAYS):
        day_rows = fetch((now - dt.timedelta(days=i)).strftime("%Y-%m-%d"))
        # Internal accounts (owner, test, team) are forced onto Railway or test by hand - keep them out of the comparison.
        tagged = [r for r in day_rows if r.get("search_engine") and int(r.get("user_id") or 0) not in INTERNAL_USER_IDS]
        out += tagged
        if day_rows and not tagged and i > 0:
            break
    return out


def _pct(vals, q):
    vals = sorted(v for v in vals if v)
    if not vals:
        return None
    return vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]


def arm_stats(rows):
    ok = [r for r in rows if r.get("status") == "ok"]
    reasons = {}
    for r in rows:
        if r.get("search_fallback"):
            reasons[r["search_fallback"]] = reasons.get(r["search_fallback"], 0) + 1
    n = len(rows)
    return {"n": n, "users": len({r.get("user_id") for r in rows}),
            "mixed": sum(1 for r in rows if r.get("search_engine") == "mixed"),
            "lat50": _pct([r.get("latency_ms") for r in ok], .5), "lat90": _pct([r.get("latency_ms") for r in ok], .9),
            "s50": _pct([r.get("search_ms") for r in rows], .5), "s90": _pct([r.get("search_ms") for r in rows], .9),
            "fallbacks": sum(reasons.values()), "reasons": sorted(reasons.items(), key=lambda kv: -kv[1]),
            "fallback_rate": (sum(reasons.values()) / n) if n else 0.0,
            "error_rate": (sum(1 for r in rows if r.get("status") == "error") / n) if n else 0.0}


def _split(rows):
    return ([r for r in rows if r.get("search_engine") in ("railway", "mixed")],
            [r for r in rows if r.get("search_engine") == "ep119"])


def verdict(rail, ep):
    """rail / ep: arm_stats since the rollout start -> (emoji, text). Rules in order (owner brief 2026-10-26)."""
    if rail["n"] < MIN_REQUESTS:
        return ":white_circle:", "collecting data (%d/%d Railway requests)" % (rail["n"], MIN_REQUESTS)
    if rail["fallback_rate"] > HOLD_FALLBACK or rail["error_rate"] > ep["error_rate"] + HOLD_ERROR_PP:
        return ":red_circle:", "hold - check fallbacks / consider REC_RAILWAY_SEARCH_PCT=0"
    if not ep["n"]:
        # 100% rollout (owner 2026-10-27, Planner on Railway search with a breaker): no ep119 arm left to compare
        # against. Fallbacks (incl. breaker_open skips during a search-api freeze) and errors are the signal.
        if rail["n"] >= RAMP_REQUESTS and rail["fallback_rate"] < RAMP_FALLBACK:
            return ":large_green_circle:", "at 100% - healthy"
        return ":large_yellow_circle:", "at 100% - watching"
    if (rail["n"] >= RAMP_REQUESTS and rail["fallback_rate"] < RAMP_FALLBACK and rail["s50"] and ep["s50"]
            and rail["s50"] <= (1 - RAMP_SPEEDUP) * ep["s50"]):
        return ":large_green_circle:", "on track - ready to ramp to 25%"
    return ":large_yellow_circle:", "watching"


def _ms(v):
    return "-" if v is None else ("%.1fs" % (v / 1000) if v >= 1000 else "%dms" % v)


def _arm_line(label, a):
    if not a["n"]:
        return "%s: no requests" % label
    mixed = " (%d mixed)" % a["mixed"] if a["mixed"] else ""
    top = ", ".join("%s %d" % kv for kv in a["reasons"][:3])
    return ("%s: %d req%s, %d users · latency p50 %s / p90 %s · search p50 %s / p90 %s · fallbacks %d%s · errors %.0f%%"
            % (label, a["n"], mixed, a["users"], _ms(a["lat50"]), _ms(a["lat90"]), _ms(a["s50"]), _ms(a["s90"]),
               a["fallbacks"], " (%s)" % top if top else "", 100 * a["error_rate"]))


def freshness(health, now):
    """One line from search-api /health (None = the GET failed)."""
    if not health:
        return "Index freshness: unavailable (/health did not answer)"
    sync = health.get("sync") or {}
    at = sync.get("last_sync_at") or health.get("last_sync")
    bits = []
    if at:
        try:
            age = (now - dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))).total_seconds() / 60
            bits.append("last sync %d min ago (%s)" % (age, at) + (" :warning: over 26h" if age > 26 * 60 else ""))
        except ValueError:
            bits.append("last sync %s" % at)
    else:
        bits.append("last sync unknown")
    if sync.get("last_sync_status") and sync.get("last_sync_status") != "ok":
        bits.append(":warning: last sync %s" % sync.get("last_sync_status"))
    if health.get("search_enabled") is False:
        bits.append(":warning: SEARCH_ENABLED=0 (every Railway search falls back)")
    return "Index freshness: " + " · ".join(bits)


def railway_block(rows, health, now=None):
    """(lines, tldr, verdict_emoji) for the block. Pure: rows (table 79, search_engine set) and health passed in."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since_ms = (now - dt.timedelta(hours=24)).timestamp() * 1000
    rail_all = [r for r in rows if r.get("search_engine") in ("railway", "mixed")]
    start = min((r.get("created_at") or 0) for r in rail_all) if rail_all else None
    lines = [BLOCK]
    if start is None:
        e, v = verdict(arm_stats([]), arm_stats([]))
        lines += ["No Railway-arm venue searches yet (REC_RAILWAY_SEARCH_PCT may still be 0).",
                  freshness(health, now), "%s Verdict: %s" % (e, v)]
        return lines, "%s Railway search: %s" % (e, v), e
    since = [r for r in rows if (r.get("created_at") or 0) >= start]
    day = [r for r in rows if (r.get("created_at") or 0) >= since_ms]
    r24, e24 = (arm_stats(x) for x in _split(day))
    rs, es = (arm_stats(x) for x in _split(since))
    started = dt.datetime.fromtimestamp(start / 1000, dt.timezone.utc).strftime("%b %d %H:%M UTC")
    lines += ["_Last 24h_", "• " + _arm_line("Railway", r24), "• " + _arm_line("ep119", e24),
              "_Since start (%s)_" % started, "• " + _arm_line("Railway", rs), "• " + _arm_line("ep119", es),
              freshness(health, now)]
    e, v = verdict(rs, es)
    lines.append("%s Verdict: %s" % (e, v))
    speed = (" · search p50 %s vs %s" % (_ms(rs["s50"]), _ms(es["s50"]))) if rs["s50"] and es["s50"] else ""
    tldr = "%s Railway search: %s - %d Railway req since start, fallback %.0f%%%s" % (
        e, v, rs["n"], 100 * rs["fallback_rate"], speed)
    return lines, tldr, e


def railway_section(token, now=None, get_rows=None, get_health=None):
    """(lines, tldr, verdict_emoji) from live data. Never raises: a failure is one short 'unavailable' line."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        rows = get_rows() if get_rows else search_rows(token, now)
    except Exception as e:
        return [BLOCK + " - unavailable (%s)" % str(e)[:120]], ":warning: Railway search: unavailable", None
    try:
        if get_health:
            health = get_health()
        else:
            r = requests.get(HEALTH_URL, timeout=10)
            health = r.json() if r.ok else None
    except Exception:
        health = None
    try:
        return railway_block(rows, health, now)
    except Exception as e:
        return [BLOCK + " - unavailable (%s)" % str(e)[:120]], ":warning: Railway search: unavailable", None


if __name__ == "__main__":
    tok = os.environ.get("XANO_METADATA_TOKEN")
    if not tok:
        sys.exit("XANO_METADATA_TOKEN not set")
    print(build(tok))
    print("\n".join(railway_section(tok)[0]))



# ---------------------------------------------------------------- weekly Planner digest (Mondays, 2026-11-01)
# Owner 2026-11-01: "are we collecting all the feedback we need for the Tulle Planner?" Explicit feedback was 6 answers
# in 14 days; the questions couples type, the dead ends and whether they come back were collected but never read.
# This is the weekly read: table 79 (usage), table 43 via 42 (what they asked), table 81 (check-in card), and - when
# ANTHROPIC_API_KEY is set on the cron - Sonnet groups the week's questions into themes. Never raises: every block
# degrades to a one-line note so the daily brief still goes out.

FEEDBACK_TABLE = int(os.environ.get("REC_FEEDBACK_TABLE_ID", "81"))
CHATS_TABLE, MESSAGES_TABLE = 42, 43
DIGEST_MODEL = os.environ.get("REC_DIGEST_MODEL", "claude-sonnet-5-5")


def _table_rows(token, table, since_ms, max_pages=12, per_page=500):
    """All rows of a small table created on/after since_ms (the metadata list endpoint has no date filter, so this
    pages through and keeps what is new enough; tables 42/43/81 are a few thousand rows at most)."""
    out, page = [], 1
    while page <= max_pages:
        r = requests.get(META + "/table/%d/content" % table, timeout=60,
                         headers={"Authorization": "Bearer " + token}, params={"page": page, "per_page": per_page})
        r.raise_for_status()
        d = r.json()
        items = d.get("items", [])
        out += [x for x in items if (x.get("created_at") or 0) >= since_ms]
        if not d.get("nextPage") or not items:
            break
        page += 1
    return out


def _themes(questions, api_key):
    """Sonnet groups the week's questions into themes with counts and one verbatim quote each. Plain Slack lines."""
    if not questions:
        return "_No typed questions this week._"
    if not api_key:
        seen, out = set(), []
        for q in questions:
            k = q.strip().lower()
            if k and k not in seen:
                seen.add(k); out.append("• " + q.strip()[:140])
            if len(out) >= 12:
                break
        return "\n".join(out) + ("\n_(set ANTHROPIC_API_KEY on the cron for themes)_")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        body = "\n".join("- " + q.strip()[:300] for q in questions[:400])
        msg = client.messages.create(
            model=DIGEST_MODEL, max_tokens=900,
            system="You summarise what couples asked a wedding-planning assistant this week for the founder's Monday "
                   "Slack digest. Group the questions into at most 8 themes, most common first. Output plain text lines "
                   "only, no markdown headers: each theme as '• <theme> (<n>) - \"<one short verbatim quote>\"'. Then a "
                   "line 'Unmet needs:' followed by at most 3 lines '• ...' naming things the assistant was asked for "
                   "that a venue-pricing planner may not do yet. Under 1,500 characters total. No preamble.",
            messages=[{"role": "user", "content": "Questions (one per line, %d total):\n%s" % (len(questions), body)}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content).strip()
        return text[:1800] or "_No themes returned._"
    except Exception as e:
        return "_Themes unavailable (%s); %d questions this week._" % (str(e)[:80], len(questions))


def weekly_planner(token, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=7)
    since_ms = int(since.timestamp() * 1000)
    prev_ms = int((now - dt.timedelta(days=14)).timestamp() * 1000)
    head = "*Tulle Planner - week to %s*" % now.strftime("%a %b %d")
    lines = [head]

    # 1. Usage (table 79): who, how much, dead ends, return.
    try:
        days = [(now - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(0, 14)]
        rows = [r for d in days for r in _rows_for_day(token, d)]
        rows = [r for r in rows if int(r.get("user_id") or 0) not in INTERNAL_USER_IDS]
        week = [r for r in rows if (r.get("created_at") or 0) >= since_ms]
        prev = [r for r in rows if prev_ms <= (r.get("created_at") or 0) < since_ms]
        ok = [r for r in week if r.get("status") == "ok"]
        users = {r.get("user_id") for r in week}
        prev_users = {r.get("user_id") for r in prev}
        returning = users & prev_users
        by_user_days = {}
        for r in week:
            by_user_days.setdefault(r.get("user_id"), set()).add(r.get("usage_day"))
        repeat = sum(1 for u, ds in by_user_days.items() if len(ds) >= 2)
        openings = sum(1 for r in ok if r.get("kind") == "opening")
        questions = sum(1 for r in ok if r.get("kind") == "refine")
        tools = sum(1 for r in ok if str(r.get("kind") or "").startswith("tool_"))
        free_users = {r.get("user_id") for r in week if r.get("counted_as_free") or r.get("tier") == "free"}
        lat = sorted(r.get("latency_ms") or 0 for r in ok if r.get("kind") == "opening" and r.get("latency_ms"))
        p50 = (lat[len(lat) // 2] / 1000) if lat else 0
        dead = {}
        for r in week:
            if r.get("status") != "ok":
                dead[r.get("status") or "?"] = dead.get(r.get("status") or "?", 0) + 1
        dead_txt = ", ".join("%s %d" % (k, v) for k, v in sorted(dead.items(), key=lambda kv: -kv[1])) or "none"
        lines.append("• %d people used it (%d free) · %d openings · %d questions · %d cost-tool runs · opening median %.1fs"
                     % (len(users), len(free_users), openings, questions, tools, p50))
        ret_pct = (100.0 * len(returning) / len(prev_users)) if prev_users else 0
        lines.append("• Came back: %d of last week's %d (%.0f%%) · %d used it on 2+ days this week · %d asked 0 questions after the opening"
                     % (len(returning), len(prev_users), ret_pct, repeat,
                        sum(1 for u in users if not any(r.get("kind") == "refine" and r.get("user_id") == u for r in ok))))
        lines.append("• Dead ends: " + dead_txt)
    except Exception as e:
        lines.append(":warning: _usage block failed: %s_" % str(e)[:120])

    # 2. The check-in card (table 81).
    try:
        fb = [r for r in _table_rows(token, FEEDBACK_TABLE, since_ms) if int(r.get("user_id") or 0) not in INTERNAL_USER_IDS]
        if fb:
            ratings = [int(r.get("rating") or 0) for r in fb if r.get("rating")]
            avg = (sum(ratings) / len(ratings)) if ratings else 0
            use = {}
            for r in fb:
                use[r.get("would_use") or "-"] = use.get(r.get("would_use") or "-", 0) + 1
            lines.append("• Check-in card: %d answers, avg %.1f/5 · keep using: %s" % (len(fb), avg, ", ".join("%s %d" % kv for kv in use.items())))
            quotes = [str(r.get("text") or "").strip() for r in fb if str(r.get("text") or "").strip()]
            for q in quotes[:6]:
                lines.append("    - “%s”" % q[:160])
        else:
            lines.append("• Check-in card: 0 answers this week")
    except Exception as e:
        lines.append(":warning: _feedback block failed: %s_" % str(e)[:120])

    # 3. What they asked (tables 43 + 42), themed.
    try:
        chats = _table_rows(token, CHATS_TABLE, prev_ms)
        owner = {int(c.get("id")): int(c.get("user_id") or 0) for c in chats if c.get("id")}
        msgs = [m for m in _table_rows(token, MESSAGES_TABLE, since_ms) if m.get("role") == "user"]
        qs = []
        for m in msgs:
            uid = owner.get(int(m.get("chat_id") or 0))
            if uid is None or uid in INTERNAL_USER_IDS:
                continue
            t = str(m.get("content") or "").strip()
            if t and not t.startswith("I updated my wedding details"):
                qs.append(t)
        lines.append("")
        lines.append("*What %d people asked (%d questions)*" % (len({owner.get(int(m.get('chat_id') or 0)) for m in msgs} - set(INTERNAL_USER_IDS) - {None}), len(qs)))
        lines.append(_themes(qs, os.environ.get("ANTHROPIC_API_KEY", "")))
    except Exception as e:
        lines.append(":warning: _questions block failed: %s_" % str(e)[:120])

    lines.append("")
    lines.append("_Not here yet: PDF opens / saves from the chat and venue requests (Mixpanel only), thumbs text (Slack only)._")
    return "\n".join(lines)


# ---------------------------------------------------------------- daily Planner block (2026-11-02)
# Owner 2026-11-01: "measuring and improving speed of Tulle Planner, feedback constantly, pushing people to the right
# events (free -> PDFs and venues, paid -> more of the tool and every category)". Everything here reads table 79 (the
# rec_service usage log: tools / categories / think_chars columns since 2026-11-02, 'pdf' rows per open attempt,
# 'lookup:<status>' on instant lookups), table 43 (messages, for the answers that said we don't have something) and
# table 81 (the check-in card). Internal accounts are left out. Never raises: each block degrades to a warning line.

MISS_PHRASES = ("couldn't find", "could not find", "don't have", "do not have", "no venues listed", "not in tulle",
                "still nothing for", "isn't in tulle", "is not in tulle")


def _p(vals, q):
    vals = sorted(v for v in vals if v is not None)
    return vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))] if vals else 0


def _lat_line(label, rows, prev):
    cur = [r["latency_ms"] / 1000 for r in rows if r.get("latency_ms")]
    old = [r["latency_ms"] / 1000 for r in prev if r.get("latency_ms")]
    if not cur:
        return "%s: none" % label
    d = ""
    if old:
        d = " (7d p50 %.1fs)" % _p(old, .5)
    return "%s: %d · p50 %.1fs · p90 %.1fs%s" % (label, len(cur), _p(cur, .5), _p(cur, .9), d)


def planner_daily(token, now=None):
    """The Planner block of the daily brief: speed by request type, accuracy misses with the couple's words, lookup
    hit rate, the PDF funnel by plan, categories paid members touched, and ratings - last 24 h against the 7 days
    before it."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since_ms = (now - dt.timedelta(hours=24)).timestamp() * 1000
    week_ms = (now - dt.timedelta(days=8)).timestamp() * 1000
    lines = ["*Tulle Planner - last 24h*"]
    try:
        days = [(now - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(0, 9)]
        rows = [r for d in days for r in _rows_for_day(token, d)]
        rows = [r for r in rows if int(r.get("user_id") or 0) not in INTERNAL_USER_IDS]
        day = [r for r in rows if (r.get("created_at") or 0) >= since_ms]
        prev = [r for r in rows if week_ms <= (r.get("created_at") or 0) < since_ms]
        ok = [r for r in day if r.get("status") == "ok"]
        pok = [r for r in prev if r.get("status") == "ok"]
        is_lookup = lambda r: r.get("model") == "lookup"
        kinds = [("Opening picks", lambda r: r.get("kind") == "opening"),
                 ("Typed question (model)", lambda r: r.get("kind") == "refine" and not is_lookup(r)),
                 ("Venue name (instant)", lambda r: r.get("kind") == "refine" and is_lookup(r))]
        users = {r.get("user_id") for r in day if r.get("kind") in ("opening", "refine", "more")}
        # Table 79 has no plan column: a counted_as_free row marks a free member's question; the rest of the people
        # are paid members or free members who only saw the opening.
        free_users = {r.get("user_id") for r in day if r.get("counted_as_free") and r.get("kind") != "pdf"}
        lines.append("• %d people (%d asked a free question) · %d openings · %d questions" % (
            len(users), len(free_users & users), sum(1 for r in ok if r.get("kind") == "opening"),
            sum(1 for r in ok if r.get("kind") == "refine")))
        lines.append("• Speed - " + " | ".join(_lat_line(lbl, [r for r in ok if f(r)], [r for r in pok if f(r)])
                                                 for lbl, f in kinds))
        # Instant lookup outcomes (tools = 'lookup:<status>').
        lk = [str(r.get("tools") or "") for r in ok if is_lookup(r)]
        if lk:
            found = sum(1 for t in lk if t in ("lookup:found", "lookup:choices"))
            lines.append("• Venue lookups: %d · found %d · not in Tulle %d" % (len(lk), found, sum(1 for t in lk if t == "lookup:not_found")))
        # PDF funnel ('pdf' rows: status paid | free | blocked).
        pdf = [r for r in day if r.get("kind") == "pdf"]
        if pdf:
            by = {}
            for r in pdf:
                by[r.get("status") or "?"] = by.get(r.get("status") or "?", 0) + 1
            openers = {r.get("user_id") for r in pdf if r.get("status") in ("paid", "free")}
            lines.append("• PDFs from the Planner: %d opens by %d people (%s) · %.0f%% of today's Planner users opened one" % (
                sum(v for k, v in by.items() if k != "blocked"), len(openers),
                ", ".join("%s %d" % kv for kv in sorted(by.items())), 100.0 * len(openers & users) / max(1, len(users))))
        else:
            lines.append("• PDFs from the Planner: 0 opens")
        # Categories: what the tools touched (rows since the 2026-11-02 deploy carry them; free questions left out so
        # the line reads as "what paid members reach for").
        cats = {}
        for r in ok:
            if r.get("counted_as_free") or r.get("kind") == "pdf" or not r.get("categories"):
                continue
            for c in str(r.get("categories") or "").split(","):
                if c:
                    cats[c] = cats.get(c, 0) + 1
        if cats:
            tot = sum(cats.values())
            lines.append("• Paid members' answers by category: " + ", ".join("%s %d%%" % (k, 100.0 * v / tot) for k, v in sorted(cats.items(), key=lambda kv: -kv[1])))
        # Tool-call shape: how many answers needed 3+ model turns.
        tc = [int(r.get("tool_calls") or 0) for r in ok if r.get("kind") == "refine" and not is_lookup(r)]
        if tc:
            lines.append("• Questions needing 3+ tool calls: %d of %d (%.0f%%)" % (sum(1 for t in tc if t >= 3), len(tc), 100.0 * sum(1 for t in tc if t >= 3) / len(tc)))
    except Exception as e:
        lines.append(":warning: _usage block failed: %s_" % str(e)[:120])

    # Accuracy misses: answers that said we don't have something, with what the couple typed.
    try:
        chats = _table_rows(token, CHATS_TABLE, week_ms)
        owner = {int(c.get("id")): int(c.get("user_id") or 0) for c in chats if c.get("id")}
        msgs = sorted(_table_rows(token, MESSAGES_TABLE, since_ms), key=lambda m: m.get("id") or 0)
        misses, last_user = [], {}
        for m in msgs:
            cid = int(m.get("chat_id") or 0)
            uid = owner.get(cid)
            if uid is None or uid in INTERNAL_USER_IDS:
                continue
            txt = str(m.get("content") or "")
            if m.get("role") == "user":
                last_user[cid] = txt
            elif m.get("role") == "assistant" and any(ph in txt.lower() for ph in MISS_PHRASES):
                misses.append((cid, last_user.get(cid, ""), txt))
        if misses:
            lines.append("• Answers that said we don't have it: %d" % len(misses))
            for cid, q, a in misses[:5]:
                lines.append("    - chat %d · they typed “%s” · we said “%s”" % (cid, q[:70], a[:90]))
        else:
            lines.append("• Answers that said we don't have it: 0")
    except Exception as e:
        lines.append(":warning: _misses block failed: %s_" % str(e)[:120])

    # Ratings (table 81).
    try:
        fb = [r for r in _table_rows(token, FEEDBACK_TABLE, since_ms) if int(r.get("user_id") or 0) not in INTERNAL_USER_IDS]
        ratings = [int(r.get("rating") or 0) for r in fb if r.get("rating")]
        if ratings:
            lines.append("• Ratings: %d · avg %.1f/5 · low (1-2): %d" % (len(ratings), sum(ratings) / len(ratings), sum(1 for x in ratings if x <= 2)))
            for q in [str(r.get("text") or "").strip() for r in fb if str(r.get("text") or "").strip()][:3]:
                lines.append("    - “%s”" % q[:140])
        else:
            lines.append("• Ratings: 0")
    except Exception as e:
        lines.append(":warning: _ratings block failed: %s_" % str(e)[:120])
    return "\n".join(lines)


if __name__ == "__main__" and "--daily" in sys.argv:
    tok = os.environ.get("XANO_METADATA_TOKEN")
    if not tok:
        sys.exit("XANO_METADATA_TOKEN not set")
    print(planner_daily(tok))


if __name__ == "__main__" and "--weekly" in sys.argv:
    tok = os.environ.get("XANO_METADATA_TOKEN")
    if not tok:
        sys.exit("XANO_METADATA_TOKEN not set")
    print(weekly_planner(tok))
