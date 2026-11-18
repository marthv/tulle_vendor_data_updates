"""Durable bot state (mute per channel, spend per UTC day) in one small JSON file on the volume, plus
in-memory event dedupe and per-channel rate limiting (those only need to survive Slack's retry window
and an hour, so a restart losing them is harmless)."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import config


def utc_day(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).strftime("%Y-%m-%d")


class State:
    def __init__(self, path: Path | None = None):
        self.path = path or (config.state_dir() / "slack_qa_state.json")
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"channels": {}, "spend": {}}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- mute -------------------------------------------------------------------------------------
    def is_muted(self, channel: str) -> bool:
        with self._lock:
            ch = self._data.setdefault("channels", {}).get(channel)
        if ch is None or "muted" not in ch:
            return config.default_muted()
        return bool(ch["muted"])

    def set_muted(self, channel: str, muted: bool, by: str = "") -> None:
        with self._lock:
            self._data.setdefault("channels", {})[channel] = {
                "muted": muted, "by": by, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self._save()

    def explicit(self, channel: str) -> bool:
        with self._lock:
            return "muted" in self._data.get("channels", {}).get(channel, {})

    # --- spend ------------------------------------------------------------------------------------
    def spent_today(self) -> float:
        with self._lock:
            return float(self._data.get("spend", {}).get(utc_day(), 0.0))

    def add_spend(self, usd: float) -> float:
        with self._lock:
            spend = self._data.setdefault("spend", {})
            day = utc_day()
            spend[day] = round(float(spend.get(day, 0.0)) + float(usd), 6)
            for old in sorted(spend)[:-30]:   # keep 30 days
                spend.pop(old, None)
            self._save()
            return spend[day]

    def reports_today(self) -> int:
        with self._lock:
            return int(self._data.get("reports", {}).get(utc_day(), 0))

    def add_report(self) -> int:
        with self._lock:
            rep = self._data.setdefault("reports", {})
            day = utc_day()
            rep[day] = int(rep.get(day, 0)) + 1
            for old in sorted(rep)[:-30]:
                rep.pop(old, None)
            self._save()
            return rep[day]

    def over_budget(self) -> bool:
        return self.spent_today() >= config.daily_usd_cap()

    # --- team style rules (learned from Slack feedback) --------------------------------------------
    # One global list (small team). target: "deploy_notes" (#deployment staging notes), "bot_answers"
    # (Tulle Bot replies), or "both". Style only - they never override the bot's mandatory rules.
    MAX_RULES = 20

    def rules(self, target: str | None = None) -> list[dict]:
        with self._lock:
            rs = list(self._data.get("rules", []))
        if target:
            rs = [r for r in rs if r.get("target") in (target, "both")]
        return rs

    def add_rule(self, text: str, target: str, by: str = "", source: str = "") -> tuple[dict, dict | None]:
        """Returns (new rule, rule dropped to stay under MAX_RULES or None)."""
        with self._lock:
            rs = self._data.setdefault("rules", [])
            nid = max([int(r.get("id", 0)) for r in rs] + [0]) + 1
            rule = {"id": nid, "text": text.strip()[:300], "target": target, "by": by, "source": source,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            rs.append(rule)
            dropped = rs.pop(0) if len(rs) > self.MAX_RULES else None
            self._save()
            return rule, dropped

    def remove_rule(self, rule_id: int) -> dict | None:
        with self._lock:
            rs = self._data.setdefault("rules", [])
            for i, r in enumerate(rs):
                if int(r.get("id", -1)) == rule_id:
                    gone = rs.pop(i)
                    self._save()
                    return gone
        return None


class Dedupe:
    """Slack retries an event (X-Slack-Retry-Num) when the 3 s ack is slow; the event_id stays the same."""

    def __init__(self, ttl: float = 3600):
        self.ttl = ttl
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def seen(self, key: str | None) -> bool:
        """True if key was already seen (caller should skip); records it otherwise."""
        if not key:
            return False
        now = time.time()
        with self._lock:
            for k, t in list(self._seen.items()):
                if now - t > self.ttl:
                    del self._seen[k]
            if key in self._seen:
                return True
            self._seen[key] = now
            return False


class RateLimiter:
    def __init__(self, window: float = 3600):
        self.window = window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, channel: str) -> bool:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(channel, []) if now - t < self.window]
            if len(hits) >= config.channel_hourly_cap():
                self._hits[channel] = hits
                return False
            hits.append(now)
            self._hits[channel] = hits
            return True
