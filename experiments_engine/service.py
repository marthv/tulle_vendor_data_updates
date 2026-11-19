"""experiments-engine HTTP service + scheduler (FRAMEWORK.md sections 5, 8, 9, 11).

Run:  uvicorn experiments_engine.service:app --host :: --port $PORT --workers 1
(one worker only: the scheduler thread lives in the process.)

Endpoints (all but /health and /hooks/railway need header ``X-Exp-Key`` == env ``EXP_API_KEY``):

  GET  /health                     liveness + snapshot age; no secrets, no auth
  GET  /snapshot?history=N         latest snapshot (pre-gzipped when the client accepts gzip; ETag).
                                   history=N (0-60, default 0) adds history_by_id: {exp_id: last N
                                   readout rows trimmed to date/p_beat/n_control/n_treatment/health};
                                   each N is its own variant with its own ETag
  POST /refresh?mode=full|registry&wait=<s>
                                   start a background run, or join the one already running.
                                   A full run within EXP_MIN_FULL_GAP_S (default 900) of the last
                                   full run gets 429 + last_full_run; mode=registry is never limited
  GET  /experiments/{id}/history?days=90
  GET  /changes?limit=200          change feed + unlogged changes against the current registry
  POST /hooks/railway?k=<secret>   Railway project webhook (k == env EXP_HOOK_SECRET); stores
                                   deploy events on the volume, ignores everything else

Scheduler (one background thread, no extra deps), all idempotent across restarts:

* start-up: load the last snapshot from the volume. A full refresh (reads Xano) runs only when
  it is older than 6 h (section 5). If it is fresh but the deployed registry changed (a registry
  push redeploys the service), the snapshot is rebuilt from the cached readouts: zero Xano calls.
* full refresh every 6 h after the last one; guardrail check after every run.
* WeWeb production publish poll every 30 min (one anonymous GET).
* daily digest 14:00-22:00 UTC, once per UTC day (``digest:YYYY-MM-DD``), only when actionable.
* weekly summary Mondays 14:00-22:00 UTC (``weekly:YYYY-Www``), only when something was decided.
* urgent guardrail DM once per breach (``guardrail:<exp>:<onset date>``).
  With DRY_RUN on, keys get a ``:dry`` suffix so flipping DRY_RUN=0 still sends that day's message.

Production safety (section 11): the only network writes are Slack DMs (alerts.send) and the
volume. Xano is read through xano.XanoReadOnly (GET/search only, paced, budgeted).
"""

import datetime as _dt
import gzip
import hashlib
import hmac
import json
import logging
import os
import tempfile
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from . import alerts, changefeed, history, snapshot

log = logging.getLogger("experiments_engine.service")

REPO_ROOT = Path(__file__).resolve().parents[1]
REFRESH_EVERY = _dt.timedelta(hours=6)
WEWEB_EVERY = _dt.timedelta(minutes=30)
DIGEST_HOURS = (14, 22)          # [start, end) UTC hour window for daily + weekly
UNLOGGED_DIGEST_DAYS = 14        # unlogged changes older than this stop appearing in the DM
SEND_MAX_ATTEMPTS = 3
SEND_RETRY_AFTER = _dt.timedelta(minutes=30)
HOOK_MAX_BYTES = 64 * 1024
DEFAULT_MIN_FULL_GAP_S = 900     # POST /refresh?mode=full is refused (429) this soon after a full run
SNAPSHOT_FILE = "snapshot_latest.json.gz"
MAX_INLINE_HISTORY = 60
HISTORY_FIELDS = ("date", "p_beat", "n_control", "n_treatment", "health")
BREACH_MAX_GAP_DAYS = 3          # days with no readout row inside one breach run (no full run that day)
VERSION = "1.0"


def _utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt):
    return dt.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def registry_path():
    return Path(os.environ.get("EXP_REGISTRY_PATH") or REPO_ROOT / "experiments" / "experiment_log.json")


def _atomic_write_bytes(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def default_metric_runner_factory(registry, now, data_dir):
    """Full run: every auto, live/paused experiment from Xano in one shared DataContext.
    Returns (runner(entry) -> readout, stats dict)."""
    from . import metrics, xano  # noqa: PLC0415 - lazy: keeps import side-effect free
    client = xano.XanoReadOnly()
    entries = [e for e in (registry.get("experiments") or []) if isinstance(e, dict)]
    readouts, _ctx = metrics.run_all(entries, client, now_ms=int(now.timestamp() * 1000),
                                     statuses=("live", "paused"), record_pdf_history=True,
                                     data_dir=data_dir)
    stats = dict(client.stats)
    stats["has_token"] = client.has_token

    def runner(entry):
        ro = readouts.get(entry.get("id"))
        return ro if isinstance(ro, dict) else {"error": "not computed (metric not auto or entry not live)",
                                                "computed_at": None}
    return runner, stats


def cached_runner(prev_snapshot):
    """Readouts from the previous snapshot, by experiment id (registry-only rebuild)."""
    cache = {r.get("id"): r.get("readout") for r in (prev_snapshot or {}).get("experiments") or []
             if isinstance(r, dict) and isinstance(r.get("readout"), dict)}

    def runner(entry):
        ro = cache.get(entry.get("id"))
        if ro is None:
            return {"error": "pending: computed at the next full refresh", "computed_at": None,
                    "cached": True}
        return dict(ro, cached=True)      # the last full run's readout: not a new failure
    return runner


class Engine:
    """Holds the latest snapshot, runs refreshes and the alert schedule.

    ``clock`` returns an aware UTC datetime; ``http`` is a requests-like module (change feed);
    ``metric_runner_factory(registry, now, data_dir) -> (runner, stats)``; ``sender(text)`` sends a DM.
    ``sync=True`` runs refreshes inline (tests).
    """

    def __init__(self, data_dir=None, clock=None, http=None, metric_runner_factory=None,
                 sender=None, sync=False, registry_file=None):
        self.data_dir = history.data_dir(data_dir)
        self.clock = clock or _utcnow
        self._http = http
        self.metric_runner_factory = metric_runner_factory or default_metric_runner_factory
        self.sender = sender or alerts.send
        self.sync = sync
        self.registry_file = Path(registry_file) if registry_file else None
        self.store = history.HistoryStore(self.data_dir)
        self.snapshot = None
        self.snapshot_gz = None
        self.snapshot_etag = None
        self.last_run = None
        self.last_full_started = None    # in-memory: covers failed full runs (they still read Xano)
        self.current_run = None
        self.runs = 0
        self.next_full_due = None
        self.next_weweb_due = None
        self._lock = threading.Lock()
        self._done = threading.Condition(self._lock)
        self._snap_lock = threading.RLock()   # serialises snapshot read-modify-write (poll vs refresh)
        self._variants = {}                   # (history N, base etag) -> (gz, etag)
        self._thread = None
        self._attempts = {}
        self._stop = threading.Event()
        self._sched = None
        self._started = False

    # -- paths / io --------------------------------------------------------------------------
    @property
    def http(self):
        if self._http is None:
            import requests  # noqa: PLC0415
            self._http = requests
        return self._http

    @property
    def snapshot_path(self):
        return os.path.join(self.data_dir, SNAPSHOT_FILE)

    @property
    def feed_dir(self):
        return os.path.join(self.data_dir, "changefeed")

    def registry_path(self):
        return self.registry_file or registry_path()

    def load_registry(self):
        raw = self.registry_path().read_bytes()
        reg = json.loads(raw.decode("utf-8"))
        if not isinstance(reg, dict):
            raise ValueError("registry is not a JSON object")
        return reg, hashlib.sha256(raw).hexdigest()

    def _set_snapshot(self, snap, persist=True):
        body = json.dumps(snap, separators=(",", ":"), default=str).encode("utf-8")
        gz = gzip.compress(body, compresslevel=6, mtime=0)
        with self._snap_lock:
            if persist:
                _atomic_write_bytes(self.snapshot_path, gz)
            self.snapshot, self.snapshot_gz = snap, gz
            self.snapshot_etag = '"' + hashlib.sha256(gz).hexdigest()[:32] + '"'
            self._variants = {}

    def snapshot_variant(self, n):
        """(gz, etag) for the snapshot with ``history_by_id`` of the last n rows (n >= 1)."""
        with self._snap_lock:
            snap, base = self.snapshot, self.snapshot_etag
            key = (n, base)
            if key in self._variants:
                return self._variants[key]
        ids = [r.get("id") for r in (snap or {}).get("experiments") or [] if isinstance(r, dict) and r.get("id")]
        by_id = {i: [] for i in ids}
        for r in self.store._read_all():      # one file read for every experiment
            if r.get("exp_id") in by_id:
                by_id[r["exp_id"]].append({k: r.get(k) for k in HISTORY_FIELDS})
        for i, rows in by_id.items():
            rows.sort(key=lambda x: str(x.get("date")))
            by_id[i] = rows[-n:]
        body = json.dumps(dict(snap, history_by_id=by_id), separators=(",", ":"), default=str).encode("utf-8")
        gz = gzip.compress(body, compresslevel=6, mtime=0)
        out = (gz, '"' + hashlib.sha256(gz).hexdigest()[:32] + "-h%d" % n + '"')
        with self._snap_lock:
            if self.snapshot_etag == base:
                self._variants[key] = out
        return out

    def load_persisted(self):
        try:
            with open(self.snapshot_path, "rb") as f:
                gz = f.read()
            snap = json.loads(gzip.decompress(gz).decode("utf-8"))
            if isinstance(snap, dict):
                self._set_snapshot(snap, persist=False)
                return True
        except FileNotFoundError:
            return False
        except Exception as exc:
            log.error("could not load the saved snapshot: %s", exc)
        return False

    def snapshot_time(self):
        if not self.snapshot:
            return None
        return changefeed.parse_ts(self.snapshot.get("generated_at"))

    def snapshot_age_s(self, now=None):
        t = self.snapshot_time()
        return None if t is None else max(0.0, ((now or self.clock()) - t).total_seconds())

    # -- start-up ------------------------------------------------------------------------------
    def startup(self):
        """Load the volume snapshot and decide whether a run is needed (section 5)."""
        now = self.clock()
        self.load_persisted()
        t = self.snapshot_time()
        if t is None or now - t >= REFRESH_EVERY:
            log.info("start-up: snapshot %s -> full refresh", "missing" if t is None else "older than 6 h")
            self.next_full_due = now
        else:
            self.next_full_due = t + REFRESH_EVERY
            # Always rescore from cached readouts on start-up (no Xano): a deploy changes either the
            # registry or the engine's own rules, and both should show at once, not 6 h later.
            try:
                self.load_registry()
                log.info("start-up: snapshot fresh -> rescore from cache (no Xano)")
                self.start_refresh("registry")
            except Exception as exc:
                log.error("start-up: registry unreadable: %s", exc)
        self.next_weweb_due = now
        self._started = True

    # -- refresh -------------------------------------------------------------------------------
    def is_running(self):
        return self.current_run is not None

    def start_refresh(self, mode="full"):
        """Start a run, or join the one already running. Returns (started, run_info)."""
        if mode not in ("full", "registry"):
            mode = "full"
        with self._lock:
            if self.current_run is not None:
                return False, dict(self.current_run)
            self.current_run = {"mode": mode, "started_at": _iso(self.clock()), "id": self.runs + 1}
            if mode == "full":
                self.last_full_started = self.clock()
            self.runs += 1
            info = dict(self.current_run)
        if self.sync:
            self._run(mode)
        else:
            self._thread = threading.Thread(target=self._run, args=(mode,), name="exp-refresh", daemon=True)
            self._thread.start()
        return True, info

    def last_full_run_at(self):
        """Latest of: the last full run started in this process, the persisted snapshot's data time."""
        times = [t for t in (self.last_full_started, self.snapshot_time()) if t is not None]
        return max(times) if times else None

    def full_refresh_blocked(self, now=None):
        """(blocked, last_full_run_at, retry_after_s): the on-demand full-refresh minimum gap."""
        try:
            gap = float(os.environ.get("EXP_MIN_FULL_GAP_S", DEFAULT_MIN_FULL_GAP_S))
        except ValueError:
            gap = DEFAULT_MIN_FULL_GAP_S
        last = self.last_full_run_at()
        if last is None or gap <= 0:
            return False, last, 0
        left = gap - ((now or self.clock()) - last).total_seconds()
        return left > 0, last, max(0, int(left + 0.999))

    def wait(self, timeout):
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._done:
            while self.current_run is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._done.wait(left)
        return True

    def _run(self, mode):
        t0 = time.monotonic()
        now = self.clock()
        result = {"mode": mode, "started_at": _iso(now), "ok": False, "error": None}
        try:
            snap = self.refresh(mode, now)
            result.update(ok=True, generated_at=snap.get("generated_at"),
                          errors=len(snap.get("errors") or []),
                          xano=(snap.get("engine") or {}).get("xano"))
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            log.error("refresh (%s) failed: %s\n%s", mode, exc, traceback.format_exc(limit=5))
        finally:
            result["finished_at"] = _iso(self.clock())
            result["duration_s"] = round(time.monotonic() - t0, 2)
            if mode == "full":
                # the next full run is 6 h after this attempt, success or not (no hot retry loop on Xano)
                self.next_full_due = now + REFRESH_EVERY
            with self._done:
                self.last_run = result
                self.current_run = None
                self._done.notify_all()
        return result

    def refresh(self, mode, now):
        """One run: registry -> metrics (full) or cache (registry) -> snapshot -> change feed ->
        history (full) -> persist -> guardrail alerts."""
        reg, sha = self.load_registry()
        if mode == "full":
            runner, xstats = self.metric_runner_factory(reg, now, self.data_dir)
            include = ("weweb", "github", "railway", "xano")
        else:
            runner, xstats = cached_runner(self.snapshot), {"requests": 0, "note": "registry-only rebuild"}
            include = ("railway",)
        snap = snapshot.build_snapshot(reg, runner, self.store, now.date())
        snap["generated_at"] = _iso(now)      # the run's clock (staleness + schedule use it)
        cf =changefeed.collect(self.http, self.feed_dir, now, include=include)
        if "weweb" in include:
            self.next_weweb_due = now + WEWEB_EVERY
        unlogged = changefeed.match_unlogged(cf["events"], reg)
        snap["change_feed"] = {"events": cf["events"][:200], "unlogged": unlogged,
                               "notes": cf["notes"], "calls": cf["calls"], "collected_at": _iso(now)}
        snap["engine"] = {"version": VERSION, "mode": mode, "registry_sha256": sha,
                          "registry_path": str(self.registry_path()), "xano": xstats,
                          "dry_run": alerts.dry_run(),
                          "source_snapshot": (self.snapshot or {}).get("generated_at") if mode != "full" else None}
        if mode != "full" and self.snapshot:
            # a cache rebuild keeps the data timestamp of the run that read Xano
            snap["generated_at"] = self.snapshot.get("generated_at") or snap["generated_at"]
            snap["rebuilt_at"] = _iso(now)
        if mode == "full":
            self.store.append_day(snap)
        self._set_snapshot(snap)
        self.check_guardrails(snap, now)
        return snap

    # -- change feed between runs ----------------------------------------------------------
    def poll_weweb(self, now):
        self.next_weweb_due = now + WEWEB_EVERY
        cf = changefeed.collect(self.http, self.feed_dir, now, include=("weweb", "railway"))
        if self.snapshot and cf["new"]:
            try:
                reg, _sha = self.load_registry()
                with self._snap_lock:          # a refresh can't land between the copy and the write
                    snap = dict(self.snapshot)
                    feed = dict(snap.get("change_feed") or {})
                    feed["events"] = cf["events"][:200]
                    feed["unlogged"] = changefeed.match_unlogged(cf["events"], reg)
                    feed["collected_at"] = _iso(now)
                    snap["change_feed"] = feed
                    self._set_snapshot(snap)
            except Exception as exc:
                log.error("weweb poll: could not update the snapshot feed: %s", exc)
        return cf

    def changes_view(self, limit=200):
        # fold in Railway webhook events received since the last run (local file, no network)
        cf = changefeed.collect(None, self.feed_dir, self.clock(), include=("railway",))
        events = cf["events"]
        try:
            reg, _ = self.load_registry()
            unlogged = changefeed.match_unlogged(events, reg)
        except Exception as exc:
            unlogged = []
            log.error("changes: registry unreadable: %s", exc)
        feed = (self.snapshot or {}).get("change_feed") or {}
        return {"events": events[:limit], "unlogged": unlogged, "notes": feed.get("notes") or [],
                "collected_at": feed.get("collected_at"), "total_events": len(events)}

    # -- alerts ----------------------------------------------------------------------------------
    def _key(self, base):
        return base + (":dry" if alerts.dry_run() else "")

    def _deliver(self, key, text, now):
        """Send once per key; up to 3 attempts 30 min apart, then give up (marked)."""
        if self.store.was_sent(key):
            return "already"
        n, last = self._attempts.get(key, (0, None))
        if last is not None and now - last < SEND_RETRY_AFTER:
            return "backoff"
        res = self.sender(text) or {}
        if res.get("ok"):
            self.store.mark_sent(key)
            self._attempts.pop(key, None)
            return "sent"
        n += 1
        self._attempts[key] = (n, now)
        if n >= SEND_MAX_ATTEMPTS:
            log.error("giving up on %s after %d attempts: %s", key, n, res.get("error"))
            self.store.mark_sent(key + ":failed")
            self.store.mark_sent(key)
            return "gave_up"
        return "failed"

    def _breach_onset(self, exp_id, today):
        """First day of the current unbroken run of guardrail_breach days in history."""
        try:
            rows = self.store.load(exp_id, days=60, today=today)
        except Exception:
            rows = []
        by_day = {str(r.get("date"))[:10]: r.get("health") for r in rows}
        onset = today
        d = today
        gap = 0
        while True:
            d = d - _dt.timedelta(days=1)
            h = by_day.get(d.isoformat())
            if h == "guardrail_breach":
                onset, gap = d, 0
            elif h is None and gap < BREACH_MAX_GAP_DAYS and d > today - _dt.timedelta(days=60):
                gap += 1               # no full run that day: the breach run is not broken
            else:
                break
        return onset

    def check_guardrails(self, snap, now):
        out = []
        today = now.date()
        for row in (snap or {}).get("experiments") or []:
            if not isinstance(row, dict) or row.get("health") != "guardrail_breach" or not row.get("id"):
                continue
            onset = self._breach_onset(row["id"], today)
            key = self._key(f"guardrail:{row['id']}:{onset.isoformat()}")
            out.append((key, self._deliver(key, alerts.format_guardrail(row), now)))
        return out

    def _recent_unlogged(self, now):
        feed = (self.snapshot or {}).get("change_feed") or {}
        lo = now - _dt.timedelta(days=UNLOGGED_DIGEST_DAYS)
        return [e for e in feed.get("unlogged") or []
                if (changefeed.parse_ts(e.get("ts")) or now) >= lo]

    def check_digest(self, now):
        if not (DIGEST_HOURS[0] <= now.hour < DIGEST_HOURS[1]) or not self.snapshot:
            return None
        if self.is_running():
            return "wait"          # send from the finished run, not a half-updated one
        t = self.snapshot_time()
        if t is None or now - t > _dt.timedelta(hours=12):
            return "stale_snapshot"
        key = self._key(f"digest:{now.date().isoformat()}")
        if self.store.was_sent(key):
            return "already"
        text = alerts.format_digest(self.snapshot, self._recent_unlogged(now))
        if text is None:
            self.store.mark_sent(key + ":quiet")
            self.store.mark_sent(key)
            log.info("digest %s: nothing actionable, not sent", key)
            return "quiet"
        return self._deliver(key, text, now)

    def check_weekly(self, now):
        if now.weekday() != 0 or not (DIGEST_HOURS[0] <= now.hour < DIGEST_HOURS[1]) or not self.snapshot:
            return None
        y, w, _ = now.isocalendar()
        key = self._key(f"weekly:{y}-W{w:02d}")
        if self.store.was_sent(key):
            return "already"
        text = alerts.format_weekly(self.snapshot, now.date())
        if text is None:
            self.store.mark_sent(key + ":quiet")
            self.store.mark_sent(key)
            return "quiet"
        return self._deliver(key, text, now)

    # -- scheduler -------------------------------------------------------------------------------
    def tick(self, now=None):
        """One scheduler step. Safe to call any time; every action is idempotent."""
        now = now or self.clock()
        if not self._started:
            self.startup()
        acted = {}
        if not self.is_running() and self.next_full_due is not None and now >= self.next_full_due:
            acted["refresh"] = self.start_refresh("full")[0]
        if not self.is_running() and self.next_weweb_due is not None and now >= self.next_weweb_due:
            try:
                acted["weweb"] = len(self.poll_weweb(now)["new"])
            except Exception as exc:
                log.error("weweb poll failed: %s", exc)
        try:
            acted["digest"] = self.check_digest(now)
            acted["weekly"] = self.check_weekly(now)
        except Exception as exc:
            log.error("alert check failed: %s\n%s", exc, traceback.format_exc(limit=3))
        return acted

    def start_scheduler(self, interval_s=30):
        if self._sched and self._sched.is_alive():
            return

        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    log.error("scheduler tick failed: %s", exc)
                self._stop.wait(interval_s)
        self._sched = threading.Thread(target=loop, name="exp-scheduler", daemon=True)
        self._sched.start()

    def stop_scheduler(self):
        self._stop.set()

    def health(self):
        now = self.clock()
        lr = self.last_run or {}
        age = self.snapshot_age_s(now)
        return {"ok": True, "service": "experiments-engine", "version": VERSION,
                "snapshot_generated_at": (self.snapshot or {}).get("generated_at"),
                "snapshot_age_s": round(age) if age is not None else None,
                "running": self.is_running(),
                "last_run": {"ok": lr.get("ok"), "mode": lr.get("mode"),
                             "finished_at": lr.get("finished_at")} if lr else None,
                "next_full_due": _iso(self.next_full_due) if self.next_full_due else None,
                "scheduler_alive": bool(self._sched and self._sched.is_alive()),
                "dry_run": alerts.dry_run(),
                "last_snapshot": self._health_summary()}

    def _health_summary(self):
        """Aggregates only (no row data, no error text): safe on the public /health."""
        snap = self.snapshot or {}
        try:
            x = (snap.get("engine") or {}).get("xano") or {}
            hc = {}
            for e in snap.get("experiments") or []:
                if isinstance(e, dict) and e.get("health"):
                    hc[e["health"]] = hc.get(e["health"], 0) + 1
            return {"summary": snap.get("summary"),
                    "xano_requests": x.get("requests"), "xano_budget": x.get("budget"),
                    "errors": [{"id": er.get("id"), "stage": er.get("stage")}
                               for er in (snap.get("errors") or [])[:20] if isinstance(er, dict)],
                    "health_counts": hc}
        except Exception:  # noqa: BLE001
            return None


# ======================================================================================= HTTP

def _private_host_ok(host):
    allowed = [h.strip().lower() for h in os.environ.get(
        "EXP_PRIVATE_HOSTS", ".railway.internal,localhost,127.0.0.1,[::1],testserver").split(",") if h.strip()]
    h = (host or "").lower()
    if h.startswith("["):
        h = h.split("]")[0] + "]"
    else:
        h = h.split(":")[0]
    return any(h == a or (a.startswith(".") and h.endswith(a)) for a in allowed)


PUBLIC_PATHS = ("/health", "/hooks/railway")


def create_app(engine=None, start_scheduler=None):
    from fastapi import FastAPI, HTTPException, Request  # noqa: PLC0415
    from fastapi.responses import JSONResponse, Response  # noqa: PLC0415

    eng = engine or Engine()
    if start_scheduler is None:
        start_scheduler = os.environ.get("EXP_DISABLE_SCHEDULER", "0") not in ("1", "true", "yes")

    @asynccontextmanager
    async def lifespan(_app):
        if start_scheduler:
            eng.start_scheduler()
        yield
        eng.stop_scheduler()

    app = FastAPI(title="experiments-engine", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = eng

    @app.middleware("http")
    async def private_only(request: Request, call_next):
        # Defence in depth if the service ever gets a public domain (needed for the Railway
        # webhook): through a public host only /health and /hooks/railway exist.
        if request.url.path not in PUBLIC_PATHS and not _private_host_ok(request.headers.get("host")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)

    def require_key(request: Request):
        expected = os.environ.get("EXP_API_KEY", "")
        if not expected:
            raise HTTPException(503, "EXP_API_KEY not configured")
        got = request.headers.get("x-exp-key", "")
        if not hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(401, "bad or missing X-Exp-Key")

    @app.get("/health")
    def health():
        return eng.health()

    @app.get("/snapshot")
    def get_snapshot(request: Request, history: int = 0):
        require_key(request)
        if eng.snapshot_gz is None:
            return JSONResponse({"status": "warming", "detail": "no snapshot yet; first run in progress"},
                                status_code=503, headers={"Retry-After": "60"})
        n = max(0, min(int(history or 0), MAX_INLINE_HISTORY))
        if n:
            gz, etag = eng.snapshot_variant(n)
        else:
            with eng._snap_lock:
                gz, etag = eng.snapshot_gz, eng.snapshot_etag
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        headers = {"ETag": etag, "Vary": "Accept-Encoding", "Cache-Control": "no-cache"}
        if "gzip" in request.headers.get("accept-encoding", "").lower():
            headers["Content-Encoding"] = "gzip"
            return Response(content=gz, media_type="application/json", headers=headers)
        return Response(content=gzip.decompress(gz), media_type="application/json", headers=headers)

    @app.post("/refresh")
    def post_refresh(request: Request, mode: str = "full", wait: float = 0):
        require_key(request)
        if mode != "registry" and not eng.is_running():
            blocked, last, retry = eng.full_refresh_blocked()
            if blocked:
                return JSONResponse({"status": "too_soon", "detail": "a full refresh ran recently; "
                                     "mode=registry rescoring is still allowed",
                                     "last_full_run": _iso(last), "retry_after_s": retry},
                                    status_code=429, headers={"Retry-After": str(retry)})
        started, info = eng.start_refresh(mode)
        status = "started" if started else "running"
        if wait and wait > 0:
            done = eng.wait(min(float(wait), 300.0))
            if done:
                return {"status": "finished", "joined": not started, "last_run": eng.last_run}
        return JSONResponse({"status": status, "joined": not started, "run": info}, status_code=202)

    @app.get("/experiments/{exp_id}/history")
    def get_history(request: Request, exp_id: str, days: int = 90):
        require_key(request)
        days = max(1, min(int(days), 365))
        rows = eng.store.load(exp_id, days=days, today=eng.clock().date())
        return {"id": exp_id, "days": days, "rows": rows}

    @app.get("/changes")
    def get_changes(request: Request, limit: int = 200):
        require_key(request)
        return eng.changes_view(max(1, min(int(limit), 1000)))

    @app.post("/hooks/railway")
    async def railway_hook(request: Request, k: str = ""):
        secret = os.environ.get("EXP_HOOK_SECRET", "")
        if not secret:
            raise HTTPException(503, "EXP_HOOK_SECRET not configured")
        if not hmac.compare_digest(k.encode("utf-8"), secret.encode("utf-8")):
            raise HTTPException(401, "bad hook secret")
        body = await request.body()
        if len(body) > HOOK_MAX_BYTES:
            raise HTTPException(413, "payload too large")
        try:
            payload = json.loads(body.decode("utf-8") or "null")
        except ValueError:
            raise HTTPException(400, "invalid JSON")
        rec = changefeed.normalise_railway_payload(payload, received_at=_iso(eng.clock()))
        if rec is None:
            return {"ok": True, "ignored": True}
        rec["received_at"] = _iso(eng.clock())
        stored = changefeed.store_railway_event(eng.feed_dir, rec)
        return {"ok": True, "stored": stored}

    return app


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def __getattr__(name):
    # ``app`` is built on first access (uvicorn imports it), so importing this module for tests
    # or tooling never needs fastapi or starts anything.
    if name == "app":
        a = create_app()
        globals()["app"] = a
        return a
    raise AttributeError(name)
