"""Filtered READ of Xano table rows for the merge card (duplicate-PDF check, live listing read).

xano_ro.ReadOnlyXano is GET-only, and Xano's GET can only page whole tables (venue_pricing alone is ~131k rows, 262
pages). The metadata API's filtered read is a POST to /table/<id>/content/search: it returns rows and changes nothing.
It lives here, not in xano_ro.py, so that file keeps its "no verb but GET" test. This module has ONE network call and
it can only build that one path: the table id must be one of TABLES and the filter must be a short `<column>|in` list.
"""
from __future__ import annotations

import requests

# PDF files, vendors, extracted PDF data, venue pricing, floral quotes/items, venue PDF facts, service quotes/items
TABLES = (10, 11, 36, 37, 84, 85, 87, 92, 93)
COLUMNS = ("Vendor_ID", "VENDOR_ID", "PDF_ID")
PER_PAGE = 250
MAX_PAGES = 8              # 2,000 rows per table per check; more -> TooManyRows ("can't tell", never a partial compare)
TIMEOUT = 10


class TooManyRows(RuntimeError):
    pass


def search(xano, table_id: int, column: str, values: list[str]) -> list[dict]:
    """Every row of `table_id` whose `column` is one of `values`. Raises on any problem: the caller shows
    'unavailable'."""
    if table_id not in TABLES or column not in COLUMNS:
        raise PermissionError(f"xano_search: table {table_id!r} / column {column!r} is not allowed")
    vals = [v for v in dict.fromkeys(str(x).strip() for x in values) if v]
    if not vals:
        return []
    if len(vals) > 60:
        raise TooManyRows(f"{len(vals)} values")
    if not getattr(xano, "token", ""):
        raise RuntimeError("no Xano token")
    url = f"{xano.root}/table/{int(table_id)}/content/search"
    out: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        r = None
        for attempt in (1, 2):                 # Xano stalls now and then (2 of 10 checks on 11-22): one retry, it's a read
            try:
                r = xano.http.post(url, json={"page": page, "per_page": PER_PAGE, "search": {f"{column}|in": vals}},
                                   timeout=TIMEOUT, headers={"Authorization": f"Bearer {xano.token}",
                                                             "Accept": "application/json"})
                break
            except requests.Timeout:
                if attempt == 2:
                    raise
        if r.status_code != 200:
            raise RuntimeError(f"Xano search t{table_id} -> HTTP {r.status_code}")
        d = r.json()
        out += d.get("items") or []
        if not d.get("nextPage"):
            # the filter must really have applied: an ignored filter would return other vendors' rows
            want = set(vals)
            if any(str(i.get(column) or "").strip() not in want for i in out):
                raise RuntimeError(f"Xano search t{table_id}: the filter was not applied")
            return out
    raise TooManyRows(f"t{table_id}: more than {MAX_PAGES * PER_PAGE} rows")
