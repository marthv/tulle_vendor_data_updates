"""Tulle recommendation service (Railway). WeWeb calls it with the user's Xano auth token.

POST /rec/opening          -> new conversation with opening picks; returns chat_id
                              (beta: Forever only - everyone else gets 402 forever_only)
POST /rec/refine           -> {chat_id, message}: one refinement in that conversation (3 free, then 402)
                              legacy {messages:[...]} still accepted (no history saved)
GET  /rec/chats            -> the user's conversations, newest first
GET  /rec/chats/{chat_id}  -> one conversation's turns (text, cards, chips) to re-render it
POST /rec/more {chat_id}   -> "Show 3 more": next 3 venues from the latest answer's searches. Spends ONE
                              question exactly like a typed one (user 2026-10-07), same limits and errors.
GET  /rec/status           -> free refinements left, paid flag, whether the service is on
POST /rec/feedback         -> Forever beta check-in {rating, would_use, text}; unlocks questions after 5
GET  /health

Conversations live in Xano rec_chats (42) / rec_messages (43); durable notes the model saves
about the couple live in rec_memory (80) and are fed into every later conversation.
"""
import datetime as dt

from concurrent.futures import ThreadPoolExecutor

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import agent
from chips import STARTER_CHIPS, dedupe_chips
import config
import guards
import notify
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
        text = (text + "\n[Venues shown: " + shown + "]").strip()
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
    """What the page gets: the hidden extras become a count (they cost a question to reveal)."""
    resp = {k: v for k, v in out.items() if k != "more_cards"}
    n = len(out.get("more_cards") or [])
    return dict(resp, more_available=n if n >= agent.MORE_STEP else 0, **extra)


def _stored_cards(out):
    """Picks + "Show 3 more" cards in one cards column; xano.get_messages splits them on read."""
    return list(out.get("cards") or []) + [dict(c, more=True) for c in (out.get("more_cards") or [])]


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


def _memory(user_id):
    """(notes, user_context) for this user; ([], "") on any read problem - never block a request on it."""
    try:
        row = xano.get_memory(user_id) or {}
        return [n.get("note") for n in row.get("notes") or [] if n.get("note")], (row.get("user_context") or "")
    except Exception:
        return [], ""


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
    # `paid` (no cap) = Forever only; `access` (exact prices from ep230) = any active plan.
    paid = xano.has_forever(user)
    access = xano.has_paid_access(user)
    f_rows = _POOL.submit(xano.user_usage, user["id"])
    f_spend = _POOL.submit(xano.spend_today_usd)
    f_fb = _POOL.submit(_feedback_given, user["id"]) if paid else None
    rows = f_rows.result()
    feedback_given = f_fb.result() if f_fb else True
    allowed, status, counted_free = guards.decide(kind, paid, rows, f_spend.result(), xano.today(), feedback_given)
    base = {"user_id": user["id"], "kind": kind, "chat_id": chat_id, "model": config.MODEL}
    if not allowed:
        xano.log_usage(dict(base, status=status))
        _POOL.submit(xano.mp_track, user["id"], "rec_server_request",
                     {"kind": kind, "status": status, "forever": paid, "has_access": access, "source": "rec_service"})
        code = (402 if status in ("blocked_free_limit", "forever_only") else 403 if status == "feedback_required"
                else 503 if status in ("killed", "blocked_global_cap") else 429)
        extra = {}
        if status == "feedback_required":
            extra.update(beta_feedback_after=config.BETA_FEEDBACK_AFTER, questions_used=guards.forever_questions(rows))
        if status == "blocked_monthly_cap":
            t = dt.date.fromisoformat(xano.today())
            extra["resets_on"] = (dt.date(t.year + (t.month == 12), t.month % 12 + 1, 1)).isoformat()
        raise HTTPException(code, {"status": status, "upsell": "forever", "free_limit": config.FREE_REFINES, **extra,
                                   "free_refines_left": guards.free_refines_left(paid, rows)})
    return paid, access, rows, feedback_given, counted_free, base


def _counters(kind, paid, rows, counted_free, feedback_given):
    """Allowance left AFTER this request, for the page's counters."""
    used = rows + ([{"status": "ok", "kind": "refine", "counted_as_free": True}] if counted_free else [])
    used_today = rows + [{"status": "ok", "usage_day": xano.today()}]
    used_fq = rows + ([{"status": "ok", "kind": "refine"}] if paid and kind == "refine" else [])
    return dict(upsell="forever",
                beta_questions_left=guards.beta_questions_left(paid, used_fq, feedback_given),
                beta_feedback_after=config.BETA_FEEDBACK_AFTER,
                free_refines_left=guards.free_refines_left(paid, used),
                forever_left_today=guards.forever_left_today(paid, used_today, xano.today()),
                forever_daily_limit=config.PAID_DAILY_REFINES, free_limit=config.FREE_REFINES)


def _run_guarded(kind, token, user, messages, chat_id=0, prior_ids=()):
    f_mem = _POOL.submit(_memory, user["id"])
    paid, access, rows, feedback_given, counted_free, base = _gate(kind, user, chat_id)
    try:
        notes, user_context = f_mem.result()
        # Detail (user 2026-10-03): only Forever gets figures; free and 1-week/4-week get fit, not numbers.
        result, usage = agent.run(token, user, access, messages, memory_notes=notes, user_context=user_context,
                                  detail="full" if paid else "light", prior_vendor_ids=prior_ids,
                                  on_note=lambda n: xano.add_memory_note(user["id"], n))
    except Exception as e:  # never charge a free refine for our own failure
        xano.log_usage(dict(base, status="error", error=str(e)[:500]))
        raise HTTPException(502, {"status": "error"})
    cost = guards.cost_usd(usage["model"], usage)
    _POOL.submit(xano.log_usage, dict(base, status="ok", counted_as_free=counted_free, model=usage["model"],
                        input_tokens=usage["input_tokens"], cache_read_tokens=usage["cache_read_tokens"],
                        cache_write_tokens=usage["cache_write_tokens"], output_tokens=usage["output_tokens"],
                        tool_calls=usage["tool_calls"], cost_usd=cost, latency_ms=usage["latency_ms"]))
    used = rows + ([{"status": "ok", "kind": "refine", "counted_as_free": True}] if counted_free else [])
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": kind, "status": "ok", "forever": paid, "has_access": access, "source": "rec_service",
        "cost_usd": cost, "latency_s": round(usage["latency_ms"] / 1000, 1), "tool_calls": usage["tool_calls"],
        "cards": len(result.get("cards") or []), "more_cards": len(result.get("more_cards") or []),
        "notes_saved": len(result.get("notes_saved") or []),
        "free_refines_left": guards.free_refines_left(paid, used), "model": usage["model"]})
    return dict(result, paid=paid, has_access=access, **_counters(kind, paid, rows, counted_free, feedback_given))


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
    out["chips"] = list(STARTER_CHIPS)
    chat = xano.create_chat(user["id"], _opening_title(user))
    background.add_task(xano.add_message, chat["id"], "assistant", out["text"], _stored_cards(out), out["chips"])
    return _public(out, chat_id=chat["id"])


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
        history = xano.get_messages(chat["id"])
        # The stored conversation starts with the opening picks (an assistant turn), so restate the
        # opening ask in front of it: the model needs a user turn first.
        msgs = [{"role": "user", "content": OPENING_ASK}]
        msgs += [_turn_for_model(t) for t in history]
        msgs = msgs[-(HISTORY_TURNS - 1):] if len(msgs) >= HISTORY_TURNS else msgs
        if msgs[0]["role"] != "user":
            msgs = msgs[1:]
        msgs.append({"role": "user", "content": text})
        # Venues this chat already showed (our own stored cards, not model output) may be shown again as
        # cards - e.g. "break down <venue from the opening>" (2026-10-05: chat 24's breakdown had no card).
        prior = [c.get("vendor_id") for t in history if t["role"] == "assistant"
                 for c in (t.get("cards") or []) + (t.get("more_cards") or [])]
        out = _run_guarded("refine", token, user, msgs, chat["id"], prior_ids=[v for v in prior if v])
        out["chips"] = dedupe_chips(out.get("chips"), history)
        if (chat.get("title") or "").startswith("Venue picks"):
            chat["title"] = text[:80]

        def persist():   # after the response: user turn, then assistant turn, then bump the chat
            xano.add_message(chat["id"], "user", text)
            xano.add_message(chat["id"], "assistant", out["text"], _stored_cards(out), out["chips"])
            xano.touch_chat(chat)
        background.add_task(persist)
        return _public(out, chat_id=chat["id"])
    # legacy: client-held history, nothing persisted
    msgs = [{"role": t.role, "content": t.content[:2000]} for t in body.messages[-HISTORY_TURNS:]]
    if not msgs or msgs[-1]["role"] != "user" or msgs[0]["role"] != "user":
        raise HTTPException(400, "messages must start and end with a user turn")
    return _public(_run_guarded("refine", token, user, msgs))


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
    left = _more_left(turns)
    last = max((i for i, t in enumerate(turns) if t["role"] == "assistant"), default=None)
    for i, t in enumerate(turns):
        t.pop("more_cards", None)         # unrevealed extras stay on the server until a question is spent
        t["more_available"] = left if i == last else 0
    return {"chat_id": chat["id"], "title": chat.get("title") or "", "turns": turns}


class MoreBody(BaseModel):
    chat_id: int


MORE_TEXT = "Here are 3 more from the same search."


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
    remaining = _more_remaining(history)
    if len(remaining) < agent.MORE_STEP:            # checked BEFORE the gate: never charge for nothing
        raise HTTPException(409, {"status": "no_more"})
    paid, access, rows, feedback_given, counted_free, base = _gate("refine", user, chat["id"])
    picks = remaining[:agent.MORE_STEP]
    xano.log_usage(dict(base, status="ok", counted_as_free=counted_free, model="none", input_tokens=0,
                        cache_read_tokens=0, cache_write_tokens=0, output_tokens=0, tool_calls=0,
                        cost_usd=0, latency_ms=0))
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": "more", "status": "ok", "forever": paid, "has_access": access, "source": "rec_service",
        "cost_usd": 0, "cards": len(picks), "more_cards": len(remaining) - len(picks),
        "free_refines_left": guards.free_refines_left(
            paid, rows + ([{"status": "ok", "counted_as_free": True}] if counted_free else []))})

    def persist():   # same order as a typed question: user turn, assistant turn, bump the chat
        xano.add_message(chat["id"], "user", "Show 3 more")
        xano.add_message(chat["id"], "assistant", MORE_TEXT, [dict(c, via_more=True) for c in picks], [])
        xano.touch_chat(chat)
    background.add_task(persist)
    left = len(remaining) - len(picks)
    return dict(text=MORE_TEXT, cards=picks, chips=[], chat_id=chat["id"], paid=paid, has_access=access,
                more_available=left if left >= agent.MORE_STEP else 0,
                **_counters("refine", paid, rows, counted_free, feedback_given))


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
    """Forever beta check-in. One row per submission; the first one unlocks questions for good."""
    _, user = _auth(authorization)
    text = (body.text or "").strip()[:2000]
    would = (body.would_use or "").strip().lower()
    if not 1 <= body.rating <= 5:
        raise HTTPException(400, {"status": "rating_required"})
    if len(text) < FEEDBACK_MIN_CHARS:
        raise HTTPException(400, {"status": "text_too_short", "min_chars": FEEDBACK_MIN_CHARS})
    if would not in ("yes", "maybe", "no"):
        would = ""
    paid = xano.has_forever(user)
    rows = xano.user_usage(user["id"])
    used = guards.forever_questions(rows)
    xano.add_beta_feedback({"user_id": int(user["id"]), "rating": body.rating, "would_use": would, "text": text,
                            "questions_used": used, "chat_id": int(body.chat_id or 0)})
    _POOL.submit(xano.mp_track, user["id"], "rec_beta_feedback", {
        "rating": body.rating, "would_use": would, "chars": len(text), "forever": paid,
        "questions_used": used, "source": "rec_service"})
    _POOL.submit(notify.beta_feedback, user, body.rating, would, text, used)
    return {"ok": True, "feedback_required": False, "beta_questions_left": None,
            "forever_left_today": guards.forever_left_today(paid, rows, xano.today())}


@app.get("/rec/status")
def status(authorization: str = Header(None)):
    _, user = _auth(authorization)
    paid = xano.has_forever(user)
    rows = xano.user_usage(user["id"])
    beta_left = guards.beta_questions_left(paid, rows, _feedback_given(user["id"]) if paid else True)
    return {"paid": paid, "has_access": xano.has_paid_access(user), "upsell": "forever",
            "beta_questions_left": beta_left, "beta_feedback_after": config.BETA_FEEDBACK_AFTER,
            "feedback_required": beta_left == 0, "forever_only": config.FOREVER_ONLY,
            "forever_left_today": guards.forever_left_today(paid, rows, xano.today()),
            "forever_daily_limit": config.PAID_DAILY_REFINES, "free_limit": config.FREE_REFINES, "free_refines_left": guards.free_refines_left(paid, rows),
            "enabled": not config.KILL_SWITCH, "memory_notes": _memory_notes(user["id"])}


@app.get("/health")
def health():
    return {"ok": True, "model": config.MODEL, "enabled": not config.KILL_SWITCH,
            "time": dt.datetime.now(dt.timezone.utc).isoformat()}
