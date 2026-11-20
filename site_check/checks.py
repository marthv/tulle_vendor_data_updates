"""Pure page checks for the tulletogether.com site QA (no network here; run.py does the fetching).

Every check returns a list of findings. A finding is a dict:
    key       stable id used to diff against the last run (check|url|what) - never contains a measured value
    check     short check name (see TITLES)
    severity  "fail" or "warn"
    url       the page (or link target) the finding is about
    detail    one line for Slack
"""
import re
from urllib.parse import urljoin, urlparse, parse_qs, urldefrag

from bs4 import BeautifulSoup

TITLES = {
    "page_status": "Pages that do not load (4xx/5xx)",
    "broken_link": "Broken links on the site",
    "external_broken": "Broken outbound links (weekly)",
    "couples_claim": "Couples count is not the canonical figure",
    "price_mismatch": "Prices that do not match the app",
    "placeholder": "Placeholder / template text visible",
    "tag_missing": "Analytics tag missing",
    "banned_link": "Links to the old Glide app",
    "app_link_missing": "Key page has no link to tulletogether.app",
    "app_link_broken": "Links to tulletogether.app that fail or lose their path",
    "utm_missing": "Links to tulletogether.app without UTMs",
    "elementor_slug": "Leftover elementor-#### page slugs in links",
    "ttfb": "Slow time-to-first-byte",
    "home_mention": "Homepage does not mention a product",
    "check_error": "Could not reach (no response after retries - usually the runner's network)",
}


def finding(check, severity, url, detail, what=""):
    return {"key": "%s|%s|%s" % (check, url, what), "check": check, "severity": severity, "url": url, "detail": detail}


# ---------------------------------------------------------------- parsing

class Page:
    def __init__(self, url, html):
        self.url = url
        self.html = html or ""
        soup = BeautifulSoup(self.html, "html.parser")
        self.links = []
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            self.links.append((href, re.sub(r"\s+", " ", a.get_text(" ")).strip()))
        for t in soup(["script", "style", "noscript", "template"]):
            t.decompose()
        self.text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()

    def abs_links(self, skip_prefixes=()):
        """(absolute url without fragment, link text) for every <a href>, skipping mailto:/tel:/#..."""
        out = []
        for href, text in self.links:
            low = href.lower()
            if not href or any(low.startswith(p) for p in skip_prefixes):
                continue
            u = urldefrag(urljoin(self.url, href))[0]
            if u.startswith(("http://", "https://")):
                out.append((u, text))
        return out


def host_of(u):
    h = (urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def path_of(u):
    return urlparse(u).path or "/"


def is_app(u, cfg):
    h = host_of(u)
    return any(h == a or h.endswith("." + a) for a in cfg["app_hosts"])


# ---------------------------------------------------------------- claims

def _count_value(raw):
    s = raw.replace(",", "").replace(" ", "").lower()
    if s.endswith("k"):
        return int(float(s[:-1]) * 1000)
    return int(float(s))


def check_couples(page, cfg):
    c = cfg["couples"]
    canonical = c["canonical"]
    canon_n = _count_value(canonical.rstrip("+"))
    words = "|".join(re.escape(w) for w in c["words"])
    rx = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s?[kK])(\s*\+)?\s*(?:happy |engaged |real |other )?(?:%s)\b" % words, re.I)
    out, seen = [], set()
    for m in rx.finditer(page.text):
        n = _count_value(m.group(1))
        if n < c["min_count"]:
            continue
        shown = m.group(0)
        if n != canon_n:
            what = str(n)
            sev = "fail"
            detail = 'says "%s" (canonical %s)' % (shown, canonical)
        elif canonical.endswith("+") and not m.group(2):
            what, sev, detail = "noplus", "warn", 'says "%s" without the "+" (canonical %s)' % (shown, canonical)
        else:
            continue
        if what in seen:
            continue
        seen.add(what)
        out.append(finding("couples_claim", sev, page.url, detail, what))
    return out


# ---------------------------------------------------------------- prices

_AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{2}))?(\s?[kKmM]\b)?")


def _amounts(text, cap):
    out = []
    for m in _AMOUNT.finditer(text):
        if m.group(3):                     # "$5K+" etc. - not a plan price
            continue
        v = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
        if 0 < v <= cap:
            out.append((v, m.start()))
    return out


def is_pricing_page(page, cfg):
    p = cfg["pricing"]
    path = path_of(page.url).rstrip("/") + "/"
    if any(path == x.rstrip("/") + "/" for x in p["page_paths"]):
        return True
    # auto-detect: at least two plan labels (current or retired) each followed closely by a $ amount
    labels = [pl["label"] for pl in p["plans"]] + p["retired_labels"]
    hits = 0
    for lab in labels:
        if re.search(r"(?:%s)\W{0,5}(?:access|plan|pass)?[^$]{0,%d}\$\s?\d" % (lab, p["auto_detect_window"]), page.text, re.I):
            hits += 1
    return hits >= 2


def check_pricing(page, cfg):
    """On a pricing page: every plan's canonical range must appear, every $ amount must fall in some plan's range,
    and retired plan labels must be gone. Amounts over ignore_amount_over and "$5K"-style amounts are ignored."""
    if not is_pricing_page(page, cfg):
        return []
    p = cfg["pricing"]
    amounts = _amounts(page.text, p["ignore_amount_over"])
    out = []
    for v, _ in sorted(set((a, 0) for a, _ in amounts)):
        if not any(pl["min"] <= v <= pl["max"] for pl in p["plans"]):
            out.append(finding("price_mismatch", "fail", page.url,
                               "shows $%g, which is not any current plan price (%s)" % (v, _ranges(p)), "amount:%g" % v))
    for pl in p["plans"]:
        if not any(pl["min"] <= v <= pl["max"] for v, _ in amounts):
            out.append(finding("price_mismatch", "fail", page.url,
                               "no %s price in $%g-%g" % (pl["name"], pl["min"], pl["max"]), "plan:" + pl["name"]))
    for lab in p["retired_labels"]:
        m = re.search(lab, page.text, re.I)
        if m:
            out.append(finding("price_mismatch", "fail", page.url,
                               'retired plan still offered: "%s"' % m.group(0), "retired:" + lab))
    return out


def _ranges(p):
    return ", ".join("%s $%g-%g" % (pl["name"], pl["min"], pl["max"]) for pl in p["plans"])


# ---------------------------------------------------------------- placeholders / tags / homepage

def check_placeholders(page, cfg):
    out = []
    low = page.text.lower()
    for ph in cfg["placeholders_text"]:
        i = low.find(ph.lower())
        if i >= 0:
            snippet = page.text[i:i + 60]
            out.append(finding("placeholder", "fail", page.url, 'visible text "%s..."' % snippet.strip(), ph))
    for ph in cfg["placeholders_html"]:
        n = page.html.count(ph)
        if n:
            out.append(finding("placeholder", "fail", page.url, 'HTML contains "%s" %dx (template placeholder)' % (ph, n), ph))
    return out


def check_tags(page, cfg):
    return [finding("tag_missing", "fail", page.url, "%s not in the page source" % t, t)
            for t in cfg["tags"] if t not in page.html]


def check_home_mentions(page, cfg):
    """home_mentions: {name: regex}. A generic "wedding planner" is not the product, so Planner needs e.g. "AI Planner"."""
    return [finding("home_mention", "warn", page.url, 'homepage never mentions the %s' % name, name)
            for name, rx in cfg["home_mentions"].items() if not re.search(rx, page.text, re.I)]


# ---------------------------------------------------------------- links (static part; status checks happen in run.py)

def check_links_static(page, cfg, require_app_link=False):
    out = []
    links = page.abs_links(cfg["skip_link_prefixes"])
    banned = set()
    for u, text in links:
        h = host_of(u)
        if any(h == b or h.endswith("." + b) for b in cfg["banned_hosts"]) and u not in banned:
            banned.add(u)
            out.append(finding("banned_link", "fail", page.url, 'links to %s ("%s")' % (u, text[:40]), u))
    base = host_of(cfg["base_url"])
    for u, text in links:
        if host_of(u) == base:
            for pat in cfg["warn_link_path_patterns"]:
                if re.search(pat, path_of(u)):
                    out.append(finding("elementor_slug", "warn", page.url, "links to %s" % path_of(u), path_of(u)))
    if require_app_link and not any(is_app(u, cfg) for u, _ in links):
        out.append(finding("app_link_missing", "fail", page.url, "no link to tulletogether.app on this page"))
    # de-dupe identical keys (same slug linked from header + footer)
    uniq = {}
    for f in out:
        uniq.setdefault(f["key"], f)
    return list(uniq.values())


def missing_utms(u, cfg):
    q = parse_qs(urlparse(u).query)
    return [k for k in cfg["utm"]["required"] if not q.get(k)]


def has_any_utm(u):
    return "utm_source=" in (urlparse(u).query or "")


def check_page(page, cfg, is_home=False, require_app_link=False):
    out = []
    out += check_couples(page, cfg)
    out += check_pricing(page, cfg)
    out += check_placeholders(page, cfg)
    out += check_tags(page, cfg)
    out += check_links_static(page, cfg, require_app_link)
    if is_home:
        out += check_home_mentions(page, cfg)
    return out


# ---------------------------------------------------------------- TTFB (site-level)

def check_ttfb(timings, cfg, key_urls):
    """timings: {url: seconds}. One finding per key page over the threshold, plus one site-wide finding when more than
    fail_share of all pages are over it. Values go in detail, never in the key, so a slow site does not re-alert daily."""
    t = cfg["ttfb"]
    thr = t["threshold_s"]
    out = []
    if not timings:
        return out
    vals = sorted(timings.values())
    slow = [u for u, s in timings.items() if s > thr]
    med = vals[len(vals) // 2]
    p90 = vals[min(len(vals) - 1, int(len(vals) * 0.9))]
    for u in key_urls:
        if u in timings and timings[u] > thr:
            out.append(finding("ttfb", "fail", u, "TTFB %.2fs (threshold %.1fs)" % (timings[u], thr), "key"))
    if len(slow) > t["fail_share"] * len(vals):
        out.append(finding("ttfb", "fail", cfg["base_url"],
                           "%d of %d pages over %.1fs TTFB (median %.2fs, p90 %.2fs)" % (len(slow), len(vals), thr, med, p90),
                           "site"))
    return out


def ttfb_stats(timings):
    if not timings:
        return {}
    vals = sorted(timings.values())
    return {"n": len(vals), "median": round(vals[len(vals) // 2], 3), "p90": round(vals[min(len(vals) - 1, int(len(vals) * 0.9))], 3),
            "max": round(vals[-1], 3)}
