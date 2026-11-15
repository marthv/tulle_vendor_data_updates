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
PHOTOG = {"vendor_id": "P1", "vendor_idx": 9, "name": "OliviaPhotos", "state": "Texas", "category": "Photography",
          "address": "Austin, TX", "description": "Documentary style. Packages from $2,222.", "price_range": [2222, 4444]}
TWINS = [{"vendor_id": "A1", "name": "Ashton Gardens North", "state": "Georgia", "category": "Venue",
          "address": "1 Peach Rd, Atlanta, GA 30301", "venue_fee_range": [7100, 9300]},
         {"vendor_id": "A2", "name": "Ashton Gardens West", "state": "Ohio", "category": "Venue",
          "address": "2 Elm St, Columbus, OH 43004", "venue_fee_range": [8200, 9900]}]
PACK = [{"id": 2, "user_id": 7, "vendor_name": "Kohl Mansion", "total_cost": 12505, "Balance_Paid": 4000,
         "Selected": True, "Category": "Venue", "Notes": "tulle:V1"}]
STATE_ROWS = [{"State": "Texas", "Cost_of_Average_Wedding": 24063}]
PRICE_NUMBERS = ("12345", "12,345", "15000", "2222", "2,222", "4444", "24063", "22764", "24,063", "22,764",
                 "7100", "9300", "8200", "9900")


@pytest.fixture
def env(monkeypatch):
    """An allowlisted FOREVER couple (id 7) by default; st["user"] / the tier lambdas are swapped per test."""
    st = {"user": dict(USER, forever_access_purchased=True), "logged": [], "priced": [], "writes": [], "auth": []}
    submit = appmod._submit_usage

    def auth(authorization):
        st["auth"].append(authorization)
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
    def name_search(term, token="", **k):
        cat = k.get("category", "Venue")
        if cat == "Venue":
            return ([dict(CARDS["V1"], google_name="")] if "kohl" in term else
                    [dict(v, google_name="") for v in TWINS] if "ashton" in term else [])
        return [dict(PHOTOG, google_name="")] if cat == "Photography" and "olivia" in term else []
    monkeypatch.setattr(xano, "name_search", name_search)
    monkeypatch.setattr(xano, "venue_pricing", venue_pricing)
    monkeypatch.setattr(xano, "state_average_rows", lambda tok: STATE_ROWS)
    monkeypatch.setattr(xano, "guest_modifier", lambda tok, n: {120: 0.86}.get(n))
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    return st


def _free(st):
    st["user"] = dict(USER, forever_access_purchased=False, date_until_access=None)


def _light(st):
    """An active plan that is not FULL (REC_BETA_AUDIENCE=forever: a 1-week / 4-week buyer)."""
    st["user"] = dict(USER, forever_access_purchased=False, date_until_access="2099-01-01")
    assert xano.has_paid_access(st["user"]) and not xano.beta_member(st["user"])


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


STUB = ("import sys, types\n"
        "sys.modules.setdefault('anthropic', types.ModuleType('anthropic')).beta_tool = lambda f: f\n"
        "import pricing_warm\n"
        "starts = []\n"
        "pricing_warm.start = lambda: starts.append(1)      # the real startup handler, minus its network warm-up\n")


def test_flag_on_mounts_mcp_on_the_real_app_and_startup_runs_exactly_once():
    pytest.importorskip("mcp")
    code = STUB + (
        "import app, config\n"
        "from fastapi.testclient import TestClient\n"
        "assert config.MCP_ENABLED and config.MCP_USER_IDS == frozenset({7, 42})\n"
        "assert [r.path for r in app.app.routes if getattr(r, 'path', '').startswith('/mcp')] == ['/mcp']\n"
        "with TestClient(app.app, base_url='http://tulle.test') as c:\n"
        "    assert starts == [1], starts\n"
        "    assert c.get('/health').status_code == 200\n"
        "    assert c.get('/mcp').status_code == 405\n"
        "    assert c.post('/mcp', json={}).status_code == 401\n"
        "assert starts == [1], starts\n"
        "print('OK')")
    r = _fresh(code, REC_MCP="1", REC_MCP_USER_IDS="7, 42,abc,", REC_MCP_ALLOWED_HOSTS="tulle.test")
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


def test_flag_on_without_allowed_hosts_does_not_mount_and_says_why():
    code = STUB + (
        "import app, config\n"
        "from fastapi.testclient import TestClient\n"
        "assert config.MCP_ENABLED and config.MCP_ALLOWED_HOSTS == []\n"
        "assert not [r for r in app.app.routes if getattr(r, 'path', '').startswith('/mcp')]\n"
        "assert 'mcp' not in sys.modules\n"
        "with TestClient(app.app) as c:\n"
        "    assert c.post('/mcp', json={}).status_code == 404 and starts == [1]\n"
        "print('OK')")
    r = _fresh(code, REC_MCP="1", REC_MCP_USER_IDS="7")
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]
    assert r.stdout.count("REC_MCP_ALLOWED_HOSTS is empty - /mcp is NOT mounted") == 1


# ---------------------------------------------------------------- (b) (c) (f) refusals: an error, no data, no Xano read

@pytest.mark.parametrize("header", [None, "", "Bearer bad", "good", "bearer good", "Bearer ", "Basic good", 7])
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


def test_the_literal_bearer_prefix_is_required_on_the_mcp_path_only(env):
    for header in ("good", "bearer good", "Token good"):
        with pytest.raises(mcp_server.MCPDenied):
            mcp_server.get_my_profile(header)
    assert env["auth"] == []                                  # refused before the sign-in check is even asked
    assert appmod._auth.__name__ == "auth"                    # (the /rec routes' own check is untouched: no prefix rule)


def test_a_failed_sign_in_check_is_generic_and_the_detail_stays_in_the_server_log(env, monkeypatch, capsys):
    import requests
    secret = "HTTPSConnectionPool(host='xqtb-2ma7-ijfy.n7e.xano.io', port=443): Read timed out. /api:v1keLQ2a/auth/me"
    monkeypatch.setattr(appmod, "_auth", lambda a: (_ for _ in ()).throw(requests.exceptions.ReadTimeout(secret)))
    for name in mcp_server.TOOLS:
        with pytest.raises(mcp_server.MCPDenied) as e:
            getattr(mcp_server, name)("Bearer good", *(["Kohl Mansion"] if name == "lookup_venue" else []))
        assert str(e.value) == "Tulle could not verify the sign-in right now. Try again in a moment."
        assert e.value.status == 503 and "xano" not in str(e.value).lower()
    assert "mcp sign-in check failed" in capsys.readouterr().out      # server side only
    assert env["logged"] == [] and env["priced"] == []


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


def test_a_plan_that_is_not_full_gets_no_figures_from_tulles_pricing_data(env, monkeypatch):
    """Only FULL gets figures. A 1-week / 4-week plan used to get agent._light_card, which left price_range and the
    description's "$" sentences in (and the wedding screen's "about $X" hints)."""
    _light(env)
    monkeypatch.setattr(config, "LIGHT_PRICE_BANDS", True)
    s = mcp_server.list_saved_venues("Bearer good")
    assert [c["name"] for c in s["venues"]] == ["Kohl Mansion", "OliviaPhotos"] and s["price_locked"] is True
    assert all(not {"price_range", "venue_fee_range", "price_band"} & set(c) and c["price_locked"] for c in s["venues"])
    assert s["venues"][0]["description"] == "A historic estate with gardens."
    _assert_no_prices(s)
    lk = mcp_server.lookup_venue("Bearer good", "Kohl Mansion")
    assert lk["plan"] == "light" and lk["price_locked"] is True and not {"pricing", "verdict"} & set(lk)
    assert not {"venue_fee_range", "price_band", "estimate_label"} & set(lk["matches"][0])
    assert env["priced"] == [("V1", False)]
    _assert_no_prices(lk)
    _assert_no_prices(mcp_server.lookup_venue("Bearer good", "OliviaPhotos"))
    _assert_no_prices(mcp_server.lookup_venue("Bearer good", "Ashton Gardens"))
    e = mcp_server.estimate_cost("Bearer good")
    assert e["price_locked"] is True and "estimate" not in e
    _assert_no_prices(e)


def test_wedding_screen_keeps_the_couples_own_numbers_and_drops_tulles_price_hints(env):
    _light(env)
    assert appmod._wedding_view("tok", env["user"])["categories"][0]["saved"][0]["price"] == 12345   # what the app shows
    w = mcp_server.get_my_wedding("Bearer good")
    assert w["show_prices"] is False and all(v["price"] is None for c in w["categories"] for v in c["saved"])
    venue = next(c for c in w["categories"] if c["key"] == "Venue")
    assert w["budget"] == 60000 and venue["booked"]["price"] == 12505 and venue["booked"]["paid"] == 4000   # theirs
    assert "12345" not in json.dumps(w)


# ---------------------------------------------------------------- lookups that are not one venue

def test_lookup_of_a_non_venue_vendor(env):
    lk = mcp_server.lookup_venue("Bearer good", "OliviaPhotos")
    assert lk["status"] == "found" and "pricing" not in lk and env["priced"] == []     # a listing: no venue panel
    card = lk["matches"][0]
    assert (card["vendor_id"], card["category"]) == ("P1", "Photography") and "image" not in card
    _free(env)
    locked = mcp_server.lookup_venue("Bearer good", "OliviaPhotos")
    assert locked["matches"][0]["name"] == "OliviaPhotos" and locked["matches"][0]["description"] == "Documentary style."
    _assert_no_prices(locked)


def test_lookup_with_several_matches_returns_them_all(env):
    lk = mcp_server.lookup_venue("Bearer good", "Ashton Gardens")
    assert lk["status"] == "choices" and "pricing" not in lk
    assert sorted(c["vendor_id"] for c in lk["matches"]) == ["A1", "A2"]
    assert lk["matches"][0]["venue_fee_range"][0] and sorted(env["priced"]) == [("A1", True), ("A2", True)]
    one = mcp_server.lookup_venue("Bearer good", "Ashton Gardens", "Ohio")              # the location picks one
    assert one["status"] == "found" and [c["vendor_id"] for c in one["matches"]] == ["A2"]
    _free(env)
    env["priced"].clear()
    locked = mcp_server.lookup_venue("Bearer good", "Ashton Gardens")
    assert sorted(c["name"] for c in locked["matches"]) == ["Ashton Gardens North", "Ashton Gardens West"]
    assert env["priced"] == []                                   # several matches, not full: nothing is priced
    _assert_no_prices(locked)


# ---------------------------------------------------------------- end to end through the mounted ASGI app

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


@pytest.fixture
def client(env, monkeypatch):
    """A fresh FastAPI host with /mcp mounted (the real app's startup would start the pricing warmer)."""
    pytest.importorskip("mcp")
    monkeypatch.setattr(config, "MCP_ALLOWED_HOSTS", ["testserver"])
    host = FastAPI()

    @host.get("/health")
    def health():
        return {"ok": True}
    assert mcp_server.mount(host) is not None
    with TestClient(host) as c:                                  # the context manager runs the lifespan
        yield c


def _rpc(client, method, params=None, token="good", rid=1):
    h = dict(HEADERS, **({"Authorization": "Bearer " + token} if token else {}))
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_end_to_end_over_streamable_http(env, client):
    assert client.get("/health").json() == {"ok": True}          # the host's own routes still answer
    assert _rpc(client, "initialize", INIT["params"])["result"]["serverInfo"]["name"] == "tulle"
    tools = _rpc(client, "tools/list")["result"]["tools"]
    assert sorted(t["name"] for t in tools) == sorted(mcp_server.TOOLS)
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
    lookup_schema = next(t for t in tools if t["name"] == "lookup_venue")["inputSchema"]
    assert lookup_schema["required"] == ["name"] and "ctx" not in lookup_schema["properties"]

    env["auth"].clear()
    ok = _rpc(client, "tools/call", {"name": "lookup_venue", "arguments": {"name": "Kohl Mansion"}})
    assert not ok["result"].get("isError")
    assert ok["result"]["structuredContent"]["matches"][0]["vendor_id"] == "V1"
    assert ok["result"]["structuredContent"]["pricing"]["lines"][0]["amount"] == 12345
    assert env["auth"] == ["Bearer good"]                        # auth/me is read ONCE per request (gate -> tool)

    bad = _rpc(client, "tools/call", {"name": "lookup_venue", "arguments": {"name": "a"}})     # a tool's own refusal
    assert bad["result"]["isError"] is True and "at least 3 letters" in json.dumps(bad["result"])
    _drain(env)
    assert [(r["kind"], r["status"]) for r in env["logged"]] == [("mcp_lookup_venue", "ok"), ("mcp_lookup_venue", "error")]


@pytest.mark.parametrize("method", ["get", "delete", "put", "patch"])
@pytest.mark.parametrize("token", [None, "good"])
def test_only_post_reaches_the_sdk_so_no_stream_is_ever_opened(env, client, method, token):
    """GET with Accept: text/event-stream used to be answered by the SDK with an open-ended event stream, token or not.
    (If it still did, this request would never return.)"""
    h = {"Accept": "text/event-stream", **({"Authorization": "Bearer " + token} if token else {})}
    r = getattr(client, method)("/mcp", headers=h)
    assert r.status_code == 405 and r.headers["allow"] == "POST"
    assert r.headers["content-type"] == "application/json" and "POST only" in r.json()["error"]
    assert env["auth"] == []                                     # refused before anything else is looked at


@pytest.mark.parametrize("body", [INIT, LIST, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                               "params": {"name": "get_my_profile", "arguments": {}}}])
@pytest.mark.parametrize("auth", [None, "Bearer bad", "good", "bearer good"])
def test_every_mcp_request_needs_a_valid_token_before_the_sdk_sees_it(env, client, body, auth):
    r = client.post("/mcp", json=body, headers=dict(HEADERS, **({"Authorization": auth} if auth else {})))
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert "Not signed in" in r.json()["error"] and "tools" not in r.text and "Ana" not in r.text
    assert env["logged"] == []


def test_gate_allowlist_kill_switch_and_sign_in_failure(env, client, monkeypatch):
    post = lambda: client.post("/mcp", json=LIST, headers=dict(HEADERS, Authorization="Bearer good"))   # noqa: E731
    assert post().status_code == 200
    monkeypatch.setattr(config, "MCP_USER_IDS", frozenset({8}))
    r = post()
    assert r.status_code == 403 and "not enabled for this account" in r.json()["error"] and "tools" not in r.text
    monkeypatch.setattr(config, "MCP_USER_IDS", frozenset({7}))
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    assert post().status_code == 503
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(appmod, "_auth", lambda a: (_ for _ in ()).throw(OSError("https://xqtb.xano.io/auth/me timed out")))
    r = post()
    assert r.status_code == 503 and "xano" not in r.text.lower() and "could not verify" in r.json()["error"]


def test_host_allowlist(env, client):
    post = lambda host: client.post("/mcp", json=LIST, headers=dict(HEADERS, Authorization="Bearer good", Host=host))   # noqa: E731
    assert post("testserver").status_code == 200 and post("testserver:8443").status_code == 200
    env["auth"].clear()
    for host in ("evil.example", "testserver.evil.example", "eviltestserver", "localhost", "127.0.0.1:8000"):
        r = post(host)
        assert r.status_code == 421 and "tools" not in r.text, host
    assert env["auth"] == []                                     # a wrong host is refused before the token is read


def test_mount_without_allowed_hosts_adds_nothing(env, monkeypatch, capsys):
    monkeypatch.setattr(config, "MCP_ALLOWED_HOSTS", [])
    host = FastAPI()
    before = host.router.lifespan_context
    assert mcp_server.mount(host) is None
    assert not [r for r in host.routes if getattr(r, "path", "") == "/mcp"] and host.router.lifespan_context is before
    assert capsys.readouterr().out.count("REC_MCP_ALLOWED_HOSTS is empty - /mcp is NOT mounted") == 1
    assert TestClient(host).post("/mcp", json=LIST).status_code == 404


@pytest.mark.parametrize("origin", ["https://editor.weweb.io", "https://tulletogether.app", "http://testserver", "null"])
def test_any_request_with_an_origin_header_is_refused(env, client, origin):
    """Browser pages (the app's CORS allows *.weweb.io and the live site) may not call /mcp, token or not."""
    r = client.post("/mcp", json=LIST, headers=dict(HEADERS, Authorization="Bearer good", Origin=origin))
    assert r.status_code == 403 and "web pages" in r.json()["error"] and "tools" not in r.text
    assert env["auth"] == []
