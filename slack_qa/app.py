"""Tulle Bot Slack Q&A service (FastAPI).

POST /slack/events  Slack Events API: signature check -> url_verification / ack in <3 s -> work in background.
GET  /health        liveness (no secrets, no Slack/Claude calls).

Run: cd slack_qa && uvicorn app:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import json
import logging

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

import config
from slack_api import verify_signature
from state import Dedupe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("slack_qa")

app = FastAPI(title="Tulle Bot Q&A", docs_url=None, redoc_url=None, openapi_url=None)
dedupe = Dedupe()
_bot = None


def get_bot():
    global _bot
    if _bot is None:
        from bot import Bot
        _bot = Bot()
    return _bot


def set_bot(bot) -> None:          # tests
    global _bot
    _bot = bot


@app.get("/health")
def health():
    return {"ok": True, "enabled": config.enabled()}


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
