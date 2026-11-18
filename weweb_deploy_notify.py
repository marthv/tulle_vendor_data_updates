"""WeWeb staging publish -> short "what changed" message in Slack #deployment.

Run on a Railway cron (railway.wwdeploy.toml, every 5 min). Each run:
  1. Reads the staging site's home HTML: `_wwcv=<cacheVersion>` + the entry-client hash
     identify the build. Same as the saved build -> exit quietly (cheap: one ~75 KB GET).
  2. On a new build: reads main-*.js (page list + chunk names) and every page's
     /data/<pageId>.json (elements, workflows, variables, formulas, collections), and
     reduces it to a snapshot of names, visible text, show/hide formulas and content hashes.
  3. Diffs that snapshot against the previous one, has Claude turn the diff into a few
     plain bullets (falls back to a rule-based list without ANTHROPIC_API_KEY or on error),
     posts to DEPLOY_SLACK_WEBHOOK_URL, then saves the new snapshot.

First run (no snapshot yet) only saves a baseline and posts nothing.
The snapshot is saved only after the post succeeds, so a failed post retries next run.

Env:
  DEPLOY_SLACK_WEBHOOK_URL  incoming webhook for #deployment (absent -> print only)
  ANTHROPIC_API_KEY         optional; without it the message is the plain list
  DEPLOY_NOTIFY_MODEL       default claude-sonnet-5-5
  WW_STAGING_HOST           default the Tulle project's *-staging.weweb.io host
  SNAPSHOT_DIR              default /data (Railway volume); falls back to ./.ww_snapshots

Flags:
  --dry-run   build + diff + print the message; no post, no save
  --baseline  save the current build as the baseline without posting
  --diff A B  diff two saved snapshot files and print the message (offline testing)
"""
from __future__ import annotations

import concurrent.futures as cf
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

STAGING_HOST = os.environ.get("WW_STAGING_HOST", "d830a133-815b-419b-8efe-a0f104b3ece2-staging.weweb.io")
BASE = f"https://{STAGING_HOST}"
MODEL = os.environ.get("DEPLOY_NOTIFY_MODEL", "claude-sonnet-5-5")
UA = {"User-Agent": "tulle-deploy-notify/1.0"}
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def snapshot_dir() -> Path:
    d = Path(os.environ.get("SNAPSHOT_DIR", "/data"))
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / ".w").write_text("x")
        (d / ".w").unlink()
        return d
    except OSError:
        d = Path(".ww_snapshots")
        d.mkdir(exist_ok=True)
        return d


def get(url: str, tries: int = 3) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"{r.status_code} for {url}")
        except requests.RequestException as e:
            last = e
        time.sleep(2 * (i + 1))
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------- build identity

def build_id() -> tuple[str, int, str]:
    h = get(f"{BASE}/?t={int(time.time())}").text
    cv = re.search(r"_wwcv=(\d+)", h)
    entry = re.search(r"assets/(entry-client-[A-Za-z0-9_-]+\.js)", h)
    if not cv or not entry:
        raise RuntimeError("staging HTML has no _wwcv / entry-client - page format changed?")
    return f"{cv.group(1)}:{entry.group(1)}", int(cv.group(1)), entry.group(1)


# ---------------------------------------------------------------- snapshot

def plain_text(v) -> str | None:
    """`_ww-text_text` is {"en": html} or a formula {code, __wwtype}."""
    if v is None:
        return None
    if isinstance(v, dict) and "code" in v:
        return "[formula] " + str(v.get("code"))[:200]
    if isinstance(v, dict):
        v = v.get("en", next(iter(v.values()), ""))
    s = re.sub(r"<[^>]+>", " ", str(v))
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    return s[:300]


def visibility(obj: dict) -> str | None:
    """conditionalRendering sits inside _state.style.<breakpoint>; collect per breakpoint."""
    out = []
    for bp, st in ((obj.get("_state") or {}).get("style") or {}).items():
        if isinstance(st, dict) and "conditionalRendering" in st:
            cr = st["conditionalRendering"]
            code = cr.get("code") if isinstance(cr, dict) else cr
            out.append(f"{bp}: {str(code)[:160]}")
    return " | ".join(sorted(out)) or None


def h(x) -> str:
    return hashlib.sha1(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()[:16]


def items_of(coll) -> list[tuple[str, dict]]:
    if isinstance(coll, dict):
        return [(str(k), v) for k, v in coll.items() if isinstance(v, dict)]
    if isinstance(coll, list):
        return [(str(v.get("id") or v.get("uid") or i), v) for i, v in enumerate(coll) if isinstance(v, dict)]
    return []


def label(it: dict) -> str:
    """Published workflows carry no name; describe them by their steps instead."""
    acts = it.get("actions")
    if isinstance(acts, dict) and acts:
        steps = [str(a.get("type")) for a in acts.values() if isinstance(a, dict)]
        return "workflow with steps " + " > ".join(steps[:5]) + (" ..." if len(steps) > 5 else "")
    return ""


def build_snapshot(cv: int, entry: str) -> dict:
    entry_js = get(f"{BASE}/assets/{entry}").text
    m = re.search(r"(main-[A-Za-z0-9_-]+\.js)", entry_js)
    if not m:
        raise RuntimeError("entry-client has no main-*.js reference")
    main_js = get(f"{BASE}/assets/{m.group(1)}").text

    pages = {}
    for pm in re.finditer(
        r"\{id:`(" + UUID + r")`,linkId:`[^`]*`,name:`([^`]*)`,folder:(?:null|`([^`]*)`),paths:\{([^}]*)\}",
        main_js,
    ):
        pid, name, folder, paths = pm.groups()
        dp = re.search(r"default:`([^`]*)`", paths)
        pages[pid] = {"name": name, "folder": folder or "", "path": "/" + (dp.group(1) if dp else "")}
    if not pages:
        raise RuntimeError("no pages found in main.js - format changed?")

    chunks = sorted(set(re.findall(r"assets/([A-Za-z0-9_.-]+\.js)", main_js)))

    def fetch(pid):
        return pid, get(f"{BASE}/data/{pid}.json?t={cv}").json()

    objects, logic = {}, {"workflow": {}, "variable": {}, "formula": {}, "collection": {}}
    stale = []
    with cf.ThreadPoolExecutor(8) as ex:
        for pid, d in ex.map(fetch, pages):
            if int(d.get("cacheVersion") or 0) != cv:
                stale.append(pid)
            pname = pages[pid]["name"]
            for uid, o in (d.get("wwObjects") or {}).items():
                rec = objects.get(uid)
                if rec is None:
                    content = o.get("content") or {}
                    rec = objects[uid] = {
                        "name": o.get("name") or "",
                        "text": plain_text(((content.get("default") or {}).get("_ww-text_text"))),
                        "vis": visibility(o),
                        "hash": h([o.get("name"), content, o.get("_state")]),
                        "content_hash": h(content),
                        "style_hash": h((o.get("_state") or {}).get("style")),
                        "wf_hash": h((o.get("_state") or {}).get("interactions")),
                        "pages": [],
                    }
                if pname not in rec["pages"]:
                    rec["pages"].append(pname)
            for kind, key in (("workflow", "workflows"), ("variable", "variables"),
                              ("formula", "formulas"), ("collection", "collections")):
                for iid, it in items_of(d.get(key)):
                    if iid not in logic[kind]:
                        logic[kind][iid] = {"name": it.get("name") or label(it), "hash": h(it), "pages": []}
                    if pname not in logic[kind][iid]["pages"]:
                        logic[kind][iid]["pages"].append(pname)
    if stale:
        raise RuntimeError(f"{len(stale)} page data files not yet at cacheVersion {cv} (CDN catching up) - retry next run")
    return {"cv": cv, "entry": entry, "taken_at": int(time.time()), "pages": pages,
            "chunks": chunks, "objects": objects, "logic": logic}


# ---------------------------------------------------------------- diff

def where(rec) -> str:
    p = rec.get("pages") or []
    return p[0] if len(p) == 1 else (f"{len(p)} pages (shared)" if p else "?")


def diff(old: dict, new: dict) -> dict:
    out: dict = {"from_cv": old["cv"], "to_cv": new["cv"]}
    op, np_ = old["pages"], new["pages"]
    out["pages_added"] = [f'{np_[p]["name"]} ({np_[p]["path"]})' for p in np_ if p not in op]
    out["pages_removed"] = [f'{op[p]["name"]} ({op[p]["path"]})' for p in op if p not in np_]
    out["pages_renamed_or_moved"] = [
        f'{op[p]["name"]} {op[p]["path"]} -> {np_[p]["name"]} {np_[p]["path"]}'
        for p in np_ if p in op and (op[p]["name"], op[p]["path"]) != (np_[p]["name"], np_[p]["path"])]

    oo, no = old["objects"], new["objects"]
    added, removed, text, vis, other = [], [], [], [], []
    for uid, r in no.items():
        if uid not in oo:
            added.append({"page": where(r), "element": r["name"], "text": r["text"]})
            continue
        o = oo[uid]
        if o["hash"] == r["hash"]:
            continue
        if o["text"] != r["text"]:
            text.append({"page": where(r), "element": r["name"], "before": o["text"], "after": r["text"]})
        if o["vis"] != r["vis"]:
            vis.append({"page": where(r), "element": r["name"], "before": o["vis"], "after": r["vis"]})
        if o["text"] == r["text"] and o["vis"] == r["vis"]:
            what = []
            if o["name"] != r["name"]:
                what.append(f'renamed from "{o["name"]}"')
            if o["content_hash"] != r["content_hash"]:
                what.append("settings/bindings")
            if o["style_hash"] != r["style_hash"]:
                what.append("style")
            if o.get("wf_hash") != r.get("wf_hash"):
                what.append("its workflows")
            other.append({"page": where(r), "element": r["name"], "changed": ", ".join(what) or "minor"})
    for uid, r in oo.items():
        if uid not in no:
            removed.append({"page": where(r), "element": r["name"], "text": r["text"]})
    out.update(elements_added=added, elements_removed=removed, text_changed=text,
               visibility_changed=vis, elements_otherwise_changed=other)

    for kind in ("workflow", "variable", "formula", "collection"):
        ol, nl = old["logic"].get(kind, {}), new["logic"].get(kind, {})
        out[f"{kind}s_added"] = [f'{v["name"] or i} ({where(v)})' for i, v in nl.items() if i not in ol]
        out[f"{kind}s_removed"] = [f'{v["name"] or i} ({where(v)})' for i, v in ol.items() if i not in nl]
        out[f"{kind}s_changed"] = [f'{v["name"] or i} ({where(v)})' for i, v in nl.items()
                                   if i in ol and ol[i]["hash"] != v["hash"]]

    def stem(c):  # "wwElement-0cRVERbD.js" -> "wwElement"; page chunks -> page uuid
        return re.sub(r"-[A-Za-z0-9_]{8}\.js$", "", c)
    oc, nc = set(old["chunks"]), set(new["chunks"])
    gone, came = sorted(oc - nc), sorted(nc - oc)
    comp = [c for c in came if stem(c).startswith("wwElement")]
    out["coded_component_bundles_changed"] = len(comp)
    out["page_code_bundles_changed"] = sorted({new["pages"].get(stem(c), {}).get("name", "")
                                               for c in came if re.fullmatch(UUID, stem(c))} - {""})
    out["other_bundles_changed"] = len([c for c in came if c not in comp and not re.fullmatch(UUID, stem(c))])
    _ = gone
    return out


def is_empty(d: dict) -> bool:
    return not any(v for k, v in d.items() if k not in ("from_cv", "to_cv", "page_code_bundles_changed",
                                                        "other_bundles_changed"))


def capped(d: dict, max_items: int = 40) -> dict:
    """Keep the prompt bounded; say how many were cut."""
    c = {}
    for k, v in d.items():
        if isinstance(v, list) and len(v) > max_items:
            c[k] = v[:max_items] + [f"... and {len(v) - max_items} more"]
        else:
            c[k] = v
    return c


# ---------------------------------------------------------------- message

PROMPT = """You write the Slack note the Tulle Together team sees each time the WeWeb app is published to STAGING.
Below is a machine diff between the previous staging build and this one (JSON). Write:
- 3 to 8 short bullets, most important first, grouped by page where it helps ("Pricing page: ...").
- Plain words a non-engineer understands. Quote changed copy exactly but keep quotes short.
- Collapse many small style/settings tweaks into one bullet with a count; don't list them one by one.
- Only state what the diff shows. Never guess why something changed or whether it works.
- Workflows have no names in the published files; they are described by their steps. Mention workflow changes briefly (count + page), don't list step names unless that is all there is.
- "coded_component_bundles_changed" means a custom coded component (e.g. the Planner chat) got new code; say that without naming one.
- If the only changes are minor style/settings tweaks, say so in one bullet.
Output ONLY the bullets, each starting with "• ". No heading, no sign-off."""


def ai_bullets(d: dict) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=MODEL, max_tokens=3000,
            messages=[{"role": "user", "content": PROMPT + "\n\nDIFF:\n" + json.dumps(capped(d), indent=1)[:60000]}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        if getattr(msg, "stop_reason", "") == "max_tokens" and "\n" in text:
            text = text.rsplit("\n", 1)[0]  # drop a half-written last bullet
        return text or None
    except Exception as e:  # never let the summary block the notice
        print(f"[deploy-notify] AI summary failed, using plain list: {e!r}", file=sys.stderr)
        return None


def plain_bullets(d: dict) -> str:
    b = []
    for k, label in (("pages_added", "New page"), ("pages_removed", "Removed page"),
                     ("pages_renamed_or_moved", "Page renamed/moved")):
        for x in d[k][:5]:
            b.append(f"• {label}: {x}")
    for x in d["text_changed"][:6]:
        b.append(f'• {x["page"]} - {x["element"]}: "{(x["before"] or "")[:80]}" -> "{(x["after"] or "")[:80]}"')
    if d["visibility_changed"]:
        b.append(f'• Show/hide rule changed on {len(d["visibility_changed"])} element(s): '
                 + ", ".join(x["element"] for x in d["visibility_changed"][:5]))
    if d["elements_added"] or d["elements_removed"]:
        b.append(f'• Elements: {len(d["elements_added"])} added, {len(d["elements_removed"])} removed')
    if d["elements_otherwise_changed"]:
        b.append(f'• {len(d["elements_otherwise_changed"])} element(s) with style/settings tweaks')
    wf = sum(len(d[f"workflows_{s}"]) for s in ("added", "removed", "changed"))
    if wf:
        b.append(f"• {wf} workflow change(s): " + ", ".join((d["workflows_added"] + d["workflows_changed"] + d["workflows_removed"])[:5]))
    if d["coded_component_bundles_changed"]:
        b.append(f'• Coded component code updated ({d["coded_component_bundles_changed"]} bundle(s))')
    return "\n".join(b) or "• No content differences found (rebuild only)"


def message(d: dict) -> str:
    head = f":package: *WeWeb staging publish* - build {d['to_cv']} (previous {d['from_cv']})"
    if is_empty(d):
        return head + "\n• No content differences found (rebuild only)"
    body = ai_bullets(d) or plain_bullets(d)
    return f"{head}\n{body}\n<https://{STAGING_HOST}|Open staging>"


def post(text: str) -> None:
    url = os.environ.get("DEPLOY_SLACK_WEBHOOK_URL", "")
    if not url:
        print("[deploy-notify] DEPLOY_SLACK_WEBHOOK_URL not set - message not posted:\n" + text)
        return
    r = requests.post(url, json={"text": text}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Slack post failed: {r.status_code} {r.text[:200]}")


# ---------------------------------------------------------------- storage

def load(p: Path):
    return json.loads(gzip.decompress(p.read_bytes())) if p.exists() else None


def save(p: Path, data) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(data).encode()))
    tmp.replace(p)


def main(argv: list[str]) -> int:
    if argv[:1] == ["--diff"]:
        print(message(diff(load(Path(argv[1])), load(Path(argv[2])))))
        return 0
    d = snapshot_dir()
    snap_path = d / "staging_snapshot.json.gz"
    prev = load(snap_path)
    key, cv, entry = build_id()
    if prev and f'{prev["cv"]}:{prev["entry"]}' == key and "--dry-run" not in argv:
        print(f"[deploy-notify] no new staging build (still {cv})")  # heartbeat: proves the run happened + the snapshot persisted
        return 0
    snap = build_snapshot(cv, entry)
    if prev is None or "--baseline" in argv:
        save(snap_path, snap)
        print(f"[deploy-notify] baseline saved at build {cv} ({len(snap['pages'])} pages, {len(snap['objects'])} elements)")
        return 0
    text = message(diff(prev, snap))
    if "--dry-run" in argv:
        print(text)
        return 0
    post(text)
    save(d / f"staging_snapshot_{prev['cv']}.json.gz", prev)  # keep the previous one for debugging
    for old in sorted(d.glob("staging_snapshot_*.json.gz"), key=lambda f: f.stat().st_mtime)[:-10]:
        old.unlink()  # keep the last 10
    save(snap_path, snap)
    print(f"[deploy-notify] posted build {prev['cv']} -> {cv}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:
        print(f"[deploy-notify] FAILED: {e!r}", file=sys.stderr)
        sys.exit(2)
