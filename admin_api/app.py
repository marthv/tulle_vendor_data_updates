"""
Tulle admin API — the data/actions half of the old Streamlit dashboard.

Served on Railway; the only client is the Cloudflare Pages app at admin.tulletogether.app,
whose /api/* proxy (admin_web/functions/api/[[path]].js) verifies the Cloudflare Access
login and then calls this service with X-Admin-Key. Requests without the key get 401, so
the Railway URL is useless on its own.

Run locally:  uvicorn admin_api.app:app --reload --port 8790   (from the repo root)
"""

import gzip
import hmac
import json
import os

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response

from . import config, health_data, jobs
from .cache import Snapshot
from .cohorts_data import build_snapshot as build_cohorts

app = FastAPI(title="Tulle admin API", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024)

SNAPSHOTS = {
    "cohorts": Snapshot("cohorts", build_cohorts, config.COHORTS_TTL),
}


@app.on_event("startup")
def _warm():
    if not config.EXPORT_SECRET:
        print("[admin_api] ANALYTICS_EXPORT_SECRET not set - snapshots will fail")
    for s in SNAPSHOTS.values():
        s.start_timer()


@app.middleware("http")
async def _require_key(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    key = request.headers.get("x-admin-key", "")
    if not config.ADMIN_API_KEY or not hmac.compare_digest(key, config.ADMIN_API_KEY):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


def _user(request: Request) -> str:
    # Set by the Pages proxy from the verified Access JWT, never by the browser.
    return request.headers.get("x-admin-user", "")


@app.get("/health")
def health():
    return {"ok": True, "snapshots": [s.status() for s in SNAPSHOTS.values()]}


@app.get("/api/me")
def me(request: Request):
    return {"email": _user(request), "snapshots": [s.status() for s in SNAPSHOTS.values()]}


_ENCODED = {}  # snapshot name -> (built_at, gzipped JSON bytes)


def _snapshot_response(name, refresh=0):
    """Serve a snapshot as pre-gzipped JSON, encoded once per build (re-encoding the
    1.5 MB cohorts payload per request cost ~1.2 s)."""
    snap = SNAPSHOTS[name]
    if refresh:
        snap.refresh_async()
    val = snap.get(wait_seconds=90)
    if val is None:
        return JSONResponse({"error": snap.error or "snapshot still building",
                             "status": snap.status()}, status_code=503)
    cached = _ENCODED.get(name)
    if not cached or cached[0] != snap.built_at:
        body = gzip.compress(json.dumps(val, separators=(",", ":")).encode(), 6)
        cached = _ENCODED[name] = (snap.built_at, body)
    return Response(cached[1], media_type="application/json",
                    headers={"content-encoding": "gzip", "x-snapshot-building": str(snap.building).lower()})


@app.get("/api/cohorts")
def cohorts(refresh: int = 0):
    return _snapshot_response("cohorts", refresh)


# ── On-demand jobs (Health tab; PDF Extraction later) ─────────────────────────────────────
# POST starts (or returns the one already running), GET polls. Body = the job's arguments.
JOB_KINDS = {
    "endpoint_health": (health_data.endpoint_checks, set()),
    "health_report": (health_data.daily_report, set()),
    "perf_digest": (health_data.perf, {"hours"}),
}


@app.post("/api/jobs/{kind}")
async def job_start(kind: str, request: Request):
    if kind not in JOB_KINDS:
        return JSONResponse({"error": f"unknown job {kind}"}, status_code=404)
    fn, allowed = JOB_KINDS[kind]
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - empty body is fine
        body = {}
    args = {k: v for k, v in (body or {}).items() if k in allowed}
    if "hours" in args:
        args["hours"] = max(1, min(72, int(args["hours"])))
    return jobs.start(kind, fn, args, by=_user(request))


@app.get("/api/jobs/{kind}")
def job_get(kind: str):
    if kind not in JOB_KINDS:
        return JSONResponse({"error": f"unknown job {kind}"}, status_code=404)
    return jobs.get(kind)


@app.get("/api/health/history")
def health_history(days: int = 30):
    try:
        return health_data.history(max(1, min(90, days)))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=503)


# ── 🧪 Experiments: the registry file in this repo (experiments/experiment_log.json). Read per request -
# it's tiny, and a push to main redeploys this service, so the page always shows the committed log.
_EXP_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments", "experiment_log.json")


@app.get("/api/experiments")
def experiments():
    try:
        with open(_EXP_LOG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        return JSONResponse({"error": f"Could not read experiments/experiment_log.json: {e}"}, status_code=500)
