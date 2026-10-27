"""Railway search circuit breaker (2026-10-27): a timeout / connection error / 5xx opens it for RAILWAY_BREAKER_S;
while open every Railway-arm search goes straight to ep119 (reason breaker_open, no search-api call); it closes
after the window; 4xx and bad_shape never trip it; 0 = no breaker. Mixpanel carries breaker_skips / breaker_state."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
import requests  # noqa: E402

import app  # noqa: E402
import config  # noqa: E402
import railway_search as rs  # noqa: E402
import xano  # noqa: E402

RAIL_OK = {"items": [{"id": 21, "Vendor_ID": "V21", "Name": "Rail One"}], "itemsTotal": 1}


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture
def net(monkeypatch):
    st = {"rail": 0, "ep119": 0, "resp": lambda: _Resp(200, RAIL_OK)}

    def rail_get(url, params=None, timeout=None, headers=None):
        st["rail"] += 1
        r = st["resp"]()
        if isinstance(r, Exception):
            raise r
        return r

    def ep_get(url, token=None, params=None, extra_headers=None):
        st["ep119"] += 1
        return {"items": [{"id": 11, "Vendor_ID": "V11", "Name": "Ep One"}], "itemsTotal": 1}

    monkeypatch.setattr(rs.requests, "get", rail_get)
    monkeypatch.setattr(xano, "_get", ep_get)
    monkeypatch.setattr(config, "RAILWAY_SEARCH_URL", "https://rail.test/search")
    monkeypatch.setattr(config, "RAILWAY_BREAKER_S", 60)
    return st


def search(log):
    return xano.search_venues("tok", states=["Ohio"], page_size=16, search_log=log)


@pytest.mark.parametrize("failure", [requests.exceptions.ReadTimeout("slow"), requests.exceptions.ConnectionError("x"),
                                     _Resp(500, {}), _Resp(503, {})])
def test_freeze_like_failure_opens_breaker_then_searches_skip_railway(net, failure):
    net["resp"] = lambda: failure
    search(rs.SearchLog("railway"))
    assert net["rail"] == 1 and net["ep119"] == 1 and rs.BREAKER.is_open()
    net["resp"] = lambda: _Resp(200, RAIL_OK)       # Railway is back, but the window is still open
    log = rs.SearchLog("railway")
    r = search(log)
    assert net["rail"] == 1 and net["ep119"] == 2    # no search-api call at all
    assert [v["vendor_id"] for v in r["venues"]] == ["V11"]
    s = log.summary()
    assert s["search_fallback"] == "breaker_open" and s["breaker_skips"] == 1 and s["breaker_state"] == "open"
    assert s["search_engine"] == "mixed" and s["railway_search_ms"] == 0


@pytest.mark.parametrize("failure", [_Resp(401, {}), _Resp(404, {}), _Resp(200, {"rows": []})])
def test_request_problems_do_not_trip(net, failure):
    net["resp"] = lambda: failure
    search(rs.SearchLog("railway"))
    assert not rs.BREAKER.is_open()
    search(rs.SearchLog("railway"))
    assert net["rail"] == 2


def test_closes_after_window(net, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock[0])
    net["resp"] = lambda: requests.exceptions.ReadTimeout("slow")
    search(rs.SearchLog("railway"))
    assert rs.BREAKER.is_open()
    clock[0] += 59.9
    assert rs.BREAKER.is_open()
    clock[0] += 0.2
    net["resp"] = lambda: _Resp(200, RAIL_OK)
    log = rs.SearchLog("railway")
    search(log)
    assert net["rail"] == 2 and log.summary()["search_engine"] == "railway" and not rs.BREAKER.is_open()


def test_zero_window_means_no_breaker(net, monkeypatch):
    monkeypatch.setattr(config, "RAILWAY_BREAKER_S", 0)
    net["resp"] = lambda: requests.exceptions.ReadTimeout("slow")
    search(rs.SearchLog("railway"))
    search(rs.SearchLog("railway"))
    assert net["rail"] == 2 and not rs.BREAKER.is_open()


def test_ep119_arm_never_touches_breaker(net):
    rs.BREAKER.trip()
    log = rs.SearchLog("ep119")
    search(log)
    assert net["rail"] == 0 and log.summary()["breaker_skips"] == 0


def test_mixpanel_props_carry_breaker(net):
    props = app._search_props({"id": 5}, {"breaker_skips": 2, "breaker_state": "open"})
    assert props["breaker_skips"] == 2 and props["breaker_state"] == "open"
    assert app._search_props({"id": 5}, None)["breaker_state"] == "closed"


def test_full_rollout_puts_everyone_on_railway(monkeypatch):
    # The deploy step REC_RAILWAY_SEARCH_PCT=100: every user id lands in the Railway arm.
    assert {rs.arm(u, 100) for u in range(1, 500)} == {rs.RAILWAY}
