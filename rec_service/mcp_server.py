"""Read-only MCP server for the Tulle rec service (2026-11-15) - a PRIVATE PROTOTYPE.

WHAT IT IS
    An MCP (Model Context Protocol) endpoint at /mcp on the existing FastAPI app, over streamable HTTP. A couple's own
    AI client (Claude Code, Claude Desktop, ...) can read what the Planner already knows about them. Five tools, all
    read-only, no model call, no writes:
        get_my_wedding      the "My wedding" screen (GET /rec/wedding's data)
        get_my_profile      the wedding profile (xano.profile_of)
        list_saved_venues   the vendors they saved with the heart
        lookup_venue        one venue by name (the Planner's name lookup), priced through xano.venue_pricing
        estimate_cost       the "Estimate my cost" quick action (cost_tools.run_tool)
    It is NOT launched to couples: opening it up is gated on a team decision. Until then it only answers the user ids
    listed in REC_MCP_USER_IDS.

ENV VARS (config.py)
    REC_MCP=1                 mounts /mcp. Default "0": no route, and the `mcp` package is never imported.
    REC_MCP_USER_IDS=7,42     the only Xano user ids that may use it. Default empty = nobody.
    REC_MCP_ALLOWED_HOSTS     REQUIRED with REC_MCP=1: the Host headers /mcp answers to (comma-separated, e.g. the
                              Railway domain). Empty = /mcp is NOT mounted and one line in the log says why.

THE GATE (every HTTP request to /mcp, before the SDK sees it - _Gate below)
    POST only: anything else is 405. The SDK would answer GET with a long-lived server-to-client event stream; the
        gate never lets a GET reach it.
    No `Origin` header: a request that carries one is 403. This prototype is for non-browser clients only (the app's
        CORS lets WeWeb pages call the service; none of them may call /mcp).
    Host must be one of REC_MCP_ALLOWED_HOSTS (421 otherwise).
    `Authorization: Bearer <the couple's Xano user token>` - the literal "Bearer " prefix is required here - checked
        by app._auth, the same function every /rec route uses (xano.verify_user -> GET auth/me); the user's id must
        be in REC_MCP_USER_IDS; the kill switch (REC_KILL_SWITCH=1) refuses everything, as POST /rec/tool does.
        401 / 403 / 503 with a short JSON message. So initialize and tools/list need a valid allowlisted token too.
        If the sign-in check itself fails (Xano down, a timeout) the client gets a generic "could not verify", and
        the detail is printed to the server log only.
    The verified user is handed to the tool on the request scope, so auth/me is read once per request. A tool that
    does not find it there (the plain functions below, called directly) runs the same check itself.

PRICES AND LOGGING (every tool call)
    Only a FULL member (xano.beta_member - the chat's FULL detail) gets figures from Tulle's pricing data. Everyone
    else - no plan, or a plan that is not full - gets NONE: every card goes through free_mode.card (the chat's
    free whitelist: price keys such as venue_fee_range / price_range / price_band dropped, price sentences cut
    from descriptions), no pricing panel or verdict, no price hints on saved vendors in get_my_wedding, and
    estimate_cost returns what was computed for, not the amounts (agent._cost_tool's "no figures for the model"
    rule). The couple's own numbers - their budget, costs they typed into their plan - stay.
    This is deliberately stricter than the app (which shows plan holders bands and no-plan couples the estimate
    chart): an MCP answer is read by a model we do not control.
    Each call writes one rec_usage row (table 79): kind "mcp_<tool>", surface "mcp", model "none", cost 0.
    guards._prompts and the reports (rec_watch.py, experiments_brief.py) leave "mcp_" rows out, so a call never
    spends a question or a daily / monthly prompt and never counts as Planner usage. A failed log write never
    fails the tool.

CONNECT FROM CLAUDE CODE (syntax checked against code.claude.com/docs/en/mcp on 2026-11-15)
    claude mcp add --transport http tulle https://<host>/mcp --header "Authorization: Bearer <token>"
    <token> is the couple's own Tulle sign-in token; it expires like any app session.

TRANSPORT
    Official Python SDK (`mcp`, v2: MCPServer). Stateless, and each POST is answered with one plain JSON body: no
    session ids, and - because the gate refuses GET - no server-to-client stream is ever opened. So it works across
    workers and restarts. The gate + the SDK's Starlette app are ONE route at exactly /mcp (not a catch-all Mount),
    and the host app's lifespan is wrapped so the SDK's session manager runs for the life of the process (a
    sub-app's own lifespan never runs).
"""
import contextlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

import config
import cost_tools
import free_mode
import lookup
import xano

TOOLS = ("get_my_wedding", "get_my_profile", "list_saved_venues", "lookup_venue", "estimate_cost")
PATH = "/mcp"
SURFACE = "mcp"
SCOPE_KEY = "tulle_mcp_caller"     # where the gate leaves (token, user) for the tool wrappers
LOCKED_NOTE = "Prices are not included on this account: exact pricing is in each vendor's PDF on Tulle and on Tulle plans."


class MCPDenied(Exception):
    """A refusal or failure the caller is told about in words. No data goes with it. status = the gate's HTTP code."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _app():
    """The FastAPI module, imported late: app.py imports this module at its very end (flag on), and the helpers
    reused here (_auth, _find_and_price, _lookup_card, _wedding_view, _submit_usage) live there."""
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
    if uid is None or uid not in config.MCP_USER_IDS:
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


def _card(card, full):
    """One vendor card. FULL: as the chat shows it. Anyone else: free_mode.card - the chat's whitelist for free couples
    (no price keys at all, price sentences cut from the description). Images and UI-only keys left out."""
    c = {k: v for k, v in (card or {}).items() if k not in ("image", "google_name", "lat", "lng")}
    if not full:
        c = free_mode.card(c)
    if not c.get("reason"):
        c.pop("reason", None)
    return c


# ---------------------------------------------------------------- the five tools (plain functions; build() wraps them)

def get_my_wedding(authorization, who=None):
    """The couple's "My wedding" screen - GET /rec/wedding's data (app._wedding_view). Not FULL: the "about $X" hints
    on saved vendors (Tulle's pricing data) are blanked; what the couple typed in (budget, booked costs, quotes) stays."""
    def go(token, user, access, full):
        v = _app()._wedding_view(token, user)
        if not full and isinstance(v, dict) and v.get("categories"):
            v = dict(v, show_prices=False, categories=[
                dict(c, saved=[dict(x, price=None) for x in c.get("saved") or []]) for c in v["categories"]])
        return v
    return _run("get_my_wedding", authorization, go, who)


def get_my_profile(authorization, who=None):
    """The couple's wedding profile, plus which plan level this answer was built for."""
    def go(token, user, access, full):
        return {"profile": xano.profile_of(user), "plan": _tier(access, full), "price_locked": not full}
    return _run("get_my_profile", authorization, go, who)


def list_saved_venues(authorization, who=None):
    """The vendors the couple saved (user.favorited_vendors), as cards."""
    def go(token, user, access, full):
        ids = xano.saved_vendor_ids(user)
        cards = []
        if ids:
            with ThreadPoolExecutor(max_workers=8) as ex:
                cards = [c for c in ex.map(lambda v: cost_tools._safe(xano.vendor_card, v), ids) if c]
        out = {"saved_count": len(ids), "venues": [_card(c, full) for c in cards], "price_locked": not full}
        if not full:
            out["note"] = LOCKED_NOTE
        return out
    return _run("list_saved_venues", authorization, go, who)


def _query(name, location=""):
    """lookup.detect's dict for an explicit lookup. detect() is the Planner's parser ("Bacchus, Brooklyn", "The Mill
    nj"); when it reads the text as something other than a name, the words are searched as a name anyway."""
    text = "%s, %s" % (name, location) if location else name
    q = lookup.detect(text) or {}
    if not q.get("name"):
        q = {"name": name, "location": location, "alt_name": "", "alt_location": "",
             "words": len(lookup.norm(name).split()), "category": ""}
    return dict(q, cue=True, after_question=False)


def lookup_venue(authorization, name, location="", who=None):
    """One venue (or other vendor) by name. Pricing comes from xano.venue_pricing(token, id, full) inside
    app._find_and_price, so Xano's paywall decides what the panel holds; rule 4 decides what leaves here."""
    name = " ".join(str(name or "").split())[:120]
    location = " ".join(str(location or "").split())[:80]

    def go(token, user, access, full):
        import agent
        a = _app()
        if len(lookup.norm(name)) < 3:
            raise MCPDenied("Give the venue's name (at least 3 letters).")
        q = _query(name, location)
        profile = xano.profile_of(user)
        try:
            prefer = agent._states_of(profile)
        except Exception:
            prefer = []
        res = a._find_and_price(q, token, full, prefer)
        status = res.get("status") or "not_found"
        matches = list(res.get("matches") or [])[:3]
        out = {"status": status, "query": name, "plan": _tier(access, full), "price_locked": not full, "matches": []}
        if not matches or status == "not_found":
            out.update(status="not_found", note="Tulle has no venue by that name. Check the spelling or add the city.")
            return out
        if (res.get("vendor_kind") or "Venue") != "Venue":       # photographer, florist, ...: a listing, no panel
            out["matches"] = [_card(a._vendor_lookup_card(m), full) for m in matches]
            return out

        def num(k):
            try:
                return int(profile.get(k) or 0)
            except (TypeError, ValueError):
                return 0
        guests, budget = num("Wedding_Guest_Count"), num("Wedding_Budget")
        pricing = res.get("pricing") or {}
        if status in ("found", "best"):
            card, p = a._lookup_card(matches[0], pricing.get(matches[0]["vendor_id"]), full, guests, budget)
            out["matches"] = [_card(card, full)]
            others = [{"name": o.get("name"), "place": a._place_label(o)} for o in matches[1:] if o.get("name")]
            if others:
                out["also"] = others
            if full and p:
                out["pricing"] = {k: p.get(k) for k in ("headline", "guest_minimum", "verdict", "lines", "source_note")}
        else:                                      # choices / unsure: every match, the caller picks
            out["matches"] = [_card(a._lookup_card(m, pricing.get(m["vendor_id"]), full, guests, budget)[0], full)
                              for m in matches]
        if not full:
            out["note"] = LOCKED_NOTE
        return out
    return _run("lookup_venue", authorization, go, who)


def estimate_cost(authorization, state="", guests=0, major_city=None, peak_season=None, budget=0, who=None):
    """The "Estimate my cost" quick action: a typical whole-wedding cost for a state and guest count (market averages,
    not vendor prices). Arguments left out come from the profile, as on POST /rec/tool."""
    args = {"state": state, "guests": guests, "budget": budget}
    if major_city is not None:
        args["major_city"] = bool(major_city)
    if peak_season is not None:
        args["peak_season"] = bool(peak_season)

    def go(token, user, access, full):
        res = cost_tools.run_tool("estimate_cost", args, token, user, access=access, xano=xano)
        w = res["widget"] if access else free_mode.widget(res["widget"])
        inputs = {k: w.get(k) for k in ("state", "guests", "major_city", "peak_season")}
        if not full:
            # Not FULL: say what was computed and what is missing, never the amounts (agent._cost_tool's LIGHT/FREE rule).
            return {"tool": "estimate_cost", "shown": w.get("type"), "inputs": inputs,
                    "missing": w.get("missing") or [], "price_locked": True,
                    "note": "The figures are not included on this account. The couple can see this estimate as a chart "
                            "in the Tulle Planner (Estimate my cost)."}
        return {"tool": "estimate_cost", "text": cost_tools.summary_text(w), "estimate": w, "price_locked": False}
    return _run("estimate_cost", authorization, go, who)


# ---------------------------------------------------------------- the MCP server (SDK imported only here)

INSTRUCTIONS = ("Read-only access to one couple's Tulle Together wedding planning data: their profile, their My wedding "
                "screen, their saved vendors, a venue lookup by name and a typical-cost estimate. When a result says "
                "price_locked, do not state or guess prices for it.")


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
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    def call(fn, ctx, *args):
        headers = ctx.headers or {}
        scope = getattr(ctx.request_context.request, "scope", None) or {}
        try:
            return fn(headers.get("authorization"), *args, who=scope.get(SCOPE_KEY))
        except MCPDenied as e:
            raise ToolError(str(e))

    @server.tool(name="get_my_wedding", annotations=ro, structured_output=True,
                 description="The couple's My wedding screen: budget, what is booked, what is left, saved vendors and "
                             "quotes by category.")
    def _wedding(ctx: Context) -> dict[str, Any]:
        return call(get_my_wedding, ctx)

    @server.tool(name="get_my_profile", annotations=ro, structured_output=True,
                 description="The couple's wedding profile: location, guest count, budget, date, style.")
    def _profile(ctx: Context) -> dict[str, Any]:
        return call(get_my_profile, ctx)

    @server.tool(name="list_saved_venues", annotations=ro, structured_output=True,
                 description="The venues and other vendors the couple saved on Tulle.")
    def _saved(ctx: Context) -> dict[str, Any]:
        return call(list_saved_venues, ctx)

    @server.tool(name="lookup_venue", annotations=ro, structured_output=True,
                 description="Look up one wedding venue (or other vendor) on Tulle by name. location is optional "
                             "(city or state) and helps when several venues share a name.")
    def _lookup(name: str, ctx: Context, location: str = "") -> dict[str, Any]:
        return call(lookup_venue, ctx, name, location)

    @server.tool(name="estimate_cost", annotations=ro, structured_output=True,
                 description="A typical whole-wedding cost for a US state and guest count (market averages, not a "
                             "vendor's prices). Anything left out comes from the couple's profile.")
    def _estimate(ctx: Context, state: str = "", guests: int = 0, major_city: bool | None = None,
                  peak_season: bool | None = None, budget: int = 0) -> dict[str, Any]:
        return call(estimate_cost, ctx, state, guests, major_city, peak_season, budget)

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

    def __init__(self, inner, allowed_hosts):
        self.inner = inner
        self.allowed_hosts = list(allowed_hosts)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.inner(scope, receive, send)
        h = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        if scope.get("method") != "POST":
            return await self._refuse(send, 405, "The Tulle MCP endpoint takes POST only.", [(b"allow", b"POST")])
        if "origin" in h:
            return await self._refuse(send, 403, "The Tulle MCP endpoint does not accept requests from web pages.")
        if not _host_ok(h.get("host"), self.allowed_hosts):
            return await self._refuse(send, 421, "The Tulle MCP endpoint is not served on this host.")
        import anyio
        try:
            who = await anyio.to_thread.run_sync(caller, h.get("authorization"))
        except MCPDenied as e:
            extra = [(b"www-authenticate", b"Bearer")] if e.status == 401 else []
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
    """The SDK's own Host / Origin check, as a second line behind the gate (no Origin is allowed)."""
    from mcp.server.transport_security import TransportSecuritySettings
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=list(hosts) + [h + ":*" for h in hosts if ":" not in h])


def mount(app):
    """Add POST /mcp to an existing FastAPI app and run the SDK's session manager inside the app's lifespan
    (startup handlers registered with @app.on_event keep running: the previous lifespan is wrapped, not replaced).
    Without REC_MCP_ALLOWED_HOSTS nothing is mounted (returns None) and the SDK is not imported."""
    hosts = list(config.MCP_ALLOWED_HOSTS)
    if not hosts:
        print("MCP: REC_MCP=1 but REC_MCP_ALLOWED_HOSTS is empty - /mcp is NOT mounted. "
              "Set REC_MCP_ALLOWED_HOSTS to the host name(s) this service is reached on.")
        return None
    from starlette.routing import Route

    server = build()
    sub = server.streamable_http_app(streamable_http_path=PATH, stateless_http=True, json_response=True,
                                     transport_security=_security(hosts))
    app.router.routes.append(Route(PATH, endpoint=_Gate(sub, hosts)))     # gate + the SDK's ASGI app, at exactly /mcp
    previous = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async with previous(a) as state:
            async with server.session_manager.run():
                yield state

    app.router.lifespan_context = lifespan
    return server
