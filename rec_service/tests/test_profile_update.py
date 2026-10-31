"""POST /rec/profile: the Planner chat's quick profile edit (2026-10-31) writes through ep21 with the user's token."""
from fastapi.testclient import TestClient

import app as appmod
import xano


def _setup(monkeypatch, written, fail=False):
    monkeypatch.setattr(appmod, "_auth", lambda a: ("tok-7", {"id": 7, "first_name": "Sam", "Wedding_Guest_Count": 100}))
    monkeypatch.setattr(xano, "plan_label", lambda u: "Free")
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)

    def update(token, fields):
        if fail:
            raise xano.XanoError("500 boom")
        written.append((token, fields))
        return {"id": 7}
    monkeypatch.setattr(xano, "update_profile", update)
    monkeypatch.setattr(xano, "verify_user", lambda t: {"id": 7, "first_name": "Sam", "Wedding_Guest_Count": 150,
                                                       "Wedding_Budget": 55000, "Wedding_Location_Updated": ["Texas"],
                                                       "password": "never-shown"})

    class Now:
        def submit(self, fn, *a, **k):
            return fn(*a, **k)
    monkeypatch.setattr(appmod, "_POOL", Now())


def _post(body):
    return TestClient(appmod.app).post("/rec/profile", json=body, headers={"Authorization": "Bearer x"})


def test_maps_fields_and_returns_fresh_profile(monkeypatch):
    written = []
    _setup(monkeypatch, written)
    r = _post({"guests": 150, "budget": 55000, "states": ["Texas", "TX", "Texas"], "date": "2027-10-16"})
    assert r.status_code == 400, "TX is not a state name"
    r = _post({"guests": 150, "budget": 55000, "states": ["Texas", "Texas", "International"], "date": "2027-10-16"})
    assert r.status_code == 200, r.text
    assert written == [("tok-7", {"Wedding_Guest_Count": 150, "Wedding_Budget": 55000,
                                  "Wedding_Location_Updated": ["Texas", "International"], "Wedding_Date": "2027-10-16"})]
    body = r.json()
    assert body["ok"] and body["updated"] == ["Wedding_Budget", "Wedding_Date", "Wedding_Guest_Count", "Wedding_Location_Updated"]
    assert body["profile"]["Wedding_Guest_Count"] == 150 and "password" not in body["profile"]


def test_only_given_fields_are_written(monkeypatch):
    written = []
    _setup(monkeypatch, written)
    assert _post({"guests": 120}).status_code == 200
    assert written == [("tok-7", {"Wedding_Guest_Count": 120})]


def test_invalid_values_rejected_before_any_write(monkeypatch):
    written = []
    _setup(monkeypatch, written)
    for body, field in (({"guests": 1}, "guests"), ({"guests": 5000}, "guests"), ({"budget": 50}, "budget"),
                        ({"date": "October 2027"}, "date"), ({"states": ["Narnia"]}, "states")):
        r = _post(body)
        assert r.status_code == 400 and r.json()["detail"] == {"status": "invalid", "fields": [field]}, body
    assert not written


def test_empty_body_is_400(monkeypatch):
    written = []
    _setup(monkeypatch, written)
    r = _post({})
    assert r.status_code == 400 and r.json()["detail"]["status"] == "nothing_to_update" and not written


def test_xano_failure_is_502(monkeypatch):
    _setup(monkeypatch, [], fail=True)
    r = _post({"guests": 120})
    assert r.status_code == 502 and r.json()["detail"] == {"status": "error"}


def test_update_profile_patches_ep21_with_user_token(monkeypatch):
    calls = []

    class R:
        status_code = 200
        ok = True
        text = '{"id": 7}'

        def json(self):
            return {"id": 7}
    monkeypatch.setattr(xano.requests, "patch", lambda url, **kw: calls.append((url, kw)) or R())
    out = xano.update_profile("tok-7", {"Wedding_Guest_Count": 150})
    url, kw = calls[0]
    assert url.endswith("/user/update_record") and "/api:WGW_G49d" in url
    assert kw["json"] == {"Wedding_Guest_Count": 150} and kw["headers"]["Authorization"] == "Bearer tok-7"
    assert out == {"id": 7}
