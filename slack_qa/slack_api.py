"""Slack: request-signature check and a small read/post Web API client (requests, no SDK).

Read calls fail soft (return [] / None) so a missing scope or a rate limit degrades the answer's context
instead of killing it. Writes: chat.postMessage (text or Block Kit), chat.update (only the bot's own
approval cards), chat.postEphemeral ("only approvers can apply this") and the CSV upload."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone

import requests

import config

log = logging.getLogger("slack_qa.slack")
API = "https://slack.com/api/"
MAX_SKEW = 60 * 5


def verify_signature(secret: str, timestamp: str | None, signature: str | None, body: bytes,
                     now: float | None = None) -> bool:
    """Slack signing secret v0 scheme; rejects timestamps more than 5 minutes off (replay)."""
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW:
        return False
    try:
        base = b"v0:" + timestamp.encode() + b":" + body
    except (AttributeError, UnicodeEncodeError):
        return False
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, signature)
    except TypeError:          # non-ASCII signature header: compare_digest refuses str with non-ASCII -> reject
        return False


class TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._d: dict = {}
        self._lock = threading.Lock()

    def get(self, key, fn):
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        val = fn()
        with self._lock:
            self._d[key] = (now, val)
        return val


def ts_label(ts: str | float | None) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return "?"


class Slack:
    def __init__(self, token: str | None = None, session: requests.Session | None = None):
        self.token = token if token is not None else config.bot_token()
        self.http = session or requests.Session()
        self._hist = TTLCache(180)
        self._chans = TTLCache(600)
        self._users = TTLCache(24 * 3600)

    def _call(self, method: str, http: str = "GET", **params) -> dict:
        if not self.token:
            return {"ok": False, "error": "no_token"}
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            if http == "POST":
                r = self.http.post(API + method, json=params, headers=headers, timeout=10)
            else:
                r = self.http.get(API + method, params=params, headers=headers, timeout=10)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("slack %s failed: %s", method, type(e).__name__)
            return {"ok": False, "error": "request_failed"}
        if not data.get("ok"):
            log.warning("slack %s -> %s", method, data.get("error"))
        return data

    # --- write ------------------------------------------------------------------------------------
    def post_message(self, channel: str, text: str, thread_ts: str | None = None, sanitize: bool = False) -> dict:
        if sanitize:                       # v2 features (roll-up, dedupe header): emails never leave the bot
            from sanitize import scrub
            text = scrub(text)
        params = {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False}
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self._call("chat.postMessage", http="POST", **params)

    def post_blocks(self, channel: str, text: str, blocks: list[dict], thread_ts: str | None = None) -> dict:
        from sanitize import scrub
        text, blocks = scrub(text), scrub(blocks)           # the Slack-send boundary for cards and Details
        params = {"channel": channel, "text": text, "blocks": blocks, "unfurl_links": False, "unfurl_media": False}
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self._call("chat.postMessage", http="POST", **params)

    def update_message(self, channel: str, ts: str, text: str, blocks: list[dict]) -> dict:
        from sanitize import scrub
        text, blocks = scrub(text), scrub(blocks)
        return self._call("chat.update", http="POST", channel=channel, ts=ts, text=text, blocks=blocks)

    def post_ephemeral(self, channel: str, user: str, text: str, thread_ts: str | None = None) -> dict:
        from sanitize import scrub
        params = {"channel": channel, "user": user, "text": scrub(text)}
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self._call("chat.postEphemeral", http="POST", **params)

    def upload_file(self, channel: str, thread_ts: str | None, filename: str, content: bytes,
                    title: str = "", comment: str = "") -> dict:
        """Slack's external upload flow (files:write): getUploadURLExternal -> POST bytes -> completeUploadExternal."""
        if not self.token:
            return {"ok": False, "error": "no_token"}
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            r = self.http.post(API + "files.getUploadURLExternal", headers=headers, timeout=20,
                               data={"filename": filename, "length": str(len(content))})
            step1 = r.json()
            if not step1.get("ok"):
                log.warning("files.getUploadURLExternal -> %s", step1.get("error"))
                return step1
            up = self.http.post(step1["upload_url"], files={"file": (filename, content, "text/csv")}, timeout=60)
            if up.status_code != 200:
                return {"ok": False, "error": f"upload_http_{up.status_code}"}
            files = [{"id": step1["file_id"], "title": title or filename}]
            data = {"files": json.dumps(files), "channel_id": channel}
            if thread_ts:
                data["thread_ts"] = thread_ts
            if comment:
                data["initial_comment"] = comment
            r3 = self.http.post(API + "files.completeUploadExternal", headers=headers, timeout=20, data=data)
            out = r3.json()
            if not out.get("ok"):
                log.warning("files.completeUploadExternal -> %s", out.get("error"))
            return out
        except (requests.RequestException, ValueError, KeyError) as e:
            log.warning("file upload failed: %s", type(e).__name__)
            return {"ok": False, "error": "upload_failed"}

    # --- read -------------------------------------------------------------------------------------
    def history(self, channel: str, limit: int = 50, days: int = 7) -> list[dict]:
        def fetch():
            oldest = f"{time.time() - days * 86400:.6f}"
            data = self._call("conversations.history", channel=channel, limit=limit, oldest=oldest)
            return list(reversed(data.get("messages", []))) if data.get("ok") else []
        return self._hist.get(("h", channel, limit, days), fetch)

    def history_all(self, channel: str, days: int = 28, max_pages: int = 10) -> list[dict]:
        """Every message of the last `days` days (oldest first), following cursors, at most max_pages x 200.
        Uncached: the weekly digest calls it once."""
        oldest = f"{time.time() - days * 86400:.6f}"
        out, cursor = [], None
        for _ in range(max(1, max_pages)):
            params = {"channel": channel, "limit": 200, "oldest": oldest}
            if cursor:
                params["cursor"] = cursor
            data = self._call("conversations.history", **params)
            if not data.get("ok"):
                break
            out += data.get("messages", [])
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return sorted(out, key=lambda m: float(m.get("ts") or 0))

    def replies(self, channel: str, thread_ts: str, limit: int = 50) -> list[dict]:
        data = self._call("conversations.replies", channel=channel, ts=thread_ts, limit=limit)
        return data.get("messages", []) if data.get("ok") else []

    def member_channels(self) -> list[dict]:
        """Channels the bot is in (users.conversations; channels:read + groups:read)."""
        def fetch():
            data = self._call("users.conversations", types="public_channel,private_channel",
                              exclude_archived="true", limit=200)
            return [{"id": c["id"], "name": c.get("name", c["id"])}
                    for c in data.get("channels", [])] if data.get("ok") else []
        return self._chans.get("member", fetch)

    def user_name(self, uid: str | None) -> str:
        if not uid:
            return "unknown"
        known = config.user_names()
        if uid in known:
            return known[uid]

        def fetch():
            data = self._call("users.info", user=uid)
            if not data.get("ok"):
                return uid
            u = data.get("user", {})
            prof = u.get("profile", {})
            # First name only: enough to follow a conversation, less personal data in prompts.
            name = prof.get("display_name") or prof.get("real_name") or u.get("name") or uid
            return name.split()[0] if name else uid
        return self._users.get(uid, fetch)

    def bot_user_id(self) -> str | None:
        def fetch():
            data = self._call("auth.test")
            return data.get("user_id") if data.get("ok") else None
        return self._users.get("__self__", fetch)

    def render(self, messages: list[dict], max_chars: int = 600) -> list[str]:
        out = []
        for m in messages:
            if m.get("subtype") in ("channel_join", "channel_leave", "channel_topic", "channel_purpose"):
                continue
            who = (m.get("bot_profile") or {}).get("name") or m.get("username") if m.get("bot_id") \
                else self.user_name(m.get("user"))
            text = (m.get("text") or "").strip()
            if not text:
                for att in m.get("attachments") or []:
                    text = att.get("text") or att.get("fallback") or ""
                    if text:
                        break
            if not text:
                continue
            text = text if len(text) <= max_chars else text[:max_chars] + " ..."
            out.append(f"[{ts_label(m.get('ts'))}] {who or 'bot'}: {text}")
        return out
