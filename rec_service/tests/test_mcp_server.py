"""Read-only MCP prototype (mcp_server.py, 2026-11-15). No network: Xano is mocked, the SDK runs in-process."""
import json
import os
import pathlib
import subprocess
import sys

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app as appmod
import config
import free_mode
import guards
import mcp_server
import xano

HERE = pathlib.Path(__file__).resolve().parent.parent
T = "2026-11-15"
USER = {"id": 7, "first_name": "Ana", "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 120,
        "Wedding_Budget": 60000, "favorited_vendors": ["V1", "P1"], "email": "ana@example.com"}
CARDS = {"V1": {"vendor_id": "V1", "vendor_idx": 1, "name": "Kohl Mansion", "state": "Texas", "category": "Venue",
                "address": "1 Main St, Austin, TX 78701", "venue_type": ["Estate / Mansion"], "max_capacity_seated": 200,
                "venue_fee_range": [12345, 15000], "image": "https://img/x.jpg",
                "description": "A historic estate with gardens. Rental starts at $12,345."},
         "P1": {"vendor_id": "P1", "name": "OliviaPhotos", "category": "Photography", "state": "Texas",
                "price_range": [2222, 4444]}}
PANEL = {"vendor_id": "V1", "headline": "Saturday rental $12,345", "guest_minimum": 80,
         "verdict": "About 9% below similar Texas venues", "source_note": "From the venue's 2026 PDF",
         "lines": [{"line": "Venue rental", "detail": "Saturday", "amount": 12345, "pct": None, "source": "pdf",
                    "vs_market": "Below market"}],
         "cost_inputs": None, "access_reason": "forever"}
PACK = [{"id": 2, "user_id": 7, "vendor_name": "Kohl Mansion", "total_cost": 12505, "Balance_Paid": 4000,
         "Selected": True, "Category": "Venue", "Notes": "tulle:V1"}]
STATE_ROWS = [{"State": "Texas", "Cost_of_Average_Wedding": 24063}]
PRICE_NUMBERS = ("12345", "12,345", "15000", "2222", "4444", "24063", "22764", "24,063", "22,764")


@pytest.fixture
def env(monkeypatch):
    """An allowlisted FOREVER couple (id 7) by default; st["user"] / the tier lambdas are swapped per test."""
    st = {"user": dict(USER, forever_access_purchased=True), "logged": [], "priced": [], "writes": []}
    submit = appmod._submit_usage

    def auth(authorization):
        if authorization != "Bearer good":
            raise HTTPException(401, "sign in required")
        return "tok", st["user"]

    def venue_pricing(token, vid, paid):
        st["priced"].append((vid, paid))
        return dict(PANEL, vendor_id=vid) if paid else dict(PANEL, vendor_id=vid, access_reason=None)
    monkeypatch.setattr(appmod, "_auth", auth)
    # The app's own _submit_usage, with its pool futures kept so a test can wait for the rows (st["logged"]).
    monkeypatch.setattr(appmod, "_submit_usage", lambda row: st["writes"].append(submit(row)))
    monkeypatch.setattr(config, "MCP_USER_IDS", frozenset({7}))
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "BETA_AUDIENCE", "forever")
    monkeypatch.setattr(xano, "log_usage", lambda row: st["logged"].append(row))
    monkeypatch.setattr(xano, "today", lambda: T)
    monkeypatch.setattr(xano, "vendor_card", lambda vid: dict(CARDS[vid]) if vid in CARDS else None)
    monkeypatch.setattr(xano, "cached_vendor_card", lambda vid, stats=None: dict(CARDS[vid]) if vid in CARDS else None)
    monkeypatch.setattr(xano, "user_packages", lambda tok, uid: list(PACK))
    monkeypatch.setattr(xano, "name_search", lambda term, token="", **k: (
        [dict(CARDS["V1"], google_name="")] if k.get("category", "Venue") == "Venue" and "kohl" in term else []))
    monkeypatch.setattr(xano, "venue_pricing", venue_pricing)
    monkeypatch.setattr(xano, "state_average_rows", lambda tok: STATE_ROWS)
    monkeypatch.setattr(xano, "guest_modifier", lambda tok, n: {120: 0.86}.get(n))
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    return st


def _free(st):
    st["user"] = dict(USER, forever_access_purchased=False, date_until_access=None)


def _drain(st):
    for f in st["writes"]:                           # usage rows are written on the app's pool
        f.result(timeout=10)


def _calls():
    """Every tool once, as the plain functions the MCP wrappers call."""
    return {"get_my_wedding": mcp_server.get_my_wedding("Bearer good"),
            "get_my_profile": mcp_server.get_my_profile("Bearer good"),
            "list_saved_venues": mcp_server.list_saved_venues("Bearer good"),
            "lookup_venue": mcp_server.lookup_venue("Bearer good", "Kohl Mansion"),
            "estimate_cost": mcp_server.estimate_cost("Bearer good")}


# ---------------------------------------------------------------- (a) flag off: nothing mounted, SDK not imported

def test_flag_defaults_off_and_mcp_is_not_mounted():
    assert os.environ.get("REC_MCP", "0") != "1" and config.MCP_ENABLED is False
    assert not [r for r in appmod.app.routes if getattr(r, "path", "").startswith("/mcp")]
    r = TestClient(appmod.app).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 404


def _fresh(code, **extra):
    """Run code in a new interpreter from rec_service/ (import-time behaviour can't be re-tested in this process)."""
    e = {k: v for k, v in os.environ.items() if not k.startswith("REC_MCP")}
    e.update(extra)
    return subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=e, capture_output=True, text=True, timeout=120)


def test_flag_off_import_works_even_when_the_sdk_cannot_be_imported():
    code = ("import sys, types\n"
            "sys.modules.setdefault('anthropic', types.ModuleType('anthropic')).beta_tool = lambda f: f\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'mcp' or name.startswith(('mcp.', 'mcp_types')):\n"
            "            raise ImportError('mcp is not installed (simulated)')\n"
            "sys.meta_path.insert(0, Block())\n"
            "import app, config\n"
            "assert config.MCP_ENABLED is False and config.MCP_USER_IDS == frozenset()\n"
            "assert not [r for r in app.app.routes if getattr(r, 'path', '').startswith('/mcp')]\n"
            "assert 'mcp_server' not in sys.modules and 'mcp' not in sys.modules\n"
            "print('OK')")
    r = _fresh(code)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


def test_flag_on_mounts_mcp_on_the_real_app():
    pytest.importorskip("mcp")
    code = ("import sys, types\n"
            "sys.modules.setdefault('anthropic', types.ModuleType('anthropic')).beta_tool = lambda f: f\n"
            "import app, config\n"
            "assert config.MCP_ENABLED and config.MCP_USER_IDS == frozenset({7, 42})\n"
            "assert [r.path for r in app.app.routes if getattr(r, 'path', '').startswith('/mcp')] == ['/mcp']\n"
            "print('OK')")
    r = _fresh(code, REC_MCP="1", REC_MCP_USER_IDS="7, 42,abc,")
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


# ---------------------------------------------------------------- (b) (c) (f) refusals: an error, no data, no Xano read

@pytest.mark.parametrize("header", [None, "", "Bearer bad", "good"])
def test_no_or_invalid_token_is_refused(env, monkeypatch, header):
    monkeypatch.setattr(xano, "profile_of", lambda u: (_ for _ in ()).throw(AssertionError("no data read")))
    for fn in (mcp_server.get_my_wedding, mcp_server.get_my_profile, mcp_server.list_saved_venues,
               mcp_server.estimate_cost, lambda a: mcp_server.lookup_venue(a, "Kohl Mansion")):
        with pytest.raises(mcp_server.MCPDenied, match="Not signed in"):
            fn(header)
    _drain(env)
    assert env["logged"] == [] and env["priced"] == []


def test_auth_is_the_apps_own_check(monkeypatch):
    """Not mocked: app._auth -> xano.verify_user decides, as on every /rec route."""
    seen = []
    monkeypatch.setattr(xano, "verify_user", lambda token: seen.append(token))
    with pytest.raises(mcp_server.MCPDenied, match="Not signed in"):
        mcp_server.get_my_profile("Bearer abc")
    assert seen == ["abc"]


@pytest.mark.parametrize("allow", [frozenset(), frozenset({8, 70})])
def test_valid_user_not_on_the_allowlist_is_refused(env, monkeypatch, allow):
    monkeypatch.setattr(config, "MCP_USER_IDS", allow)       # empty (the default) = nobody
    monkeypatch.setattr(xano, "profile_of", lambda u: (_ for _ in ()).throw(AssertionError("no data read")))
    for name in mcp_server.TOOLS:
        with pytest.raises(mcp_server.MCPDenied, match="not enabled for this account"):
            getattr(mcp_server, name)("Bearer good", *(["Kohl Mansion"] if name == "lookup_venue" else []))
    _drain(env)
    assert env["logged"] == [] and env["priced"] == []


def test_kill_switch_refuses_every_tool(env, monkeypatch):
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    for name in mcp_server.TOOLS:
        with pytest.raises(mcp_server.MCPDenied, match="switched off"):
            getattr(mcp_server, name)("Bearer good", *(["Kohl Mansion"] if name == "lookup_venue" else []))
    _drain(env)
    assert env["logged"] == [] and env["priced"] == []


# ---------------------------------------------------------------- (d) allowlisted full member: data from the mocks

def test_full_member_gets_each_tools_data(env):
    out = _calls()
    json.dumps(out)                                             # JSON-serialisable
    w = out["get_my_wedding"]
    assert w == appmod._wedding_view("tok", env["user"])        # exactly GET /rec/wedding's assembly
    venue = next(c for c in w["categories"] if c["key"] == "Venue")
    assert venue["booked"]["name"] == "Kohl Mansion" and venue["saved"][0]["price"] == 12345 and w["show_prices"]
    p = out["get_my_profile"]
    assert p["profile"] == xano.profile_of(env["user"]) and p["plan"] == "full" and p["price_locked"] is False
    s = out["list_saved_venues"]
    assert [c["name"] for c in s["venues"]] == ["Kohl Mansion", "OliviaPhotos"] and s["saved_count"] == 2
    assert s["venues"][0]["venue_fee_range"] == [12345, 15000] and "image" not in s["venues"][0]
    lk = out["lookup_venue"]
    assert lk["status"] == "found" and lk["matches"][0]["vendor_id"] == "V1" and lk["price_locked"] is False
    assert lk["pricing"]["lines"][0]["amount"] == 12345 and lk["pricing"]["verdict"] == PANEL["verdict"]
    assert env["priced"] == [("V1", True)]                      # priced through xano.venue_pricing, paid panel
    e = out["estimate_cost"]
    assert e["estimate"]["type"] == "cost_estimate" and e["estimate"]["state"] == "Texas"
    assert [t["amount"] for t in e["estimate"]["tiers"]] == [6829, 22764, 38698] and "$22,764" in e["text"]


def test_each_call_is_logged_as_mcp_and_never_spends_a_question(env):
    _calls()
    _drain(env)
    rows = env["logged"]
    assert sorted(r["kind"] for r in rows) == sorted("mcp_" + t for t in mcp_server.TOOLS)
    assert all(r["surface"] == "mcp" and r["status"] == "ok" and r["user_id"] == 7 and r["cost_usd"] == 0
               and r["model"] == "none" and r["counted_as_free"] is False for r in rows)
    many = [dict(r, usage_day=T) for r in rows] * 100
    assert guards.used_today(many, T) == 0 and guards.free_used(many) == 0
    assert guards.decide("refine", True, many, 0, T)[0] is True


def test_a_logging_failure_never_breaks_a_tool(env, monkeypatch):
    monkeypatch.setattr(appmod, "_submit_usage", lambda row: (_ for _ in ()).throw(RuntimeError("pool is gone")))
    assert mcp_server.get_my_profile("Bearer good")["profile"]["first_name"] == "Ana"
    monkeypatch.setattr(appmod, "_submit_usage", lambda row: appmod._POOL.submit(xano.log_usage, row))
    monkeypatch.setattr(xano, "log_usage", lambda row: (_ for _ in ()).throw(xano.XanoError("table 79 is down")))
    assert mcp_server.get_my_profile("Bearer good")["plan"] == "full"


def test_a_failing_tool_is_an_error_with_no_detail_and_is_logged(env, monkeypatch):
    monkeypatch.setattr(xano, "state_average_rows", lambda tok: (_ for _ in ()).throw(xano.XanoError("500 secret")))
    with pytest.raises(mcp_server.MCPDenied) as e:
        mcp_server.estimate_cost("Bearer good")
    assert "secret" not in str(e.value) and "estimate_cost" in str(e.value)
    _drain(env)
    assert [(r["kind"], r["status"]) for r in env["logged"]] == [("mcp_estimate_cost", "error")]


def test_lookup_args_and_misses(env):
    assert mcp_server.lookup_venue("Bearer good", "Kohl Mansion", "Austin")["status"] == "found"
    miss = mcp_server.lookup_venue("Bearer good", "Zzyzx Ballroom")
    assert miss["status"] == "not_found" and miss["matches"] == [] and "pricing" not in miss
    with pytest.raises(mcp_server.MCPDenied, match="name"):
        mcp_server.lookup_venue("Bearer good", " a ")


def test_estimate_arguments_override_the_profile(env, monkeypatch):
    monkeypatch.setattr(xano, "guest_modifier", lambda tok, n: {120: 0.86, 150: 1.3}.get(n))
    e = mcp_server.estimate_cost("Bearer good", guests=150, peak_season=True)["estimate"]
    assert (e["guests"], e["peak_season"], e["major_city"]) == (150, True, False)


# ---------------------------------------------------------------- (e) no plan: no price figures leave

def _assert_no_prices(obj):
    dump = json.dumps(obj)
    assert not free_mode._FIGURE.search(dump), dump
    assert not any(n in dump for n in PRICE_NUMBERS), dump


def test_free_couple_gets_no_price_figures_from_lookup_or_estimate(env, monkeypatch):
    _free(env)
    lk = mcp_server.lookup_venue("Bearer good", "Kohl Mansion")
    assert lk["status"] == "found" and lk["price_locked"] is True and lk["plan"] == "free"
    card = lk["matches"][0]
    assert card["name"] == "Kohl Mansion" and card["price_locked"] is True
    assert card["description"] == "A historic estate with gardens."        # the price sentence is gone
    assert not {"pricing", "verdict"} & set(lk) and not {"venue_fee_range", "price_band", "estimate_label"} & set(card)
    assert env["priced"] == [("V1", False)]         # Xano's free panel (ep231) was asked, never the paid one
    _assert_no_prices(lk)
    e = mcp_server.estimate_cost("Bearer good")
    assert e["price_locked"] is True and e["shown"] == "cost_estimate" and e["missing"] == []
    assert e["inputs"] == {"state": "Texas", "guests": 120, "major_city": False, "peak_season": False}
    assert not {"estimate", "text", "tiers", "facts", "budget"} & set(e)
    _assert_no_prices(e)


def test_free_couple_sees_no_vendor_prices_in_the_other_tools(env):
    _free(env)
    s = mcp_server.list_saved_venues("Bearer good")
    assert [c["name"] for c in s["venues"]] == ["Kohl Mansion", "OliviaPhotos"] and s["price_locked"] is True
    _assert_no_prices(s)
    w = mcp_server.get_my_wedding("Bearer good")
    assert w["show_prices"] is False
    assert all(v["price"] is None for c in w["categories"] for v in c["saved"])
    assert mcp_server.get_my_profile("Bearer good")["plan"] == "free"


def test_free_stays_price_free_even_in_the_open_planner_light_mode(env, monkeypatch):
    """REC_FREE_PRICING=light shows no-plan couples price bands in the chat; the MCP server does not."""
    _free(env)
    monkeypatch.setattr(config, "FREE_DAILY_MODE", True)
    monkeypatch.setattr(config, "FREE_PRICING", "light")
    monkeypatch.setattr(config, "LIGHT_PRICE_BANDS", True)
    _assert_no_prices(mcp_server.lookup_venue("Bearer good", "Kohl Mansion"))
    _assert_no_prices(mcp_server.list_saved_venues("Bearer good"))


def test_a_plan_that_is_not_full_gets_the_chats_light_card(env, monkeypatch):
    env["user"] = dict(USER, forever_access_purchased=False, date_until_access="2099-01-01")
    monkeypatch.setattr(config, "LIGHT_PRICE_BANDS", True)
    lk = mcp_server.lookup_venue("Bearer good", "Kohl Mansion")
    card = lk["matches"][0]
    assert lk["plan"] == "light" and "pricing" not in lk and "venue_fee_range" not in card
    assert card["price_band"]["low"] == 10000 and "verdict" not in lk       # the verdict quotes a figure: left out
    assert "12345" not in json.dumps(lk) and env["priced"] == [("V1", False)]


# ---------------------------------------------------------------- end to end through the mounted ASGI app

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client, method, params=None, token=None, rid=1):
    h = dict(HEADERS, **({"Authorization": "Bearer " + token} if token else {}))
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_end_to_end_over_streamable_http(env):
    pytest.importorskip("mcp")
    host = FastAPI()

    @host.get("/health")
    def health():
        return {"ok": True}
    mcp_server.mount(host)
    with TestClient(host) as c:                                  # the context manager runs the lifespan
        assert c.get("/health").json() == {"ok": True}           # the host's own routes still answer
        init = _rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}})
        assert init["result"]["serverInfo"]["name"] == "tulle"
        tools = _rpc(c, "tools/list")["result"]["tools"]
        assert sorted(t["name"] for t in tools) == sorted(mcp_server.TOOLS)
        assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
        lookup_schema = next(t for t in tools if t["name"] == "lookup_venue")["inputSchema"]
        assert lookup_schema["required"] == ["name"] and "ctx" not in lookup_schema["properties"]

        ok = _rpc(c, "tools/call", {"name": "lookup_venue", "arguments": {"name": "Kohl Mansion"}}, token="good")
        assert not ok["result"].get("isError")
        assert ok["result"]["structuredContent"]["matches"][0]["vendor_id"] == "V1"
        assert ok["result"]["structuredContent"]["pricing"]["lines"][0]["amount"] == 12345

        for token in (None, "bad"):                              # the header of THIS request decides
            denied = _rpc(c, "tools/call", {"name": "get_my_profile", "arguments": {}}, token=token)
            assert denied["result"]["isError"] is True and "structuredContent" not in denied["result"]
            text = json.dumps(denied["result"])
            assert "Not signed in" in text and "Ana" not in text
    _drain(env)
    assert [r["kind"] for r in env["logged"]] == ["mcp_lookup_venue"]
