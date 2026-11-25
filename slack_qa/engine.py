"""Read-only answers from the experiments-engine service (private network, X-Exp-Key):

  "how's the save offer experiment doing?"  -> GET /snapshot, quote the engine's own verdict (TL;DR, recommended
                                              call, how we'll make the call). No numbers are re-derived here.
  "what changed in the last WeWeb publish?" -> GET /changes (WeWeb publishes the engine detected by polling) joined
                                              with the #deployment staging note posted nearest each publish.

Falls back to the registry (knowledge.py) when the engine is unset or unreachable, and says so.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import requests

import config
import dedupe

log = logging.getLogger("slack_qa.engine")

EXPERIMENT_Q = re.compile(r"\b(how(?:'s| is| are)|status of|update on|where are we on|doing|results?|verdict|call)\b"
                          r".*\b(experiment|test|split|rotation)s?\b|\b(experiment|test)s?\b.*\b(doing|going|status|"
                          r"results?|verdict)\b", re.I)
PUBLISH_Q = re.compile(r"\b(weweb|publish(?:ed|es)?|went live|last release)\b", re.I)


class Engine:
    def __init__(self, session=None, ttl: float = 300):
        self.http = session or requests.Session()
        self.ttl = ttl
        self._cache: dict = {}

    @property
    def enabled(self) -> bool:
        return bool(config.engine_url() and config.engine_key())

    def _get(self, path: str, params: dict | None = None):
        hit = self._cache.get((path, json.dumps(params or {}, sort_keys=True)))
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        r = self.http.get(config.engine_url().rstrip("/") + path, params=params or {}, timeout=15,
                          headers={"X-Exp-Key": config.engine_key(), "Accept-Encoding": "gzip"})
        if r.status_code != 200:
            raise RuntimeError(f"engine {path} -> HTTP {r.status_code}")
        data = r.json()
        self._cache[(path, json.dumps(params or {}, sort_keys=True))] = (time.time(), data)
        return data

    def snapshot(self) -> dict:
        return self._get("/snapshot")

    def changes(self, limit: int = 200) -> dict:
        return self._get("/changes", {"limit": limit})


def _words(s: str) -> set[str]:
    stop = {"the", "a", "an", "how", "is", "are", "doing", "experiment", "test", "going", "status", "of", "on", "s",
            "what", "whats", "our", "with", "update", "results", "result", "verdict", "call", "it", "this", "that",
            "tulle", "bot", "we", "where", "split", "rotation"}
    return {w for w in dedupe.norm(s).split() if w not in stop and len(w) > 1}


def match_experiments(question: str, exps: list[dict], n: int = 3) -> list[dict]:
    q = _words(question)
    if not q:
        return []
    scored = []
    for e in exps:
        hay = _words(" ".join(str(e.get(k) or "") for k in ("id", "name", "title", "what_changed", "tests")))
        s = len(q & hay) / max(1, len(q))
        if s >= 0.5:
            scored.append((s, e))
    scored.sort(key=lambda t: -t[0])
    return [e for _, e in scored[:n]]


def _rotation(snap: dict) -> list[dict]:
    return [e for e in snap.get("experiments") or [] if e.get("in_rotation")]


def _unlocked_split(e: dict) -> bool:
    """EXP_TAB_V2 is on here: hidden by the registry's own rule (readlock), whatever the engine's snapshot says; a
    running split the engine did not lock, or one the registry can't confirm, is hidden too (fail closed)."""
    if str(os.environ.get("EXP_TAB_V2", "")).strip().lower() not in ("1", "on", "true", "yes"):
        return False
    try:
        import knowledge
        reg = next((x for x in knowledge.load_registry().get("experiments") or []
                    if isinstance(x, dict) and x.get("id") == e.get("id")), None) if e.get("id") else None
        if reg is not None and knowledge.readlock_module().hidden(reg):
            return True
    except Exception:  # noqa: BLE001
        pass
    return (e.get("assignment_type") == "split" and e.get("lifecycle") in ("live", "paused")
            and "result_hidden" not in e)


def format_experiment(e: dict) -> str:
    v = e.get("verdict") or {}
    hw = v.get("how_we_call") or {}
    name = e.get("name") or e.get("title") or e.get("id")
    lines = [f"*{name}* ({e.get('status', '?')})"]
    if e.get("result_hidden") or _unlocked_split(e):      # EXP_TAB_V2 read lock: one read on a fixed date, nothing before it
        lines.append(str(e.get("result_text") or "Result: hidden until its read date") + ".")
        if e.get("days_running") is not None:
            lines.append(f"Running for {e['days_running']} days.")
        if e.get("lifecycle") == "decided" or e.get("status") == "stopped early":
            return "\n".join(lines)
        bad = [c for c in e.get("checks") or [] if c.get("colour") in ("red", "amber")]
        lines += [f"Setup check, {c.get('label')} ({c.get('colour')}): {c.get('text')}" for c in bad]
        return "\n".join(lines)
    if v.get("tldr"):
        lines.append(v["tldr"])
    rec = v.get("recommend")
    if isinstance(rec, dict) and (rec.get("label") or rec.get("text")):
        lines.append(f"Recommended call: {rec.get('label') or rec.get('text')}")
    elif isinstance(rec, str) and rec:
        lines.append(f"Recommended call: {rec}")
    if hw.get("now"):
        lines.append(f"How we'll call it: {hw['now']}")
    elif hw.get("bar"):
        lines.append(f"The bar: {hw['bar']}")
    if v.get("unstick"):
        lines.append(f"To move it along: {v['unstick']}")
    return "\n".join(lines)


def answer_experiment(question: str, engine: Engine, registry_fallback) -> str | None:
    """Return the reply text, or None if this isn't something we can match."""
    if engine.enabled:
        try:
            snap = engine.snapshot()
        except Exception as e:  # noqa: BLE001
            log.warning("engine snapshot failed: %s", type(e).__name__)
            snap = None
        if snap:
            exps = (snap.get("experiments") or []) + (snap.get("decided") or [])
            hits = match_experiments(question, exps)
            if not hits and re.search(r"\b(all|running|live|rotation|which|what)\b", question, re.I):
                hits = _rotation(snap)
            if hits:
                stamp = snap.get("generated_at") or snap.get("last_run") or ""
                body = "\n\n".join(format_experiment(e) for e in hits)
                return body + (f"\n_From the experiments engine's live readout{(' (' + str(stamp)[:16] + ' UTC)') if stamp else ''}, "
                               "internal accounts excluded._")
    reg = registry_fallback(question)
    if reg:
        return reg + "\n_From the experiment registry (the live engine readout wasn't available)._"
    return None


def answer_publishes(engine: Engine, deploy_history: list[dict], n: int = 3) -> str | None:
    if not engine.enabled:
        return None
    try:
        ch = engine.changes(300)
    except Exception as e:  # noqa: BLE001
        log.warning("engine changes failed: %s", type(e).__name__)
        return None
    pubs = [e for e in ch.get("events") or [] if e.get("source") == "weweb"][:n]
    if not pubs:
        return "I don't see a WeWeb production publish in the engine's change feed yet."
    notes = []
    for m in deploy_history:
        try:
            notes.append((float(m.get("ts")), (m.get("text") or "").strip()))
        except (TypeError, ValueError):
            continue
    out = []
    for p in pubs:
        try:
            t = datetime.fromisoformat(str(p["ts"]).replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            t = None
        near = None
        if t is not None and notes:
            cand = [(abs(nt - t), txt) for nt, txt in notes if abs(nt - t) <= 6 * 3600 and txt]
            near = min(cand)[1] if cand else None
        when = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%b %d %H:%M UTC") if t else p.get("ts", "?")
        line = f"• *{when}* · {p.get('title', 'WeWeb publish')}"
        if near:      # quoted Slack text: no <!here>/<!channel> pings or disguised links
            near = near.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        line += f"\n   What changed (from the nearest #deployment note): {near[:400]}" if near else \
            "\n   No #deployment note near this publish, so I can't say what changed."
        out.append(line)
    unl = [u for u in ch.get("unlogged") or [] if (u.get("source") or (u.get("event") or {}).get("source")) == "weweb"]
    tail = f"\n{len(unl)} of these publish(es) have no experiment-registry entry yet." if unl else ""
    return "Recent WeWeb production publishes:\n" + "\n".join(out) + tail
