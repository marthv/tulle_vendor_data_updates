"""Tulle recommendation service (Railway). WeWeb calls it with the user's Xano auth token.

POST /rec/opening          -> new conversation with free opening picks; returns chat_id
POST /rec/refine           -> {chat_id, message}: one refinement in that conversation (3 free, then 402)
                              legacy {messages:[...]} still accepted (no history saved)
GET  /rec/chats            -> the user's conversations, newest first
GET  /rec/chats/{chat_id}  -> one conversation's turns (text, cards, chips) to re-render it
GET  /rec/status           -> free refinements left, paid flag, whether the service is on
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


def _memory_notes(user_id):
    try:
        row = xano.get_memory(user_id)
        return [n.get("note") for n in (row or {}).get("notes") or [] if n.get("note")]
    except Exception:
        return []


def _run_guarded(kind, token, user, messages, chat_id=0):
    # `paid` (no cap) = Forever only; `access` (exact prices from ep230) = any active plan.
    paid = xano.has_forever(user)
    access = xano.has_paid_access(user)
    f_rows = _POOL.submit(xano.user_usage, user["id"])
    f_spend = _POOL.submit(xano.spend_today_usd)
    f_notes = _POOL.submit(_memory_notes, user["id"])
    rows = f_rows.result()
    allowed, status, counted_free = guards.decide(kind, paid, rows, f_spend.result(), xano.today())
    base = {"user_id": user["id"], "kind": kind, "chat_id": chat_id, "model": config.MODEL}
    if not allowed:
        xano.log_usage(dict(base, status=status))
        code = 402 if status == "blocked_free_limit" else (503 if status in ("killed", "blocked_global_cap") else 429)
        raise HTTPException(code, {"status": status, "upsell": "forever",
                                   "free_refines_left": guards.free_refines_left(paid, rows)})
    try:
        result, usage = agent.run(token, user, access, messages, memory_notes=f_notes.result(),
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
    return dict(result, paid=paid, has_access=access, upsell="forever",
                free_refines_left=guards.free_refines_left(paid, used))


@app.post("/rec/opening")
def opening(background: BackgroundTasks, authorization: str = Header(None)):
    token, user = _auth(authorization)
    # Create the chat only AFTER the guard allowed and the model answered - a blocked (402/429/503) or
    # failed opening must not leave an empty conversation behind. Costs ~0.3s.
    out = _run_guarded("opening", token, user, [{"role": "user", "content": OPENING_ASK}])
    chat = xano.create_chat(user["id"], "Venue picks for you")
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
        if (chat.get("title") or "") == "Venue picks for you":
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


@app.get("/rec/status")
def status(authorization: str = Header(None)):
    _, user = _auth(authorization)
    paid = xano.has_forever(user)
    return {"paid": paid, "has_access": xano.has_paid_access(user), "upsell": "forever", "free_refines_left": guards.free_refines_left(paid, xano.user_usage(user["id"])),
            "enabled": not config.KILL_SWITCH, "memory_notes": _memory_notes(user["id"])}


@app.get("/health")
def health():
    return {"ok": True, "model": config.MODEL, "enabled": not config.KILL_SWITCH,
            "time": dt.datetime.now(dt.timezone.utc).isoformat()}
