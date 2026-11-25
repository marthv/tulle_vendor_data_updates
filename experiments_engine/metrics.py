"""Metric definitions for the experiment engine (FRAMEWORK.md sections 2, 4, 5).

``METRICS = {key: fn(ctx, params, window) -> readout}``. Every metric reads Xano only through a
shared :class:`DataContext`, which loads each table view ONCE per run, from the earliest time any
experiment in the run needs (incremental range filter, page size 500), so N experiments cost about
the same ~15 requests as one. Definitions follow scratchpad recon_metrics.md section 2.

Readout shapes (consumed by snapshot.py / health.py)::

    rate       {"arms": {"control": {"n", "conv"}, "treatment": {"n", "conv"}}, "pending": {...}}
    benchmark  {"arms": {"treatment": {"n", "conv"}}, "benchmark": {"n", "conv"}}
    rollout    {"arms": {"treatment": {"n", "conv"}}}
    count      {"count": {"pre": {"count", "days"}, "post": {"count", "days"}}}
    + "exposure_by_day": {"YYYY-MM-DD": n | {"control": n, "treatment": n}},
      "computed_at", "definition" (one line, plain English, with the cohort window),
      "caveats": [...], "error": None | str

Rules applied everywhere:

* Internal accounts are dropped: ids in ``xano.INTERNAL_IDS`` plus any e-mail starting
  ``vivek.marthi`` or ending ``@tulletogether.com`` (the client turns e-mails into ``is_internal``
  and drops them; tables without an e-mail are joined to the loaded table-1 rows for that flag).
* A cohort unit whose follow-up window has not elapsed is never counted; it is reported under
  ``pending``.
* Assignment (section 2): ``pre_post`` (cohort windows), ``split`` (``user_id_parity`` or
  ``hash_pct``, mirroring rec_service exactly, with forced ids), ``benchmark`` and ``rollout``.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import tempfile

from . import readlock as _rl
from . import xano as _x

DAY = 86400000
HOUR = 3600000

PLANNER_USE_KINDS = ("opening", "refine", "lookup", "more")       # recon finding 6 whitelist
PDF_OK = ("free", "paid")
FREE_GATE_STATUSES = ("blocked_free_limit", "feedback_required")  # the paywall-type Planner blocks
TABLE_USER, TABLE_PAY, TABLE_USAGE, TABLE_NPS = 1, 16, 79, 77

# hash variants used by shipped code (recon section 3). plan.py takes the first 8 hex digits;
# railway_search.py and whole_wedding.py take the whole digest. Both format salt + ":" + uid.
DEFAULT_PRE_DAYS = 14
SPLIT_RULES = ("user_id_parity", "hash_pct")
KNOWN_SALT_DIGITS = {"plan-2026-11": 8, "railsearch": "full", "wholewedding": "full"}


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------

class UnsupportedParam(ValueError):
    """A registry param this metric can't honour: report it instead of computing something else."""


def _utcnow_ms():
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp() * 1000)


def day_of(ms):
    return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%d")


def day_start(ms):
    return (int(ms) // DAY) * DAY


def parse_ms(v, end_of_day=False):
    """'2026-11-09' (00:00 UTC, or next midnight if end_of_day) / ISO / epoch -> ms."""
    if v is None or v == "":
        return None
    s = str(v).strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            d = _dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None
        ms = int(d.timestamp() * 1000)
        return ms + DAY if end_of_day else ms
    return _x.to_ms(v)


def _iso(ms):
    return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# assignment
# ---------------------------------------------------------------------------

def hash_bucket(salt, uid, digits=8):
    """0-99. digits=8: plan.py (int(sha256(salt:uid)[:8], 16) % 100); digits='full':
    railway_search.py / whole_wedding.py (int(sha256(salt:uid), 16) % 100)."""
    h = hashlib.sha256(("%s:%s" % (salt, uid)).encode()).hexdigest()
    if digits not in (None, "full", 0):
        h = h[:int(digits)]
    return int(h, 16) % 100


def split_arm(uid, assignment):
    """'treatment' | 'control' | None (not eligible) for a split assignment."""
    a = assignment or {}
    uid = _x.to_int(uid)
    if not uid:
        return None
    if uid in {_x.to_int(i) for i in a.get("exclude_ids") or []}:
        return None
    if uid in {_x.to_int(i) for i in a.get("force_ids") or a.get("forced_ids") or []}:
        return "treatment"
    rule = a.get("rule")
    if rule == "user_id_parity":
        even = uid % 2 == 0
        t = str(a.get("treatment") or "even").lower()
        return "treatment" if (even if t == "even" else not even) else "control"
    if rule == "hash_pct":
        salt = str(a.get("salt") or "").rstrip(":")    # registry may write "railsearch:" (prefix form)
        digits = a.get("hash_digits", KNOWN_SALT_DIGITS.get(salt, 8))
        pct = float(a.get("pct") if a.get("pct") is not None else 50)
        return "treatment" if hash_bucket(salt, uid, digits) < pct else "control"
    return None


def build_window(entry, now_ms=None):
    """Turn a registry entry's assignment + dates into the window every metric uses."""
    now_ms = now_ms or _utcnow_ms()
    a = entry.get("assignment") if isinstance(entry.get("assignment"), dict) else {}
    atype = a.get("type") or "rollout"
    start = parse_ms(a.get("post_start")) or parse_ms(entry.get("start_date")) or now_ms
    end = parse_ms(entry.get("end_date"), end_of_day=True) or now_ms
    end = min(end, now_ms)
    pre, pre_defaulted = None, False
    if atype == "pre_post":
        if isinstance(a.get("pre"), (list, tuple)) and len(a["pre"]) == 2:
            p0, p1 = parse_ms(a["pre"][0]), parse_ms(a["pre"][1], end_of_day=True)
            if p0 and p1:
                pre = (p0, p1)
        if pre is None:
            # registry left pre null: compare with the DEFAULT_PRE_DAYS before the start
            pre = (start - DEFAULT_PRE_DAYS * DAY, start)
            pre_defaulted = True
    earliest = min(start, pre[0]) if pre else start
    return {"type": atype, "assignment": a, "start_ms": start, "end_ms": end, "now_ms": now_ms,
            "pre": pre, "pre_defaulted": pre_defaulted, "earliest_ms": earliest, "id": entry.get("id")}


def _arm_for(unit, window):
    """Arm for a unit with an anchor time, or None (outside every window / not eligible)."""
    t = unit["anchor"]
    w = window
    if w["type"] == "pre_post":
        if w["pre"] and w["pre"][0] <= t < w["pre"][1]:
            return "control"
        if w["start_ms"] <= t < w["end_ms"]:
            return "treatment"
        return None
    if not (w["start_ms"] <= t < w["end_ms"]):
        return None
    if w["type"] == "split":
        return split_arm(unit["uid"], w["assignment"])
    return "treatment"     # benchmark / rollout


def _window_text(window):
    w = window
    post = "%s to %s" % (_iso(w["start_ms"]), _iso(w["end_ms"]))
    if w["type"] == "pre_post" and w["pre"]:
        return "before %s to %s vs after %s" % (day_of(w["pre"][0]), day_of(w["pre"][1] - 1), post)
    return post


def _window_caveats(window):
    if window.get("pre_defaulted"):
        return ["assignment.pre is not set in the registry: 'before' defaulted to the %d days before the "
                "start." % DEFAULT_PRE_DAYS]
    return []


def rate_readout(units, window, definition, caveats=None, extra=None):
    """units: [{"uid", "anchor", "conv", "pending"}] -> rate readout with arms / pending / exposure."""
    arms, pending, expo = {}, {}, {}
    more = {}
    if window.get("v2"):                   # EXP_TAB_V2: exposure rule, arm switchers, repeat accounts
        from . import v2_data
        units, more = v2_data.prepare_units(units, window)
    split = window["type"] == "split"
    for u in units:
        arm = _arm_for(u, window)
        if arm is None:
            continue
        d = day_of(u["anchor"])
        if split:
            expo.setdefault(d, {"control": 0, "treatment": 0})[arm] += 1
        else:
            expo[d] = expo.get(d, 0) + 1
        if u.get("pending"):
            pending[arm] = pending.get(arm, 0) + 1
            continue
        a = arms.setdefault(arm, {"n": 0, "conv": 0})
        a["n"] += 1
        a["conv"] += 1 if u["conv"] else 0
    want = ("control", "treatment") if window["type"] in ("pre_post", "split") else ("treatment",)
    for k in want:
        arms.setdefault(k, {"n": 0, "conv": 0})
    out = {"arms": arms, "pending": pending, "exposure_by_day": dict(sorted(expo.items())),
           "definition": "%s; cohort %s" % (definition, _window_text(window)),
           "caveats": list(caveats or []), "error": None}
    if window["type"] == "benchmark":
        b = window["assignment"].get("benchmark")
        out["benchmark"] = b
    if window["type"] == "pre_post":
        out["caveats"].append("Pre/post: evidence, not proof (changes in either window confound it).")
    out["caveats"] += _window_caveats(window)
    if extra:
        out.update(extra)
    if more:
        out.update(more)
    return out


def count_readout(events, window, definition, caveats=None, baseline_days=14):
    """events: [(ms, weight)] -> {"count": {"pre", "post"}} (Gamma-Poisson). Only complete UTC days
    after the start are counted in post; pre is the assignment's pre window, or the
    ``baseline_days`` before the start for split / benchmark / rollout."""
    w = window
    caveats = list(caveats or [])
    if w["type"] == "pre_post" and w["pre"]:
        p0, p1 = w["pre"]
    else:
        p0, p1 = w["start_ms"] - baseline_days * DAY, w["start_ms"]
    q0 = w["start_ms"]
    q1 = min(day_start(w["now_ms"]), w["end_ms"])
    pre_c = post_c = 0.0
    expo = {}
    for t, wt in events:
        if p0 <= t < p1:
            pre_c += wt
        elif q0 <= t < q1:
            post_c += wt
            d = day_of(t)
            expo[d] = round(expo.get(d, 0) + wt, 2)
    post_days = max(q1 - q0, 0) / DAY
    if post_days < 1:
        caveats.append("Less than one complete UTC day since the start: post is not readable yet.")
    caveats.append("Day-of-week mix is not adjusted.")
    if w["type"] == "pre_post":
        caveats.append("Pre/post: evidence, not proof (changes in either window confound it).")
    caveats += _window_caveats(w)
    return {"count": {"pre": {"count": round(pre_c, 2), "days": round((p1 - p0) / DAY, 4)},
                      "post": {"count": round(post_c, 2), "days": round(post_days, 4)}},
            "exposure_by_day": dict(sorted(expo.items())),
            "definition": "%s; pre %s to %s vs post %s to %s (complete days)" % (
                definition, _iso(p0), _iso(p1), _iso(q0), _iso(q1)),
            "caveats": caveats, "error": None}


# ---------------------------------------------------------------------------
# data context: each table view read once per run
# ---------------------------------------------------------------------------

class DataContext:
    """Lazily loads table views from Xano, each once per run, from the widest ``since`` needed.

    ``prepare(entries)`` pre-computes the earliest time per view over all experiments, so the
    first (and only) read of each view is already wide enough. If a metric later asks for an
    earlier time, only the gap is fetched (range filter) and merged.
    """

    VIEWS = {
        # name: (table, timestamp field, op used for the lower bound)
        "users": (TABLE_USER, "created_at", ">="),
        "offer_holders": (TABLE_USER, "paywall_offer_expires_at", ">"),
        "pricing_viewers": (TABLE_USER, "pricing_viewed_at", ">="),
        "payments": (TABLE_PAY, "Time_of_Payment", ">="),
        "usage": (TABLE_USAGE, "created_at", ">="),
    }

    def __init__(self, client, now_ms=None, record_pdf_history=False, data_dir=None):
        self.client = client
        self.now_ms = now_ms or _utcnow_ms()
        self._since = {}           # view -> planned earliest
        self._loaded = {}          # view -> (since, {row_id: row})
        self._nps = None
        self.record_pdf_history = record_pdf_history
        self.data_dir = data_dir
        self._pdf_hist = None
        self._loc_cache = None
        self.location_lookups = 0
        self.load_log = []         # [(view, since_ms, rows)]

    # planning ----------------------------------------------------------------------------
    def want(self, view, since_ms):
        if since_ms is None:
            return
        cur = self._since.get(view)
        self._since[view] = since_ms if cur is None else min(cur, since_ms)

    def prepare(self, entries):
        for e in entries:
            if _rl.enabled():
                from . import v2_data
                v2_data.want_views(self, e)
            if not is_auto(e):
                continue
            w = build_window(e, self.now_ms)
            specs = [((e.get("metric") or {}).get("key"), (e.get("metric") or {}).get("params") or {})]
            specs += [(g.get("key"), g.get("params") or {}) for g in e.get("guardrails") or []
                      if isinstance(g, dict)]
            for key, params in specs:
                if params.get("cohort") and not (key in GEO_METRICS and geo_cohort(params["cohort"])):
                    continue              # compute() refuses these: don't load data for them
                need = NEEDS.get(key)
                if need:
                    for view, since in need(params, w).items():
                        self.want(view, since)

    # loading -----------------------------------------------------------------------------
    def view(self, name, since_ms):
        table, field, op = self.VIEWS[name]
        since_ms = int(min(since_ms, self._since.get(name, since_ms)))
        have = self._loaded.get(name)
        if have is None:
            rows = self.client.search(table, field, op, since_ms)
            store = {}
            for r in rows:
                store[r.get("id", id(r))] = r
            self._loaded[name] = (since_ms, store)
            self.load_log.append((name, since_ms, len(rows)))
            if name == "users" and self.record_pdf_history:
                self.pdf_history().record(rows, self.now_ms)
        elif since_ms < have[0]:
            hi = have[0]
            rows = self.client.search(table, filters=[(field, op, since_ms), (field, "<", hi)])
            for r in rows:
                have[1][r.get("id", id(r))] = r
            self._loaded[name] = (since_ms, have[1])
            self.load_log.append((name + "+gap", since_ms, len(rows)))
        since, store = self._loaded[name]
        return [r for r in store.values() if _x.to_ms(r.get(field)) is not None
                and _x._cmp(_x.to_ms(r.get(field)), op, since_ms)]

    def nps(self):
        if self._nps is None:
            self._nps = self.client.search(TABLE_NPS, per_page=500)
            self.load_log.append(("nps", None, len(self._nps)))
        return self._nps

    def internal_uids(self):
        """Ids flagged internal by e-mail in any loaded table-1 view, plus the fixed list."""
        out = set(_x.INTERNAL_IDS)
        for name in ("users", "offer_holders", "pricing_viewers"):
            if name in self._loaded:
                for r in self._loaded[name][1].values():
                    if r.get("is_internal"):
                        out.add(_x.to_int(r.get("id")))
        return out

    def payments_by_uid(self, since_ms):
        """{uid: [(ms, amount, type)]} for attributable, non-internal payments since since_ms."""
        out = {}
        internal = self.internal_uids()
        for p in self.view("payments", since_ms):
            uid = _x.to_int(p.get("Client_Reference_ID"))
            t = _x.to_ms(p.get("Time_of_Payment"))
            if not uid or t is None or p.get("is_internal") or uid in internal:
                continue
            out.setdefault(uid, []).append((t, float(p.get("Amount") or 0), str(p.get("Type") or "")))
        for v in out.values():
            v.sort()
        return out

    def locations(self, uids):
        """{uid: tokens | None}. Order: table-1 rows already loaded this run, the volume cache,
        then up to LOCATION_LOOKUPS_PER_RUN get_row calls (None = not looked up yet)."""
        if self._loc_cache is None:
            self._loc_cache = LocationCache(self.data_dir)
        cache, out = self._loc_cache, {}
        loaded = {}
        for name in ("users", "offer_holders", "pricing_viewers"):
            for r in (self._loaded.get(name) or (None, {}))[1].values():
                if LOCATION_FIELD in r:
                    loaded[_x.to_int(r.get("id"))] = r.get(LOCATION_FIELD)
        for uid in sorted(set(uids)):
            if uid in loaded:
                toks = location_tokens(loaded[uid])
                cache.put(uid, toks, self.now_ms)
                out[uid] = toks
                continue
            hit = cache.get(uid, self.now_ms)
            if hit is not None:
                out[uid] = hit
                continue
            if self.location_lookups >= LOCATION_LOOKUPS_PER_RUN:
                out[uid] = None
                continue
            self.location_lookups += 1
            try:
                row = self.client.get_row(TABLE_USER, uid)   # BudgetExceeded propagates (run aborts)
            except _x.BudgetExceeded:
                raise
            except Exception:              # one failed lookup = unresolved, retried next run
                out[uid] = None
                continue
            toks = location_tokens((row or {}).get(LOCATION_FIELD))
            cache.put(uid, toks, self.now_ms)
            out[uid] = toks
        try:
            cache.save()
        except OSError:
            pass                       # read-only volume in tests: lookups simply repeat next run
        return out

    def pdf_history(self):
        if self._pdf_hist is None:
            self._pdf_hist = PdfFirstOpenHistory(self.data_dir)
        return self._pdf_hist


def _paid_between(pays, uid, a, b):
    return any(a <= t <= b for t, _amt, _ty in pays.get(uid, ()))


# ---------------------------------------------------------------------------
# pdf first-open history (closes the "no first-view timestamp" gap, recon section 2)
# ---------------------------------------------------------------------------

class PdfFirstOpenHistory:
    """Per-user ``total_pdf_views`` observations on the engine volume (``EXP_DATA_DIR``).

    File ``pdf_first_open.json``: {uid: {"created_at", "first_seen", "last_zero", "first_positive"}}
    (epoch ms of the engine runs that saw the user, saw 0 views, first saw > 0). Written from the
    table-1 rows the run already loaded: no extra Xano request. Entries older than 60 days after
    sign-up are pruned. Resolution is the run cadence (6 h).
    """

    FILE = "pdf_first_open.json"
    KEEP_DAYS = 60

    def __init__(self, data_dir=None):
        if data_dir is None:
            data_dir = os.environ.get("EXP_DATA_DIR") or "/data/experiments"
        self.path = os.path.join(data_dir, self.FILE)
        self._data = None

    def load(self):
        if self._data is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def record(self, users, now_ms):
        d = self.load()
        for u in users:
            uid = str(_x.to_int(u.get("id")))
            created = _x.to_ms(u.get("created_at"))
            if uid == "0" or created is None:
                continue
            rec = d.setdefault(uid, {"created_at": created, "first_seen": now_ms,
                                     "last_zero": None, "first_positive": None})
            views = _x.to_int(u.get("total_pdf_views"))
            if views > 0:
                if rec.get("first_positive") is None:
                    rec["first_positive"] = now_ms
            elif rec.get("first_positive") is None:
                rec["last_zero"] = now_ms
        cutoff = now_ms - self.KEEP_DAYS * DAY
        for k in [k for k, v in d.items() if (v.get("created_at") or 0) < cutoff]:
            del d[k]
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(self.path) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, separators=(",", ":"))
        os.replace(tmp, self.path)

    def within(self, uid, created_ms, days=1, slack_ms=6 * HOUR):
        """True/False when exact for this user, None when history doesn't cover their first day."""
        rec = self.load().get(str(_x.to_int(uid)))
        if not rec or rec.get("first_seen") is None:
            return None
        limit = created_ms + days * DAY
        if rec["first_seen"] > limit + slack_ms:
            return None       # first observed after their window closed: can't tell when they opened
        fp = rec.get("first_positive")
        if fp is not None and fp <= limit + slack_ms:
            return True
        lz = rec.get("last_zero")
        if lz is not None and lz >= limit:
            return False      # still 0 views at a run after their window closed
        return None



# ---------------------------------------------------------------------------
# geo cohorts (wedding location), same rule as the WeWeb price bindings / bnpl_watch.arm_for:
# Wedding_Location_Updated items, comma-packed parts split, trimmed, lower-cased.
# ---------------------------------------------------------------------------

LOCATION_FIELD = "Wedding_Location_Updated"
GEO_COHORTS = {
    # cohort -> (label, test(tokens))
    "texas_only": ("Texas-only couples", lambda t: t == {"texas"}),
    "texas": ("couples with Texas in their wedding location", lambda t: "texas" in t),
    "ca": ("California couples", lambda t: "california" in t),
    "california": ("California couples", lambda t: "california" in t),
    "ny": ("New York couples", lambda t: "new york" in t),
    "new_york": ("New York couples", lambda t: "new york" in t),
}
GEO_METRICS = ("purchases_per_day", "revenue_per_day")
LOCATION_LOOKUPS_PER_RUN = 30         # get_row calls per run for payers not already loaded/cached
LOCATION_MIN_COVERAGE = 0.95          # below this a geo readout is withheld, never guessed
LOCATION_TTL_DAYS = 30


def location_tokens(raw):
    items = raw if isinstance(raw, list) else ([raw] if raw else [])
    out = set()
    for it in items:
        for part in str(it if it is not None else "").split(","):
            t = part.strip().lower()
            if t:
                out.add(t)
    return out


def geo_cohort(name):
    return GEO_COHORTS.get(str(name or "").strip().lower())


class LocationCache:
    """{uid: {"loc": [tokens], "t": ms}} on the engine volume (``locations.json``). Locations are
    looked up once per couple (refreshed after 30 days), so after the first runs a geo readout
    costs no extra Xano requests. Holds lower-cased US-state/country words only, nothing else."""

    FILE = "locations.json"

    def __init__(self, data_dir=None):
        data_dir = data_dir or os.environ.get("EXP_DATA_DIR")
        self.path = os.path.join(data_dir, self.FILE) if data_dir else None   # None = in memory only
        self._data = None
        self.dirty = False

    def load(self):
        if self._data is None and self.path is None:
            self._data = {}
        if self._data is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def get(self, uid, now_ms):
        rec = self.load().get(str(uid))
        if not rec or (now_ms - (rec.get("t") or 0)) > LOCATION_TTL_DAYS * DAY:
            return None
        return set(rec.get("loc") or [])

    def put(self, uid, tokens, now_ms):
        self.load()[str(uid)] = {"loc": sorted(tokens), "t": now_ms}
        self.dirty = True

    def save(self):
        if not self.dirty or self.path is None:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(self.path) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, separators=(",", ":"))
        os.replace(tmp, self.path)
        self.dirty = False

# ---------------------------------------------------------------------------
# cohort builders
# ---------------------------------------------------------------------------

def _new_users(ctx, window, lookback=0):
    rows = ctx.view("users", window["earliest_ms"] - lookback)
    internal = ctx.internal_uids()
    out = []
    for u in rows:
        uid = _x.to_int(u.get("id"))
        t = _x.to_ms(u.get("created_at"))
        if not uid or t is None or u.get("is_internal") or uid in internal:
            continue
        out.append((uid, t, u))
    return out


def surface_allowed(value, allowed):
    """Table-79 ``surface`` ("planner", "bubble:/vendors/..") against an exposure rule's surfaces. A row with no
    surface (older component versions) is not proof of exposure, so it is left out."""
    s = str(value or "").strip().lower()
    for a in allowed or ():
        a = str(a).strip().lower()
        if s == a or (a and s.startswith(a) and not s[len(a)].isalnum()):
            return True
    return False


def _usage_rows(ctx, since_ms):
    internal = ctx.internal_uids()
    out = []
    surfaces = getattr(ctx, "exposure_surfaces", None)     # EXP_TAB_V2 only (None = every surface, as before)
    for r in ctx.view("usage", since_ms):
        uid = _x.to_int(r.get("user_id"))
        kind = str(r.get("kind") or "")
        t = _x.to_ms(r.get("created_at"))
        if not uid or uid in internal or t is None or kind.startswith("mcp_"):
            continue
        if surfaces and not surface_allowed(r.get("surface"), surfaces):
            continue
        out.append((uid, t, kind, str(r.get("status") or ""), r))
    out.sort(key=lambda x: x[1])
    return out


def _scan_from(window, params, default_days):
    return window["earliest_ms"] - int(params.get("lookback_days", default_days)) * DAY


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

PAY_SKEW_MS = HOUR   # a payment up to 1 h before created_at still counts (clock skew)


def m_new_paid_within_days(ctx, params, window):
    n_days = float(params.get("days", 1))
    follow = int(n_days * DAY)
    users = _new_users(ctx, window)
    pays = ctx.payments_by_uid(window["earliest_ms"] - PAY_SKEW_MS)
    units = [{"uid": uid, "anchor": t, "pending": t + follow > ctx.now_ms,
              "conv": _paid_between(pays, uid, t - PAY_SKEW_MS, t + follow)} for uid, t, _u in users]
    return rate_readout(units, window, "share of new accounts that pay within %g day(s) of sign-up" % n_days,
                        ["Any purchase type counts (1 week, 4 weeks, Forever); amounts include tax."])


def m_gate_hitters_paid(ctx, params, window):
    gate = str(params.get("gate", "pdf"))
    n_days = float(params.get("days", 1))
    follow = int(n_days * DAY)
    if gate not in ("pdf", "pdf_limit", "planner"):
        raise UnsupportedParam("gate=%r (supported: pdf, pdf_limit, planner)" % gate)
    if gate == "planner":
        statuses = tuple(params.get("statuses") or FREE_GATE_STATUSES)
        scan = _scan_from(window, params, 0)
        first = {}
        for uid, t, kind, status, _r in _usage_rows(ctx, scan):
            if status in statuses and uid not in first:
                first[uid] = t
        pays = ctx.payments_by_uid(scan)
        units = [{"uid": uid, "anchor": t, "pending": t + follow > ctx.now_ms,
                  "conv": _paid_between(pays, uid, t, t + follow)} for uid, t in first.items()]
        return rate_readout(units, window, "share of couples blocked by the Planner (%s) who pay within %g day(s) "
                            "of the first block" % ("/".join(statuses), n_days),
                            ["First block inside the loaded window, not first ever.",
                             "Couples who already paid before the block are not removed."])
    views = int(params.get("views", 3))
    users = _new_users(ctx, window)
    pays = ctx.payments_by_uid(window["earliest_ms"] - PAY_SKEW_MS)
    units = [{"uid": uid, "anchor": t, "pending": t + follow > ctx.now_ms,
              "conv": _paid_between(pays, uid, t - PAY_SKEW_MS, t + follow)}
             for uid, t, u in users if _x.to_int(u.get("total_pdf_views")) >= views]
    return rate_readout(units, window, "share of new accounts with >= %d PDF views who pay within %g day(s) of "
                        "sign-up" % (views, n_days),
                        ["Gate membership is a snapshot of total_pdf_views at read time (no time of hitting it); "
                         "recent cohorts are still filling up.",
                         "Bonus +2 views and the first-vendor-free rule (ep230) blur the 3-view gate."])


def m_pdf_open_rate_new(ctx, params, window):
    if params.get("window_hours") is not None:
        raise UnsupportedParam("window_hours (no first-view time in Xano; use pdf_open_within_1d)")
    min_age = float(params.get("min_age_days", params.get("days", 1)))
    users = _new_users(ctx, window)
    units = [{"uid": uid, "anchor": t, "pending": t + min_age * DAY > ctx.now_ms,
              "conv": _x.to_int(u.get("total_pdf_views")) > 0} for uid, t, u in users]
    ro = rate_readout(units, window, "share of new accounts that have opened a PDF by read time "
                      "(accounts at least %g day(s) old)" % min_age,
                      ["No first-view timestamp in Xano: this is 'opened by read time', not 'within 1 day'. "
                       "Older cohorts have had longer, so a pre cohort is biased upward.",
                       "Not comparable with the Mixpanel 1-day baseline (~60%)."])
    # Across time windows (pre/post, benchmark) the older cohort has had longer to open a PDF, so the
    # comparison is biased by construction: evidence only, never a ship/revert call (health.py).
    # A split test compares same-age arms, so it stays callable.
    if window.get("type") != "split":
        ro["approximate"] = True
    return ro


def m_pdf_open_within_1d(ctx, params, window):
    """Exact from the engine's own pdf history when it covers every counted account in every arm;
    otherwise falls back to the approximate pdf_open_rate_new with a caveat."""
    days = float(params.get("days", 1))
    follow = int(days * DAY)
    users = _new_users(ctx, window)
    hist = ctx.pdf_history()
    units, exact = [], True
    if _rl.enabled():
        return _pdf_open_covered(ctx, window, users, hist, days, follow)
    for uid, t, u in users:
        pending = t + follow + 6 * HOUR > ctx.now_ms
        unit = {"uid": uid, "anchor": t, "pending": pending, "conv": False}
        if _arm_for(unit, window) is None:
            continue
        if not pending:
            v = hist.within(uid, t, days)
            if v is None:
                exact = False
                break
            unit["conv"] = v
        units.append(unit)
    if exact:
        return rate_readout(units, window, "share of new accounts that open a PDF within %g day(s) of sign-up "
                            "(engine pdf history)" % days,
                            ["Resolution is the engine run cadence (about 6 h)."])
    ro = m_pdf_open_rate_new(ctx, {"min_age_days": days}, window)
    ro["caveats"].insert(0, "APPROXIMATE: engine pdf history does not yet cover every account in the cohort, "
                            "so this is the 'opened by read time' definition. Exact once history spans the "
                            "whole cohort window.")
    ro["approximate"] = True
    return ro


PDF_MIN_COVERAGE = 0.98


def _pdf_open_covered(ctx, window, users, hist, days, follow):
    """EXP_TAB_V2: an account the engine's pdf history can't place is dropped, not allowed to make the whole metric
    approximate. ``coverage`` says how many were placed, by arm; under 98% is red (v2.py). Outside a split the old
    guard stays: below 98% the readout is still marked approximate, so it can't be called."""
    units, cov = [], {}
    for uid, t, u in users:
        pending = t + follow + 6 * HOUR > ctx.now_ms
        unit = {"uid": uid, "anchor": t, "pending": pending, "conv": False}
        if _arm_for(unit, window) is None:
            continue
        if not pending:
            v = hist.within(uid, t, days)
            unit["covered"] = v is not None
            unit["conv"] = bool(v)
        units.append(unit)
    more, raw_window = {}, window
    if window.get("v2"):
        from . import v2_data
        units, more = v2_data.prepare_units(units, window)
        raw_window = dict(window, v2=False)
    for u in units:
        if not u["pending"]:
            c = cov.setdefault(_arm_for(u, window), {"covered": 0, "total": 0})
            c["total"] += 1
            c["covered"] += 1 if u["covered"] else 0
    ro = rate_readout([u for u in units if u["pending"] or u["covered"]], raw_window,
                      "share of new accounts that open a PDF within %g day(s) of sign-up (engine pdf history; "
                      "accounts it can't place are left out)" % days,
                      ["Resolution is the engine run cadence (about 6 h)."])
    ro["coverage"] = cov
    ro.update(more)
    if window.get("type") != "split" and any(c["total"] and c["covered"] / c["total"] < PDF_MIN_COVERAGE
                                             for c in cov.values()):
        ro["approximate"] = True
    return ro


SEGMENTS = {"all": "", "free": "free ", "paid": "paid ", "forever": "Forever "}


def _segment_of(params, default="all"):
    seg = str(params.get("segment") or default).lower()
    if seg not in SEGMENTS:
        raise UnsupportedParam("segment=%r (known: %s)" % (seg, ", ".join(SEGMENTS)))
    return seg


class _SegmentTracker:
    """Per-couple tier from their table-79 rows in the scan: ``forever`` (rec_service writes the
    account's Forever flag on every row), ``free`` (a counted-free or free-limit row, or no access),
    ``paid`` (has_access on a row). A couple seen both free and paid in the scan counts as paid."""

    def __init__(self):
        self.forever, self.free, self.paid = set(), set(), set()

    def see(self, uid, status, r):
        if r.get("forever") is True:
            self.forever.add(uid)
        if r.get("has_access") is True:
            self.paid.add(uid)
        if r.get("counted_as_free") or status == "blocked_free_limit" or r.get("has_access") is False:
            self.free.add(uid)

    def keep(self, uid, seg):
        if seg == "all":
            return True
        if seg == "forever":
            return uid in self.forever
        if seg == "paid":
            return uid in self.paid or uid in self.forever
        return uid in self.free and uid not in self.paid and uid not in self.forever


def m_planner_second_question(ctx, params, window):
    n_days = float(params.get("days", 1))
    follow = int(n_days * DAY)
    scan = _scan_from(window, params, 14)
    seg = _segment_of(params)
    tiers = _SegmentTracker()
    qs = {}
    for uid, t, kind, status, r in _usage_rows(ctx, scan):
        tiers.see(uid, status, r)
        if kind == "refine" and status == "ok":
            qs.setdefault(uid, []).append(t)
    units = []
    for uid, ts in qs.items():
        if not tiers.keep(uid, seg):
            continue
        t0 = ts[0]
        if day_of(t0) <= day_of(scan):
            continue          # seen on the scan's first day: may not be their first question
        units.append({"uid": uid, "anchor": t0, "pending": t0 + follow > ctx.now_ms,
                      "conv": sum(1 for t in ts if t <= t0 + follow) >= 2})
    return rate_readout(units, window, "share of %scouples asking their first Planner question who ask a second "
                        "within %g day(s)" % (SEGMENTS[seg], n_days),
                        ["'First' = first in a %d-day lookback, not first ever." % int(params.get("lookback_days", 14)),
                         "Free couples are capped (3 lifetime + 1/day since 11-09)." + (
                             "" if seg != "all" else " Not segmented free/paid (params.segment).")])


def m_planner_return_within_days(ctx, params, window):
    n_days = int(params.get("days", 3))
    if params.get("segment"):
        seg = _segment_of(params)
    else:
        seg = "free" if bool(params.get("free_only", True)) else "all"
    scan = _scan_from(window, params, 7)
    tiers = _SegmentTracker()
    scan_day = day_of(scan)
    use_days, first_t, free = {}, {}, set()
    for uid, t, kind, status, r in _usage_rows(ctx, scan):
        if kind.startswith("ww_"):
            continue
        tiers.see(uid, status, r)
        if r.get("counted_as_free") or status == "blocked_free_limit":
            free.add(uid)
        if kind in PLANNER_USE_KINDS and status == "ok":
            use_days.setdefault(uid, set()).add(day_of(t))
            first_t.setdefault(uid, t)
    today = day_of(ctx.now_ms)
    units = []
    for uid, t0 in first_t.items():
        if seg == "free":
            if uid not in free:
                continue
        elif not tiers.keep(uid, seg):
            continue
        d0 = day_of(t0)
        if d0 <= scan_day:
            continue
        base = parse_ms(d0)
        later = {day_of(base + i * DAY) for i in range(1, n_days + 1)}
        last = day_of(base + n_days * DAY)
        units.append({"uid": uid, "anchor": t0, "pending": last >= today,
                      "conv": bool(use_days[uid] & later)})
    who = SEGMENTS[seg]
    return rate_readout(units, window, "share of new %sPlanner couples who use the Planner again on a later UTC "
                        "day within %d days (opening/refine/lookup/more, status ok)" % (who, n_days),
                        ["Free status is inferred (counted_as_free or blocked_free_limit row).",
                         "Prewarm, pdf, mcp_ and ww_ rows are not counted as use; a blocked return is not a return."])


def m_offer_code_buyers(ctx, params, window):
    prefix = str(params.get("prefix", "TT60-"))
    hours = float(params.get("offer_hours", 12))
    since = parse_ms(params.get("since")) or window["earliest_ms"]
    holders = ctx.view("offer_holders", since)
    internal = ctx.internal_uids()
    offered = []
    for u in holders:
        uid = _x.to_int(u.get("id"))
        code = str(u.get("paywall_offer_code") or "")
        exp = _x.to_ms(u.get("paywall_offer_expires_at"))
        if not uid or exp is None or not code.startswith(prefix) or u.get("is_internal") or uid in internal:
            continue
        offered.append((uid, exp - int(hours * HOUR)))
    pays = ctx.payments_by_uid(min([m for _u, m in offered] + [since]))
    follow = int((hours + 24) * HOUR)        # a purchase counts within the offer + 24 h
    units, revenue = [], 0.0
    for uid, made in offered:
        hit = [(t, a) for t, a, _ty in pays.get(uid, ()) if made <= t <= made + follow]
        # not bought and the purchase window is still open: pending, not a non-buyer
        unit = {"uid": uid, "anchor": made, "pending": not hit and made + follow > ctx.now_ms, "conv": bool(hit)}
        if hit and _arm_for(unit, window) is not None:
            revenue += hit[0][1]
        units.append(unit)
    return rate_readout(units, window, "share of %s offer-code holders who buy within %g h of the offer "
                        "(offer made = expiry - %g h)" % (prefix, hours + 24, hours),
                        ["Counts any plan bought within the offer + 24 h; Stripe promo redemption is not in "
                         "Xano, so it does not prove the code was used.",
                         "Holders whose window is still open and who have not bought are pending.",
                         "Revenue (holders inside the experiment window only) includes tax, refunds not netted."],
                        extra={"revenue": round(revenue, 2)})


def _nps_internal(ctx):
    """Internal ids for table-77 rows: the fixed list plus accounts flagged internal by e-mail in
    table 1. If this run plans to load table-1 users anyway, load them first (no extra request)."""
    if "users" in ctx._since and "users" not in ctx._loaded:
        ctx.view("users", ctx._since["users"])
    return ctx.internal_uids()


def _nps_rows(ctx):
    out = []
    excluded_scale = 0
    internal = _nps_internal(ctx)
    for r in ctx.nps():
        s = _x.to_int(r.get("score"))
        t = _x.to_ms(r.get("created_at"))
        if r.get("is_internal") or _x.to_int(r.get("user_id")) in internal or t is None or s <= 0:
            continue
        if s > 5:
            excluded_scale += 1
            continue
        out.append((_x.to_int(r.get("user_id")), t, s, r))
    return out, excluded_scale


def m_nps_avg_stars(ctx, params, window):
    rows, skipped = _nps_rows(ctx)
    means = {}
    units = []
    for uid, t, s, _r in rows:
        units.append({"uid": uid, "anchor": t, "pending": False, "conv": False, "_s": s})
    arms, expo = {}, {}
    split = window["type"] == "split"
    for u in units:
        arm = _arm_for(u, window)
        if arm is None:
            continue
        a = means.setdefault(arm, {"n": 0, "sum": 0.0})
        a["n"] += 1
        a["sum"] += u["_s"]
        d = day_of(u["anchor"])
        if split:
            expo.setdefault(d, {"control": 0, "treatment": 0})[arm] += 1
        else:
            expo[d] = expo.get(d, 0) + 1
    want = ("control", "treatment") if window["type"] in ("pre_post", "split") else ("treatment",)
    for k in want:
        means.setdefault(k, {"n": 0, "sum": 0.0})
    for k, a in means.items():
        a["mean"] = round(a["sum"] / a["n"], 3) if a["n"] else None
        # Beta encoding of a bounded mean: each rating contributes (stars-1)/4 of a "success".
        arms[k] = {"n": a["n"], "conv": int(round((a["sum"] - a["n"]) / 4.0))}
    cav = ["Mean metric scored as a Beta on (stars-1)/4 per rating, rounded: an approximation. "
           "See 'mean' for the actual averages.", "About 1 rating a day; logged-out raters (user_id 0) included."]
    if skipped:
        cav.append("%d rating(s) above 5 (old 0-10 scale) left out." % skipped)
    if window["type"] == "pre_post":
        cav.append("Pre/post: evidence, not proof (changes in either window confound it).")
    cav += _window_caveats(window)
    out = {"arms": arms, "mean": {k: {"n": v["n"], "mean": v["mean"]} for k, v in means.items()},
           "pending": {}, "exposure_by_day": dict(sorted(expo.items())),
           "definition": "mean NPS card stars (1-5) per rating; ratings %s" % _window_text(window),
           "caveats": cav, "error": None}
    if window["type"] == "benchmark":
        out["benchmark"] = window["assignment"].get("benchmark")
    return out


def m_nps_low_share(ctx, params, window):
    low = int(params.get("max_low", 3))
    rows, skipped = _nps_rows(ctx)
    units = [{"uid": uid, "anchor": t, "pending": False, "conv": s <= low} for uid, t, s, _r in rows]
    cav = ["Unit is a rating, not a couple."]
    if skipped:
        cav.append("%d rating(s) above 5 (old 0-10 scale) left out." % skipped)
    return rate_readout(units, window, "share of NPS card ratings at or below %d stars" % low, cav)


def m_trustpilot_completion(ctx, params, window):
    units = []
    internal = _nps_internal(ctx)
    for r in ctx.nps():
        st = str(r.get("review_prompt_state") or "")
        t = _x.to_ms(r.get("created_at"))
        if r.get("is_internal") or _x.to_int(r.get("user_id")) in internal or t is None:
            continue
        if st in ("", "not_offered"):
            continue
        units.append({"uid": _x.to_int(r.get("user_id")), "anchor": t, "pending": False,
                      "conv": st in ("clicked", "completed")})
    return rate_readout(units, window, "share of raters asked for a Trustpilot review who click through "
                        "(clicked or completed)",
                        ["'completed' can't be observed (TrustBox runs in Trustpilot's iframe): this is the click "
                         "rate. 'declined' counts as asked.", "Anchored on the rating's created_at."])


def _payment_events(ctx, params, window, value):
    types = [str(t).lower() for t in (params.get("types") or ([params["plan"]] if params.get("plan") else []))]
    since = (window["pre"][0] if window["pre"] else window["start_ms"] - int(params.get("baseline_days", 14)) * DAY)
    internal = ctx.internal_uids()
    rows = []
    for p in ctx.view("payments", since):
        uid = _x.to_int(p.get("Client_Reference_ID"))
        t = _x.to_ms(p.get("Time_of_Payment"))
        if t is None or p.get("is_internal") or (uid and uid in internal):
            continue
        ty = str(p.get("Type") or "").lower()
        if types and not any(ty.startswith(x) for x in types):
            continue
        rows.append((uid, t, p))
    geo = geo_cohort(params.get("cohort")) if params.get("cohort") else None
    if params.get("cohort") and geo is None:
        raise UnsupportedParam("cohort=%r (known: %s)" % (params["cohort"], ", ".join(sorted(GEO_COHORTS))))
    if geo is None:
        return [(t, value(p)) for _u, t, p in rows], None
    label, test = geo
    payers = [u for u, _t, _p in rows if u]
    locs = ctx.locations(payers)
    unresolved = sum(1 for u in set(payers) if locs.get(u) is None)
    gifts = sum(1 for u, _t, _p in rows if not u)
    total = len(set(payers))
    cov = 1.0 if not total else (total - unresolved) / total
    info = {"cohort": params["cohort"], "label": label, "payers": total, "unresolved": unresolved,
            "coverage": round(cov, 3), "gifts_skipped": gifts}
    if cov < LOCATION_MIN_COVERAGE:
        raise GeoPending(info)
    ev = [(t, value(p)) for u, t, p in rows if u and locs.get(u) is not None and test(locs[u])]
    return ev, info


class GeoPending(Exception):
    """Payer locations are still being looked up: withhold the readout rather than guess."""

    def __init__(self, info):
        super().__init__("location lookups pending")
        self.info = info


def _geo_caveats(info):
    if not info:
        return []
    out = ["Cohort: %s, by the wedding location on the account today (Wedding_Location_Updated, same rule as "
           "the app's price buttons); a couple who changed location after paying is counted where they are now."
           % info["label"]]
    if info["unresolved"]:
        out.append("%d of %d payers' locations not looked up yet (left out of both periods)."
                   % (info["unresolved"], info["payers"]))
    if info["gifts_skipped"]:
        out.append("%d gift payment(s) without an account left out." % info["gifts_skipped"])
    return out


def m_purchases_per_day(ctx, params, window):
    ev, geo = _payment_events(ctx, params, window, lambda p: 1)
    types = params.get("types") or ([params["plan"]] if params.get("plan") else [])
    what = ("purchases of type %s*" % "/".join(types)) if types else "purchases"
    if geo:
        what += " by %s" % geo["label"]
    cav = [] if types else ["Includes upgrades and gifts (filter with params.types)."]
    ro = count_readout(ev, window, "non-internal %s per UTC day" % what, cav + _geo_caveats(geo),
                       int(params.get("baseline_days", 14)))
    if geo:
        ro["geo"] = geo
    return ro


def m_revenue_per_day(ctx, params, window):
    ev, geo = _payment_events(ctx, params, window, lambda p: float(p.get("Amount") or 0))
    total = sum(v for _t, v in ev)
    ticket = total / len(ev) if ev else 0.0
    # Gamma-Poisson needs event counts: express revenue in purchase-equivalents (dollars / mean
    # ticket over both periods), so the dispersion is that of purchases, not of single dollars.
    scaled = [(t, v / ticket) for t, v in ev] if ticket else []
    who = (" from %s" % geo["label"]) if geo else ""
    ro = count_readout(scaled, window, "gross revenue%s per UTC day, in purchase-equivalents of the mean ticket" % who,
                       ["Gross incl. tax, refunds (~6%) not netted.",
                        "Scored as counts of mean-ticket purchases ($%.2f); 'dollars' has the raw sums." % ticket]
                       + _geo_caveats(geo),
                       int(params.get("baseline_days", 14)))
    if geo:
        ro["geo"] = geo
    raw = count_readout(ev, window, "", [], int(params.get("baseline_days", 14)))["count"]
    ro["dollars"] = {"pre": raw["pre"]["count"], "post": raw["post"]["count"], "mean_ticket": round(ticket, 2)}
    return ro


def m_nps_ratings_per_day(ctx, params, window):
    rows, _ = _nps_rows(ctx)
    return count_readout([(t, 1) for _u, t, _s, _r in rows], window, "NPS card ratings per UTC day", [],
                         int(params.get("baseline_days", 14)))


def m_paid_after_pricing_view(ctx, params, window):
    n_days = float(params.get("days", 1))
    follow = int(n_days * DAY)
    src = params.get("src")
    rows = ctx.view("pricing_viewers", window["earliest_ms"])
    internal = ctx.internal_uids()
    pays = ctx.payments_by_uid(window["earliest_ms"])
    units = []
    for u in rows:
        uid = _x.to_int(u.get("id"))
        t = _x.to_ms(u.get("pricing_viewed_at"))
        if not uid or t is None or u.get("is_internal") or uid in internal:
            continue
        if src:
            c = u.get("pricing_viewed_ctx")
            if isinstance(c, str):
                try:
                    c = json.loads(c)
                except ValueError:
                    c = {}
            if str((c or {}).get("src") or "") != str(src):
                continue
        units.append({"uid": uid, "anchor": t, "pending": t + follow > ctx.now_ms,
                      "conv": _paid_between(pays, uid, t, t + follow)})
    return rate_readout(units, window, "share of first /pricing visitors%s who pay within %g day(s)" % (
        (" (src=%s)" % src) if src else "", n_days),
        ["First visit only; data starts 2026-11-12; existing payers are probably never stamped."])


def m_planner_pdf_open_rate(ctx, params, window):
    n_days = float(params.get("days", 7))
    follow = int(n_days * DAY)
    scan = _scan_from(window, params, 0)
    seg = _segment_of(params)
    tiers = _SegmentTracker()
    first, pdfs = {}, {}
    for uid, t, kind, status, r in _usage_rows(ctx, scan):
        tiers.see(uid, status, r)
        if kind in PLANNER_USE_KINDS and status == "ok":
            first.setdefault(uid, t)
        elif kind == "pdf" and status in PDF_OK:
            pdfs.setdefault(uid, []).append(t)
    units = [{"uid": uid, "anchor": t0, "pending": t0 + follow > ctx.now_ms,
              "conv": any(t0 <= t <= t0 + follow for t in pdfs.get(uid, ()))} for uid, t0 in first.items()
             if tiers.keep(uid, seg)]
    return rate_readout(units, window, "share of %sPlanner couples who open a PDF from a Planner card within %g "
                        "day(s) of first use" % (SEGMENTS[seg], n_days),
                        ["Planner-card opens only; vendor-page PDF opens are not logged in table 79."])


def m_manual_only(reason):
    def fn(ctx, params, window):
        return {"error": reason, "manual": True}
    return fn


METRICS = {
    "new_paid_within_days": m_new_paid_within_days,
    "gate_hitters_paid": m_gate_hitters_paid,
    "pdf_open_rate_new": m_pdf_open_rate_new,
    "pdf_open_within_1d": m_pdf_open_within_1d,
    "planner_second_question": m_planner_second_question,
    "planner_return_within_days": m_planner_return_within_days,
    "offer_code_buyers": m_offer_code_buyers,
    "nps_avg_stars": m_nps_avg_stars,
    "nps_low_share": m_nps_low_share,
    "nps_ratings_per_day": m_nps_ratings_per_day,
    "trustpilot_completion": m_trustpilot_completion,
    "purchases_per_day": m_purchases_per_day,
    "revenue_per_day": m_revenue_per_day,
    "paid_after_pricing_view": m_paid_after_pricing_view,
    "planner_pdf_open_rate": m_planner_pdf_open_rate,
    "planner_thumbs_down_rate": m_manual_only("not in Xano (Mixpanel only)"),
    "manual": m_manual_only("manual metric (no Xano definition)"),
}

# recon catalog names -> the implementation (params fill-ins in ALIAS_PARAMS)
ALIASES = {
    "new_paid_within_n_days": "new_paid_within_days",
    "paid_after_pdf_gate": "gate_hitters_paid",
    "paid_after_planner_gate": "gate_hitters_paid",
    "save_offer_buyers": "offer_code_buyers",
    "trustpilot_click_rate": "trustpilot_completion",
    "planner_second_question_rate": "planner_second_question",
    "planner_return_within_n_days_free": "planner_return_within_days",
}
ALIAS_PARAMS = {"paid_after_planner_gate": {"gate": "planner"}, "paid_after_pdf_gate": {"gate": "pdf"}}
for _a, _k in ALIASES.items():
    METRICS.setdefault(_a, METRICS[_k])


# params each metric understands; anything else is reported, never silently ignored
ACCEPTED = {
    "new_paid_within_days": {"days"},
    "gate_hitters_paid": {"gate", "days", "views", "statuses", "lookback_days"},
    "pdf_open_rate_new": {"min_age_days", "days", "window_hours"},
    "pdf_open_within_1d": {"days"},
    "planner_second_question": {"days", "lookback_days", "segment"},
    "planner_return_within_days": {"days", "free_only", "lookback_days", "segment"},
    "offer_code_buyers": {"prefix", "offer_hours", "since"},
    "nps_avg_stars": set(), "nps_low_share": {"max_low"}, "nps_ratings_per_day": {"baseline_days"},
    "trustpilot_completion": set(),
    "purchases_per_day": {"types", "plan", "baseline_days"},
    "revenue_per_day": {"types", "plan", "baseline_days"},
    "paid_after_pricing_view": {"days", "src"},
    "planner_pdf_open_rate": {"days", "lookback_days", "segment"},
}


def _resolve(key, params):
    p = dict(ALIAS_PARAMS.get(key, {}))
    p.update(params or {})
    return METRICS.get(key), p


# Which views (and from when) a metric reads, so DataContext.prepare can widen each view once.
def _users_need(params, w):
    return {"users": w["earliest_ms"], "payments": w["earliest_ms"] - PAY_SKEW_MS}


def _usage_need(default_lookback, pays=False):
    def f(params, w):
        s = w["earliest_ms"] - int(params.get("lookback_days", default_lookback)) * DAY
        return {"usage": s, "payments": s} if pays else {"usage": s}
    return f


def _gate_need(params, w):
    p = dict(params or {})
    return _usage_need(0, pays=True)(p, w) if p.get("gate") == "planner" else _users_need(p, w)


def _pay_need(params, w):
    return {"payments": w["pre"][0] if w["pre"] else w["start_ms"] - int(params.get("baseline_days", 14)) * DAY}


NEEDS = {
    "new_paid_within_days": _users_need, "new_paid_within_n_days": _users_need,
    "gate_hitters_paid": _gate_need, "paid_after_pdf_gate": _users_need,
    "paid_after_planner_gate": _usage_need(0, pays=True),
    "pdf_open_rate_new": lambda p, w: {"users": w["earliest_ms"]},
    "pdf_open_within_1d": lambda p, w: {"users": w["earliest_ms"]},
    "planner_second_question": _usage_need(14), "planner_second_question_rate": _usage_need(14),
    "planner_return_within_days": _usage_need(7), "planner_return_within_n_days_free": _usage_need(7),
    "planner_pdf_open_rate": _usage_need(0),
    "offer_code_buyers": lambda p, w: {"offer_holders": parse_ms(p.get("since")) or w["earliest_ms"]},
    "purchases_per_day": _pay_need, "revenue_per_day": _pay_need,
    "paid_after_pricing_view": lambda p, w: {"pricing_viewers": w["earliest_ms"], "payments": w["earliest_ms"]},
}


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

def is_auto(entry):
    m = entry.get("metric") or {}
    return (str(entry.get("reporting") or "").lower() == "auto" and m.get("key")
            and str(m.get("key")) != "manual")


def compute(ctx, key, params, window):
    """One metric -> readout. Never raises except BudgetExceeded (which must abort the run)."""
    fn, p = _resolve(key, params)
    if fn is None:
        return {"error": "unknown metric", "metric": key}
    canon = ALIASES.get(key, key)
    if canon in ACCEPTED:
        extra = sorted(set(p) - ACCEPTED[canon] - {"cohort"})
        if extra:
            return {"error": "unsupported param(s) for %s: %s" % (key, ", ".join(extra)), "metric": key}
    if p.get("cohort") and key not in ("manual", "planner_thumbs_down_rate") and not (
            canon in GEO_METRICS and geo_cohort(p["cohort"])):
        return {"error": "unsupported param: cohort=%r for %s (geo cohorts work on purchases_per_day and "
                         "revenue_per_day: %s)" % (p["cohort"], key, ", ".join(sorted(GEO_COHORTS))), "metric": key}
    if window["type"] == "split" and window["assignment"].get("rule") not in SPLIT_RULES:
        return {"error": "unsupported split rule %r (supported: %s)" % (
            window["assignment"].get("rule"), ", ".join(SPLIT_RULES)), "metric": key}
    try:
        ro = fn(ctx, p, window)
    except _x.BudgetExceeded:
        raise
    except UnsupportedParam as exc:
        return {"error": "unsupported param: %s" % exc, "metric": key}
    except GeoPending as exc:
        i = exc.info
        runs = -(-i["unresolved"] // max(LOCATION_LOOKUPS_PER_RUN, 1))
        return {"error": "looking up wedding locations: %d of %d payers done; the starting point appears "
                         "in about %d more run(s) (every 6 h)" % (i["payers"] - i["unresolved"], i["payers"], runs),
                "pending_geo": i, "metric": key}
    except Exception as exc:  # one metric failing never takes down the others
        return {"error": "%s: %s" % (type(exc).__name__, exc), "metric": key}
    ro.setdefault("metric", key)
    ro["params"] = p
    ro["computed_at"] = _dt.datetime.fromtimestamp(ctx.now_ms / 1000, _dt.timezone.utc).isoformat()
    return ro


def run_experiment(ctx, entry):
    """Primary metric readout + guardrails[] for one registry entry."""
    if _rl.enabled():
        from . import v2_data
        return v2_data.run_experiment(ctx, entry)
    w = build_window(entry, ctx.now_ms)
    m = entry.get("metric") or {}
    ro = compute(ctx, m.get("key"), m.get("params") or {}, w)
    guards = []
    for g in entry.get("guardrails") or []:
        if not isinstance(g, dict) or not g.get("key"):
            continue
        gr = compute(ctx, g["key"], g.get("params") or {}, w)
        row = {"key": g["key"]}
        for k in ("count", "arms", "error", "manual", "definition"):
            if k in gr:
                row[k] = gr[k]
        guards.append(row)
    ro["guardrails"] = guards
    return ro


def run_all(entries, client, now_ms=None, statuses=("live",), record_pdf_history=False, data_dir=None,
            scorecards=False):
    """Compute every auto experiment in ``entries``. Returns (readouts{id: readout}, ctx).

    On BudgetExceeded the run stops: experiments already computed keep their readout, the rest
    get {"error": "budget exceeded"} (section 11: the run aborts, nothing partial is scored as data).
    """
    ctx = DataContext(client, now_ms=now_ms, record_pdf_history=record_pdf_history, data_dir=data_dir)
    todo = [e for e in entries if is_auto(e) and str(e.get("status") or "") in statuses]
    if _rl.enabled():                      # manual entries too: guardrails, payments and exposure by arm
        from . import v2_data
        todo = [e for e in entries if str(e.get("status") or "") in statuses and (is_auto(e) or v2_data.wants_run(e))]
    if scorecards:
        todo = todo + scorecard_entries(ctx.now_ms)
    ctx.prepare(todo)
    out = {}
    for i, e in enumerate(todo):
        try:
            out[e.get("id")] = run_experiment(ctx, e)
        except _x.BudgetExceeded as exc:
            for rest in todo[i:]:
                out[rest.get("id")] = {"error": "budget exceeded: %s" % exc}
            break
    if _rl.enabled():
        from . import v2_data
        v2_data.attach_overlaps(ctx, todo, out)
    if scorecards:
        sc = [e for e in todo if e.get("_scorecard")]
        ctx.scorecards = {"planner": scorecard_rows(out, sc), "computed_at": _iso(ctx.now_ms)}
        for e in sc:
            out.pop(e["id"], None)
    return out, ctx



# ---------------------------------------------------------------------------
# standing Planner scorecard (owner 2026-11-19): is the Planner working, by who uses it?
#   free couples   -> the Planner's job is to get them opening pricing PDFs (on the way to the paywall)
#   Forever couples -> retention: do they come back, and do they keep asking
# Last 7 complete days vs the 7 before, from the same table-79 load as the experiments (no extra requests
# beyond widening the usage view by a few days).
# ---------------------------------------------------------------------------

SCORECARD_PLANNER = [
    ("free_pdf_open", "Free couples who open a PDF from the Planner within 3 days",
     "planner_pdf_open_rate", {"days": 3, "segment": "free"}),
    ("forever_return", "Forever couples who come back to the Planner within 3 days",
     "planner_return_within_days", {"days": 3, "segment": "forever"}),
    ("forever_second_question", "Forever couples who ask a second question within 3 days",
     "planner_second_question", {"days": 3, "segment": "forever"}),
]


def scorecard_entries(now_ms):
    today = day_start(now_ms)
    pre0, post0 = today - 14 * DAY, today - 7 * DAY
    def d(ms):
        return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%d")
    out = []
    for key, label, metric, params in SCORECARD_PLANNER:
        out.append({"id": "SCORECARD-planner-" + key, "status": "live", "reporting": "auto",
                    "metric": {"key": metric, "params": dict(params), "label": label},
                    "assignment": {"type": "pre_post", "pre": [d(pre0), d(post0 - DAY)],
                                   "post_start": d(post0) + "T00:00:00Z"},
                    "end_date": d(today - DAY), "_scorecard": (key, label)})
    return out


def scorecard_rows(readouts, entries):
    """[{key, label, before:{n,conv}, after:{n,conv}, pending, definition, error}] for the dash."""
    rows = []
    for e in entries:
        key, label = e["_scorecard"]
        ro = readouts.get(e["id"]) or {}
        arms = ro.get("arms") or {}
        rows.append({"key": key, "label": label,
                     "before": arms.get("control"), "after": arms.get("treatment"),
                     "before_label": "7 days before", "after_label": "Last 7 days",
                     "pending": sum((ro.get("pending") or {}).values()) if isinstance(ro.get("pending"), dict) else 0,
                     "definition": ro.get("definition"), "error": ro.get("error")})
    return rows
