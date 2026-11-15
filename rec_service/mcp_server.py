"""Read-only MCP server for the Tulle rec service (2026-11-15) - a PRIVATE PROTOTYPE.

WHAT IT IS
    An MCP (Model Context Protocol) endpoint at /mcp on the existing FastAPI app, over streamable HTTP. A couple's own
    AI client (Claude Code, Claude Desktop, ...) can read their own planning data. Five tools, all read-only, no
    model call, no writes:
        get_my_profile      the wedding profile (xano.profile_of)
        get_my_wedding      their own plan: budget, booked / considered items with the costs THEY entered, notes
        list_saved_vendors  the vendors they saved with the heart, as public cards
        find_vendor         one vendor by its (near) full NAME, as a public card (or a few name-only candidates)
        budget_status       their own budget against their own booked items (cost_tools.budget_status)
    It is NOT launched to couples: opening it up is gated on a team decision. Until then it only answers the user ids
    listed in REC_MCP_USER_IDS.

DATA POLICY (owner 2026-11-15) - read this before adding a field or a tool
    Everything a tool returns is sent to an OUTSIDE AI provider. So:
    1. NO PRICING, for anyone, in any form. This server has nothing to do with Tulle's prices or pricing data: no
       figures, ranges, bands, estimates, market averages, benchmarks or "below / above typical" labels, and no tool
       may call a pricing function (xano.venue_pricing, the estimate / compare / split tools, benchmarks ...).
       tests/test_mcp_server.py patches those to raise and runs every tool.
    2. public_card() builds the ONLY vendor shape a tool may return: name, state (city only from a structured
       city field - never parsed out of an address), category, venue_type and
       tulle_url (the vendor's public page, https://www.tulletogether.app/vendors/<V-id> - the id inside that link
       is the one id the owner accepts exposing). Never capacity, descriptions or other free text from Tulle's
       data, PDF counts or links, addresses, contact details, images or any other internal id.
    3. NO search, filter or browse tool without an owner decision: a vendor is found BY NAME only, and only on a
       strong name match (name_match below: the words typed must sit in the vendor's name and cover at least half
       of it; a query of only place / venue-type words must be the exact name). At most 3 names come back, at most
       6 upstream searches run per call, and calls are capped per account per day (REC_MCP_DAILY_LOOKUPS, counted
       in memory under a lock AND from the usage rows). The exact tool list is pinned in the tests.
    4. What the couple typed in themselves (budget, the costs of what they booked, their quotes and notes) is
       theirs and is returned - EXCEPT an amount that equals the price hint the Tulle app pre-fills for that
       vendor ("Agreed price" on Mark booked): that figure may be Tulle's, so the row says amount_hidden and is
       left out of every total (hidden_amounts below). On rows linked to a Tulle vendor only the name, category
       and the couple's own amounts are returned; notes, contact and package only on rows they created themselves.
    Left out on purpose (2026-11-15): list_vendors (every listing path is the Discovery search endpoint, which is
    price-aware and has no city filter), split_budget (its percentages are a Xano table, not constants), next_step
    (plan.next_step reads table 90 while My wedding books into table 33 - it can suggest a slot already booked),
    and every cost-estimate / comparison tool.

ENV VARS (config.py)
    REC_MCP=1                 mounts /mcp. Default "0": no route, and the `mcp` package is never imported.
    REC_MCP_USER_IDS=7,42     the only Xano user ids that may use it. Default empty = nobody.
    REC_MCP_PUBLIC_URL        REQUIRED with REC_MCP=1: this service's public https origin; the MCP URL is <it>/mcp.
    REC_MCP_TOKEN_KEY         REQUIRED: the secret that encrypts the OAuth tokens (mcp_oauth.py). Either one missing =
                              /mcp is NOT mounted and one line in the log says why.
    REC_MCP_ALLOWED_HOSTS     optional extra Host headers /mcp answers to (the public URL's host always is one).
    (REC_MCP_CONSENT_URL, REC_MCP_CLIENT_HOSTS, REC_MCP_ALLOWED_ORIGINS, REC_MCP_DEV_RAW_TOKEN: see config.py.)
    REC_MCP_DAILY_LOOKUPS=25  find_vendor calls per account per UTC day (not-found ones count). Over it: a tool error.

THE GATE (every HTTP request to /mcp, before the SDK sees it - _Gate below)
    POST only: anything else is 405. The SDK would answer GET with a long-lived server-to-client event stream; the
        gate never lets a GET reach it.
    No `Origin` header (unless listed in REC_MCP_ALLOWED_ORIGINS): 403. Host must be the public URL's host or one of
        REC_MCP_ALLOWED_HOSTS (421 otherwise).
    `Authorization: Bearer <access token>` - an OAuth access token issued by mcp_oauth.py ("Sign in with Tulle";
        read that module's docstring for the whole flow). No token, or one that is not valid: 401 with
        WWW-Authenticate: Bearer resource_metadata="..." - the only thing that makes Claude start sign-in, which is
        why it happens here and not in a tool. The couple's Xano token comes out of the access token and goes
        through caller() below: app._auth (xano.verify_user -> GET auth/me, as on every /rec route), the allowlist
        REC_MCP_USER_IDS (`*` = everyone) and the kill switch - on EVERY request, initialize and tools/list included.
        If the sign-in check itself fails (Xano down, a timeout) the client gets a generic "could not verify", and
        the detail is printed to the server log only.
    The verified user is handed to the tool on the request scope, so auth/me is read once per request. The plain
    tool functions below, called directly (tests), take the couple's Xano token as `Bearer <token>` and run
    caller() themselves.

LOGGING (every tool call)
    One rec_usage row (table 79): kind "mcp_<tool>", surface "mcp", model "none", cost 0. guards._prompts and the
    reports (rec_watch.py, experiments_brief.py) leave "mcp_" rows out, so a call never spends a question or a daily
    / monthly prompt and never counts as Planner usage. A failed log write never fails the tool. The same rows are
    what the daily lookup cap counts. Every tier (full, plan, no plan) gets the same output: there are no prices
    to gate.

CONNECT
    claude.ai / Desktop / mobile: Customize > Connectors > Add custom connector > https://<public host>/mcp.
    Claude Code: claude mcp add --transport http tulle https://<public host>/mcp   then /mcp to sign in.

TRANSPORT
    Official Python SDK (`mcp`, v2: MCPServer). Stateless, and each POST is answered with one plain JSON body: no
    session ids, and - because the gate refuses GET - no server-to-client stream is ever opened. So it works across
    workers and restarts. The gate + the SDK's Starlette app are ONE route at exactly /mcp (not a catch-all Mount),
    and the host app's lifespan is wrapped so the SDK's session manager runs for the life of the process (a
    sub-app's own lifespan never runs).
"""
import contextlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

import config
import cost_tools
import lookup
import wedding
import xano

TOOLS = ("get_my_profile", "get_my_wedding", "list_saved_vendors", "find_vendor", "budget_status")
PATH = "/mcp"
SURFACE = "mcp"
SCOPE_KEY = "tulle_mcp_caller"     # where the gate leaves (token, user) for the tool wrappers
PRICE_NOTE = "Prices are not included."
HIDDEN_NOTE = "Some booked amounts are shown only in the Tulle app."


class MCPDenied(Exception):
    """A refusal or failure the caller is told about in words. No data goes with it. status = the gate's HTTP code."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _app():
    """The FastAPI module, imported late: app.py imports this module at its very end (flag on), and the helpers
    reused here (_auth, OTHER_VENDOR_CATS, _submit_usage) live there."""
    import app
    return app


# ---------------------------------------------------------------- auth + guards + logging

def caller(authorization):
    """(token, user) for an allowed caller, else MCPDenied. Order: signed in, on the allowlist, kill switch.
    The literal "Bearer " prefix is required on this path (the /rec routes are unchanged)."""
    if not isinstance(authorization, str) or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise MCPDenied("Not signed in: send the couple's Tulle token as 'Authorization: Bearer <token>'.", 401)
    try:
        token, user = _app()._auth(authorization)
    except HTTPException:
        raise MCPDenied("Not signed in: send the couple's Tulle token as 'Authorization: Bearer <token>'.", 401)
    except Exception as e:     # Xano down, a timeout, ...: the detail (it can hold a URL) stays in the server log
        print("mcp sign-in check failed:", repr(e)[:300])
        raise MCPDenied("Tulle could not verify the sign-in right now. Try again in a moment.", 503)
    try:
        uid = int(user.get("id"))
    except (TypeError, ValueError):
        uid = None
    if uid is None or not (config.MCP_ALL_USERS or uid in config.MCP_USER_IDS):
        raise MCPDenied("The Tulle MCP server is a private prototype and is not enabled for this account.", 403)
    if config.KILL_SWITCH:
        raise MCPDenied("Tulle's planner is switched off right now. Try again later.", 503)
    return token, user


def _log(row):
    """One rec_usage row on the app's pool. Never raises: logging must not break a tool."""
    try:
        _app()._submit_usage(dict(row, surface=SURFACE))
    except Exception as e:
        print("mcp usage log failed:", repr(e)[:200])


def _run(tool, authorization, fn, who=None):
    """Auth + guards, then fn(token, user, access, full) with one usage row either way.
    who = (token, user) the gate already verified for this request; without it the same check runs here."""
    token, user = who or caller(authorization)
    access = xano.has_paid_access(user)     # any active plan
    full = xano.beta_member(user)           # full member: the chat's FULL detail
    base = {"user_id": user["id"], "kind": "mcp_" + tool, "chat_id": 0, "model": "none",
            "tier": "paid" if full else "free", "counted_as_free": False, "input_tokens": 0, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "output_tokens": 0, "tool_calls": 0, "cost_usd": 0}
    t0 = time.time()
    try:
        out = fn(token, user, access, full)
    except MCPDenied as e:
        _log(dict(base, status="error", error=str(e)[:500]))
        raise
    except Exception as e:
        _log(dict(base, status="error", error=repr(e)[:500]))
        raise MCPDenied("Tulle could not run %s right now. Try again in a moment." % tool)
    _log(dict(base, status="ok", latency_ms=int((time.time() - t0) * 1000)))
    return out


def _tier(access, full):
    return "full" if full else "light" if access else "free"


# ---------------------------------------------------------------- the one vendor shape (DATA POLICY)

CARD_FIELDS = ("name", "city", "state", "category", "venue_type", "tulle_url")
VENDOR_PAGE = "https://www.tulletogether.app/vendors/"       # the site's public vendor pages: /vendors/V666
_CURRENCY = re.compile(r"[$\u20ac\u00a3]\s?\d|\b(?:usd|eur|gbp)\s?\d|\b\d[\d,.]*\s?(?:usd|eur|gbp|dollars?|euros?)\b", re.I)


def _first(v):
    return v[0] if isinstance(v, list) and v else ("" if isinstance(v, list) else v)


def _label(v, n):
    """A short label from Tulle's data, or "" - never a string that carries a figure."""
    t = " ".join(str(v or "").split())[:n] if isinstance(v, str) else ""
    return "" if ("$" in t or any(ch.isdigit() for ch in t)) else t


def public_card(card):
    """THE ONLY vendor shape a tool may return (module docstring, DATA POLICY): name, state, category, venue_type
    and tulle_url - built field by field, so nothing else on the source card can ride along.
    city: only from a structured `city` field. Today's rows have none (state + a free-text address), and a city parsed
    out of an address turned into street or venue text ("Austin, Private Lane"), so the address is never read.
    name: dropped when it carries a currency amount. tulle_url: the vendor's public page, only for a V-prefixed id."""
    card = card or {}
    name = " ".join(str(card.get("name") or "").split())[:120]
    vt = card.get("venue_type")
    types = [t for t in (_label(x, 60) for x in (vt if isinstance(vt, list) else [vt])) if t][:4]
    out = {"name": "" if _CURRENCY.search(name) else name, "city": _label(card.get("city"), 60),
           "state": _label(_first(card.get("state")), 60),
           "category": _label(card.get("category") or "Venue", 40) or "Venue", "venue_type": types}
    vid = str(card.get("vendor_id") or "")
    if re.fullmatch(r"V\d{1,9}", vid):
        out["tulle_url"] = VENDOR_PAGE + vid
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def _candidate(card):
    """A name-only candidate for an ambiguous lookup: name + state."""
    c = public_card(card)
    return {k: c[k] for k in ("name", "state") if k in c}


# ---------------------------------------------------------------- the daily lookup cap (anti-scrape)

_LOOKUPS, _LOOKUPS_LOCK = {}, threading.Lock()


def lookups_today(user_id):
    """find_vendor calls this account made today (UTC) that ran - found or not - from its rec_usage rows."""
    day = xano.today()
    return sum(1 for r in xano.user_usage(user_id)
               if r.get("kind") == "mcp_find_vendor" and r.get("status") == "ok" and r.get("usage_day") == day)


def claim_lookup(user_id):
    """True and one lookup counted, or False at the cap. The usage rows are written after a call, on a pool, so on
    their own they let parallel calls through; the count here is taken BEFORE the search, under a lock:
    max(counted in this process today, persisted rows today). A failed read of the rows raises (fail closed).
    Per process: with several workers each has its own counter, and the persisted rows are what they share."""
    day = xano.today()
    persisted = lookups_today(user_id)
    key = (int(user_id), day)
    with _LOOKUPS_LOCK:
        for k in [k for k in _LOOKUPS if k[1] != day]:
            del _LOOKUPS[k]
        n = max(_LOOKUPS.get(key, 0), persisted)
        if n >= config.MCP_DAILY_LOOKUPS:
            return False
        _LOOKUPS[key] = n + 1
        return True


# ---------------------------------------------------------------- a strong NAME match (find_vendor is not a search)

MAX_SEARCHES = 6                      # upstream name searches one find_vendor call may run
_NAME_IGNORE = set(lookup.STOP) | {"venue", "venues", "event", "events", "wedding", "weddings"}
# Place and venue-type words: a query made ONLY of these ("Brooklyn", "hotel", "barn texas") names no vendor.
_GEO_WORDS = {w for p in (set(lookup.US_STATES) | set(lookup.PLACES) | set(lookup.DESTINATIONS)) for w in lookup.norm(p).split()}
_GEO_WORDS |= set(lookup.STATE_ABBR) | {"city", "county", "borough", "town", "village", "downtown", "uptown", "north",
                                        "south", "east", "west", "area", "state", "usa"}
_VENDOR_WORD = re.compile("|".join(pat for _, pat in lookup.VENDOR_WORDS), re.I)     # photographer, dj, florist, ...


def _words(text):
    return [t for t in lookup.norm(text).split() if t not in _NAME_IGNORE]


def generic_only(text):
    w = _words(text)
    return (not w or lookup._place(text)
            or all(t in _GEO_WORDS or t in lookup.GENERIC or _VENDOR_WORD.fullmatch(t) for t in w))


def name_match(query, vendor_name):
    """0 = not a match; 2 = the vendor's exact name; 1 + coverage (1.5 .. 2) = the words typed sit in the name, in
    order, as whole words, and are at least half of the name's words. A query of only place / venue-type words
    matches nothing but the exact name."""
    q, n = _words(query), _words(vendor_name)
    if not q or not n:
        return 0.0
    if q == n:
        return 2.0
    if generic_only(query):
        return 0.0
    if not any(n[i:i + len(q)] == q for i in range(len(n) - len(q) + 1)):
        return 0.0
    cover = len(q) / float(len(n))
    return 1.0 + cover if cover >= 0.5 else 0.0


def _search_order(name):
    """The vendor categories to search, at most MAX_SEARCHES: the one the name itself points at first ("... Photography"),
    then venues, then the rest in the app's order. (Seven categories exist, so without such a word the last one in
    the app's list is not searched.)"""
    a = _app()
    hint = lookup.vendor_hint(name)[1]
    order = ([hint] if hint else []) + ["Venue"] + list(a.OTHER_VENDOR_CATS)
    return list(dict.fromkeys(order))[:MAX_SEARCHES]


def strong_matches(name, location, token):
    """[(score, row)] best first - only rows whose NAME strongly matches (name_match), in the place named when one is
    given. One search for the first category; the others only when that found nothing. Never more than
    MAX_SEARCHES upstream searches."""
    term = " ".join(lookup.tokens(name))
    if not term:
        return []
    cats = _search_order(name)
    if generic_only(name):
        cats = cats[:1]               # only an exact name can match: one search is enough

    def strong(rows):
        best = {}
        for r in rows or []:
            sc = name_match(name, r.get("name"))
            if sc and r.get("vendor_id") and (not location or lookup._in_place(r, location)):
                if sc > best.get(r["vendor_id"], (0, None))[0]:
                    best[r["vendor_id"]] = (sc, r)
        return sorted(best.values(), key=lambda x: -x[0])

    def search(cat):
        return [dict(r, category=cat) for r in xano.name_search(term, token, category=cat) or []]
    found = strong(search(cats[0]))
    if found or len(cats) == 1:
        return found
    errors, rows = [], []

    def one(cat):
        try:
            return search(cat)
        except Exception as e:
            errors.append(e)
            return []
    with ThreadPoolExecutor(max_workers=len(cats) - 1) as ex:
        for got in ex.map(one, cats[1:]):
            rows += got
    if errors and len(errors) == len(cats) - 1:
        raise errors[0]
    return strong(rows)


# ---------------------------------------------------------------- the couple's plan rows (table 33)

def _linked(p):
    """The Tulle vendor id a plan row is linked to ("" = a row the couple created themselves)."""
    return wedding._vid(p)


def hidden_amounts(packages, cards_by_id):
    """ids of plan rows whose amount may be TULLE'S figure, not the couple's: the row is linked to a Tulle vendor and
    its price equals the hint the app pre-fills as "Agreed price" for that vendor (wedding.price_hint - a pure read
    of the vendor card already fetched, no pricing call). No hint for the vendor = it cannot have been pre-filled."""
    out = set()
    for p in packages or []:
        vid = _linked(p)
        hint = wedding.price_hint(cards_by_id.get(vid)) if vid else None
        if hint and abs(wedding._num(p.get("total_cost")) - float(hint)) < 0.5:
            out.add(id(p))
    return out


def plan_rows(token, user, selected_only=False):
    """(packages safe to total, the hidden rows, the saved vendors' cards, the row ids of the hidden rows).
    A hidden row keeps its name and category but its price AND paid are zeroed and it is flagged _mcp_hidden, so no
    total, slice or remainder can give the figure back (a hidden row with no id is dropped altogether). Cards: the saved vendors plus the vendors the rows are linked to (the same table-11 card
    read, cached; never a pricing endpoint)."""
    packages = [p for p in xano.user_packages(token, user["id"]) or [] if not wedding.is_example(p)]
    if selected_only:
        packages = [p for p in packages if p.get("Selected") is True]
    saved = xano.saved_vendor_ids(user)
    need = list(dict.fromkeys(saved + [v for v in (_linked(p) for p in packages) if v]))[:80]
    cards = {}
    if need:
        with ThreadPoolExecutor(max_workers=8) as ex:
            cards = {v: c for v, c in zip(need, ex.map(xano.cached_vendor_card, need)) if c}
    hide = hidden_amounts(packages, cards)
    safe, hidden_ids = [], set()
    for p in packages:
        if id(p) in hide:
            if p.get("id") is None:
                continue
            p = dict(p, total_cost=0, Balance_Paid=0, Balance_Left=0, _mcp_hidden=True)
            hidden_ids.add(p["id"])
        safe.append(p)
    return safe, hide, [cards[v] for v in saved if v in cards], hidden_ids


# ---------------------------------------------------------------- the five tools (plain functions; build() wraps them)

def _own_item(item, hidden_ids=()):
    """One plan row for the outside: name and category; the couple's amounts unless hidden (plan_rows); the name of
    their uploaded file, never its link. Notes, contact and package only on a row they created themselves - on a
    row linked to a Tulle vendor those can hold Tulle's text. Never the row id or the vendor id."""
    if not isinstance(item, dict):
        return item
    out = {"name": item.get("name") or "", "category": item.get("category") or ""}
    if item.get("id") in hidden_ids:
        out["amount_hidden"] = True
    else:
        out["price"], out["paid"] = item.get("price"), item.get("paid")
    if not item.get("vendor_id"):
        for k in ("notes", "contact", "package"):
            if item.get(k):
                out[k] = item[k]
    if isinstance(item.get("file"), dict):
        out["file"] = {"name": item["file"].get("name") or "File"}
    return out


def get_my_wedding(authorization, who=None):
    """The couple's own plan (packages + saved vendors -> wedding.view, as GET /rec/wedding reads it): their budget,
    what they booked, their quotes and other costs as THEY entered them. Saved vendors are public cards; the price
    hints of wedding.view are never asked for (show_prices=False); an amount that equals such a hint is hidden and
    left out of every total (plan_rows)."""
    def go(token, user, access, full):
        if not wedding.ENABLED:
            return {"enabled": False}
        packages, hide, cards, hidden_ids = plan_rows(token, user)
        v = wedding.view(user, packages, cards, show_prices=False)
        keys = {c["key"] for c in wedding.CATS}
        own = lambda x: _own_item(x, hidden_ids)       # noqa: E731
        cats = [{"key": c.get("key"), "label": c.get("label"), "booked": own(c.get("booked")),
                 "quotes": [own(x) for x in c.get("quotes") or []],
                 "saved": [public_card(k) for k in cards if (k.get("category") or "Venue") == c.get("key")]}
                for c in v.get("categories") or []]
        out = {"enabled": v.get("enabled"), "budget": v.get("budget"), "booked_total": v.get("booked_total"),
               "left": v.get("left"), "paid_total": v.get("paid_total"), "slices": v.get("slices") or [],
               "categories": cats, "extras": [own(x) for x in v.get("extras") or []],
               "considering": [own(x) for x in v.get("considering") or []],
               "saved_other": [public_card(k) for k in cards if k.get("category") not in keys],
               "amounts_hidden": len(hide)}
        if hide:
            out["note"] = HIDDEN_NOTE
        return out
    return _run("get_my_wedding", authorization, go, who)


def get_my_profile(authorization, who=None):
    """The couple's wedding profile (their own answers), plus their plan level."""
    def go(token, user, access, full):
        return {"profile": xano.profile_of(user), "plan": _tier(access, full)}
    return _run("get_my_profile", authorization, go, who)


def list_saved_vendors(authorization, who=None):
    """The vendors the couple saved (user.favorited_vendors), as public cards."""
    def go(token, user, access, full):
        ids = xano.saved_vendor_ids(user)
        cards = []
        if ids:
            with ThreadPoolExecutor(max_workers=8) as ex:
                cards = [c for c in ex.map(lambda v: cost_tools._safe(xano.vendor_card, v), ids) if c]
        return {"saved_count": len(ids), "vendors": [public_card(c) for c in cards], "note": PRICE_NOTE}
    return _run("list_saved_vendors", authorization, go, who)


def find_vendor(authorization, name, location="", who=None):
    """One venue or other vendor by its NAME -> one public card (plus up to 2 other names), or up to 3 names when no
    single one stands out. Only on a strong name match (name_match); no pricing is read; at most MAX_SEARCHES
    upstream searches; capped per account per day (claim_lookup)."""
    name = " ".join(str(name or "").split())[:120]
    location = " ".join(str(location or "").split())[:80]

    def go(token, user, access, full):
        if sum(1 for ch in lookup.norm(name) if ch.isalnum()) < 4:
            raise MCPDenied("A vendor name of at least 4 letters is needed.")
        if not claim_lookup(user["id"]):
            raise MCPDenied("Daily lookup limit reached.", 429)
        found = strong_matches(name, location, token)
        out = {"status": "not_found", "query": name, "matches": [], "note": PRICE_NOTE}
        if not found:
            out["note"] = "No vendor with that name was found. The lookup matches a vendor's full name."
        elif len(found) == 1 or found[0][0] > found[1][0]:          # one exact / strongest match
            card = public_card(found[0][1])
            if card.get("name"):
                out.update(status="found", matches=[card])
                also = [c for c in (_candidate(r) for _, r in found[1:3]) if c.get("name")]
                if also:
                    out["also"] = also
        else:                                                         # several equally good: names only
            names = [c for c in (_candidate(r) for _, r in found[:3]) if c.get("name")]
            if names:
                out.update(status="choices", candidates=names, note="Several vendors match that name.")
        return out
    return _run("find_vendor", authorization, go, who)


def budget_status(authorization, who=None):
    """Where the couple's budget stands: what THEY added to their Budget tab (selected items, the seeded examples left
    out), paid and owed, against THEIR budget (cost_tools.budget_status: arithmetic over their own rows). A row whose
    amount is hidden (plan_rows) is left out of every sum and counted in amounts_hidden."""
    def go(token, user, access, full):
        packages, hide, _, hidden_ids = plan_rows(token, user, selected_only=True)
        kept = [p for p in packages if not p.get("_mcp_hidden")]
        w = cost_tools.budget_status(user, packages=kept)["widget"]
        keys = ("budget", "added", "paid", "owed", "remaining", "pct_of_budget", "item_count", "by_category")
        out = {"tool": "budget_status", "text": cost_tools.summary_text(w), "status": {k: w.get(k) for k in keys},
               "source": "the couple's own Budget tab on Tulle", "amounts_hidden": len(hide)}
        if hide:
            out["note"] = HIDDEN_NOTE
        return out
    return _run("budget_status", authorization, go, who)


# ---------------------------------------------------------------- the MCP server (SDK imported only here)

INSTRUCTIONS = (
    "Tulle Together (tulletogether.app) wedding planning connector. Returns one couple's own planning data: their "
    "profile, their wedding plan and budget as they entered them, and the vendors they saved. A vendor can be looked "
    "up by its name; there is no search or filtering. Read-only. Prices are not included.")

# name -> (title, description). Descriptions only say what a tool returns, exactly (directory review: no instructions
# to the model, no promotion). Names stay under 64 characters.
TOOL_TEXT = {
    "get_my_profile": ("Wedding profile", "Returns the couple's own wedding profile: location, guest count, date, "
                       "budget and style, as they entered them on Tulle Together."),
    "get_my_wedding": ("Wedding plan", "Returns the couple's own wedding plan: their budget, the vendors they booked or "
                       "are considering with the amounts they entered, their notes on rows they created, totals paid "
                       "and left, and the vendors they saved (name, state, category, type and Tulle page link). An "
                       "amount that matches a figure pre-filled by Tulle is marked amount_hidden and left out of the "
                       "totals. Does not include prices from Tulle; pricing lives on tulletogether.app."),
    "list_saved_vendors": ("Saved vendors", "Returns the venues and other vendors the couple saved on Tulle Together: "
                           "name, state, category, type and Tulle page link. Does not include prices; pricing lives "
                           "on tulletogether.app."),
    "find_vendor": ("Find a vendor by name", "Returns one wedding venue or other vendor on Tulle Together whose name "
                    "matches the name given: name, state, category, type and Tulle page link, plus up to two other "
                    "matching names. When no single vendor stands out, returns up to three names with their state. "
                    "Matches a vendor's full or nearly full name only; location (a city or state) is optional. Has a "
                    "daily limit per account. Does not include prices; pricing lives on tulletogether.app."),
    "budget_status": ("Budget status", "Returns where the couple's budget stands, from the numbers they entered on "
                      "Tulle Together: what they added, paid and still owe against their budget, by category. Amounts "
                      "pre-filled by Tulle are left out and counted in amounts_hidden."),
}


def build():
    """The MCPServer with the five tools. Each wrapper reads the request's Authorization header from the SDK context
    and turns MCPDenied into a tool error (is_error, message only)."""
    from typing import Any

    from mcp.server import MCPServer
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_types import ToolAnnotations

    # log_level: the SDK calls logging.basicConfig with it - WARNING leaves the host app's logging as it was.
    server = MCPServer("tulle", instructions=INSTRUCTIONS, log_level="WARNING")
    assert set(TOOL_TEXT) == set(TOOLS) and all(len(n) <= 64 for n in TOOLS)
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    def tool(name):
        title, description = TOOL_TEXT[name]
        return server.tool(name=name, title=title, description=description, annotations=ro, structured_output=True)

    def call(fn, ctx, *args):
        headers = ctx.headers or {}
        scope = getattr(ctx.request_context.request, "scope", None) or {}
        try:
            return fn(headers.get("authorization"), *args, who=scope.get(SCOPE_KEY))
        except MCPDenied as e:
            raise ToolError(str(e))

    @tool("get_my_profile")
    def _profile(ctx: Context) -> dict[str, Any]:
        return call(get_my_profile, ctx)

    @tool("get_my_wedding")
    def _wedding(ctx: Context) -> dict[str, Any]:
        return call(get_my_wedding, ctx)

    @tool("list_saved_vendors")
    def _saved(ctx: Context) -> dict[str, Any]:
        return call(list_saved_vendors, ctx)

    @tool("find_vendor")
    def _find_tool(name: str, ctx: Context, location: str = "") -> dict[str, Any]:
        return call(find_vendor, ctx, name, location)

    @tool("budget_status")
    def _budget(ctx: Context) -> dict[str, Any]:
        return call(budget_status, ctx)

    return server


def _host_ok(host, allowed):
    """host = the request's Host header. An allowlist entry without a port also matches that host on any port."""
    host = (host or "").strip().lower()
    for h in allowed:
        h = h.strip().lower()
        if h and (host == h or (":" not in h and host.rsplit(":", 1)[0] == h and host.count(":") == 1)):
            return True
    return False


class _Gate:
    """Plain ASGI in front of the SDK's app (module docstring, THE GATE). Nothing reaches the SDK - and no response
    stream is opened - until the method, Origin, Host and the caller's token have all passed."""

    def __init__(self, inner, allowed_hosts, auth=None, origins=()):
        self.inner = inner
        self.allowed_hosts = list(allowed_hosts)
        self.auth = auth or caller                 # mount() passes mcp_oauth.bearer_caller (an OAuth access token)
        self.origins = set(origins)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.inner(scope, receive, send)
        h = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        if scope.get("method") != "POST":
            return await self._refuse(send, 405, "The Tulle MCP endpoint takes POST only.", [(b"allow", b"POST")])
        if "origin" in h and h["origin"] not in self.origins:
            return await self._refuse(send, 403, "The Tulle MCP endpoint does not accept requests from web pages.")
        if not _host_ok(h.get("host"), self.allowed_hosts):
            return await self._refuse(send, 421, "The Tulle MCP endpoint is not served on this host.")
        import anyio
        try:
            who = await anyio.to_thread.run_sync(self.auth, h.get("authorization"))
        except MCPDenied as e:
            # 401 + WWW-Authenticate (with resource_metadata, set by mcp_oauth) is what starts sign-in in Claude.
            extra = [(b"www-authenticate", str(getattr(e, "challenge", "") or "Bearer").encode("latin-1"))] if e.status == 401 else []
            return await self._refuse(send, e.status, str(e), extra)
        scope[SCOPE_KEY] = who
        await self.inner(scope, receive, send)

    @staticmethod
    async def _refuse(send, status, message, extra=()):
        body = json.dumps({"error": message}).encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("ascii"))] + list(extra)})
        await send({"type": "http.response.body", "body": body})


def _security(hosts):
    """The SDK's own Host / Origin check, as a second line behind the gate."""
    from mcp.server.transport_security import TransportSecuritySettings
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=list(hosts) + [h + ":*" for h in hosts if ":" not in h],
                                     allowed_origins=list(config.MCP_ALLOWED_ORIGINS))


def mount(app):
    """Add POST /mcp to an existing FastAPI app and run the SDK's session manager inside the app's lifespan
    (startup handlers registered with @app.on_event keep running: the previous lifespan is wrapped, not replaced).
    Without REC_MCP_PUBLIC_URL and REC_MCP_TOKEN_KEY nothing is mounted (returns None) and the SDK is not imported.
    Also adds the OAuth routes of mcp_oauth.py ("Sign in with Tulle")."""
    import mcp_oauth
    why = mcp_oauth.not_ready()
    if why:
        print("MCP: REC_MCP=1 but %s - /mcp is NOT mounted." % "; ".join(why))
        return None
    hosts = list(dict.fromkeys([mcp_oauth.public_host()] + [h.lower() for h in config.MCP_ALLOWED_HOSTS]))
    from starlette.routing import Route

    server = build()
    sub = server.streamable_http_app(streamable_http_path=PATH, stateless_http=True, json_response=True,
                                     transport_security=_security(hosts))
    gate = _Gate(sub, hosts, auth=mcp_oauth.bearer_caller, origins=config.MCP_ALLOWED_ORIGINS)
    app.router.routes.append(Route(PATH, endpoint=gate))     # gate + the SDK's ASGI app, at exactly /mcp
    mcp_oauth.install(app)
    previous = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async with previous(a) as state:
            async with server.session_manager.run():
                yield state

    app.router.lifespan_context = lifespan
    return server
