"""MERGE_CARD_FAST (2026-11-25): "@Tulle Bot merge X into Y" cards were ~1 min to post (60 whole t10/t36 pages read per
card) and ~3 min when five came in together; all five price checks then said "can't compute: Xano did not answer
(ReadTimeout)". The flag (default off) counts PDFs with one filtered read and gives a slow read 20 s, then 30 s.
These tests pin: off = exactly as before; on = the same card and the same price text, with far fewer reads, and a read
that the old timeouts gave up on is now waited for."""
import json
import os
import re
import sys
import threading
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import dedupe  # noqa: E402
import merge_checks as mc  # noqa: E402
import price_impact  # noqa: E402
from fakes import mention  # noqa: E402
from test_merge_requests import ROWS  # noqa: E402
from test_vendor_actions import make_bot  # noqa: E402


# ------------------------------------------------------------------------------------- reference counts (card post)

class MetaXano:
    """A metadata API with t10/t36 the live size (21,762 / 45,361 rows on 2026-11-25), serving whole pages (GET) and
    filtered searches (POST content/search). Counts every request."""
    enabled = True
    root, token = "https://x/api:meta/workspace/1", "t"

    def __init__(self, pdfs):
        self.http, self.gets, self.posts = self, 0, 0
        self.lock = threading.Lock()
        self.t = {10: [dict(id=i, Vendor_ID=f"VX{i % 5000}", PDF_ID=f"P{i}") for i in range(1, 21763)],
                  36: [dict(id=i, VENDOR_ID=f"VX{i % 5000}", PDF_ID=f"P{i}", VENUE_NAME="x") for i in range(1, 45362)]}
        nxt = 30000
        for vid, n in pdfs.items():                       # the listings in play, scattered through the table
            for k in range(n):
                nxt += 1
                self.t[10][(nxt * 7919) % len(self.t[10])] = dict(id=(nxt * 7919) % len(self.t[10]) + 1,
                                                                   Vendor_ID=vid, PDF_ID=f"Q{nxt}")
        for t in self.t:
            self.t[t].sort(key=lambda r: r["id"])

    def rows(self, table_id):
        return ([], False)

    def get(self, path, params=None, **kw):                # RefIndex: xano.get("/table/<t>/content", page)
        with self.lock:
            self.gets += 1
        t = int(re.search(r"/table/(\d+)/content", path).group(1))
        rows = self.t.get(t, [])
        p, n = int(params["page"]), int(params["per_page"])
        chunk = rows[(p - 1) * n: p * n]
        return {"items": chunk, "nextPage": p + 1 if p * n < len(rows) else None}

    def post(self, url, json=None, headers=None, timeout=None):   # xano_search
        with self.lock:
            self.posts += 1
        t = int(re.search(r"/table/(\d+)/content/search$", url).group(1))
        (key, vals), = json["search"].items()
        col = key.split("|")[0]
        items = [r for r in self.t.get(t, []) if r.get(col) in vals]
        p, n = json["page"], json["per_page"]

        class R:
            status_code = 200

            def json(self_):
                return {"items": items[(p - 1) * n: p * n], "nextPage": p + 1 if p * n < len(items) else None}
        return R()


def _named_card(monkeypatch, fast: bool):
    monkeypatch.setenv("DEDUPE_PAGE_DELAY", "0")
    if fast:
        monkeypatch.setenv("MERGE_CARD_FAST", "1")
    else:
        monkeypatch.delenv("MERGE_CARD_FAST", raising=False)
    import listing_compare
    import pdf_dupes
    monkeypatch.setattr(listing_compare, "fresh", lambda xano, members: members)
    monkeypatch.setattr(pdf_dupes, "safe_check", lambda *a, **k: {"status": "unavailable", "why": "test"})
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network write")))
    b, slack, store = make_bot(rows=[dict(r) for r in ROWS])
    b.xano = MetaXano({"V9": 2, "V10": 1})
    t0 = time.monotonic()
    assert b.handle(mention("merge V9 into V10")) == "merge:proposed"
    secs = time.monotonic() - t0
    [p] = store.all()
    card = json.dumps(slack.of("chat.postMessage")[-1]["blocks"]).replace(p["id"], "<id>")
    return p, card, b.xano, secs


def test_fast_named_card_is_the_same_card_from_one_read_instead_of_sixty_pages(monkeypatch):
    old, old_card, ox, _ = _named_card(monkeypatch, fast=False)
    new, new_card, nx, _ = _named_card(monkeypatch, fast=True)
    assert ox.gets == 60 and ox.posts == 0                 # the default budget, all spent; t36 never finished
    assert nx.gets == 0 and nx.posts == 1                  # one filtered read of t10
    for k in ("survivor", "losers", "counts", "total", "tables", "row_ids", "why_survivor", "snapshots", "counters",
              "visibility", "origin"):
        assert new["payload"][k] == old["payload"][k], k
    assert new["plain"] == old["plain"] and new_card == old_card
    t = {x["table"]: x for x in new["payload"]["tables"] if x["table"] in (10, 36)}
    assert t[10]["status"] == "counted" and t[10]["count"] == 2 and t[36]["status"] == "not_counted"


def test_fast_falls_back_to_paging_when_the_filtered_read_fails(monkeypatch):
    import xano_search
    monkeypatch.setattr(xano_search, "search", lambda *a, **k: (_ for _ in ()).throw(requests.ReadTimeout("x")))
    p, _, x, _ = _named_card(monkeypatch, fast=True)
    assert x.gets == 60 and {t["table"]: t["count"] for t in p["payload"]["tables"]}[10] == 2


def test_fast_is_not_used_when_the_page_budget_could_finish_t36(monkeypatch):
    monkeypatch.setenv("FEEDBACK_PAGE_BUDGET", str(dedupe.RefIndex.FULL_10_36_PAGES))
    p, _, x, _ = _named_card(monkeypatch, fast=True)
    assert x.posts == 0 and x.gets == 135
    assert {t["table"]: t["status"] for t in p["payload"]["tables"]}[36] == "counted"


def test_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv("MERGE_CARD_FAST", raising=False)
    assert config.merge_card_fast() is False and mc._budget()[0] == mc.SECONDS
    monkeypatch.setenv("MERGE_CARD_FAST", "1")
    assert config.merge_card_fast() is True and mc._budget()[0] == mc.PATIENT_SECONDS


def test_price_passes_the_patient_timeouts_only_when_on(monkeypatch):
    seen = []

    def compute(xano, s, losers, **kw):
        seen.append(kw)
        return {"alert": False, "cant": [], "stamp": "s", "after": None, "reasons": [], "kinds": []}
    monkeypatch.setattr(price_impact, "compute", compute)
    monkeypatch.setattr(price_impact, "blocks", lambda r: {"extra_blocks": [], "confirm_text": None, "alert": False})
    monkeypatch.delenv("XANO_MERGE_URL", raising=False)
    monkeypatch.delenv("MERGE_CARD_FAST", raising=False)
    mc._compute(object(), {"survivor": "V1", "losers": ["V2"]}, [], pv=None)
    monkeypatch.setenv("MERGE_CARD_FAST", "1")
    mc._compute(object(), {"survivor": "V1", "losers": ["V2"]}, [], pv=None)
    assert seen[0] == {"preview": None, "hidden_pdfs": [], "deadline": mc.SECONDS}
    assert seen[1] == {"preview": None, "hidden_pdfs": [], "deadline": mc.PATIENT_SECONDS,
                       "timeouts": price_impact.PATIENT_TIMEOUTS}
