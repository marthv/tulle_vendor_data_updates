"""Shipped-change feed (FRAMEWORK.md section 8).

Collectors return normalised events::

    {"ts": iso UTC, "source": "weweb"|"github"|"railway"|"xano", "ref": str, "title": str,
     "surfaces": [str], "url": str|None, "user_facing": bool, ...extra}

and :func:`match_unlogged` returns the events that touch a user-facing surface and have no
``changes[]`` entry or experiment start that covers them (section 8 "unlogged change").

Every collector is read-only and cheap:

* WeWeb   one anonymous GET of the production home page, at most every 30 min. The build
          identity is the same pair weweb_deploy_notify.py reads on staging: ``_wwcv=<n>``
          (cacheVersion) + the ``assets/entry-client-*.js`` hash.
* GitHub  public commits API, If-None-Match/ETag, at most ``MAX_GITHUB_CALLS`` (4) per run.
          ``GITHUB_READ_TOKEN`` (optional, read-only PAT) adds the repos in ``EXP_GITHUB_REPOS``.
* Railway nothing is fetched: ``POST /hooks/railway`` stores deploy events on the volume and
          this module reads them back. No Railway token exists or is needed.
* Xano    workspace audit log via the metadata API (GET only). 401/403/404 degrade to an
          "unavailable" note; nothing else is retried.

State lives in ``<EXP_DATA_DIR>/changefeed/``. Nothing touches the network or the filesystem at
import time. ``http`` arguments take anything with a ``requests``-style ``get``.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile

UA = {"User-Agent": "tulle-exp-engine/1.0 (read-only change feed)"}
WEWEB_URL = os.environ.get("EXP_WEWEB_URL", "https://tulletogether.app/")
WEWEB_MIN_INTERVAL_S = 30 * 60
MAIN_REPO = "marthv/tulle_vendor_data_updates"
MAX_GITHUB_CALLS = 4
GITHUB_PENDING_CAP = 20
FIRST_RUN_LOOKBACK_H = 48
XANO_META_BASE = os.environ.get("XANO_META_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta")
XANO_AUDIT_PATH = os.environ.get("XANO_AUDIT_PATH", "/workspace/1/audit_log")
EVENT_KEEP_DAYS = 90
MATCH_WINDOW_H = 24

# Railway services whose deploys change what couples see (project "tulle admin dash").
USER_FACING_SERVICES = tuple(s.strip() for s in os.environ.get(
    "EXP_USER_FACING_SERVICES", "rec-engine").split(",") if s.strip())

# Surface families a registry entry can be matched to by keyword (CHOICE, see match_unlogged).
FAMILY_KEYWORDS = {
    "weweb": (r"\bweweb\b", r"\bpublish", r"cacheversion", r"\bww-[a-z]", r"\bcomponent\b", r"\bpage\b", r"\bcopy\b"),
    "rec-engine": (r"rec-engine", r"\brec[ _]", r"\bREC_[A-Z_]+", r"\bplanner\b", r"rec_service"),
    "xano": (r"\bxano\b", r"\bep\s?\d+", r"\btable\s?\d+", r"\bendpoint", r"\btask\s?\d+", r"\bfunction\b"),
    "search": (r"search-api", r"\bsearch\b", r"meili"),
    "vendor-portal": (r"vendor[- ]portal",),
}
USER_FACING_PREFIXES = ("rec-engine", "weweb", "xano", "search-api", "vendor-portal")


# ----------------------------------------------------------------------------- helpers

def _utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


def iso(dt):
    return dt.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(v):
    """ISO string, date string, epoch seconds or epoch ms -> aware UTC datetime (or None)."""
    if v is None or v == "":
        return None
    if isinstance(v, _dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc)
    if isinstance(v, _dt.date):
        return _dt.datetime(v.year, v.month, v.day, tzinfo=_dt.timezone.utc)
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d{9,14}", v)):
        x = float(v)
        if x > 1e11:
            x /= 1000.0
        return _dt.datetime.fromtimestamp(x, _dt.timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            d = _dt.datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def state_dir(base=None):
    root = base or os.environ.get("EXP_DATA_DIR") or "/data/experiments"
    return os.path.join(root, "changefeed")


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return default


def _write_json(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, indent=1, sort_keys=True, default=str)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    out.append(r)
    except (FileNotFoundError, OSError):
        pass
    return out


def _write_jsonl(path, rows):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def event(ts, source, ref, title, surfaces, url=None, user_facing=None, **extra):
    surfaces = [s for s in (surfaces or []) if s]
    if user_facing is None:
        user_facing = any(str(s).startswith(USER_FACING_PREFIXES) for s in surfaces)
    e = {"ts": iso(parse_ts(ts) or _utcnow()), "source": source, "ref": str(ref) if ref is not None else "",
         "title": str(title or "")[:300], "surfaces": surfaces, "url": url, "user_facing": bool(user_facing)}
    e.update(extra)
    return e


def is_user_facing(ev):
    if "user_facing" in ev:
        return bool(ev["user_facing"])
    return any(str(s).startswith(USER_FACING_PREFIXES) for s in ev.get("surfaces") or [])


# ----------------------------------------------------------------------------- (a) WeWeb

_CV_RE = re.compile(r"_wwcv=(\d+)")
_ENTRY_RE = re.compile(r"assets/(entry-client-[A-Za-z0-9_-]+\.js)")


def parse_weweb_build(html_text):
    """(build_id, cache_version, entry_client) from a WeWeb page, the same markers as
    weweb_deploy_notify.build_id(). Raises ValueError if the page format changed."""
    cv = _CV_RE.search(html_text or "")
    entry = _ENTRY_RE.search(html_text or "")
    if not cv or not entry:
        raise ValueError("page has no _wwcv / entry-client marker - WeWeb page format changed?")
    return f"{cv.group(1)}:{entry.group(1)}", int(cv.group(1)), entry.group(1)


def poll_weweb(http, sdir, now=None, force=False, url=None):
    """Return (events, note). At most one GET per WEWEB_MIN_INTERVAL_S. The first ever poll
    saves a baseline and returns no event (same as the staging notifier)."""
    now = now or _utcnow()
    path = os.path.join(sdir, "weweb_state.json")
    st = _read_json(path, {})
    last = parse_ts(st.get("last_check"))
    if not force and last and (now - last).total_seconds() < WEWEB_MIN_INTERVAL_S:
        return [], None
    url = url or WEWEB_URL
    try:
        r = http.get(url, headers=UA, timeout=30)
        if getattr(r, "status_code", 0) != 200:
            raise RuntimeError(f"HTTP {getattr(r, 'status_code', '?')}")
        build, cv, entry = parse_weweb_build(r.text)
    except Exception as exc:
        st["last_check"] = iso(now)
        st["last_error"] = f"{type(exc).__name__}: {exc}"
        _write_json(path, st)
        return [], f"weweb: check failed ({st['last_error']})"
    events = []
    prev = st.get("build")
    if prev and prev != build:
        events.append(event(now, "weweb", str(cv),
                            f"WeWeb production publish (cacheVersion {st.get('cache_version')} -> {cv})",
                            ["weweb"], url="https://www.tulletogether.app/", user_facing=True,
                            entry_client=entry, previous=prev,
                            window_start=st.get("last_check"),
                            ts_note="detected by polling; published between window_start and ts"))
    st.update({"build": build, "cache_version": cv, "entry_client": entry, "last_check": iso(now),
               "last_error": None})
    if not prev:
        st["baseline_at"] = iso(now)
    _write_json(path, st)
    return events, None


# ----------------------------------------------------------------------------- (b) GitHub

# (path prefix, surface, user_facing, is_change). First match wins.
PATH_RULES = {
    MAIN_REPO: [
        ("rec_service/", "rec-engine", True, True),
        ("experiments/", "registry", False, False),   # the registry itself is not a change
        ("experiments_engine/", "experiments-engine", False, True),
        ("slack_qa/", "tulle-bot", False, True),
        ("admin_api/", "admin-dash", False, True),
        ("admin_web/", "admin-dash", False, True),
        ("dashboard.py", "admin-dash", False, True),
        ("experiments_tab.py", "admin-dash", False, True),
        ("cohorts.py", "admin-dash", False, True),
        ("theme.py", "admin-dash", False, True),
        ("extract_", "pipeline", False, True),
        ("ingest_batch.py", "pipeline", False, True),
        ("batch_worker.py", "pipeline", False, True),
        ("run_chunk.py", "pipeline", False, True),
        ("launch_parallel.py", "pipeline", False, True),
        ("tests/", "tests", False, False),
        (".claude/", "tooling", False, False),
    ],
    "marthv/tulletogether": [
        ("components/", "weweb:component", True, True),
        (".claude/", "tooling", False, False),
    ],
}
REPO_DEFAULTS = {
    MAIN_REPO: ("internal-cron", False),
    "marthv/tulletogether": ("tulletogether-repo", False),
    "marthv/tulle-search": ("search-api", True),
    "marthv/tulle-vendor-portal": ("vendor-portal", True),
    "marthv/feedback_bot": ("slackbot", False),
}
_DOC_RE = re.compile(r"(\.md|\.txt|^docs/|/docs/|LICENSE)$|^docs/", re.I)


def surfaces_for_paths(repo, paths):
    """-> (surfaces sorted, user_facing, is_change). A commit that only touches the registry,
    tests, tooling or docs is not a change."""
    rules = PATH_RULES.get(repo, [])
    default_surface, default_uf = REPO_DEFAULTS.get(repo, (repo.split("/")[-1], False))
    surfaces, uf, change = set(), False, False
    for p in paths or []:
        p = str(p).lstrip("/")
        hit = None
        for prefix, surface, user_facing, is_change in rules:
            if p.startswith(prefix):
                hit = (surface, user_facing, is_change)
                break
        if hit is None:
            hit = ("docs", False, False) if _DOC_RE.search(p) else (default_surface, default_uf, True)
        if hit[2]:
            surfaces.add(hit[0])
            uf = uf or hit[1]
            change = True
    return sorted(surfaces), uf, change


def github_repos():
    repos = [MAIN_REPO]
    if os.environ.get("GITHUB_READ_TOKEN"):
        for r in os.environ.get("EXP_GITHUB_REPOS", "").split(","):
            r = r.strip()
            if r and r not in repos:
                repos.append(r)
    return repos


def _gh_headers(etag=None):
    h = dict(UA)
    h["Accept"] = "application/vnd.github+json"
    tok = os.environ.get("GITHUB_READ_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    if etag:
        h["If-None-Match"] = etag
    return h


def poll_github(http, sdir, now=None, max_calls=MAX_GITHUB_CALLS, repos=None, branch="main"):
    """Return (events, notes, calls_used). One list call per repo (conditional), then commit
    detail calls for unseen commits while the budget lasts; the rest wait in ``pending`` for
    the next run. First run per repo: only commits from the last 48 h are queued."""
    now = now or _utcnow()
    path = os.path.join(sdir, "github_state.json")
    st = _read_json(path, {})
    events, notes, calls = [], [], 0
    repos = repos or github_repos()
    for repo in repos:
        rs = st.setdefault(repo, {"etag": None, "head": None, "pending": []})
        if calls < max_calls:
            calls += 1
            try:
                r = http.get(f"https://api.github.com/repos/{repo}/commits",
                             params={"sha": branch, "per_page": 30},
                             headers=_gh_headers(rs.get("etag")), timeout=20)
                code = getattr(r, "status_code", 0)
                if code == 200:
                    commits = r.json() or []
                    rs["etag"] = (getattr(r, "headers", {}) or {}).get("ETag") or rs.get("etag")
                    new = []
                    for c in commits:
                        sha = c.get("sha")
                        if not sha or sha == rs.get("head"):
                            break
                        cts = parse_ts(((c.get("commit") or {}).get("committer") or {}).get("date"))
                        if rs.get("head") is None and cts and (now - cts).total_seconds() > FIRST_RUN_LOOKBACK_H * 3600:
                            break
                        new.append(sha)
                    if commits and commits[0].get("sha"):
                        rs["head"] = commits[0]["sha"]
                    pend = [s for s in new if s not in rs["pending"]] + rs["pending"]
                    if len(pend) > GITHUB_PENDING_CAP:
                        notes.append(f"github {repo}: {len(pend) - GITHUB_PENDING_CAP} older commits dropped (backlog cap)")
                    rs["pending"] = pend[:GITHUB_PENDING_CAP]
                elif code == 304:
                    pass
                elif code in (401, 403, 404, 429):
                    notes.append(f"github {repo}: unavailable (HTTP {code})")
                else:
                    notes.append(f"github {repo}: HTTP {code}")
            except Exception as exc:
                notes.append(f"github {repo}: {type(exc).__name__}: {exc}")
        # commit details, newest-first pending -> oldest first so a backlog drains in order
        while rs.get("pending") and calls < max_calls:
            sha = rs["pending"][-1]
            calls += 1
            try:
                r = http.get(f"https://api.github.com/repos/{repo}/commits/{sha}",
                             headers=_gh_headers(), timeout=20)
                code = getattr(r, "status_code", 0)
                if code != 200:
                    notes.append(f"github {repo}@{sha[:7]}: HTTP {code}")
                    break
                d = r.json() or {}
            except Exception as exc:
                notes.append(f"github {repo}@{sha[:7]}: {type(exc).__name__}: {exc}")
                break
            rs["pending"].pop()
            files = [f.get("filename") for f in d.get("files") or [] if f.get("filename")]
            surfaces, uf, change = surfaces_for_paths(repo, files)
            if not change:
                continue
            cm = d.get("commit") or {}
            msg = (cm.get("message") or "").strip()
            ts = (cm.get("committer") or {}).get("date") or (cm.get("author") or {}).get("date")
            events.append(event(ts, "github", sha[:7], f"{repo.split('/')[-1]}: {msg.splitlines()[0] if msg else sha[:7]}",
                                surfaces, url=d.get("html_url") or f"https://github.com/{repo}/commit/{sha}",
                                user_facing=uf, repo=repo, sha=sha, files=len(files)))
        if rs.get("pending"):
            notes.append(f"github {repo}: {len(rs['pending'])} commits waiting for the next run (call budget)")
    _write_json(path, st)
    return events, notes, calls


# ----------------------------------------------------------------------------- (c) Railway

RAILWAY_FILE = "railway_events.jsonl"
DEPLOY_OK = ("SUCCESS", "DEPLOYED")


def normalise_railway_payload(payload, received_at=None):
    """Railway project-webhook JSON -> {service, commit, message, status, ts, ...} or None when
    it is not a deploy event. Handles the current format (type "Deployment.<status>", details,
    resource) and the legacy one (type "DEPLOY", status, deployment.meta)."""
    if not isinstance(payload, dict):
        return None
    typ = str(payload.get("type") or "")
    if typ.lower().startswith("deployment."):
        det = payload.get("details") or {}
        res = payload.get("resource") or {}
        status = str(det.get("status") or typ.split(".", 1)[1]).upper()
        return {"service": (res.get("service") or {}).get("name"),
                "service_id": (res.get("service") or {}).get("id"),
                "environment": (res.get("environment") or {}).get("name"),
                "project": (res.get("project") or {}).get("name"),
                "deployment_id": (res.get("deployment") or {}).get("id") or det.get("id"),
                "commit": det.get("commitHash"), "message": (det.get("commitMessage") or "")[:300],
                "branch": det.get("branch"), "status": status, "type": typ,
                "ts": payload.get("timestamp") or received_at}
    if typ.upper() == "DEPLOY":
        dep = payload.get("deployment") or {}
        meta = dep.get("meta") or {}
        return {"service": (payload.get("service") or {}).get("name"),
                "service_id": (payload.get("service") or {}).get("id"),
                "environment": (payload.get("environment") or {}).get("name"),
                "project": (payload.get("project") or {}).get("name"),
                "deployment_id": dep.get("id"),
                "commit": meta.get("commitHash"), "message": (meta.get("commitMessage") or "")[:300],
                "branch": meta.get("branch"), "status": str(payload.get("status") or "").upper(),
                "type": typ, "ts": payload.get("timestamp") or received_at}
    return None


def store_railway_event(sdir, rec, keep=2000):
    """Append a normalised deploy event (dedup on deployment_id + status). Returns True if new."""
    path = os.path.join(sdir, RAILWAY_FILE)
    rows = _read_jsonl(path)
    key = (rec.get("deployment_id"), rec.get("status"))
    if rec.get("deployment_id") and any((r.get("deployment_id"), r.get("status")) == key for r in rows):
        return False
    rows.append(rec)
    _write_jsonl(path, rows[-keep:])
    return True


def railway_events(sdir):
    """Deploy-success events as change-feed events. A deploy whose commit equals the service's
    previous successful deploy is labelled a variable change / redeploy (section 8)."""
    rows = sorted(_read_jsonl(os.path.join(sdir, RAILWAY_FILE)), key=lambda r: str(r.get("ts")))
    last_commit, out = {}, []
    for r in rows:
        if str(r.get("status")).upper() not in DEPLOY_OK:
            continue
        if r.get("environment") and str(r.get("environment")).lower() != "production":
            continue
        svc = r.get("service") or "?"
        commit = r.get("commit") or ""
        same = bool(commit) and last_commit.get(svc) == commit
        last_commit[svc] = commit or last_commit.get(svc)
        kind = "redeploy of the same commit (likely a variable change)" if same else "deploy"
        msg = (r.get("message") or "").splitlines()[0] if r.get("message") else ""
        uf = svc in USER_FACING_SERVICES
        out.append(event(r.get("ts"), "railway", commit[:7] or (r.get("deployment_id") or "")[:8],
                         f"{svc} {kind}" + (f": {msg}" if msg and not same else ""),
                         [svc], url=None, user_facing=uf,
                         service=svc, deployment_id=r.get("deployment_id"), commit=commit,
                         variable_change=same))
    return out


# ----------------------------------------------------------------------------- (d) Xano audit

_XANO_READONLY_RE = re.compile(r"\b(login|logout|auth|viewed|export|download|read)\b", re.I)


def poll_xano_audit(http, sdir, now=None, token=None, per_page=50):
    """Return (events, note, calls). One GET. New rows since the last seen one; on the first
    run only rows from the last 48 h. 401/403/404 -> 'unavailable' note (scope missing or the
    path is wrong; UNVERIFIED which until it runs on Railway)."""
    now = now or _utcnow()
    token = token if token is not None else os.environ.get("XANO_METADATA_TOKEN", "")
    if not token:
        return [], "xano audit: unavailable (no XANO_METADATA_TOKEN)", 0
    path = os.path.join(sdir, "xano_state.json")
    st = _read_json(path, {})
    try:
        r = http.get(XANO_META_BASE + XANO_AUDIT_PATH, params={"page": 1, "per_page": per_page},
                     headers={**UA, "Authorization": f"Bearer {token}", "Accept": "application/json"},
                     timeout=30)
        code = getattr(r, "status_code", 0)
        if code in (401, 403, 404):
            return [], f"xano audit: unavailable (HTTP {code})", 1
        if code != 200:
            return [], f"xano audit: HTTP {code}", 1
        body = r.json()
    except Exception as exc:
        return [], f"xano audit: {type(exc).__name__}: {exc}", 1
    rows = body.get("items") if isinstance(body, dict) else body
    rows = [x for x in rows or [] if isinstance(x, dict)]
    last_ts = parse_ts(st.get("last_ts"))
    seen = set(st.get("seen_ids") or [])
    cutoff = last_ts or (now - _dt.timedelta(hours=FIRST_RUN_LOOKBACK_H))
    events, newest = [], last_ts
    for x in rows:
        ts = parse_ts(x.get("created_at") or x.get("timestamp"))
        rid = str(x.get("id") or f"{x.get('created_at')}:{x.get('msg')}")
        if ts is None or ts < cutoff or rid in seen:
            continue
        msg = x.get("msg") or x.get("message") or " ".join(
            str(x.get(k)) for k in ("action", "object_type", "object_name") if x.get(k)) or "audit row"
        user = (x.get("user") or {}).get("email") if isinstance(x.get("user"), dict) else x.get("user_email")
        uf = not _XANO_READONLY_RE.search(str(msg))
        events.append(event(ts, "xano", f"audit:{rid}", f"Xano: {msg}", ["xano"], url=None,
                            user_facing=uf, actor=user))
        seen.add(rid)
        if newest is None or ts > newest:
            newest = ts
    st["last_ts"] = iso(newest) if newest else st.get("last_ts")
    st["seen_ids"] = sorted(seen)[-500:]
    _write_json(path, st)
    return events, None, 1


# ----------------------------------------------------------------------------- store + orchestration

EVENTS_FILE = "events.jsonl"


def _event_key(e):
    return (e.get("source"), e.get("ref"), e.get("ts") if e.get("source") == "railway" else None)


def merge_events(sdir, new_events, now=None):
    """Add new events to the volume feed (dedup, keep 90 days). Returns the full feed, newest first."""
    now = now or _utcnow()
    path = os.path.join(sdir, EVENTS_FILE)
    rows = _read_jsonl(path)
    keys = {_event_key(r) for r in rows}
    for e in new_events:
        if _event_key(e) not in keys:
            rows.append(e)
            keys.add(_event_key(e))
    cutoff = now - _dt.timedelta(days=EVENT_KEEP_DAYS)
    rows = [r for r in rows if (parse_ts(r.get("ts")) or now) >= cutoff]
    rows.sort(key=lambda r: str(r.get("ts")))
    _write_jsonl(path, rows)
    return list(reversed(rows))


def load_events(sdir):
    return list(reversed(sorted(_read_jsonl(os.path.join(sdir, EVENTS_FILE)), key=lambda r: str(r.get("ts")))))


def collect(http, sdir=None, now=None, include=("weweb", "github", "railway", "xano"), weweb_force=False):
    """Run the collectors, merge into the volume feed. Never raises.

    Returns {"new": [...], "events": [...], "notes": [...], "calls": {"github": n, "weweb": n, "xano": n}}.
    Railway events are re-derived from the webhook log on every call (cheap, local).
    """
    now = now or _utcnow()
    sdir = sdir or state_dir()
    new, notes, calls = [], [], {"github": 0, "weweb": 0, "xano": 0}
    if "weweb" in include:
        try:
            before = _read_json(os.path.join(sdir, "weweb_state.json"), {}).get("last_check")
            ev, note = poll_weweb(http, sdir, now, force=weweb_force)
            after = _read_json(os.path.join(sdir, "weweb_state.json"), {}).get("last_check")
            calls["weweb"] = int(before != after)
            new += ev
            if note:
                notes.append(note)
        except Exception as exc:
            notes.append(f"weweb: {type(exc).__name__}: {exc}")
    if "github" in include:
        try:
            ev, n, c = poll_github(http, sdir, now)
            new += ev
            notes += n
            calls["github"] = c
        except Exception as exc:
            notes.append(f"github: {type(exc).__name__}: {exc}")
    if "xano" in include:
        try:
            ev, note, c = poll_xano_audit(http, sdir, now)
            new += ev
            calls["xano"] = c
            if note:
                notes.append(note)
        except Exception as exc:
            notes.append(f"xano audit: {type(exc).__name__}: {exc}")
    if "railway" in include:
        try:
            new += railway_events(sdir)
        except Exception as exc:
            notes.append(f"railway: {type(exc).__name__}: {exc}")
    try:
        feed = merge_events(sdir, new, now)
    except Exception as exc:
        notes.append(f"feed store: {type(exc).__name__}: {exc}")
        feed = list(new)
    return {"new": new, "events": feed, "notes": notes, "calls": calls}


# ----------------------------------------------------------------------------- matching

def _family(ev):
    fam = set()
    for s in ev.get("surfaces") or []:
        s = str(s)
        if s.startswith("weweb"):
            fam.add("weweb")
        elif s.startswith("rec-engine"):
            fam.add("rec-engine")
        elif s.startswith("xano"):
            fam.add("xano")
        elif s.startswith("search"):
            fam.add("search")
        elif s.startswith("vendor-portal"):
            fam.add("vendor-portal")
    return fam


SOURCE_FAMILY = {"weweb": "weweb", "rec-engine": "rec-engine", "railway-var": "rec-engine",
                 "xano": "xano"}


def _entry_text(d, keys):
    parts = []
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        parts.append(json.dumps(v, default=str) if not isinstance(v, str) else v)
    return " ".join(parts)


def _ref_tokens(ev):
    """Strings that, found in a registry entry, prove it logs this event."""
    toks = set()
    ref = str(ev.get("ref") or "")
    if ev.get("source") == "xano" or ev.get("variable_change"):
        # audit row ids are never written into the registry; a variable-flip redeploy reuses an
        # old commit, so the commit naming an earlier change must not cover it
        return toks
    if ref:
        toks.add(ref)
    for k in ("sha", "commit"):
        v = str(ev.get(k) or "")
        if len(v) >= 7:
            toks.add(v[:7])
    return {t for t in toks if len(t) >= 4}


def _mentions(text, token):
    return re.search(r"(?<![0-9A-Za-z])" + re.escape(token) + r"(?![0-9A-Za-z])", text, re.I) is not None


def _keyword_families(text):
    fam = set()
    for f, pats in FAMILY_KEYWORDS.items():
        if any(re.search(p, text, re.I) for p in pats):
            fam.add(f)
    return fam


def _entry_dates(d, keys):
    out = []
    for k in keys:
        t = parse_ts(d.get(k))
        if t:
            out.append(t)
    return out


def match_event(ev, registry, window_h=MATCH_WINDOW_H):
    """-> (matched: bool, how: str). An event is logged when either

    1. a ref token (commit short sha, WeWeb cacheVersion, deployment id) appears as a whole word
       in a ``changes[]`` entry's what/ref/links/dates_note or an experiment's links/what_changed/
       what/dates_note (any date); or
    2. a ``changes[]`` entry dated within +-24 h of the event (by UTC day, since most entries only
       carry a date) names the same surface family: its v2 ``source`` maps to the family, or (v1
       entries with no source) its text mentions it (e.g. "WeWeb", "rec-engine", "ep230"); or
    3. an experiment whose ``start_date`` is within +-24 h lists a surface in the same family.

    CHOICE: rule 2 needs the family to agree, so one logged change on a day does not silently
    cover every other change that day.
    """
    reg = registry if isinstance(registry, dict) else {}
    changes = [c for c in reg.get("changes") or [] if isinstance(c, dict)]
    exps = [e for e in reg.get("experiments") or [] if isinstance(e, dict)]
    toks = _ref_tokens(ev)
    fam = _family(ev)
    ts = parse_ts(ev.get("ts"))
    ch_keys = ("what", "ref", "links", "dates_note")
    ex_keys = ("links", "what_changed", "what", "dates_note", "ref")
    for c in changes:
        text = _entry_text(c, ch_keys)
        for t in toks:
            if _mentions(text, t):
                return True, f"changes[] {c.get('date')} mentions {t}"
    for e in exps:
        text = _entry_text(e, ex_keys)
        for t in toks:
            if _mentions(text, t):
                return True, f"experiment {e.get('id')} mentions {t}"
    if ts is None:
        return False, "no timestamp"
    lo_day = (ts - _dt.timedelta(hours=window_h)).date()
    hi_day = (ts + _dt.timedelta(hours=window_h)).date()

    def in_window(dts):
        return any(lo_day <= d.date() <= hi_day for d in dts)

    for c in changes:
        if not in_window(_entry_dates(c, ("date",))):
            continue
        src = str(c.get("source") or "").lower()
        cfam = {SOURCE_FAMILY[src]} if src in SOURCE_FAMILY else set()
        if not src:
            cfam = _keyword_families(_entry_text(c, ("what",)))
        if fam & cfam:
            return True, f"changes[] {c.get('date')} ({', '.join(sorted(fam & cfam))})"
    for e in exps:
        if not in_window(_entry_dates(e, ("start_date",))):
            continue
        efam = _family({"surfaces": e.get("surfaces") or []})
        if not efam and not e.get("surfaces"):
            efam = _keyword_families(_entry_text(e, ("what_changed", "what", "name")))
        if fam & efam:
            return True, f"experiment {e.get('id')} started {e.get('start_date')}"
    return False, "no changes[] or experiment entry within 24 h"


def match_unlogged(events, registry, window_h=MATCH_WINDOW_H):
    """Events touching a user-facing surface with no covering registry entry (section 8).
    Returned events are copies with ``unlogged: True`` and ``why``. A Railway deploy and the
    GitHub commit it built are collapsed to one (same short sha)."""
    out, seen_refs = [], set()
    for ev in sorted(events or [], key=lambda e: str(e.get("ts"))):
        if not isinstance(ev, dict) or not is_user_facing(ev):
            continue
        ok, how = match_event(ev, registry, window_h)
        if ok:
            continue
        dedup = (ev.get("commit") or ev.get("sha") or "")[:7] if ev.get("source") in ("github", "railway") else None
        if dedup and not ev.get("variable_change"):
            if dedup in seen_refs:
                continue
            seen_refs.add(dedup)
        out.append(dict(ev, unlogged=True, why=how))
    return list(reversed(out))
