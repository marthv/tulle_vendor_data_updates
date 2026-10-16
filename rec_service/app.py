"""Tulle recommendation service (Railway). WeWeb calls it with the user's Xano auth token.

POST /rec/opening          -> new conversation with opening picks; returns chat_id
                              (beta: Forever only - everyone else gets 402 forever_only)
POST /rec/refine           -> {chat_id, message}: one refinement in that conversation (FREE_REFINES free - 1 since 2026-10-14 - then 402)
                              no chat_id ({messages:[...]} or {message}): continues the user's chat from the
                              last 30 min, else starts one; turns saved, chat_id returned (2026-10-12)
GET  /rec/chats            -> the user's conversations, newest first
GET  /rec/chats/{chat_id}  -> one conversation's turns (text, cards, chips) to re-render it
POST /rec/more {chat_id}   -> "Show 3 more": next 3 venues from the latest answer's searches. Spends ONE
                              question exactly like a typed one (user 2026-10-07), same limits and errors.
GET  /rec/status           -> free refinements left, paid flag, whether the service is on, and the page-state
                              keys tier / price_locked / questions_left / feedback_required / feedback_done /
                              paywall (also on every answer and every blocked response)
POST /rec/feedback         -> beta check-in {rating, would_use, text}. Paid members: unlocks questions.
                              Free users (2026-10-14): required once after their free question(s); earns nothing more.

FREE MODE (owner decisions 2026-10-14, on when REC_FOREVER_ONLY=0): every signed-in user may use the assistant.
No active plan -> tier "free": opening + FREE_REFINES questions for life (1 since 2026-10-14), then one required feedback, then the paywall,
and NO price of any kind in text, cards or chips (free_mode.py; price_locked=true).
GET  /health

Conversations live in Xano rec_chats (42) / rec_messages (43); durable notes the model saves
about the couple live in rec_memory (80) and are fed into every later conversation.
"""
from typing import Optional
import datetime as dt

from concurrent.futures import ThreadPoolExecutor

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import agent
from chips import STARTER_CHIPS, dedupe_chips, ensure_chips, more_chips
import config
import free_mode
import guards
import notify
import prefs
import xano

app = FastAPI(title="Tulle recommendations")
# The live app, plus WeWeb's editor/preview hosts so the page can be tested before publishing.
# Every request still needs a valid user token; CORS only decides which pages may call us.
app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS,
                   allow_origin_regex=r"https://([a-z0-9-]+\.)*weweb(-preview)?\.(io|app)",
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

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


def _public(out, **extra):
    """What the page gets: the hidden extras become a count (they cost a question to reveal); the search
    filters stay server-side (stored with the answer for "Show 3 more" and the next question)."""
    resp = {k: v for k, v in out.items() if k not in ("more_cards", "search_filters")}
    n = len(out.get("more_cards") or [])
    return dict(resp, more_available=n if n >= agent.MORE_STEP else 0, **extra)


def _stored_cards(out):
    """Picks + "Show 3 more" cards in one cards column; xano.get_messages splits them on read. The answer's
    search filters ride along as one hidden {"meta": "search"} entry (no schema change; parse_message_row
    takes it out again, so no reader of get_messages ever sees it as a card)."""
    stored = list(out.get("cards") or []) + [dict(c, more=True) for c in (out.get("more_cards") or [])]
    if out.get("search_filters"):
        stored.append({"meta": "search", "filters": out["search_filters"]})
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


def _gate(kind, user, chat_id=0):
    """The one question check. Raises the blocked response (402/403/429/503) or returns what the caller
    needs to log the use and report the counters. Used by typed questions AND "Show 3 more"."""
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
    # tier is written for the free cap; Xano drops it until table 79 has a `tier` column (see config).
    base = {"user_id": user["id"], "kind": kind, "chat_id": chat_id, "model": config.MODEL,
            "tier": "paid" if paid else "free"}
    if not allowed:
        xano.log_usage(dict(base, status=status))
        _POOL.submit(xano.mp_track, user["id"], "rec_server_request",
                     {"kind": kind, "status": status, "forever": xano.has_forever(user), "beta_member": paid, "has_access": access, "source": "rec_service"})
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
                                                  guards.free_feedback_required(paid, rows, feedback_given))})
    return paid, access, rows, feedback_given, counted_free, base


def _tier_fields(paid, access, feedback_given, beta_left, forever_left, free_left, free_fb):
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
            "paywall": (not paid) and (config.FOREVER_ONLY or (not fb_req and not left)),
            "free_feedback_required": bool(free_fb), "free_feedback_bonus": config.FREE_FEEDBACK_BONUS}


def _counters(kind, paid, rows, counted_free, feedback_given, access=True):
    """Allowance left AFTER this request, for the page's counters."""
    used = rows + ([{"status": "ok", "kind": "refine", "counted_as_free": True}] if counted_free else [])
    used_today = rows + [{"status": "ok", "usage_day": xano.today()}]
    used_fq = rows + ([{"status": "ok", "kind": "refine"}] if paid and kind == "refine" else [])
    c = dict(upsell="forever",
             beta_questions_left=guards.beta_questions_left(paid, used_fq, feedback_given),
             beta_feedback_after=config.BETA_FEEDBACK_AFTER,
             free_refines_left=guards.free_refines_left(paid, used, feedback_given),
             forever_left_today=guards.forever_left_today(paid, used_today, xano.today()),
             forever_daily_limit=config.PAID_DAILY_REFINES, free_limit=config.FREE_REFINES)
    c.update(_tier_fields(paid, access, feedback_given, c["beta_questions_left"], c["forever_left_today"],
                          c["free_refines_left"], guards.free_feedback_required(paid, used, feedback_given)))
    return c


def _run_guarded(kind, token, user, messages, chat_id=0, prior_ids=(), active_filters=None):
    f_mem = _POOL.submit(_memory_row, user["id"])
    paid, access, rows, feedback_given, counted_free, base = _gate(kind, user, chat_id)
    try:
        row = f_mem.result()
        raw = row.get("notes") or []
        # Preference answers go to the model as one structured line (filters + priority), not as loose notes.
        notes = [n.get("note") for n in raw if n.get("note") and n.get("kind") != "pref"]
        user_context = row.get("user_context") or ""
        pref_hint = prefs.search_hint(prefs.from_notes(raw))
        # Detail: full members get figures; a non-member with a plan gets fit, not numbers ("light"); no plan at
        # all gets no price of any kind ("free", 2026-10-14).
        result, usage = agent.run(token, user, access, messages, memory_notes=notes, user_context=user_context,
                                  detail="full" if paid else "light" if access else "free",
                                  prior_vendor_ids=prior_ids, active_filters=active_filters,
                                  preferences=pref_hint,
                                  on_note=lambda n: xano.add_memory_note(user["id"], n))
    except Exception as e:  # never charge a free refine for our own failure
        xano.log_usage(dict(base, status="error", error=str(e)[:500]))
        raise HTTPException(502, {"status": "error"})
    redactions = None
    if not access:   # free mode: price guard on text, cards and chips (code, not only the prompt)
        result = free_mode.sanitize_result(result)
        redactions = result.pop("price_redactions", 0)
    cost = guards.cost_usd(usage["model"], usage)
    _POOL.submit(xano.log_usage, dict(base, status="ok", counted_as_free=counted_free, model=usage["model"],
                        input_tokens=usage["input_tokens"], cache_read_tokens=usage["cache_read_tokens"],
                        cache_write_tokens=usage["cache_write_tokens"], output_tokens=usage["output_tokens"],
                        tool_calls=usage["tool_calls"], cost_usd=cost, latency_ms=usage["latency_ms"]))
    used = rows + ([{"status": "ok", "kind": "refine", "counted_as_free": True}] if counted_free else [])
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": kind, "status": "ok", "forever": xano.has_forever(user), "beta_member": paid, "has_access": access, "source": "rec_service",
        "cost_usd": cost, "latency_s": round(usage["latency_ms"] / 1000, 1), "tool_calls": usage["tool_calls"],
        "cards": len(result.get("cards") or []), "more_cards": len(result.get("more_cards") or []),
        "notes_saved": len(result.get("notes_saved") or []), "price_locked": not access,
        "price_redactions": redactions,
        "free_refines_left": guards.free_refines_left(paid, used, feedback_given), "model": usage["model"],
        # Per-step timing (2026-10-16 speed pass): where the wait goes.
        "prefetch_ms": usage.get("prefetch_ms"), "model_turns": len(usage.get("turns") or []),
        "model_ms": sum(t.get("ms", 0) for t in usage.get("turns") or []),
        "tools_ms": sum(t.get("tool_ms", 0) for t in usage.get("turns") or []),
        "output_tokens": usage.get("output_tokens"), "early_stop": config.EARLY_STOP})
    return dict(result, paid=paid, has_access=access,
                **_counters(kind, paid, rows, counted_free, feedback_given, access))


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


@app.post("/rec/opening")
def opening(background: BackgroundTasks, authorization: str = Header(None)):
    token, user = _auth(authorization)
    # Create the chat only AFTER the guard allowed and the model answered - a blocked (402/429/503) or
    # failed opening must not leave an empty conversation behind. Costs ~0.3s.
    out = _run_guarded("opening", token, user, [{"role": "user", "content": OPENING_ASK}])
    out["chips"] = list(free_mode.STARTER_CHIPS if out.get("price_locked") else STARTER_CHIPS)
    chat = xano.create_chat(user["id"], _opening_title(user))
    background.add_task(xano.add_message, chat["id"], "assistant", out["text"], _stored_cards(out), out["chips"])
    return _public(out, chat_id=chat["id"])


def _refine_in_chat(token, user, chat, text, background):
    """One typed question inside a stored conversation: server-held history in, both turns saved after."""
    history = xano.get_messages(chat["id"])
    msgs, kw = _model_inputs(history, text)
    out = _run_guarded("refine", token, user, msgs, chat["id"], prior_ids=kw["prior_vendor_ids"],
                       active_filters=kw["active_filters"])
    out["chips"] = ensure_chips(dedupe_chips(out.get("chips"), history))
    if out.get("price_locked"):
        out["chips"] = free_mode.chips(out["chips"], history)
    if (chat.get("title") or "").startswith("Venue picks"):
        chat["title"] = text[:80]

    def persist():   # after the response: user turn, then assistant turn, then bump the chat
        xano.add_message(chat["id"], "user", text)
        xano.add_message(chat["id"], "assistant", out["text"], _stored_cards(out), out["chips"])
        xano.touch_chat(chat)
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
    if body.chat_id:
        chat = xano.get_chat(user["id"], body.chat_id)
        if not chat:
            raise HTTPException(404, "conversation not found")
        text = (body.message or "").strip()[:2000]
        if not text:
            raise HTTPException(400, "message required")
        return _refine_in_chat(token, user, chat, text, background)
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
    chat = _recent_chat(user["id"])
    if chat:
        return _refine_in_chat(token, user, chat, text, background)
    # New conversation: the client's turns are the history this once. Create the chat only AFTER the guard
    # allowed and the model answered, so a blocked or failed question leaves no empty conversation behind.
    out = _run_guarded("refine", token, user, msgs)
    out["chips"] = ensure_chips(out.get("chips"))
    if out.get("price_locked"):
        out["chips"] = free_mode.chips(out["chips"])
    chat = xano.create_chat(user["id"], text[:80])
    earlier = msgs[:-1]

    def persist():   # earlier client-held turns (if any), then this question and its answer
        for t in earlier:
            xano.add_message(chat["id"], t["role"], t["content"])
        xano.add_message(chat["id"], "user", text)
        xano.add_message(chat["id"], "assistant", out["text"], _stored_cards(out), out["chips"])
        xano.touch_chat(chat)
    background.add_task(persist)
    return _public(out, chat_id=chat["id"])


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
        t["more_available"] = left if i == last else 0
    return {"chat_id": chat["id"], "title": chat.get("title") or "", "turns": turns}


class MoreBody(BaseModel):
    chat_id: int


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
        picks, chips = [free_mode.card(c) for c in picks], free_mode.chips(chips)
        text = free_mode.sanitize_text(text)[0]
    return {"text": text, "cards": picks, "chips": chips, "left": len(remaining) - len(picks)}


@app.post("/rec/more")
def show_more(body: MoreBody, background: BackgroundTasks, authorization: str = Header(None)):
    """"Show 3 more" under the latest answer. Spends one question exactly like typing one (user decision
    2026-10-07): same gate, same usage row (kind refine), same counters. No model call - the venues were
    found by that answer's own searches and stored with it."""
    token, user = _auth(authorization)
    chat = xano.get_chat(user["id"], body.chat_id)
    if not chat:
        raise HTTPException(404, "conversation not found")
    history = xano.get_messages(chat["id"])
    if len(_more_remaining(history)) < agent.MORE_STEP:   # checked BEFORE the gate: never charge for nothing
        raise HTTPException(409, {"status": "no_more"})
    paid, access, rows, feedback_given, counted_free, base = _gate("refine", user, chat["id"])
    reply = _more_reply(history, access)
    picks, chips, text = reply["cards"], reply["chips"], reply["text"]
    xano.log_usage(dict(base, status="ok", counted_as_free=counted_free, model="none", input_tokens=0,
                        cache_read_tokens=0, cache_write_tokens=0, output_tokens=0, tool_calls=0,
                        cost_usd=0, latency_ms=0))
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": "more", "status": "ok", "forever": xano.has_forever(user), "beta_member": paid, "has_access": access, "source": "rec_service",
        "cost_usd": 0, "cards": len(picks), "more_cards": reply["left"],
        "free_refines_left": guards.free_refines_left(
            paid, rows + ([{"status": "ok", "counted_as_free": True}] if counted_free else []), feedback_given)})

    def persist():   # same order as a typed question: user turn, assistant turn, bump the chat
        xano.add_message(chat["id"], "user", "Show 3 more")
        xano.add_message(chat["id"], "assistant", text, [dict(c, via_more=True) for c in picks], chips)
        xano.touch_chat(chat)
    background.add_task(persist)
    left = reply["left"]
    return dict(text=text, cards=picks, chips=chips, chat_id=chat["id"], paid=paid, has_access=access,
                more_available=left if left >= agent.MORE_STEP else 0,
                **_counters("refine", paid, rows, counted_free, feedback_given, access))


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
    context: Optional[str] = None   # last step "Anything else Tulle should know?" = the couple's own context
                                    # (same field as /rec/context; None = leave it alone, "" = clear it)


@app.get("/rec/preferences")
def get_preferences(authorization: str = Header(None)):
    """The preference questions + this couple's current answers. asked=False -> show them before the picks."""
    _, user = _auth(authorization)
    row = _memory_row(user["id"])
    raw = row.get("notes") or []
    return {"questions": prefs.QUESTIONS, "answers": prefs.from_notes(raw), "asked": prefs.asked(raw),
            "context": row.get("user_context") or "", "context_max": xano.CONTEXT_MAX}


@app.post("/rec/preferences")
def save_preferences(body: PrefsBody, authorization: str = Header(None)):
    """Save answers into the couple's memory. `added` = the lines to show as 'Added to what Tulle Assistant
    knows about you' (user 2026-10-08: tell them we're adding to their context)."""
    _, user = _auth(authorization)
    answers = prefs.clean(body.answers)
    has_context = bool((body.context or "").strip())
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


class FeedbackBody(BaseModel):
    rating: int = 0          # 1-5, how useful so far
    would_use: str = ""      # yes / maybe / no
    text: str = ""           # what's working, what isn't
    chat_id: int = 0


FEEDBACK_MIN_CHARS = 10


@app.post("/rec/feedback")
def beta_feedback(body: FeedbackBody, authorization: str = Header(None)):
    """Beta check-in. One row per submission. Paid members: the first one unlocks questions for good.
    Free users (2026-10-14): required once after their free question(s); it earns no more (FREE_FEEDBACK_BONUS=0),
    so the next ask is the paywall."""
    _, user = _auth(authorization)
    text = (body.text or "").strip()[:2000]
    would = (body.would_use or "").strip().lower()
    if not 1 <= body.rating <= 5:
        raise HTTPException(400, {"status": "rating_required"})
    if len(text) < FEEDBACK_MIN_CHARS:
        raise HTTPException(400, {"status": "text_too_short", "min_chars": FEEDBACK_MIN_CHARS})
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
    access = xano.has_paid_access(user)
    free_left = guards.free_refines_left(paid, rows, True)
    forever_left = guards.forever_left_today(paid, rows, xano.today())
    return {"ok": True, "beta_questions_left": None, "free_refines_left": free_left,
            "forever_left_today": forever_left,
            **_tier_fields(paid, access, True, None, forever_left, free_left, False)}


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
    return {"paid": paid, "has_access": access, "upsell": "forever",
            "beta_questions_left": beta_left, "beta_feedback_after": config.BETA_FEEDBACK_AFTER,
            "forever_only": config.FOREVER_ONLY, "free_mode": not config.FOREVER_ONLY,
            "forever_left_today": forever_left,
            "forever_daily_limit": config.PAID_DAILY_REFINES, "free_limit": config.FREE_REFINES, "free_refines_left": free_left,
            **_tier_fields(paid, access, fb, beta_left, forever_left, free_left,
                           guards.free_feedback_required(paid, rows, fb)),
            "enabled": not config.KILL_SWITCH, "memory_notes": _memory_notes(user["id"]),
            "prefs_asked": prefs.asked(_memory_row(user["id"]).get("notes") or [])}


@app.get("/health")
def health():
    return {"ok": True, "model": config.MODEL, "enabled": not config.KILL_SWITCH,
            "time": dt.datetime.now(dt.timezone.utc).isoformat()}
