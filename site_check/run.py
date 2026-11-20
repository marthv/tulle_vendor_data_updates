"""Daily QA of the tulletogether.com marketing site -> Slack #growth-strategy (only when something changes).

What it does each run:
  1. Reads the sitemap (+ key pages from config.json), fetches every page politely (one request at a time,
     request_delay_s between requests to the same host), records status + TTFB.
  2. Runs the page checks in checks.py (couples claim, prices vs the app, placeholders, GA4/GTM ids, Glide links,
     app links present, homepage mentions Planner / Price Report).
  3. Checks every internal link and every tulletogether.app link (status, redirect keeps the path, UTMs).
     Outbound links to other sites are checked on the weekly run only.
  4. Diffs the findings against the last run (STATE_DIR/state.json) and posts ONE Slack message if anything
     opened, resolved, or pages were added/removed. Unchanged open failures are not re-posted daily; they are in
     the weekly summary (Mondays by default).

Usage:
  python run.py                       # DRY_RUN defaults to 1: prints the Slack text, posts nothing, state not saved
  python run.py --save-state          # dry run that also saves state (to test the diff locally)
  python run.py --weekly              # force the weekly summary + outbound link check
  python run.py --max-pages 20        # quick smoke run
  DRY_RUN=0 python run.py             # live: posts with SITE_CHECK_SLACK_TOKEN to SITE_CHECK_SLACK_CHANNEL

Env: DRY_RUN (default 1), SITE_CHECK_SLACK_TOKEN (xoxb-, chat:write), SITE_CHECK_SLACK_CHANNEL (channel id),
     STATE_DIR (default ./state; /data on Railway), SITE_CHECK_CONFIG (default config.json next to this file),
     WEEKLY_WEEKDAY (0=Monday default, -1 = never), SITE_CHECK_NOTIFY (changes [default] | failures = post every run
     that has any open failure).

Exit codes: 0 ran (findings or not); 2 could not run (home page / sitemap unreachable, or the Slack post failed).
On exit 2 the state is NOT advanced, so the next run re-reports.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests

import checks

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config(path=None):
    with open(path or os.environ.get("SITE_CHECK_CONFIG") or os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- HTTP

class Resp:
    def __init__(self, status, url, text="", headers=None, elapsed=0.0, error=""):
        self.status, self.url, self.text, self.headers, self.elapsed, self.error = status, url, text, headers or {}, elapsed, error


class Fetcher:
    """One request at a time, delay_s between requests to the same host. elapsed = time to response headers (TTFB)."""

    def __init__(self, cfg):
        self.delay = cfg["request_delay_s"]
        self.timeout = cfg["timeout_s"]
        self.s = requests.Session()
        self.s.headers["User-Agent"] = cfg["user_agent"]
        self.last = {}
        self.count = 0
        self.retry_waits = (3, 15)

    def _wait(self, url):
        h = urlparse(url).hostname or ""
        gap = time.monotonic() - self.last.get(h, 0)
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self.last[h] = time.monotonic()

    def get(self, url, head=False):
        r = self._get_once(url, head)
        for wait in self.retry_waits:                      # DNS blips happen (seen twice in local live runs)
            if r.status != 0:
                break
            print("  retry in %ds: %s (%s)" % (wait, url, r.error), file=sys.stderr)
            time.sleep(wait)
            r = self._get_once(url, head)
        return r

    def _get_once(self, url, head=False):
        self._wait(url)
        self.count += 1
        try:
            if head:
                r = self.s.head(url, allow_redirects=True, timeout=self.timeout)
                if r.status_code in (403, 405, 501):          # some servers refuse HEAD
                    self._wait(url)
                    r = self.s.get(url, allow_redirects=True, timeout=self.timeout, stream=True)
                    r.close()
                return Resp(r.status_code, r.url, "", dict(r.headers), r.elapsed.total_seconds())
            r = self.s.get(url, allow_redirects=True, timeout=self.timeout)
            first = r.history[0].elapsed.total_seconds() if r.history else r.elapsed.total_seconds()
            ctype = r.headers.get("Content-Type", "")
            return Resp(r.status_code, r.url, r.text if ("html" in ctype or "xml" in ctype) else "", dict(r.headers), first)
        except requests.RequestException as e:
            print("  network error on %s: %s" % (url, str(e)[:200]), file=sys.stderr)
            return Resp(0, url, error=short_error(e))


def short_error(e):
    m = str(e)
    if "NameResolution" in m or "getaddrinfo" in m:
        return "DNS lookup failed"
    if isinstance(e, requests.Timeout):
        return "timed out"
    if "SSL" in m or "certificate" in m.lower():
        return "TLS/certificate error"
    if isinstance(e, requests.ConnectionError):
        return "connection failed"
    return type(e).__name__


# ---------------------------------------------------------------- crawl

def sitemap_urls(fetch, cfg):
    base = cfg["base_url"].rstrip("/")
    todo = [urljoin(base + "/", s.lstrip("/")) for s in cfg["sitemaps"]]
    seen, pages = set(), []
    while todo:
        sm = todo.pop(0)
        if sm in seen or any(x in sm for x in cfg["sitemap_skip"]):
            continue
        seen.add(sm)
        r = fetch.get(sm)
        if r.status != 200 or not r.text:
            continue
        try:
            root = ET.fromstring(r.text.encode("utf-8"))
        except ET.ParseError:
            continue
        for el in root.iter():
            if el.tag.endswith("loc") and el.text:
                loc = el.text.strip()
                if root.tag.endswith("sitemapindex"):
                    todo.append(loc)
                else:
                    pages.append(loc)
    return list(dict.fromkeys(pages))


def unreachable(url, r, where=""):
    """No HTTP response at all (DNS/connection/timeout after retries). Usually the runner's network, not the site,
    so it is a warning under check_error; a real outage of the home page stops the run instead (exit 2)."""
    return checks.finding("check_error", "warn", url, "no response after retries (%s)%s" % (r.error, (" - " + where) if where else ""))


def norm(u):
    p = urlparse(u)
    host = (p.hostname or "").lower()
    path = p.path or "/"
    return "%s://%s%s%s" % (p.scheme, host, path, ("?" + p.query) if p.query else "")


def run_checks(cfg, fetch, weekly=False, max_pages=None, log=print):
    """Crawl + check. Returns (findings, inventory, stats) or raises RuntimeError when the site can't be read."""
    base = cfg["base_url"].rstrip("/")
    base_host = checks.host_of(base)
    home = base + "/"
    key_urls = [urljoin(home, k.lstrip("/")) for k in cfg["key_pages"]]

    urls = sitemap_urls(fetch, cfg)
    sitemap_n = len(urls)
    for k in key_urls:
        if k not in urls:
            urls.insert(0, k)
    if home in urls:
        urls.remove(home)
    urls.insert(0, home)
    cap = max_pages or cfg["max_pages"]
    urls = urls[:cap]
    log("crawl: %d sitemap urls, checking %d pages" % (sitemap_n, len(urls)))

    findings, timings, fetched, link_sources = [], {}, {}, {}
    cache_miss = 0
    require = {urljoin(home, p.lstrip("/")) for p in cfg["require_app_link_paths"]}
    for i, u in enumerate(urls):
        r = fetch.get(u)
        fetched[norm(u)] = r.status
        if r.status == 0 or r.status >= 400:
            if u == home:
                raise RuntimeError("home page unreachable: %s %s" % (r.status, r.error))
            findings.append(unreachable(u, r) if r.status == 0 else checks.finding("page_status", "fail", u, "returns %s" % r.status))
            continue
        if r.url and norm(r.url) != norm(u):
            fetched[norm(r.url)] = r.status
        timings[u] = r.elapsed
        if "MISS" in (r.headers.get("X-ac") or r.headers.get("x-ac") or ""):
            cache_miss += 1
        if not r.text:
            continue
        page = checks.Page(u, r.text)
        findings += checks.check_page(page, cfg, is_home=(u == home), require_app_link=(u in require))
        for link, _text in page.abs_links(cfg["skip_link_prefixes"]):
            link_sources.setdefault(link, []).append(u)
        if (i + 1) % 50 == 0:
            log("  ...%d pages" % (i + 1))

    findings += checks.check_ttfb(timings, cfg, key_urls)

    # ---- links
    internal, app, external = [], [], []
    for link in link_sources:
        h = checks.host_of(link)
        if h == base_host:
            path = checks.path_of(link)
            if any(path.startswith(p) for p in cfg["skip_link_paths"]):
                continue
            if norm(link) not in fetched:
                internal.append(link)
        elif checks.is_app(link, cfg):
            app.append(link)
        elif not any(h == b or h.endswith("." + b) for b in cfg["banned_hosts"]):
            external.append(link)
    log("links: %d internal not yet fetched, %d app, %d outbound%s" % (len(internal), len(app), len(external), "" if weekly else " (outbound skipped: weekly only)"))

    def srcs(link):
        s = sorted(set(link_sources[link]))
        return "linked from %s%s" % (", ".join(checks.path_of(x) for x in s[:3]), " +%d more" % (len(s) - 3) if len(s) > 3 else "")

    for link in internal:
        r = fetch.get(link, head=True)
        fetched[norm(link)] = r.status
        if r.status == 0:
            findings.append(unreachable(link, r, srcs(link)))
        elif r.status >= 400:
            findings.append(checks.finding("broken_link", "fail", link, "%s - %s" % (r.status, srcs(link))))
    # internal pages that were fetched as pages AND linked: report broken links to them too (e.g. /blog/ 404 linked from nav)
    for link in link_sources:
        st = fetched.get(norm(link))
        if checks.host_of(link) == base_host and link not in internal and st is not None and st >= 400:
            findings.append(checks.finding("broken_link", "fail", link, "%s - %s" % (st, srcs(link))))

    utm_mode = cfg["utm"]["mode"]
    utm_live = utm_mode == "fail" or (utm_mode == "auto" and any(checks.has_any_utm(l) for l in app))
    checked_app = {}
    for link in app:
        target = norm(link.split("?")[0])
        if target not in checked_app:
            checked_app[target] = fetch.get(link)
        r = checked_app[target]
        if r.status == 0:
            findings.append(unreachable(link, r, srcs(link)))
        elif r.status >= 400:
            findings.append(checks.finding("app_link_broken", "fail", link, "%s - %s" % (r.status, srcs(link))))
        elif checks.path_of(link).rstrip("/") and not checks.path_of(r.url).rstrip("/"):
            findings.append(checks.finding("app_link_broken", "fail", link,
                                           "redirects to %s and loses the %s path - %s" % (r.url, checks.path_of(link), srcs(link))))
        miss = checks.missing_utms(link, cfg)
        if miss and utm_live:
            findings.append(checks.finding("utm_missing", "fail", link, "missing %s - %s" % (", ".join(miss), srcs(link))))
    app_with_utm = sum(1 for l in app if checks.has_any_utm(l))

    if weekly:
        for link in external[: cfg["external_links"]["max_checked"]]:
            r = fetch.get(link, head=True)
            if r.status in cfg["external_links"]["fail_statuses"] or (r.status == 0 and r.error == "DNS lookup failed"):
                findings.append(checks.finding("external_broken", "warn", link, "%s - %s" % (r.status or r.error, srcs(link))))

    # de-dupe by key
    uniq = {}
    for f in findings:
        uniq.setdefault(f["key"], f)
    stats = {
        "pages": len(urls), "sitemap_urls": sitemap_n, "requests": fetch.count,
        "ttfb": checks.ttfb_stats(timings), "cache_miss": cache_miss,
        "app_links": len(app), "app_links_with_utm": app_with_utm, "utm_enforced": utm_live,
        "outbound_checked": min(len(external), cfg["external_links"]["max_checked"]) if weekly else 0,
    }
    return list(uniq.values()), sorted(set(urls)), stats


# ---------------------------------------------------------------- state + diff

def load_state(state_dir):
    p = os.path.join(state_dir, "state.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_state(state_dir, state):
    os.makedirs(state_dir, exist_ok=True)
    p = os.path.join(state_dir, "state.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, p)


def diff(prev, findings, inventory):
    prev_f = prev.get("findings", {})
    cur = {f["key"]: f for f in findings}
    new = [cur[k] for k in cur if k not in prev_f]
    resolved = [prev_f[k] for k in prev_f if k not in cur]
    prev_inv = set(prev.get("inventory", []))
    inv = set(inventory)
    added = sorted(inv - prev_inv) if prev_inv else []
    removed = sorted(prev_inv - inv) if prev_inv else []
    return {"new": new, "resolved": resolved, "open": list(cur.values()), "added": added, "removed": removed,
            "first_run": not prev}


def next_state(prev, findings, inventory, stats, now):
    first_seen = dict(prev.get("first_seen", {}))
    cur = {f["key"]: f for f in findings}
    for k in cur:
        first_seen.setdefault(k, now.isoformat())
    first_seen = {k: v for k, v in first_seen.items() if k in cur}
    hist = list(prev.get("history", []))
    hist.append({"at": now.isoformat(), "fail": sum(f["severity"] == "fail" for f in findings),
                 "warn": sum(f["severity"] == "warn" for f in findings), "pages": stats.get("pages"),
                 "ttfb_median": stats.get("ttfb", {}).get("median"), "ttfb_p90": stats.get("ttfb", {}).get("p90")})
    events = list(prev.get("events", []))
    d = diff(prev, findings, inventory)
    for f in d["new"]:
        events.append({"at": now.isoformat(), "type": "opened", "check": f["check"], "url": f["url"], "detail": f["detail"]})
    for f in d["resolved"]:
        events.append({"at": now.isoformat(), "type": "resolved", "check": f["check"], "url": f["url"], "detail": f["detail"]})
    cutoff = (now - dt.timedelta(days=35)).isoformat()
    return {"last_run": now.isoformat(), "findings": cur, "inventory": inventory, "first_seen": first_seen,
            "history": [h for h in hist if h["at"] >= cutoff], "events": [e for e in events if e["at"] >= cutoff],
            "weekly_sent": prev.get("weekly_sent", ""), "last_stats": stats}


# ---------------------------------------------------------------- Slack text

ORDER = list(checks.TITLES)
SITE = "tulletogether.com"


def _short(u, cfg):
    base = cfg["base_url"].rstrip("/")
    return u[len(base):] or "/" if u.startswith(base) else u


COLLAPSE_AT = 4     # the same problem on this many pages = a template-wide issue, shown as one line


def _group_lines(fs, cfg, per_group=6):
    lines = []
    by = {}
    for f in fs:
        by.setdefault(f["check"], []).append(f)
    for chk in sorted(by, key=lambda c: ORDER.index(c) if c in ORDER else 99):
        items = sorted(by[chk], key=lambda f: f["url"])
        lines.append("*%s* (%d)" % (checks.TITLES.get(chk, chk), len(items)))
        # collapse findings that share the same "what" (e.g. the 35,000 footer on 237 posts) into one line
        same = {}
        for f in items:
            what = f["key"].split("|", 2)[2]
            same.setdefault(what or f["key"], []).append(f)
        rows = []
        for what, grp in sorted(same.items(), key=lambda kv: -len(kv[1])):
            if len(grp) >= COLLAPSE_AT:
                eg = ", ".join("`%s`" % _short(f["url"], cfg) for f in grp[:3])
                rows.append("  • on *%d pages*: %s (e.g. %s)" % (len(grp), grp[0]["detail"], eg))
            else:
                rows += ["  • `%s` %s" % (_short(f["url"], cfg), f["detail"]) for f in grp]
        lines += rows[:per_group]
        if len(rows) > per_group:
            lines.append("  • _...and %d more_" % (len(rows) - per_group))
    return lines


def daily_text(d, stats, cfg, notify="changes"):
    """None = nothing to post."""
    changed = d["new"] or d["resolved"] or d["added"] or d["removed"]
    open_fail = [f for f in d["open"] if f["severity"] == "fail"]
    if not changed and not (notify == "failures" and open_fail):
        return None
    t = stats.get("ttfb", {})
    head = ":mag: *%s site check* - %d open failures, %d warnings across %d pages" % (
        SITE, len(open_fail), len(d["open"]) - len(open_fail), stats.get("pages", 0))
    if d["first_run"]:
        head += " _(first run: everything below is listed as new)_"
    out = [head]
    nf = [f for f in d["new"] if f["severity"] == "fail"]
    nw = [f for f in d["new"] if f["severity"] == "warn"]
    if nf:
        out += ["", ":red_circle: *New failures*"] + _group_lines(nf, cfg)
    if nw:
        out += ["", ":large_yellow_circle: *New warnings*"] + _group_lines(nw, cfg)
    if d["resolved"]:
        out += ["", ":white_check_mark: *Fixed since last run*"] + _group_lines(d["resolved"], cfg)
    if d["added"] or d["removed"]:
        out += ["", ":page_facing_up: *Pages changed in the sitemap*"]
        for u in d["added"][:8]:
            out.append("  • added `%s`" % _short(u, cfg))
        for u in d["removed"][:8]:
            out.append("  • removed `%s`" % _short(u, cfg))
        extra = max(0, len(d["added"]) - 8) + max(0, len(d["removed"]) - 8)
        if extra:
            out.append("  • _...and %d more_" % extra)
    if not changed and open_fail:
        out += ["", "_No change since last run; still open:_"] + _group_lines(open_fail, cfg, per_group=3)
    out += ["", "_TTFB median %ss / p90 %ss, edge-cache MISS on %d of %d pages; app links %d (%d with UTMs)._" % (
        t.get("median", "?"), t.get("p90", "?"), stats.get("cache_miss", 0), stats.get("ttfb", {}).get("n", 0),
        stats.get("app_links", 0), stats.get("app_links_with_utm", 0))]
    return "\n".join(out)


def weekly_text(state, d, stats, cfg, now):
    week_ago = (now - dt.timedelta(days=7)).isoformat()
    ev = [e for e in state.get("events", []) if e["at"] >= week_ago]
    opened = [e for e in ev if e["type"] == "opened"]
    fixed = [e for e in ev if e["type"] == "resolved"]
    open_fail = [f for f in d["open"] if f["severity"] == "fail"]
    open_warn = [f for f in d["open"] if f["severity"] == "warn"]
    hist = [h for h in state.get("history", []) if h["at"] >= week_ago]
    meds = [h["ttfb_median"] for h in hist if h.get("ttfb_median") is not None]
    out = [":bar_chart: *%s weekly site check* - week to %s" % (SITE, now.date().isoformat()),
           "%d runs this week. Opened %d, fixed %d. Open now: %d failures, %d warnings across %d pages." % (
               len(hist), len(opened), len(fixed), len(open_fail), len(open_warn), stats.get("pages", 0))]
    if meds:
        out.append("TTFB median by run: %s (threshold %.1fs)." % (", ".join("%.2fs" % m for m in meds), cfg["ttfb"]["threshold_s"]))
    if stats.get("outbound_checked"):
        out.append("Outbound links checked: %d." % stats["outbound_checked"])
    if open_fail:
        out += ["", "*Still open*"] + _group_lines(open_fail, cfg, per_group=4)
    if open_warn:
        out += ["", "*Warnings*"] + _group_lines(open_warn, cfg, per_group=3)
    return "\n".join(out)


def post_slack(text, token, channel):
    r = requests.post("https://slack.com/api/chat.postMessage", timeout=20,
                      headers={"Authorization": "Bearer " + token},
                      json={"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False})
    body = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
    if r.status_code != 200 or not body.get("ok"):
        print("slack post failed: %s %s" % (r.status_code, (body.get("error") or r.text)[:120]), file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------- main

def main(argv=None, fetcher=None, now=None, out=print):
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true", help="force the weekly summary + outbound link check")
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--save-state", action="store_true", help="save state even in dry run")
    ap.add_argument("--config")
    ap.add_argument("--json", help="also write the findings to this file")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    dry = os.environ.get("DRY_RUN", "1").strip().lower() not in ("0", "false", "no", "off")
    token = os.environ.get("SITE_CHECK_SLACK_TOKEN", "")
    channel = os.environ.get("SITE_CHECK_SLACK_CHANNEL", "")
    state_dir = os.environ.get("STATE_DIR") or os.path.join(HERE, "state")
    notify = os.environ.get("SITE_CHECK_NOTIFY", "changes")
    weekday = int(os.environ.get("WEEKLY_WEEKDAY", "0"))
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    if not dry and not (token and channel):
        out("DRY_RUN=0 but SITE_CHECK_SLACK_TOKEN / SITE_CHECK_SLACK_CHANNEL not set - refusing to run")
        return 2

    prev = load_state(state_dir)
    week_id = "%d-W%02d" % now.isocalendar()[:2]
    weekly = a.weekly or (weekday >= 0 and now.weekday() == weekday and prev.get("weekly_sent") != week_id)

    t0 = time.monotonic()
    try:
        findings, inventory, stats = run_checks(cfg, fetcher or Fetcher(cfg), weekly=weekly, max_pages=a.max_pages, log=out)
    except RuntimeError as e:
        out("site check could not run: %s" % e)
        msg = ":warning: *%s site check could not run*: %s" % (SITE, e)
        if not dry and prev.get("last_error") != str(e):
            post_slack(msg, token, channel)
        if not dry or a.save_state:
            prev["last_error"] = str(e)
            save_state(state_dir, prev)
        return 2
    stats["seconds"] = round(time.monotonic() - t0, 1)
    out("done in %.0fs, %d requests, %d findings" % (stats["seconds"], stats["requests"], len(findings)))

    d = diff(prev, findings, inventory)
    state = next_state(prev, findings, inventory, stats, now)
    msgs = []
    t = daily_text(d, stats, cfg, notify)
    if t:
        msgs.append(t)
    if weekly:
        msgs.append(weekly_text(state, d, stats, cfg, now))
        state["weekly_sent"] = week_id

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"findings": findings, "stats": stats}, f, indent=1)

    if not msgs:
        out("no change since last run - nothing to post")
    for m in msgs:
        if dry:
            out("----- DRY RUN: would post to %s -----\n%s\n-----" % (channel or "#growth-strategy (SITE_CHECK_SLACK_CHANNEL unset)", m))
        elif not post_slack(m, token, channel):
            return 2                                   # state not advanced: next run re-reports
    if not dry or a.save_state:
        state.pop("last_error", None)
        save_state(state_dir, state)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
