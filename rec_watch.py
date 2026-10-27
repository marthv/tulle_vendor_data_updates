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
CAP = float(os.environ.get("REC_GLOBAL_DAILY_USD", "40"))


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
    errors = sum(1 for r in rows if r.get("status") == "error")
    capped = sum(1 for r in rows if r.get("status") in ("blocked_global_cap", "killed"))
    monthly = len({r.get("user_id") for r in rows if r.get("status") == "blocked_monthly_cap"})
    lat = [r.get("latency_ms") or 0 for r in ok if r.get("latency_ms")]
    light = ":red_circle:" if errors > max(2, 0.1 * len(rows)) or capped else ":large_green_circle:"
    lines = [
        "%s %s - %d users, %d opening + %d refine, $%.2f spent ($%.0f/day cap), $%.3f per user"
        % (light, head, len(users), opening, refine, spend, CAP, spend / max(len(users), 1)),
        "Paywall shown (free limit hit): %d · errors: %d · blocked by cap/kill switch: %d · Forever users at the "
        "300/month cap: %d · median %.1fs" % (paywall, errors, capped, monthly, (st.median(lat) / 1000) if lat else 0),
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
