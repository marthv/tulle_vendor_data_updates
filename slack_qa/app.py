"""Tulle Bot Slack Q&A service (FastAPI).

POST /slack/events        Slack Events API: signature check -> url_verification / ack in <3 s -> work in background.
POST /slack/interactions  Slack interactivity (Approve / Reject / Undo on proposal cards): same signature check,
                          ack in <3 s, then interactions.Interactions runs in the background.
GET  /health        liveness (no secrets, no Slack/Claude calls).

Run: cd slack_qa && uvicorn app:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, Response

import config
from slack_api import verify_signature
from state import Dedupe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("slack_qa")

dedupe = Dedupe()
_bot = None
_interactions = None
_scheduler = None


@asynccontextmanager
async def lifespan(_app):
    global _scheduler
    if os.environ.get("QA_WARM_ON_START", "1").strip().lower() not in ("0", "false", "no", "off"):
        # owner 2026-11-22: after a restart the first request waited ~3 min while the 20k-vendor list loaded.
        # Load it in the background so nobody waits. The bot is built HERE, on the main thread, before the warm
        # thread or the scheduler can race to build a second one (QA 11-22 blocker: two Bots = two proposal stores).
        bot = get_bot()
        threading.Thread(target=bot.warm, name="warm", daemon=True).start()
    if config.scheduler_enabled():
        from scheduler import Scheduler
        _scheduler = Scheduler(get_bot())
        _scheduler.start()
        log.info("scheduler started")
    yield
    if _scheduler is not None:
        _scheduler.stop()


app = FastAPI(title="Tulle Bot Q&A", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


_bot_lock = threading.Lock()


def get_bot():
    """One Bot per process (it owns the proposal store). Double-checked lock: request threads, the scheduler and the
    warm thread may all ask at once."""
    global _bot
    if _bot is None:
        with _bot_lock:
            if _bot is None:
                from bot import Bot
                _bot = Bot()
    return _bot


def set_bot(bot) -> None:          # tests
    global _bot, _interactions
    _bot = bot
    _interactions = None


def get_interactions():
    """Shares the bot's Slack client and proposal store (one in-memory store per process)."""
    global _interactions
    if _interactions is None:
        from interactions import Interactions
        b = get_bot()
        _interactions = Interactions(slack=b.slack, store=b.store)
    return _interactions


@app.get("/health")
def health():
    return {"ok": True, "enabled": config.enabled()}


@app.get("/style-rules")
def style_rules(request: Request, target: str = "deploy_notes"):
    """Standing writing rules learned from Slack feedback, for the #deployment notifier (separate Railway service).
    Bearer QA_RULES_TOKEN; 404 when the token isn't configured, so the route doesn't exist by default."""
    import hmac

    from state import State
    token = config.rules_token()
    if not token:
        return JSONResponse({"ok": False}, status_code=404)
    got = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(got, token):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    if target not in ("deploy_notes", "bot_answers"):
        return JSONResponse({"ok": False, "error": "bad_target"}, status_code=400)
    return {"ok": True, "rules": [{"id": r["id"], "text": r["text"]} for r in State().rules(target)]}


@app.post("/slack/events")
async def slack_events(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not verify_signature(config.signing_secret(), request.headers.get("X-Slack-Request-Timestamp"),
                            request.headers.get("X-Slack-Signature"), body):
        return JSONResponse({"ok": False, "error": "bad_signature"}, status_code=401)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}
    if payload.get("type") != "event_callback":
        return {"ok": True}

    retry = request.headers.get("X-Slack-Retry-Num")
    if dedupe.seen(payload.get("event_id")):
        log.info("duplicate event %s (retry %s) skipped", payload.get("event_id"), retry)
        return {"ok": True, "duplicate": True}
    if not config.enabled():
        return {"ok": True, "disabled": True}
    background.add_task(get_bot().handle_safe, payload)
    return {"ok": True}


@app.post("/slack/interactions")
async def slack_interactions(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not verify_signature(config.signing_secret(), request.headers.get("X-Slack-Request-Timestamp"),
                            request.headers.get("X-Slack-Signature"), body):
        return JSONResponse({"ok": False, "error": "bad_signature"}, status_code=401)
    from interactions import parse_body
    payload = parse_body(body)
    if payload is None:
        return JSONResponse({"ok": False, "error": "bad_payload"}, status_code=400)
    if not config.enabled():
        return Response(status_code=200)
    background.add_task(get_interactions().handle_safe, payload)
    return Response(status_code=200)          # empty 200 = ack; the card is updated via chat.update
