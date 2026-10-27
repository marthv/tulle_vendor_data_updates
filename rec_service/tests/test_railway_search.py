"""Railway search rollout (2026-10-26): bucketing, routing + ep119 fallback, per-request recording, rec_usage /
Mixpanel fields, /rec/status. No network: requests.get (Railway) and xano._get (ep119) are mocked."""
import collections
import hashlib
import pathlib
import sys
import threading
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import pytest  # noqa: E402
import requests  # noqa: E402

import config  # noqa: E402
import railway_search as rs  # noqa: E402
import xano  # noqa: E402

EP119_ITEMS = [{"id": 11, "Vendor_ID": "V11", "Name": "Ep One"}, {"id": 12, "Vendor_ID": "V12", "Name": "Ep Two"}]
RAIL_ITEMS = [{"id": 21, "Vendor_ID": "V21", "Name": "Rail One"}]


class _Resp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code, self._body, self._bad = status, body, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


@pytest.fixture
def net(monkeypatch):
    st = {"rail": [], "ep119": [], "rail_resp": lambda: _Resp(200, {"items": RAIL_ITEMS, "itemsTotal": 1})}

    def rail_get(url, params=None, timeout=None, headers=None):
        st["rail"].append({"url": url, "params": dict(params), "timeout": timeout, "headers": dict(headers or {})})
        r = st["rail_resp"]()
        if isinstance(r, Exception):
            raise r
        return r

    def ep_get(url, token=None, params=None, extra_headers=None):
        st["ep119"].append({"url": url, "token": token, "params": dict(params)})
        return {"items": EP119_ITEMS, "itemsTotal": 2}

    monkeypatch.setattr(rs.requests, "get", rail_get)
    monkeypatch.setattr(xano, "_get", ep_get)
    monkeypatch.setattr(config, "RAILWAY_SEARCH_URL", "https://rail.test/search")
    monkeypatch.setattr(config, "RAILWAY_SEARCH_TIMEOUT_S", 3.0)
    return st


# ---------------------------------------------------------------- bucketing

def test_bucket_is_the_spec_hash_and_stable():
    for uid in (1, 7, 827, 33663, "abc"):
        b = int(hashlib.sha256(("railsearch:%s" % uid).encode()).hexdigest(), 16) % 100
        assert rs.arm(uid, 10) == ("railway" if b < 10 else "ep119")
        assert len({rs.arm(uid, 10) for _ in range(5)}) == 1          # same user, same arm


def test_bucket_share_is_close_to_pct_and_monotonic():
    users = range(1, 20001)
    at10 = {u for u in users if rs.arm(u, 10) == "railway"}
    at25 = {u for u in users if rs.arm(u, 25) == "railway"}
    assert 0.09 < len(at10) / 20000 < 0.11 and 0.235 < len(at25) / 20000 < 0.265
    assert at10 <= at25                     # ramping 10 -> 25 never moves a Railway user back to ep119


def test_pct_edges(monkeypatch):
    assert all(rs.arm(u, 0) == "ep119" for u in range(500))
    assert all(rs.arm(u, 100) == "railway" for u in range(500))
    assert rs.arm(None, 50) == "ep119"
    monkeypatch.setattr(config, "RAILWAY_SEARCH_PCT", 0)
    assert rs.arm(5) == "ep119"


def test_config_pct_parsing():
    assert [config._pct(v) for v in ("10", "0", "150", "-3", "abc", None, "12.7")] == [10, 0, 100, 0, 0, 0, 12]


# ---------------------------------------------------------------- routing + fallback

def test_railway_arm_uses_railway_with_same_params_and_token(net):
    log = rs.SearchLog("railway")
    r = xano.search_venues("tok", states=["Texas"], page_size=16, guests=120, vibes=["Waterfront"], search_log=log)
    assert [v["vendor_id"] for v in r["venues"]] == ["V21"] and r["total_matches"] == 1
    assert net["ep119"] == []
    call = net["rail"][0]
    assert call["url"] == "https://rail.test/search" and call["timeout"] == (1.0, 3.0)
    assert call["headers"] == {"Authorization": "Bearer tok"}
    assert call["params"] == xano.venue_search_params(states=["Texas"], page_size=16, guests=120, vibes=["Waterfront"])
    s = log.summary()
    assert s["search_engine"] == "railway" and s["search_fallback"] == "" and s["search_fallbacks"] == 0


@pytest.mark.parametrize("resp,reason", [
    (requests.exceptions.ReadTimeout("slow"), "timeout"),
    (requests.exceptions.ConnectTimeout("slow"), "timeout"),
    (requests.exceptions.ConnectionError("down"), "error"),
    (_Resp(500, {"detail": "x"}), "http_500"),
    (_Resp(503, {"detail": "search disabled"}), "http_503"),
    (_Resp(401, {"detail": "bad token"}), "http_401"),
    (_Resp(200, {"rows": []}), "bad_shape"),
    (_Resp(200, {"items": [], "itemsTotal": "5"}), "bad_shape"),
    (_Resp(200, [1, 2]), "bad_shape"),
    (_Resp(200, None, bad_json=True), "bad_shape"),
])
def test_any_railway_failure_falls_back_to_ep119_same_search(net, resp, reason):
    net["rail_resp"] = lambda: resp
    log = rs.SearchLog("railway")
    r = xano.search_venues("tok", states=["Ohio"], page_size=16, search_log=log)
    assert [v["vendor_id"] for v in r["venues"]] == ["V11", "V12"] and r["total_matches"] == 2
    assert len(net["rail"]) == 1 and len(net["ep119"]) == 1
    assert net["ep119"][0]["params"] == net["rail"][0]["params"] and net["ep119"][0]["token"] == "tok"
    s = log.summary()
    assert s["search_engine"] == "mixed" and s["search_fallback"] == reason and s["search_fallbacks"] == 1


def test_timeout_is_connect_1s_read_from_config(net, monkeypatch):
    monkeypatch.setattr(config, "RAILWAY_SEARCH_TIMEOUT_S", 2.5)
    xano.search_venues("tok", states=["Texas"], page_size=8, search_log=rs.SearchLog("railway"))
    assert net["rail"][0]["timeout"] == (1.0, 2.5)


def test_ep119_arm_never_calls_railway(net):
    log = rs.SearchLog("ep119")
    xano.search_venues("tok", states=["Texas", "Oklahoma"], page_size=16, search_log=log)
    assert net["rail"] == [] and len(net["ep119"]) == 2
    s = log.summary()
    assert s["search_engine"] == "ep119" and s["railway_search_ms"] == 0 and s["search_arm"] == "ep119"


def test_no_log_is_origin_main_behaviour(net):
    r = xano.search_venues("tok", states=["Texas"], page_size=8)
    assert net["rail"] == [] and len(net["ep119"]) == 1
    assert net["ep119"][0]["url"].endswith("/wptp_updated_mappings_search")
    assert r["total_matches"] == 2


def test_parallel_states_mixed_when_one_falls_back(net, monkeypatch):
    # Parallel per-state fallback on its own; with the breaker on (2026-10-27) the states still in flight would
    # race it - covered by the breaker tests in test_railway_breaker.py.
    monkeypatch.setattr(config, "RAILWAY_BREAKER_S", 0)
    lock, n = threading.Lock(), collections.Counter()

    def resp():
        with lock:
            n["c"] += 1
            k = n["c"]
        return requests.exceptions.ReadTimeout("x") if k == 2 else _Resp(200, {"items": RAIL_ITEMS, "itemsTotal": 1})
    net["rail_resp"] = resp
    log = rs.SearchLog("railway")
    r = xano.search_venues("tok", states=["Texas", "Ohio", "Utah"], page_size=9, search_log=log)
    assert len(net["rail"]) == 3 and len(net["ep119"]) == 1
    assert r["total_matches"] == 1 + 1 + 2
    s = log.summary()
    assert s["search_engine"] == "mixed" and s["search_fallback"] == "timeout" and s["search_fallbacks"] == 1
    assert len(log.searches) == 3 and len(log.calls) == 4 and s["search_ms"] >= 0


def test_summary_empty_and_all_railway():
    assert rs.SearchLog("railway").summary()["search_engine"] == ""
    log = rs.SearchLog("railway")
    log.add_search("railway"); log.add_search("railway")
    log.add_call("railway", 12.6); log.add_call("railway", 30)
    s = log.summary()
    assert s["search_engine"] == "railway" and s["railway_search_ms"] == 42 and s["ep119_search_ms"] == 0


def test_recording_is_thread_safe():
    log = rs.SearchLog("railway")

    def work():
        for _ in range(500):
            log.add_call("railway", 1)
            log.add_search("railway")
            log.add_wall(1)
    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    s = log.summary()
    assert len(log.calls) == 4000 and s["railway_search_ms"] == 4000 and s["search_ms"] == 4000


def test_pe_and_radius_paths_never_touch_railway(net, monkeypatch):
    log = rs.SearchLog("railway")
    net["ep119"].clear()
    monkeypatch.setattr(xano, "_get", lambda url, token=None, params=None: {"items": [], "itemsTotal": 0})
    xano.search_pe("tok", "Photography", states=["California"])
    assert net["rail"] == []
    assert "search_log" not in xano.search_pe.__code__.co_varnames
    assert "search_log" not in xano.venues_near.__code__.co_varnames


# ---------------------------------------------------------------- agent: one SearchLog per request

RAIL_UID = next(u for u in range(1, 1000) if rs.arm(u, 10) == "railway")
EP_UID = next(u for u in range(1, 1000) if rs.arm(u, 10) == "ep119")


def _agent_run(monkeypatch, uid, pct):
    """agent.run end to end with the fake tool runner from test_progress_stop: one search_venues call through the
    REAL xano.search_venues, ep119 and Railway mocked."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import copy
    import agent
    from test_progress_stop import PRICING, _fake_client
    monkeypatch.setattr(config, "RAILWAY_SEARCH_PCT", pct)
    monkeypatch.setattr(config, "EARLY_STOP", True)
    monkeypatch.setattr(xano, "venue_pricing", lambda tok, vid, paid: copy.deepcopy(PRICING))
    monkeypatch.setattr(xano, "market_benchmarks", lambda s, g=0: {"state": s, "metrics": {}})
    monkeypatch.setattr(xano, "saved_vendor_ids", lambda u: [])
    monkeypatch.setattr(xano, "vendor_card", lambda vid: {"vendor_id": vid, "name": vid})
    monkeypatch.setattr(xano, "state_average_rows", lambda tok: [])
    monkeypatch.setattr(xano, "guest_modifier", lambda tok, n: 1.0)
    turns = [[("search_venues", {"states": ["Texas"]})],
             [("present_recommendations", {"message": "Picks.", "vendor_ids": [], "reasons": [], "chips": []})]]
    monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(turns, {}), raising=False)
    return agent.run("tok", {"id": uid}, True, [{"role": "user", "content": "venues in Texas"}],
                     profile_override={"Wedding_Location_Updated": ["Texas"]}, detail="full")


def test_agent_railway_arm_records_railway(net, monkeypatch):
    _, usage = _agent_run(monkeypatch, RAIL_UID, 10)
    assert len(net["rail"]) == 1 and net["ep119"] == []
    assert usage["search"]["search_engine"] == "railway" and usage["search"]["search_arm"] == "railway"


def test_agent_pct_zero_never_calls_railway(net, monkeypatch):
    _, usage = _agent_run(monkeypatch, RAIL_UID, 0)      # this user WOULD be Railway at 10%
    assert net["rail"] == [] and len(net["ep119"]) == 1
    assert usage["search"]["search_engine"] == "ep119" and usage["search"]["search_arm"] == "ep119"


def test_agent_fallback_shows_mixed(net, monkeypatch):
    net["rail_resp"] = lambda: _Resp(503, {"detail": "off"})
    _, usage = _agent_run(monkeypatch, RAIL_UID, 10)
    assert len(net["rail"]) == 1 and len(net["ep119"]) == 1
    assert usage["search"]["search_engine"] == "mixed" and usage["search"]["search_fallback"] == "http_503"


# ---------------------------------------------------------------- app: rec_usage row, Mixpanel, /rec/status

class _Sync:
    def submit(self, f, *a, **k):
        from concurrent.futures import Future
        fut = Future()
        try:
            fut.set_result(f(*a, **k))
        except Exception as e:
            fut.set_exception(e)
        return fut


@pytest.fixture
def api(monkeypatch, net):
    import agent
    import app
    from fastapi.testclient import TestClient
    st = {"logged": [], "mp": [], "uid": RAIL_UID}

    def run(token, user, access, messages, search_log=None, **kw):
        if st.get("search", True):
            xano.search_venues(token, states=["Texas"], page_size=16, search_log=search_log)
        if st.get("raise"):
            raise RuntimeError("boom")
        return ({"text": "picks", "cards": [], "more_cards": [], "chips": []},
                {"model": "m", "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 1,
                 "tool_calls": 1, "latency_ms": 900, "search_ms": 5, "search": search_log.summary()})
    monkeypatch.setattr(config, "RAILWAY_SEARCH_PCT", 10)
    monkeypatch.setattr(app, "_POOL", _Sync())
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": st["uid"]}))
    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    monkeypatch.setattr(xano, "create_chat", lambda uid, title: {"id": 51, "title": title})
    monkeypatch.setattr(xano, "list_chats", lambda uid: [])
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "title": "t"})
    monkeypatch.setattr(xano, "get_messages", lambda cid: [])
    monkeypatch.setattr(xano, "add_message", lambda *a: None)
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "log_usage", lambda row: st["logged"].append(row))
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(xano, "spend_today", lambda: (0.0, 0.0))
    monkeypatch.setattr(xano, "has_forever", lambda u: True)
    monkeypatch.setattr(xano, "beta_member", lambda u: True)
    monkeypatch.setattr(xano, "has_paid_access", lambda u: True)
    monkeypatch.setattr(xano, "mp_track", lambda uid, ev, props: st["mp"].append((ev, props)))
    monkeypatch.setattr(xano, "today", lambda: "2026-10-26")
    monkeypatch.setattr(app, "_feedback_given", lambda uid: True)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    st["client"] = TestClient(app.app)
    return st


def _ask(api):
    return api["client"].post("/rec/refine", json={"message": "barns in Texas"})


def _last(api, status="ok"):
    return [r for r in api["logged"] if r.get("status") == status][-1]


def _last_mp(api):
    return [m for m in api["mp"] if m[0] == "rec_server_request"][-1][1]


def test_usage_row_and_mixpanel_railway(api):
    assert _ask(api).status_code == 200
    row = _last(api)
    assert row["search_engine"] == "railway" and row["search_fallback"] == "" and isinstance(row["search_ms"], int)
    props = _last_mp(api)
    assert props["search_arm"] == "railway" and props["search_engine"] == "railway"
    assert props["search_fallback"] == "" and props["search_fallbacks"] == 0
    assert {"railway_search_ms", "ep119_search_ms"} <= set(props)
    assert props["search_ms"] == 5                      # the existing prop is unchanged (agent timing)


def test_usage_row_mixed_on_fallback(api, net):
    net["rail_resp"] = lambda: requests.exceptions.ReadTimeout("x")
    assert _ask(api).status_code == 200
    row = _last(api)
    assert row["search_engine"] == "mixed" and row["search_fallback"] == "timeout"
    props = _last_mp(api)
    assert props["search_fallbacks"] == 1 and props["search_engine"] == "mixed"


def test_no_venue_search_logs_blank_engine_but_arm(api):
    api["search"] = False
    assert _ask(api).status_code == 200
    row = _last(api)
    assert row["search_engine"] == "" and row["search_ms"] == 0
    props = _last_mp(api)
    assert props["search_arm"] == "railway" and props["search_engine"] == ""


def test_error_row_keeps_the_engine(api):
    api["raise"] = True
    assert _ask(api).status_code == 502
    assert _last(api, "error")["search_engine"] == "railway"


def test_status_reports_arm_and_pct(api):
    d = api["client"].get("/rec/status").json()
    assert d["search_arm"] == "railway" and d["railway_search_pct"] == 10
    api["uid"] = EP_UID
    assert api["client"].get("/rec/status").json()["search_arm"] == "ep119"


def test_forced_user_always_railway(monkeypatch):
    """Owner 2026-10-26: listed user ids are always on Railway search (ep119 stays their fallback), at any pct."""
    import config, railway_search
    monkeypatch.setattr(config, "RAILWAY_SEARCH_FORCE_IDS", {"14998"})
    for pct in (0, 15, 100):
        assert railway_search.arm(14998, pct=pct) == railway_search.RAILWAY
        assert railway_search.arm("14998", pct=pct) == railway_search.RAILWAY
    assert railway_search.arm(1, pct=0) == railway_search.EP119
