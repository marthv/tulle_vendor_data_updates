"""READ-ONLY Xano metadata client for Slack reports.

XANO_METADATA_TOKEN can write and delete anything in the workspace, so this module is deliberately narrow:
  * it only ever calls `session.get` (there is no code path that sends POST/PUT/PATCH/DELETE), and
  * every path must match READ_PATHS (table list, table schema, table content) or PermissionError is raised
    BEFORE any request is made.
tests/test_reports.py checks both (and greps this file for any other HTTP verb).

Same base/workspace as perf_digest.py and experiments_brief.py: XANO_META_BASE (default the Tulle instance's
api:meta), workspace XANO_WORKSPACE_ID (1). Content is paged at 500 rows/page.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

import requests

log = logging.getLogger("slack_qa.xano")

READ_PATHS = (
    re.compile(r"^/table$"),                      # list/search tables (find venue_lookup_log by name)
    re.compile(r"^/table/\d+/schema$"),           # column names
    re.compile(r"^/table/\d+/content$"),          # rows (GET only; paged)
)
PER_PAGE = 500


class ReadOnlyXano:
    def __init__(self, token: str | None = None, base: str | None = None, workspace: int | None = None,
                 session=None, max_pages: int = 200, cache_ttl: float = 600):
        self.token = token if token is not None else os.environ.get("XANO_METADATA_TOKEN", "")
        base = base or os.environ.get("XANO_META_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta")
        ws = workspace if workspace is not None else int(os.environ.get("XANO_WORKSPACE_ID", "1"))
        self.root = f"{base.rstrip('/')}/workspace/{ws}"
        self.http = session or requests.Session()
        self.max_pages = max_pages
        self.cache_ttl = cache_ttl
        self._cache: dict = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    # The ONLY network call in this module.
    def request(self, method: str, path: str, params: dict | None = None) -> dict:
        if method.upper() != "GET":
            raise PermissionError(f"read-only Xano client: {method} is not allowed")
        if not any(p.match(path) for p in READ_PATHS):
            raise PermissionError(f"read-only Xano client: path {path!r} is not an allowed read endpoint")
        if not self.token:
            raise RuntimeError("XANO_METADATA_TOKEN is not set")
        r = self.http.get(self.root + path, params=params or {}, timeout=60,
                          headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        if r.status_code != 200:
            raise RuntimeError(f"Xano GET {path} -> HTTP {r.status_code}")
        return r.json()

    def get(self, path: str, params: dict | None = None) -> dict:
        return self.request("GET", path, params)

    # --- helpers -------------------------------------------------------------------------------------
    def rows(self, table_id: int) -> tuple[list[dict], bool]:
        """All rows of a table (cached 10 min). Returns (rows, truncated_by_page_cap)."""
        key = ("rows", int(table_id))
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < self.cache_ttl:
                return hit[1]
        out: list[dict] = []
        truncated = False
        page = 1
        while True:
            d = self.get(f"/table/{int(table_id)}/content", {"page": page, "per_page": PER_PAGE})
            items = d.get("items", []) if isinstance(d, dict) else (d or [])
            out += items
            nxt = d.get("nextPage") if isinstance(d, dict) else None
            if not items or len(items) < PER_PAGE or (isinstance(d, dict) and "nextPage" in d and not nxt):
                break
            page = int(nxt) if nxt else page + 1
            if page > self.max_pages:
                truncated = True
                break
        val = (out, truncated)
        with self._lock:
            self._cache[key] = (time.time(), val)
        return val

    def find_table(self, name: str) -> int | None:
        d = self.get("/table", {"search": name, "page": 1, "per_page": 50})
        items = d.get("items", []) if isinstance(d, dict) else (d or [])
        for t in items:
            if str(t.get("name", "")).lower() == name.lower():
                return int(t["id"])
        return None
