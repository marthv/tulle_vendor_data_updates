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
    REC_MCP_USER_IDS=7,42     the only Xano user ids that may call a tool. Default empty = nobody.
    REC_MCP_ALLOWED_HOSTS     optional: Host headers the transport answers to (e.g. the Railway domain). Empty = no
                              Host check (see config.py for why that is acceptable behind the proxy).

AUTH AND GUARDS (every tool call)
    1. `Authorization: Bearer <the couple's Xano user token>` on the MCP HTTP request, checked by app._auth - the
       same function every /rec route uses (xano.verify_user -> GET auth/me). The header is a credential that Xano
       validates, never an identity claim we take on trust.
    2. The user's id must be in REC_MCP_USER_IDS.
    3. The kill switch (REC_KILL_SWITCH=1) refuses every call, as POST /rec/tool does.
    4. Price rules, by tier (xano.has_paid_access / xano.beta_member, the tiers the chat uses):
         full member    figures as the chat shows them (Xano's ep230 still decides what the panel holds)
         plan, not full the chat's LIGHT card (a price band, no exact figures)
         no plan        NO price figures: cards go through free_mode.card (the chat's free whitelist), no pricing
                        panel, and estimate_cost returns what was computed for, not the amounts - the same
                        "no figures for the model" rule the chat applies to cost tools (agent._cost_tool).
       This is deliberately stricter than the app for a couple with no plan (the Planner page draws the estimate
       chart for them, and REC_FREE_PRICING=light shows them price bands): an MCP answer is read by a model we do
       not control, so nothing price-like goes out.
    5. Each call writes one rec_usage row (table 79): kind "mcp_<tool>", surface "mcp", model "none", cost 0.
       guards._prompts leaves "mcp_" rows out, so a call never spends a question or a daily / monthly prompt.
       A failed log write never fails the tool.

CONNECT FROM CLAUDE CODE (syntax checked against code.claude.com/docs/en/mcp on 2026-11-15)
    claude mcp add --transport http tulle https://<host>/mcp --header "Authorization: Bearer <token>"
    <token> is the couple's own Tulle sign-in token; it expires like any app session.

TRANSPORT
    Official Python SDK (`mcp`, v2: MCPServer). Stateless + plain JSON responses: no session ids, no server-to-client
    stream, so it works across workers and restarts. The SDK's Starlette app is added as ONE route at exactly /mcp
    (not a catch-all Mount), and the host app's lifespan is wrapped so the SDK's session manager runs for the life
    of the process (a sub-app's own lifespan never runs).
"""
import contextlib
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
LOCKED_NOTE = "Prices are not included on this account: exact pricing is in each vendor's PDF on Tulle and on Tulle plans."


class MCPDenied(Exception):
    """A refusal or failure the caller is told about in words. No data goes with it."""


def _app():
    """The FastAPI module, imported late: app.py imports this module at its very end (flag on), and the helpers
    reused here (_auth, _find_and_price, _lookup_card, _wedding_view, _submit_usage) live there."""
    import app
    return app


# ---------------------------------------------------------------- auth + guards + logging

def caller(authorization):
    """(token, user) for an allowed caller, else MCPDenied. Order: signed in, on the allowlist, kill switch."""
    try:
        token, user = _app()._auth(authorization)
    except HTTPException:
        raise MCPDenied("Not signed in: send the couple's Tulle token as 'Authorization: Bearer <token>'.")
    try:
        uid = int(user.get("id"))
    except (TypeError, ValueError):
        uid = None
    if uid is None or uid not in config.MCP_USER_IDS:
        raise MCPDenied("The Tulle MCP server is a private prototype and is not enabled for this account.")
    if config.KILL_SWITCH:
        raise MCPDenied("Tulle's planner is switched off right now. Try again later.")
    return token, user


def _log(row):
    """One rec_usage row on the app's pool. Never raises: logging must not break a tool."""
    try:
        _app()._submit_usage(dict(row, surface=SURFACE))
    except Exception as e:
        print("mcp usage log failed:", repr(e)[:200])


def _run(tool, authorization, fn):
    """Auth + guards, then fn(token, user, access, full) with one usage row either way."""
    token, user = caller(authorization)
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


def _card(card, access, full, light_done=False):
    """One vendor card at the caller's price level (module docstring, rule 4). Images and UI-only keys left out."""
    c = {k: v for k, v in (card or {}).items() if k not in ("image", "google_name", "lat", "lng")}
    if not access:
        c = free_mode.card(c)
    elif not full and not light_done:
        import agent
        c = agent._light_card(c)
    if not c.get("reason"):
        c.pop("reason", None)
    return c


# ---------------------------------------------------------------- the five tools (plain functions; build() wraps them)

def get_my_wedding(authorization):
    """The couple's "My wedding" screen - exactly GET /rec/wedding's data (app._wedding_view)."""
    def go(token, user, access, full):
        return _app()._wedding_view(token, user)
    return _run("get_my_wedding", authorization, go)


def get_my_profile(authorization):
    """The couple's wedding profile, plus which plan level this answer was built for."""
    def go(token, user, access, full):
        return {"profile": xano.profile_of(user), "plan": _tier(access, full), "price_locked": not access}
    return _run("get_my_profile", authorization, go)


def list_saved_venues(authorization):
    """The vendors the couple saved (user.favorited_vendors), as cards."""
    def go(token, user, access, full):
        ids = xano.saved_vendor_ids(user)
        cards = []
        if ids:
            with ThreadPoolExecutor(max_workers=8) as ex:
                cards = [c for c in ex.map(lambda v: cost_tools._safe(xano.vendor_card, v), ids) if c]
        out = {"saved_count": len(ids), "venues": [_card(c, access, full) for c in cards], "price_locked": not access}
        if not access:
            out["note"] = LOCKED_NOTE
        return out
    return _run("list_saved_venues", authorization, go)


def _query(name, location=""):
    """lookup.detect's dict for an explicit lookup. detect() is the Planner's parser ("Bacchus, Brooklyn", "The Mill
    nj"); when it reads the text as something other than a name, the words are searched as a name anyway."""
    text = "%s, %s" % (name, location) if location else name
    q = lookup.detect(text) or {}
    if not q.get("name"):
        q = {"name": name, "location": location, "alt_name": "", "alt_location": "",
             "words": len(lookup.norm(name).split()), "category": ""}
    return dict(q, cue=True, after_question=False)


def lookup_venue(authorization, name, location=""):
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
        out = {"status": status, "query": name, "plan": _tier(access, full), "price_locked": not access, "matches": []}
        if not matches or status == "not_found":
            out.update(status="not_found", note="Tulle has no venue by that name. Check the spelling or add the city.")
            return out
        if (res.get("vendor_kind") or "Venue") != "Venue":       # photographer, florist, ...: a listing, no panel
            out["matches"] = [_card(a._vendor_lookup_card(m), access, full) for m in matches]
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
            out["matches"] = [_card(card, access, full, light_done=True)]
            others = [{"name": o.get("name"), "place": a._place_label(o)} for o in matches[1:] if o.get("name")]
            if others:
                out["also"] = others
            verdict = (p or {}).get("verdict") or ""
            if full and p:
                out["pricing"] = {k: p.get(k) for k in ("headline", "guest_minimum", "verdict", "lines", "source_note")}
            elif access and verdict and not agent._DIGITS.search(verdict):
                out["verdict"] = verdict          # the chat's LIGHT rule: a verdict only when it quotes no figure
        else:                                      # choices / unsure: every match, the caller picks
            out["matches"] = [_card(a._lookup_card(m, pricing.get(m["vendor_id"]), full, guests, budget)[0],
                                    access, full, light_done=True) for m in matches]
        if not access:
            out["note"] = LOCKED_NOTE
        return out
    return _run("lookup_venue", authorization, go)


def estimate_cost(authorization, state="", guests=0, major_city=None, peak_season=None, budget=0):
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
        if not access:
            # No plan: say what was computed and what is missing, never the amounts (agent._cost_tool's LIGHT/FREE rule).
            return {"tool": "estimate_cost", "shown": w.get("type"), "inputs": inputs,
                    "missing": w.get("missing") or [], "price_locked": True,
                    "note": "The figures are not included on this account. The couple can see this estimate as a chart "
                            "in the Tulle Planner (Estimate my cost)."}
        return {"tool": "estimate_cost", "text": cost_tools.summary_text(w), "estimate": w, "price_locked": False}
    return _run("estimate_cost", authorization, go)


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

    def call(fn, ctx, *args, **kwargs):
        headers = ctx.headers or {}
        try:
            return fn(headers.get("authorization"), *args, **kwargs)
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


def _security():
    from mcp.server.transport_security import TransportSecuritySettings
    hosts = list(config.MCP_ALLOWED_HOSTS)
    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=hosts + [h + ":*" for h in hosts if ":" not in h])


def mount(app):
    """Add POST/GET /mcp to an existing FastAPI app and run the SDK's session manager inside the app's lifespan
    (startup handlers registered with @app.on_event keep running: the previous lifespan is wrapped, not replaced)."""
    from starlette.routing import Route

    server = build()
    sub = server.streamable_http_app(streamable_http_path=PATH, stateless_http=True, json_response=True,
                                     transport_security=_security())
    app.router.routes.append(Route(PATH, endpoint=sub))     # the SDK's own ASGI app, at exactly /mcp
    previous = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async with previous(a) as state:
            async with server.session_manager.run():
                yield state

    app.router.lifespan_context = lifespan
    return server
