"""Read-only client for the Xano metadata API (FRAMEWORK.md sections 5 and 11).

Only two kinds of call ever leave this module:

* ``GET  {base}/workspace/{ws}/table/{id}[/schema|/content|/content/{row}]``
* ``POST {base}/workspace/{ws}/table/{id}/content/search``   (a search is a read)

Anything else raises :class:`WriteRefused` *before* a request is built. This is an explicit
``raise``, not an ``assert``, so it still holds under ``python -O``.

Safety rails (section 11):

* pacing: at least ``pace`` seconds (default 0.3) between any two HTTP attempts;
* a per-run request budget (default 60, env ``EXP_XANO_BUDGET``). Every attempt, retries
  included, counts. The call that would exceed it raises :class:`BudgetExceeded` unsent;
* retries with exponential backoff on 429/502/503/504 and connection errors (max 3 retries,
  ``Retry-After`` honoured up to 30 s); timeout 30 s per attempt;
* every row is sanitised the moment it is decoded: password / token / jwt / session / hash /
  auth / secret fields are dropped, every e-mail address is dropped after being used once to
  compute a boolean ``is_internal``. Nothing upstream of :func:`sanitize_row` sees them.

Search trap (recon_metrics.md section 0, xano.py:203-207 in the old brief): a search dict with
TWO keys is silently ignored and the WHOLE table comes back. :meth:`XanoReadOnly.search` always
sends one key per dict (a list of one-key dicts for ranges) and always re-filters client-side,
so an ignored filter costs requests (bounded by the budget / ``max_pages``) but never changes
a number.

The token is read from ``XANO_METADATA_TOKEN`` and is never printed, logged or put in repr().
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time

DEFAULT_BASE = "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta"
DEFAULT_WORKSPACE = 1
DEFAULT_BUDGET = 60
DEFAULT_PACE_S = 0.3
DEFAULT_TIMEOUT_S = 30
MAX_RETRIES = 3
RETRY_STATUSES = (429, 502, 503, 504)
MAX_RETRY_AFTER_S = 30.0

# Internal accounts (experiments_brief.py:44-49, CLAUDE memory "Exclude internal accounts").
INTERNAL_IDS = frozenset({14998, 30614, 32636, 31619, 118, 5622, 34249, 33167, 185, 35757})

# Which field holds the couple id, per table. Table 1: the row id IS the couple. Table 16: the
# Stripe client reference (a string; empty for gifts). Everything else: user_id.
UID_FIELD = {1: "id", 16: "Client_Reference_ID"}

_SENSITIVE_NAME = re.compile(r"(password|passwd|token|jwt|session|hash|auth|secret|api_?key|magic|email"
                             r"|phone|first_?name|last_?name|full_?name|reset_?code|address|^stripe"
                             r"|^name$|^ip$|_ip$|^ip_)", re.IGNORECASE)
# The account's OWN e-mail columns: the only ones that may mark a row internal. Any other
# e-mail-valued column (referrer_email, invited_by, ...) is dropped but never sets is_internal.
OWN_EMAIL_FIELDS = {"email", "primary_email", "user_email"}
OWN_EMAIL_BY_TABLE = {16: {"user"}}     # table 16 keeps the buyer's e-mail in "User"
_EMAIL_VALUE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_OPS = ("=", "!=", ">", ">=", "<", "<=")

_GET_PATH = re.compile(r"^/workspace/\d+/table/\d+(/schema|/content(/\d+)?)?$")
_POST_PATH = re.compile(r"^/workspace/\d+/table/\d+/content/search$")


class XanoError(RuntimeError):
    pass


class WriteRefused(XanoError):
    """A method/path that is not one of the two allowed reads. Raised before any I/O."""


class BudgetExceeded(XanoError):
    """The per-run request budget is spent. The run must abort (section 11)."""


class IncompleteRead(XanoError):
    """Paging stopped at max_pages with pages still left: a number built on it would be wrong."""


# ---------------------------------------------------------------------------
# helpers shared with metrics.py
# ---------------------------------------------------------------------------

def to_int(v):
    try:
        s = str(v).strip()
        return int(float(s)) if s else 0
    except (TypeError, ValueError):
        return 0


def to_ms(v):
    """Xano timestamp -> epoch ms (int) or None. Accepts ms, seconds, numeric strings, ISO strings."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).strip()
        try:
            x = float(s)
        except ValueError:
            try:
                d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                return None
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone.utc)
            return int(d.timestamp() * 1000)
    if x <= 0:
        return None
    return int(x * 1000) if x < 1e11 else int(x)


def is_internal(uid=None, email=""):
    e = str(email or "").strip().lower()
    return (to_int(uid) in INTERNAL_IDS) or e.startswith("vivek.marthi") or e.endswith("@tulletogether.com")


def _scrub(v):
    """Recursively drop sensitive keys and e-mail-looking strings from nested dicts / lists."""
    if isinstance(v, dict):
        return {str(k): _scrub(x) for k, x in v.items()
                if not _SENSITIVE_NAME.search(str(k)) and not _is_email(x)}
    if isinstance(v, (list, tuple)):
        return [_scrub(x) for x in v if not _is_email(x)]
    return v


def _is_email(v):
    return isinstance(v, str) and bool(_EMAIL_VALUE.match(v.strip()))


def sanitize_row(row, table=None):
    """Drop sensitive fields (recursively) and compute ``is_internal``.

    Dropped: field names matching password/token/jwt/session/hash/auth/secret/api_key/magic/email/
    phone/first_name/last_name/full_name/name/reset_code/address/stripe*/ip, and every string value
    that looks like an e-mail address, at any depth. ``is_internal`` comes from the id list and the
    account's OWN e-mail column(s) only (email / primary_email / user_email; table 16 "User").
    """
    if not isinstance(row, dict):
        return row
    uid_field = UID_FIELD.get(table, "user_id")
    own = OWN_EMAIL_FIELDS | OWN_EMAIL_BY_TABLE.get(table, set())
    emails = [str(v).strip().lower() for k, v in row.items()
              if str(k).lower() in own and isinstance(v, str)]
    out = _scrub(row)
    out["is_internal"] = is_internal(row.get(uid_field)) or any(is_internal(None, e) for e in emails)
    return out


def _cmp(row_val, op, value):
    """Client-side re-filter. Numbers/timestamps compare numerically when both sides parse."""
    if op == "=":
        return row_val == value or (row_val is not None and str(row_val) == str(value))
    if op == "!=":
        return not _cmp(row_val, "=", value)
    if row_val is None or row_val == "":
        return False
    try:
        a, b = float(row_val), float(value)
    except (TypeError, ValueError):
        a, b = to_ms(row_val), to_ms(value)
        if a is None or b is None:
            a, b = str(row_val), str(value)
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]


def _default_transport(method, url, headers, params, body, timeout):
    import requests  # local import: tests never need it

    r = requests.request(method, url, headers=headers, params=params, json=body, timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        data = None
    return r.status_code, dict(r.headers or {}), data


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------

class XanoReadOnly:
    """Paced, budgeted, read-only metadata client. See module docstring."""

    def __init__(self, base=None, token=None, workspace=DEFAULT_WORKSPACE, budget=None,
                 pace=DEFAULT_PACE_S, timeout=DEFAULT_TIMEOUT_S, transport=None,
                 sleep=time.sleep, clock=time.monotonic):
        self.base = (base or os.environ.get("XANO_META_BASE") or DEFAULT_BASE).rstrip("/")
        self._token = token if token is not None else os.environ.get("XANO_METADATA_TOKEN", "")
        self.workspace = int(workspace)
        if budget is None:
            budget = to_int(os.environ.get("EXP_XANO_BUDGET")) or DEFAULT_BUDGET
        self.budget = int(budget)
        self.pace = max(float(pace), DEFAULT_PACE_S) if pace is not None else DEFAULT_PACE_S
        self.timeout = timeout
        self._transport = transport or _default_transport
        self._sleep = sleep
        self._clock = clock
        self._last = None
        self.requests = 0
        self.retries = 0
        self.rows = 0
        self.calls = []          # [(method, path, status)] for the run report; never holds bodies

    def __repr__(self):
        return "XanoReadOnly(base=%r, workspace=%d, requests=%d/%d)" % (
            self.base, self.workspace, self.requests, self.budget)

    @property
    def has_token(self):
        return bool(self._token)

    @property
    def stats(self):
        return {"requests": self.requests, "retries": self.retries, "budget": self.budget,
                "remaining": self.budget - self.requests, "rows": self.rows}

    # -- guard -----------------------------------------------------------------------------
    @staticmethod
    def check_allowed(method, path):
        m = str(method or "").upper()
        p = str(path or "")
        if ".." in p or "?" in p or "#" in p:
            raise WriteRefused("refused: odd path %r" % p)
        if m == "GET" and _GET_PATH.fullmatch(p):
            return m
        if m == "POST" and _POST_PATH.fullmatch(p):
            return m
        raise WriteRefused("refused: %s %s is not a read this client allows "
                           "(only GET table/content and POST table/content/search)" % (m, p))

    # -- transport -------------------------------------------------------------------------
    def _pace(self):
        if self._last is not None:
            wait = self.pace - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _request(self, method, path, params=None, body=None):
        method = self.check_allowed(method, path)          # before anything else
        if not self._token:
            raise XanoError("XANO_METADATA_TOKEN is not set")
        url = self.base + path
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        attempt = 0
        while True:
            if self.requests >= self.budget:
                raise BudgetExceeded("Xano request budget of %d spent; aborting the run" % self.budget)
            self._pace()
            self.requests += 1
            try:
                status, rheaders, data = self._transport(method, url, headers, params, body, self.timeout)
            except Exception as exc:  # connection error / timeout
                status, rheaders, data = None, {}, None
                err = exc
            else:
                err = None
            self.calls.append((method, path, status))
            if status is not None and 200 <= status < 300:
                return data
            retryable = status in RETRY_STATUSES or status is None
            if not retryable or attempt >= MAX_RETRIES:
                if status is None:
                    raise XanoError("Xano %s %s failed: %s" % (method, path, type(err).__name__))
                raise XanoError("Xano %s %s -> HTTP %s" % (method, path, status))
            delay = 2.0 ** attempt
            ra = (rheaders or {}).get("Retry-After") or (rheaders or {}).get("retry-after")
            try:
                if ra is not None:
                    delay = min(max(float(ra), delay), MAX_RETRY_AFTER_S)
            except (TypeError, ValueError):
                pass
            attempt += 1
            self.retries += 1
            self._sleep(delay)

    # -- reads -----------------------------------------------------------------------------
    def _table_path(self, table, suffix=""):
        return "/workspace/%d/table/%d%s" % (self.workspace, int(table), suffix)

    def get_row(self, table, row_id):
        data = self._request("GET", self._table_path(table, "/content/%d" % int(row_id)))
        if not isinstance(data, dict):
            return None
        self.rows += 1
        return sanitize_row(data, table)

    def search(self, table, filter_key=None, op="=", value=None, sort=None, per_page=500, max_pages=20,
               filters=None):
        """POST .../content/search, paged, sanitised and re-filtered client-side.

        One filter: ``filter_key`` / ``op`` / ``value``. Several (ANDed): ``filters`` =
        [(key, op, value), ...], sent as a list of one-key dicts (never one dict with two keys).
        Returns a list of sanitised rows. Raises IncompleteRead if pages remain after max_pages.
        """
        conds = list(filters or [])
        if filter_key is not None:
            conds.insert(0, (filter_key, op, value))
        clauses = []
        for key, cop, val in conds:
            if cop not in _OPS:
                raise ValueError("unsupported op %r" % cop)
            if "|" in str(val):
                raise ValueError("a '|' inside a search value gives a 400")
            clauses.append({(key if cop == "=" else "%s|%s" % (key, cop)): val})
        search = clauses[0] if len(clauses) == 1 else clauses   # never a two-key dict
        out = []
        page = 1
        path = self._table_path(table, "/content/search")
        while True:
            body = {"page": page, "per_page": int(per_page)}
            if clauses:
                body["search"] = search
            body["sort"] = sort if sort is not None else {"id": "asc"}
            data = self._request("POST", path, body=body) or {}
            items = data.get("items") if isinstance(data, dict) else None
            for raw in items or []:
                self.rows += 1
                if not all(_cmp(raw.get(k) if isinstance(raw, dict) else None, o, v) for k, o, v in conds):
                    continue
                out.append(sanitize_row(raw, table))
            nxt = data.get("nextPage") if isinstance(data, dict) else None
            if not nxt or not items:
                return out
            if page >= max_pages:
                raise IncompleteRead("table %s: still pages left after %d pages of %d (filter ignored?)"
                                     % (table, max_pages, per_page))
            page = int(nxt) if str(nxt).isdigit() else page + 1


def describe_call_log(client):
    """Request log without bodies or headers (safe to print)."""
    return json.dumps([{"method": m, "path": p, "status": s} for m, p, s in client.calls])
