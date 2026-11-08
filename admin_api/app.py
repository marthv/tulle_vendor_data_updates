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

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response

from . import config
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
