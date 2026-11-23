"""What would this merge do to the price couples see? Read-only; used by the merge card BEFORE Approve.

WHERE THE COUPLE-FACING PRICE COMES FROM (first-hand, read live 2026-11-22)
  Venue page: the card shows the Xano ep231 / ep230 payload, i.e. fn56 utils/venue_pricing_panel over table 62
  (venue_spaces), table 63 (market benchmarks) and the vendor's Category. Table 62 is written per vendor by fn54
  utils/rebuild_venue_spaces from t36 + t37 rows, t10 PDF labels/years and t11 flt_states. ep578 moves the t10/t36/t37
  rows to the survivor and runs fn54, so after = fn54(survivor rows + loser rows) -> fn56.
  Planner: rec_service/estimate.py on the same ep230 payload. Discovery: venue cards show no venue price; only the price
  FILTERS use t11 flt_min/max_*, written by fn21 recompute_vendor_filter_aggregates.

HOW THIS STAYS TRUE TO THE LIVE CODE
  fn54's and fn56's logic is JavaScript inside api.lambda blocks. This module downloads the LIVE source and runs those
  blocks as they are, in an embedded JS engine (quickjs: no network, no files, time and memory limits), so a change to
  the package list or the maths is picked up at once. What is ported here by hand is only the database steps around
  them (which rows go in) and fn21. Those ported parts are pinned by hash: if the live version differs from the one this
  file was written against, the answer is "can't compute", which forces the alert. Nothing is ever guessed.

  Other guards: "today" is priced from the stored t62 rows (what couples really see) and must equal the live page
  endpoint; the after-price is recomputed under several row orders (Postgres gives no order) and must not move.

RULES (owner, AMENDMENT 1): any change is shown. A change >= 10%, none <-> some, or anything that can't be computed is a
prominent alert plus a confirm step. A change is described, never judged, and the approver is never told it is fine.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("slack_qa.price_impact")

ALERT_PCT = 10.0
WORKERS = 2                # reads in flight at once per card (4 brought brief 502s 2026-11-22; merge_checks also runs one card at a time)
DEADLINE = 25.0            # seconds for one card, everything included
GUESTS = (100, 50, 150, 200)   # 100 = what the page opens on (the fn56 default); the others catch hidden changes
ORDERS = 5                 # row orders tried for the after-price
MAX_LISTINGS = 4           # ep578: one survivor + up to three losers
MAX_SPACES = 60
CODE_TTL = 600
API_BASE = "https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B"
FN_SPACES, FN_PANEL, FN_FILTERS = 54, 56, 21
# sha256[:16] of the parts of the live source that are re-implemented here (see _shell). Different = can't compute.
PINNED = {FN_SPACES: "2098c857be35fa5b", FN_PANEL: "cc7e5f6fa20217b8", FN_FILTERS: "1e6e0500584e2925"}

TABLES = {10: "Vendor_ID", 11: "Vendor_ID", 36: "VENDOR_ID", 37: "Vendor_ID", 62: "VENDOR_ID", 63: "State",
          84: "Vendor_ID", 85: "Vendor_ID", 92: "Vendor_ID", 93: "Vendor_ID"}
FUNCTIONS = (FN_SPACES, FN_PANEL, FN_FILTERS)
PER_PAGE = 500
MAX_PAGES = 8

MONEY_FIELDS = (("venue_fee", "rental"), ("fb_min", "food and drink minimum"), ("pp_fb", "food and drink per guest"),
                ("ceremony_fee", "ceremony fee"), ("service_pct", "service charge %"), ("guest_min", "guest minimum"))
FLT = ("flt_min_venue_fee", "flt_max_venue_fee", "flt_min_ppfb", "flt_max_ppfb", "flt_min_fbmin", "flt_max_fbmin")
FLT_LABEL = {"flt_min_venue_fee": "lowest rental", "flt_max_venue_fee": "highest rental",
             "flt_min_ppfb": "lowest per guest", "flt_max_ppfb": "highest per guest",
             "flt_min_fbmin": "lowest food minimum", "flt_max_fbmin": "highest food minimum"}


# what an alert can be about, most important first; the card names these, so a confirm step never looks unexplained
KINDS = ("can't compute", "price shown or not", "opening price", "opening room", "own figures or estimate", "room",
         "guest count", "hand-reviewed rule", "search filter")


class CantCompute(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------- reading (GET + row search)

class Reader:
    """The only network code here. Three shapes of request, nothing else: GET one of three functions' source, POST a
    filtered row SEARCH (a read) on ten tables by one fixed column, GET the public page endpoint."""

    def __init__(self, xano, deadline: float, http=None):
        self.root, self.token = xano.root, getattr(xano, "token", "")
        self.http = http or xano.http
        self.deadline = deadline
        self.calls = 0

    def _send(self, method, url, **kw):
        """One retry on a dropped, slow or 5xx read (measured: an occasional Xano read stalls past 12 s or answers 502)."""
        for i in range(2):
            left = self.deadline - time.monotonic()
            if left <= 0.2:
                raise CantCompute("ran out of time")
            self.calls += 1
            try:
                r = getattr(self.http, method)(url, timeout=min(8.0 if i == 0 else 12.0, left), **kw)
            except OSError as e:          # requests exceptions are OSErrors
                if i == 1:
                    raise CantCompute(f"Xano did not answer ({type(e).__name__})") from e
                continue
            if i == 0 and r.status_code in (429, 502, 503, 504):
                time.sleep(min(1.5, max(0.0, self.deadline - time.monotonic() - 0.3)))
                continue
            return r

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def source(self, fid: int) -> dict:
        if fid not in FUNCTIONS:
            raise PermissionError(f"function {fid!r} is not readable here")
        r = self._send("get", f"{self.root}/function/{int(fid)}", params={"include_xanoscript": "true"},
                       headers=self._headers())
        if r.status_code != 200:
            raise CantCompute(f"Xano source of function {fid}: HTTP {r.status_code}")
        d = r.json()
        x = d.get("xanoscript")
        x = x if isinstance(x, str) else (x or {}).get("value")
        if not x:
            raise CantCompute(f"Xano returned no source for function {fid}")
        return {"code": x, "updated_at": d.get("updated_at")}

    def rows(self, table: int, values) -> list[dict]:
        col = TABLES.get(table)
        if col is None:
            raise PermissionError(f"table {table!r} is not readable here")
        vals = sorted({str(v) for v in values if v})
        if not vals:
            return []
        out, seen = [], set()
        for page in range(1, MAX_PAGES + 1):
            r = self._send("post", f"{self.root}/table/{table}/content/search", headers=self._headers(),
                           json={"page": page, "per_page": PER_PAGE, "sort": {"id": "asc"},
                                 "search": {f"{col}|in": vals}})
            if r.status_code != 200:
                raise CantCompute(f"Xano table {table}: HTTP {r.status_code}")
            d = r.json()
            for i in d.get("items") or []:
                if str(i.get(col) or "") not in vals:
                    raise CantCompute(f"Xano table {table}: the filter was not applied")
                if i["id"] not in seen:
                    seen.add(i["id"])
                    out.append(i)
            if not d.get("nextPage"):
                return out
        raise CantCompute(f"table {table}: more than {MAX_PAGES * PER_PAGE} rows")

    def page_payload(self, vendor_id: str) -> dict:
        # cache_v is part of ep231's 4-hour cache key: a fresh value reads today's figures, not a stored page
        r = self._send("get", API_BASE + "/venue/pricing_intelligence",
                       params={"vendor_id": vendor_id, "cache_v": 900000 + int(time.time()) % 900000})
        if r.status_code != 200:
            raise CantCompute(f"venue page endpoint: HTTP {r.status_code}")
        return r.json()


# ------------------------------------------------------------------------------------------------- the live code

LAMBDA = re.compile(r'code = """\r?\n(.*?)\r?\n\s*"""\s*\r?\n\s*timeout = \d+\s*\r?\n\s*\}\s*as \$(\w+)', re.S)


def lambdas(src: str) -> dict:
    return {m.group(2): m.group(1) for m in LAMBDA.finditer(src)}


def _strip(src: str) -> str:
    """Source without comment lines and spacing, so wording changes don't trip the pin."""
    return re.sub(r"\s+", " ", re.sub(r"(?m)^\s*//.*$", "", src)).strip()


def _shell(src: str, fid: int) -> str:
    """The part re-implemented here: fn54/fn56 = everything around the lambdas; fn21 = all of it."""
    if fid != FN_FILTERS:
        src = LAMBDA.sub(lambda m: f"<lambda {m.group(2)}>", src)
    return _strip(src)


def fingerprint(src: str, fid: int) -> str:
    return hashlib.sha256(_shell(src, fid).encode("utf-8")).hexdigest()[:16]


_CODE: dict = {}
_CODE_LOCK = threading.Lock()


def live_code(reader: Reader) -> dict:
    """{fid: {code, updated_at, pin_ok}}, cached 10 minutes."""
    with _CODE_LOCK:
        if _CODE and _CODE["at"] > time.monotonic() - CODE_TTL:
            return _CODE["val"]
    got = {fid: reader.source(fid) for fid in FUNCTIONS}      # one at a time, then cached 10 minutes
    for fid, g in got.items():
        g["pin_ok"] = fingerprint(g["code"], fid) == PINNED[fid]
    with _CODE_LOCK:
        _CODE.update(at=time.monotonic(), val=got)
    return got


DEFAULT_LINE = "const DEFAULT_GUESTS = 100;"


def engine(code54: str, code56: str):
    """Returns run(kind, vars, input) -> python value; kind in spaces / ctx / panel<guests>."""
    try:
        import quickjs
    except ImportError as e:
        raise CantCompute("the JS engine (quickjs-ng) is not installed") from e
    l54, l56 = lambdas(code54), lambdas(code56)
    if "plan" not in l54 or "ctx" not in l56 or "panel" not in l56:
        raise CantCompute("the live pricing code no longer has the expected parts")
    if l56["panel"].count(DEFAULT_LINE) != 1:
        raise CantCompute("the live card's default guest count is no longer 100")
    ctx = quickjs.Context()
    ctx.set_memory_limit(128 << 20)
    ctx.set_time_limit(15)
    ctx.eval("""
      var F = {};
      function def(name, body) { F[name] = new Function('$var', '$input', body); }
      function run(name, vars, input) {
        const r = F[name](JSON.parse(vars), JSON.parse(input));
        return JSON.stringify(r === undefined ? null : r);
      }""")
    d, run = ctx.get("def"), ctx.get("run")
    d("spaces", l54["plan"])
    d("ctx", l56["ctx"])
    for g in GUESTS:
        d(f"panel{g}", l56["panel"].replace(DEFAULT_LINE, f"const DEFAULT_GUESTS = {g};"))

    def call(kind, vars_, input_):
        try:
            return json.loads(run(kind, json.dumps(vars_), json.dumps(input_)))
        except Exception as e:  # noqa: BLE001 - a JS error means we can't reproduce the page
            raise CantCompute(f"live code failed ({kind}): {str(e)[:120]}") from e
    return call


# -------------------------------------------------------------------------------------------- the two live functions

def sim_spaces(run, vid: str, t36, t37, pdfs, vendor_row) -> list[dict]:
    """fn54: the t62 rows it would write for this vendor."""
    v = {"vid": vid, "t36": t36, "t37": t37, "pdfs": pdfs, "existing": [],
         "vendor_rows": [{"Vendor_ID": vid, "flt_states": vendor_row.get("flt_states")}]}
    return run("spaces", v, {"vendor_id": vid, "dry_run": True})["rows"]


def sim_panel(run, vid: str, rows, category: str, bm, space_key="", entitled=True, guests=100):
    """fn56: the card payload. bm = every benchmark row for the states in play."""
    c = run("ctx", {"spaces": rows}, {"space_key": space_key, "vendor_id": vid, "entitled": entitled})
    want = set(c["states"])
    v = {"spaces": rows, "ctx": c, "bm": [b for b in bm if b.get("State") in want],
         "vendor_row": [{"Category": category}], "pv": {"list": [], "headline": "", "more_label": "", "linked": 0}}
    return run(f"panel{guests}", v, {"vendor_id": vid, "space_key": space_key, "entitled": entitled,
                                     "hide_range": False})


def sim_filters(rows36, primary_ids) -> dict:
    """fn21: t11 flt_min/max price columns (peak Saturday; Primary PDFs only, else every row)."""
    price = [r for r in rows36 if r.get("PDF_ID") in primary_ids] or rows36

    def n(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    def eff(r, first, second):
        return n(r.get(first)) if n(r.get(first)) > 0 else n(r.get(second))
    cols = {"venue_fee": [eff(r, "Package_Price", "Venue_Fee_on_a_Peak_Season_Saturday") for r in price],
            "ppfb": [eff(r, "Hourly_Rate", "Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday") for r in price],
            "fbmin": [n(r.get("Food_and_Beverage_Min_on_a_Peak_Season_Saturday")) for r in price]}
    out = {}
    for key, vals in cols.items():
        pos = [v for v in vals if v > 0]
        out[f"flt_min_{key}"] = min(pos) if pos else 0
        out[f"flt_max_{key}"] = max(pos) if pos else 0
    return out


# --------------------------------------------------------------------------------------------- what couples read

def money(n) -> str:
    return f"${int(round(n)):,}"


def _component(space, key):
    return next((c for c in space.get("components") or [] if c.get("key") == key), {})


COSTS = ("food_and_drink", "ceremony", "venue_fee", "service_charge")


def describe(payload: dict | None) -> dict:
    """The couple-facing facts of one entitled fn56 payload. kind: none (no card) / market (all estimate) / venue."""
    sp = (payload or {}).get("space")
    if not payload or not payload.get("found") or not sp:
        return {"kind": "none", "total": None, "text": "no price shown", "estimated": [], "spaces": 0}
    ai = sp["all_in"]
    at = ai["at_default"]
    est = [k for k in COSTS if _component(sp, k).get("source") != "venue" or _component(sp, k).get("is_estimate")]
    total = at["total"]
    # ww-price-report: any market stand-in -> "About", rounded to $1,000 (to $100 below $10,000). JS rounds half up.
    step = 1000 if total >= 10000 else 100
    shown = int(total / step + 0.5) * step if est else total
    band = sp.get("band") or {}
    pos = {"budget": "lower quarter", "average": "middle half", "luxury": "upper quarter"}.get(band.get("position"), "")
    if pos:
        basis = {"pp_fb": " (food and drink per guest only)", "venue_fee": " (rental only)",
                 "ceremony_fee": " (ceremony fee only)"}.get(band.get("basis_component"), "")
        pos = f"{pos} of {band.get('n_spaces')} comparable {band.get('scope_label')} spaces{basis}"
    return {"kind": "market" if ai.get("is_estimate") else "venue", "total": total, "guests": at["guests"],
            "per_guest": at["per_guest"], "text": ("About " if est else "") + money(shown),
            "estimated": est, "space": sp.get("label"), "space_key": sp.get("space_key"),
            "spaces": len(payload.get("spaces") or []), "position": pos, "contracts": sp.get("contract_count"),
            "floor": sp.get("guest_floor") or 0, "planner": planner_total(sp)}


def planner_total(sp, asked=100):
    """rec_service/estimate.py compute() with package totals on (live 2026-11-22), for the same payload."""
    food, cer, vf, svc = (_component(sp, k) for k in COSTS)
    est = sp["all_in"].get("estimates") or {}

    def pos(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None
    g = max(asked, int(sp.get("guest_floor") or 0))
    rental = pos(vf.get("amount")) or pos(est.get("venue_fee")) or 0
    if not pos(cer.get("amount")) and (cer.get("fee_type") == "included" or cer.get("source") == "package"):
        ceremony = 0
    else:
        ceremony = pos(cer.get("amount")) or pos(est.get("ceremony")) or 0
    own = food.get("source") == "venue"
    if food.get("fee_includes_fb"):
        pp, n = pos(food.get("pp_fb")) or 0, int(food.get("included_guests") or 0)
        f = pp * max(0, g - n) if pp and n else (max(0, pp * g - rental) if pp else 0)
    else:
        pp = (pos(food.get("pp_fb")) if own else None) or pos(est.get("pp_fb")) or 0
        mn = (pos(food.get("fb_min")) if own else None) or 0
        f = max(pp * g, mn * g if food.get("fb_min_is_per_person") else mn)
    pct = pos(svc.get("pct"))
    if not pct:
        pct = 0 if svc.get("source") == "package" else (pos(est.get("service_pct")) or 20)
    sub = rental + ceremony + f
    return round(sub + sub * pct / 100.0)


def visible(v: dict) -> bool:
    return str(v.get("Validated_Data")) == "1" and not str(v.get("merged_into") or "").strip()


def _pct(a, b):
    """Percent change; None when there is no base to compare with (0 today and something else after)."""
    if a == b:
        return 0.0
    return (b - a) * 100.0 / a if a else None


def _big(p) -> bool:
    return p is None or abs(p) >= ALERT_PCT


# ---------------------------------------------------------------------------------------------------- the merge

def _years(pdf) -> int:
    ys = str((pdf or {}).get("Year_of_Pricing") or "")
    if re.search(r"2,0|20,2", ys):
        ys = ys.replace(",", "")
    m = re.findall(r"20\d\d", ys)
    return max(map(int, m)) if m else 0


def _label_year(pdf) -> int:
    """ep578 v5's reading of the stored year: newest stand-alone 2000-2100; a short range ("2025-26") is unreadable."""
    s = str((pdf or {}).get("Year_of_Pricing") or "") + " "
    if re.search(r"20[0-9][0-9] *[-/\u2013] *[0-9]{2}(?![0-9])", s):
        return 0
    return max((int(x) for x in re.findall(r"(?<![0-9])[0-9]{4}(?![0-9])", s) if 2000 <= int(x) <= 2100), default=0)


def _file(pdf) -> str:
    m = re.search(r"[-A-Za-z0-9_]{25,}", str((pdf or {}).get("PDF_Link") or ""))
    return m.group(0) if m else f"row {pdf.get('id')}" if not str(pdf.get("PDF_Link") or "").strip() else str(pdf["PDF_Link"]).strip()


def assignments_after(pdfs, survivor: str, losers, given=None, hidden=()) -> dict:
    """{t10 row id: Current_Assignment after the merge}. `given` = ep578's own planned changes (preview t10_plan).
    Without it, the owner's rule (PASS_BAR Amendment 3, 2026-11-22): one Primary stays - visible first, then the kept
    listing's, then the losers in order, then the newest year - but a loser's visible Primary with a strictly newer year
    than the kept listing's takes over; every other Primary becomes a Past Season PDF."""
    out = {p["id"]: p.get("Current_Assignment") for p in pdfs}
    if given is not None:
        out.update({int(k): v for k, v in given.items()})
        return out
    prim = [p for p in pdfs if str(p.get("Current_Assignment") or "").strip().lower() == "primary"]
    if not prim:
        return out

    def rank(p):
        vis = p.get("Show_PDF") is not False and bool(str(p.get("PDF_Link") or "").strip()) and p["PDF_ID"] not in hidden
        mine = p.get("Vendor_ID") == survivor
        return (not vis, not mine, -_label_year(p), -1 if mine else list(losers).index(p["Vendor_ID"]), p["id"])
    keep = min(prim, key=rank)
    own = {_file(p) for p in pdfs if p.get("Vendor_ID") == survivor and not rank(p)[0]}
    fresh = [p for p in prim if p.get("Vendor_ID") != survivor and not rank(p)[0] and _file(p) not in own]
    if keep.get("Vendor_ID") == survivor:
        if not rank(keep)[0] and _label_year(keep) and fresh:      # the same file on both listings doesn't count as newer
            top = min(fresh, key=rank)
            keep = top if _label_year(top) > _label_year(keep) else keep
    elif fresh:
        keep = min(fresh, key=rank)
    for p in prim:
        if p is not keep:                                          # ep578 v4: exactly one Primary after
            out[p["id"]] = "Past Season PDF"
    return out


def _year(pdf) -> str:
    return str(_years(pdf) or "year not listed")


def _orders(s_rows, l_rows):
    """Row orders to try: by id, survivor first then losers, newest first, then seeded shuffles."""
    base = sorted(s_rows + l_rows, key=lambda r: r["id"])
    yield base
    yield list(s_rows) + list(l_rows)
    yield base[::-1]
    for i in range(max(0, ORDERS - 3)):
        x = list(base)
        random.Random(20261122 + i).shuffle(x)
        yield x


def _space_totals(run, vid, rows, category, bm) -> dict:
    if len(rows) > MAX_SPACES:
        raise CantCompute(f"{vid} has {len(rows)} spaces, more than this check reads")
    return {r["space_key"]: describe(sim_panel(run, vid, rows, category, bm, r["space_key"])) for r in rows}


def _overlay_keys(code56: str) -> set:
    """space keys with a hand-reviewed rule inside fn56 (package price / fee inside the minimum)."""
    return set(re.findall(r"'(V\d+\|[^']+)'\s*:", code56))


def compute(xano, survivor: str, losers: list[str], *, preview: dict | None = None, hidden_pdfs=(),
            deadline: float = DEADLINE, live_check: bool = True) -> dict:
    """The full impact record. Never raises: anything that can't be reproduced is in `cant` and forces the alert.
    preview: ep578 preview response for the SAME survivor, losers and hide_pdf_ids (best: its own plan is used).
    hidden_pdfs: PDF_IDs the merge hides. Hiding can change which PDF stays Primary, so recompute when a choice changes.
    The live pricing code ignores Show_PDF itself; that is re-checked on every card.
    `stamp` fingerprints the answer: compare it again on Approve and refuse / re-show the card if it moved."""
    t0 = time.monotonic()
    out = {"survivor": survivor, "losers": list(losers), "cant": [], "_why": [], "notes": [], "now": {},
           "after": None, "rooms": [], "filters": None, "assumptions": [], "lost_rules": []}
    reader = None
    try:
        reader = Reader(xano, t0 + deadline)
        _compute(reader, out, survivor, list(losers), preview, list(hidden_pdfs), live_check)
    except CantCompute as e:
        out["cant"].append(str(e))
    except Exception as e:  # noqa: BLE001 - never let a bug or a dropped connection hide a price change
        log.warning("price impact failed: %s", type(e).__name__)
        out["cant"].append(f"could not finish the check ({type(e).__name__})")
    tagged = [("can't compute", f"can't compute: {c}") for c in out["cant"]] + out.pop("_why")
    tagged.sort(key=lambda x: KINDS.index(x[0]))      # stable: the order inside a kind is kept
    out["reasons"] = [t for _, t in tagged]
    out["kinds"] = [k for k in KINDS if any(x[0] == k for x in tagged)]
    out["alert"] = bool(out["reasons"])
    out["stamp"] = hashlib.sha256(json.dumps(
        [[(v, d["total"], d["kind"], d["visible"]) for v, d in sorted(out["now"].items())],
         (out["after"] or {}).get("total"), sorted(out["reasons"]), (out["filters"] or {}).get("after")],
        default=str).encode()).hexdigest()[:16]
    out["seconds"] = round(time.monotonic() - t0, 2)
    out["calls"] = reader.calls if reader else 0
    return out


def _compute(reader, out, survivor, losers, preview, hidden, live_check):
    ids = [survivor] + losers
    if not losers or len(ids) > MAX_LISTINGS or len(set(ids)) != len(ids):
        raise CantCompute("expected one kept listing and one to three different others")
    if preview is not None and (preview.get("survivor") != survivor or set(preview.get("losers") or []) != set(losers)
                                or set(preview.get("hide_pdf_ids") or []) != set(hidden)):
        raise CantCompute("the merge preview is for a different set of listings or hidden PDFs")
    with ThreadPoolExecutor(WORKERS) as ex:
        code_f = ex.submit(live_code, reader)
        tabs = {t: ex.submit(reader.rows, t, ids) for t in (10, 11, 36, 37, 62)}
        live_f = {v: ex.submit(reader.page_payload, v) for v in ids} if live_check else {}
        code = code_f.result()
        T = {t: f.result() for t, f in tabs.items()}
        live, live_err = {}, {}
        for v, f in live_f.items():
            try:
                live[v] = f.result()
            except Exception as e:  # noqa: BLE001
                live_err[v] = type(e).__name__ if not isinstance(e, CantCompute) else str(e)
    vend = {}
    for r in T[11]:
        if r["Vendor_ID"] in vend:
            raise CantCompute(f"two listings share the id {r['Vendor_ID']}")
        vend[r["Vendor_ID"]] = r
    missing = [v for v in ids if v not in vend]
    if missing:
        raise CantCompute("listing not found: " + ", ".join(missing))
    out["names"] = {v: (vend[v].get("Name") or "").strip() for v in ids}
    out["visible"] = {v: visible(vend[v]) for v in ids}
    out["source"] = {f"fn{f}": c["updated_at"] for f, c in code.items()}
    S = vend[survivor]
    cats = {v: str(vend[v].get("Category") or "") for v in ids}

    def by(t, v):
        return [r for r in T[t] if r.get(TABLES[t]) == v]

    given = None
    if preview is not None:
        given = {c["row_id"]: c["new"] for c in preview.get("t10_plan") or [] if c.get("column") == "Current_Assignment"}
    assign = assignments_after(T[10], survivor, losers, given, set(hidden))
    moves = any(r.get("VENDOR_ID") in losers for r in T[36]) or any(r.get("Vendor_ID") in losers for r in T[37])
    relabel = any(assign[p["id"]] != p.get("Current_Assignment") for p in T[10])
    if preview is not None:
        rebuilt = bool((preview.get("rebuild_plan") or {}).get("survivor") or preview.get("touches_pricing"))
    else:
        cap = [float(vend[v].get("Max_Capacity_Seated") or 0) for v in ids]
        rebuilt = moves or relabel or max(cap) > cap[0]
        if relabel:
            out["assumptions"].append("Assumes one Primary PDF after the merge (the kept listing's, unless a duplicate's has a "
                                      "newer year); other Primary PDFs become Past Season")
    plan = {"assign": assign, "rebuilt": rebuilt}
    _filters(out, code, T, vend, survivor, plan)
    if any("venue" not in c.lower() for c in cats.values()):
        with ThreadPoolExecutor(WORKERS) as ex:
            T.update(zip((84, 85, 92, 93), ex.map(lambda t: reader.rows(t, ids), (84, 85, 92, 93))))
        _non_venue(out, T, cats, survivor, losers)
        return

    for fid, name in ((FN_SPACES, "venue_spaces rebuild (fn54)"), (FN_PANEL, "price card (fn56)")):
        if not code[fid]["pin_ok"]:
            raise CantCompute(f"the live {name} changed its database steps since this check was written")
    if hidden and any("Show_PDF" in code[f]["code"] for f in FUNCTIONS):
        raise CantCompute("the live code now looks at hidden PDFs")

    run = engine(code[FN_SPACES]["code"], code[FN_PANEL]["code"])
    states = {"__NATIONAL__"}
    for v in ids:
        toks = [str(t) for t in (vend[v].get("flt_states") or []) if t and not str(t).startswith("country:")]
        states.update(toks[:1])
        states.update(str(t) for r in by(62, v) for t in [x for x in (r.get("state_tokens") or []) if x][:1])
    bm = reader.rows(63, states)

    # ---- today, from the stored rows (what couples see), checked against the live page endpoint
    for v in ids:
        rows = sorted(by(62, v), key=lambda r: r.get("sort_rank") or 0)
        d = describe(sim_panel(run, v, rows, cats[v], bm))
        d["by_guests"] = {g: describe(sim_panel(run, v, rows, cats[v], bm, guests=g))["total"] for g in GUESTS[1:]}
        d["all_spaces"] = _space_totals(run, v, rows, cats[v], bm)
        d["visible"] = out["visible"][v]
        out["now"][v] = d
        if live_check:
            if v in live_err:
                out["cant"].append(f"{v}: the live page could not be read ({live_err[v]})")
            else:
                diff = payload_diff(sim_panel(run, v, rows, cats[v], bm, entitled=False), live[v])
                if diff:
                    out["cant"].append(f"{v}: today's figure does not reproduce the live page ({diff})")

    # ---- after
    def state_of(v):
        return [t for t in (vend[v].get("flt_states") or []) if t and not str(t).startswith("country:")]
    filled = (any(c.get("column") == "State" and c.get("role") == "survivor" for c in preview.get("t11_plan") or [])
              if preview is not None else (not state_of(survivor) and any(state_of(v) for v in losers)))
    if filled:
        out["cant"].append("the merge fills in the kept listing's state, which changes the market comparison")
    pdfs = [dict(p, Vendor_ID=survivor, Current_Assignment=assign[p["id"]]) for p in T[10]]
    s36, l36 = by(36, survivor), [r for v in losers for r in by(36, v)]
    s37, l37 = by(37, survivor), [r for v in losers for r in by(37, v)]
    runs = []
    for a, b in zip(_orders(s36, l36), _orders(s37, l37)):
        # ep578 rebuilds t62 only when price rows move, a PDF is relabelled or capacity/state changes
        rows = (sim_spaces(run, survivor, a, b, pdfs, S) if rebuilt
                else sorted(by(62, survivor), key=lambda r: r.get("sort_rank") or 0))
        runs.append((rows, describe(sim_panel(run, survivor, rows, cats[survivor], bm))))
    rows, after = runs[0]
    if len({(d["total"], d["kind"]) for _, d in runs}) > 1:
        seen = sorted({d["total"] for _, d in runs if d["total"] is not None})
        out["cant"].append("the result depends on database row order (" + " or ".join(money(x) for x in seen) + ")")
    out["order_sensitive_rooms"] = len({json.dumps(sorted((r["space_key"], r["venue_fee"], r["fb_min"], r["pp_fb"],
                                        r["ceremony_fee"], r["service_pct"]) for r in x)) for x, _ in runs}) > 1
    if out["order_sensitive_rooms"]:
        out["cant"].append("some rooms or their figures depend on database row order")
    after["by_guests"] = {g: describe(sim_panel(run, survivor, rows, cats[survivor], bm, guests=g))["total"]
                          for g in GUESTS[1:]}
    after["all_spaces"] = _space_totals(run, survivor, rows, cats[survivor], bm)
    after["visible"] = any(out["visible"].values())
    after["rows"] = rows
    out["after"] = after

    # ---- the rebuild on its own: is part of the change already waiting (stored rows out of date)?
    alone = describe(sim_panel(run, survivor, sim_spaces(run, survivor, s36, s37, by(10, survivor), S),
                               cats[survivor], bm))
    out["rebuild_alone"] = alone["total"]
    if alone["total"] != out["now"][survivor]["total"]:
        out["notes"].append(f"{survivor}'s stored price is out of date: rebuilding it alone, without the merge, gives "
                            f"{alone['text']} ({alone.get('space')})")
    _rooms(out, T, rows, by, survivor, losers)
    _overlays(out, code[FN_PANEL]["code"], by, rows, ids)
    _reasons(out, by, survivor, losers)


def payload_diff(sim: dict, live: dict) -> str:
    """'' when the simulated payload equals the live one on everything a couple can read about price."""
    if bool(sim.get("found")) != bool(live.get("found")):
        return f"found {sim.get('found')} vs {live.get('found')}"
    if sim.get("is_venue") != live.get("is_venue"):
        return "is_venue differs"
    if not sim.get("found"):
        return ""
    for k in ("selected_space_key", "spaces_headline"):
        if sim.get(k) != live.get(k):
            return f"{k}: {sim.get(k)} vs {live.get(k)}"
    a, b = sim.get("space") or {}, live.get("space") or {}
    for k in ("all_in", "band", "components", "guest_floor", "contract_count"):
        if _canon(a.get(k)) != _canon(b.get(k)):
            return f"{k} differs"
    if _canon(sim.get("spaces")) != _canon(live.get("spaces")):
        return "spaces differ"
    return ""


def _canon(v):
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 6)
    if isinstance(v, list):
        return [_canon(x) for x in v]
    return {k: _canon(x) for k, x in v.items()}


def _add(out, kind, text):
    out["_why"].append((kind, text))


def _filters(out, code, T, vend, survivor, plan):
    if not code[FN_FILTERS]["pin_ok"]:
        out["cant"].append("the live filter code (fn21) changed since this check was written")
        return
    primary = {p["PDF_ID"] for p in T[10] if plan["assign"][p["id"]] == "Primary"}
    now = {k: float(vend[survivor].get(k) or 0) for k in FLT}
    after = sim_filters(T[36], primary) if plan["rebuilt"] else now
    changed = [k for k in FLT if abs(now[k] - float(after[k])) > 0.005]
    out["filters"] = {"now": now, "after": after, "changed": changed}
    if not out["visible"][survivor]:        # nobody could filter to it today; the none -> some line covers it
        return
    for k in changed:
        a, b = now[k], float(after[k])
        if a == 0 or b == 0 or _big(_pct(a, b)):
            _add(out, "search filter", f"search filter, {FLT_LABEL[k]}: {money(a) if a else 'none'} -> "
                                  f"{money(b) if b else 'none'}")


def _non_venue(out, T, cats, survivor, losers):
    """No price card on non-venue pages. If no price row moves, nothing can change; otherwise it is not simulated."""
    moving = sum(1 for t in (36, 37, 84, 85, 92, 93) for r in T[t] if r.get(TABLES[t]) in losers)
    out["kind"] = "non-venue"
    if len({c.lower() for c in cats.values()}) > 1:
        out["cant"].append("the listings are in different categories (" + ", ".join(sorted(set(cats.values()))) + ")")
    if moving:
        out["cant"].append(f"{cats[survivor] or 'this'} pages are not simulated, and {moving} price rows would move to the "
                           "kept listing")
    elif not out["visible"][survivor] and any(out["visible"].values()):
        _add(out, "price shown or not", f"{survivor} (kept) is hidden today and becomes visible")
    else:
        out["notes"].append("No price rows move. This is not a venue, so there is no price card")


def _rooms(out, T, rows, by, survivor, losers):
    """Rooms whose figures come from more than one listing, and every field the listings disagree on."""
    owner = {p["PDF_ID"]: p["Vendor_ID"] for p in T[10]}
    pdf = {p["PDF_ID"]: p for p in T[10]}
    before = {v: by(62, v) for v in [survivor] + losers}
    for r in rows:
        src = {}
        for p in r.get("source_pdf_ids") or []:
            src.setdefault(owner.get(p, "?"), []).append(p)
        if len(src) < 2:
            continue
        item = {"room": r["space_label"], "space_key": r["space_key"], "from": {}, "conflicts": []}
        match = {}
        for v, ps in src.items():
            item["from"][v] = [f"{p} ({_year(pdf.get(p))})" for p in ps]
            match[v] = [b for b in before.get(v, []) if set(b.get("source_pdf_ids") or []) & set(ps)
                        and (b["space_slug"] in r["space_slug"] or r["space_slug"] in b["space_slug"])]
        for f, label in MONEY_FIELDS:
            vals = {v: sorted({b.get(f) for b in bs if b.get(f)}) for v, bs in match.items()}
            if len([1 for xs in vals.values() if xs]) > 1 and len({x for xs in vals.values() for x in xs}) > 1:
                item["conflicts"].append({"field": label, "listings": vals, "after": r.get(f)})
        out["rooms"].append(item)


def _overlays(out, code56, by, rows, ids):
    """A hand-reviewed rule in fn56 is tied to one space key; a merge can change the key and drop the rule."""
    new = {r["space_key"] for r in rows}
    lost = [k for k in sorted(_overlay_keys(code56)) if k.split("|")[0] in ids and k not in new
            and any(r["space_key"] == k for r in by(62, k.split("|")[0]))]
    out["lost_rules"] = lost
    if lost:
        _add(out, "hand-reviewed rule", "a hand-reviewed price rule (package price, or fee inside the minimum) is tied to a listing or "
                              "room name this merge changes, so it would stop applying: " + ", ".join(lost))


def _same_room(row, after_rows):
    """The after-merge row a room ends up in: same name, else the one row fn54 merged its spelling into. None = gone."""
    exact = [r for r in after_rows if r["space_slug"] == row["space_slug"]]
    if exact:
        return exact[0]
    pdfs = set(row.get("source_pdf_ids") or [])
    near = [r for r in after_rows if pdfs & set(r.get("source_pdf_ids") or []) and len(row["space_slug"]) >= 4
            and (row["space_slug"] in r["space_slug"] or r["space_slug"] in row["space_slug"])]
    return near[0] if len(near) == 1 else None


def _reasons(out, by, survivor, losers):
    after = out["after"]
    none_after = after["kind"] == "none" or not after["visible"]
    for v in [survivor] + losers:
        n = out["now"][v]
        who = f"{v} ({'kept' if v == survivor else 'merged away'})"
        none_now = not n["visible"] or n["kind"] == "none"
        n["change"] = None
        if none_now and none_after:
            continue
        if none_now or none_after:
            if v == survivor or n["visible"]:      # a hidden loser nobody saw is no change for couples
                _add(out, "price shown or not",
                     f"{who}: {'no price shown (hidden)' if not n['visible'] else n['text']} -> "
                     f"{'no price shown' if none_after else after['text']}")
            continue
        p = n["change"] = _pct(n["total"], after["total"])
        if n["kind"] != after["kind"]:
            _add(out, "own figures or estimate", f"{who}: {_kind(n['kind'])} -> {_kind(after['kind'])}")
        if _big(p):
            _add(out, "opening price", f"{who}: opening price {money(n['total'])} -> {money(after['total'])} ({_signed(p)})")
        else:
            for g in GUESTS[1:]:
                a, b = n["by_guests"][g], after["by_guests"][g]
                if _big(_pct(a, b)):
                    _add(out, "guest count", f"{who}: at {g} guests {money(a)} -> {money(b)} ({_signed(_pct(a, b))}); "
                                             f"{_signed(p)} at {n['guests']} guests")
        _room_changes(out, by(62, v), v, who, v == survivor)


def _room_changes(out, rows, v, who, kept):
    """Every room couples can pick on this listing today: where it goes, and what it costs there."""
    n, after = out["now"][v], out["after"]
    for r in sorted(rows, key=lambda x: x.get("sort_rank") or 0):
        d, opens = n["all_spaces"][r["space_key"]], r["space_key"] == n.get("space_key")
        to = _same_room(r, after["rows"])
        opening_after = bool(to) and to["space_key"] == after.get("space_key")
        renamed = bool(to) and to["space_slug"] != r["space_slug"]
        if opens and (not opening_after or renamed):
            t = f"{who}: opens on {d['space']} today, on {after.get('space')} after"
            t += f"; {d['space']} is gone" if to is None else f"; {d['space']} is combined into {to['space_label']}" if renamed else ""
            if kept:
                _add(out, "opening room", t)
            else:
                out["notes"].append(t)
        if to is None:
            _add(out, "room", f"{who}: room {d['space']} ({money(d['total'])}) is no longer listed under that name")
            continue
        if renamed and not (opens and kept):        # fn54 joins look-alike names: they can be two different rooms
            _add(out, "room", f"{who}: room {d['space']} is no longer listed; combined into {to['space_label']}")
        b = after["all_spaces"][to["space_key"]]["total"]
        if opens and opening_after:
            continue                        # this is the opening price, already compared above
        if _big(_pct(d["total"], b)):
            _add(out, "room", f"{who}: room {d['space']} {money(d['total'])} -> {money(b)} ({_signed(_pct(d['total'], b))})")


def _kind(k):
    return {"market": "market estimate only", "venue": "the venue's own figures", "none": "no price"}[k]


def _signed(p):
    return "from $0" if p is None else f"{p:+.1f}%"


# ------------------------------------------------------------------------------------------------------ the card

def _esc(s) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("*", "").replace("`", "")


PREDICTED = "Predicted from stored data, not checked against the PDFs"


def _facts(d):
    extra = [f"exact {money(d['total'])}", f"{d['guests']} guests", "opens on " + _esc(d["space"])]
    if d["spaces"] > 1:
        extra.append(f"{d['spaces']} rooms")
    if d["kind"] == "market":
        extra.append("all market estimate")
    elif d["estimated"]:
        extra.append(f"{len(d['estimated'])} of 4 costs estimated")
    return f"({', '.join(extra)})"


def _line(v, d, tag, after):
    """One listing: today's price next to the predicted one."""
    today = ("hidden, no price shown" if not d["visible"] else
             d["text"] if d["kind"] == "none" else f"*{d['text']}* {_facts(d)}")
    if after is None:
        return f"• `{v}` {tag}: today {today}; predicted: can't compute"
    then = ("no price shown" if after["kind"] == "none" or not after["visible"] else f"*{after['text']}*")
    ch = d.get("change")
    moved = "" if ch is None else ", same total" if d["total"] == after["total"] else f", {_signed(ch)}"
    return f"• `{v}` {tag}: today {today} -> predicted {then}{moved}"


def blocks(r: dict) -> dict:
    """{extra_blocks, confirm_text, alert} for the merge card. confirm_text is None when no confirm step is needed."""
    a = r.get("after")
    lines = [f"*{PREDICTED}.*"] + [_line(v, r["now"][v], "kept" if v == r["survivor"] else "merged away", a)
                                  for v in [r["survivor"]] + r["losers"] if v in r["now"]]
    if a:
        n = r["now"][r["survivor"]]
        if a["kind"] != "none":
            lines.append(f"• Kept listing after the merge: *{a['text']}* {_facts(a)}")
        if a.get("position") != n.get("position"):
            lines.append(f"• Place on the market scale: {_esc(n.get('position') or 'not placed')} -> "
                         f"{_esc(a.get('position') or 'not placed')}")
        if a.get("planner") != n.get("planner"):
            lines.append(f"• Planner total at 100 guests: {money(n['planner']) if n.get('planner') else 'none'} -> "
                         f"{money(a['planner']) if a.get('planner') else 'none'}")
    f = r.get("filters")
    if f and f["changed"]:
        lines.append("• Search filters: " + "; ".join(f"{FLT_LABEL[k]} {money(f['now'][k])} -> {money(f['after'][k])}"
                                                     for k in f["changed"]))
    for m in r["rooms"][:4]:
        frm = "; ".join(f"`{v}` {', '.join(p)}" for v, p in m["from"].items())
        lines.append(f"• Same room from both: {_esc(m['room'])} ({frm})")
        for c in m["conflicts"][:3]:
            vals = "; ".join(f"`{v}` {'/'.join(f'{x:,.10g}' for x in xs)}" for v, xs in c["listings"].items() if xs)
            lines.append(f"    - {c['field']}: {vals} -> {float(c['after'] or 0):,.10g} after")
    if len(r["rooms"]) > 4:
        lines.append(f"• and {len(r['rooms']) - 4} more rooms combined")
    lines += ["• " + _esc(x) for x in r["assumptions"] + r["notes"][:6]]
    if len(r["notes"]) > 6:
        lines.append(f"• and {len(r['notes']) - 6} more notes")
    head = ("PRICE ALERT: " + ", ".join(r["kinds"])) if r["alert"] else "Price couples see"
    out = [{"type": "header", "text": {"type": "plain_text", "text": head[:150]}}]
    if r["alert"]:
        why = r["reasons"][:8] + ([f"and {len(r['reasons']) - 8} more"] if len(r["reasons"]) > 8 else [])
        out.append(_sec("*Why you are asked to confirm*\n" + "\n".join("• " + _esc(x) for x in why)))
    out.append(_sec("\n".join(lines)))
    out.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
               PREDICTED + ". Before tax and gratuity, at the guest count the page opens on. Pages can keep showing a stored "
               "figure for up to 4 hours."}]})
    confirm = None
    if r["alert"]:
        # clipped before escaping so an entity is never cut; Slack allows 300 characters
        raw = f"Alert on: {', '.join(r['kinds'])}. {r['reasons'][0]}"
        confirm = _esc(raw if len(raw) <= 200 else raw[:197] + "...") + " Merge anyway?"
    return {"extra_blocks": out, "confirm_text": confirm, "alert": r["alert"]}


def _sec(t):
    return {"type": "section", "text": {"type": "mrkdwn", "text": t if len(t) <= 2900 else t[:2880] + "\n(cut)"}}
