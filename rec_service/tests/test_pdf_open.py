"""POST /rec/pdf (2026-11-01): the Planner card opens the vendor's PRIMARY pricing PDF directly, with the vendor page's
own accounting - vendor view PATCH, total_pdf_views + 1, the free-view meter, and the Mixpanel events. No network."""
from fastapi.testclient import TestClient

import app as appmod
import xano

ROWS = [{"id": 3, "PDF_ID": "P3", "PDF_Link": "https://drive.example/past", "Current_Assignment": "Past Season PDF 1",
         "Year_of_Pricing": "2024"},
        {"id": 2, "PDF_ID": "P2", "PDF_Link": "https://drive.example/primary", "Current_Assignment": "Primary",
         "Year_of_Pricing": "2026 - 2027"},
        {"id": 9, "PDF_ID": "P9", "PDF_Link": "", "Current_Assignment": "Primary", "Year_of_Pricing": "2028"}]


def _setup(monkeypatch, user, rows=ROWS, rows_fail=False):
    st = {"patches": [], "views": [], "events": []}
    monkeypatch.setattr(appmod, "_auth", lambda a: ("tok-7", user))
    monkeypatch.setattr(xano, "plan_label", lambda u: "Free")

    def pdf_rows(token, vid):
        if rows_fail:
            raise xano.XanoError("500 boom")
        st["rows_for"] = (token, vid)
        return rows
    monkeypatch.setattr(xano, "pdf_rows", pdf_rows)
    monkeypatch.setattr(xano, "update_profile", lambda token, fields: st["patches"].append((token, fields)) or {})
    monkeypatch.setattr(xano, "record_vendor_view", lambda token, u, vid, name="": st["views"].append((token, vid, name)))
    monkeypatch.setattr(xano, "mp_track", lambda uid, ev, props: st["events"].append((uid, ev, props)))
    st["usage"] = []
    monkeypatch.setattr(xano, "log_usage", lambda row: st["usage"].append(row))   # table-79 'pdf' row (2026-11-02)

    class Now:
        def submit(self, fn, *a, **k):
            return fn(*a, **k)
    monkeypatch.setattr(appmod, "_POOL", Now())
    appmod._PDF_LEFT.clear()      # the remembered free-view counts are per process
    return st


def _post(body):
    return TestClient(appmod.app).post("/rec/pdf", json=body, headers={"Authorization": "Bearer x"})


def test_primary_pdf_picks_the_row_the_vendor_page_lists_first():
    assert xano.primary_pdf(ROWS)["PDF_ID"] == "P2"            # Primary beats a later year; a row without a link never wins
    assert xano.primary_pdf([ROWS[0]])["PDF_ID"] == "P3"
    assert xano.primary_pdf([ROWS[2]]) is None and xano.primary_pdf([]) is None
    latest = [{"id": 1, "PDF_Link": "a", "Year_of_Pricing": "2025"}, {"id": 2, "PDF_Link": "b", "Year_of_Pricing": "2027"}]
    assert xano.primary_pdf(latest)["PDF_Link"] == "b"


def test_paid_couple_opens_and_counts_a_pdf_view_and_a_vendor_view(monkeypatch):
    st = _setup(monkeypatch, {"id": 7, "date_until_access": "3000-01-01", "FreeViewsRemaining": 3, "total_pdf_views": 4,
                              "total_vendor_views": 10})
    r = _post({"vendor_id": "V1", "vendor_name": "Oak Hall", "chat_id": 55})
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://drive.example/primary", "mode": "paid", "views_left": 3, "last_free": False,
                        "year": "2026 - 2027"}
    assert st["rows_for"] == ("tok-7", "V1")
    assert st["patches"] == [("tok-7", {"id": 7, "total_pdf_views": 5, "free_views_remaining": 3})]
    assert st["views"] == [("tok-7", "V1", "Oak Hall")]
    names = [e[1] for e in st["events"]]
    assert names == ["viewed_vendor_page", "viewed_vendor_pdf"]
    assert st["events"][1][2]["source"] == "planner" and st["events"][1][2]["mode"] == "paid"
    assert st["events"][1][2]["chat_id"] == 55 and st["events"][1][2]["pdf_id"] == "P2"


def test_forever_counts_as_entitled_whatever_the_date(monkeypatch):
    st = _setup(monkeypatch, {"id": 7, "forever_access_purchased": True, "date_until_access": "2020-01-01",
                              "FreeViewsRemaining": 0, "total_pdf_views": 0})
    r = _post({"vendor_id": "V1"})
    assert r.json()["mode"] == "paid" and st["patches"][0][1]["free_views_remaining"] == 0


def test_free_couple_spends_one_view(monkeypatch):
    st = _setup(monkeypatch, {"id": 7, "date_until_access": "2020-01-01", "FreeViewsRemaining": 2, "total_pdf_views": 1})
    r = _post({"vendor_id": "V1"})
    assert r.json() == {"url": "https://drive.example/primary", "mode": "free", "views_left": 1, "last_free": False,
                        "year": "2026 - 2027"}
    assert st["patches"] == [("tok-7", {"id": 7, "total_pdf_views": 2, "free_views_remaining": 1})]
    assert len(st["views"]) == 1


def test_last_free_view_is_flagged(monkeypatch):
    st = _setup(monkeypatch, {"id": 7, "FreeViewsRemaining": 1, "total_pdf_views": 2})
    body = _post({"vendor_id": "V1"}).json()
    assert body["mode"] == "free" and body["views_left"] == 0 and body["last_free"] is True
    assert st["patches"][0][1]["free_views_remaining"] == 0


def test_no_views_left_opens_nothing_and_writes_nothing(monkeypatch):
    st = _setup(monkeypatch, {"id": 7, "FreeViewsRemaining": 0, "total_pdf_views": 3, "date_until_access": "2020-01-01"})
    r = _post({"vendor_id": "V1"})
    assert r.status_code == 200
    assert r.json() == {"url": None, "mode": "blocked", "views_left": 0, "last_free": False}
    assert st["patches"] == [] and st["views"] == []
    assert [e[1] for e in st["events"]] == ["pdf_limit_popup_triggered"]
    # The only write is the usage-log row the daily brief counts blocked opens from (2026-11-02).
    assert [(r["kind"], r["status"], r["tools"]) for r in st["usage"]] == [("pdf", "blocked", "pdf:V1")]


def test_access_date_today_counts_as_entitled(monkeypatch):
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    st = _setup(monkeypatch, {"id": 7, "date_until_access": today + "T00:00:00Z", "FreeViewsRemaining": 0, "total_pdf_views": 0})
    assert _post({"vendor_id": "V1"}).json()["mode"] == "paid"
    assert st["patches"]


def test_vendor_without_a_pdf_is_a_404(monkeypatch):
    st = _setup(monkeypatch, {"id": 7, "FreeViewsRemaining": 3}, rows=[ROWS[2]])
    r = _post({"vendor_id": "V1"})
    assert r.status_code == 404 and r.json()["detail"]["status"] == "no_pdf"
    assert st["patches"] == [] and [e[1] for e in st["events"]] == ["rec_pdf_missing"]


def test_xano_failure_is_a_502_and_missing_vendor_a_400(monkeypatch):
    _setup(monkeypatch, {"id": 7, "FreeViewsRemaining": 3}, rows_fail=True)
    assert _post({"vendor_id": "V1"}).status_code == 502
    assert _post({"vendor_id": ""}).status_code == 400
