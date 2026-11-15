"""Read-only MCP server for the Tulle rec service (2026-11-15) - a PRIVATE PROTOTYPE.

WHAT IT IS
    An MCP (Model Context Protocol) endpoint at /mcp on the existing FastAPI app, over streamable HTTP. A couple's own
    AI client (Claude Code, Claude Desktop, ...) can read their own planning data. Five tools, all read-only, no
    model call, no writes:
        get_my_profile      the wedding profile (xano.profile_of)
        get_my_wedding      their own plan: budget, booked / considered items with the costs THEY entered, notes
        list_saved_vendors  the vendors they saved with the heart, as public cards
        find_vendor         one vendor BY NAME, as a public card (or up to 3 name-only candidates)
        budget_status       their own budget against their own booked items (cost_tools.budget_status)
    It is NOT launched to couples: opening it up is gated on a team decision. Until then it only answers the user ids
    listed in REC_MCP_USER_IDS.

DATA POLICY (owner 2026-11-15) - read this before adding a field or a tool
    Everything a tool returns is sent to an OUTSIDE AI provider. So:
    1. NO PRICING, for anyone, in any form. This server has nothing to do with Tulle's prices or pricing data: no
       figures, ranges, bands, estimates, market averages, benchmarks or "below / above typical" labels, and no tool
       may call a pricing function (xano.venue_pricing, the estimate / compare / split tools, benchmarks ...).
       tests/test_mcp_server.py patches those to raise and runs every tool.
    2. public_card() builds the ONLY vendor shape a tool may return: name, city, state, category, venue_type and
       tulle_url (the vendor's public page, https://www.tulletogether.app/vendors/<V-id> - the id inside that link
       is the one id the owner accepts exposing). Never capacity, descriptions or other free text from Tulle's
       data, PDF counts or links, addresses, contact details, images or any other internal id.
    3. NO search, filter or browse tool without an owner decision: a vendor is found BY NAME only (at most 3
       name-only candidates when the name is ambiguous), capped per account per day (REC_MCP_DAILY_LOOKUPS). The
       exact tool list is pinned in the tests.
    4. What the couple typed in themselves (budget, the costs of what they booked, their quotes and notes) is
       theirs and is returned.
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
PRICE_NOTE = "Prices are not included. Pricing lives on tulletogether.app."


class MCPDenied(Exception):
    """A refusal or failure the caller is told about in words. No data goes with it. status = the gate's HTTP code."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _app():
    """The FastAPI module, imported late: app.py imports this module at its very end (flag on), and the helpers
    reused here (_auth, _place_label, _any_vendor_search, _submit_usage) live there."""
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


def _first(v):
    return v[0] if isinstance(v, list) and v else ("" if isinstance(v, list) else v)


def _label(v, n):
    """A short label from Tulle's data, or "" - never a string that carries a figure."""
    t = " ".join(str(v or "").split())[:n]
    return "" if ("$" in t or any(ch.isdigit() for ch in t)) else t


def public_card(card):
    """THE ONLY vendor shape a tool may return (module docstring, DATA POLICY): name, city, state, category,
    venue_type and tulle_url - built field by field, so nothing else on the source card can ride along. tulle_url is
    the vendor's public page and exists only for a V-prefixed vendor id (owner 2026-11-15: that id may show there)."""
    card = card or {}
    state = _label(_first(card.get("state")), 60)
    place = _app()._place_label(card) if (card.get("address") or state) else ""
    city = _label(place.rsplit(", ", 1)[0], 60) if ", " in place else ""     # a street line has digits -> dropped
    vt = card.get("venue_type")
    types = [t for t in (_label(x, 60) for x in (vt if isinstance(vt, list) else [vt]) if isinstance(x, str)) if t][:4]
    out = {"name": " ".join(str(card.get("name") or "").split())[:120], "city": city, "state": state,
           "category": _label(card.get("category") or "Venue", 40), "venue_type": types}
    vid = str(card.get("vendor_id") or "")
    if re.fullmatch(r"V\d{1,9}", vid):
        out["tulle_url"] = VENDOR_PAGE + vid
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def _candidate(card):
    """A name-only candidate for an ambiguous lookup: name + city / state."""
    c = public_card(card)
    return {k: c[k] for k in ("name", "city", "state") if k in c}


def lookups_today(user_id):
    """find_vendor calls this account made today (UTC) that ran - found or not - from its rec_usage rows."""
    day = xano.today()
    return sum(1 for r in xano.user_usage(user_id)
               if r.get("kind") == "mcp_find_vendor" and r.get("status") == "ok" and r.get("usage_day") == day)


# ---------------------------------------------------------------- the five tools (plain functions; build() wraps them)

def _own_item(item):
    """A row the couple typed into their plan (booked vendor, quote, other cost): theirs, kept - minus the internal
    vendor id and the link to their uploaded file (its name stays)."""
    if not isinstance(item, dict):
        return item
    out = {k: v for k, v in item.items() if k != "vendor_id"}
    if isinstance(out.get("file"), dict):
        out["file"] = {"name": out["file"].get("name") or "File"}
    return out


def get_my_wedding(authorization, who=None):
    """The couple's own plan, read as GET /rec/wedding reads it (packages + saved vendors -> wedding.view): their
    budget, what they booked, their quotes and other costs exactly as THEY entered them. Saved vendors (Tulle's
    data) are public cards; wedding.view's price hints are never asked for (show_prices=False) and not copied."""
    def go(token, user, access, full):
        if not wedding.ENABLED:
            return {"enabled": False}
        packages = xano.user_packages(token, user["id"])
        ids = xano.saved_vendor_ids(user)
        cards = []
        if ids:
            with ThreadPoolExecutor(max_workers=8) as ex:
                cards = [c for c in ex.map(xano.cached_vendor_card, ids) if c]
        v = wedding.view(user, packages, cards, show_prices=False)
        keys = {c["key"] for c in wedding.CATS}
        cats = [{"key": c.get("key"), "label": c.get("label"), "booked": _own_item(c.get("booked")),
                 "quotes": [_own_item(x) for x in c.get("quotes") or []],
                 "saved": [public_card(k) for k in cards if (k.get("category") or "Venue") == c.get("key")]}
                for c in v.get("categories") or []]
        return {"enabled": v.get("enabled"), "budget": v.get("budget"), "booked_total": v.get("booked_total"),
                "left": v.get("left"), "paid_total": v.get("paid_total"), "slices": v.get("slices") or [],
                "categories": cats, "extras": [_own_item(x) for x in v.get("extras") or []],
                "considering": [_own_item(x) for x in v.get("considering") or []],
                "saved_other": [public_card(k) for k in cards if k.get("category") not in keys]}
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


def _query(name, location=""):
    """lookup.detect's dict for an explicit lookup. detect() is the Planner's parser ("Bacchus, Brooklyn", "The Mill
    nj"); when it reads the text as something other than a name, the words are searched as a name anyway."""
    text = "%s, %s" % (name, location) if location else name
    q = lookup.detect(text) or {}
    if not q.get("name"):
        q = {"name": name, "location": location, "alt_name": "", "alt_location": "",
             "words": len(lookup.norm(name).split()), "category": ""}
    return dict(q, cue=True, after_question=False)


def _find(q, token, prefer):
    """The name search of app._find_and_price WITHOUT its pricing step (that function fetches the pricing panel of
    what it finds; nothing here may). Venues first, then the other vendor types by name."""
    a = _app()
    hint = q.get("category") or ""
    if hint and hint != "Venue":
        return lookup.resolve(q, lookup.find(q, a._any_vendor_search(token, (hint,))), prefer)
    res = lookup.resolve(q, lookup.find(q, lambda term: xano.name_search(term, token)), prefer)
    if res["status"] == "not_found" and not lookup.destination_in(q["name"]):
        other = lookup.resolve(q, lookup.find(q, a._any_vendor_search(token, a.OTHER_VENDOR_CATS)), prefer)
        if other["status"] != "not_found":
            res = other
    return res


def find_vendor(authorization, name, location="", who=None):
    """One venue or other vendor BY NAME -> one public card, or up to 3 name-only candidates when the name is
    ambiguous. No pricing is read. Capped per account per day (config.MCP_DAILY_LOOKUPS)."""
    name = " ".join(str(name or "").split())[:120]
    location = " ".join(str(location or "").split())[:80]

    def go(token, user, access, full):
        import agent
        if len(lookup.norm(name)) < 3:
            raise MCPDenied("Give the vendor's name (at least 3 letters).")
        if lookups_today(user["id"]) >= config.MCP_DAILY_LOOKUPS:
            raise MCPDenied("Today's limit of %d vendor lookups is used up. The couple can keep exploring vendors on "
                            "tulletogether.app; lookups here reset tomorrow." % config.MCP_DAILY_LOOKUPS, 429)
        try:
            prefer = agent._states_of(xano.profile_of(user))
        except Exception:
            prefer = []
        res = _find(_query(name, location), token, prefer)
        status = res.get("status") or "not_found"
        matches = [m for m in res.get("matches") or [] if m.get("name")][:3]
        out = {"status": status, "query": name, "matches": [], "note": PRICE_NOTE}
        if not matches or status == "not_found":
            out.update(status="not_found", note="Tulle has no vendor by that name. Check the spelling or add the city.")
        elif status in ("found", "best"):
            out["matches"] = [public_card(matches[0])]
            if matches[1:]:
                out["also"] = [_candidate(m) for m in matches[1:]]
        else:                                       # choices / unsure: names only, never a list of cards
            out.update(status="choices", candidates=[_candidate(m) for m in matches],
                       note="Several vendors match that name. A more specific name or a city narrows it to one.")
        return out
    return _run("find_vendor", authorization, go, who)


def budget_status(authorization, who=None):
    """Where the couple's budget stands: what THEY added to their Budget tab (selected items, the seeded examples left
    out), paid and owed, against THEIR budget. cost_tools.budget_status: arithmetic over their own rows, no Tulle data."""
    def go(token, user, access, full):
        w = cost_tools.run_tool("budget_status", {}, token, user, access=access, xano=xano)["widget"]
        keys = ("budget", "added", "paid", "owed", "remaining", "pct_of_budget", "item_count", "by_category")
        return {"tool": "budget_status", "text": cost_tools.summary_text(w), "status": {k: w.get(k) for k in keys},
                "source": "the couple's own Budget tab on Tulle"}
    return _run("budget_status", authorization, go, who)


# ---------------------------------------------------------------- the MCP server (SDK imported only here)

INSTRUCTIONS = (
    "Tulle Together (tulletogether.app) wedding planning connector. Returns one couple's own planning data: their "
    "profile, their wedding plan and budget as they entered them, and the vendors they saved. A vendor can be looked "
    "up by name; there is no search or filtering. Read-only. Does not include prices; pricing lives on "
    "tulletogether.app.")

# name -> (title, description). Descriptions only say what a tool returns (directory review: no instructions to the
# model, no promotion). Names stay under 64 characters.
TOOL_TEXT = {
    "get_my_profile": ("Wedding profile", "Returns the couple's own wedding profile: location, guest count, date, "
                       "budget and style, as they entered them on Tulle Together."),
    "get_my_wedding": ("Wedding plan", "Returns the couple's own wedding plan: their budget, the vendors they booked or "
                       "are considering with the costs and notes they entered, totals paid and left, and the vendors "
                       "they saved (name, place, type and Tulle page link). Does not include prices from Tulle; "
                       "pricing lives on tulletogether.app."),
    "list_saved_vendors": ("Saved vendors", "Returns the venues and other vendors the couple saved on Tulle Together: "
                           "name, city, state, category, type and Tulle page link. Does not include prices; pricing "
                           "lives on tulletogether.app."),
    "find_vendor": ("Find a vendor by name", "Returns one wedding venue or other vendor on Tulle Together matched by "
                    "name: name, city, state, category, type and Tulle page link. When several vendors share the "
                    "name, returns up to three candidates (name, city, state). location (a city or state) is optional. "
                    "Lookup by name only, with a daily limit per account. Does not include prices; pricing lives on "
                    "tulletogether.app."),
    "budget_status": ("Budget status", "Returns where the couple's budget stands, from the numbers they entered on "
                      "Tulle Together: what they added, paid and still owe against their budget, by category."),
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
