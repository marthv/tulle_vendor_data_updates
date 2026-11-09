"""2026-11-09 PDF gate: rapid repeat opens must not reuse a free view that is already spent (owner: free account opened
6 PDFs in a row; auth/me lagged behind the background write so every request saw the same count)."""
import threading

from fastapi.testclient import TestClient

import app as appmod
import xano

ROWS = [{"id": 2, "PDF_ID": "P2", "PDF_Link": "https://drive.example/primary", "Current_Assignment": "Primary",
         "Year_of_Pricing": "2026"}]


def _setup(monkeypatch, views=2):
    st = {"patches": [], "views": []}
    user = {"id": 7, "date_until_access": None, "FreeViewsRemaining": views, "total_pdf_views": 0}
    # auth/me keeps returning the SAME stale row (the bug): FreeViewsRemaining never changes between requests.
    monkeypatch.setattr(appmod, "_auth", lambda a: ("tok-7", dict(user)))
    monkeypatch.setattr(xano, "plan_label", lambda u: "Free")
    monkeypatch.setattr(xano, "pdf_rows", lambda token, vid: ROWS)
    monkeypatch.setattr(xano, "update_profile", lambda token, fields: st["patches"].append(fields) or {})
    monkeypatch.setattr(xano, "record_vendor_view", lambda token, u, vid, name="": st["views"].append(vid))
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "log_usage", lambda row: None)

    class Now:
        def submit(self, fn, *a, **k):
            return fn(*a, **k)
    monkeypatch.setattr(appmod, "_POOL", Now())
    appmod._PDF_LEFT.clear()
    return st


def _post(vid="V1"):
    return TestClient(appmod.app).post("/rec/pdf", json={"vendor_id": vid}, headers={"Authorization": "Bearer x"}).json()


def test_stale_reads_cannot_reuse_a_spent_view(monkeypatch):
    st = _setup(monkeypatch, views=2)
    modes = [_post("V%d" % i)["mode"] for i in range(5)]
    assert modes == ["free", "free", "blocked", "blocked", "blocked"]
    assert [p["free_views_remaining"] for p in st["patches"]] == [1, 0]     # written before the URL went back
    assert st["views"] == ["V0", "V1"]                                       # blocked opens record no vendor view


def test_blocked_returns_no_url(monkeypatch):
    _setup(monkeypatch, views=0)
    r = _post()
    assert r["mode"] == "blocked" and r["url"] is None


def test_concurrent_taps_spend_one_view_each(monkeypatch):
    st = _setup(monkeypatch, views=1)
    out = []
    ts = [threading.Thread(target=lambda: out.append(_post()["mode"])) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(out) == ["blocked", "blocked", "blocked", "free"]
    assert len(st["patches"]) == 1


def test_remembered_count_expires(monkeypatch):
    _setup(monkeypatch, views=2)
    appmod._PDF_LEFT[7] = (0, 0)          # an old entry (1970) is ignored -> auth/me's 2 counts again
    assert _post()["mode"] == "free"


def test_paid_unaffected(monkeypatch):
    st = _setup(monkeypatch, views=0)
    monkeypatch.setattr(appmod, "_auth", lambda a: ("tok-7", {"id": 7, "forever_access_purchased": True, "FreeViewsRemaining": 0}))
    assert [_post()["mode"] for _ in range(3)] == ["paid", "paid", "paid"]
