"""
Tulle Vendor Scraper — photographer pricing ingestion
-----------------------------------------------------
A polite web scraper + Reddit API ingestion path that reuses the existing
Claude extraction + Xano + cost/credit machinery from extract_core.py.

It mirrors extract_core.run_extraction's generator contract: yields log strings
during the run, then yields a final summary dict
    {ok, partial, failed, skipped, cost_usd, tokens, results, credit_exhausted, log}
so the dashboard can drive it with the same streaming UI (incl. the credit-halt
banner).

Trust hierarchy (stored per observation):  submission(4) > website(3) > blog(2) > reddit(1)

Required env vars (widgets degrade gracefully if unset):
    XANO_PHOTOGRAPHERS_ENDPOINT  — GET worklist: photographers (table 11, Category=Photographer)
    XANO_OBSERVATION_ENDPOINT    — POST one photographer_pricing observation
    XANO_PATCH_VENDOR_ENDPOINT   — PATCH /{id} scrape_status fields on table 11
    ANTHROPIC_API_KEY            — reused from the PDF pipeline
Reddit (Phase 2 — optional; reddit_search no-ops until all three are set + praw installed):
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
Optional politeness overrides:
    SCRAPER_USER_AGENT, SCRAPER_PER_DOMAIN_INTERVAL
"""

import os
import re
import time
import json
import random
import urllib.robotparser as robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin

import requests
import anthropic

from extract_core import (
    call_claude_text,
    CreditExhausted,
    _slack_alert,
    _fetch_xano_pages,
    _compute_cost,
)

try:
    from bs4 import BeautifulSoup
except Exception:                       # pragma: no cover - dependency optional at import time
    BeautifulSoup = None

# ── CONFIG ────────────────────────────────────────────────────────────────────

SCRAPER_UA = os.environ.get(
    "SCRAPER_USER_AGENT",
    "TulleTogetherBot/1.0 (+https://tulletogether.com/bot; wedding pricing research; contact: data@tulletogether.com)",
)
try:
    _PER_DOMAIN_MIN_INTERVAL = float(os.environ.get("SCRAPER_PER_DOMAIN_INTERVAL", "10"))
except Exception:
    _PER_DOMAIN_MIN_INTERVAL = 10.0

_FETCH_TIMEOUT  = 20
_MAX_BYTES      = 3_000_000      # cap raw page size
_MAX_TEXT_CHARS = 40_000         # cap text corpus fed to Claude (per vendor)
_MAX_PRICING_PAGES = 5           # bounded same-host crawl per vendor

TRUST = {"submission": 4, "website": 3, "blog": 2, "reddit": 1}

# Module-level politeness state (per-process)
_robots_cache: dict = {}         # host -> RobotFileParser | False(=unreadable, fail-closed)
_last_hit: dict     = {}         # host -> monotonic timestamp of last request
_cond_cache: dict   = {}         # url  -> (etag, last_modified)

_URL_HINT = re.compile(r"(pricing|invest|package|collection|rates|booking|info|faq|weddings?)", re.I)


# ── POLITE WEB FETCH ──────────────────────────────────────────────────────────

def _host(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _robots_ok(url):
    """(allowed, crawl_delay). Fail-CLOSED: if robots.txt can't be read due to a
    network/parse error we return (False, None) and do not scrape. A clean 404/empty
    robots.txt means 'no rules' → allowed (standard convention)."""
    host = _host(url)
    if not host:
        return False, None
    rp = _robots_cache.get(host)
    if rp is False:
        return False, None
    if rp is None:
        rp = robotparser.RobotFileParser()
        robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
        try:
            r = requests.get(robots_url, headers={"User-Agent": SCRAPER_UA}, timeout=_FETCH_TIMEOUT)
            if r.status_code >= 400 or not (r.text or "").strip():
                rp.parse([])                      # no robots → allow all
            else:
                rp.parse(r.text.splitlines())
        except Exception:
            _robots_cache[host] = False           # fail-closed
            return False, None
        _robots_cache[host] = rp
    return rp.can_fetch(SCRAPER_UA, url), rp.crawl_delay(SCRAPER_UA)


def polite_fetch(url, force=False):
    """GET a public page politely. Returns (html_or_None, meta).
    Safeguards: robots.txt allow (fail-closed), per-host rate limit honoring
    Crawl-delay, descriptive UA, conditional GET (304 skip), Retry-After backoff,
    https/GET-only, no auth, size + content-type guards."""
    meta = {"status": None, "from_cache": False, "skipped_reason": "", "final_url": url, "bytes": 0}
    if not url.lower().startswith(("http://", "https://")):
        meta["skipped_reason"] = "not http(s)"
        return None, meta

    allowed, crawl_delay = _robots_ok(url)
    if not allowed:
        meta["skipped_reason"] = "robots disallow / unreadable"
        return None, meta

    host = _host(url)
    min_interval = max(_PER_DOMAIN_MIN_INTERVAL, float(crawl_delay or 0))
    last = _last_hit.get(host)
    if last is not None:
        wait = min_interval - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)

    headers = {"User-Agent": SCRAPER_UA, "Accept": "text/html,application/xhtml+xml,*/*"}
    if not force and url in _cond_cache:
        etag, lastmod = _cond_cache[url]
        if etag:
            headers["If-None-Match"] = etag
        if lastmod:
            headers["If-Modified-Since"] = lastmod

    try:
        r = requests.get(url, headers=headers, timeout=_FETCH_TIMEOUT, allow_redirects=True)
    except Exception as e:
        _last_hit[host] = time.monotonic()
        meta["skipped_reason"] = f"fetch error: {e}"
        return None, meta
    _last_hit[host] = time.monotonic()
    meta["status"], meta["final_url"] = r.status_code, r.url

    if r.status_code == 304:
        meta["from_cache"] = True
        meta["skipped_reason"] = "304 not modified"
        return None, meta

    if r.status_code in (429, 503):
        ra = r.headers.get("Retry-After")
        try:
            wait = min(60, int(ra)) if ra else 10
        except Exception:
            wait = 10
        time.sleep(wait + random.uniform(0, 2))
        try:
            r = requests.get(url, headers=headers, timeout=_FETCH_TIMEOUT, allow_redirects=True)
            _last_hit[host] = time.monotonic()
            meta["status"] = r.status_code
        except Exception as e:
            meta["skipped_reason"] = f"retry error: {e}"
            return None, meta

    if r.status_code != 200:
        meta["skipped_reason"] = f"status {r.status_code}"
        return None, meta

    ctype = (r.headers.get("Content-Type") or "").lower()
    if "html" not in ctype and "text" not in ctype:
        meta["skipped_reason"] = f"non-html ({ctype or 'unknown'})"
        return None, meta

    content = r.text or ""
    if len(content) > _MAX_BYTES:
        content = content[:_MAX_BYTES]
    meta["bytes"] = len(content)

    et, lm = r.headers.get("ETag"), r.headers.get("Last-Modified")
    if et or lm:
        _cond_cache[url] = (et, lm)
    return content, meta


def _html_to_text(html):
    """Strip scripts/nav/footers → price-relevant text, bounded to _MAX_TEXT_CHARS."""
    if not html:
        return ""
    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
            t.decompose()
        text = soup.get_text("\n")
    else:
        text = re.sub(r"<[^>]+>", " ", html)
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text[:_MAX_TEXT_CHARS]


def discover_pricing_urls(html, base_url, limit=_MAX_PRICING_PAGES):
    """Same-host pages whose href/anchor text looks pricing-related (bounded)."""
    if not html or not BeautifulSoup:
        return []
    soup = BeautifulSoup(html, "html.parser")
    base_host = _host(base_url)
    found, seen = [], set()
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])
        if not full.lower().startswith("http") or _host(full) != base_host:
            continue
        full = full.split("#")[0]
        if full in seen or full.rstrip("/") == base_url.rstrip("/"):
            continue
        if _URL_HINT.search(a["href"]) or _URL_HINT.search(a.get_text(" ")):
            seen.add(full)
            found.append(full)
        if len(found) >= limit:
            break
    return found


# ── REDDIT (Phase 2 — no-ops until praw + REDDIT_* env are present) ────────────

_SUBS_DEFAULT = ["weddingphotography", "weddingplanning", "wedding"]


def _reddit_client():
    cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    sec = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    ua  = os.environ.get("REDDIT_USER_AGENT", "").strip()
    if not (cid and sec and ua):
        return None
    try:
        import praw
        return praw.Reddit(client_id=cid, client_secret=sec, user_agent=ua, read_only=True)
    except Exception:
        return None


def reddit_search(name, state, limit=25):
    """Return [{text, permalink}] of threads mentioning the vendor + a price word.
    praw auto-throttles the OAuth rate limit. Stores only derived text for one-shot
    extraction — see README for the Reddit Data API terms caveat."""
    reddit = _reddit_client()
    if reddit is None:
        return []
    query = f'"{name}" (price OR cost OR package OR paid OR quote OR $)'
    out = []
    try:
        for sub in _SUBS_DEFAULT:
            for post in reddit.subreddit(sub).search(query, sort="relevance", time_filter="all", limit=limit):
                body = ((post.title or "") + "\n\n" + (getattr(post, "selftext", "") or "")).strip()
                if body:
                    out.append({"text": body[:_MAX_TEXT_CHARS],
                                "permalink": "https://www.reddit.com" + post.permalink})
    except Exception:
        return out
    return out


# ── CLAUDE EXTRACTION ─────────────────────────────────────────────────────────

PHOTOG_PRICING_PROMPT = """You extract WEDDING PHOTOGRAPHER package pricing from the provided text \
(a scraped web page or a Reddit thread). Return ONLY a JSON array of observation objects — no prose, \
no markdown fences.

Each object:
{
  "package_name":       string (e.g. "Collection 2", "8-hour package", "" if unnamed),
  "price":              number or null (USD, digits only, no $ or commas),
  "price_type":         one of "package" | "hourly" | "starting_at" | "range_low" | "range_high" | "unknown",
  "currency":           "USD" unless clearly otherwise,
  "hours":              number or null (coverage hours),
  "num_photographers":  integer or null,
  "second_shooter":     true | false | null,
  "engagement_session": true | false | null,
  "album":              true | false | null,
  "deliverables":       string (galleries, # edited images, prints, etc.; "" if none),
  "region":             string (city/metro if stated, else ""),
  "quote":              string — the SHORT verbatim snippet from the text that states this price,
  "confidence":         "high" | "med" | "low"
}

Rules:
- Extract ONLY concrete prices that actually appear in the text. NEVER invent or estimate numbers.
- If the text has no concrete price (e.g. "inquire for pricing"), return [].
- One object per distinct package/price point. "Starting at $X" → price_type "starting_at".
- "quote" must be copied verbatim from the source and must contain the price it supports.
- Prefer high confidence only when the price is unambiguous and clearly the photographer's own rate."""


def _num(v):
    try:
        return float(v) if v not in (None, "", "null") else None
    except Exception:
        return None


def _int(v):
    try:
        return int(float(v)) if v not in (None, "", "null") else None
    except Exception:
        return None


def _tribool(v):
    if isinstance(v, bool):
        return v
    if v in (None, "", "null"):
        return None
    return str(v).strip().lower() in ("true", "yes", "1", "y", "included")


def extract_photog_pricing(client, source_text, *, vendor_name, region, source_type, source_url):
    """(observations, note, usage) via call_claude_text. observations is a list of dicts."""
    if not (source_text or "").strip():
        return [], " (empty source)", {}
    instr = (
        f"Vendor: {vendor_name}\nRegion: {region or 'unknown'}\n"
        f"Source type: {source_type}\nSource URL: {source_url}\n\n"
        "Extract photographer pricing per the system rules. Return ONLY the JSON array."
    )
    parsed, note, usage = call_claude_text(client, source_text, PHOTOG_PRICING_PROMPT, instr, max_tokens=4000)
    if isinstance(parsed, dict) and isinstance(parsed.get("observations"), list):
        parsed = parsed["observations"]
    if not isinstance(parsed, list):
        parsed = []
    return parsed, note, usage


# ── XANO I/O ──────────────────────────────────────────────────────────────────

def _post_observation(obs, *, vendor_id, vendor_name, source_type, source_url, trust_tier, captured_at, state):
    """POST one observation → photographer_pricing. Returns (ok, fail)."""
    endpoint = os.environ.get("XANO_OBSERVATION_ENDPOINT", "").strip()
    if not endpoint:
        return 0, 1
    conf  = str(obs.get("confidence", "")).strip().lower() or "low"
    price = _num(obs.get("price"))
    payload = {
        "vendor_id":          vendor_id,
        "vendor_name":        vendor_name,
        "category":           "Photographer",
        "source_type":        source_type,
        "source_url":         source_url,
        "captured_at":        captured_at,
        "trust_tier":         int(trust_tier),
        "quote":              str(obs.get("quote", ""))[:1000],
        "package_name":       str(obs.get("package_name", "")),
        "price":              price,
        "price_type":         str(obs.get("price_type", "unknown") or "unknown"),
        "currency":           str(obs.get("currency", "USD") or "USD"),
        "hours":              _num(obs.get("hours")),
        "num_photographers":  _int(obs.get("num_photographers")),
        "second_shooter":     _tribool(obs.get("second_shooter")),
        "engagement_session": _tribool(obs.get("engagement_session")),
        "album":              _tribool(obs.get("album")),
        "deliverables":       str(obs.get("deliverables", "")),
        "region":             str(obs.get("region", "") or ""),
        "state":              state,
        "raw_extract":        obs,
        "confidence":         conf,
        "needs_review":       (conf != "high") or (price is None),
    }
    try:
        r = requests.post(endpoint, json=payload, timeout=30)
        return (1, 0) if r.status_code in (200, 201) else (0, 1)
    except Exception:
        return 0, 1


def _update_scrape_status(vendor_xano_id, status, *, error="", cost_usd=0.0):
    """PATCH the scrape_* fields on table 11. Never raises."""
    endpoint = os.environ.get("XANO_PATCH_VENDOR_ENDPOINT", "").strip()
    if not endpoint or vendor_xano_id in (None, ""):
        return "no endpoint"
    payload = {
        "scrape_status":            status,
        "scrape_last_attempted_at": datetime.now(timezone.utc).isoformat(),
        "scrape_last_error":        (error or "")[:1000],
        "scrape_cost_usd":          round(float(cost_usd), 6),
    }
    try:
        r = requests.patch(f"{endpoint}/{vendor_xano_id}", json=payload, timeout=15)
        return str(r.status_code)
    except Exception as e:
        return f"err {e}"


def _fetch_photographers():
    """All photographer rows from the worklist endpoint (paginated)."""
    endpoint = os.environ.get("XANO_PHOTOGRAPHERS_ENDPOINT", "").strip()
    if not endpoint:
        return []
    all_rows = []
    try:
        for all_rows, _ in _fetch_xano_pages(endpoint):
            pass
    except Exception:
        return all_rows
    return all_rows


def _g(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return ""


def get_scrape_status():
    """Queue overview for the dashboard. {rows, counts, total, with_website}."""
    rows = _fetch_photographers()
    counts = {}
    for r in rows:
        s = str(_g(r, "scrape_status")).strip().lower() or "pending"
        if s not in ("scraped", "partial", "failed", "skipped"):
            s = "pending"
        counts[s] = counts.get(s, 0) + 1
    with_site = sum(1 for r in rows if str(_g(r, "Website", "website")).strip())
    return {"rows": rows, "counts": counts, "total": len(rows), "with_website": with_site}


# ── PUBLIC GENERATOR ──────────────────────────────────────────────────────────

def run_scrape(start_row=0, end_row=None, vendor_ids=None, rerun_failed=False,
               sources=("website", "reddit"), force_all=False):
    """Generator. Yields log strings, then a final summary dict — same contract as
    extract_core.run_extraction (incl. credit_exhausted)."""
    log = []

    def emit(line):
        log.append(line)
        return (line,)

    def _empty(extra_log=None):
        return {"ok": 0, "partial": 0, "failed": 0, "skipped": 0, "cost_usd": 0.0,
                "tokens": {}, "results": [], "credit_exhausted": False, "log": log}

    yield from emit("🔎 Fetching photographer worklist from Xano...")
    rows = _fetch_photographers()
    if not rows:
        yield from emit("⚠  No photographers returned (is XANO_PHOTOGRAPHERS_ENDPOINT set?).")
        yield _empty()
        return

    if vendor_ids:
        want = {v.strip() for v in vendor_ids if v.strip()}
        batch = [r for r in rows if str(_g(r, "Vendor_ID", "vendor_id")).strip() in want]
    elif rerun_failed:
        batch = [r for r in rows if str(_g(r, "scrape_status")).strip().lower() == "failed"]
    else:
        batch = rows[start_row:(None if end_row is None else end_row)]
        if not force_all:
            batch = [r for r in batch
                     if str(_g(r, "scrape_status")).strip().lower() not in ("scraped", "skipped")]

    yield from emit(f"▶ {len(batch)} photographer(s) to scrape · sources={', '.join(sources)}")
    if not batch:
        yield from emit("Nothing to do — all selected vendors already scraped (use force/row-range to redo).")
        yield _empty()
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    total_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    results_log, credit_halted = [], False
    captured_at = datetime.now(timezone.utc).isoformat()

    def add_usage(u, bucket):
        for k in total_tokens:
            total_tokens[k] += u.get(k, 0)
            bucket[k] += u.get(k, 0)

    for idx, row in enumerate(batch):
        vendor_id = str(_g(row, "Vendor_ID", "vendor_id")).strip()
        name      = str(_g(row, "Name", "name")).strip() or vendor_id
        website   = str(_g(row, "Website", "website")).strip()
        state     = str(_g(row, "State", "state")).strip()
        xano_id   = row.get("id")

        yield from emit("")
        yield from emit(f"[{idx + 1}/{len(batch)}] {name} ({vendor_id})")
        run_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
        observations = []          # list of (obs_dict, source_type, source_url)

        try:
            # ── Website ──
            if "website" in sources and website:
                yield from emit(f"  🌐 {website}")
                html, meta = polite_fetch(website)
                if html is None:
                    yield from emit(f"  ⚠  homepage: {meta.get('skipped_reason') or 'no content'}")
                corpus_parts = []
                if html:
                    corpus_parts.append(_html_to_text(html))
                    for purl in discover_pricing_urls(html, website):
                        phtml, _pm = polite_fetch(purl)
                        if phtml:
                            yield from emit(f"  🔗 {purl}")
                            corpus_parts.append(_html_to_text(phtml))
                corpus = "\n\n".join(p for p in corpus_parts if p)[:_MAX_TEXT_CHARS]
                if corpus.strip():
                    yield from emit(f"  🤖 Extracting from {len(corpus):,} chars...")
                    obs, note, usage = extract_photog_pricing(
                        client, corpus, vendor_name=name, region=state,
                        source_type="website", source_url=website)
                    add_usage(usage, run_usage)
                    observations += [(o, "website", website) for o in obs]
                    yield from emit(f"  ✓  {len(obs)} website observation(s){note}")
                else:
                    yield from emit("  ⚠  no usable website text")
            elif "website" in sources:
                yield from emit("  ⏭  no website on file")

            # ── Reddit (Phase 2; no-ops until praw + creds) ──
            if "reddit" in sources:
                threads = reddit_search(name, state)
                if threads:
                    yield from emit(f"  👽 {len(threads)} reddit thread(s)")
                for th in threads:
                    obs, note, usage = extract_photog_pricing(
                        client, th["text"], vendor_name=name, region=state,
                        source_type="reddit", source_url=th["permalink"])
                    add_usage(usage, run_usage)
                    observations += [(o, "reddit", th["permalink"]) for o in obs]

        except CreditExhausted as ce:
            credit_halted = True
            yield from emit(f"  🛑 CREDIT BALANCE EXHAUSTED at {name} — halting to avoid mass false-failures.")
            yield from emit(f"     ({ce})")
            yield from emit("  ℹ  Remaining vendors left PENDING — add credits, then re-run.")
            _slack_alert(f"🛑 Tulle scraper HALTED — Anthropic credit balance too low (stopped at {vendor_id}). Add credits + re-run.")
            break

        # ── Post observations + status writeback ──
        ok_posts = fail_posts = 0
        for obs, st_type, surl in observations:
            okc, failc = _post_observation(
                obs, vendor_id=vendor_id, vendor_name=name, source_type=st_type,
                source_url=surl, trust_tier=TRUST.get(st_type, 1), captured_at=captured_at, state=state)
            ok_posts += okc
            fail_posts += failc

        run_cost = _compute_cost(run_usage)
        no_source = ("website" in sources and not website) and not ("reddit" in sources and _reddit_client())
        if no_source and not observations:
            status, result_status = "skipped", "SKIPPED"
        elif fail_posts and ok_posts:
            status, result_status = "partial", "PARTIAL"
        elif fail_posts and not ok_posts:
            status, result_status = "failed", "FAILED"
        else:
            status, result_status = "scraped", "OK"

        patch = _update_scrape_status(xano_id, status, error="", cost_usd=run_cost)
        tiers = ",".join(sorted({st for _o, st, _u in observations}))
        yield from emit(
            f"  📝 {ok_posts} observation(s) posted"
            f"{f', {fail_posts} failed' if fail_posts else ''}"
            f" · status={status} (${run_cost:.4f}) · writeback {patch}")
        results_log.append({
            "status":       result_status,
            "pdf_id":       vendor_id,          # reuse PDF-tab card keys
            "venue_name":   name,
            "summary_rows": ok_posts,
            "pricing_rows": 0,
            "cost_usd":     run_cost,
            "offering":     f"sources: {tiers}" if tiers else "",
            "category":     "Photographer",
            "reason":       "" if not fail_posts else f"{fail_posts} post(s) failed",
        })
        time.sleep(0.4)

    ok_count   = sum(1 for r in results_log if r["status"] == "OK")
    part_count = sum(1 for r in results_log if r["status"] == "PARTIAL")
    fail_count = sum(1 for r in results_log if r["status"] == "FAILED")
    skip_count = sum(1 for r in results_log if r["status"] == "SKIPPED")
    cost_usd   = _compute_cost(total_tokens)

    yield from emit("")
    yield from emit("─" * 48)
    if credit_halted:
        yield from emit("🛑 HALTED — Anthropic credit balance too low. Add credits, then re-run.")
    yield from emit(f"✅ Done — {ok_count} scraped, {part_count} partial, {skip_count} skipped, {fail_count} failed")
    yield from emit(f"💰 Claude cost: ${cost_usd:.4f}")

    yield {
        "ok":               ok_count,
        "partial":          part_count,
        "failed":           fail_count,
        "skipped":          skip_count,
        "cost_usd":         cost_usd,
        "tokens":           total_tokens,
        "results":          results_log,
        "credit_exhausted": credit_halted,
        "log":              log,
    }
