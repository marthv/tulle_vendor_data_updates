"""Proposal store + the approve / reject / undo / re-check state machine.

One JSON file on the volume (proposals.json) plus an append-only decision log (decisions.jsonl). A proposal is
created BEFORE its card is posted, so the button's id always resolves. Transitions are made under one lock with a
compare-and-set on `status`, which is what makes a double click (or Slack retrying the interaction) safe:

    pending --approve--> applying --> applied | dry_run | failed | unknown
    pending --reject---> rejected
    unknown --re-check-> applied | pending (confirmed nothing happened) | unknown
    applied --undo-----> undoing  --> undone | applied (undo refused) | unknown
    pending --card post failed--> post_failed   (not shown to anyone: may be proposed again, not counted in caps)

`dry_run` is fixed at creation: a card posted while dry run was on can only ever dry-run, even if the flag is turned
off later (the approver approved a card that said "nothing is written").
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

ACTIVE = {"pending", "applying", "applied", "undoing", "unknown"}       # on screen / in flight / done
NEVER_AGAIN = ACTIVE | {"rejected"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_dry_run(kind: str) -> bool:
    return {"merge": config.merge_dry_run, "vendor_edit": config.vendor_edit_dry_run,
            "task": config.task_dry_run}.get(kind, lambda: True)()


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or (config.state_dir() / "proposals.json")
        self.log_path = self.path.with_name("decisions.jsonl")
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"proposals": {}, "meta": {}}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _log(self, rec: dict) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **rec}) + "\n")

    # --- create / read ------------------------------------------------------------------------------
    def create(self, kind: str, title: str, what: str, why: str, next_steps: str, payload: dict,
               touches: str = "", key: str = "", source: str = "", dry_run: bool | None = None) -> dict | None:
        """New proposal, or None if an active/rejected proposal with the same key exists (checked under the lock,
        so two scans racing can't both create it)."""
        pid = f"{kind[:2]}{int(time.time())}{secrets.token_hex(3)}"
        p = {"id": pid, "kind": kind, "title": title, "what": what, "why": why, "touches": touches,
             "next_steps": next_steps, "payload": payload, "key": key, "source": source,
             "dry_run": default_dry_run(kind) if dry_run is None else bool(dry_run),
             "status": "pending", "created_at": _now(), "status_at": _now()}
        with self._lock:
            if key and any(q.get("key") == key and q.get("status") in NEVER_AGAIN
                           for q in self._data["proposals"].values()):
                return None
            self._data["proposals"][pid] = p
            self._save()
        self._log({"event": "proposed", "id": pid, "kind": kind, "key": key, "title": title, "dry_run": p["dry_run"]})
        return dict(p)

    def get(self, pid: str) -> dict | None:
        with self._lock:
            p = self._data["proposals"].get(pid)
            return json.loads(json.dumps(p)) if p else None

    def set_message(self, pid: str, channel: str, ts: str) -> None:
        with self._lock:
            p = self._data["proposals"].get(pid)
            if p:
                p["channel"], p["ts"] = channel, ts
                self._save()

    def by_key(self, key: str, active_only: bool = False) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self._data["proposals"].values() if p.get("key") == key
                    and (not active_only or p.get("status") in NEVER_AGAIN)]

    def open_or_rejected_keys(self, kind: str) -> set[str]:
        """Keys never to propose again: on screen, in flight, applied, or rejected (a human said no)."""
        with self._lock:
            return {p["key"] for p in self._data["proposals"].values()
                    if p.get("kind") == kind and p.get("key") and p.get("status") in NEVER_AGAIN}

    def count_since(self, kind: str, iso_day: str) -> int:
        with self._lock:
            return sum(1 for p in self._data["proposals"].values()
                       if p.get("kind") == kind and p.get("status") != "post_failed"
                       and str(p.get("created_at", "")).startswith(iso_day))

    def count_source_since(self, prefix: str, iso_day: str) -> int:
        with self._lock:
            return sum(1 for p in self._data["proposals"].values()
                       if str(p.get("source", "")).startswith(prefix) and p.get("status") != "post_failed"
                       and str(p.get("created_at", "")).startswith(iso_day))

    def meta(self, k: str, default=None):
        with self._lock:
            return self._data.setdefault("meta", {}).get(k, default)

    def set_meta(self, k: str, v) -> None:
        with self._lock:
            self._data.setdefault("meta", {})[k] = v
            self._save()

    def incr_meta(self, k: str, by: float = 1) -> float:
        with self._lock:
            m = self._data.setdefault("meta", {})
            m[k] = (m.get(k) or 0) + by
            self._save()
            return m[k]

    # --- transitions --------------------------------------------------------------------------------
    def transition(self, pid: str, frm: set[str], to: str, user: str, **fields) -> tuple[dict | None, str]:
        """Compare-and-set. Returns (proposal, "") on success or (proposal|None, reason) when refused."""
        with self._lock:
            p = self._data["proposals"].get(pid)
            if not p:
                return None, "I can't find that proposal any more (it may be from before a reset)."
            if p.get("status") not in frm:
                who = f" by <@{p['decided_by']}>" if p.get("decided_by") else ""
                return dict(p), f"That one is already {p.get('status')}{who}."
            p["status"], p["status_at"] = to, _now()
            if to in ("applying", "rejected"):
                p["decided_by"], p["decided_at"] = user, _now()
            if to == "undoing":
                p["undo_by"] = user
            p.update(fields)
            self._save()
            out = dict(p)
        self._log({"event": to, "id": pid, "user": user, **{k: v for k, v in fields.items() if k != "payload"}})
        return out, ""

    def sweep_stale(self, minutes: int) -> list[str]:
        """At start-up: anything still applying/undoing after `minutes` was interrupted (restart mid-apply). We can't
        know whether the write happened, so it becomes `unknown` (the card offers Re-check), never `failed`."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        hit = []
        with self._lock:
            for p in self._data["proposals"].values():
                if p.get("status") not in ("applying", "undoing"):
                    continue
                try:
                    at = datetime.fromisoformat(p.get("status_at") or p.get("decided_at") or p["created_at"])
                except (KeyError, ValueError):
                    at = cutoff
                if at <= cutoff:
                    was = p["status"]
                    p["status"], p["status_at"] = "unknown", _now()
                    p["result_text"] = (f"The bot restarted while this was {was}, so I can't tell whether it went "
                                        "through. Press Re-check before doing anything else.")
                    p["unknown_from"] = was
                    hit.append(p["id"])
            if hit:
                self._save()
        for pid in hit:
            self._log({"event": "unknown", "id": pid, "user": "sweep"})
        return hit
