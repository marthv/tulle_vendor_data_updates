"""Duplicate-PDF check for a vendor merge (owner design 2026-11-22; bar = PASS_BAR.md section 3 + Amendment 1).

check(xano, survivor, losers) reads the table-10 rows of the listings on a merge card (within one listing too) and
compares every pair of VISIBLE PDFs. Read-only. Types:
  EXACT        same Drive file, or two files with the same Drive md5                 -> pre-selected to hide (untickable)
  SAME_TEXT    re-saved: every word and number the same, in the same order           -> needs its own click
  SAME_PRICES  new design: same pricing year and EVERY price on file identical       -> needs its own click
  SUBSET       an excerpt: every phrase of the shorter one is in the longer one      -> needs its own click
  SAME_LINK    both open the same web page (not a Drive file)                        -> needs its own click
Except for EXACT (one file can't be two years), a different pricing year, or any price that differs, means KEEP BOTH.
Missing data means "can't tell", never a flag.
A changed number, day, or word order is never "the same text". What is compared without Drive access: the file id in the
link, and the prices already extracted (t36/37/84/85/87/92/93). Stored prices cover only part of a PDF, so SAME_PRICES
without the text needs more matching prices, matching room/package names, and says on the card the PDFs weren't read.

Which copy stays: the PDF that will be the listing's Primary after the merge (the kept listing's own), then newer year,
more pages, more extracted rows, older row. A copy that stays is never itself on the hide list, so at least one always
stays visible; hide_ids() re-checks that at approve time, recheck_live() re-reads the rows just before the merge.
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import logging
import re
import threading
import time
import unicodedata

import requests

import drive_ro
import xano_search

log = logging.getLogger("slack_qa.pdf_dupes")

CHECK_SECONDS = 40            # whole check; the card posts with "PDF check unavailable" after this
MAX_FLAGS = 8                 # more than this on one card: the rest are listed as "not offered" (kept)
MAX_PDFS = 24
MIN_TOKENS, MIN_MONEY = 120, 3        # a text layer with less than this can't prove two PDFs are the same
MIN_PRICES_TEXT, MIN_PRICES_NO_TEXT = 3, 5
TYPE_LABEL = {"EXACT": "Exact copy of the same file", "SAME_TEXT": "Same PDF saved again",
              "SAME_PRICES": "Possibly the same prices in a new design", "SUBSET": "An excerpt of the longer PDF",
              "SAME_LINK": "Both open the same web page"}

# --- prices on file -----------------------------------------------------------------------------------------
# table -> (label columns, dollar columns, other numbers that change what a couple pays, year column)
PRICE_TABLES = {
    36: (("Venue_Space_Name", "Package_Name"),
         ("Ceremony_Fee", "Venue_Fee_on_a_Peak_Season_Saturday", "Food_and_Beverage_Min_on_a_Peak_Season_Saturday",
          "Per_Person_Food_and_Beverage_on_a_Peak_Season_Saturday", "Venue_Fee_on_Lowest_Saturday",
          "Food_and_Beverage_Min_on_Lowest_Saturday", "Per_Person_Food_and_Beverage_on_Lowest_Saturday",
          "Base_Menu_Fee_Per_Person", "Base_Bar_Package_Per_Person", "Package_Price", "Hourly_Rate",
          "Overtime_Hourly_Rate", "Addons_Detail"),
         ("Admin_Service_Fee", "Guest_Min_Highest_Sat", "Guest_Min_Lowest_Sat", "Hours_Included"), "Pricing_Year"),
    37: (("Venue_Space_Name",),
         ("Venue_Fee", "FB_Min", "Per_Person_FB", "Base_Menu_Per_Person", "Base_Bar_Per_Person", "Ceremony_Fee"),
         ("Admin_Fee_Pct", "Tax_Pct", "Service_Fee_Pct", "Guest_Min", "Guest_Max"), None),
    84: ((), ("min_spend", "typical_spend_low", "typical_spend_high", "total", "delivery_setup_amount",
              "labor_amount", "rentals_amount", "service_fee_amount", "breakdown_amount"),
         ("service_fee_pct", "tax_pct", "deposit_pct", "delivery_setup_pct"), "pricing_year"),
    85: (("florist_item_name",), ("price_low", "price_high", "line_total"), ("quantity",), None),
    87: ((), ("small_wedding_price_from", "outside_caterer_fee_amount", "required_fees"),
         ("tax_pct", "admin_service_fee_pct", "gratuity_pct", "off_peak_discount_pct"), None),
    92: ((), ("minimum_spend", "typical_spend_low", "typical_spend_high", "total"),
         ("guest_minimum", "service_charge_pct", "gratuity_pct", "tax_rate_pct"), "pricing_year"),
    93: (("item_name",), ("price_low", "price_high"), ("hours", "min_quantity"), None),
}
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def nums(v) -> set[float]:
    """Every non-zero number in a stored value (0 / blank = not stated)."""
    if v is None or isinstance(v, bool):
        return set()
    if isinstance(v, (int, float)):
        return {round(float(v), 2)} if v else set()
    if isinstance(v, list):                      # t87 required_fees: [{name, amount, ...}]
        return set().union(*[nums(x.get("amount") if isinstance(x, dict) else x) for x in v]) if v else set()
    out = set()
    for m in _NUM.findall(str(v)):
        f = float(m.replace(",", ""))
        if f:
            out.add(round(f, 2))
    return out


def years(v) -> frozenset:
    """{"2025"} from "2025", "2,025", "2025-2026"; empty = not stated ("No date listed", blank, junk)."""
    return frozenset(re.findall(r"(?<!\d)20[12345]\d(?!\d)", re.sub(r"(?<=\d),(?=\d{3}\b)", "", str(v or ""))))


def _label(v) -> str:
    return " ".join(sorted(re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).split()))


class Prices:
    """Everything priced on file for one PDF."""

    def __init__(self):
        self.by_col: dict[tuple, set] = collections.defaultdict(set)     # (table, column) -> values
        self.rows: set[tuple] = set()                                    # (table, label, column, value)
        self.dollars: set[float] = set()
        self.years: set[str] = set()
        self.n = 0

    def add(self, tid: int, row: dict) -> None:
        labels, money, other, ycol = PRICE_TABLES[tid]
        self.n += 1
        lab = " / ".join(_label(row.get(c)) for c in labels)
        for c in money + other:
            vals = nums(row.get(c))
            if not vals:
                continue
            self.by_col[(tid, c)] |= vals
            self.rows |= {(tid, lab, c, v) for v in vals}
            if c in money:
                self.dollars |= vals
        if ycol:
            self.years |= years(row.get(ycol))


def _fmt(vals) -> str:
    return ", ".join(f"{v:,.0f}" if v == int(v) else f"{v:,}" for v in sorted(vals)[:4]) or "none"


def _price_diff(a: Prices, b: Prices, k) -> str:
    x, y = a.by_col.get(k, set()), b.by_col.get(k, set())
    return f"{k[1].replace('_', ' ').lower()} on file differs ({_fmt(x - y)} vs {_fmt(y - x)})"


def compare_prices(a: Prices, b: Prices) -> tuple[str, str]:
    """("same" | "differ" | "cant", plain reason). Same = every column that has a value on either PDF has exactly the
    same values on the other."""
    if not a.dollars or not b.dollars:
        return "cant", "one of them has no prices on file yet"
    for k in sorted(set(a.by_col) & set(b.by_col)):
        if a.by_col[k] != b.by_col[k]:
            return "differ", _price_diff(a, b, k)
    if {t for t, _c in a.by_col} != {t for t, _c in b.by_col}:
        return "cant", "they were read by different extractions, so the prices on file can't be lined up"
    for k in sorted(set(a.by_col) ^ set(b.by_col)):
        return "differ", _price_diff(a, b, k)
    return "same", f"all {len(a.dollars)} different dollar amounts on file"


# --- text ----------------------------------------------------------------------------------------------------
_TOK = re.compile(r"\d[\d,]*(?:\.\d+)?|[^\W\d_]+")
_MONEY = re.compile(r"[$£€]\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s?(?:usd\b|dollars\b|per person\b|/person\b|pp\b)",
                    re.I)
# words that change what a price means: if one of these is only on one side, it isn't "the same text"
GUARD = set("""not no non never without except excluding excludes exclude excluded includes include included including
inclusive exclusive plus additional extra only minimum maximum min max per person guest guests each waived discount
monday tuesday wednesday thursday friday saturday sunday mon tue wed thu fri sat sun weekday weekend holiday january
february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct
nov dec spring summer fall autumn winter peak off low high season daytime evening brunch lunch dinner ceremony reception
tax service gratuity deposit draft expired revised updated sample corporate social one two three four five six seven eight
nine ten eleven twelve hundred thousand half double""".split())


class Text:
    """Normalised text of one PDF. Pages are split on form feeds when the source gives them."""

    def __init__(self, raw: str, pages: int = 0):
        t = unicodedata.normalize("NFKC", raw or "").replace("­", "").lower()
        t = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", t)
        self.page_toks = [[self._n(x) for x in _TOK.findall(p)] for p in t.split("\f")]
        self.toks = [x for p in self.page_toks for x in p]
        self.pages = pages
        self.money = {round(float((m[0] or m[1]).replace(",", "")), 2) for m in _MONEY.findall(t)}
        self.years = frozenset(re.findall(r"(?<!\d)20[12345]\d(?!\d)", t))
        self.joined = "".join(self.toks)

    @staticmethod
    def _n(tok: str) -> str:
        if tok[0].isdigit():
            tok = tok.replace(",", "")
            if "." in tok:
                tok = tok.rstrip("0").rstrip(".")
            return "#" + tok + "#"              # marks keep "1" + "50" from reading as "150"
        return tok

    @property
    def usable(self) -> bool:
        return len(self.toks) >= MIN_TOKENS and len(self.money) >= MIN_MONEY

    def shingles(self, k: int = 5) -> set[tuple]:
        return {tuple(p[i:i + k]) for p in self.page_toks for i in range(len(p) - k + 1)}


def _insertions(small: list[str], big: list[str]) -> list[str] | None:
    """The tokens `big` has on top of `small` when `small` reads through `big` in order, else None."""
    i, extra = 0, []
    for t in big:
        if i < len(small) and t == small[i]:
            i += 1
        else:
            extra.append(t)
    return extra if i == len(small) else None


def same_text(a: Text, b: Text) -> tuple[bool, str]:
    """True only when every word and number matches in order, allowing a handful of extra plain words (a footer, a file
    name) on one side; never an extra number, day, month or word that changes a price's meaning."""
    if a.pages and b.pages and a.pages != b.pages:
        return False, f"{a.pages} pages vs {b.pages}"
    if a.joined == b.joined:
        return True, "every word and number is the same, in the same order"
    s, g = (a, b) if len(a.toks) <= len(b.toks) else (b, a)
    extra = _insertions(s.toks, g.toks)
    if extra is not None and len(extra) <= min(10, max(2, len(s.toks) // 100)) \
            and not any(e.startswith("#") or e in GUARD for e in extra):
        return True, f"same except {len(extra)} extra word{'s' if len(extra) != 1 else ''} ({', '.join(extra[:5])})"
    na, nb = [t for t in a.toks if t[0] == "#"], [t for t in b.toks if t[0] == "#"]
    if na != nb:
        return False, "the numbers in them differ"
    return False, "the wording differs"


def is_subset(small: Text, big: Text) -> bool:
    """Every 5-word phrase on every page of `small` is in `big`, and `big` is clearly longer."""
    if len(small.toks) > 0.9 * len(big.toks):
        return False
    s = small.shingles()
    return len(s) >= MIN_TOKENS // 3 and s <= big.shingles()


# --- one PDF, one pair ---------------------------------------------------------------------------------------
class Pdf:
    def __init__(self, row: dict):
        self.row = row
        self.pid = str(row.get("PDF_ID") or "").strip()
        self.vid = str(row.get("Vendor_ID") or "").strip()
        self.rid = int(row.get("id") or 0)
        self.link = str(row.get("PDF_Link") or "").strip()
        self.fid = drive_ro.file_id(self.link)
        self.years = years(row.get("Year_of_Pricing"))
        self.visible = row.get("Show_PDF") is not False and bool(self.link)      # ep578 counts visible the same way
        self.assignment = str(row.get("Current_Assignment") or "").strip()
        self.is_primary = self.assignment.lower() == "primary"
        self.extracted = str(row.get("extraction_status") or "") == "extracted"
        self.unique = True             # False: another table-10 row has the same PDF_ID, so it can't be hidden safely
        self.md5 = ""
        self.text: Text | None = None
        self.prices = Prices()

    def year_text(self) -> str:
        return "-".join(sorted(self.years)) or "no year"

    def public(self) -> dict:
        return {"pdf_id": self.pid, "vendor_id": self.vid, "row_id": self.rid, "link": self.link,
                "label": self.assignment or "PDF", "year": str(self.row.get("Year_of_Pricing") or ""),
                "visible": self.visible}


def _out(verdict, type_=None, matched="", differed="", conf="", subset="") -> dict:
    return {"verdict": verdict, "type": type_, "matched": matched, "differed": differed, "confidence": conf,
            "subset": subset}


def compare(a: Pdf, b: Pdf) -> dict:
    """{"verdict": dup|keep|cant, "type", "matched", "differed", "confidence", "subset": the shorter one's PDF_ID}"""
    conflict = bool(a.years and b.years and a.years != b.years)
    same_file = bool(a.fid and a.fid == b.fid)
    if same_file or (a.md5 and a.md5 == b.md5):
        why = "both open the same Google Drive file" if same_file else "the two files are identical, byte for byte"
        # owner 2026-11-22: one file can't be two years, so identical files are EXACT whatever our year labels say. The
        # year rule is for the other types. decide() spells out which label stays.
        return _out("dup", "EXACT", why, "nothing", "certain")
    if conflict:
        return _out("keep", None, "", f"different years ({a.year_text()} vs {b.year_text()})")
    if a.link and a.link == b.link and not a.fid:
        return _out("dup", "SAME_LINK", "both links are the same web page",
                    "nothing" if a.years and b.years else "one has no pricing year", "high")
    if not a.years or not b.years:
        return _out("cant", None, "", "no pricing year on one of them")
    ta, tb = a.text, b.text
    both_text = bool(ta and tb and ta.usable and tb.usable)
    if both_text:
        same, detail = same_text(ta, tb)
        if same:
            return _out("dup", "SAME_TEXT", f"pricing year {a.year_text()}; {detail}", "nothing that affects a price",
                        "high")
        for s, g in ((a, b), (b, a)):
            if is_subset(s.text, g.text):
                return _out("dup", "SUBSET", f"pricing year {a.year_text()}; every line of the shorter one is in the "
                            "longer one", "the longer one has more in it", "high", subset=s.pid)
    if not (a.extracted and b.extracted):
        return _out("cant", None, "", "one of them hasn't been read for prices yet")
    if a.prices.years and b.prices.years and a.prices.years != b.prices.years:
        return _out("keep", None, "", "the prices inside are for different years "
                    f"({', '.join(sorted(a.prices.years))} vs {', '.join(sorted(b.prices.years))})")
    verdict, why = compare_prices(a.prices, b.prices)
    if verdict != "same":
        return _out("keep" if verdict == "differ" else "cant", None, "", why)
    if ta and tb and ta.money and tb.money and ta.money != tb.money:       # even when the text is too short to prove
        return _out("keep", None, "", f"dollar amounts printed in the PDFs differ "          # sameness, it can disprove it
                    f"({_fmt(ta.money - tb.money)} vs {_fmt(tb.money - ta.money)})")
    if both_text:
        if ta.years and tb.years and ta.years != tb.years:
            return _out("keep", None, "", "the years printed in the PDFs differ")
        if len(a.prices.dollars) < MIN_PRICES_TEXT:
            return _out("cant", None, "", "too few prices to be sure")
        return _out("dup", "SAME_PRICES", f"pricing year {a.year_text()}; {why}; and every dollar amount printed in "
                    "both PDFs", "the design or wording", "high")
    if len(a.prices.dollars) < MIN_PRICES_NO_TEXT:
        return _out("cant", None, "", "too few prices on file to be sure")
    if a.prices.rows != b.prices.rows:
        return _out("cant", None, "", "the same amounts, but not on the same rooms or packages")
    return _out("dup", "SAME_PRICES", f"pricing year {a.year_text()}; {why}, on the same rooms or packages",
                "the PDFs themselves were not read, so a price not on file could differ: open both before hiding",
                "medium")


# --- which copy stays --------------------------------------------------------------------------------------------
def post_merge_primary(pdfs: list[Pdf], survivor: str, losers: list[str] | None = None) -> str:
    """PDF_ID that is the visible Primary once merged, by ep578 v2's own rule: the kept listing's, else the first
    duplicate's (card order), then newer year, then older row."""
    order = [survivor] + list(losers or [])
    prim = [p for p in pdfs if p.visible and p.is_primary]
    prim.sort(key=lambda p: (order.index(p.vid) if p.vid in order else len(order),
                             -max((int(y) for y in p.years), default=0), p.rid))
    return prim[0].pid if prim else ""


def keep_key(p: Pdf, primary: str):
    y = max((int(x) for x in p.years), default=0)
    return (0 if p.pid and p.pid == primary else 1, -y, -(p.text.pages if p.text else 0), -p.prices.n, p.rid)


def decide(pdfs: list[Pdf], survivor: str, results: dict, losers: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Flags from the pair verdicts. Each flag's keep copy is never hidden by any other flag."""
    notes: list[str] = []
    primary = post_merge_primary(pdfs, survivor, losers)
    kept: list[Pdf] = []
    flags: list[dict] = []

    def res(a, b):
        return results.get(frozenset((a.rid, b.rid))) or {}

    def flag(keep, hide, r):
        if not hide.pid or not hide.unique:
            notes.append("one likely duplicate can't be hidden safely: its PDF id is blank or shared with another file")
            return False
        differed = r["differed"]
        if r["type"] in ("EXACT", "SAME_LINK") and keep.years != hide.years:
            differed = (f"our year labels: the copy that stays is labelled {keep.year_text()} and keeps that label, the "
                        f"hidden one is labelled {hide.year_text()}. Check which year is right")
        flags.append({"keep_pdf_id": keep.pid, "hide_pdf_id": hide.pid, "type": r["type"], "what_matched": r["matched"],
                      "what_differed": differed, "year_label_differs": keep.years != hide.years, "confidence": r["confidence"], "keep": keep.public(),
                      "hide": hide.public(), "choice": "hide" if r["type"] == "EXACT" else "keep",
                      "reason": f"{TYPE_LABEL[r['type']]}: {r['matched']}."})
        return True

    for p in sorted((p for p in pdfs if p.visible), key=lambda p: keep_key(p, primary)):
        hits = [(k, res(k, p)) for k in kept if res(k, p).get("verdict") == "dup" and res(k, p)["type"] != "SUBSET"]
        hits.sort(key=lambda h: list(TYPE_LABEL).index(h[1]["type"]))         # the strongest match explains the flag
        if not (hits and flag(hits[0][0], p, hits[0][1])):
            kept.append(p)
    used = {f["keep_pdf_id"] for f in flags}
    gone: set[int] = set()
    for p in sorted(kept, key=lambda p: len(p.text.toks) if p.text else 0):
        big = next((k for k in kept if k is not p and k.rid not in gone and res(k, p).get("type") == "SUBSET"
                    and res(k, p).get("subset") == p.pid), None)
        if not big or p.pid in used:
            continue
        if p.pid == primary:
            notes.append("the Primary PDF is an excerpt of a longer one on these listings (kept: the Primary is never "
                         "hidden)")
            continue
        if flag(big, p, res(big, p)):
            gone.add(p.rid)
            used.add(big.pid)
    flags.sort(key=lambda f: (list(TYPE_LABEL).index(f["type"]), f["hide_pdf_id"]))
    if len(flags) > MAX_FLAGS:
        notes.append(f"{len(flags) - MAX_FLAGS} more likely duplicates aren't offered on this card (kept)")
        flags = flags[:MAX_FLAGS]
    return flags, notes


# --- the check ------------------------------------------------------------------------------------------------
def check(xano, survivor: str, losers: list[str], drive=None, seconds: float = CHECK_SECONDS) -> dict:
    """The whole check. May raise; callers use safe_check()."""
    deadline = time.time() + seconds
    vids = [v for v in dict.fromkeys([survivor] + list(losers)) if v]
    rows = xano_search.search(xano, 10, "Vendor_ID", vids)
    pdfs = [Pdf(r) for r in {int(r["id"]): r for r in rows}.values()]
    if len(pdfs) > MAX_PDFS:
        return _unavailable(f"there are {len(pdfs)} PDFs on these listings, too many to compare here")
    vis = [p for p in pdfs if p.visible]
    out = {"status": "ok", "n": len(pdfs), "visible": len(vis), "flags": [], "notes": [], "compared": 0, "keep": 0,
           "cant": 0, "files": "no_access", "text": "no_access", "pairs": [],
           "pdfs": [p.public() for p in pdfs], "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if len(vis) < 2:
        return out
    ids = [p.pid for p in pdfs if p.pid]
    seen = collections.Counter(str(r.get("PDF_ID") or "").strip() for r in xano_search.search(xano, 10, "PDF_ID", ids))
    for p in pdfs:
        p.unique = bool(p.pid) and seen.get(p.pid) == 1
    by_pid = {p.pid: p for p in vis if p.unique}
    try:
        for tid in PRICE_TABLES:
            if time.time() > deadline:
                raise TimeoutError("prices")
            # t36 has no index on PDF_ID (8-20 s a read, measured 2026-11-22); VENDOR_ID is indexed (0.1-0.4 s)
            col, vals = ("VENDOR_ID", vids) if tid == 36 else ("PDF_ID", list(by_pid))
            for r in xano_search.search(xano, tid, col, vals):
                p = by_pid.get(str(r.get("PDF_ID") or "").strip())
                if p is not None:
                    p.prices.add(tid, r)
        out["prices"] = "checked"
    except Exception as e:  # noqa: BLE001 - the file checks still run; no price is compared on a partial read
        log.warning("pdf check: prices not read: %s", type(e).__name__)
        out["prices"] = "failed"
        for p in pdfs:
            p.prices = Prices()
    if drive is not None:
        out["files"], out["text"] = _read_files(drive, vis, deadline)
    results: dict = {}
    for i, a in enumerate(vis):
        for b in vis[i + 1:]:
            r = compare(a, b)
            results[frozenset((a.rid, b.rid))] = r
            out["compared"] += 1
            if r["verdict"] in ("keep", "cant"):
                out[r["verdict"]] += 1
            out["pairs"].append({"a": a.pid, "b": b.pid, "verdict": r["verdict"], "type": r["type"],
                                 "why": r["differed"] if r["verdict"] != "dup" else r["matched"]})
    out["flags"], notes = decide(pdfs, survivor, results, vids[1:])
    out["notes"] = list(dict.fromkeys(out["notes"] + notes))
    for n, f in enumerate(out["flags"], 1):
        f["n"] = n
    return out


def _read_files(drive, vis: list[Pdf], deadline: float) -> tuple[str, str]:
    """md5 for every Drive PDF, then text for the ones that share a pricing year. A failure = that file not checked."""
    want = [p for p in vis if p.fid]
    if not want:
        return "none", "none"

    def meta(p):
        p.md5 = str(drive.meta(p.fid).get("md5Checksum") or "")
        return bool(p.md5)

    def text(p):
        raw, pages = drive.text(p.fid, deadline)
        p.text = Text(raw, pages)
        return p.text.usable

    def run(fn, items, workers):
        ok, ex = 0, cf.ThreadPoolExecutor(workers)
        futs = [ex.submit(fn, p) for p in items]
        try:
            for f in cf.as_completed(futs, timeout=max(0.1, deadline - time.time())):
                try:
                    ok += bool(f.result())
                except Exception as e:  # noqa: BLE001
                    log.info("pdf file not read: %s", type(e).__name__)
        except cf.TimeoutError:
            pass
        ex.shutdown(wait=False, cancel_futures=True)
        return ok

    n = run(meta, want, 4)
    files = "checked" if n == len(vis) else "partly" if n else "failed"
    per_year = collections.Counter(p.years for p in want if p.years)
    todo = [p for p in want if p.years and per_year[p.years] > 1]
    if not todo:
        return files, "none"
    t = run(text, todo, 2)                  # 2 at a time: a PDF can be 25 MB
    return files, "checked" if t == len(todo) else "partly" if t else "failed"


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason, "flags": [], "notes": [], "pairs": [], "pdfs": [],
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def safe_check(xano, survivor: str, losers: list[str], drive="auto", seconds: float = CHECK_SECONDS) -> dict:
    """check() that never raises and never runs longer than `seconds` + 2: a card is never blocked by it."""
    if xano is None or not getattr(xano, "token", "") or not hasattr(xano, "http"):
        return _unavailable("I can't read the PDF list from here")
    box: dict = {}

    def run():
        try:
            d = drive_ro.get() if drive == "auto" else drive
            box["r"] = check(xano, survivor, losers, drive=d, seconds=seconds)
        except Exception as e:  # noqa: BLE001
            log.warning("pdf check failed: %s", type(e).__name__)
            box["r"] = _unavailable("the check took too long" if isinstance(e, (TimeoutError, cf.TimeoutError, requests.Timeout))
                                    else "the check failed")

    t = threading.Thread(target=run, name="pdf-check", daemon=True)
    t.start()
    t.join(seconds + 2)
    return box.get("r") or _unavailable("the check took too long")


# --- choices and the merge body ---------------------------------------------------------------------------------
def version(payload: dict) -> str:
    """The choices as the approver sees them; goes in the buttons' block_id so a click on a stale card is refused."""
    return "".join("h" if f.get("choice") == "hide" else "k" for f in (payload.get("pdf_check") or {}).get("flags") or [])


def _chosen(payload: dict) -> list[dict]:
    return [f for f in (payload.get("pdf_check") or {}).get("flags") or [] if f.get("choice") == "hide"]


def hide_ids(payload: dict) -> list[str]:
    """The approved hide list for ep578. Raises ValueError when it isn't safe, so nothing is sent."""
    chosen = _chosen(payload)
    ids = list(dict.fromkeys(f["hide_pdf_id"] for f in chosen))
    if not ids:
        return []
    visible = {p["pdf_id"] for p in payload["pdf_check"].get("pdfs") or [] if p.get("visible")}
    if {f["keep_pdf_id"] for f in chosen} & set(ids) or not set(ids) <= visible or not visible - set(ids):
        raise ValueError("the PDFs to hide don't leave a visible copy")
    return ids


def recheck_live(xano, payload: dict) -> str | None:
    """Just before the merge: None when every PDF to hide, and its kept copy, is still as the card showed it; else why
    not (the merge is then not sent)."""
    try:
        if not hide_ids(payload):
            return None
        chosen = _chosen(payload)
        rows = xano_search.search(xano, 10, "PDF_ID", [f[k] for f in chosen for k in ("hide_pdf_id", "keep_pdf_id")])
    except ValueError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        log.warning("pdf recheck failed: %s", type(e).__name__)
        return "I couldn't re-read the PDFs just now"
    vids = {str(v) for v in [payload.get("survivor")] + list(payload.get("losers") or []) if v}
    by = collections.defaultdict(list)
    for r in rows:
        by[str(r.get("PDF_ID") or "").strip()].append(r)
    for f in chosen:
        for k in ("hide", "keep"):
            snap, live = f[k], by.get(f[f"{k}_pdf_id"], [])
            if k == "hide" and len(live) != 1:
                return "a PDF to hide is gone, or its id is shared with another file"
            r = next((x for x in live if int(x.get("id") or 0) == snap["row_id"]), None)
            if r is None or str(r.get("Vendor_ID") or "").strip() not in vids or r.get("Show_PDF") is False \
                    or str(r.get("PDF_Link") or "").strip() != snap["link"] \
                    or str(r.get("Year_of_Pricing") or "") != snap["year"]:
                return "a PDF on these listings changed since the card was posted"
    return None


# --- words for the card ------------------------------------------------------------------------------------------
def _esc(t) -> str:
    return str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ref(p: dict, survivor: str) -> str:
    y = "-".join(sorted(years(p.get("year")))) or "no year"
    where = "kept listing" if p.get("vendor_id") == survivor else "duplicate listing"
    text = f"{_esc(p.get('label') or 'PDF')}, {y}, {where}".replace("|", "/")
    link = str(p.get("link") or "")
    return f"<{link}|{text}>" if re.fullmatch(r"https?://[^\s<>|]+", link) else text


def summary_line(c: dict) -> str:
    if c.get("status") != "ok":
        return (f":page_facing_up: *PDF check unavailable* ({_esc(c.get('reason') or 'unknown')}). Approve tries it again "
                "first; the merge isn't sent until the PDF list can be read.")
    n, k = c.get("visible", 0), len(c.get("flags") or [])
    if n < 2:
        return (f":page_facing_up: *PDF check:* {n or 'no'} visible PDF{'s' if n != 1 else ''} on these listings, so "
                "nothing to compare.")
    bits = [f"{n} visible PDFs on these listings, "
            + ("no duplicates found" if not k else f"{k} likely duplicate{'s' if k != 1 else ''}")]
    if c.get("cant"):
        bits.append(f"{c['cant']} pair{'s' if c['cant'] != 1 else ''} I couldn't tell (kept)")
    out = ":page_facing_up: *PDF check:* " + "; ".join(bits) + "."
    nc = [w for w, key in (("file copies", "files"), ("text", "text"), ("prices on file", "prices")) if c.get(key, "none") not in ("checked", "none")]
    if nc:
        why = " (the bot can't open Google Drive yet)" if c.get("files") == "no_access" else ""
        part = "fully " if "partly" in (c.get("files"), c.get("text")) else ""
        out += f" _Not {part}checked: {' and '.join(nc)}{why}._"
    if k:          # live fn54 / fn21 read 2026-11-22: neither reads Show_PDF. Prices are min / max / first-non-null (never
        # summed), but fn21 flt_space_count = number of t36 rows and fn54 contract_count = number of distinct PDF ids.
        out += (" Hiding a PDF takes it off the page; prices already read from it still count toward this venue's "
                "prices. Amounts are never added together, but the copy still counts as a second PDF and its rooms count "
                "again in this venue's room count.")
    for note in (c.get("notes") or [])[:3]:
        out += f"\n_Note: {_esc(note)}._"
    return out


def flag_text(f: dict, survivor: str) -> str:
    choice = "*Choice: hide the duplicate*" if f.get("choice") == "hide" else "*Choice: keep both*"
    if f["type"] == "EXACT" and f.get("choice") == "hide" and not f.get("choice_by"):
        choice += " (pre-selected because it's an exact copy; Approve covers it, or press Keep both)"
    return (f"*PDF {f.get('n', '')}. {TYPE_LABEL[f['type']]}* ({f['confidence']})\n"
            f"Keep {_ref(f['keep'], survivor)} · possible duplicate {_ref(f['hide'], survivor)}\n"
            f"Matched: {_esc(f['what_matched'])}. "
            f"{':warning: *Year labels differ.* ' if f.get('year_label_differs') else ''}"
            f"Differed: {_esc(f['what_differed'])}.\n{choice}")


def details_lines(payload: dict) -> str:
    c = payload.get("pdf_check")
    if not c:
        return "*PDF check*: not run for this card."
    if c.get("status") != "ok":
        return f"*PDF check*: unavailable ({_esc(c.get('reason'))})."
    lines = [f"*PDF check* ({_esc(c.get('checked_at'))}) · file md5: {c.get('files')} · text: {c.get('text')}"]
    lines += [f"• flag {f['n']}: keep {_esc(f['keep_pdf_id'])}, hide {_esc(f['hide_pdf_id'])} ({f['type']}) · choice "
              f"{f['choice']}" for f in c.get("flags") or []]
    lines += [f"• {_esc(x['a'])} vs {_esc(x['b'])}: {x['verdict']}{' ' + x['type'] if x.get('type') else ''} · "
              f"{_esc(x['why'])}" for x in (c.get("pairs") or [])[:40]]
    return "\n".join(lines)
