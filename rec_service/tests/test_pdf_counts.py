"""POST /rec/pdf-counts (2026-11-14): the Planner card says when a vendor has more than one pricing PDF. No network."""
from fastapi.testclient import TestClient

import app as appmod
import xano

ROWS = {
    "V581": [{"PDF_Link": "a", "Year_of_Pricing": "2027", "Current_Assignment": "Primary"},
             {"PDF_Link": "b", "Year_of_Pricing": "2025"}, {"PDF_Link": "c", "Year_of_Pricing": "2027"},
             {"PDF_Link": "d", "Year_of_Pricing": "2026"}],
    "V2": [{"PDF_Link": "a", "Year_of_Pricing": "2026 - 2027"}, {"PDF_Link": "", "Year_of_Pricing": "2028"},
           {"PDF_Link": "h", "Year_of_Pricing": "2024", "Show_PDF": False}],
}


def _setup(monkeypatch, fail=()):
    calls = []
    monkeypatch.setattr(appmod, "_auth", lambda a: ("tok", {"id": 7}))

    def pdf_rows(token, vid):
        calls.append(vid)
        if vid in fail:
            raise xano.XanoError("500")
        return ROWS.get(vid, [])
    monkeypatch.setattr(xano, "pdf_rows", pdf_rows)
    appmod._PDF_COUNTS.clear()
    return calls


def _post(ids):
    r = TestClient(appmod.app).post("/rec/pdf-counts", json={"vendor_ids": ids}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 200, r.text
    return r.json()["counts"]


def test_counts_and_years_match_the_vendor_page_list(monkeypatch):
    _setup(monkeypatch)
    c = _post(["V581", "V2", "V9"])
    assert c["V581"] == {"count": 4, "years": [2027, 2026, 2025]}
    assert c["V2"] == {"count": 1, "years": [2027, 2026]}      # no link and hidden rows don't count
    assert c["V9"] == {"count": 0, "years": []}


def test_cached_and_deduped_and_a_failure_is_left_out(monkeypatch):
    calls = _setup(monkeypatch, fail=("VX",))
    c = _post(["V581", "V581", "VX"])
    assert "VX" not in c and c["V581"]["count"] == 4
    _post(["V581"])
    assert calls.count("V581") == 1                              # second ask served from memory


def test_at_most_12_vendors(monkeypatch):
    calls = _setup(monkeypatch)
    _post(["V%d" % i for i in range(30)])
    assert len(calls) == 12
