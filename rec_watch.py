"""Recommendation engine watch - one short section of the daily health DM (Oct 2026).

    python rec_watch.py              # print the section (needs XANO_METADATA_TOKEN)

Reads Xano table 79 rec_usage (one row per AI request, written by rec_service). Reports the last
24h: users, opening vs refine requests, spend vs the $/day cap, free-limit hits (= paywall
shown), errors, and median latency. Silent ("no usage yet") until the page is live.

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
    lat = [r.get("latency_ms") or 0 for r in ok if r.get("latency_ms")]
    light = ":red_circle:" if errors > max(2, 0.1 * len(rows)) or capped else ":large_green_circle:"
    lines = [
        "%s %s - %d users, %d opening + %d refine, $%.2f spent ($%.0f/day cap), $%.3f per user"
        % (light, head, len(users), opening, refine, spend, CAP, spend / max(len(users), 1)),
        "Paywall shown (free limit hit): %d · errors: %d · blocked by cap/kill switch: %d · median %.1fs"
        % (paywall, errors, capped, (st.median(lat) / 1000) if lat else 0),
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    tok = os.environ.get("XANO_METADATA_TOKEN")
    if not tok:
        sys.exit("XANO_METADATA_TOKEN not set")
    print(build(tok))
