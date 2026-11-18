import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

import app as app_module
from slack_api import verify_signature

SECRET = "test-signing-secret"


def sign(body: bytes, ts: int | None = None, secret: str = SECRET) -> dict:
    ts = ts if ts is not None else int(time.time())
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": sig, "Content-Type": "application/json"}


class RecordingBot:
    def __init__(self):
        self.payloads = []

    def handle_safe(self, payload):
        self.payloads.append(payload)
        return "ok"


@pytest.fixture
def client():
    app_module.dedupe._seen.clear()
    bot = RecordingBot()
    app_module.set_bot(bot)
    c = TestClient(app_module.app)
    c.bot = bot
    yield c
    app_module.set_bot(None)


def test_verify_signature_unit():
    body = b'{"a":1}'
    h = sign(body)
    assert verify_signature(SECRET, h["X-Slack-Request-Timestamp"], h["X-Slack-Signature"], body)
    assert not verify_signature(SECRET, h["X-Slack-Request-Timestamp"], h["X-Slack-Signature"], body + b" ")
    assert not verify_signature("other", h["X-Slack-Request-Timestamp"], h["X-Slack-Signature"], body)
    assert not verify_signature("", h["X-Slack-Request-Timestamp"], h["X-Slack-Signature"], body)
    assert not verify_signature(SECRET, "notanumber", h["X-Slack-Signature"], body)
    assert not verify_signature(SECRET, None, None, body)


def test_url_verification(client):
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    r = client.post("/slack/events", content=body, headers=sign(body))
    assert r.status_code == 200 and r.json() == {"challenge": "abc123"}


def test_bad_signature_rejected(client):
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    r = client.post("/slack/events", content=body, headers=sign(body, secret="wrong"))
    assert r.status_code == 401


def test_old_timestamp_rejected(client):
    body = json.dumps({"type": "event_callback", "event_id": "Ev1", "event": {}}).encode()
    r = client.post("/slack/events", content=body, headers=sign(body, ts=int(time.time()) - 600))
    assert r.status_code == 401
    assert client.bot.payloads == []


def test_missing_headers_rejected(client):
    r = client.post("/slack/events", content=b"{}")
    assert r.status_code == 401


def test_event_acked_and_dispatched(client):
    body = json.dumps({"type": "event_callback", "event_id": "Ev42", "event": {"type": "message"}}).encode()
    r = client.post("/slack/events", content=body, headers=sign(body))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert [p["event_id"] for p in client.bot.payloads] == ["Ev42"]


def test_retry_deduped(client):
    body = json.dumps({"type": "event_callback", "event_id": "Ev99", "event": {"type": "message"}}).encode()
    client.post("/slack/events", content=body, headers=sign(body))
    h = sign(body)
    h["X-Slack-Retry-Num"] = "1"
    h["X-Slack-Retry-Reason"] = "http_timeout"
    r = client.post("/slack/events", content=body, headers=h)
    assert r.status_code == 200 and r.json().get("duplicate") is True
    assert len(client.bot.payloads) == 1


def test_retry_of_unseen_event_is_processed(client):
    body = json.dumps({"type": "event_callback", "event_id": "Ev100", "event": {"type": "message"}}).encode()
    h = sign(body)
    h["X-Slack-Retry-Num"] = "2"
    client.post("/slack/events", content=body, headers=h)
    assert len(client.bot.payloads) == 1


def test_kill_switch(client, monkeypatch):
    monkeypatch.setenv("QA_ENABLED", "0")
    body = json.dumps({"type": "event_callback", "event_id": "Ev7", "event": {"type": "message"}}).encode()
    r = client.post("/slack/events", content=body, headers=sign(body))
    assert r.status_code == 200 and client.bot.payloads == []


def test_health(client):
    assert client.get("/health").json()["ok"] is True
