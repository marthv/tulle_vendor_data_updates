"""Tulle recommendation service (Railway). WeWeb calls it with the user's Xano auth token.

POST /rec/opening          -> new conversation with opening picks; returns chat_id
                              (beta: Forever only - everyone else gets 402 forever_only)
POST /rec/refine           -> {chat_id, message}: one refinement in that conversation (3 free, then 402)
                              legacy {messages:[...]} still accepted (no history saved)
GET  /rec/chats            -> the user's conversations, newest first
GET  /rec/chats/{chat_id}  -> one conversation's turns (text, cards, chips) to re-render it
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
import config
import guards
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
    return {"role": t["role"], "content": text or "(recommendations shown)"}


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


def _run_guarded(kind, token, user, messages, chat_id=0):
    # `paid` (no cap) = Forever only; `access` (exact prices from ep230) = any active plan.
    paid = xano.has_forever(user)
    access = xano.has_paid_access(user)
    f_rows = _POOL.submit(xano.user_usage, user["id"])
    f_spend = _POOL.submit(xano.spend_today_usd)
    f_mem = _POOL.submit(_memory, user["id"])
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
    try:
        notes, user_context = f_mem.result()
        result, usage = agent.run(token, user, access, messages, memory_notes=notes, user_context=user_context,
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
    used_today = rows + [{"status": "ok", "usage_day": xano.today()}]
    _POOL.submit(xano.mp_track, user["id"], "rec_server_request", {
        "kind": kind, "status": "ok", "forever": paid, "has_access": access, "source": "rec_service",
        "cost_usd": cost, "latency_s": round(usage["latency_ms"] / 1000, 1), "tool_calls": usage["tool_calls"],
        "cards": len(result.get("cards") or []), "notes_saved": len(result.get("notes_saved") or []),
        "free_refines_left": guards.free_refines_left(paid, used), "model": usage["model"]})
    used_fq = rows + ([{"status": "ok", "kind": "refine"}] if paid and kind == "refine" else [])
    return dict(result, paid=paid, has_access=access, upsell="forever",
                beta_questions_left=guards.beta_questions_left(paid, used_fq, feedback_given),
                beta_feedback_after=config.BETA_FEEDBACK_AFTER,
                free_refines_left=guards.free_refines_left(paid, used),
                forever_left_today=guards.forever_left_today(paid, used_today, xano.today()),
                forever_daily_limit=config.PAID_DAILY_REFINES, free_limit=config.FREE_REFINES)


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
    chat = xano.create_chat(user["id"], _opening_title(user))
    background.add_task(xano.add_message, chat["id"], "assistant", out["text"], out["cards"], out["chips"])
    return dict(out, chat_id=chat["id"])


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
        out = _run_guarded("refine", token, user, msgs, chat["id"])
        if (chat.get("title") or "").startswith("Venue picks"):
            chat["title"] = text[:80]

        def persist():   # after the response: user turn, then assistant turn, then bump the chat
            xano.add_message(chat["id"], "user", text)
            xano.add_message(chat["id"], "assistant", out["text"], out["cards"], out["chips"])
            xano.touch_chat(chat)
        background.add_task(persist)
        return dict(out, chat_id=chat["id"])
    # legacy: client-held history, nothing persisted
    msgs = [{"role": t.role, "content": t.content[:2000]} for t in body.messages[-HISTORY_TURNS:]]
    if not msgs or msgs[-1]["role"] != "user" or msgs[0]["role"] != "user":
        raise HTTPException(400, "messages must start and end with a user turn")
    return _run_guarded("refine", token, user, msgs)


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
    return {"chat_id": chat["id"], "title": chat.get("title") or "", "turns": xano.get_messages(chat["id"])}


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
