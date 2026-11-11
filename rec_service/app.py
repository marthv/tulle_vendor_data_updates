"""Tulle recommendation service (Railway). WeWeb calls it with the user's Xano auth token.

POST /rec/opening          -> new conversation with opening picks; returns chat_id
                              (beta: Forever only - everyone else gets 402 forever_only)
POST /rec/refine           -> {chat_id, message}: one refinement in that conversation (FREE_REFINES free - 1 since 2026-10-14 - then 402)
                              no chat_id ({messages:[...]} or {message}): continues the user's chat from the
                              last 30 min, else starts one; turns saved, chat_id returned (2026-10-12)
                              A venue asked for by NAME ("oheka castle", "is X listed?") is answered from table 11
                              without the model (lookup.py, 2026-10-26): lookup {status, query} + venue_request
                              {name, location} when we don't have it (both null otherwise). REC_NAME_LOOKUP=0 = off.
GET  /rec/chats            -> the user's conversations, newest first
GET  /rec/chats/{chat_id}  -> one conversation's turns (text, cards, chips) to re-render it
POST /rec/more {chat_id}   -> "Show 3 more": next 3 venues from the latest answer's searches. Spends ONE
                              question exactly like a typed one (user 2026-10-07), same limits and errors.
GET  /rec/status           -> free refinements left, paid flag, whether the service is on, and the page-state
                              keys tier / price_locked / questions_left / feedback_required / feedback_done /
                              paywall (also on every answer and every blocked response)
POST /rec/tool             -> {tool, args, chat_id?}: one cost tool (estimate_cost | split_budget | compare_venues |
                              budget_status) for the chat's quick actions. No model call, never counts toward free
                              questions or the daily caps (owner 2026-10-25); its widget is saved in the chat if given.
                              Every answer (opening / refine / more) carries `widgets` ([] unless a cost tool ran).
POST /rec/opening|refine|more also take an optional `request_id` (client UUID, max 64 chars, 2026-10-25):
GET  /rec/progress/{id}    -> {state: running|done|stopped|unknown, label, step} for the page's "working on it" line
                              (owner only, else 404; unknown/expired -> state "unknown"; kept 10 min after finishing)
POST /rec/cancel/{id}      -> {ok: true}: Stop. The request ends at its next checkpoint (no further model or tool call)
                              and answers 200 {status: "stopped", stopped: true, text: "", cards: [], ...}: not counted
                              as a question, logged to rec_usage as status "stopped" with its real cost, nothing saved
                              to the chat (same as an error). See progress.py (in memory: one replica only).
POST /rec/feedback         -> beta check-in {rating, would_use, text}. Paid members: unlocks questions.
                              Free users (2026-10-14): required once after their free question(s); earns nothing more.

FREE MODE (owner decisions 2026-10-14, on when REC_FOREVER_ONLY=0): every signed-in user may use the assistant.
No active plan -> tier "free": opening + FREE_REFINES questions for life (1 since 2026-10-14), then one required feedback, then the paywall,
and NO price of any kind in text, cards or chips (free_mode.py; price_locked=true).
POST /rec/profile          -> {guests?, budget?, states?, date?}: the Planner chat's quick profile edit (2026-10-31).
                              Written through ep21 user/update_record with the couple's own token; returns the fresh
                              profile. Counts nothing.
GET  /health

Conversations live in Xano rec_chats (42) / rec_messages (43); durable notes the model saves
about the couple live in rec_memory (80) and are fed into every later conversation.
"""
from typing import Optional
import datetime as dt
import json
import time
import re
import threading

from concurrent.futures import ThreadPoolExecutor

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import agent
import anthropic
from chips import (STARTER_CHIPS, STARTER_CHIPS_COST_TOOLS, CHIP_TOPUPS, dedupe_chips, ensure_chips,
                   guarantee_next_step, more_chips, place_action_chip, tool_chips)
import config
import cost_tools
import free_mode
import guards
import lookup
import plan
import wedding
import notify
import opening_reuse
import re as _re

import prefs
import pricing_warm
import profile_questions
import progress
import railway_search
import sheet_log
import upsell
import xano

app = FastAPI(title="Tulle recommendations")


@app.on_event("startup")
def _start_pricing_warm():
    """Fill the free-tier pricing cache for the top venues per state in the background (pricing_warm.py, 2026-11-02)."""
    pricing_warm.start()
# The live app, plus WeWeb's editor/preview hosts so the page can be tested before publishing.
# Every request still needs a valid user token; CORS only decides which pages may call us.
app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS,
                   allow_origin_regex=r"https://([a-z0-9-]+\.)*weweb(-preview)?\.(io|app)",
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])



class _UserAgentToSheetLog:
    """Puts each request's User-Agent in sheet_log.UA, so Planner sheet rows get Device + Browser (owner 2026-11-03).
    Plain ASGI (not BaseHTTPMiddleware) so the value is seen by the endpoint and its background tasks."""

    def __init__(self, asgi):
        self.asgi = asgi

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.asgi(scope, receive, send)
        ua = ""
        for k, v in scope.get("headers") or []:
            if k == b"user-agent":
                ua = v.decode("latin-1")[:500]
                break
        surface = ""
        try:
            from urllib.parse import parse_qs
            q = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
            surface = str((q.get("surface") or [""])[0])[:80]
        except Exception:
            surface = ""
        tok = sheet_log.UA.set(ua)
        stok = xano.SURFACE.set(surface)
        try:
            await self.asgi(scope, receive, send)
        finally:
            sheet_log.UA.reset(tok)
            xano.SURFACE.reset(stok)


app.add_middleware(_UserAgentToSheetLog)

OPENING_ASK = ("Recommend 3 venues for us based on our profile, and tell us the single most useful next step "
               "for where we are in planning.")
HISTORY_TURNS = 8
_POOL = ThreadPoolExecutor(max_workers=8)


def _turn_for_model(t):
    """What the model reads back for a stored turn. Assistant turns carry the venues that were shown
    (name + vendor_id), so 'which of those is cheapest?' works across turns and across visits."""
    text = t["text"] or ""
    if t["role"] == "assistant" and t["cards"]:
        shown = "; ".join("%s (%s)" % (c.get("name"), c.get("vendor_id")) for c in t["cards"])
        text = (text + "\n[Vendors shown: " + shown + "]").strip()
    if t["role"] == "assistant" and t.get("chips"):
        # The prompt says "don't repeat a chip from earlier in the chat" - the model can only follow it
        # if it sees them (2026-10-04: user 31797 got "Is this a good price for California?" 5 times).
        text = (text + "\n[Chips offered: " + "; ".join(t["chips"]) + "]").strip()
    return {"role": t["role"], "content": text or "(recommendations shown)"}


def _more_remaining(turns):
    """Unrevealed "Show 3 more" venues of the LATEST answer (an opening or typed question - a turn written by
    /rec/more is not a new answer). Anything shown after that answer is excluded."""
    src = max((i for i, t in enumerate(turns) if t["role"] == "assistant" and not t.get("is_more")), default=None)
    if src is None:
        return []
    shown = {c.get("vendor_id") for t in turns[src + 1:] for c in (t.get("cards") or [])}
    return [c for c in (turns[src].get("more_cards") or []) if c.get("vendor_id") not in shown]


def _more_left(turns):
    n = len(_more_remaining(turns))
    return n if n >= agent.MORE_STEP else 0


_Q_TAIL = re.compile(r"(?:^|(?<=[.!?])\s+)([^.!?]*\?)\s*$")


def strip_trailing_question(text):
    """Drop the closing question(s) of a reply when real content remains before them (open-ended mode)."""
    t = (text or "").rstrip()
    for _ in range(2):
        m = _Q_TAIL.search(t)
        if not m or len(t[:m.start(1)].strip()) < 40:
            break
        t = t[:m.start(1)].rstrip()
    return t


def _open_ended(out):
    """OPEN_ENDED (owner 2026-11-07): no chips, no questions, no closing question. A lookup that found several venues
    keeps its name chips - those are the answer, not a nudge."""
    if not config.OPEN_ENDED or not isinstance(out, dict):
        return out
    if ((out.get("lookup") or {}).get("status")) != "best":    # "best" offers the other names; nothing else keeps chips
        out["chips"] = []
        out["text"] = strip_trailing_question(out.get("text") or "")
    out["questions"] = []
    return out


def _public(out, **extra):
    """What the page gets: the hidden extras become a count (they cost a question to reveal); the search
    filters stay server-side (stored with the answer for "Show 3 more" and the next question)."""
    resp = {k: v for k, v in out.items() if k not in ("more_cards", "search_filters")}
    # Venue name lookup (2026-10-26): lookup = {status, query} on a fast-path answer, venue_request = {name, location}
    # when we don't have the venue (the page can offer "Request this venue"). Both null on every other answer.
    resp.setdefault("lookup", None)
    resp.setdefault("venue_request", None)
    resp.setdefault("sources", [])
    resp.setdefault("plan_enabled", False)   # "My wedding" A/B arm (2026-11-07); the page also reads /rec/status
    resp.setdefault("plan_next", None)   # 2026-11-07: where the answer's facts came from ([] when none)
    n = len(out.get("more_cards") or [])
    # widgets (cost tools, 2026-10-25): always present, [] by default, so the current page is unaffected.
    # questions / upsell_prompt (2026-10-26): always present ([] / null by default), so the page can bind them.
    return dict(resp, more_available=n if n >= agent.MORE_STEP else 0, widgets=list(out.get("widgets") or []),
                questions=list(out.get("questions") or []), upsell_prompt=out.get("upsell_prompt"), **extra)


def _stored_cards(out):
    """Picks + "Show 3 more" cards in one cards column; xano.get_messages splits them on read. The answer's
    search filters ride along as one hidden {"meta": "search"} entry (no schema change; parse_message_row
    takes it out again, so no reader of get_messages ever sees it as a card)."""
    stored = list(out.get("cards") or []) + [dict(c, more=True) for c in (out.get("more_cards") or [])]
    if out.get("search_filters"):
        stored.append({"meta": "search", "filters": out["search_filters"]})
    # Cost-tool widgets ride along the same way (2026-10-25), so a reopened chat redraws them.
    stored += [{"meta": "widget", "widget": w} for w in out.get("widgets") or []]
    # Thin-profile questions (so the next turn can save the couple's answer) and the upgrade prompt (once per
    # chat), 2026-10-26 - hidden entries the same way.
    if out.get("questions"):
        stored.append({"meta": "questions", "questions": out["questions"]})
    if out.get("upsell_prompt"):
        stored.append({"meta": "upsell", "reason": out["upsell_prompt"].get("reason")})
    if out.get("sources"):     # 2026-11-07: a reopened chat shows the same sources under the answer
        stored.append({"meta": "sources", "sources": out["sources"]})
    return stored


def _active_filters(history):
    """The filters of the chat's latest search: the newest assistant turn that stored some."""
    for t in reversed(history or []):
        if t.get("role") == "assistant" and t.get("filters"):
            return dict(t["filters"])
    return {}


def _model_inputs(history, text):
    """(messages, agent.run kwargs) for a typed question in a stored conversation."""
    # The stored conversation starts with the opening picks (an assistant turn), so restate the
    # opening ask in front of it: the model needs a user turn first.
    # (A chat started from a typed question already begins with a user turn.)
    msgs = [{"role": "user", "content": OPENING_ASK}] if history and history[0]["role"] == "assistant" else []
    msgs += [_turn_for_model(t) for t in history]
    msgs = msgs[-(HISTORY_TURNS - 1):] if len(msgs) >= HISTORY_TURNS else msgs
    if msgs and msgs[0]["role"] != "user":
        msgs = msgs[1:]
    msgs.append({"role": "user", "content": text})
    # Venues this chat already showed (our own stored cards, not model output) may be shown again as
    # cards - e.g. "break down <venue from the opening>" (2026-10-05: chat 24's breakdown had no card) - and
    # are left out of new searches unless asked for (2026-10-14).
    prior = [c.get("vendor_id") for t in history if t["role"] == "assistant"
             for c in (t.get("cards") or []) + (t.get("more_cards") or [])]
    return msgs, {"prior_vendor_ids": [v for v in prior if v], "active_filters": _active_filters(history)}


class Turn(BaseModel):
    role: str
    content: str


class RefineBody(BaseModel):
    chat_id: int = 0
    message: str = ""
    messages: list[Turn] = []     # legacy shape: prior turns + the new user message last
    request_id: str = Field("", max_length=progress.MAX_ID_LEN)   # optional: progress + Stop (2026-10-25)
    quote: Optional[dict] = None  # 11-08: a vendor quote the couple uploaded, parsed by ep157 (Forever only)


QUOTE_KEYS = ("document_type", "summary", "vendor_name", "category", "total_cost", "package_name", "deposit_or_paid",
              "included_sum", "notes", "contact_information")


def quote_block(q, file_name=""):
    """The model-only context for 'is this a good deal?' (owner 2026-11-08). Parsed figures are the couple's own
    quote, not Tulle data; the stored chat keeps only the short question."""
    if not isinstance(q, dict):
        return ""
    d = {k: q.get(k) for k in QUOTE_KEYS if q.get(k) not in (None, "", [], {})}
    lines = []
    for li in (q.get("line_items") or [])[:30]:
        if isinstance(li, dict):
            lines.append({k: li.get(k) for k in ("description", "item", "name", "amount", "price", "quantity", "included", "optional")
                          if li.get(k) not in (None, "")})
    if lines:
        d["line_items"] = lines
    sched = [x for x in (q.get("payment_schedule") or [])[:8] if isinstance(x, (dict, str))]
    if sched:
        d["payment_schedule"] = sched
    blob = json.dumps(d, default=str)[:4000]
    return ("\n\n[FILE THE COUPLE ATTACHED%s - a PDF or screenshot, read by Tulle. Its figures are THEIR document, not Tulle "
            "data.]\n%s\n[HOW TO ANSWER] Answer the question they asked about this file - it may be a quote, a contract, a "
            "menu, an invoice, a floor plan or a message from a vendor, and they are not always asking whether it is a good "
            "deal. Use what the file says. ONLY if they ask about price, value or whether to book: compare with Tulle's data - "
            "look the vendor up by name if Tulle lists it, market_benchmarks / State benchmarks for their state and guest count "
            "for a venue, starting_price_vs_state for photographers and entertainment, estimate_cost for an all-in what-if - "
            "and give a plain verdict with the one or two numbers that show it. Point out anything notable (fees, minimums, "
            "deadlines, what's missing) when it helps answer them. Quote figures only from the file or your tools. If the "
            "file doesn't contain what they asked, say so.") % (
        (' ("%s")' % file_name[:120]) if file_name else "", blob)


class AttachmentBody(BaseModel):
    file: Optional[dict] = None     # attachment object from the app's POST /upload/attachment


ATTACH_PROMPT = ("This image was attached by a couple planning their wedding (a screenshot or photo). Read it and return ONLY "
                 "a JSON object, no markdown: {\"document_type\": \"quote|contract|invoice|menu|price_list|message|floor_plan|other\", "
                 "\"summary\": \"2-4 plain sentences on what it shows, with any prices, dates and names exactly as written\", "
                 "\"vendor_name\": \"business name or empty\", \"category\": \"Venue|Food and Beverage|Photography|Entertainment|"
                 "Flowers|Wedding Planner|Beauty|Attire|Rentals|Other or empty\", \"total_cost\": 0, \"package_name\": \"\", "
                 "\"deposit_or_paid\": 0, \"line_items\": [{\"description\": \"\", \"amount\": 0}], \"notes\": \"key terms or "
                 "deadlines, max 200 chars\"}. Use 0 or empty when a value isn't shown. Never invent a figure.")


@app.post("/rec/attachment/read")
def read_attachment(body: AttachmentBody, authorization: str = Header(None)):
    """11-08 (owner): Forever couples paste a screenshot (or attach an image) in the Planner; this reads it into the same
    shape parse-vendor-pdf returns for PDFs, plus a summary. PDFs keep going through ep157. Counts no question."""
    _, user = _auth(authorization)
    if not xano.has_forever(user):
        raise HTTPException(403, {"status": "attachment_forever_only"})
    att = wedding.clean_attachment(body.file)
    mime = str((body.file or {}).get("mime") or "").lower()
    if not att or not mime.startswith("image/"):
        raise HTTPException(400, {"status": "image_required"})
    url = wedding.XANO_FILE_BASE + att["path"]
    t0 = time.time()
    try:
        r = anthropic.Anthropic().messages.create(
            model=config.MODEL, max_tokens=1500, output_config={"effort": "low"},
            messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": url}},
                                                   {"type": "text", "text": ATTACH_PROMPT}]}])
    except Exception as e:
        _POOL.submit(xano.mp_track, user["id"], "rec_attachment_read", {"ok": False, "error": str(e)[:120]})
        raise HTTPException(502, {"status": "read_failed"})
    txt = next((b.text for b in r.content if getattr(b, "type", "") == "text"), "") or ""
    m = re.search(r"\{.*\}", txt, re.S)
    try:
        out = json.loads(m.group(0)) if m else {}
    except Exception:
        out = {}
    if not isinstance(out, dict) or not out:
        out = {"document_type": "other", "summary": txt.strip()[:1500]}
    p = config.PRICES.get(config.MODEL) or config.PRICES["claude-sonnet-5-5"]
    cost = round(((r.usage.input_tokens or 0) * p["in"] + (r.usage.output_tokens or 0) * p["out"]) / 1e6, 6)
    _POOL.submit(xano.mp_track, user["id"], "rec_attachment_read", {"ok": True, "type": out.get("document_type"),
                 "cost_usd": cost, "latency_s": round(time.time() - t0, 1)})
    return out


class OpeningBody(BaseModel):
    request_id: str = Field("", max_length=progress.MAX_ID_LEN)


def _auth(authorization):
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = xano.verify_user(token)
    if not user or not user.get("id"):
        raise HTTPException(401, "sign in required")
    return token, user


def _memory_row(user_id):
    try:
        return xano.get_memory(user_id) or {}
    except Exception:
        return {}


def _memory(user_id):
    """(notes, user_context) for this user; ([], "") on any read problem - never block a request on it.
    notes = every visible memory line, preference answers included (the "knows about you" panel)."""
    row = _memory_row(user_id)
    return [n.get("note") for n in row.get("notes") or [] if n.get("note")], (row.get("user_context") or "")


def _feedback_given(user_id):
    """Fail OPEN: if the feedback table can't be read, never lock a paying customer out."""
    try:
        return xano.has_beta_feedback(user_id)
    except Exception:
        return True


def _memory_notes(user_id):
    return _memory(user_id)[0]


def _search_cols(summary):
    """rec_usage columns for the Railway search rollout (2026-10-26; added to table 79 in Xano)."""
    s = summary or {}
    return {"search_engine": s.get("search_engine") or "", "search_fallback": s.get("search_fallback") or "",
            "search_ms": int(s.get("search_ms") or 0)}


def _search_props(user, summary=None):
    """Mixpanel rec_server_request props for the rollout. search_arm = the user's bucket even if no search ran."""
    s = summary or {}
    return {"search_arm": s.get("search_arm") or railway_search.arm(user.get("id")),
            "search_engine": s.get("search_engine") or "", "search_fallback": s.get("search_fallback") or "",
            "railway_search_ms": int(s.get("railway_search_ms") or 0),
            "ep119_search_ms": int(s.get("ep119_search_ms") or 0),
            "search_fallbacks": int(s.get("search_fallbacks") or 0),
            # Circuit breaker (2026-10-27): searches skipped straight to ep119 while open, and its state now.
            "breaker_skips": int(s.get("breaker_skips") or 0),
            "breaker_state": s.get("breaker_state") or ("open" if railway_search.BREAKER.is_open() else "closed")}


def _tool_cols(usage):
    """rec_usage (table 79) columns added 2026-11-02: the tools an answer ran ('search_venues,present_recommendations'),
    the vendor categories they touched ('venue' / 'photography' / ...), and thinking characters."""
    u = usage or {}
    return {"tools": ",".join(u.get("tools_used") or [])[:500], "categories": ",".join(u.get("categories") or [])[:200],
            "think_chars": int(u.get("think_chars") or 0)}


def _turn_props(usage):
    """rec_server_request props (2026-10-26 speed pass): each model call's wall ms and the tools it asked for, so
    turn 1 (which only picks search_venues args) can be weighed; and the cross-request pricing cache's counts."""
    u = usage or {}
    ms = [int(x or 0) for x in (u.get("turn_ms") or [])]
    pc = u.get("pricing_cache") or {}
    return {"model_turns": len(u.get("turns") or ms), "turn_ms": ms,
            "turn1_model_ms": ms[0] if ms else None, "turn2_model_ms": ms[1] if len(ms) > 1 else None,
            "turn1_tools": (u.get("turn_tools") or [[]])[0] if ms else [],
            "turn_ms_has_tools": bool(u.get("turn_ms_has_tools")),
            "pricing_cache_hits": int(pc.get("hits") or 0), "pricing_cache_misses": int(pc.get("misses") or 0),
            "pricing_cache_bypass": int(pc.get("bypass") or 0),
            # Pre-search (2026-11-01): the opening's first search ran before the model; its ms and venue count.
            "presearch": bool(u.get("presearch")), "presearch_ms": u.get("presearch_ms"),
            "presearch_n": u.get("presearch_n"),
            # Refine router (2026-11-07, REC_REFINE_ROUTER): whether it ran, searched, and its own ms.
            "routed": bool(u.get("routed")), "router_searched": bool(u.get("router_searched")),
            "router_ms": u.get("router_ms"),
            # Prefetch split + caches (2026-10-27): which prefetch part costs time, and how often the caches hit.
            **_prefetch_props(u)}


def _prefetch_props(usage):
    u = usage or {}
    pc = u.get("prefetch_cache") or {}
    return {"prefetch_bench_ms": u.get("prefetch_bench_ms"), "prefetch_saved_ms": u.get("prefetch_saved_ms"),
            "prefetch_saved_n": u.get("prefetch_saved_n"),
            "bench_cache_hits": int(pc.get("bench_hits") or 0), "bench_cache_misses": int(pc.get("bench_misses") or 0),
            "card_cache_hits": int(pc.get("card_hits") or 0), "card_cache_misses": int(pc.get("card_misses") or 0)}


def _gate(kind, user, chat_id=0, waive_free=False, row_kind=None):
    """The one question check. Raises the blocked response (402/403/429/503) or returns what the caller
    needs to log the use and report the counters. Used by typed questions AND "Show 3 more".
    waive_free (2026-10-26): the message answers one of OUR thin-profile questions, so it must not spend one of a
    free user's questions (or be refused for having none left). Paid caps, spend caps and the kill switch still apply.
    row_kind (2026-11-02): the usage-row / Mixpanel kind when it differs from the rule kind - a prewarm is gated as an
    opening (the same caps and kill switch) but logged as "prewarm" and never counted as a free question."""
    # `paid` = full beta member: Forever, plus every active plan when REC_BETA_AUDIENCE=paid (2026-10-08).
    # `access` (exact prices from ep230) = any active plan.
    paid = xano.beta_member(user)
    access = xano.has_paid_access(user)
    f_rows = _POOL.submit(xano.user_usage, user["id"])
    f_spend = _POOL.submit(xano.spend_today)
    # Feedback matters for paid members (beta check-in) and, in free mode, for free users (required once).
    f_fb = _POOL.submit(_feedback_given, user["id"]) if paid or not config.FOREVER_ONLY else None
    rows = f_rows.result()
    feedback_given = f_fb.result() if f_fb else True
    spend, free_spend = f_spend.result()
    allowed, status, counted_free = guards.decide(kind, paid, rows, spend, xano.today(), feedback_given,
                                                  free_spend_today=free_spend)
    if waive_free and not paid:
        if not allowed and status in ("blocked_free_limit", "feedback_required"):
            allowed, status = True, "ok"
        counted_free = False
    # tier is written for the free cap; Xano drops it until table 79 has a `tier` column (see config).
    if row_kind:
        counted_free = False
    base = {"user_id": user["id"], "kind": row_kind or kind, "chat_id": chat_id, "model": config.MODEL,
            "tier": "paid" if paid else "free"}
    if not allowed:
        xano.log_usage(dict(base, status=status))
        _POOL.submit(xano.mp_track, user["id"], "rec_server_request",
                     {"kind": row_kind or kind, "status": status, "forever": xano.has_forever(user), "beta_member": paid, "has_access": access, "source": "rec_service",
                      "search_arm": railway_search.arm(user.get("id"))})
        code = (402 if status in ("blocked_free_limit", "forever_only") else 403 if status == "feedback_required"
                else 503 if status in ("killed", "blocked_global_cap", "blocked_free_cap") else 429)
        extra = {}
        if status == "feedback_required" and paid:
            extra.update(beta_feedback_after=config.BETA_FEEDBACK_AFTER, questions_used=guards.forever_questions(rows))
        elif status == "feedback_required":
            extra.update(free_feedback_bonus=config.FREE_FEEDBACK_BONUS, questions_used=guards.free_used(rows))
        if status == "blocked_monthly_cap":
            t = dt.date.fromisoformat(xano.today())
            extra["resets_on"] = (dt.date(t.year + (t.month == 12), t.month % 12 + 1, 1)).isoformat()
        free_left = guards.free_refines_left(paid, rows, feedback_given)
        beta_left = guards.beta_questions_left(paid, rows, feedback_given)
        raise HTTPException(code, {"status": status, "upsell": "forever", "free_limit": config.FREE_REFINES, **extra,
                                   "free_refines_left": free_left,
                                   **_tier_fields(paid, access, feedback_given, beta_left,
                                                  guards.forever_left_today(paid, rows, xano.today()), free_left,
                                                  guards.free_feedback_required(paid, rows, feedback_given), rows)})
    return paid, access, rows, feedback_given, counted_free, base


def _tier_fields(paid, access, feedback_given, beta_left, forever_left, free_left, free_fb, rows=None):
    """Page-state keys (free mode, 2026-10-14) - the same on /rec/status, every answer and every blocked
    response, so the page renders from whichever it has last:
      tier             "paid" (full member) | "free"
      price_locked     true = no prices anywhere (no active plan); the page shows "Open pricing PDF" instead
      questions_left   free: lifetime questions left before feedback/paywall; paid: questions left before the
                       beta check-in if it is pending, else prompts left today
      feedback_required  free users: the next question is refused 403 until POST /rec/feedback.
                         paid members (2026-10-16): the beta check-in is due - the page shows the feedback
                         strip but must NOT lock the input (the server never blocks a paid question)
      feedback_suggested paid member at the check-in (same as feedback_required for paid; explicit flag)
      feedback_done    this user has left assistant feedback
      paywall          free user with nothing left and no feedback owed (or free mode off): next ask = 402"""
    fb_req = (beta_left == 0) if paid else bool(free_fb)   # paid: a prompt only, never enforced (10-16)
    left = forever_left if paid else free_left
    return {"tier": "paid" if paid else "free", "price_locked": not access, "questions_left": left,
            "feedback_required": fb_req, "feedback_done": bool(feedback_given),
            "feedback_suggested": bool(paid and beta_left == 0),
            # Open planner (2026-10-31): a free user who used today's allowance is capped (429), not paywalled.
            "paywall": (not paid) and (config.FOREVER_ONLY or (not config.FREE_DAILY_MODE and not fb_req and not left)),
            "free_daily_mode": config.FREE_DAILY_MODE,
            "free_daily_limit": config.FREE_DAILY_AFTER_INITIAL if config.FREE_DAILY_MODE else None,
            # 11-08 owner rule: "initial" = the first 3 free questions (page: "n free questions left"); "daily" = 1 a day after
            "free_phase": (guards.free_phase(rows) if (rows is not None and not paid) else None),
            "free_initial_limit": config.FREE_INITIAL_REFINES if config.FREE_DAILY_MODE else None,
            "free_feedback_required": bool(free_fb), "free_feedback_bonus": config.FREE_FEEDBACK_BONUS}


def _detail(paid, access):
    """What the model may see: "full" (full members) / "light" (a plan without full figures - and, since the open
    planner of 2026-10-31 with REC_FREE_PRICING=light, no plan at all) / "free" (no plan, price-free free_mode)."""
    if paid:
        return "full"
    if access or (config.FREE_DAILY_MODE and config.FREE_PRICING == "light"):
        return "light"
    return "free"


def _counters(kind, paid, rows, counted_free, feedback_given, access=True, spent=True, row_tools=""):
    """Allowance left AFTER this request, for the page's counters. spent=False: a stopped request (2026-10-25) -
    it used nothing, so the counters are the ones before it. row_tools: this request's usage-row tools value
    ("lookup:choices" / "lookup:not_found" do not spend a free question - guards.FREE_CLARIFY_TOOLS)."""
    used = rows + ([{"status": "ok", "kind": "refine", "counted_as_free": True, "tools": row_tools}]
                   if counted_free and spent else [])
    used_today = rows + ([{"status": "ok", "usage_day": xano.today()}] if spent else [])
    used_fq = rows + ([{"status": "ok", "kind": "refine"}] if paid and kind == "refine" and spent else [])
    c = dict(upsell="forever",
             beta_questions_left=guards.beta_questions_left(paid, used_fq, feedback_given),
             beta_feedback_after=config.BETA_FEEDBACK_AFTER,
             free_refines_left=guards.free_refines_left(paid, used, feedback_given),
             forever_left_today=guards.forever_left_today(paid, used_today, xano.today()),
             forever_daily_limit=config.PAID_DAILY_REFINES, free_limit=config.FREE_REFINES)
    c.update(_tier_fields(paid, access, feedback_given, c["beta_questions_left"], c["forever_left_today"],
                          c["free_refines_left"], guards.free_feedback_required(paid, used, feedback_given), used))
    return c


def _stopped(kind, paid, access, rows, feedback_given, chat_id=None):
    """The answer to a request the couple stopped (2026-10-25): every key the page reads on an answer, empty, plus
    the counters as they were before it (a stopped request is never counted)."""
    return dict(status="stopped", stopped=True, text="", cards=[], chips=[], widgets=[], more_available=0,
                questions=[], upsell_prompt=None,
                notes_saved=[], chat_id=chat_id, paid=paid, has_access=access,
                **_counters(kind, paid, rows, False, feedback_given, access, spent=False))


def _log_stopped(base, kind, user, paid, access, usage=None):
    """rec_usage row for a stopped request: status "stopped" (guards count only "ok" rows, so it never spends a
    free question or a daily prompt) with the cost actually incurred - the daily spend caps still see it."""
    u = usage or {}
    cost = guards.cost_usd(u["model"], u) if u.get("model") else 0
    search = u.get("search")
    _POOL.submit(xano.log_usage, dict(base, status="stopped", counted_as_free=False, model=u.get("model") or "none",
                                      input_tokens=u.get("input_tokens", 0),
                                      cache_read_tokens=u.get("cache_read_tokens", 0),
                                      cache_write_tokens=u.get("cache_write_tokens", 0),
                                      output_tokens=u.get("output_tokens", 0), tool_calls=u.get("tool_calls", 0),
                                      cost_usd=cost, latency_ms=u.get("latency_ms", 0), **_search_cols(search)))
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": kind, "status": "stopped", "forever": xano.has_forever(user), "beta_member": paid,
        "has_access": access, "source": "rec_service", "cost_usd": cost,
        "latency_s": round((u.get("latency_ms") or 0) / 1000, 1), "tool_calls": u.get("tool_calls", 0),
        **_turn_props(u), **_search_props(user, search)})


def _free_views(user):
    try:
        return int(user.get("FreeViewsRemaining"))
    except (TypeError, ValueError):
        return 0


def _run_guarded(kind, token, user, messages, chat_id=0, prior_ids=(), active_filters=None, tracker=None,
                 history=(), saved_lines=(), waive_free=False, prewarm=False):
    """history: the chat's stored turns (the upgrade prompt shows at most once per chat). saved_lines: memory lines
    this request already saved from the couple's answer to a thin-profile question (shown as notes_saved).
    prewarm (2026-11-02, POST /rec/prewarm): generate the opening into the reuse store only - gated like an opening,
    logged as kind "prewarm" (never counted toward any allowance); returns {"status": "ready"|"running"} when an
    opening for the same inputs is already stored or being generated."""
    t_req = time.time()
    f_mem = _POOL.submit(_memory_row, user["id"])
    in_plan = plan.arm(user["id"]) == "plan"
    f_plan = _POOL.submit(xano.plan_items, user["id"]) if in_plan else None   # "My wedding" (2026-11-07)
    paid, access, rows, feedback_given, counted_free, base = _gate(kind, user, chat_id, waive_free=waive_free,
                                                                   row_kind="prewarm" if prewarm else None)
    ev_kind = "prewarm" if prewarm else kind
    if tracker and tracker.cancelled():   # Stop before the model started: no model call, nothing spent
        _log_stopped(base, kind, user, paid, access)
        return _stopped(kind, paid, access, rows, feedback_given)
    slog = railway_search.SearchLog(railway_search.arm(user.get("id")))   # Railway search rollout (2026-10-26)
    inflight = None
    reuse_miss = ""
    try:
        row = f_mem.result()
        raw = row.get("notes") or []
        # Preference answers go to the model as one structured line (filters + priority), not as loose notes.
        notes = [n.get("note") for n in raw if n.get("note") and n.get("kind") != "pref"]
        user_context = row.get("user_context") or ""
        plan_rows = []
        if f_plan is not None:
            try:
                plan_rows = f_plan.result(timeout=3)
            except Exception as e:     # never fail an answer over the plan
                print("plan read failed:", repr(e)[:200])
            user_context = (user_context or "") + plan.context_line(plan_rows, show_prices=access)
        answers = prefs.from_notes(raw)
        pref_hint = prefs.search_hint(answers)
        # Thin profile (2026-10-26): the opening asks at most 2 questions with tap answers (profile_questions.py).
        qs = (profile_questions.questions(xano.profile_of(user), raw, user_context, active_filters)
              if kind == "opening" and config.PROFILE_QUESTIONS and not config.OPEN_ENDED else [])
        guests_extra = {"Wedding_Guest_Count": int(answers["guests"][0])} if answers.get("guests") else {}
        # Price question from someone not on Forever (2026-10-26): answer on fit + one Forever line + the
        # upgrade card, at most once per chat. Detected on the couple's own words, not left to the model.
        last = messages[-1].get("content") if messages else ""
        price_q = (kind != "opening" and not paid and isinstance(last, str) and upsell.is_price_question(last)
                   and not upsell.shown_in(history))
        # Reuse today's opening (2026-10-27, opening_reuse.py): only after the full _gate above allowed it, and only
        # when every input below is unchanged. No model call, no rec_usage row, nothing counted.
        # Prewarm (2026-11-02): an opening for the same inputs already being generated (a prewarm, or the page's
        # double mount) is waited for and served from the store instead of run twice. reuse_miss says why a fresh
        # opening was needed (opening_reuse.Store.lookup) - logged so the hit rate can be read before widening prewarm.
        reuse_fp = reuse_parts = None
        if kind == "opening" and config.OPENING_REUSE:
            reuse_parts = _opening_fp_parts(user, paid, access, raw, user_context, pref_hint, qs, answers, guests_extra)
            reuse_fp = opening_reuse.fingerprint(reuse_parts)
            hit, src, reuse_miss = opening_reuse.STORE.lookup(user["id"], reuse_fp, reuse_parts, claim=not prewarm)
            if hit is None and opening_reuse.INFLIGHT.running(user["id"], reuse_fp):
                if prewarm:
                    return {"status": "running"}
                stop_now = (lambda: tracker.cancelled()) if tracker else (lambda: False)
                if opening_reuse.INFLIGHT.wait(user["id"], reuse_fp, config.PREWARM_JOIN_S, stop_now):
                    hit, src, again = opening_reuse.STORE.lookup(user["id"], reuse_fp, reuse_parts, claim=True)
                    reuse_miss = "" if hit is not None else "joined_" + again
                else:
                    reuse_miss = "join_timeout"
            if hit is not None:
                if prewarm:
                    return {"status": "ready"}
                return _reused_opening(hit, user, paid, access, rows, feedback_given, tracker, src=src, base=base,
                                       t0=t_req)
            if not prewarm:
                print("opening_reuse miss user=%s reason=%s" % (user.get("id"), reuse_miss))
            inflight = opening_reuse.INFLIGHT.begin(user["id"], reuse_fp)
        # Detail: full members get figures; a non-member with a plan gets fit, not numbers ("light"); no plan at
        # all gets no price of any kind ("free", 2026-10-14).
        result, usage = agent.run(token, user, access, messages, memory_notes=notes, user_context=user_context,
                                  detail=_detail(paid, access),
                                  prior_vendor_ids=prior_ids, active_filters=active_filters,
                                  preferences=pref_hint, profile_questions=qs,
                                  known_area=(answers.get("area") or [""])[0], profile_extra=guests_extra,
                                  price_question=price_q,
                                  presearch=(prefs.search_filters(answers)
                                             if kind == "opening" and config.PRESEARCH else None),
                                  route=(kind == "refine"),     # agent: only with config.REFINE_ROUTER (2026-11-07)
                                  on_note=lambda n: xano.add_memory_note(user["id"], n), search_log=slog,
                                  **({"progress": tracker} if tracker else {}))
    except Exception as e:  # never charge a free refine for our own failure
        opening_reuse.INFLIGHT.end(user["id"], inflight)
        # The venue searches that ran before the failure (if any) keep the row attributable to an engine.
        xano.log_usage(dict(base, status="error", error=str(e)[:500], **_search_cols(slog.summary())))
        raise HTTPException(502, {"status": "error"})
    # Stopped (2026-10-25) - also when Stop came after the last model call: the page has already given up on this
    # answer, so it is not shown, not counted and not saved (the same as an error).
    if result.get("stopped") or (tracker and tracker.cancelled()):
        opening_reuse.INFLIGHT.end(user["id"], inflight)
        _log_stopped(base, ev_kind, user, paid, access, usage)
        return _stopped(kind, paid, access, rows, feedback_given)
    redactions = None
    if _detail(paid, access) == "free":   # price-free mode: price guard on text, cards and chips (code, not only the prompt)
        result = free_mode.sanitize_result(result)
        redactions = result.pop("price_redactions", 0)
    result = dict(result, questions=qs, upsell_prompt=None)
    if price_q:
        result.update(text=upsell.apply_text(result.get("text")), upsell_prompt=upsell.prompt(has_plan=access))
    elif kind == "opening" and not access and _free_views(user) <= 0:
        # Free couple with no free PDF views left: the upgrade card on the first answer of every chat (owner
        # 2026-10-26, "forcing upgrades"). Card only - the text already says exact figures need a plan.
        result["upsell_prompt"] = upsell.wall_prompt()
    if saved_lines:
        result["notes_saved"] = list(saved_lines) + list(result.get("notes_saved") or [])
    if reuse_fp is not None and not (tracker and tracker.cancelled()):
        # stores only a normal answer (opening_reuse.reusable); then release anyone waiting on this run
        opening_reuse.STORE.put(user["id"], reuse_fp, result, parts=reuse_parts, src="prewarm" if prewarm else "opening")
    opening_reuse.INFLIGHT.end(user["id"], inflight)
    cost = guards.cost_usd(usage["model"], usage)
    _POOL.submit(xano.log_usage, dict(base, status="ok", counted_as_free=counted_free, model=usage["model"],
                        input_tokens=usage["input_tokens"], cache_read_tokens=usage["cache_read_tokens"],
                        cache_write_tokens=usage["cache_write_tokens"], output_tokens=usage["output_tokens"],
                        tool_calls=usage["tool_calls"], cost_usd=cost, latency_ms=usage["latency_ms"],
                        # why this opening was not served from the store (Xano drops the key until table 79 has
                        # a reuse_miss column; the Railway log line above carries it either way)
                        **({"reuse_miss": reuse_miss[:120]} if kind == "opening" else {}),
                        **_tool_cols(usage), **_search_cols(usage.get("search"))))
    used = rows + ([{"status": "ok", "kind": "refine", "counted_as_free": True}] if counted_free else [])
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": ev_kind, "status": "ok", "forever": xano.has_forever(user), "beta_member": paid, "has_access": access, "source": "rec_service",
        "reuse_miss": reuse_miss if kind == "opening" else "",
        "cost_usd": cost, "latency_s": round(usage["latency_ms"] / 1000, 1), "tool_calls": usage["tool_calls"],
        "cards": len(result.get("cards") or []), "more_cards": len(result.get("more_cards") or []),
        "notes_saved": len(result.get("notes_saved") or []), "price_locked": not access,
        "price_redactions": redactions,
        "upsell_prompt": upsell.REASON if price_q else "", "profile_questions": [q["id"] for q in qs],
        "profile_answers_saved": len(saved_lines or ()),
        "reused": False,   # opening reuse (2026-10-27): true only on _reused_opening's event
        "free_refines_left": guards.free_refines_left(paid, used, feedback_given), "model": usage["model"],
        # Per-step timing (2026-10-16 speed pass): where the wait goes.
        "prefetch_ms": usage.get("prefetch_ms"), "model_turns": len(usage.get("turns") or []),
        "model_ms": sum(t.get("ms", 0) for t in usage.get("turns") or []),
        "tools_ms": sum(t.get("tool_ms", 0) for t in usage.get("turns") or []),
        "output_tokens": usage.get("output_tokens"), "early_stop": config.EARLY_STOP,
        # Per-tool timing (2026-10-17): ep119 search vs ep230/231 pricing inside search_venues, every tool's
        # total ms, and the end-of-request wait for background "Show 3 more" pricing.
        "search_ms": usage.get("search_ms"), "pricing_ms": usage.get("pricing_ms"),
        "searches": usage.get("searches"), "priced_sync": usage.get("priced_sync"),
        "deferred_wait_ms": usage.get("deferred_wait_ms"),
        "tool_breakdown": usage.get("tool_breakdown") or {},
        **{"tool_ms_" + k: v for k, v in (usage.get("tool_breakdown") or {}).items()},
        # Which tools and vendor categories this answer used (2026-11-02): lists for Mixpanel breakdowns.
        "tools_used": usage.get("tools_used") or [], "tool_count": len(usage.get("tools_used") or []),
        "categories": usage.get("categories") or [], "think_chars": usage.get("think_chars") or 0,
        # Per-model-call ms + tools, and pricing cache hit/miss (2026-10-26). model_turns is re-stated, same value.
        **_turn_props(usage),
        # Railway search rollout (2026-10-26): which engine answered the venue searches, and the user's arm.
        **_search_props(user, usage.get("search"))})
    if in_plan:   # 2026-11-07: the one "next for your wedding" suggestion once the couple is engaged
        asked = sum(1 for r in rows if r.get("status") == "ok" and r.get("kind") == "refine") + (kind == "refine")
        # plan_next OFF (owner 2026-11-07: "I asked for a photographer, then it says find a photographer again").
        result = dict(result, plan_enabled=True,
                      plan_next=plan.next_step(plan_rows, asked) if config.PLAN_NEXT_STEP else None)
    return dict(result, paid=paid, has_access=access,
                **_counters(kind, paid, rows, counted_free, feedback_given, access))



def _opening_fp_parts(user, paid, access, memory_raw, user_context, pref_hint, qs, answers, guests_extra):
    """Everything that shapes an opening answer (opening_reuse.py). Conservative: when in doubt, it goes in."""
    try:
        views = int(user.get("FreeViewsRemaining"))
    except (TypeError, ValueError):
        views = None
    return {
        "v": opening_reuse.VERSION,
        "prompt": opening_reuse.fingerprint([agent.system_prompt(config.LISTINGS_IN_CHAT), agent.COST_TOOLS_RULES,
                                             free_mode.RULES, free_mode.COST_TOOLS_RULE, OPENING_ASK]),
        "model": [config.MODEL, config.EFFORT, config.THINKING, config.MAX_TOOL_ROUNDS],
        "flags": [config.LISTINGS_IN_CHAT, config.COST_TOOLS_IN_CHAT, config.PROFILE_QUESTIONS, config.FOREVER_ONLY,
                  config.BETA_AUDIENCE, config.FREE_REFINES],
        "profile": xano.profile_of(user),
        "saved": xano.saved_vendor_ids(user),
        # Tier + entitlement: detail level, exact-price access, ep230's pricing entitlement and its inputs.
        "tier": [paid, access, xano.has_forever(user), xano.pi_entitled(user),
                 user.get("forever_access_purchased"), user.get("date_until_access"), user.get("total_amount_paid")],
        "free_views": views,
        "memory": [memory_raw or [], user_context or ""],
        "prefs": [pref_hint or "", answers or {}, guests_extra or {}],
        "questions": qs or [],
    }


def _reused_opening(stored, user, paid, access, rows, feedback_given, tracker, src="opening", base=None, t0=None):
    """Today's stored opening, with fresh counters. A repeat visit (src "opening") spent nothing (spent=False) and
    writes no usage row. The first visit served from a PREWARM (src "prewarm", 2026-11-02) is this couple's real
    opening: it writes the "opening" usage row a model-run opening would (model "prewarm", cost 0 - the prewarm row
    carries the cost; latency = what the couple actually waited) and counts like one, so the daily brief's people,
    openings and opening speed stay right."""
    t0 = time.time() if t0 is None else t0
    if tracker and tracker.cancelled():
        return _stopped("opening", paid, access, rows, feedback_given)
    warm = src == "prewarm"
    out = dict(stored, paid=paid, has_access=access,
               **_counters("opening", paid, rows, False, feedback_given, access, spent=warm))
    wait_ms = int((time.time() - t0) * 1000)
    if warm and base:
        _POOL.submit(xano.log_usage, dict(base, status="ok", counted_as_free=False, model="prewarm", input_tokens=0,
                                          cache_read_tokens=0, cache_write_tokens=0, output_tokens=0, tool_calls=0,
                                          cost_usd=0, latency_ms=wait_ms, tools="prewarm"))
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": "opening", "status": "ok", "reused": True, "forever": xano.has_forever(user), "beta_member": paid,
        "has_access": access, "source": "rec_service", "cost_usd": 0, "model": "prewarm" if warm else "reuse",
        "latency_s": round(wait_ms / 1000, 2), "tool_calls": 0, "cards": len(out.get("cards") or []),
        "more_cards": len(out.get("more_cards") or []), "price_locked": not access,
        "profile_questions": [q["id"] for q in out.get("questions") or []],
        "search_arm": railway_search.arm(user.get("id"))})
    return out

# ---------------------------------------------------------------- venue name lookup fast path (2026-10-26)
# The most common typed request is one venue by name ("oheka castle", "Khol mansion", "is X listed?"); about half
# failed after ~12 s of model + search. lookup.detect spots them, lookup.find looks the name up in table 11 (name
# key, distinctive tokens, typo variants, rescored in code) and the answer is built here WITHOUT the model:
#   found      -> that venue's card (same card + pricing code as a search: DETAIL LIGHT strips figures, free mode
#                 strips every price) + a short templated line + chips
#   choices    -> up to 3 close matches as cards, "which one did you mean?", one chip per name
#   not_found  -> "We don't have <name> in Tulle yet" + up to 3 venues in the area if we know it, venue_request
#                 {name, location} for the page, and a Slack notice (#feedback) + Mixpanel rec_venue_request for the
#                 data team's list of venues to add.
# Accounting: spends one question exactly like a typed one / "Show 3 more" (same _gate, rec_usage kind "refine",
# so the free counters, daily caps, beta check-in and the daily brief's refine count are unchanged); model "lookup"
# and cost 0 mark the row. Mixpanel rec_server_request kind "lookup". Anything unsure -> None -> the model as before.
LOOKUP_TIMEOUT_S = 6.0
LOOKUP_AFTER_QUESTION_TIMEOUT_S = 2.5   # a bare reply to our own question (see lookup.detect after_question)
LOOKUP_SIMILAR = 3
# "best" answers (2026-11-07) end with this line + the other matches; a tapped name from it is looked up, not sent to
# the model as a chip. Keep the wording stable - _refine_in_chat matches it.
ALSO_HAVE = "Not the one? We also have"


def _place_label(c):
    """'Burlingame, California' from the address + state, else the state."""
    parts = [p.strip() for p in str(c.get("address") or "").split(",") if p.strip()]
    state = c.get("state") or ""
    state = state[0] if isinstance(state, list) and state else state
    if state == "International":
        return ", ".join(parts[-2:]) if len(parts) >= 2 else "outside the US"
    # Drop a trailing country and "GA 30518"-style state+ZIP parts (2026-11-07 live: "GA 30518, Georgia").
    while parts and re.fullmatch(r"(?i)usa|united states(?: of america)?|[A-Z]{2}\s*\d{5}(?:-\d{4})?|\d{5}", parts[-1]):
        parts = parts[:-1]
    city = parts[-1] if len(parts) >= 2 else ""
    return ", ".join(x for x in (city, state) if x) or "Tulle"


def _price_or_none(token, vid, full):
    try:
        return xano.venue_pricing(token, vid, full)
    except Exception:
        return None


OTHER_VENDOR_CATS = ("Photography", "Entertainment", "Flowers", "Food and Beverage", "Beauty", "Wedding Planner")
VENDOR_KIND = {"Photography": "photographer", "Entertainment": "entertainer", "Flowers": "florist",
               "Food and Beverage": "caterer", "Beauty": "hair and makeup artist", "Wedding Planner": "wedding planner"}


def _any_vendor_search(token, cats):
    """search(term) over several vendor types at once (2026-11-07): each category's name search in parallel."""
    def search(term):
        with ThreadPoolExecutor(max_workers=len(cats)) as ex:
            got = list(ex.map(lambda c: xano.name_search(term, token, category=c), cats))
        return [r for rows in got for r in rows]
    return search


def _find_and_price(q, token, full, prefer=()):
    """lookup.find, then the pricing panel of the match in parallel - all while the question is being gated.
    Only a FOUND venue is priced: a "which one did you mean?" list shows names and places, and its pricing was
    the whole cost of that answer (measured 2026-11-02: 7.0 s with two matches vs 0.2 s for a miss). The tap on a
    name comes back as a found lookup and is priced then."""
    hint = q.get("category") or ""
    if hint and hint != "Venue":      # "Bloom florist", "Jane Doe Photography": that type only
        res = lookup.resolve(q, lookup.find(q, _any_vendor_search(token, (hint,))), prefer)
    else:
        res = lookup.resolve(q, lookup.find(q, lambda term: xano.name_search(term, token)), prefer)
        if res["status"] == "not_found" and not lookup.destination_in(q["name"]):
            # Not a venue we have: maybe a photographer, florist, caterer ... by name (one more parallel round).
            other = lookup.resolve(q, lookup.find(q, _any_vendor_search(token, OTHER_VENDOR_CATS)), prefer)
            if other["status"] != "not_found":
                res = other
    res["vendor_kind"] = (res.get("matches") or [{}])[0].get("category") or "Venue"
    if res["vendor_kind"] != "Venue":
        return res                    # listings: no venue pricing panel to fetch
    ids = ([m["vendor_id"] for m in res["matches"][:1]] if res["status"] in ("found", "best") else
           [m["vendor_id"] for m in res["matches"][:3]] if res["status"] == "choices" and full else [])
    if ids:
        with ThreadPoolExecutor(max_workers=len(ids)) as ex:
            res["pricing"] = dict(zip(ids, ex.map(lambda v: _price_or_none(token, v, full), ids)))
    return res


def _lookup_card(c, p, full, guests, budget, reason=""):
    """The same card a search builds: the PI panel's rental + (FULL) all-in estimate; LIGHT/FREE: no figures."""
    c = {k: v for k, v in c.items() if k not in ("google_name", "lat", "lng")}
    c = agent._with_rental(c, p, guests, budget)
    if not full:
        c = agent._light_card(c)
    return dict(c, reason=reason), p


def _similar_near(token, q, user, slog, exclude=(), use_profile=True, guests=0):
    """Up to LOOKUP_SIMILAR real venues in the area the couple named (or their profile state): one fast search.
    ([], '') when the area is unknown or the search fails - the honest "we don't have it" still stands.
    guests (2026-11-07): only venues that seat the couple's guest count - the planner log showed the same three
    most-clicked names for every miss in a state ("Allied Arts Guild, Alila Marea, Aliso Viejo"), whatever the size."""
    loc = q.get("location") or q.get("alt_location") or ""
    state = lookup.state_of(loc) if loc else ""
    venues, where = [], ""
    try:
        if loc and not state:
            near = xano.geocode_city(loc)
            if near:
                venues = xano.venues_near(near[0], near[1], 25)["venues"]
                where = "near " + near[2]
        if not venues:
            # use_profile=False (a venue we don't have, no area named): their profile state is a guess - live test
            # 2026-10-26, "Il Sereno on Lake Como" got "3 venues we do have in Alabama".
            states = [state] if state else (agent._states_of(xano.profile_of(user))[:1] if use_profile else [])
            if states:
                kw = {"guests": int(guests)} if guests else {}
                venues = xano.search_venues(token, states=states, page_size=LOOKUP_SIMILAR + 3,
                                            search_log=slog, **kw)["venues"]
                where = "in " + ("international destinations" if states == ["International"] else states[0])
    except Exception:
        return [], ""
    out = [v for v in venues if v.get("vendor_id") and v["vendor_id"] not in set(exclude)][:LOOKUP_SIMILAR]
    return out, (where if out else "")


def _vendor_lookup_card(m):
    """A non-venue vendor found by name (2026-11-07): the listing card, no venue fields, no price."""
    keep = ("vendor_id", "vendor_idx", "name", "state", "address", "image", "description", "category", "at_a_glance")
    return dict({k: m.get(k) for k in keep if m.get(k) not in (None, "")}, reason="The vendor you asked about")


def _display_name(name):
    return name if any(ch.isupper() for ch in name) else " ".join(w.capitalize() for w in name.split())


def _state1(st):
    return st[0] if isinstance(st, list) and st else st


def _lookup_fast(token, user, text, last_assistant="", chat_id=0, tracker=None, repeat=False):
    """The fast answer for "look up <venue>", or None (not a lookup, or unsure -> the model answers as before)."""
    if not config.NAME_LOOKUP:
        return None
    q = lookup.detect(text, last_assistant)
    if not q:
        return None
    t0 = time.time()
    full = xano.beta_member(user)    # FULL detail = full members, exactly as agent.run's detail="full"
    try:
        prefer = agent._states_of(xano.profile_of(user))     # their wedding state breaks a "which one?" tie
    except Exception:
        prefer = []
    f_find = _POOL.submit(_find_and_price, q, token, full, prefer)  # runs while the question is gated
    paid, access, rows, feedback_given, counted_free, base = _gate("refine", user, chat_id)   # raises when blocked
    if tracker and tracker.cancelled():
        _log_stopped(base, "lookup", user, paid, access)
        return _stopped("refine", paid, access, rows, feedback_given)
    after_q = bool(q.get("after_question"))
    try:
        # A reply to our own question gets a short wait: when it is not a venue name the model must not be delayed.
        res = f_find.result(timeout=LOOKUP_AFTER_QUESTION_TIMEOUT_S if after_q else LOOKUP_TIMEOUT_S)
    except Exception as e:   # Xano down / slow: never say "we don't have it" because of our own failure
        print("lookup fell back to the model:", repr(e)[:200])
        return None
    status = res["status"]
    if status == "unsure":
        return None          # "aspen", "North": several equal venues for one bare word - the model reads it
    if status == "not_found" and not q["cue"] and q["words"] == 1:
        return None          # one bare word we can't place: let the model read it
    if after_q and status not in ("found", "best", "choices") and not lookup.destination_in(
            "%s %s" % (q["name"], q.get("location") or q.get("alt_location") or "")):
        return None          # a reply to our question that is not a venue we have: the model reads it as before
        # (a destination name - "Rosewood Bermuda" - keeps the honest coverage line even mid-chat, 2026-11-02)
    profile = xano.profile_of(user)
    try:
        guests = int(profile.get("Wedding_Guest_Count") or 0)
    except (TypeError, ValueError):
        guests = 0
    try:
        budget = int(profile.get("Wedding_Budget") or 0)
    except (TypeError, ValueError):
        budget = 0
    slog = railway_search.SearchLog(railway_search.arm(user.get("id")))
    saved = xano.saved_vendor_ids(user)
    name = _display_name(q["name"])
    venue_request, filters = None, {}
    vkind = res.get("vendor_kind") or "Venue"
    if vkind != "Venue" and status in ("found", "best", "choices"):
        cards = [_vendor_lookup_card(m) for m in res["matches"][:3]]
        kind = VENDOR_KIND.get(vkind, "vendor")
        if status == "choices" and len(cards) > 1:
            text_out = ("We have %d %ss that match “%s”: %s. Each card opens its page on Tulle."
                        % (len(cards), kind, name, ", ".join("%s (%s)" % (c.get("name"), _place_label(c)) for c in cards)))
            chips = []
        else:
            c0 = cards[0]
            text_out = "Here's %s, a %s in %s. Open its page on the card for what they offer%s." % (
                c0.get("name"), kind, _place_label(c0),
                " and their packages" if vkind in ("Photography", "Entertainment") else "")
            chips = []
            if status == "best" and len(cards) > 1:
                text_out += " %s %s." % (ALSO_HAVE, " and ".join("%s (%s)" % (c.get("name"), _place_label(c))
                                                                 for c in cards[1:]))
                chips = [c.get("name") for c in cards[1:]]
            cards = cards[:1] if status == "found" else cards
        if cards and cards[0].get("state"):
            filters = {"states": [_state1(cards[0]["state"])]}
    elif status in ("found", "best"):
        m = res["matches"][0]
        p = (res.get("pricing") or {}).get(m["vendor_id"])
        card, p = _lookup_card(m, p, full, guests, budget, "The venue you asked about")
        cards = [card]
        vn = card.get("name")
        text_out = "Here's %s in %s." % (vn, _place_label(card))
        if full and card.get("estimate_label"):
            text_out += " %s." % card["estimate_label"]
        verdict = (p or {}).get("verdict") or ""
        if verdict and (full or (access and not agent._DIGITS.search(verdict))):
            text_out += " " + verdict.rstrip(".") + "."
        text_out += " Open its pricing PDF on the card for everything the venue sent couples."
        chips = ["Similar venues near %s" % vn,
                 ("Cost breakdown for %s" % vn) if full else ("Is %s a good fit for us?" % vn)]
        if saved and card.get("vendor_id") not in saved:
            chips.append("Compare %s with my saved venues" % vn)
        if status == "best":
            # The other close matches, by name (a tap looks that one up - see ALSO_HAVE in _refine_in_chat).
            others = [o for o in res["matches"][1:3] if o.get("name")]
            if others:
                text_out += " %s %s." % (ALSO_HAVE, " and ".join(
                    "%s (%s)" % (o["name"], _place_label(o)) for o in others))
                labels = [o["name"] for o in others]
                chips = [o["name"] if labels.count(o["name"]) == 1 and o["name"] != vn
                         else "%s, %s" % (o["name"], _place_label(o)) for o in others]
        if card.get("state"):
            filters = {"states": [_state1(card["state"])]}
    elif status == "choices":
        cards = [_lookup_card(m, (res.get("pricing") or {}).get(m["vendor_id"]), full, guests, budget)[0]
                 for m in res["matches"]]
        # 2026-11-07 (owner): an answer, not "which one did you mean?" - after that question 0 of 22 couples tapped a
        # name and 11 left (planner log 11-03..11-06). Every match is a card with its PDF button.
        where = [_place_label(c) for c in cards]
        text_out = ("We have %d venues that match “%s”: %s. Each card has its pricing PDF."
                    % (len(cards), name, ", ".join("%s (%s)" % (c.get("name"), w) for c, w in zip(cards, where))))
        chips = []
    else:
        loc = q.get("location") or q.get("alt_location") or ""
        # 2026-11-02 (chat 929, "Rosewood Bermuda" x2 -> "tell me the city" x2 -> the couple left): a destination gets
        # the honest coverage line, no cards; any other miss shows venues from the area named or, failing that, the
        # couple's own profile state (use_profile) instead of a question; a repeat of the same name says so.
        dest = lookup.destination_in("%s %s" % (name, loc))
        # A place the couple NAMED that we can't locate ("Aspen Highlands") is never swapped for their profile state
        # (2026-11-07 live: Ritz-Carlton Club, Aspen Highlands -> "3 venues we do have in Texas").
        hint_other = (q.get("category") or "Venue") != "Venue"   # "Bloom florist" we don't have: no venue cards
        similar, where = ([], "") if (dest or hint_other) else _similar_near(
            token, q, user, slog, use_profile=not (q.get("location") or q.get("alt_location")), guests=guests)
        cards = []
        for v in similar:
            c = agent._with_rental(dict(v), None, guests, budget)
            cards.append(dict(c if full else agent._light_card(c), reason=""))
        if dest:
            text_out = ("We don't have %s - Tulle covers US venues, so there's no real pricing for a %s wedding here. "
                        "If you're also weighing a US option, tell me the area and I'll show you venues we do have there."
                        % (name, dest.title()))
        elif repeat:
            text_out = "Still nothing for %s - it's already on our list to add." % name
        else:
            text_out = "We don't have %s in Tulle yet - I've passed it to our team to add." % name
        if cards:
            text_out += " Here are %d venues we do have %s, each with real pricing from couples." % (len(cards), where)
        elif not dest:
            text_out += (" Tell me a city or area, or a style you're after, and I'll search what we do have."
                         if repeat else " Tell me the city or area and I'll show you venues we do have there.")
        venue_request = {"name": name, "location": loc}
        chips = (["More venues %s" % where] if where else []) + ["Find a photographer"]
        if cards and cards[0].get("state"):
            filters = {"states": [_state1(cards[0]["state"])]}
        _POOL.submit(_venue_request_log, user, name, loc, text, chat_id, res.get("near") or [])
    result = {"text": text_out, "cards": cards, "chips": chips, "more_cards": [], "search_filters": filters,
              "notes_saved": [], "widgets": [], "dropped_unverified_ids": [],
              "lookup": {"status": status, "query": name}, "venue_request": venue_request}
    if _detail(paid, access) == "free":   # price-free mode: the same price guard as a model answer (text, cards, chips)
        result = free_mode.sanitize_result(result)
        result.pop("price_redactions", None)
    ms = int((time.time() - t0) * 1000)
    search = slog.summary()
    _POOL.submit(xano.log_usage, dict(base, status="ok", counted_as_free=counted_free, model="lookup",
                                      input_tokens=0, cache_read_tokens=0, cache_write_tokens=0, output_tokens=0,
                                      tool_calls=0, cost_usd=0, latency_ms=ms, tools="lookup:" + status,
                                      categories=(res.get("vendor_kind") or "Venue").lower(), **_search_cols(search)))
    used = rows + ([{"status": "ok", "kind": "refine", "counted_as_free": True, "tools": "lookup:" + status}]
                   if counted_free else [])
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": "lookup", "status": "ok", "forever": xano.has_forever(user), "beta_member": paid, "has_access": access,
        "source": "rec_service", "cost_usd": 0, "latency_s": round(ms / 1000, 2), "cards": len(result["cards"]),
        "lookup_status": status, "lookup_queries": len(res.get("queries") or []), "lookup_scores": res.get("scores"),
        "lookup_category": res.get("vendor_kind") or "Venue",
        "price_locked": not access, "free_refines_left": guards.free_refines_left(paid, used, feedback_given),
        **_search_props(user, search)})
    return dict(result, paid=paid, has_access=access,
                **_counters("refine", paid, rows, counted_free, feedback_given, access, row_tools="lookup:" + status))


def _venue_request_log(user, name, location, question, chat_id, near):
    """The missing-venue list for the data team: Mixpanel rec_venue_request (queryable) + a Slack notice."""
    try:
        xano.mp_track(user["id"], "rec_venue_request", {"venue_name": name, "location": location,
                                                        "question": (question or "")[:300], "chat_id": chat_id,
                                                        "closest": list(near)[:3], "source": "rec_service"})
        if config.VENUE_REQUEST_SLACK:
            notify.venue_request(user, name, location, question, xano.plan_label(user), chat_id, near)
    except Exception as e:
        print("venue request log failed:", repr(e)[:200])


def _opening_title(user):
    """'Venue picks · Arizona, 80 guests' so a list of opening-only chats is tellable apart."""
    p = xano.profile_of(user)
    loc = p.get("Wedding_Location_Updated") or []
    loc = [loc] if isinstance(loc, str) else loc
    states = [x.strip() for l in loc if l for x in str(l).split(",") if x.strip()]   # some rows comma-pack states
    bits = [", ".join(states[:2]) + (" +%d" % (len(states) - 2) if len(states) > 2 else "")] if states else []
    if p.get("Wedding_Guest_Count"):
        bits.append("%s guests" % p["Wedding_Guest_Count"])
    return "Venue picks" + (" · " + ", ".join(b for b in bits if b) if any(bits) else "")


def _picks_title(out, user):
    """Owner 2026-11-01 ('make Your chats more descriptive'): an opening is named after what it found -
    'Scottsdale · Clayton House, The Willow, Gainey Ranch' - instead of the profile line every opening shared."""
    cards = [c for c in (out.get("cards") or []) if isinstance(c, dict) and c.get("name")]
    if not cards:
        return _opening_title(user)
    f = out.get("search_filters") or {}
    states = f.get("states") or []
    area = str(f.get("city") or (states[0] if states else "") or "").strip()
    names = []
    for c in cards[:3]:
        n = str(c["name"]).strip()
        names.append(n if len(n) <= 26 else n[:24].rstrip() + "\u2026")
    return (area + " \u00b7 " if area else "Venue picks \u00b7 ") + ", ".join(names)


_ASK_RE = re.compile(r"^\s*tell me about (.+?)(?:,? and whether it fits my wedding)?\s*[.?!]*\s*$", re.I)


def _question_title(text):
    """A typed question as a chat name: the vendor-page 'Ask Tulle' template collapses to 'About <vendor>'; anything
    else is the question itself, first letter capitalised, trailing punctuation dropped, 80 characters max."""
    t = " ".join(str(text or "").split())
    m = _ASK_RE.match(t)
    if m:
        return ("About " + m.group(1).strip())[:80]
    t = t.rstrip(" .!")
    if t:
        t = t[0].upper() + t[1:]
    return t[:80]


def _starter_chips(price_locked):
    """Fixed opening chips. REC_COST_TOOLS_IN_CHAT on (2026-10-25): "What will our wedding cost?" (the estimate cost
    tool - open to free users too) takes the venue-check slot. Off: exactly as before."""
    if config.COST_TOOLS_IN_CHAT:
        return free_mode.STARTER_CHIPS_COST_TOOLS if price_locked else STARTER_CHIPS_COST_TOOLS
    return free_mode.STARTER_CHIPS if price_locked else STARTER_CHIPS


_AREA_Q = _re.compile(r"\b(city|cities|area|areas|part of|where in|which town|near)\b", _re.I)


def _opening_chips(text, model_chips, user, price_locked):
    """Opening chips (owner 2026-10-31): the likely ANSWERS to the closing question come first, the fixed starter
    chips fill the rest. "Which city or area of Arizona?" -> "Near Phoenix", "Near Scottsdale", ... from
    profile_questions.area_options; any other closing question -> the model's own chips (the prompt makes them its
    answers). No closing question -> the starters exactly as before."""
    starters = list(_starter_chips(price_locked))
    m = _re.search(r"([^.!?\n]{6,240}\?)\s*$", text or "")
    q = m.group(1).strip() if m else ""
    answers = []
    if q and _AREA_Q.search(q):
        try:
            answers = [o["label"] for o in profile_questions.area_options(profile_questions.state_of(xano.profile_of(user)))][:4]
        except Exception:
            answers = []
    if q and not answers:
        answers = [c for c in model_chips if isinstance(c, str) and c.strip() and c not in starters][:3]
    out = answers + [c for c in starters if c not in answers]
    return out[:4] if answers else starters


def _next_step(out, history=()):
    """Chip post-processing after de-dupe and the free price guard (owner 2026-10-25: "make sure the chips still
    exist to push them to the next step"). Flag on: a reply with a cost widget always carries a planning or action
    chip (chips.guarantee_next_step). Always: at most one action chip, last, never the same one twice in a row
    (a no-op when the reply has no action chip, so the flag-off path is unchanged)."""
    locked = bool(out.get("price_locked"))
    chips = out.get("chips") or []
    if config.COST_TOOLS_IN_CHAT:
        chips = guarantee_next_step(chips, out.get("widgets"), history, free=locked)
    return place_action_chip(chips, history, topups=free_mode.TOPUPS if locked else CHIP_TOPUPS)


def _tracked(request_id, user, handler):
    """Run handler(tracker) with the request's progress entry (None without a request_id), and mark it done or
    stopped however the handler ends (a blocked 402/429 or a 502 reads "done": the page has its answer)."""
    tracker = progress.start(request_id, user["id"])
    state = "done"
    try:
        out = handler(tracker)
        if isinstance(out, dict) and out.get("stopped"):
            state = "stopped"
        return out
    finally:
        if tracker:
            tracker.finish(state)


@app.post("/rec/opening")
def opening(background: BackgroundTasks, body: Optional[OpeningBody] = None, authorization: str = Header(None)):
    token, user = _auth(authorization)
    return _tracked(body.request_id if body else "", user, lambda tr: _opening(token, user, background, tr))


# One chat per opening, not two (2026-11-07). The planner log 11-03..11-06 had 290 openings for 122 couples, 47 of them
# in the same minute as that couple's previous opening: the page mounts the chat element twice (the tab variable
# flips, the editor double-mounts), the second POST /rec/opening is served the SAME stored opening (opening_reuse),
# and every call created a new chat - two identical conversations in "Chats", two rows in the team's sheet. Now an
# identical opening for the same couple within RECENT_OPENING_S, in a chat nobody has written in yet, returns that
# chat. "New chat" after a real conversation still makes a new one (the old chat is marked used on its first message).
RECENT_OPENING_S = 120
_RECENT_OPENING = {}          # user_id -> (chat_id, opening text, time)
_USED_CHATS = set()           # chat ids that got a message after their opening (bounded below)
_RECENT_LOCK = threading.Lock()


def _chat_used(chat_id):
    if not chat_id:
        return
    with _RECENT_LOCK:
        _USED_CHATS.add(int(chat_id))
        if len(_USED_CHATS) > 20000:
            _USED_CHATS.clear()


def _recent_opening_chat(user_id, text, now=None):
    """The chat id to reuse for this opening, or None."""
    now = time.time() if now is None else now
    with _RECENT_LOCK:
        rec = _RECENT_OPENING.get(user_id)
        if rec and now - rec[2] <= RECENT_OPENING_S and rec[1] == text and rec[0] not in _USED_CHATS:
            return rec[0]
    return None


def _remember_opening(user_id, chat_id, text, now=None):
    with _RECENT_LOCK:
        _RECENT_OPENING[user_id] = (int(chat_id), text, time.time() if now is None else now)
        if len(_RECENT_OPENING) > 5000:
            cutoff = time.time() - RECENT_OPENING_S
            for k in [k for k, v in _RECENT_OPENING.items() if v[2] < cutoff]:
                _RECENT_OPENING.pop(k, None)


def _opening(token, user, background, tracker):
    # Create the chat only AFTER the guard allowed and the model answered - a blocked (402/429/503) or
    # failed opening must not leave an empty conversation behind. Costs ~0.3s.
    out = _run_guarded("opening", token, user, [{"role": "user", "content": OPENING_ASK}], tracker=tracker)
    if out.get("stopped"):
        return out           # no chat created, nothing saved
    if not out.get("paid") and config.OPENING_NO_FOREVER:
        # The first message never sells a plan the couple hasn't seen (2026-11-07; the prompt says so too - this is
        # the guarantee, and it also cleans openings already in the reuse store). The upgrade card is unaffected.
        out["text"] = upsell.strip_forever(out.get("text") or "")
    same = _recent_opening_chat(user["id"], out.get("text") or "")
    if same:
        out["chips"] = _opening_chips(out.get("text") or "", out.get("chips") or [], user,
                                      bool(out.get("price_locked")))
        if out.get("questions"):
            out["chips"] = [o["label"] for o in out["questions"][0]["options"]][:4]
        _open_ended(out)
        return _public(out, chat_id=same)   # the page's second mount: same chat, nothing stored or logged twice
    out["chips"] = _opening_chips(out.get("text") or "", out.get("chips") or [], user, bool(out.get("price_locked")))
    if out.get("questions"):
        # The first question's answers are the chips, so the current page already shows them as taps; a tap is
        # sent as the next message and _refine_in_chat saves it (the page may also render `questions` itself).
        out["chips"] = [o["label"] for o in out["questions"][0]["options"]][:4]
        background.add_task(_mark_asked, user["id"], [q["id"] for q in out["questions"]])
    _open_ended(out)
    chat = xano.create_chat(user["id"], _picks_title(out, user))
    _remember_opening(user["id"], chat["id"], out.get("text") or "")
    background.add_task(xano.add_message, chat["id"], "assistant", out["text"], _stored_cards(out), out["chips"])
    background.add_task(sheet_log.event, user, "Planner's first message", "(opened the Planner)", out.get("text"),
                        chat["id"], out.get("cards"), out.get("search_filters"), sheet_log.flag_of(out))
    return _public(out, chat_id=chat["id"])


# ---------------------------------------------------------------- prewarm (owner 2026-11-02, time-to-value)
# The opening is ~4.5 s of model writing (p50, 11-02), and nothing on the critical path can make that much faster. So
# generate it BEFORE the couple opens the Planner: the page calls POST /rec/prewarm at a moment when the inputs are
# final and the Planner is likely next (first: right after onboarding saves the profile). The answer goes into the
# opening reuse store; /rec/opening then serves it with no model run (or waits for it if it is still running -
# opening_reuse.INFLIGHT). The response is immediate; the work runs on its own small pool so it never delays
# logging. Cost: a normal opening's model run (~$0.03), logged as kind "prewarm" (never counted toward any
# allowance); the same gate as an opening (kill switch, spend caps), and at most PREWARM_MAX_PER_DAY runs per couple
# per ET day. A prewarm nobody opens is the cost of this; the reuse_miss logging says how often that happens.
_PREWARM_POOL = ThreadPoolExecutor(max_workers=4)
_PREWARM_RUNS = {}          # (user id, ET day) -> prewarm jobs started today
_PREWARM_LOCK = threading.Lock()


def _prewarm_claim(user_id):
    """Count one prewarm job for today; False when the couple is at the daily limit."""
    day = opening_reuse.et_day()
    key = (str(user_id), day)
    with _PREWARM_LOCK:
        for k in [k for k in _PREWARM_RUNS if k[1] != day]:
            del _PREWARM_RUNS[k]
        n = _PREWARM_RUNS.get(key, 0)
        if n >= config.PREWARM_MAX_PER_DAY:
            return False
        _PREWARM_RUNS[key] = n + 1
    return True


def _prewarm_release(user_id):
    """A job that ran no model (already stored / already running) gives its slot back."""
    key = (str(user_id), opening_reuse.et_day())
    with _PREWARM_LOCK:
        if _PREWARM_RUNS.get(key):
            _PREWARM_RUNS[key] -= 1


def _prewarm_job(token, user):
    uid = user.get("id")
    try:
        out = _run_guarded("opening", token, user, [{"role": "user", "content": OPENING_ASK}], prewarm=True)
        st = out.get("status") if isinstance(out, dict) else None
        if st in ("ready", "running"):
            _prewarm_release(uid)
        else:
            st = "stored" if opening_reuse.reusable(out) else "not_stored"
        print("prewarm user=%s -> %s" % (uid, st))
    except HTTPException as e:   # blocked by the gate (kill switch, spend cap, daily cap): nothing to do
        d = e.detail if isinstance(e.detail, dict) else {}
        print("prewarm user=%s -> blocked %s" % (uid, d.get("status") or e.status_code))
    except Exception as e:
        print("prewarm user=%s -> failed %s" % (uid, repr(e)[:200]))


@app.post("/rec/prewarm")
def prewarm(authorization: str = Header(None)):
    """Start generating this couple's opening in the background. Always answers at once:
    started | limit (PREWARM_MAX_PER_DAY reached) | off (REC_PREWARM=0 or opening reuse off). Fire and forget - the
    page never waits on it and never shows anything from it."""
    token, user = _auth(authorization)
    if not (config.PREWARM and config.OPENING_REUSE):
        return {"status": "off"}
    if not _prewarm_claim(user["id"]):
        return {"status": "limit"}
    _PREWARM_POOL.submit(_prewarm_job, token, user)
    return {"status": "started"}


def _mark_asked(user_id, ids):
    try:
        xano.mark_questions_asked(user_id, ids)
    except Exception as e:   # a lost marker only means the question may be asked once more
        print("mark_questions_asked failed:", repr(e)[:200])


def _pending_questions(history):
    """The thin-profile questions the latest answer asked (only if it is the latest assistant turn)."""
    last = next((t for t in reversed(history or []) if t.get("role") == "assistant"), None)
    return list((last or {}).get("questions") or [])


def _save_profile_answers(user, pending, text):
    """The couple's tap or typed reply to a thin-profile question -> saved to their memory BEFORE the model runs
    (so this answer already uses it, and no later save_note write races it). Returns (answers, lines)."""
    answers = profile_questions.answers_from_reply(pending, text)
    if not answers:
        return {}, []
    try:
        added = xano.merge_preferences(user["id"], answers)
    except Exception as e:   # never fail the question over the save
        print("merge_preferences failed:", repr(e)[:200])
        return answers, []
    _POOL.submit(xano.mp_track, user["id"], "rec_profile_answer", {
        "answered": sorted(answers), "source": "rec_service"})
    return answers, [n["note"] for n in added if n.get("note")]


def _refine_in_chat(token, user, chat, text, background, tracker=None, quote=None):
    """One typed question inside a stored conversation: server-held history in, both turns saved after."""
    _chat_used(chat.get("id") if isinstance(chat, dict) else chat)
    t0 = time.time()
    history = xano.get_messages(chat["id"])
    pending = _pending_questions(history)
    answered, lines = _save_profile_answers(user, pending, text) if pending else ({}, [])
    last_t = next((t for t in reversed(history) if t.get("role") == "assistant"), {}) or {}
    last_a = last_t.get("text") or ""
    # A tapped chip ("Keep it all-inclusive") is never a venue name: no lookup round trip for it.
    is_chip = text.strip().lower() in {str(c).strip().lower() for c in (last_t.get("chips") or []) if c}
    if is_chip and (ALSO_HAVE in last_a or "which one did you mean" in last_a or "the one you meant" in last_a):
        is_chip = False      # a venue name we offered: look it up (0.5 s), not the model (8 s)
    last_u = next((t.get("text") or "" for t in reversed(history) if t.get("role") == "user"), "")
    repeat = bool(last_u) and last_u.strip().lower() == text.strip().lower()
    # "oheka castle": no model (2026-10-26). Never for an answer to our own profile question ("Near Dallas").
    out = None if (answered or is_chip or quote) else _lookup_fast(token, user, text, last_a, chat["id"], tracker, repeat=repeat)
    if out is None:
        msgs, kw = _model_inputs(history, text)
        if quote and msgs and msgs[-1].get("role") == "user":   # model-only: the stored turn stays the short question
            msgs = msgs[:-1] + [dict(msgs[-1], content=msgs[-1]["content"] + quote_block(quote, str(quote.get("_file_name") or "")))]
        out = _run_guarded("refine", token, user, msgs, chat["id"], prior_ids=kw["prior_vendor_ids"],
                           active_filters=kw["active_filters"], tracker=tracker, history=history, saved_lines=lines,
                           waive_free=bool(answered))
    action = ("Answered a profile question" if answered else
              "Tapped a suggested reply" if is_chip else "Typed a question")
    if out.get("stopped"):
        sheet_log.event(user, action, text, "", chat["id"], flag="stopped by the couple")
        return dict(out, chat_id=chat["id"])   # nothing saved: not even the question (as on an error)
    out["chips"] = ensure_chips(dedupe_chips(out.get("chips"), history))
    if out.get("price_locked"):
        out["chips"] = free_mode.chips(out["chips"], history, planning=config.COST_TOOLS_IN_CHAT)
    out["chips"] = _next_step(out, history)
    rest = [q for q in pending if q["id"] not in answered]
    if answered and rest:
        # They answered one of the opening's 2 questions: ask the other one now, its answers as the chips.
        out["questions"] = rest[:1]
        out["chips"] = [o["label"] for o in rest[0]["options"]][:4]
    _open_ended(out)
    if (chat.get("title") or "").startswith("Venue picks"):
        chat["title"] = _question_title(text)

    def persist():   # after the response: user turn, then assistant turn, then bump the chat
        xano.add_message(chat["id"], "user", text)
        xano.add_message(chat["id"], "assistant", out["text"], _stored_cards(out), out["chips"])
        xano.touch_chat(chat)
        sheet_log.event(user, action, text, out.get("text"), chat["id"], out.get("cards"), out.get("search_filters"),
                        sheet_log.flag_of(out), int((time.time() - t0) * 1000))
    background.add_task(persist)
    return _public(out, chat_id=chat["id"])


RECENT_CHAT_MS = 30 * 60 * 1000


def _recent_chat(user_id):
    """The user's most recently active conversation if it was touched in the last 30 minutes, else None."""
    try:
        chats = xano.list_chats(user_id)
    except Exception:
        return None
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    for c in chats[:1]:
        last = c.get("updated_at") or c.get("created_at") or 0
        if last and now_ms - int(last) <= RECENT_CHAT_MS:
            return xano.get_chat(user_id, c["id"])
    return None


@app.post("/rec/refine")
def refine(body: RefineBody, background: BackgroundTasks, authorization: str = Header(None)):
    token, user = _auth(authorization)
    if body.quote is not None and not xano.has_forever(user):
        raise HTTPException(403, {"status": "quote_forever_only"})   # owner 11-08: quote check is a Forever feature
    try:
        return _tracked(body.request_id, user, lambda tr: _refine(token, user, body, background, tr))
    except HTTPException as e:
        if e.status_code in (402, 429):   # out of free questions / daily limit: the team sees the ask it blocked
            sheet_log.event(user, "Typed a question", body.message or "", "", body.chat_id or 0,
                            flag="blocked: %s" % ("out of free questions" if e.status_code == 402 else "daily limit"))
        raise


def _refine(token, user, body, background, tracker):
    if body.chat_id:
        chat = xano.get_chat(user["id"], body.chat_id)
        if not chat:
            raise HTTPException(404, "conversation not found")
        text = (body.message or "").strip()[:2000]
        if not text:
            raise HTTPException(400, "message required")
        return _refine_in_chat(token, user, chat, text, background, tracker, quote=body.quote)
    # No chat_id (the page asks before any opening has created a conversation). Until 2026-10-12 this ran
    # statelessly and saved nothing: user 827's 3 rated questions were lost and each one started from scratch,
    # so a state named in one question was forgotten by the next. Now: continue the user's conversation from
    # the last 30 minutes if there is one, else start one - the turns are saved and the chat_id is returned.
    msgs = [{"role": t.role, "content": t.content[:2000]} for t in body.messages[-HISTORY_TURNS:]]
    text = (body.message or "").strip()[:2000]
    if text and (not msgs or msgs[-1]["role"] != "user" or msgs[-1]["content"] != text):
        msgs.append({"role": "user", "content": text})
    if not msgs or msgs[-1]["role"] != "user" or msgs[0]["role"] != "user":
        raise HTTPException(400, "messages must start and end with a user turn")
    text = msgs[-1]["content"].strip()
    # 2026-11-07 (owner screenshot): since the home screen (no automatic opening), a question typed on a fresh screen
    # arrives with no chat_id - and was attached to the chat used in the last 30 minutes, so the Planner answered
    # "the three venues above" with nothing above. The current page always sends its chat_id mid-conversation (also
    # after a reload, which resumes the chat), so no chat_id + no client-held history = a NEW conversation. Only the
    # legacy shape (prior turns in `messages`) still continues the recent chat.
    chat = _recent_chat(user["id"]) if len(body.messages or []) > 1 else None
    if chat:
        return _refine_in_chat(token, user, chat, text, background, tracker, quote=body.quote)
    # New conversation: the client's turns are the history this once. Create the chat only AFTER the guard
    # allowed and the model answered, so a blocked or failed question leaves no empty conversation behind.
    last_a = next((m["content"] for m in reversed(msgs[:-1]) if m["role"] == "assistant"), "")
    out = None if body.quote else _lookup_fast(token, user, text, last_a, 0, tracker)   # venue by name: no model (2026-10-26)
    if out is None:
        if body.quote:
            msgs = msgs[:-1] + [{"role": "user", "content": text + quote_block(body.quote, str(body.quote.get("_file_name") or ""))}]
        out = _run_guarded("refine", token, user, msgs, tracker=tracker)
    if out.get("stopped"):
        return out           # no chat created, nothing saved
    out["chips"] = ensure_chips(out.get("chips"))
    if out.get("price_locked"):
        out["chips"] = free_mode.chips(out["chips"], planning=config.COST_TOOLS_IN_CHAT)
    out["chips"] = _next_step(out)   # client-held turns carry no chips
    _open_ended(out)
    chat = xano.create_chat(user["id"], _question_title(text))
    earlier = msgs[:-1]

    def persist():   # earlier client-held turns (if any), then this question and its answer
        for t in earlier:
            xano.add_message(chat["id"], t["role"], t["content"])
        xano.add_message(chat["id"], "user", text)
        xano.add_message(chat["id"], "assistant", out["text"], _stored_cards(out), out["chips"])
        xano.touch_chat(chat)
        sheet_log.event(user, "Typed a question", text, out.get("text"), chat["id"], out.get("cards"),
                        out.get("search_filters"), sheet_log.flag_of(out))
    background.add_task(persist)
    return _public(out, chat_id=chat["id"])


@app.get("/rec/progress/{request_id}")
def get_progress(request_id: str, authorization: str = Header(None)):
    """Poll while an answer is being worked on (2026-10-25). Owner only: another user's id is a 404, so ids can't
    be probed. Unknown or expired (10 min after finishing) -> {"state": "unknown", "label": "", "step": 0}."""
    _, user = _auth(authorization)
    p = progress.get(request_id, user["id"])
    if p is None:
        raise HTTPException(404, "not found")
    return p


@app.post("/rec/cancel/{request_id}")
def cancel_request(request_id: str, authorization: str = Header(None)):
    """Stop (2026-10-25). The request stops at its next checkpoint - see progress.py and agent.run."""
    _, user = _auth(authorization)
    if not progress.cancel(request_id, user["id"]):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.get("/rec/chats")
def chats(authorization: str = Header(None)):
    _, user = _auth(authorization)
    return {"chats": xano.list_chats(user["id"])[:30]}


@app.get("/rec/chats/{chat_id}")
def chat_detail(chat_id: int, authorization: str = Header(None)):
    _, user = _auth(authorization)
    chat = xano.get_chat(user["id"], chat_id)
    if not chat:
        raise HTTPException(404, "conversation not found")
    turns = xano.get_messages(chat["id"])
    if not xano.has_paid_access(user):   # e.g. a lapsed member's old chats: no prices once the plan ends
        turns = free_mode.sanitize_turns(turns)
    left = _more_left(turns)
    last = max((i for i, t in enumerate(turns) if t["role"] == "assistant"), default=None)
    for i, t in enumerate(turns):
        t.pop("more_cards", None)         # unrevealed extras stay on the server until a question is spent
        t.pop("filters", None)            # server-side search state
        t.pop("upsell_shown", None)       # server-side: the upgrade prompt shows once per chat
        if i != last:
            t["questions"] = []           # only the latest answer's questions are still open
        t["more_available"] = left if i == last else 0
    return {"chat_id": chat["id"], "title": chat.get("title") or "", "turns": turns}


class MoreBody(BaseModel):
    chat_id: int
    request_id: str = Field("", max_length=progress.MAX_ID_LEN)   # optional: progress + Stop (2026-10-25)


MORE_TEXT = "Here are 3 more from the same search."   # until 2026-10-14; now more_text() describes the batch


def _more_reply(history, access=True):
    """The next "Show 3 more" batch of the latest answer, or None when fewer than 3 are left: its cards with a
    code-written line each on how they differ, a reply that names the search and the stand-out card, and chips.
    access=False (no plan): price-free lines and cards (free_mode)."""
    remaining = _more_remaining(history)
    if len(remaining) < agent.MORE_STEP:
        return None
    picks = remaining[:agent.MORE_STEP]
    filters = _active_filters(history)
    picks = [dict(c, reason=w) for c, w in zip(picks, agent.why_lines(picks, priced=access))]
    text = agent.more_text(picks, filters, priced=access)
    chips = more_chips(history)
    if not access:
        picks, chips = [free_mode.card(c) for c in picks], free_mode.chips(chips, planning=config.COST_TOOLS_IN_CHAT)
        text = free_mode.sanitize_text(text)[0]
    # more_chips carries the last answer's chips forward - an action chip there would show twice in a row.
    chips = place_action_chip(chips, history, topups=CHIP_TOPUPS if access else free_mode.TOPUPS)
    return {"text": text, "cards": picks, "chips": [] if config.OPEN_ENDED else chips, "left": len(remaining) - len(picks)}


@app.post("/rec/more")
def show_more(body: MoreBody, background: BackgroundTasks, authorization: str = Header(None)):
    """"Show 3 more" under the latest answer. Spends one question exactly like typing one (user decision
    2026-10-07): same gate, same usage row (kind refine), same counters. No model call - the venues were
    found by that answer's own searches and stored with it."""
    token, user = _auth(authorization)
    return _tracked(body.request_id, user, lambda tr: _show_more(user, body, background, tr))


def _show_more(user, body, background, tracker):
    _chat_used(body.chat_id)
    chat = xano.get_chat(user["id"], body.chat_id)
    if not chat:
        raise HTTPException(404, "conversation not found")
    history = xano.get_messages(chat["id"])
    if len(_more_remaining(history)) < agent.MORE_STEP:   # checked BEFORE the gate: never charge for nothing
        raise HTTPException(409, {"status": "no_more"})
    paid, access, rows, feedback_given, counted_free, base = _gate("refine", user, chat["id"])
    if tracker and tracker.cancelled():   # no model call here: the one checkpoint is before the question is spent
        _log_stopped(base, "more", user, paid, access)
        return _stopped("refine", paid, access, rows, feedback_given, chat_id=chat["id"])
    reply = _more_reply(history, access)
    picks, chips, text = reply["cards"], reply["chips"], reply["text"]
    xano.log_usage(dict(base, status="ok", counted_as_free=counted_free, model="none", input_tokens=0,
                        cache_read_tokens=0, cache_write_tokens=0, output_tokens=0, tool_calls=0,
                        cost_usd=0, latency_ms=0))
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": "more", "status": "ok", "forever": xano.has_forever(user), "beta_member": paid, "has_access": access, "source": "rec_service",
        "cost_usd": 0, "cards": len(picks), "more_cards": reply["left"],
        "free_refines_left": guards.free_refines_left(
            paid, rows + ([{"status": "ok", "counted_as_free": True}] if counted_free else []), feedback_given),
        "search_arm": railway_search.arm(user.get("id"))})

    def persist():   # same order as a typed question: user turn, assistant turn, bump the chat
        xano.add_message(chat["id"], "user", "Show 3 more")
        xano.add_message(chat["id"], "assistant", text, [dict(c, via_more=True) for c in picks], chips)
        xano.touch_chat(chat)
        sheet_log.event(user, "Tapped Show more", "Show 3 more", text, chat["id"], picks)
    background.add_task(persist)
    left = reply["left"]
    return dict(text=text, cards=picks, chips=chips, chat_id=chat["id"], paid=paid, has_access=access,
                more_available=left if left >= agent.MORE_STEP else 0, widgets=[], questions=[], upsell_prompt=None,
                **_counters("refine", paid, rows, counted_free, feedback_given, access))


class ToolBody(BaseModel):
    tool: str
    args: dict = {}
    chat_id: int = 0


# What the chat shows as the couple's side of a quick action (stored as the user turn, so the history alternates).
TOOL_LABELS = {"estimate_cost": "Estimate my cost", "split_budget": "Split my budget",
               "compare_venues": "Compare my venues", "budget_status": "Budget check"}


@app.post("/rec/tool")
def run_tool(body: ToolBody, background: BackgroundTasks, authorization: str = Header(None)):
    """A quick action (owner decision 2026-10-25): one cost tool, computed in code - NO model call, NOT counted
    toward free questions or the daily/monthly caps (its rec_usage row is kind "tool_<name>", cost 0, which
    guards._prompts leaves out). Respects the kill switch. Free users (no plan) get the same tools, but
    compare_venues never shows vendor PDF pricing (free_mode.widget). Args left out come from the profile.
    With chat_id the quick action and its widget are saved in that chat, so it shows in the history and the
    next typed question can refer to it."""
    token, user = _auth(authorization)
    if config.KILL_SWITCH:
        raise HTTPException(503, {"status": "killed"})
    name = (body.tool or "").strip()
    if name not in cost_tools.TOOLS:
        raise HTTPException(400, {"status": "unknown_tool", "tools": list(cost_tools.TOOLS)})
    chat = None
    if body.chat_id:
        chat = xano.get_chat(user["id"], body.chat_id)
        if not chat:
            raise HTTPException(404, "conversation not found")
        _chat_used(body.chat_id)
    access = xano.has_paid_access(user)
    paid = xano.beta_member(user)
    base = {"user_id": user["id"], "kind": "tool_" + name, "chat_id": int(body.chat_id or 0), "model": "none",
            "tier": "paid" if paid else "free", "counted_as_free": False, "input_tokens": 0, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "output_tokens": 0, "tool_calls": 0, "cost_usd": 0}
    t0 = dt.datetime.now(dt.timezone.utc)
    try:
        out = cost_tools.run_tool(name, body.args or {}, token, user, access=access, xano=xano)
    except Exception as e:
        _POOL.submit(xano.log_usage, dict(base, status="error", error=str(e)[:500]))
        raise HTTPException(502, {"status": "error"})
    widget = out["widget"] if access else free_mode.widget(out["widget"])
    text = cost_tools.summary_text(widget)
    # Next-step chips (owner 2026-10-25), computed in code like the widget - flag-independent, as /rec/tool is.
    # No chat history is read here (one more Xano call per tap); the next typed reply's de-dupe sees these chips.
    chips = tool_chips(name, free=not access, widget=widget)
    ms = int((dt.datetime.now(dt.timezone.utc) - t0).total_seconds() * 1000)
    _POOL.submit(xano.log_usage, dict(base, status="ok", latency_ms=ms))
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": "tool_" + name, "status": "ok", "forever": xano.has_forever(user), "beta_member": paid,
        "has_access": access, "source": "rec_service", "cost_usd": 0, "latency_s": round(ms / 1000.0, 1),
        "widget": widget.get("type"), "locked": bool(widget.get("locked")), "chat_id": int(body.chat_id or 0),
        "search_arm": railway_search.arm(user.get("id"))})
    sheet_log.event(user, "Used a quick action", TOOL_LABELS[name], text, int(body.chat_id or 0))
    if chat:
        def persist():   # same order as a typed question: the quick action, then the answer with its widget
            xano.add_message(chat["id"], "user", TOOL_LABELS[name])
            xano.add_message(chat["id"], "assistant", text, [{"meta": "widget", "widget": widget}], chips)
            xano.touch_chat(chat)
        background.add_task(persist)
    return {"tool": name, "text": text, "widgets": [widget], "chips": [] if config.OPEN_ENDED else chips, "chat_id": chat["id"] if chat else None,
            "price_locked": not access}


class ContextBody(BaseModel):
    context: str = ""


class NoteDelete(BaseModel):
    index: int


@app.get("/rec/context")
def get_context(authorization: str = Header(None)):
    """What the assistant knows about the couple: their own context + the notes it saved."""
    _, user = _auth(authorization)
    notes, user_context = _memory(user["id"])
    return {"context": user_context, "context_max": xano.CONTEXT_MAX, "notes": notes}


@app.post("/rec/context")
def set_context(body: ContextBody, authorization: str = Header(None)):
    _, user = _auth(authorization)
    saved = xano.set_user_context(user["id"], body.context)
    _POOL.submit(xano.mp_track, user["id"], "rec_context_saved", {"chars": len(saved), "source": "rec_service"})
    return {"context": saved, "context_max": xano.CONTEXT_MAX, "notes": _memory(user["id"])[0]}


class PrefsBody(BaseModel):
    answers: dict = {}       # {question_id: [option value, ...]} - unknown ids/values are dropped
    skipped: bool = False    # "Skip for now": remember not to push the questions again
    merge: bool = False      # 2026-10-26: add/replace only these answers (an opening question tapped on the page,
                             # e.g. {"answers": {"area": ["Dallas, Texas"]}, "merge": true}); no question is spent
    context: Optional[str] = None   # last step "Anything else Tulle should know?" = the couple's own context
                                    # (same field as /rec/context; None = leave it alone, "" = clear it)


@app.get("/rec/preferences")
def get_preferences(authorization: str = Header(None)):
    """The preference questions + this couple's current answers. asked=False -> show them before the picks."""
    _, user = _auth(authorization)
    row = _memory_row(user["id"])
    raw = row.get("notes") or []
    answers = prefs.from_notes(raw)
    return {"questions": prefs.QUESTIONS, "answers": {k: v for k, v in answers.items() if k in prefs.QUESTION_IDS},
            "profile_answers": {k: v for k, v in answers.items() if k in prefs.INLINE_IDS}, "asked": prefs.asked(raw),
            "context": row.get("user_context") or "", "context_max": xano.CONTEXT_MAX}


@app.post("/rec/preferences")
def save_preferences(body: PrefsBody, authorization: str = Header(None)):
    """Save answers into the couple's memory. `added` = the lines to show as 'Added to what Tulle Assistant
    knows about you' (user 2026-10-08: tell them we're adding to their context)."""
    _, user = _auth(authorization)
    answers = prefs.clean(body.answers)
    has_context = bool((body.context or "").strip())
    if body.merge:
        added = xano.merge_preferences(user["id"], answers)
    else:
        added = xano.set_preferences(user["id"], answers, skipped=body.skipped and not answers and not has_context)
    lines = [n["note"] for n in added if n.get("note")]
    if body.context is not None:
        saved = xano.set_user_context(user["id"], body.context)
        if saved:
            lines.append("Your notes: " + (saved if len(saved) <= 80 else saved[:77].rstrip() + "..."))
    _POOL.submit(xano.mp_track, user["id"], "rec_prefs_saved", {
        "answered": sorted(answers), "n_answered": len(answers), "context_chars": len((body.context or "").strip()),
        "skipped": bool(body.skipped and not answers and not has_context), "source": "rec_service"})
    notes, user_context = _memory(user["id"])
    return {"answers": answers, "added": lines, "notes": notes, "context": user_context}


@app.post("/rec/notes/delete")
def delete_note(body: NoteDelete, authorization: str = Header(None)):
    _, user = _auth(authorization)
    notes = xano.delete_memory_note(user["id"], body.index)
    return {"notes": [n.get("note") for n in notes if n.get("note")]}


class NoteAdd(BaseModel):
    note: str = ""           # the couple's own words, e.g. "a barn near Austin under $8k"
    source: str = ""         # dead_end:<code> | error:<code> - where in the chat it was typed
    chat_id: int = 0


@app.post("/rec/notes")
def add_note(body: NoteAdd, authorization: str = Header(None)):
    """Owner 2026-11-01: a dead-end answer asks 'what were you after?'. Couples read that as 'Tulle will keep this
    in mind', not as a thumbs-down, so keep that promise: the line goes into their memory notes (what the agent
    reads at the start of every chat) as 'Still looking for: ...'. The team gets a quiet Slack line
    (notify.still_looking), not the thumbs-down notice."""
    _, user = _auth(authorization)
    text = " ".join((body.note or "").split()).strip()
    if len(text) < 2:
        raise HTTPException(status_code=400, detail="note too short")
    note = "Still looking for: " + text[:170]
    xano.add_memory_note(user["id"], note)
    _POOL.submit(xano.mp_track, user["id"], "rec_note_added", {"chars": len(text), "source": body.source or "", "kind": "still_looking"})
    # Team copy: a quiet Slack line, not the thumbs-down notice (owner 2026-11-01).
    _POOL.submit(notify.still_looking, user, text, xano.plan_label(user), body.source or "", int(body.chat_id or 0))
    sheet_log.event(user, "Feedback: still looking for", text, "", int(body.chat_id or 0))
    notes, _ctx = _memory(user["id"])
    return {"note": note, "notes": notes}


class FeedbackBody(BaseModel):
    rating: int = 0          # 1-5, how useful so far
    would_use: str = ""      # yes / maybe / no
    text: str = ""           # what's working, what isn't
    chat_id: int = 0


@app.post("/rec/feedback")
def beta_feedback(body: FeedbackBody, authorization: str = Header(None)):
    """Beta check-in. One row per submission. Paid members: the first one unlocks questions for good.
    Free users (2026-10-14): required once after their free question(s); it earns FREE_FEEDBACK_BONUS more
    questions (Railway 1 since 2026-10-27), then the paywall.
    One tap (owner 2026-10-27): the rating alone is enough. 'Keep using it' and the comment are optional - the
    10-character minimum cut completion to ~2% (58 free users shown the card 10-10..10-27, 1 sent). A comment sent
    after the rating is just another row."""
    _, user = _auth(authorization)
    text = (body.text or "").strip()[:2000]
    would = (body.would_use or "").strip().lower()
    if not 1 <= body.rating <= 5:
        raise HTTPException(400, {"status": "rating_required"})
    if would not in ("yes", "maybe", "no"):
        would = ""
    paid = xano.beta_member(user)
    plan = xano.plan_label(user)
    rows = xano.user_usage(user["id"])
    used = guards.forever_questions(rows) if paid else guards.free_used(rows)
    xano.add_beta_feedback({"user_id": int(user["id"]), "rating": body.rating, "would_use": would, "text": text,
                            "questions_used": used, "chat_id": int(body.chat_id or 0)})
    _POOL.submit(xano.mp_track, user["id"], "rec_beta_feedback", {
        "rating": body.rating, "would_use": would, "chars": len(text), "forever": plan == "Forever",
        "plan": plan, "questions_used": used, "source": "rec_service"})
    _POOL.submit(notify.beta_feedback, user, body.rating, would, text, used, plan)
    sheet_log.event(user, "Feedback: rated %d/5" % body.rating, text, "", int(body.chat_id or 0))
    access = xano.has_paid_access(user)
    free_left = guards.free_refines_left(paid, rows, True)
    forever_left = guards.forever_left_today(paid, rows, xano.today())
    return {"ok": True, "beta_questions_left": None, "free_refines_left": free_left,
            "forever_left_today": forever_left,
            **_tier_fields(paid, access, True, None, forever_left, free_left, False, rows)}


class ProfileBody(BaseModel):
    """Quick profile update from the Planner chat (2026-10-31). Every field optional; only the ones given are written."""
    guests: Optional[int] = None        # Wedding_Guest_Count, 2..1500
    budget: Optional[int] = None        # Wedding_Budget in dollars, 1,000..5,000,000
    states: Optional[list[str]] = None  # Wedding_Location_Updated: full US state names / "International", max 5
    date: Optional[str] = None          # Wedding_Date, YYYY-MM-DD


PROFILE_STATES = set(profile_questions.CITIES_BY_STATE) | {"International"}
PROFILE_MAX_STATES = 5


def profile_fields(body):
    """(Xano columns to write, invalid field names). Pure, so tests can check the mapping alone."""
    fields, bad = {}, []
    if body.guests is not None:
        if 2 <= int(body.guests) <= 1500:
            fields["Wedding_Guest_Count"] = int(body.guests)
        else:
            bad.append("guests")
    if body.budget is not None:
        if 1000 <= int(body.budget) <= 5_000_000:
            fields["Wedding_Budget"] = int(body.budget)
        else:
            bad.append("budget")
    if body.states is not None:
        sts = []
        for s in body.states:
            s = str(s or "").strip()
            if s in PROFILE_STATES:
                if s not in sts:
                    sts.append(s)
            elif s:
                bad.append("states")
        if "states" not in bad:
            fields["Wedding_Location_Updated"] = sts[:PROFILE_MAX_STATES]
    if body.date is not None:
        d = str(body.date or "").strip()[:10]
        try:
            dt.date.fromisoformat(d)
            fields["Wedding_Date"] = d
        except ValueError:
            bad.append("date")
    return fields, sorted(set(bad))


@app.post("/rec/profile")
def update_profile(body: ProfileBody, authorization: str = Header(None)):
    """The Planner chat's quick profile edit (owner 2026-10-31, roadmap item 2: couples change guests / budget /
    location / date inside the assistant). Writes through ep21 user/update_record with the couple's own token
    (xano.update_profile), tracks rec_profile_updated, and returns the fresh profile. Counts nothing. The next
    opening regenerates on its own: the profile is part of the opening-reuse fingerprint."""
    token, user = _auth(authorization)
    fields, bad = profile_fields(body)
    if bad:
        raise HTTPException(400, {"status": "invalid", "fields": bad})
    if not fields:
        raise HTTPException(400, {"status": "nothing_to_update"})
    try:
        xano.update_profile(token, fields)
    except xano.XanoError as e:
        print("update_profile failed:", repr(e)[:200])
        raise HTTPException(502, {"status": "error"})
    fresh = xano.verify_user(token) or dict(user, **fields)
    _POOL.submit(xano.mp_track, user["id"], "rec_profile_updated", {
        "fields": sorted(fields), "source": "rec_service", "plan": xano.plan_label(user)})
    return {"ok": True, "updated": sorted(fields), "profile": xano.profile_of(fresh)}


class PdfBody(BaseModel):
    vendor_id: str = ""
    vendor_name: str = ""
    chat_id: int = 0


def _pdf_entitled(user):
    """Can this couple open any PDF without spending a free view: Forever, or an access date on or after today.
    Dates are compared as dates (the vendor page's string compare fails on the access day itself when the field
    carries a time - project_pdf_warning_view3)."""
    if xano.has_forever(user):
        return True
    raw = str(user.get("date_until_access") or "")[:10]
    return bool(raw) and raw >= dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _pdf_accounting(token, user, vendor_id, vendor_name, fields):
    """After the answer went back: the same two writes the site makes - ep21 user/update_record (total_pdf_views
    + 1 and the free-view meter, exactly the body the vendor page's View PDF workflow sends) and the Discovery card's
    user/update_vendor_views. Failures are logged, never shown: the PDF is already open."""
    try:
        xano.update_profile(token, fields)
    except Exception as e:
        print("pdf view record failed:", repr(e)[:200])
    try:
        xano.record_vendor_view(token, user, vendor_id, vendor_name)
    except Exception as e:
        print("vendor view record failed:", repr(e)[:200])


# 2026-11-09 PDF GATE (owner: "I was able to open a LOT of pdfs right off the bat"). Table 79 showed one free account
# opening 6 PDFs, 5 of them inside 15 s, every one "free", while FreeViewsRemaining only went 2 -> 1: each request read
# the count from auth/me BEFORE the previous request's background write had landed, so they all spent "the same" view.
# Now, per couple: one PDF decision at a time (lock), the free-view write happens BEFORE the URL is returned, and the
# last count we wrote is remembered for 30 min so a stale auth/me can never hand out a view that is already spent.
_PDF_LOCKS = {}
_PDF_LOCKS_GUARD = threading.Lock()
_PDF_LEFT = {}            # user_id -> (free views left after our last write, unix time)
PDF_LEFT_TTL = 1800


def _pdf_lock(uid):
    with _PDF_LOCKS_GUARD:
        lk = _PDF_LOCKS.get(uid)
        if lk is None:
            lk = _PDF_LOCKS[uid] = threading.Lock()
        return lk


def _pdf_views_now(user):
    """FreeViewsRemaining from auth/me, never more than what our own last write left (auth/me can lag behind it)."""
    views = _free_views(user)
    seen = _PDF_LEFT.get(user["id"])
    if seen and time.time() - seen[1] < PDF_LEFT_TTL:
        views = min(views, seen[0])
    return views


def _pdf_vendor_view(token, user, vendor_id, vendor_name):
    try:
        xano.record_vendor_view(token, user, vendor_id, vendor_name)
    except Exception as e:
        print("vendor view record failed:", repr(e)[:200])


@app.post("/rec/pdf")
def open_pdf(body: PdfBody, authorization: str = Header(None)):
    """The Planner card's "Open pricing PDF" (owner 2026-11-01): open the vendor's PRIMARY pricing PDF directly, and
    account for it exactly as if the couple had opened the vendor page and tapped that PDF there - the vendor-view
    PATCH every Discovery card makes, then the vendor page's own View PDF rule: a plan or Forever opens it and counts
    total_pdf_views; a free couple spends one free view (0 views = no PDF, the upgrade path); Mixpanel gets
    viewed_vendor_page + viewed_vendor_pdf (or pdf_limit_popup_triggered) with source "planner". Returns {url, mode,
    views_left, last_free}; 404 no_pdf when the vendor has no PDF link (the card then opens the vendor page)."""
    token, user = _auth(authorization)
    vid = (body.vendor_id or "").strip()
    if not vid:
        raise HTTPException(400, "vendor_id required")
    try:
        rows = xano.pdf_rows(token, vid)
    except xano.XanoError as e:
        print("pdf_rows failed:", repr(e)[:200])
        raise HTTPException(502, {"status": "error"})
    row = xano.primary_pdf(rows)
    base = {"vendor_id": vid, "source": "planner", "chat_id": body.chat_id or 0, "plan": xano.plan_label(user)}
    if not row:
        _POOL.submit(xano.mp_track, user["id"], "rec_pdf_missing", base)
        raise HTTPException(404, {"status": "no_pdf"})
    entitled = _pdf_entitled(user)
    total = int(user.get("total_pdf_views") or 0)
    lock = _pdf_lock(user["id"])
    with lock:
        views = _free_views(user) if entitled else _pdf_views_now(user)
        if entitled:
            mode, left = "paid", views
        elif views > 0:
            mode, left = "free", views - 1
        else:
            mode, left = "blocked", 0
        if mode == "free":
            # Spend the view NOW (before the URL goes back), and remember it, so the next tap can't reuse it.
            _PDF_LEFT[user["id"]] = (left, time.time())
            try:
                xano.update_profile(token, {"id": user["id"], "total_pdf_views": total + 1, "free_views_remaining": left})
            except Exception as e:
                print("pdf free-view write failed:", repr(e)[:200])   # the in-memory count still holds the line
    # The usage log (table 79) gets a 'pdf' row per open attempt (2026-11-02: the daily brief's PDF funnel reads
    # Xano, not Mixpanel): status = paid | free | blocked, no cost, chat_id when the card came from a chat.
    _POOL.submit(xano.log_usage, {"user_id": user["id"], "kind": "pdf", "status": mode, "counted_as_free": mode == "free",
                                  "model": "", "chat_id": body.chat_id or 0, "tools": "pdf:" + vid, "categories": "venue"})
    sheet_log.event(user, "Opened a pricing PDF", body.vendor_name or vid, "", body.chat_id or 0,
                    [{"name": body.vendor_name or vid}],
                    flag="blocked: no free views left" if mode == "blocked" else ("free view" if mode == "free" else ""))
    if mode == "blocked":
        _POOL.submit(xano.mp_track, user["id"], "pdf_limit_popup_triggered", base)
        return {"url": None, "mode": mode, "views_left": 0, "last_free": False}
    if mode == "free":
        _POOL.submit(_pdf_vendor_view, token, user, vid, body.vendor_name or "")   # the view count was written above
    else:
        fields = {"id": user["id"], "total_pdf_views": total + 1, "free_views_remaining": left}
        _POOL.submit(_pdf_accounting, token, user, vid, body.vendor_name or "", fields)
    _POOL.submit(xano.mp_track, user["id"], "viewed_vendor_page", base)
    _POOL.submit(xano.mp_track, user["id"], "viewed_vendor_pdf", dict(base, mode=mode, views_left=left,
                                                                       pdf_id=row.get("PDF_ID") or row.get("id")))
    return {"url": row.get("PDF_Link"), "mode": mode, "views_left": left, "last_free": mode == "free" and views == 1,
            "year": row.get("Year_of_Pricing") or ""}


class AnswerFeedbackBody(BaseModel):
    chat_id: int = 0
    answer_index: int = 0     # 0-based count of real answers in the chat (same key the page uses for thumbs)
    text: str = ""            # the couple's own words after a thumbs down (optional for a thumbs up)
    vote: str = "down"        # "down" (comment required) or "up" (2026-10-16: owner wants thumbs up in Slack too)


@app.post("/rec/answer-feedback")
def answer_feedback(body: AnswerFeedbackBody, authorization: str = Header(None)):
    """Thumbs-down comment (2026-10-16, owner: 'we need real raw feedback'). Posts to Slack #feedback with the
    question and answer it is about, and tracks rec_answer_feedback. Counts nothing, unlocks nothing. Never fails
    the page on a Slack or lookup problem."""
    _, user = _auth(authorization)
    text = (body.text or "").strip()[:2000]
    vote = "up" if str(body.vote or "").lower() == "up" else "down"
    if vote == "down" and len(text) < 2:
        raise HTTPException(400, {"status": "text_too_short"})
    question, answer = "", ""
    try:
        chat = xano.get_chat(user["id"], int(body.chat_id or 0)) if body.chat_id else None
        if chat:
            turns = xano.get_messages(chat["id"])
            answers = [i for i, t in enumerate(turns) if t["role"] == "assistant"]
            if 0 <= body.answer_index < len(answers):
                i = answers[body.answer_index]
                answer = turns[i].get("text") or ""
                question = next((turns[j].get("text") or "" for j in range(i - 1, -1, -1) if turns[j]["role"] == "user"), "")
    except Exception as e:   # context is nice to have; the comment itself is what matters
        print("answer feedback lookup failed:", repr(e)[:200])
    plan = xano.plan_label(user)
    _POOL.submit(xano.mp_track, user["id"], "rec_answer_feedback", {
        "chat_id": int(body.chat_id or 0), "answer_index": body.answer_index, "chars": len(text), "plan": plan, "vote": vote,
        "found_answer": bool(answer), "source": "rec_service"})
    _POOL.submit(notify.answer_feedback, user, text, question, answer, plan, int(body.chat_id or 0), vote)
    if vote == "down" and config.SOURCED_ANSWERS:
        # 2026-11-07 (owner: "take feedback into account"): the couple's correction becomes a durable note the
        # Planner reads on every later answer (rec_memory -> "What we learned in earlier conversations").
        note = "The couple said an answer was wrong or unhelpful: \"%s\"%s. Take it into account; re-check sources." % (
            text[:300], (" (they had asked: %s)" % question[:160]) if question else "")
        _POOL.submit(xano.add_memory_note, user["id"], note)
    sheet_log.event(user, "Feedback: thumbs %s" % vote, text or "(no comment)",
                    ("On: %s\n\n%s" % (question, answer)) if (question or answer) else "", int(body.chat_id or 0))
    return {"ok": True}


class PlanAddBody(BaseModel):
    vendor_id: str = ""
    vendor_name: str = ""
    category: str = ""        # the card's category (Venue, Photography, ...); "" = Venue
    est_cost: int = 0
    chat_id: int = 0
    source: str = "planner_card"
    note: str = ""


class PlanRemoveBody(BaseModel):
    item_id: int = 0


def _plan_view(user, rows=None):
    access = xano.has_paid_access(user)
    rows = xano.plan_items(user["id"]) if rows is None else rows
    budget = xano.profile_of(user).get("Wedding_Budget") or 0
    return dict(plan.summary(rows, budget, show_prices=access), enabled=plan.arm(user["id"]) == "plan",
                ab_plan=plan.arm(user["id"]))


@app.get("/rec/plan")
def plan_get(authorization: str = Header(None)):
    """The couple's "My wedding" plan (2026-11-07): every slot in booking order, filled or open."""
    _, user = _auth(authorization)
    return _plan_view(user)


@app.post("/rec/plan/add")
def plan_add(body: PlanAddBody, authorization: str = Header(None)):
    """Add a vendor to a slot of the couple's wedding. Free for everyone. Same vendor twice = no second row."""
    _, user = _auth(authorization)
    name = (body.vendor_name or "").strip()[:200]
    vid = (body.vendor_id or "").strip()[:40]
    if not (vid or name):
        raise HTTPException(400, {"status": "nothing_to_add"})
    rows = xano.plan_items(user["id"])
    if vid and any(r.get("vendor_id") == vid for r in rows):
        return dict(_plan_view(user, rows), added=False, already=True)
    slot = plan.slot_of(body.category)
    row = {"user_id": user["id"], "slot": slot, "vendor_id": vid, "vendor_name": name,
           "est_cost": max(0, min(int(body.est_cost or 0), 10_000_000)), "note": (body.note or "").strip()[:300],
           "source": (body.source or "planner_card")[:40], "chat_id": int(body.chat_id or 0), "status": "active"}
    created = xano.plan_add(row) or row
    rows = rows + [created]
    _POOL.submit(xano.mp_track, user["id"], "rec_plan_add", {
        "slot": slot, "vendor_id": vid, "source": row["source"], "chat_id": row["chat_id"], "items_after": len(rows),
        "slots_filled_after": plan.summary(rows)["filled"], "has_cost": bool(row["est_cost"])})
    sheet_log.event(user, "Added to my wedding", "%s (%s)" % (name or vid, slot), "", row["chat_id"],
                    [{"name": name or vid}])
    return dict(_plan_view(user, rows), added=True, already=False)


@app.post("/rec/plan/remove")
def plan_remove(body: PlanRemoveBody, authorization: str = Header(None)):
    _, user = _auth(authorization)
    ok = xano.plan_remove(user["id"], body.item_id) if body.item_id else False
    if ok:
        _POOL.submit(xano.mp_track, user["id"], "rec_plan_remove", {"item_id": body.item_id})
    return dict(_plan_view(user), removed=bool(ok))


class WeddingBookBody(BaseModel):
    vendor_id: str = ""
    name: str = ""
    category: str = ""
    price: int = 0
    paid: int = 0
    notes: str = ""
    file: Optional[dict] = None     # attachment object from the app's POST /upload/attachment (11-08 v2)


class WeddingItemBody(BaseModel):
    item_id: int = 0


class WeddingEditBody(BaseModel):
    item_id: int = 0
    notes: Optional[str] = None
    file: Optional[dict] = None
    clear_file: bool = False
    price: Optional[int] = None
    paid: Optional[int] = None
    selected: Optional[bool] = None   # v3: Booked on an existing Budget-tab quote = the same row flips to Selected true


def _wedding_view(token, user, packages=None):
    if not wedding.ENABLED:
        return {"enabled": False}
    access = xano.has_paid_access(user)
    pk = xano.user_packages(token, user["id"]) if packages is None else packages
    ids = xano.saved_vendor_ids(user)
    with ThreadPoolExecutor(max_workers=8) as ex:
        cards = [c for c in ex.map(xano.cached_vendor_card, ids) if c]
    return wedding.view(user, pk, cards, show_prices=access)


@app.get("/rec/wedding")
def wedding_get(authorization: str = Header(None)):
    """'My wedding' (2026-11-08): budget left, the four covered categories with saved + booked vendors, other costs."""
    token, user = _auth(authorization)
    v = _wedding_view(token, user)
    _POOL.submit(xano.mp_track, user["id"], "rec_wedding_view", {"enabled": v.get("enabled"),
                 "booked": sum(1 for c in v.get("categories") or [] if c.get("booked")),
                 "saved": sum(len(c.get("saved") or []) for c in v.get("categories") or [])})
    return v


@app.post("/rec/wedding/book")
def wedding_book(body: WeddingBookBody, authorization: str = Header(None)):
    """Mark booked / + Add a cost: a new budget item (table 33, Selected true). Free for everyone."""
    token, user = _auth(authorization)
    if not wedding.ENABLED:
        raise HTTPException(503, {"status": "off"})
    name = (body.name or "").strip()[:200]
    price = max(0, min(int(body.price or 0), 10_000_000))
    if not name or not price:
        raise HTTPException(400, {"status": "name_and_price_required"})
    vid = (body.vendor_id or "").strip()[:40]
    row = {"user_id": user["id"], "vendor_name": name, "total_cost": price,
           "Balance_Paid": max(0, min(int(body.paid or 0), price)), "Balance_Left": price - max(0, min(int(body.paid or 0), price)),
           "Category": wedding.table_category(body.category), "Selected": True,
           "Notes": (body.notes or "").strip()[:2000],
           "Package_Name": "Booked in My wedding · tulle:" + (vid or "manual")}
    if vid:
        row["vendor_id"] = vid                        # 11-08 column: links the card to the vendor's Tulle page
    att = wedding.clean_attachment(body.file)
    if att:
        row["Files"] = att
    xano.package_add(row)
    _POOL.submit(xano.mp_track, user["id"], "rec_wedding_book", {"category": row["Category"], "has_vendor": bool(vid),
                 "price": price})
    sheet_log.event(user, "Marked booked", "%s (%s) $%s" % (name, row["Category"], price), "", 0, [{"name": name}])
    return _wedding_view(token, user)


@app.post("/rec/wedding/unbook")
def wedding_unbook(body: WeddingItemBody, authorization: str = Header(None)):
    """Undo: the item stops counting (Selected false). Never deleted."""
    token, user = _auth(authorization)
    if not wedding.ENABLED:
        raise HTTPException(503, {"status": "off"})
    ok = xano.package_set_selected(user["id"], body.item_id, False) if body.item_id else False
    if ok:
        _POOL.submit(xano.mp_track, user["id"], "rec_wedding_unbook", {"item_id": body.item_id})
    return dict(_wedding_view(token, user), undone=bool(ok))


@app.post("/rec/wedding/item")
def wedding_item(body: WeddingEditBody, authorization: str = Header(None)):
    """Edit one booked item in place (11-08 v2): the couple's notes, the file (contract / pricing PDF), price, paid."""
    token, user = _auth(authorization)
    if not wedding.ENABLED:
        raise HTTPException(503, {"status": "off"})
    changes = {}
    if body.notes is not None:
        changes["Notes"] = body.notes.strip()[:2000]
    if body.clear_file:
        changes["Files"] = None
    elif body.file is not None:
        att = wedding.clean_attachment(body.file)
        if not att:
            raise HTTPException(400, {"status": "bad_file"})
        changes["Files"] = att
    if body.selected is not None:
        changes["Selected"] = bool(body.selected)
    ok = xano.package_update(user["id"], body.item_id, changes, body.price, body.paid) if body.item_id else False
    if not ok:
        raise HTTPException(404, {"status": "not_found"})
    _POOL.submit(xano.mp_track, user["id"], "rec_wedding_item_edit", {"notes": body.notes is not None,
                 "file": bool(body.file) and not body.clear_file, "clear_file": body.clear_file,
                 "selected": body.selected})
    return _wedding_view(token, user)


class EventBody(BaseModel):
    kind: str = ""            # vendor_click | save | unsave
    vendor_id: str = ""
    vendor_name: str = ""
    chat_id: int = 0


EVENT_ACTIONS = {"vendor_click": "Clicked View vendor", "save": "Saved a vendor", "unsave": "Removed a saved vendor"}


@app.post("/rec/event")
def planner_event(body: EventBody, authorization: str = Header(None)):
    """Card actions only the page sees (owner 2026-11-03: the team's Planner sheet). Logs a sheet row; counts
    nothing, never fails the page (an unknown kind is ignored)."""
    _, user = _auth(authorization)
    action = EVENT_ACTIONS.get((body.kind or "").strip())
    if action:
        name = (body.vendor_name or body.vendor_id or "").strip()[:200]
        sheet_log.event(user, action, name, "", int(body.chat_id or 0), [{"name": name}] if name else None)
    return {"ok": True}


@app.get("/rec/status")
def status(authorization: str = Header(None)):
    _, user = _auth(authorization)
    paid = xano.beta_member(user)
    access = xano.has_paid_access(user)
    rows = xano.user_usage(user["id"])
    fb = _feedback_given(user["id"]) if paid or not config.FOREVER_ONLY else True
    beta_left = guards.beta_questions_left(paid, rows, fb)
    free_left = guards.free_refines_left(paid, rows, fb)
    forever_left = guards.forever_left_today(paid, rows, xano.today())
    return {"paid": paid, "has_access": access, "upsell": "forever", "forever": xano.has_forever(user),
            "beta_questions_left": beta_left, "beta_feedback_after": config.BETA_FEEDBACK_AFTER,
            "forever_only": config.FOREVER_ONLY, "free_mode": not config.FOREVER_ONLY,
            "forever_left_today": forever_left,
            "forever_daily_limit": config.PAID_DAILY_REFINES, "free_limit": config.FREE_REFINES, "free_refines_left": free_left,
            **_tier_fields(paid, access, fb, beta_left, forever_left, free_left,
                           guards.free_feedback_required(paid, rows, fb), rows),
            "enabled": not config.KILL_SWITCH, "memory_notes": _memory_notes(user["id"]),
            "cost_tools_in_chat": config.COST_TOOLS_IN_CHAT,
            # Railway search rollout (2026-10-26): this user's stable bucket, so the split can be checked live.
            "search_arm": railway_search.arm(user.get("id")), "railway_search_pct": config.RAILWAY_SEARCH_PCT,
            # "My wedding" A/B (2026-11-07): this couple's arm; the page shows Add + the plan only when enabled.
            "ab_plan": plan.arm(user.get("id")), "plan_enabled": plan.arm(user.get("id")) == "plan",
            "prefs_asked": prefs.asked(_memory_row(user["id"]).get("notes") or [])}


@app.get("/health")
def health():
    return {"ok": True, "model": config.MODEL, "enabled": not config.KILL_SWITCH,
            "time": dt.datetime.now(dt.timezone.utc).isoformat(),
            # Pricing cache + warmer (2026-11-02): entries now held, and the last warm-up's counts.
            "pricing_cache": {"entries": len(xano.PRICING_CACHE), "ttl_s": config.PRICING_CACHE_S,
                              "warm": dict(pricing_warm.last)}}
