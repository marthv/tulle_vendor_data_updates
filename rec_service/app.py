"""Tulle recommendation service (Railway). WeWeb calls it with the user's Xano auth token.

POST /rec/opening   -> free opening picks for the user's profile (counts toward DAILY_OPENINGS only)
POST /rec/refine    -> one chat refinement (3 free, then 402 -> show the plan paywall)
GET  /rec/status    -> free refinements left, paid flag, whether the service is on
GET  /health
"""
import datetime as dt

from fastapi import FastAPI, Header, HTTPException
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


class Turn(BaseModel):
    role: str
    content: str


class RefineBody(BaseModel):
    messages: list[Turn]          # prior turns + the new user message last
    chat_id: int = 0


def _auth(authorization):
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = xano.verify_user(token)
    if not user or not user.get("id"):
        raise HTTPException(401, "sign in required")
    return token, user


def _handle(kind, authorization, messages, chat_id=0):
    token, user = _auth(authorization)
    paid = xano.has_paid_access(user)
    rows = xano.user_usage(user["id"])
    allowed, status, counted_free = guards.decide(kind, paid, rows, xano.spend_today_usd(), xano.today())
    base = {"user_id": user["id"], "kind": kind, "chat_id": chat_id, "model": config.MODEL}
    if not allowed:
        xano.log_usage(dict(base, status=status))
        code = 402 if status == "blocked_free_limit" else (503 if status in ("killed", "blocked_global_cap") else 429)
        raise HTTPException(code, {"status": status, "free_refines_left": guards.free_refines_left(paid, rows)})
    try:
        result, usage = agent.run(token, user, paid, messages)
    except Exception as e:  # never charge a free refine for our own failure
        xano.log_usage(dict(base, status="error", error=str(e)[:500]))
        raise HTTPException(502, {"status": "error"})
    cost = guards.cost_usd(usage["model"], usage)
    xano.log_usage(dict(base, status="ok", counted_as_free=counted_free, model=usage["model"],
                        input_tokens=usage["input_tokens"], cache_read_tokens=usage["cache_read_tokens"],
                        cache_write_tokens=usage["cache_write_tokens"], output_tokens=usage["output_tokens"],
                        tool_calls=usage["tool_calls"], cost_usd=cost, latency_ms=usage["latency_ms"]))
    left = guards.free_refines_left(paid, rows + ([{"status": "ok", "kind": "refine", "counted_as_free": True}] if counted_free else []))
    return dict(result, paid=paid, free_refines_left=left)


@app.post("/rec/opening")
def opening(authorization: str = Header(None)):
    ask = ("Recommend 3 venues for us based on our profile, and tell us the single most useful next step "
           "for where we are in planning.")
    return _handle("opening", authorization, [{"role": "user", "content": ask}])


@app.post("/rec/refine")
def refine(body: RefineBody, authorization: str = Header(None)):
    msgs = [{"role": t.role, "content": t.content[:2000]} for t in body.messages[-8:]]
    if not msgs or msgs[-1]["role"] != "user" or msgs[0]["role"] != "user":
        raise HTTPException(400, "messages must start and end with a user turn")
    return _handle("refine", authorization, msgs, body.chat_id)


@app.get("/rec/status")
def status(authorization: str = Header(None)):
    token, user = _auth(authorization)
    paid = xano.has_paid_access(user)
    return {"paid": paid, "free_refines_left": guards.free_refines_left(paid, xano.user_usage(user["id"])),
            "enabled": not config.KILL_SWITCH}


@app.get("/health")
def health():
    return {"ok": True, "model": config.MODEL, "enabled": not config.KILL_SWITCH,
            "time": dt.datetime.now(dt.timezone.utc).isoformat()}
