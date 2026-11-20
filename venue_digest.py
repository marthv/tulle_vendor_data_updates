"""Weekly venue lookup digest (2026-11-18) - reads Xano table venue_lookup_log (written by rec_service/venue_log.py when
REC_VENUE_LOG=1 on rec-engine) and says, for the last N days (default 7):

  (a) MISSING   not_found lookups grouped by normalized name + state, ranked by distinct couples - venues to add
  (b) REVIEW    'found' / 'best' answers whose match score was low - the venue we showed may not be the one asked for
  (c) MISSED    venues we HAVE but the couple did not get: 'wrong' (they retyped within 2 min), 'dym' (we had to ask
                "did you mean"), and not_found whose nearest candidate scored high enough to be the same venue

Output: a compact Slack message to VENUE_DIGEST_SLACK_WEBHOOK (absent = printed only) and a CSV (stdout, or --csv FILE).

Usage:
  python venue_digest.py                  # read Xano, post (if the webhook is set), CSV to stdout
  python venue_digest.py --dry-run        # read Xano, print the Slack text, never post
  python venue_digest.py --input rows.json --dry-run    # offline: rows from a file (a list of table rows)
  python venue_digest.py --csv digest.csv --days 14

Variables: XANO_METADATA_TOKEN (read-only use), VENUE_LOG_TABLE_ID (the table's id; unset = nothing to read, exit 0),
VENUE_DIGEST_SLACK_WEBHOOK, VENUE_DIGEST_EXCLUDE_USER_IDS (comma-separated; default: the team's internal accounts).
daily_brief.py runs weekly() on Mondays when VENUE_LOG_TABLE_ID is set. Never raises out of weekly()."""
import argparse
import csv
import datetime as dt
import io
import json
import os
import sys
import time
from collections import defaultdict

import requests

META = os.environ.get("XANO_META_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta") + "/workspace/1"
DAY_MS = 86400 * 1000
REVIEW_BELOW = 0.8          # a found / best answer under this score is worth a look (the lookup's HIGH is 0.88)
MISSED_AT = 0.6             # a not_found whose nearest candidate scored this high may be a venue we have
TOP = 10
# Team / test accounts (memory: exclude internal accounts from real-user numbers).
# Des's + Kate's test accounts (owner 2026-11-20). Not excluded: real couples.
DEFAULT_EXCLUDE = "14998,30614,32636,31619,118,5622,21414,21797,21800,21801,21998,22002,22007,22118,22548,22549,22581,22694,24403,26339,26647,26649,26726,27701,29112,29564,29747,31971,32114,33154,33155,33602,34096,35020,35212,36553,35527"


def _excluded():
    out = set()
    for x in os.environ.get("VENUE_DIGEST_EXCLUDE_USER_IDS", DEFAULT_EXCLUDE).split(","):
        try:
            out.add(int(x.strip()))
        except ValueError:
            pass
    return out


def _ms(v):
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def fetch(token, table_id, since_ms):
    """Rows created since since_ms, through the metadata API (read only). Re-filtered in code."""
    rows, page = [], 1
    while page <= 40:
        r = requests.post("%s/table/%d/content/search" % (META, int(table_id)), timeout=30,
                          headers={"Authorization": "Bearer " + token},
                          json={"search": [{"created_at|>": since_ms}], "page": page, "per_page": 500})
        r.raise_for_status()
        d = r.json() or {}
        rows += d.get("items") or []
        if not d.get("nextPage"):
            break
        page += 1
    return [x for x in rows if _ms(x.get("created_at")) >= since_ms]


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def digest(rows, exclude=None):
    """Pure: rows -> {"missing", "review", "missed", "totals"}."""
    exclude = _excluded() if exclude is None else exclude
    rows = [r for r in rows if int(r.get("user_id") or 0) not in exclude]
    missing = defaultdict(lambda: {"users": set(), "count": 0, "examples": [], "nearest": ""})
    review, missed = [], defaultdict(lambda: {"users": set(), "count": 0, "outcomes": set(), "score": 0.0,
                                              "example": ""})
    outcomes = defaultdict(int)
    for r in rows:
        oc = r.get("outcome") or ""
        outcomes[oc] += 1
        uid = int(r.get("user_id") or 0)
        norm = (r.get("normalized") or r.get("typed") or "").strip().lower()
        score = _f(r.get("best_score"))
        if oc == "not_found":
            m = missing[(norm, r.get("state_hint") or "")]
            m["users"].add(uid)
            m["count"] += 1
            if len(m["examples"]) < 2 and r.get("typed") not in m["examples"]:
                m["examples"].append(r.get("typed") or "")
            if score >= MISSED_AT and r.get("best_name"):
                m["nearest"] = r.get("best_name")
        if oc in ("found", "best") and r.get("best_name") and score and score < REVIEW_BELOW:
            review.append({"typed": r.get("typed") or "", "best_name": r.get("best_name"), "score": round(score, 3),
                           "source": r.get("source") or "", "user_id": uid})
        if (oc in ("wrong", "dym") or (oc == "not_found" and score >= MISSED_AT)) and r.get("best_name"):
            k = (norm, r.get("best_name"))
            x = missed[k]
            x["users"].add(uid)
            x["count"] += 1
            x["outcomes"].add(r.get("answered_as") and "%s->wrong" % r["answered_as"] or oc)
            x["score"] = max(x["score"], score)
            x["example"] = x["example"] or (r.get("typed") or "")
    miss = sorted(({"name": k[0], "state": k[1], "couples": len(v["users"]), "count": v["count"],
                    "examples": v["examples"], "nearest": v["nearest"]} for k, v in missing.items()),
                  key=lambda x: (-x["couples"], -x["count"], x["name"]))
    review.sort(key=lambda x: x["score"])
    mis = sorted(({"typed": k[0], "best_name": k[1], "couples": len(v["users"]), "count": v["count"],
                   "outcomes": sorted(v["outcomes"]), "score": round(v["score"], 3), "example": v["example"]}
                  for k, v in missed.items()), key=lambda x: (-x["couples"], -x["score"]))
    return {"missing": miss, "review": review, "missed": mis,
            "totals": {"rows": len(rows), "couples": len({int(r.get("user_id") or 0) for r in rows}),
                       "outcomes": dict(outcomes)}}


def slack_text(d, days=7):
    t = d["totals"]
    oc = t["outcomes"]
    lines = ["*Venue lookups - last %d days* · %d lookups from %d couples · found %d · best %d · choices %d · "
             "did-you-mean %d · not found %d · wrong %d" % (
                 days, t["rows"], t["couples"], oc.get("found", 0), oc.get("best", 0), oc.get("choices", 0),
                 oc.get("dym", 0), oc.get("not_found", 0), oc.get("wrong", 0))]
    if d["missing"]:
        lines.append("*Missing - add these* (by couples)")
        for m in d["missing"][:TOP]:
            lines.append("• %s%s - %d couple%s%s" % (m["name"] or "?", (" (%s)" % m["state"]) if m["state"] else "",
                                                    m["couples"], "" if m["couples"] == 1 else "s",
                                                    (" · nearest we have: %s" % m["nearest"]) if m["nearest"] else ""))
    if d["missed"]:
        lines.append("*We have it but they missed it*")
        for m in d["missed"][:TOP]:
            lines.append("• “%s” -> %s (%s, score %.2f, %d couple%s)" % (
                m["typed"], m["best_name"], "/".join(m["outcomes"]), m["score"], m["couples"],
                "" if m["couples"] == 1 else "s"))
    if d["review"]:
        lines.append("*Low-score matches to review*")
        for m in d["review"][:TOP]:
            lines.append("• “%s” -> %s (score %.2f, %s)" % (m["typed"], m["best_name"], m["score"], m["source"]))
    if len(lines) == 1:
        lines.append("_Nothing to act on._")
    return "\n".join(lines)


def csv_text(d):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "name_or_typed", "state", "best_name", "score", "couples", "count", "detail"])
    for m in d["missing"]:
        w.writerow(["missing", m["name"], m["state"], m["nearest"], "", m["couples"], m["count"], " | ".join(m["examples"])])
    for m in d["missed"]:
        w.writerow(["missed", m["typed"], "", m["best_name"], m["score"], m["couples"], m["count"], "/".join(m["outcomes"])])
    for m in d["review"]:
        w.writerow(["review", m["typed"], "", m["best_name"], m["score"], 1, 1, m["source"]])
    return buf.getvalue()


def post(text, webhook=None):
    webhook = webhook if webhook is not None else os.environ.get("VENUE_DIGEST_SLACK_WEBHOOK", "")
    # 2026-11-18 (owner: digest goes to #growth-strategy): post as Tulle Bot when a bot token + channel are set,
    # so no separate incoming webhook is needed. VENUE_DIGEST_SLACK_TOKEN / VENUE_DIGEST_CHANNEL (channel id).
    tok, ch = os.environ.get("VENUE_DIGEST_SLACK_TOKEN", ""), os.environ.get("VENUE_DIGEST_CHANNEL", "")
    if not webhook and tok and ch:
        r = requests.post("https://slack.com/api/chat.postMessage", timeout=15,
                          headers={"Authorization": "Bearer " + tok},
                          json={"channel": ch, "text": text, "unfurl_links": False})
        ok = r.status_code == 200 and bool(r.json().get("ok"))
        if not ok:
            print("venue digest slack failed:", r.status_code, r.text[:120])
        return ok
    if not webhook:
        print(text)
        return False
    r = requests.post(webhook, json={"text": text}, timeout=15)
    ok = r.status_code == 200
    if not ok:
        print("venue digest slack failed:", r.status_code, r.text[:120])
    return ok


def weekly(token, now_ms=None, days=7, dry_run=False):
    """daily_brief's Monday hook: read, post. Returns a one-line status for the brief. Never raises."""
    try:
        table = int(os.environ.get("VENUE_LOG_TABLE_ID", "0") or 0)
        if not table:
            return ""
        now_ms = now_ms or int(time.time() * 1000)
        d = digest(fetch(token, table, now_ms - days * DAY_MS))
        text = slack_text(d, days)
        if dry_run:
            print(text)
        else:
            post(text)
        return "Venue lookups digest: %d lookups, %d missing names" % (d["totals"]["rows"], len(d["missing"]))
    except Exception as e:
        return ":warning: _Venue lookups digest failed: %s_" % str(e)[:120]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true", help="print the Slack text, never post")
    ap.add_argument("--input", help="rows from a JSON file instead of Xano (offline)")
    ap.add_argument("--csv", help="write the CSV here instead of stdout")
    a = ap.parse_args(argv)
    if a.input:
        with open(a.input, encoding="utf-8") as f:
            rows = json.load(f)
        if isinstance(rows, dict):
            rows = rows.get("items") or []
    else:
        token = os.environ.get("XANO_METADATA_TOKEN", "")
        table = int(os.environ.get("VENUE_LOG_TABLE_ID", "0") or 0)
        if not token or not table:
            print("venue_digest: XANO_METADATA_TOKEN and VENUE_LOG_TABLE_ID are required (nothing read)")
            return 0
        rows = fetch(token, table, int(time.time() * 1000) - a.days * DAY_MS)
    d = digest(rows)
    text = slack_text(d, a.days)
    if a.dry_run:
        print(text)
    else:
        post(text)
    out = csv_text(d)
    if a.csv:
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            f.write(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
