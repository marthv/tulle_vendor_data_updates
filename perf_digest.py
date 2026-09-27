"""Measures what real users actually experienced, from Xano's own request history.

WHY A PASSIVE DIGEST INSTEAD OF A SYNTHETIC PROBE
-------------------------------------------------
A prober hits a handful of URLs you thought to list, with one query shape, from one IP, on
a warm cache, while signed in correctly. It tells you the site is up. It cannot tell you
that 70% of real searches miss the cache because the frontend spells an empty filter four
different ways, or that 90% of sessions eat a 401 because the auth token arrives after the
first request. Both of those were true on 2026-09-12 and both are invisible to a probe.

Xano already records every request with its duration, status, inputs and headers. Reading
that back is the cheapest honest measurement available: it is the real users, the real query
mix, the real cache state. This reads it, compares against the budgets in perf_slo.json, and
alerts only on a breach.

A synthetic probe is still worth adding later for one thing this cannot do — notice an
outage at 3am when there is no traffic to measure. That is a second layer, not a substitute.

WHAT IT MEASURES
----------------
Per endpoint: n, p50/p95/p99, status mix, and (where Xano reports it) the cache hit rate.
Globally: 5xx count and the RACE-401 RATE, which is the metric that matters most here.

A raw 401 count is not actionable — signed-out visitors browsing public pages produce 401s
all day and always will. The actionable subset is a 401 sent by a client that was
demonstrably signed in moments before or after: that is the pre-auth race, where WeWeb fires
a request before the auth plugin has restored the token. Those are user-visible failures
(and worse, fetchUser's catch calls logout(), so the 401 can destroy the session). This
separates the two by asking, for each 401, whether the same client IP also got an
authenticated 200 within RACE_WINDOW_S.

That split is a heuristic, not a proof — mobile carrier NAT puts many users behind one IP,
so treat the ratio as directional with maybe 15% slop. It is still the difference between
"15% of requests are 401" (true, useless) and "the access badge fails for 4 out of 5
sessions" (true, and someone can go fix it).

WHY BUDGETS, NOT ABSOLUTES
--------------------------
Same reasoning as security_audit.py: an alert nobody can act on gets muted, and a muted
alert is worse than none. Budgets live in perf_slo.json as a reviewed, committed file, so
the file doubles as the record of what experience is considered acceptable and why.

Latency is only evaluated once an endpoint has min_requests_for_latency calls in the window
— a p95 over three requests is noise, and paging on noise is how this gets turned off.

SETUP
-----
    XANO_METADATA_TOKEN  — Xano metadata API token. Read-only usage here.
    SLACK_WEBHOOK_URL    — optional; posts breaches (and --digest) when non-empty.
    XANO_WORKSPACE_ID    — defaults to 1.

    python perf_digest.py            # report breaches, exit 1 if any
    python perf_digest.py --digest   # always post the full summary (the daily 08:00 run)
    python perf_digest.py --json     # emit the summary as JSON, for the dashboard tab
    python perf_digest.py --health   # daily deep-dive on the watched endpoints (see below)

THE DAILY ENDPOINT HEALTH REPORT (--health)
-------------------------------------------
The budget check above answers "is anything over budget". --health answers the questions you
ask when something is: for each endpoint in perf_slo.json's `health_watch` list — how often
did it fail, WHY (each failure is classified, not just counted), how long did it take, what
are people actually asking it, and which query shapes eat the most slow time.

Xano keeps only ~24h of request history, so the shape of failures over weeks is lost unless
someone writes it down. Each --health run appends one row per endpoint to a Xano table
(HEALTH_TABLE_ID) and compares today against the trailing 7 days of those rows. A write
failure is reported IN the Slack post, never swallowed — a history table that silently stops
filling is the same blind spot this exists to remove.

It also audits the ep119 cache warmer (task 8) from the server side: the warmer's own
requests appear in ep119's history with no Referer, so a run that should send 15 and sent
fewer, or a bucket that took long enough for the warmer's HTTP client to give up, is visible
here even though the warmer itself only reports a count.

    HEALTH_SLACK_WEBHOOK_URL — where --health posts (#tulle-users). Falls back to
                               SLACK_WEBHOOK_URL.
    HEALTH_TABLE_ID          — Xano table for the daily rollup. Unset = no history kept
                               (the post says so).
"""

import datetime as dt
import json
import os
import re
import sys

import requests

META_BASE = os.environ.get("XANO_META_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta")
WORKSPACE_ID = int(os.environ.get("XANO_WORKSPACE_ID", "1"))
SLO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "perf_slo.json")

# How close in time an authenticated success has to be, from the same client IP, for us to
# call a 401 a race rather than a signed-out visitor. Two minutes comfortably covers a page
# load's fan-out without spanning a genuine sign-out-then-sign-in.
RACE_WINDOW_S = 120

# Xano's history is paged newest-first. 500/page is its maximum; 40 pages is ~20k requests,
# far more than a day of current traffic, and the loop stops at the window edge anyway.
PER_PAGE = 500
MAX_PAGES = 40


# --------------------------------------------------------------------------- fetching

def _post_with_retry(url, headers, body, tries=4):
    """Xano's gateway 502s under heavy history reads (first --health run, 2026-09-27: 502
    after 52s on a 500-row all-endpoint page). Retry 5xx with backoff; raise on the last."""
    import time
    for attempt in range(tries):
        r = requests.post(url, headers=headers, json=body, timeout=120)
        if r.status_code < 500 or attempt == tries - 1:
            r.raise_for_status()
            return r
        time.sleep(5 * (attempt + 1))


def fetch_endpoint_history(token, hours, query_id, per_page=200):
    """Like fetch_history, but for ONE endpoint (server-side query_id filter), in smaller
    pages with retries. The daily health report only needs a handful of endpoints, and
    pulling all traffic with full headers is what made Xano's gateway time out."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    headers = {"Authorization": f"Bearer {token}"}
    rows, page = {}, 1
    while page <= 200:
        body = _post_with_retry(
            f"{META_BASE}/workspace/{WORKSPACE_ID}/request_history/search", headers,
            {"page": page, "per_page": per_page, "query_id": int(query_id),
             "sort": {"created_at": "desc"}},
        ).json()
        items = body.get("items", [])
        if not items:
            break
        for it in items:
            rows[it["id"]] = it
        if parse_ts(items[-1]) < cutoff or not body.get("nextPage"):
            break
        page += 1
    return [it for it in rows.values() if parse_ts(it) >= cutoff]


def fetch_history(token, hours):
    """Every request logged in the last `hours`, newest first.

    Paged until the window edge rather than filtered server-side: the history search
    endpoint takes an equality `search` object, and there is no documented range operator
    for created_at. Walking pages and stopping at the cutoff is slower but unambiguous —
    and guessing a filter that silently matches nothing would make this report a quiet
    all-clear, which is the one failure mode a health check must not have.
    """
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    headers = {"Authorization": f"Bearer {token}"}
    rows, page = {}, 1

    while page <= MAX_PAGES:
        r = requests.post(
            f"{META_BASE}/workspace/{WORKSPACE_ID}/request_history/search",
            headers=headers,
            json={"page": page, "per_page": PER_PAGE, "sort": {"created_at": "desc"}},
            timeout=120,
        )
        r.raise_for_status()
        body = r.json()
        items = body.get("items", [])
        if not items:
            break
        for it in items:
            rows[it["id"]] = it
        if parse_ts(items[-1]) < cutoff or not body.get("nextPage"):
            break
        page += 1

    return [it for it in rows.values() if parse_ts(it) >= cutoff]


def parse_ts(it):
    return dt.datetime.strptime(it["created_at"][:19], "%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- helpers

def hdr(it, key, which="request_headers"):
    prefix = key.lower() + ":"
    for h in it.get(which) or []:
        if h.lower().startswith(prefix):
            return h.split(":", 1)[1].strip()
    return ""


def client_ip(it):
    return hdr(it, "X-Real-Ip")


def had_token(it):
    """True if the caller sent an Authorization header. Xano redacts the value but keeps
    the header, so its presence is a reliable 'this client was signed in' signal."""
    return bool(hdr(it, "Authorization"))


def cache_state(it):
    """'1' hit, '0' miss, '' if the endpoint has no cache configured."""
    return hdr(it, "X-Query-Cache", "response_headers")


def pct(values, q):
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, int(round((len(v) - 1) * q)))]


def endpoint_path(it):
    """api:GROUP/path with numeric ids collapsed, for display."""
    m = re.match(r"https?://[^/]+/(api:[^/]+)/([^?]*)", it.get("uri") or "")
    if not m:
        return f"qid{it['query_id']}"
    # Built outside the f-string: Railway runs Python 3.11 (runtime.txt), which rejects a
    # backslash inside an f-string expression. 3.12+ accepts it, so a local compile passes.
    path = re.sub(r"/\d+(?=/|$)", "/{id}", m.group(2))
    return f"{m.group(1)}/{path}"


# --------------------------------------------------------------------------- analysis

def mark_races(items):
    """Tag each 401 as a race or a genuinely signed-out caller.

    Returns {request_id: True/False}, True meaning race. Grouped by IP first so the
    pairwise scan stays small.
    """
    by_ip = {}
    for it in items:
        ip = client_ip(it)
        if ip:
            by_ip.setdefault(ip, []).append(it)

    out = {}
    for it in items:
        if it["status"] != 401:
            continue
        ip = client_ip(it)
        if not ip:
            out[it["id"]] = False
            continue
        t = parse_ts(it)
        out[it["id"]] = any(
            o["status"] == 200 and o["id"] != it["id"] and had_token(o)
            and abs((parse_ts(o) - t).total_seconds()) <= RACE_WINDOW_S
            for o in by_ip[ip]
        )
    return out


def summarize(items, slo):
    """Fold raw history into per-endpoint stats plus global counters."""
    app_host = slo.get("app_host", "tulletogether.app")
    races = mark_races(items)

    # Only traffic that came from a browser on the real app counts toward the budgets.
    # The pipeline, the Streamlit dashboard and this script's own meta calls all share the
    # history, and none of them represent a user waiting on a page.
    app = [i for i in items if app_host in hdr(i, "Referer")]

    eps = {}
    for it in app:
        qid = str(it["query_id"])
        e = eps.setdefault(qid, {
            "qid": qid, "path": endpoint_path(it), "verb": it["verb"],
            "durations": [], "status": {}, "cache_hit": 0, "cache_miss": 0,
            "race_401": 0, "signedout_401": 0,
        })
        e["durations"].append(it["duration"])
        e["status"][str(it["status"])] = e["status"].get(str(it["status"]), 0) + 1
        c = cache_state(it)
        if c == "1":
            e["cache_hit"] += 1
        elif c == "0":
            e["cache_miss"] += 1
        if it["status"] == 401:
            e["race_401" if races.get(it["id"]) else "signedout_401"] += 1

    for e in eps.values():
        d = e["durations"]
        e["n"] = len(d)
        e["p50"], e["p95"], e["p99"] = pct(d, .5), pct(d, .95), pct(d, .99)
        e["max"] = max(d)
        cached = e["cache_hit"] + e["cache_miss"]
        e["cache_hit_pct"] = round(100 * e["cache_hit"] / cached, 1) if cached else None
        e["race_401_pct"] = round(100 * e["race_401"] / e["n"], 1) if e["n"] else 0.0
        del e["durations"]

    n_app = len(app)
    race_total = sum(1 for i in app if i["status"] == 401 and races.get(i["id"]))
    ips = {client_ip(i) for i in app if client_ip(i)}
    ips_401 = {client_ip(i) for i in app if i["status"] == 401 and client_ip(i)}

    return {
        "generated_at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ"),
        "window_hours": slo.get("window_hours", 24),
        "requests_total": len(items),
        "requests_app": n_app,
        "sessions": len(ips),
        "sessions_with_401": len(ips_401),
        "count_5xx": sum(1 for i in app if i["status"] >= 500),
        "race_401": race_total,
        "race_401_pct": round(100 * race_total / n_app, 2) if n_app else 0.0,
        "signedout_401": sum(1 for i in app
                             if i["status"] == 401 and not races.get(i["id"])),
        "p50": round(pct([i["duration"] for i in app], .5), 3) if app else 0,
        "p95": round(pct([i["duration"] for i in app], .95), 3) if app else 0,
        "p99": round(pct([i["duration"] for i in app], .99), 3) if app else 0,
        "endpoints": eps,
    }


def evaluate(summary, slo):
    """Compare the summary against the budgets. Returns a list of breach dicts."""
    g = slo.get("global", {})
    min_n = g.get("min_requests_for_latency", 20)
    breaches = []

    if summary["count_5xx"] > g.get("max_5xx_count", 0):
        breaches.append({
            "scope": "global", "metric": "5xx",
            "detail": f"{summary['count_5xx']} server errors "
                      f"(budget {g.get('max_5xx_count', 0)})",
        })

    if summary["race_401_pct"] > g.get("max_race_401_pct", 2.0):
        breaches.append({
            "scope": "global", "metric": "race-401",
            "detail": f"{summary['race_401_pct']}% of app requests were 401s from clients "
                      f"that were signed in (budget {g.get('max_race_401_pct')}%) — "
                      f"{summary['sessions_with_401']} of {summary['sessions']} sessions "
                      f"saw at least one 401",
        })

    for qid, rule in slo.get("endpoints", {}).items():
        e = summary["endpoints"].get(qid)
        name = rule.get("name", f"ep{qid}")
        if not e or e["n"] < min_n:
            continue   # too little traffic to judge; silence beats a noisy guess

        if "p95_s" in rule and e["p95"] > rule["p95_s"]:
            breaches.append({
                "scope": name, "metric": "p95",
                "detail": f"p95 {e['p95']:.2f}s over budget {rule['p95_s']}s "
                          f"(n={e['n']}, p50 {e['p50']:.2f}s, max {e['max']:.2f}s)",
            })

        if "min_cache_hit_pct" in rule and e["cache_hit_pct"] is not None \
                and e["cache_hit_pct"] < rule["min_cache_hit_pct"]:
            breaches.append({
                "scope": name, "metric": "cache",
                "detail": f"cache hit rate {e['cache_hit_pct']}% under budget "
                          f"{rule['min_cache_hit_pct']}% "
                          f"({e['cache_miss']} misses of {e['cache_hit'] + e['cache_miss']})",
            })

        if "max_race_401_pct" in rule and e["race_401_pct"] > rule["max_race_401_pct"]:
            breaches.append({
                "scope": name, "metric": "race-401",
                "detail": f"{e['race_401_pct']}% race-401s over budget "
                          f"{rule['max_race_401_pct']}% ({e['race_401']} of {e['n']} calls)",
            })

    return breaches


# --------------------------------------------------------------------------- output

def format_report(summary, breaches):
    L = [f"window: last {summary['window_hours']}h  ·  {summary['requests_app']} app "
         f"requests from ~{summary['sessions']} sessions "
         f"({summary['requests_total']} total incl. pipeline/admin)",
         f"latency: p50 {summary['p50']}s  p95 {summary['p95']}s  p99 {summary['p99']}s",
         f"errors:  {summary['count_5xx']} 5xx  ·  {summary['race_401']} race-401 "
         f"({summary['race_401_pct']}%)  ·  {summary['signedout_401']} signed-out 401",
         "",
         f"{'endpoint':<42} {'n':>5} {'p50':>7} {'p95':>7} {'cache':>7} {'race401':>8}",
         "-" * 80]

    for e in sorted(summary["endpoints"].values(), key=lambda x: -x["n"]):
        cache = "-" if e["cache_hit_pct"] is None else f"{e['cache_hit_pct']:.0f}%"
        L.append(f"{e['path'][:42]:<42} {e['n']:>5} {e['p50']:>7.3f} {e['p95']:>7.3f} "
                 f"{cache:>7} {e['race_401_pct']:>7.1f}%")

    L += ["", f"{len(breaches)} budget breach(es)" if breaches else "all budgets met."]
    for b in breaches:
        L.append(f"  [{b['metric']}] {b['scope']}: {b['detail']}")
    return "\n".join(L)


def post_slack(summary, breaches, always=False):
    url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not url or (not breaches and not always):
        return

    if breaches:
        head = (f":rotating_light: *Tulle performance — {len(breaches)} budget breach(es)* "
                f"(last {summary['window_hours']}h)")
    else:
        head = f":white_check_mark: *Tulle performance — all budgets met* (last {summary['window_hours']}h)"

    lines = [head,
             f"_{summary['requests_app']} app requests · ~{summary['sessions']} sessions · "
             f"p50 {summary['p50']}s · p95 {summary['p95']}s · "
             f"{summary['count_5xx']} 5xx · {summary['race_401_pct']}% race-401_", ""]
    lines += [f"• *{b['scope']}* — {b['detail']}" for b in breaches]

    if always and not breaches:
        busiest = sorted(summary["endpoints"].values(), key=lambda x: -x["n"])[:5]
        lines += [f"• `{e['path']}` n={e['n']} p95 {e['p95']:.2f}s" for e in busiest]
    if breaches:
        lines += ["", "_Budgets live in `perf_slo.json` — raise one only as a reviewed commit._"]

    try:
        requests.post(url, json={"text": "\n".join(lines)}, timeout=20)
    except Exception as e:
        print(f"slack post failed: {e}", file=sys.stderr)


def run(hours=None):
    """Fetch, summarize and evaluate. Used by main() and by the dashboard tab."""
    token = os.environ.get("XANO_METADATA_TOKEN", "")
    if not token:
        raise RuntimeError("XANO_METADATA_TOKEN is not set.")
    with open(SLO_PATH, encoding="utf-8") as f:
        slo = json.load(f)
    if hours:
        slo["window_hours"] = hours
    items = fetch_history(token, slo["window_hours"])
    summary = summarize(items, slo)
    return summary, evaluate(summary, slo), slo


def render_perf_digest(default_hours=24):
    """Collapsed dashboard panel. Sibling of render_endpoint_health: that one answers 'can
    we still reach our dependencies', this one answers 'how did it feel to use the site'.

    Streamlit and pandas are imported here rather than at module scope so the cron
    container never loads them — this file's main path is a headless job.
    """
    import pandas as pd_
    import streamlit as st

    with st.expander("📊 Performance digest — what real users actually experienced", expanded=False):
        st.caption(
            "Read from Xano's own request history, scoped to browser traffic on the live "
            "app. Budgets live in `perf_slo.json`. The daily endpoint health report "
            "(`perf_digest.py --health`) runs on the `health-report-cron` service at 13:32 UTC "
            "and posts to #tulle-users. Needs XANO_METADATA_TOKEN on this service."
        )
        hours = st.slider("Window (hours)", 1, 72, default_hours, key="pd_hours")

        if st.button("▶ Run performance digest", key="pd_run"):
            with st.spinner("Reading request history…"):
                try:
                    st.session_state["pd_result"] = run(hours=hours)
                except Exception as e:
                    st.session_state["pd_result"] = e

        res = st.session_state.get("pd_result")
        if res is None:
            st.info("Not run yet.")
            return
        if isinstance(res, Exception):
            st.error(f"Digest could not run: {res}")
            return

        summary, breaches, _slo = res
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("App requests", f"{summary['requests_app']:,}")
        c2.metric("p95", f"{summary['p95']}s")
        c3.metric("5xx", summary["count_5xx"])
        c4.metric("Race 401s", f"{summary['race_401_pct']}%")

        if breaches:
            st.error(f"{len(breaches)} budget breach(es).")
            for b in breaches:
                st.markdown(f"- **{b['scope']}** · `{b['metric']}` — {b['detail']}")
        else:
            st.success("All budgets met.")

        st.caption(
            f"{summary['sessions_with_401']} of {summary['sessions']} sessions saw at least "
            f"one 401 · {summary['race_401']} were from clients that were signed in "
            f"(pre-auth race), {summary['signedout_401']} from signed-out visitors."
        )

        rows = [{
            "Endpoint": e["path"], "n": e["n"],
            "p50": round(e["p50"], 3), "p95": round(e["p95"], 3), "p99": round(e["p99"], 3),
            "max": round(e["max"], 3),
            "cache %": "-" if e["cache_hit_pct"] is None else e["cache_hit_pct"],
            "race 401 %": e["race_401_pct"],
        } for e in sorted(summary["endpoints"].values(), key=lambda x: -x["n"])]
        st.dataframe(pd_.DataFrame(rows), use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------- daily health

# Inputs that carry no user intent when empty. A query's "shape" is the set of params that
# are NOT one of these — so "Venue grid, Florida" and "Venue grid, Texas" share a shape
# (Category_Input,State_Input,states) while "Venue grid + keyword search" does not.
_EMPTY = ("", None, "0", "false", "null", [], {})
_PAGING = ("page", "page_size", "cache_v")

# A request at or above this many seconds counts as "slow" for the slow-time ranking. 3s is
# roughly where a grid load stops feeling like a click and starts feeling like a wait.
SLOW_S = 3.0

# The warmer's api.request sets no timeout, and the two alerting runs on 2026-09-24/25 each
# cost ~12s extra per failed bucket — the signature of a client-side timeout near 10s. A
# warmer bucket slower than this on the SERVER is one the warmer probably gave up on.
WARMER_CLIENT_TIMEOUT_S = 10.0
WARMER_BUCKETS = 15


def query_shape(it):
    inp = it.get("input") or {}
    keys = [k for k, v in inp.items() if k not in _PAGING and v not in _EMPTY]
    return ",".join(sorted(keys)) or "(defaults only)"


def query_label(it):
    """Human-readable version of one request's intent, for the 'most common' list."""
    inp = it.get("input") or {}
    parts = [f"{k}={v if not isinstance(v, list) else '|'.join(map(str, v))}"
             for k, v in sorted(inp.items()) if k not in _PAGING and v not in _EMPTY]
    return " ".join(parts) or "(defaults only)"


def failure_reason(it):
    """Why a request failed, in words someone can act on. None if it did not fail."""
    s = it["status"]
    if s < 400:
        return None
    if s == 401:
        # Xano redacts the token but keeps the header, so presence is reliable. No header on
        # a CORS request is the preflight / signed-out class (ep119 2026-09-16); a header
        # that was rejected is an expired or invalid token.
        return ("401 no token (signed-out or CORS preflight)" if not had_token(it)
                else "401 token rejected (expired/invalid)")
    return {
        400: "400 bad input", 403: "403 forbidden", 404: "404 not found",
        429: "429 rate limited", 500: "500 server error (endpoint threw)",
        502: "502 bad gateway (Xano upstream)", 503: "503 unavailable (Xano overloaded)",
        504: "504 gateway timeout",
    }.get(s, f"{s} other")


def endpoint_health(items, qid, app_host):
    """Deep stats for one endpoint over the window. `items` is ALL history rows."""
    rows = [i for i in items if str(i["query_id"]) == str(qid)]
    app = [i for i in rows if app_host in hdr(i, "Referer")]
    if not app:
        return {"qid": str(qid), "n": 0}

    fails = [i for i in app if i["status"] >= 400]
    reasons = {}
    for i in fails:
        r = failure_reason(i)
        reasons[r] = reasons.get(r, 0) + 1

    ok = [i["duration"] for i in app if i["status"] < 400]
    hits = [i["duration"] for i in app if cache_state(i) == "1"]
    misses = [i["duration"] for i in app if cache_state(i) == "0"]

    by_shape, by_label = {}, {}
    for i in app:
        by_shape.setdefault(query_shape(i), []).append(i)
        by_label.setdefault(query_label(i), []).append(i)

    slow_total = sum(i["duration"] - SLOW_S for i in app if i["duration"] >= SLOW_S) or 1e-9

    def shape_row(name, its):
        d = [i["duration"] for i in its]
        slow = [x for x in d if x >= SLOW_S]
        cached = [i for i in its if cache_state(i) in ("0", "1")]
        return {
            "shape": name, "n": len(its),
            "share_pct": round(100 * len(its) / len(app), 1),
            "p50": round(pct(d, .5), 2), "p95": round(pct(d, .95), 2),
            "slow_n": len(slow),
            # Seconds ABOVE the slow threshold, not total seconds: a shape called 1,500
            # times at 0.1s is not what makes the site feel slow, and ranking by raw total
            # would put it on top.
            "slow_excess_s": round(sum(x - SLOW_S for x in slow), 1),
            "slow_share_pct": round(100 * sum(x - SLOW_S for x in slow) / slow_total, 1),
            "miss_pct": (round(100 * sum(1 for i in cached if cache_state(i) == "0")
                               / len(cached)) if cached else None),
        }

    shapes = [shape_row(k, v) for k, v in by_shape.items()]
    worst = max(app, key=lambda i: i["duration"])

    return {
        "qid": str(qid), "path": endpoint_path(app[0]),
        "n": len(app), "n_all_sources": len(rows),
        "failures": len(fails),
        "fail_pct": round(100 * len(fails) / len(app), 2),
        "fail_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])),
        # Sent with no Authorization header. On a public endpoint these still succeed, so
        # this is the number that says whether requiring auth would empty people's results.
        "tokenless": sum(1 for i in app if not had_token(i)),
        "mean": round(sum(ok) / len(ok), 3) if ok else 0,
        "p50": round(pct(ok, .5), 3), "p95": round(pct(ok, .95), 3),
        "p99": round(pct(ok, .99), 3), "max": round(max(ok), 2) if ok else 0,
        "slow_n": sum(1 for i in app if i["duration"] >= SLOW_S),
        "cache_hit_pct": (round(100 * len(hits) / (len(hits) + len(misses)), 1)
                          if hits or misses else None),
        "miss_p50": round(pct(misses, .5), 2), "miss_p95": round(pct(misses, .95), 2),
        "top_queries": sorted(
            [{"query": k, "n": len(v), "p50": round(pct([i["duration"] for i in v], .5), 2)}
             for k, v in by_label.items()], key=lambda x: -x["n"])[:5],
        "top_shapes": sorted(shapes, key=lambda x: -x["n"])[:5],
        "slow_drivers": [s for s in sorted(shapes, key=lambda x: -x["slow_excess_s"])
                         if s["slow_n"]][:5],
        "slowest": {"s": round(worst["duration"], 2), "query": query_label(worst),
                    "at": worst["created_at"][:16]},
    }


def warmer_audit(items):
    """Server-side view of task 8's runs, from ep119 rows with no Referer and no UA.

    The warmer only knows a count and the LAST bucket's status, which is why its alert said
    'last status 200' while buckets were failing. This sees every bucket it sent.
    """
    rows = [i for i in items if str(i["query_id"]) == "119"
            and not hdr(i, "Referer") and not hdr(i, "User-Agent")]
    runs = {}
    for i in rows:
        runs.setdefault(i["created_at"][:13], []).append(i)   # one run per hour-bucket
    out = []
    for hour, its in sorted(runs.items()):
        out.append({
            "run": hour, "sent": len(its),
            "non_2xx": sum(1 for i in its if not 200 <= i["status"] < 300),
            "over_client_timeout": sum(1 for i in its
                                       if i["duration"] >= WARMER_CLIENT_TIMEOUT_S),
            "slowest_s": round(max(i["duration"] for i in its), 1),
        })
    bad = [r for r in out if r["sent"] < WARMER_BUCKETS or r["non_2xx"]
           or r["over_client_timeout"]]
    return {"runs": len(out), "bad_runs": bad,
            "bucket_p50": round(pct([i["duration"] for i in rows], .5), 2),
            "bucket_max": round(max((i["duration"] for i in rows), default=0), 1)}


def meta_request(method, path, token, **kw):
    return requests.request(method, f"{META_BASE}/workspace/{WORKSPACE_ID}{path}",
                            headers={"Authorization": f"Bearer {token}"}, timeout=60, **kw)


def load_history(token, table_id, days=7):
    """Trailing rollup rows from the history table, newest first. [] if unavailable."""
    try:
        r = meta_request("GET", f"/table/{table_id}/content", token,
                         params={"page": 1, "per_page": 200, "sort": "id", "order": "desc"})
        r.raise_for_status()
        rows = r.json().get("items", [])
    except Exception:
        return None
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    return [x for x in rows if (x.get("report_date") or "") >= cutoff]


def save_history(token, table_id, report_date, h):
    row = {
        "report_date": report_date, "query_id": int(h["qid"]), "path": h.get("path", ""),
        "requests": h["n"], "failures": h.get("failures", 0),
        "fail_pct": h.get("fail_pct", 0), "fail_reasons": h.get("fail_reasons", {}),
        "mean_s": h.get("mean", 0), "p50_s": h.get("p50", 0), "p95_s": h.get("p95", 0),
        "p99_s": h.get("p99", 0), "max_s": h.get("max", 0),
        "cache_hit_pct": h.get("cache_hit_pct") or 0, "tokenless": h.get("tokenless", 0),
        "top_queries": h.get("top_queries", []), "slow_drivers": h.get("slow_drivers", []),
    }
    r = meta_request("POST", f"/table/{table_id}/content", token, json=row)
    r.raise_for_status()


def trend(h, past):
    """' (7d avg 1.2% / p95 3.1s)' from stored rows for this endpoint, or ''."""
    mine = [x for x in (past or []) if str(x.get("query_id")) == h["qid"]]
    if not mine:
        return ""
    fp = sum(x.get("fail_pct", 0) for x in mine) / len(mine)
    p95 = sum(x.get("p95_s", 0) for x in mine) / len(mine)
    return f" _(prior {len(mine)}d avg: {fp:.1f}% fail, p95 {p95:.2f}s)_"


def session_audit(items, app_host):
    """Expired-session signal: auth/me (qid 3) rejecting a token the browser still holds.

    Before 2026-09-27 every token lasted 24h inside a 1-year cookie, so a user returning the
    next day presented a dead token, got 401, and WeWeb's fetchUser catch logged them out.
    This count should fall sharply once the 14-day tokens are the ones in circulation.
    """
    app = [i for i in items if app_host in hdr(i, "Referer")]
    me = [i for i in app if str(i["query_id"]) == "3" and i["verb"] == "GET"]
    return {
        "me_calls": len(me),
        "expired": sum(1 for i in me if i["status"] == 401 and had_token(i)),
        "logins": sum(1 for i in app if str(i["query_id"]) in ("1", "2", "5", "6", "7", "18")
                      and i["status"] == 200),
    }


def format_health(health, warm, coverage_h, window_h, history_note, sessions=None):
    L = [f":stethoscope: *Endpoint health — last {window_h}h*"]
    if coverage_h < window_h - 0.5:
        L.append(f":warning: history only covered {coverage_h:.1f}h of the {window_h}h window "
                 f"— counts below are for that span.")
    for h in health:
        if not h["n"]:
            L += ["", f"*ep{h['qid']}* — no app traffic in window"]
            continue
        L += ["", f"*ep{h['qid']} `{h['path'].split('/')[-1]}`* — {h['n']:,} app requests"
                  f"{h.get('_trend', '')}"]
        if h["failures"]:
            why = ", ".join(f"{n}× {r}" for r, n in h["fail_reasons"].items())
            L.append(f"• *Failures: {h['failures']} ({h['fail_pct']}%)* — {why}")
        else:
            L.append("• Failures: 0")
        L.append(f"• Sent with no login token: {h['tokenless']} "
                 f"({100 * h['tokenless'] / h['n']:.1f}%)")
        cache = ("" if h["cache_hit_pct"] is None else
                 f" · cache hit {h['cache_hit_pct']:.0f}% (miss p50 {h['miss_p50']}s,"
                 f" p95 {h['miss_p95']}s)")
        L.append(f"• Response: avg {h['mean']}s · p50 {h['p50']}s · p95 {h['p95']}s · "
                 f"p99 {h['p99']}s · max {h['max']}s{cache}")
        L.append(f"• Slowest: {h['slowest']['s']}s at {h['slowest']['at']} — "
                 f"`{h['slowest']['query'][:110]}`")
        L.append("• Most common queries:")
        L += [f"    {q['n']:>4}×  `{q['query'][:100]}`  (p50 {q['p50']}s)"
              for q in h["top_queries"]]
        if h["slow_drivers"]:
            L.append(f"• What makes it slow ({h['slow_n']} requests ≥{SLOW_S:.0f}s), "
                     f"by share of slow time:")
            L += [f"    {s['slow_share_pct']:>4.0f}%  `{s['shape']}` — {s['slow_n']} slow of "
                  f"{s['n']}, p95 {s['p95']}s"
                  + (f", {s['miss_pct']}% cache miss" if s["miss_pct"] is not None else "")
                  for s in h["slow_drivers"][:3]]
    if sessions is not None:
        L += ["", f"*Sessions* — {sessions['logins']} logins · {sessions['me_calls']} auth/me "
                  f"calls · *{sessions['expired']} auth/me 401s carrying a token* (expired "
                  f"sessions: each one logs a user out). Tokens last 14 days since 2026-09-27."]
    if warm is not None:
        L += ["", f"*ep119 cache warmer (task 8)* — {warm['runs']} runs seen, bucket p50 "
                  f"{warm['bucket_p50']}s, max {warm['bucket_max']}s"]
        if warm["bad_runs"]:
            for r in warm["bad_runs"]:
                L.append(f"• {r['run']}:00 UTC — sent {r['sent']}/{WARMER_BUCKETS}, "
                         f"{r['non_2xx']} non-2xx, {r['over_client_timeout']} took "
                         f"≥{WARMER_CLIENT_TIMEOUT_S:.0f}s (warmer likely timed out)")
        else:
            L.append(f"• every run sent all {WARMER_BUCKETS} buckets, all 2xx, none near "
                     f"the client timeout")
    L += ["", f"_{history_note}_"]
    return "\n".join(L)


def run_health():
    token = os.environ.get("XANO_METADATA_TOKEN", "")
    if not token:
        raise RuntimeError("XANO_METADATA_TOKEN is not set.")
    with open(SLO_PATH, encoding="utf-8") as f:
        slo = json.load(f)
    window_h = slo.get("window_hours", 24)
    # Only the endpoints this report reads: the watched ones, auth/me (3) and the six
    # login/signup endpoints for the session audit.
    qids = list(dict.fromkeys(slo.get("health_watch", ["119", "121"])
                              + ["3", "1", "2", "5", "6", "7", "18"]))
    items = []
    for q in qids:
        items += fetch_endpoint_history(token, window_h, q)
    return build_health(items, slo, token=token)


def build_health(items, slo, token=None):
    """Pure-ish core so it can be tested against a saved history dump (token=None)."""
    window_h = slo.get("window_hours", 24)
    app_host = slo.get("app_host", "tulletogether.app")
    watch = slo.get("health_watch", ["119", "121"])
    oldest = min((parse_ts(i) for i in items), default=dt.datetime.utcnow())
    newest = max((parse_ts(i) for i in items), default=dt.datetime.utcnow())
    coverage_h = (newest - oldest).total_seconds() / 3600

    health = [endpoint_health(items, q, app_host) for q in watch]
    warm = warmer_audit(items) if "119" in watch else None

    table_id = os.environ.get("HEALTH_TABLE_ID", "")
    if not token or not table_id:
        note = ("History NOT recorded — HEALTH_TABLE_ID is unset, so no trend beyond this "
                "24h is being kept.")
    else:
        past = load_history(token, table_id)
        for h in health:
            if h["n"]:
                h["_trend"] = trend(h, past)
        errs = []
        today = newest.strftime("%Y-%m-%d")
        for h in health:
            if not h["n"]:
                continue
            try:
                save_history(token, table_id, today, h)
            except Exception as e:
                errs.append(f"ep{h['qid']}: {e}")
        note = (f"Recorded to Xano table {table_id} for trend history."
                if not errs else
                f"History write FAILED ({'; '.join(errs)[:200]}) — today is not recorded.")
        if past is None:
            note += " Could not read prior days."

    sessions = session_audit(items, app_host)
    return health, warm, format_health(health, warm, coverage_h, window_h, note, sessions)


def main():
    if "--health" in sys.argv:
        try:
            health, warm, text = run_health()
        except Exception as e:
            print(f"health report failed: {e}", file=sys.stderr)
            text = f":warning: *Endpoint health report could not run*: {e}"
            health = None
        print(text)
        url = (os.environ.get("HEALTH_SLACK_WEBHOOK_URL")
               or os.environ.get("SLACK_WEBHOOK_URL", ""))
        if url:
            try:
                requests.post(url, json={"text": text}, timeout=20)
            except Exception as e:
                print(f"slack post failed: {e}", file=sys.stderr)
        return 2 if health is None else 0

    try:
        summary, breaches, _ = run()
    except Exception as e:
        print(f"perf digest failed: {e}", file=sys.stderr)
        return 2

    if "--json" in sys.argv:
        print(json.dumps({"summary": summary, "breaches": breaches}, indent=2))
        return 0

    print(format_report(summary, breaches))

    # One cron entry cannot vary its arguments, so the daily summary is decided here: the
    # run that lands on PERF_DIGEST_HOUR posts whether or not anything breached. Every
    # other run stays silent unless there is something to say. Set the hour to -1 to turn
    # the daily line off and keep breach-only alerting.
    daily_hour = int(os.environ.get("PERF_DIGEST_HOUR", "8"))
    is_daily = "--digest" in sys.argv or dt.datetime.utcnow().hour == daily_hour
    post_slack(summary, breaches, always=is_daily)
    return 1 if breaches else 0


if __name__ == "__main__":
    sys.exit(main())
