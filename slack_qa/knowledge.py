"""What the bot knows, assembled per question.

STATIC (identical bytes for every request until the next deploy -> prompt-cached):
  * experiments/experiment_log.json (the experiment registry), whole
  * MEMORY.md's "PICK UP HERE" section (the owner's current handoff list)
DYNAMIC (per question, after the cache breakpoint):
  * the conversation being answered (thread replies or recent channel messages)
  * #deployment's last ~30 messages (WeWeb staging publish summaries)
  * recent messages from every other channel the bot is in
  * recent commits: live from GitHub when GITHUB_TOKEN is set, else the synced commit logs,
    plus a file-list summary of up to 3 commits the question is clearly about
  * the most relevant memory-note / commit-log chunks by BM25 (the notes are ~1.4 MB, too big for one prompt)
Everything dynamic is scrubbed for secrets before it reaches Claude.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import config
from scrub import scrub

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REGISTRY = REPO / "experiments" / "experiment_log.json"
KNOW = HERE / "knowledge"

STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its this
that these those what which who whom whose why how when where do does did done can could should would will
shall may might must have has had i you he she we they me my our your their them us not no yes so than then
there here about into over under up down out any all some just also only very more most much many still yet
tulle bot please thanks thank hey hi""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9_\-]*[a-z0-9]|[a-z0-9]", (text or "").lower())
            if len(t) > 1 and t not in STOP]


# --- static ------------------------------------------------------------------------------------------------
_static_cache: dict = {}


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def load_registry(path: Path = REGISTRY) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"experiments": [], "changes": []}


def memory_top(path: Path = KNOW / "memory" / "MEMORY.md", max_chars: int = 12000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    i = text.find("## PICK UP HERE")
    if i < 0:
        return text[:max_chars]
    j = text.find("\n## ", i + 5)
    return text[i:j if j > 0 else None][:max_chars]


# Framework v2 (experiments/FRAMEWORK.md, 2026-11-19) added structured spec fields the engine and dash use.
# The bot answers from the human-readable fields, so the structured ones are dropped here: the registry
# grew ~2.4x and this whole block goes into every prompt (cost + QA_DAILY_USD cap).
BOT_DROP_KEYS = frozenset({
    "spec_gaps", "metric", "audit_2026_11_19", "audience", "assignment", "guardrails", "rollback", "surfaces",
    "tests", "tier", "max_days", "planned_days", "hypothesis_reconstructed", "backfilled", "framework_version",
    "decision_rule", "links", "reporting", "what_changed", "hypothesis", "notes_synced_through", "paused_ranges",
})


def readlock_module():
    if str(REPO) not in sys.path:
        sys.path.append(str(REPO))
    from experiments_engine import readlock
    return readlock


def _read_locked(reg: dict) -> dict:
    """EXP_TAB_V2: a running split's results, notes and read-outs never reach a prompt before its read date. If the
    lock module is missing the bot sees no split's results at all (fail closed)."""
    if str(os.environ.get("EXP_TAB_V2", "")).strip().lower() not in ("1", "on", "true", "yes"):
        return reg
    try:
        return readlock_module().redact_registry(reg)
    except Exception:  # noqa: BLE001
        keep = ("id", "name", "status", "start_date", "review_date", "read_date")
        out = dict(reg)
        out["experiments"] = [{k: e[k] for k in keep if k in e} if isinstance(e, dict) and (
            "launch_lock" in e or "split" in json.dumps(e.get("assignment") or e.get("design") or "").lower()) else e
            for e in reg.get("experiments", [])]
        return out


def bot_view(reg: dict) -> dict:
    """The registry as the bot sees it: same shape, minus the structured v2 spec fields."""
    reg = _read_locked(reg)
    out = {k: v for k, v in reg.items() if k != "experiments"}
    out["experiments"] = [{k: v for k, v in e.items() if k not in BOT_DROP_KEYS} if isinstance(e, dict) else e
                          for e in reg.get("experiments", [])]
    return out


def static_context() -> str:
    """Registry + MEMORY.md top. Rebuilt only when either file changes, so the bytes stay identical."""
    key = (_mtime(REGISTRY), _mtime(KNOW / "memory" / "MEMORY.md"))
    if os.environ.get("EXP_TAB_V2"):      # a read date passing must rebuild the text
        key += (os.environ["EXP_TAB_V2"], time.strftime("%Y-%m-%d", time.gmtime()))
    if _static_cache.get("key") != key:
        reg = load_registry()
        _static_cache["key"] = key
        _static_cache["text"] = (
            "<experiment_registry source=\"experiments/experiment_log.json\">\n"
            + scrub(json.dumps(bot_view(reg), ensure_ascii=False, separators=(",", ":")))
            + "\n</experiment_registry>\n\n<owner_handoff_notes source=\"memory note MEMORY.md (PICK UP HERE)\">\n"
            + memory_top() + "\n</owner_handoff_notes>")
    return _static_cache["text"]


def headlines(days: int = 21) -> str:
    """Short list of recent changes for the cheap triage call."""
    reg = load_registry()
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    lines = []
    for e in reg.get("experiments", []):
        if e.get("status") in ("live", "planned") or (e.get("start_date") or "") >= cutoff:
            lines.append(f"- experiment {e.get('id')} [{e.get('status')}]: {(e.get('name') or e.get('what') or '')[:140]}")
    for c in reg.get("changes", []):
        if (c.get("date") or "") >= cutoff:
            lines.append(f"- change {c.get('date')}: {(c.get('what') or '')[:160]}")
    return "\n".join(lines[-60:])


# --- BM25 over memory notes + commit logs ----------------------------------------------------------------------
class Corpus:
    def __init__(self, chunks: list[tuple[str, str]]):
        self.chunks = chunks                      # (title, text)
        self.toks = [Counter(tokens(t + " " + x)) for t, x in chunks]
        self.lens = [sum(c.values()) for c in self.toks]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        df = Counter()
        for c in self.toks:
            df.update(c.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, budget_chars: int = 24000, k1: float = 1.4, b: float = 0.75,
               max_chunks: int = 12) -> list[tuple[str, str, float]]:
        q = set(tokens(query))
        if not q or not self.chunks:
            return []
        scored = []
        for i, c in enumerate(self.toks):
            s = 0.0
            for t in q:
                f = c.get(t)
                if f:
                    s += self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lens[i] / self.avg))
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        out, used = [], 0
        for s, i in scored[:max_chunks * 2]:
            title, text = self.chunks[i]
            if used + len(text) > budget_chars:
                continue
            out.append((title, text, s))
            used += len(text)
            if len(out) >= max_chunks:
                break
        return out


def _chunk(title: str, text: str, size: int = 3000) -> list[tuple[str, str]]:
    parts, cur = [], []
    n = 0
    for line in text.splitlines():
        if (n + len(line) > size and cur) or (line.startswith("#") and n > size // 2):
            parts.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        parts.append("\n".join(cur))
    return [(title, p) for p in parts if p.strip()]


_corpus_cache: dict = {}


def corpus(know: Path = KNOW) -> Corpus:
    key = str(know)
    if key not in _corpus_cache:
        chunks: list[tuple[str, str]] = []
        for f in sorted((know / "memory").glob("*.md")):
            if f.name == "MEMORY.md":
                continue                          # its top section is always in the static block
            chunks += _chunk(f"memory note {f.stem}", f.read_text(encoding="utf-8", errors="replace"))
        for f in sorted(know.glob("commits_*.md")):
            chunks += _chunk(f"commit log {f.stem[len('commits_'):]}", f.read_text(encoding="utf-8"), size=2500)
        _corpus_cache[key] = Corpus(chunks)
    return _corpus_cache[key]


def synced_recent_commits(days: int = 14, know: Path = KNOW, limit: int = 120) -> list[str]:
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    rows = []
    for f in sorted(know.glob("commits_*.md")):
        repo = f.stem[len("commits_"):]
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC (.*)", line)
            if m and m.group(1)[:10] >= cutoff:
                rows.append(f"{m.group(1)} {repo} {m.group(2)}")
    return sorted(rows, reverse=True)[:limit]


# --- dynamic -------------------------------------------------------------------------------------------------
def _block(tag: str, attrs: str, lines: list[str] | str) -> str:
    body = lines if isinstance(lines, str) else "\n".join(lines)
    return f"<{tag} {attrs}>\n{body}\n</{tag}>" if body.strip() else ""


def build_dynamic(question: str, channel: str, thread_ts: str | None, slack, github,
                  current_ts: str | None = None) -> str:
    parts: list[str] = []

    # 1. The conversation being answered.
    convo: list[str] = []
    if slack is not None:
        if thread_ts and thread_ts != current_ts:
            convo = slack.render(slack.replies(channel, thread_ts))
        else:
            convo = slack.render(slack.history(channel, limit=30, days=7)[-30:])
    parts.append(_block("current_conversation", f'channel="{channel}"', [scrub(x) for x in convo]))

    # 2. #deployment publish notes.
    dep = config.deploy_channel()
    if slack is not None and dep != channel:
        parts.append(_block("deployment_channel", f'channel="{dep}" note="#deployment: WeWeb publish summaries"',
                            [scrub(x) for x in slack.render(slack.history(dep, limit=30, days=21)[-30:], 900)]))

    # 3. Other channels the bot is in.
    if slack is not None:
        budget = 12000
        for ch in slack.member_channels():
            if ch["id"] in (channel, dep) or ch["id"] in config.blocked_channels() or budget <= 0:
                continue
            lines = [scrub(x) for x in slack.render(slack.history(ch["id"], limit=50, days=7)[-15:], 300)]
            text = "\n".join(lines)[:min(4000, budget)]
            budget -= len(text)
            parts.append(_block("other_channel", f'channel="#{ch["name"]}"', text))

    # 4. Commits.
    if github is not None and github.enabled:
        rows = github.recent_commits(days=14)
        parts.append(_block("recent_commits", 'source="GitHub live, default branches, last 14 days"',
                            [scrub(f"{r['date']} {r['repo']} {r['sha']} {r['subject']}") for r in rows[:150]]))
        details = []
        for r in _commits_about(question, rows):
            d = github.commit_summary(r["full"], r["sha"])
            if d:
                files = ", ".join(f"{f['file']} (+{f['+']}/-{f['-']})" for f in d["files"][:20])
                details.append(scrub(f"{d['repo']} {d['sha']}: {d['stats'].get('total', '?')} lines changed: {files}"))
        parts.append(_block("commit_details", 'source="GitHub commit file lists"', details))
    else:
        parts.append(_block("recent_commits", 'source="synced commit logs (git log --all), last 14 days"',
                            synced_recent_commits()))

    # 5. Relevant memory-note / older commit-log chunks.
    hits = corpus().search(question + " " + " ".join(convo[-3:]))
    for title, text, _s in hits:
        parts.append(_block("note", f'source="{title}"', text))

    return "\n\n".join(p for p in parts if p)


def _commits_about(question: str, rows: list[dict], k: int = 3) -> list[dict]:
    """Commits whose hash appears in the question, else the best keyword matches (needs >= 2 shared terms)."""
    q = (question or "").lower()
    named = [r for r in rows if r["sha"] and r["sha"].lower() in q]
    if named:
        return named[:k]
    qt = set(tokens(question))
    scored = sorted(((len(qt & set(tokens(r["subject"]))), i) for i, r in enumerate(rows)), reverse=True)
    return [rows[i] for s, i in scored[:k] if s >= 2]
