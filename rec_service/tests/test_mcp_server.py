"""Read-only MCP prototype (mcp_server.py, 2026-11-15). No network: Xano is mocked, the SDK runs in-process.
DATA POLICY tests: the exact tool list, a walker over every tool's output for every tier (no pricing, public cards
only), no pricing function ever called, the couple's own numbers still returned, the daily lookup cap."""
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app as appmod
import config
import cost_tools
import guards
import mcp_server
import xano

HERE = pathlib.Path(__file__).resolve().parent.parent
T = "2026-11-15"
# The couple's OWN numbers (these must come back): budget 60000, booked 12505 / paid 4000, a 900 quote, 9000 extra.
USER = {"id": 7, "first_name": "Ana", "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 120,
        "Wedding_Budget": 60000, "favorited_vendors": ["V101", "V202", "V303"], "email": "ana@example.com"}
PACK = [{"id": 1, "user_id": 7, "vendor_name": "Example 1: Venue", "total_cost": 30000, "Selected": True, "Category": "Venue"},
        # booked from a Tulle card, but the couple typed their own agreed price (not the pre-filled 12345)
        {"id": 2, "user_id": 7, "vendor_name": "Kohl Mansion", "total_cost": 12505, "Balance_Paid": 4000,
         "Selected": True, "Category": "Venue", "vendor_id": "V101", "Notes": "LEAK note on a Tulle-linked row",
         "Contact_Information": "LEAK contact", "Package_Name": "LEAK Saturday package · tulle:V101",
         "Files": {"name": "contract.pdf", "url": "https://files.example/vault/SECRETFILE/contract.pdf"}},
        # a cost the couple added themselves ("+ Add a cost")
        {"id": 3, "user_id": 7, "vendor_name": "Catering Co", "total_cost": 9000, "Selected": True,
         "Category": "Food and Beverage", "Notes": "deposit due in May", "Contact_Information": "ask for Dana",
         "Package_Name": "Booked in My wedding · tulle:manual"},
        {"id": 4, "user_id": 7, "vendor_name": "Old DJ", "total_cost": 900, "Selected": False, "Category": "Entertainment",
         "Package_Name": "Gold package"},
        # Mark booked with the PRE-FILLED "Agreed price": the stored amount is Tulle's hint for V202 (2222)
        {"id": 5, "user_id": 7, "vendor_name": "OliviaPhotos", "total_cost": 2222, "Balance_Paid": 500,
         "Selected": True, "Category": "Photography", "Package_Name": "Booked in My wedding · tulle:V202"},
        # still considering, linked through the v1 Notes marker, amount = Tulle's hint for V101 (12345)
        {"id": 6, "user_id": 7, "vendor_name": "Kohl Mansion", "total_cost": 12345, "Selected": False,
         "Category": "Venue", "Notes": "tulle:V101"}]


def tulle_row(vid, name, category="Venue", state="Texas", city="Austin", **extra):
    """A vendor as Tulle's data holds it, with a price or other forbidden value in EVERY field a card can carry.
    Every forbidden value contains a number from FORBIDDEN_NUMBERS or the word LEAK."""
    return dict({
        "vendor_id": vid, "vendor_idx": 987651, "name": name, "state": state, "category": category,
        "address": "4321 LEAK Street, %s, TX 78701" % city, "google_name": "LEAK google name",
        "venue_type": ["Estate / Mansion"] if category == "Venue" else "Documentary",
        "max_capacity_seated": 287, "venue_fee_range": [12345, 15678], "price_range": [2222, 4444],
        "food_minimum": 31313, "guest_minimum": 83, "per_head": 233, "price_from": 5151, "price_to": 6161,
        "price_label": "from $5,151", "price_band": {"low": 10000, "high": 15000, "label": "rental about $10K-$15K"},
        "estimate_label": "About $41,414 for 120 guests", "estimate_total": 41414, "estimate_guests": 120,
        "description": "LEAK description. Rental starts at $12,345.", "at_a_glance": ["LEAK glance", "$233 a head"],
        "image": "https://img.example/LEAK.jpg", "pdf_link": "https://files.example/LEAK.pdf", "pdf_count": 7,
        "package_count": 5, "packages_label": "5 packages from $2,222", "hours_label": "8 hours",
        "phone": "512-555-0199", "email": "LEAK@vendor.example", "website": "https://LEAK.example",
        "lat": 30.2672, "lng": -97.7431, "google_rating": 4.7, "google_review_count": 311,
        "florist_pricing": {"typical_total": 7373}, "reason": "LEAK reason"}, **extra)


CARDS = {"V101": tulle_row("V101", "Kohl Mansion", price_range=None),      # app's price hint: 12345 (rental low end)
         "V202": tulle_row("V202", "OliviaPhotos", category="Photography"),  # app's price hint: 2222 (package low end)
         "V303": tulle_row("V303", "Bloomery", category="Beauty", city="Dallas")}
TWINS = [tulle_row("V40%d" % i, "Ashton Gardens %s" % n, state=st, city=c)
         for i, (n, st, c) in enumerate([("North", "Georgia", "Atlanta"), ("West", "Ohio", "Columbus"),
                                         ("East", "Ohio", "Dayton"), ("South", "Utah", "Provo")], 1)]
# Names a generic word would browse to if find_vendor were a search (V3).
BROWSE = [tulle_row("V50%d" % i, n, state=st, city=c)
          for i, (n, st, c) in enumerate([("Brooklyn Winery", "New York", "Brooklyn"), ("The Brooklyn Barn", "New York", "Brooklyn"),
                                          ("Hotel Emma", "Texas", "Dallas"), ("The Barn at Forest Hill", "Texas", "Waco"),
                                          ("Garden Hotel", "Texas", "Dallas")], 1)]
FORBIDDEN_NUMBERS = ("12345", "12,345", "15678", "2222", "2,222", "4444", "31313", "5151", "5,151", "6161", "41414",
                     "41,414", "7373", "987651", "287", "233", "311", "4321", "78701", "555-0199", "30.26", "97.74")
CARD_KEYS = {"name", "city", "state", "category", "venue_type", "tulle_url"}   # the public card: nothing else, ever
PAGE = re.compile(r"https://www\.tulletogether\.app/vendors/V\d{1,9}")           # the one place an id may show
CARD_PLACES = {"matches": CARD_KEYS, "vendors": CARD_KEYS, "saved": CARD_KEYS, "saved_other": CARD_KEYS,
               "candidates": {"name", "state"}, "also": {"name", "state"}}
ROW_KEYS = {"name", "category", "price", "paid", "amount_hidden", "notes", "contact", "package", "file"}   # a plan row
ROW_PLACES = ("quotes", "extras", "considering")
PRICING_FUNCTIONS = ("venue_pricing", "cached_venue_pricing", "market_benchmarks", "cached_market_benchmarks",
                     "pe_benchmarks", "florist_pricing", "venue_details", "pdf_rows", "state_average_rows",
                     "guest_modifier", "budget_categories", "venue_quotes", "search_venues", "search_pe",
                     "search_listings")


def walk(obj):
    """THE REGRESSION GUARD. Fails if any tool output holds (1) a vendor card with a key outside the allowlist, (2) a
    number or "$" anywhere inside a vendor card, or (3) any value of Tulle's data other than the card fields - found
    by the marker every such mocked value carries (LEAK / FORBIDDEN_NUMBERS)."""
    dump = json.dumps(obj)
    assert "LEAK" not in dump and not any(n in dump for n in FORBIDDEN_NUMBERS), dump

    def card(c, allowed, where):
        assert isinstance(c, dict) and set(c) <= allowed, (where, sorted(set(c) - allowed))
        for k, v in c.items():
            if k == "tulle_url":
                assert isinstance(v, str) and PAGE.fullmatch(v), (where, v)
                continue
            for x in (v if isinstance(v, list) else [v]):
                assert isinstance(x, str) and "$" not in x and not re.search(r"\d", x), (where, k, x)

    def go(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in CARD_PLACES:
                    assert isinstance(v, list), (path, k)
                    for c in v:
                        card(c, CARD_PLACES[k], path + "." + k)
                elif k in ROW_PLACES or k == "booked":
                    for row in (v if isinstance(v, list) else [v] if v else []):
                        assert set(row) <= ROW_KEYS and "id" not in row and "vendor_id" not in row, (path, k, sorted(row))
                        assert not (row.get("amount_hidden") and ("price" in row or "paid" in row)), (path, k, row)
                else:
                    go(v, path + "." + k)
        elif isinstance(o, list):
            for x in o:
                go(x, path + "[]")
    go(obj, "$")
    return obj


@pytest.fixture
def env(monkeypatch):
    """An allowlisted FOREVER couple (id 7) by default. Every pricing function in xano raises if a tool touches it."""
    st = {"user": dict(USER, forever_access_purchased=True), "logged": [], "usage": [], "writes": [], "auth": [],
          "searched": []}
    submit = appmod._submit_usage

    def auth(authorization):
        st["auth"].append(authorization)
        if authorization != "Bearer good":
            raise HTTPException(401, "sign in required")
        return "tok", st["user"]

    def name_search(term, token="", **k):
        cat = k.get("category", "Venue")
        st["searched"].append((term, cat))
        if cat == "Venue":
            return ([dict(CARDS["V101"])] if "kohl" in term else [dict(v) for v in TWINS] if "ashton" in term else
                    [dict(v) for v in BROWSE if any(w in v["name"].lower() for w in term.split())])
        return [dict(CARDS["V202"])] if cat == "Photography" and "olivia" in term else []

    def forbidden(fname):
        def boom(*a, **k):
            raise AssertionError("pricing function called by an MCP tool: %s" % fname)
        return boom
    for fname in PRICING_FUNCTIONS:
        assert hasattr(xano, fname), fname
        monkeypatch.setattr(xano, fname, forbidden(fname))
    for mod, fname in ((appmod, "_find_and_price"), (appmod, "_price_or_none"), (appmod, "_lookup_card"),
                       (cost_tools, "estimate_cost"), (cost_tools, "split_budget"), (cost_tools, "compare_venues")):
        monkeypatch.setattr(mod, fname, forbidden(fname))
    monkeypatch.setattr(appmod, "_auth", auth)
    # The app's own _submit_usage, with its pool futures kept so a test can wait for the rows (st["logged"]).
    monkeypatch.setattr(appmod, "_submit_usage", lambda row: st["writes"].append(submit(row)))
    monkeypatch.setattr(config, "MCP_USER_IDS", frozenset({7}))
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 25)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "BETA_AUDIENCE", "forever")
    monkeypatch.setattr(xano, "log_usage", lambda row: st["logged"].append(row))
    monkeypatch.setattr(xano, "user_usage", lambda uid: list(st["usage"]))
    monkeypatch.setattr(xano, "today", lambda: T)
    monkeypatch.setattr(xano, "vendor_card", lambda vid: dict(CARDS[vid]) if vid in CARDS else None)
    monkeypatch.setattr(xano, "cached_vendor_card", lambda vid, stats=None: dict(CARDS[vid]) if vid in CARDS else None)
    monkeypatch.setattr(xano, "user_packages", lambda tok, uid: [dict(p) for p in PACK])
    monkeypatch.setattr(xano, "name_search", name_search)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    mcp_server._LOOKUPS.clear()
    yield st
    mcp_server._LOOKUPS.clear()


def _full(st):
    st["user"] = dict(USER, forever_access_purchased=True)


def _light(st):
    """An active plan that is not FULL (REC_BETA_AUDIENCE=forever: a 1-week / 4-week buyer)."""
    st["user"] = dict(USER, forever_access_purchased=False, date_until_access="2099-01-01")
    assert xano.has_paid_access(st["user"]) and not xano.beta_member(st["user"])


def _free(st):
    st["user"] = dict(USER, forever_access_purchased=False, date_until_access=None)


TIERS = {"full": _full, "light": _light, "free": _free}
ARGS = {"find_vendor": ["Kohl Mansion"]}


def _drain(st):
    for f in st["writes"]:                           # usage rows are written on the app's pool
        f.result(timeout=10)


def _calls():
    """Every tool once, as the plain functions the MCP wrappers call."""
    return {name: getattr(mcp_server, name)("Bearer good", *ARGS.get(name, [])) for name in mcp_server.TOOLS}


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


KEY = "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s="       # base64 of 32 bytes (b"k" * 32)
ON = {"REC_MCP": "1", "REC_MCP_USER_IDS": "7, 42,abc,", "REC_MCP_PUBLIC_URL": "https://tulle.test", "REC_MCP_TOKEN_KEY": KEY}


def test_flag_on_mounts_mcp_on_the_real_app_and_startup_runs_exactly_once():
    pytest.importorskip("mcp")
    code = STUB + (
        "import app, config\n"
        "from fastapi.testclient import TestClient\n"
        "assert config.MCP_ENABLED and config.MCP_USER_IDS == frozenset({7, 42}) and not config.MCP_ALL_USERS\n"
        "paths = [getattr(r, 'path', '') for r in app.app.routes]\n"
        "assert paths.count('/mcp') == 1 and '/token' in paths and '/authorize' in paths and '/oauth/approve' in paths\n"
        "with TestClient(app.app, base_url='https://tulle.test') as c:\n"
        "    assert starts == [1], starts\n"
        "    assert c.get('/health').status_code == 200\n"
        "    assert c.get('/mcp').status_code == 405\n"
        "    r = c.post('/mcp', json={})\n"
        "    assert r.status_code == 401 and 'resource_metadata=' in r.headers['www-authenticate']\n"
        "    assert c.get('/.well-known/oauth-protected-resource/mcp').json()['resource'] == 'https://tulle.test/mcp'\n"
        "assert starts == [1], starts\n"
        "print('OK')")
    r = _fresh(code, **ON)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


@pytest.mark.parametrize("missing,line", [("REC_MCP_PUBLIC_URL", "REC_MCP_PUBLIC_URL is empty"),
                                          ("REC_MCP_TOKEN_KEY", "REC_MCP_TOKEN_KEY is missing or is not base64 of at least 32 bytes")])
def test_flag_on_without_public_url_or_key_does_not_mount_and_says_why(missing, line):
    code = STUB + (
        "import app, config\n"
        "from fastapi.testclient import TestClient\n"
        "assert config.MCP_ENABLED\n"
        "paths = [getattr(r, 'path', '') for r in app.app.routes]\n"
        "assert not [p for p in paths if p.startswith(('/mcp', '/oauth', '/authorize', '/token', '/.well-known'))]\n"
        "assert 'mcp' not in sys.modules\n"
        "with TestClient(app.app) as c:\n"
        "    assert c.post('/mcp', json={}).status_code == 404 and starts == [1]\n"
        "print('OK')")
    r = _fresh(code, **{k: v for k, v in ON.items() if k != missing})
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]
    assert r.stdout.count(line) == 1 and r.stdout.count("/mcp is NOT mounted") == 1


BLOCK = ("class Block:\n"
         "    def find_spec(self, name, path=None, target=None):\n"
         "        if name.split('.')[0] in BLOCKED:\n"
         "            raise ImportError(name + ' is not installed (simulated)')\n"
         "sys.meta_path.insert(0, Block())\n")


def test_import_app_works_without_mcp_and_without_cryptography():
    """Flag off: neither package is touched. Flag on without `cryptography`: not mounted, one line says why."""
    code = STUB + "BLOCKED = ('mcp', 'mcp_types', 'cryptography')\n" + BLOCK + (
        "import app, config\n"
        "assert not [r for r in app.app.routes if getattr(r, 'path', '').startswith(('/mcp', '/oauth', '/token'))]\n"
        "assert not {'mcp', 'cryptography', 'mcp_server', 'mcp_oauth'} & set(sys.modules)\n"
        "print('OK', config.MCP_ENABLED)")
    off = _fresh(code)
    assert off.returncode == 0 and "OK False" in off.stdout, off.stderr[-2000:]
    on = _fresh(code.replace("assert not {'mcp', 'cryptography', 'mcp_server', 'mcp_oauth'}", "assert not {'mcp', 'cryptography'}"), **ON)
    assert on.returncode == 0 and "OK True" in on.stdout, on.stderr[-2000:]
    assert "the `cryptography` package is not installed - /mcp is NOT mounted" in on.stdout


# ---------------------------------------------------------------- (b) (c) (f) refusals: an error, no data, no Xano read

@pytest.mark.parametrize("header", [None, "", "Bearer bad", "good", "bearer good", "Bearer ", "Basic good", 7])
def test_no_or_invalid_token_is_refused(env, monkeypatch, header):
    monkeypatch.setattr(xano, "profile_of", lambda u: (_ for _ in ()).throw(AssertionError("no data read")))
    for name in mcp_server.TOOLS:
        with pytest.raises(mcp_server.MCPDenied, match="Not signed in"):
            getattr(mcp_server, name)(header, *ARGS.get(name, []))
    _drain(env)
    assert env["logged"] == [] and env["searched"] == []


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
            getattr(mcp_server, name)("Bearer good", *ARGS.get(name, []))
        assert str(e.value) == "Tulle could not verify the sign-in right now. Try again in a moment."
        assert e.value.status == 503 and "xano" not in str(e.value).lower()
    assert "mcp sign-in check failed" in capsys.readouterr().out      # server side only
    assert env["logged"] == [] and env["searched"] == []


@pytest.mark.parametrize("allow", [frozenset(), frozenset({8, 70})])
def test_valid_user_not_on_the_allowlist_is_refused(env, monkeypatch, allow):
    monkeypatch.setattr(config, "MCP_USER_IDS", allow)       # empty (the default) = nobody
    monkeypatch.setattr(xano, "profile_of", lambda u: (_ for _ in ()).throw(AssertionError("no data read")))
    for name in mcp_server.TOOLS:
        with pytest.raises(mcp_server.MCPDenied, match="not enabled for this account"):
            getattr(mcp_server, name)("Bearer good", *ARGS.get(name, []))
    _drain(env)
    assert env["logged"] == [] and env["searched"] == []


def test_kill_switch_refuses_every_tool(env, monkeypatch):
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    for name in mcp_server.TOOLS:
        with pytest.raises(mcp_server.MCPDenied, match="switched off"):
            getattr(mcp_server, name)("Bearer good", *ARGS.get(name, []))
    _drain(env)
    assert env["logged"] == [] and env["searched"] == []


# ---------------------------------------------------------------- the tool list (no search, no pricing tool)

EXACT_TOOLS = ["budget_status", "find_vendor", "get_my_profile", "get_my_wedding", "list_saved_vendors"]


def test_the_exact_tool_list():
    assert sorted(mcp_server.TOOLS) == EXACT_TOOLS
    for gone in ("lookup_venue", "list_saved_venues", "estimate_cost", "estimate_wedding_cost", "compare_venues",
                 "split_budget", "list_vendors", "search_vendors", "search_venues", "next_step"):
        assert not hasattr(mcp_server, gone), gone
    banned = re.compile(r"search|filter|browse|list_vendors|estimate|compare|price|pricing|cost|benchmark|market")
    assert not [t for t in mcp_server.TOOLS if banned.search(t)]


ORDERS = re.compile(r"\b(you must|you should|always|never|do not|don't|make sure|be sure|please|must|should|"
                    r"ask the|tell the|send the|point the|remind|recommend|best|leading|trusted)\b", re.I)


def test_instructions_and_tool_texts_only_describe_what_is_returned():
    """Directory review rejects instruction-style or promotional descriptions (owner 2026-11-15)."""
    texts = {"instructions": mcp_server.INSTRUCTIONS}
    texts.update({n: "%s. %s" % mcp_server.TOOL_TEXT[n] for n in mcp_server.TOOLS})
    assert set(mcp_server.TOOL_TEXT) == set(mcp_server.TOOLS)
    for name, text in texts.items():
        assert not ORDERS.search(text), (name, ORDERS.search(text).group(0))
    for n in ("get_my_wedding", "list_saved_vendors", "find_vendor"):
        assert "Does not include prices" in mcp_server.TOOL_TEXT[n][1]
    for n in mcp_server.TOOLS:                                   # F5: purely descriptive - no pointer to the site
        assert "tulletogether.app" not in mcp_server.TOOL_TEXT[n][1] and "pricing lives" not in mcp_server.TOOL_TEXT[n][1]
    assert all(mcp_server.TOOL_TEXT[n][1].startswith("Returns ") and len(n) <= 64 and mcp_server.TOOL_TEXT[n][0]
               for n in mcp_server.TOOLS)


# ---------------------------------------------------------------- the walker: every tool, every tier

@pytest.mark.parametrize("tier", list(TIERS))
def test_no_pricing_and_only_public_cards_in_any_tool_for_any_tier(env, tier):
    """The mocked Tulle rows carry a price / free text / contact / id in every field (tulle_row); every pricing
    function raises (env). Nothing but the public card may come out - the same for every tier."""
    TIERS[tier](env)
    out = walk(_calls())
    json.dumps(out)
    for name, args in (("OliviaPhotos", []), ("Ashton Gardens", []), ("Ashton Gardens", ["Ohio"]), ("Zzyzx Ballroom", [])):
        walk(mcp_server.find_vendor("Bearer good", name, *args))
    assert out["find_vendor"]["matches"] == [{"name": "Kohl Mansion", "state": "Texas", "category": "Venue",
                                              "venue_type": ["Estate / Mansion"],
                                              "tulle_url": "https://www.tulletogether.app/vendors/V101"}]
    assert out["list_saved_vendors"]["vendors"][1] == {"name": "OliviaPhotos", "state": "Texas",
                                                       "category": "Photography", "venue_type": ["Documentary"],
                                                       "tulle_url": "https://www.tulletogether.app/vendors/V202"}
    assert "Austin" not in json.dumps(out) and "Dallas" not in json.dumps(out)      # nothing is read out of an address
    assert out["get_my_profile"]["plan"] == tier


def test_every_tier_gets_the_same_vendor_data(env):
    got = {}
    for tier, set_tier in TIERS.items():
        set_tier(env)
        o = _calls()
        o["get_my_profile"].pop("plan")
        got[tier] = o
    assert got["full"] == got["light"] == got["free"]


def test_the_walker_itself_catches_what_it_should():
    ok = {"matches": [{"name": "Kohl Mansion", "city": "Austin", "state": "Texas", "category": "Venue"}],
          "extras": [{"name": "Catering Co", "category": "Food and Beverage", "price": 9000, "paid": 0, "notes": "ours"}]}
    walk(ok)
    for bad in ({"matches": [dict(ok["matches"][0], max_capacity_seated=200)]},
                {"matches": [dict(ok["matches"][0], description="Lovely")]},
                {"vendors": [dict(ok["matches"][0], venue_type=["From $9 a head"])]},
                {"saved": [dict(ok["matches"][0], city="12 Main St")]},
                {"candidates": [dict(ok["matches"][0])]},                      # a candidate is name + state only
                {"also": [{"name": "A", "city": "Austin"}]},
                {"extras": [dict(ok["extras"][0], id=3)]}, {"quotes": [dict(ok["extras"][0], vendor_id="V1")]},
                {"considering": [dict(ok["extras"][0], amount_hidden=True)]},   # hidden AND still carrying the amount
                {"categories": [{"booked": dict(ok["extras"][0], estimate=1)}]},
                {"x": {"y": [{"also": [{"name": "A", "vendor_id": "V1"}]}]}},
                {"also": [{"name": "A", "tulle_url": "https://www.tulletogether.app/vendors/V1"}]},   # candidates: no link
                {"matches": [dict(ok["matches"][0], tulle_url="https://www.tulletogether.app/vendors/V1?ref=9")]},
                {"matches": [dict(ok["matches"][0], tulle_url="https://evil.example/vendors/V1")]},
                {"note": "Rental starts at $12,345."}, {"anything": {"deep": ["LEAK"]}}):
        with pytest.raises(AssertionError):
            walk(bad)


def test_public_card_copies_nothing_but_its_fields():
    c = mcp_server.public_card(CARDS["V101"])
    assert c == {"name": "Kohl Mansion", "state": "Texas", "category": "Venue",
                 "venue_type": ["Estate / Mansion"], "tulle_url": "https://www.tulletogether.app/vendors/V101"}
    assert set(c) <= set(mcp_server.CARD_FIELDS) and mcp_server._candidate(CARDS["V101"]) == {"name": "Kohl Mansion", "state": "Texas"}
    for odd_id in ("", None, "P1", "V", "V12/../x", "V1?x=1", "v101", 101, "V1234567890"):   # only a real V-id links
        assert "tulle_url" not in mcp_server.public_card(dict(CARDS["V101"], vendor_id=odd_id)), odd_id
    odd = mcp_server.public_card({"name": "Barn 9", "state": ["Ohio"], "address": "77 Elm Rd",
                                  "venue_type": ["Barn / Ranch", "From $5,000", 7, "Seats 200"], "category": "Venue"})
    assert odd == {"name": "Barn 9", "state": "Ohio", "category": "Venue", "venue_type": ["Barn / Ranch"]}


@pytest.mark.parametrize("address", ["Austin, Private Lane", "TX", "Private Lane, Austin", "12 Main St, Suite 4",
                                     "The Old Mill, Austin, TX 78701", "Austin", ""])
def test_city_is_never_parsed_out_of_an_address(address):
    """V4: 'Austin, Private Lane' used to come back as city 'Private Lane', and a bare 'TX' as the city."""
    c = mcp_server.public_card({"name": "X Hall", "state": "Texas", "address": address})
    assert c == {"name": "X Hall", "state": "Texas", "category": "Venue"}
    assert mcp_server._candidate({"name": "X Hall", "state": "Texas", "address": address}) == {"name": "X Hall", "state": "Texas"}


def test_city_comes_only_from_a_structured_field():
    assert mcp_server.public_card({"name": "X Hall", "state": "Texas", "city": "Austin"})["city"] == "Austin"
    for bad in ("12 Main St", "From $5", ["Austin"], 7, None):
        assert "city" not in mcp_server.public_card({"name": "X Hall", "state": "Texas", "city": bad}), bad


@pytest.mark.parametrize("name", ["Barn from $4,500", "The $99 Chapel", "Venue (USD 5000)", "Hall - 3000 dollars", "€2000 Loft"])
def test_a_vendor_name_carrying_a_currency_amount_is_dropped(env, monkeypatch, name):
    assert "name" not in mcp_server.public_card({"name": name, "state": "Texas", "vendor_id": "V9"})
    assert mcp_server.public_card({"name": "Studio 54", "state": "Texas"})["name"] == "Studio 54"      # a number is not a price
    monkeypatch.setattr(xano, "vendor_card", lambda vid: {"name": name, "state": "Texas", "vendor_id": "V9"})
    assert "name" not in mcp_server.list_saved_vendors("Bearer good")["vendors"][0]


# ---------------------------------------------------------------- the couple's own numbers ARE returned

@pytest.mark.parametrize("tier", list(TIERS))
def test_get_my_wedding_returns_what_the_couple_entered(env, tier):
    TIERS[tier](env)
    w = walk(mcp_server.get_my_wedding("Bearer good"))
    cats = {c["key"]: c for c in w["categories"]}
    # their own numbers: the budget, a booked vendor at the price THEY agreed, a cost they added, a quote
    assert w["budget"] == 60000
    assert cats["Venue"]["booked"] == {"name": "Kohl Mansion", "category": "Venue", "price": 12505, "paid": 4000,
                                       "file": {"name": "contract.pdf"}}       # their file's name, never its link
    assert w["extras"] == [{"name": "Catering Co", "category": "Food and Beverage", "price": 9000, "paid": 0,
                            "notes": "deposit due in May", "contact": "ask for Dana"}]      # a row they created: all theirs
    assert cats["Entertainment"]["quotes"] == [{"name": "Old DJ", "category": "Entertainment", "price": 900, "paid": 0,
                                                "package": "Gold package"}]
    assert "SECRETFILE" not in json.dumps(w)
    # Tulle's vendors in it: public cards only
    assert cats["Venue"]["saved"] == [mcp_server.public_card(CARDS["V101"])]
    assert cats["Photography"]["saved"][0]["name"] == "OliviaPhotos"
    assert [c["name"] for c in w["saved_other"]] == ["Bloomery"] and "show_prices" not in w


@pytest.mark.parametrize("tier", list(TIERS))
def test_an_amount_that_equals_tulles_prefilled_hint_is_hidden_and_out_of_every_total(env, tier):
    """V1: Mark booked pre-fills "Agreed price" with Tulle's price hint; at 221fb7d that figure came back as the
    couple's own (booked 2222, and 2222 inside booked_total / slices / budget_status)."""
    TIERS[tier](env)
    w = walk(mcp_server.get_my_wedding("Bearer good"))
    cats = {c["key"]: c for c in w["categories"]}
    assert cats["Photography"]["booked"] == {"name": "OliviaPhotos", "category": "Photography", "amount_hidden": True}
    assert cats["Venue"]["quotes"] == [{"name": "Kohl Mansion", "category": "Venue", "amount_hidden": True}]
    assert w["amounts_hidden"] == 2 and w["note"] == "Some booked amounts are shown only in the Tulle app."
    # no total, remainder or slice can give the figure back
    assert (w["booked_total"], w["left"], w["paid_total"]) == (21505, 38495, 4000)
    assert w["slices"] == [{"label": "Venue", "amount": 12505}, {"label": "Food and Beverage", "amount": 9000}]
    b = walk(mcp_server.budget_status("Bearer good"))
    assert b["status"] == {"budget": 60000, "added": 21505, "paid": 4000, "owed": 17505, "remaining": 38495,
                           "pct_of_budget": 36, "item_count": 2,
                           "by_category": [{"category": "Venue", "added": 12505, "paid": 4000, "owed": 8505},
                                           {"category": "Food and Beverage", "added": 9000, "paid": 0, "owed": 9000}]}
    assert b["amounts_hidden"] == 1 and b["note"] == w["note"] and "$21,505" in b["text"]
    for out in (w, b):                                 # 2222 / 12345 = the hints; 23727 and 4500 = totals with them in
        dump = json.dumps(out)
        assert not any(n in dump for n in ("2222", "12345", "23727", "4500", "36273")), dump


def test_which_amounts_count_as_tulle_derived():
    cards = {"V101": CARDS["V101"], "V202": CARDS["V202"], "V9": {"vendor_id": "V9", "name": "No Hint Hall"}}
    row = lambda **k: dict({"id": 1, "vendor_name": "x", "Selected": True}, **k)      # noqa: E731
    hidden = lambda p: bool(mcp_server.hidden_amounts([p], cards))                   # noqa: E731
    assert hidden(row(vendor_id="V101", total_cost=12345)) and hidden(row(vendor_id="V202", total_cost=2222))
    assert hidden(row(vendor_id="V101", total_cost="12345")) and hidden(row(vendor_id="V101", total_cost=12345.0))
    assert hidden(row(Package_Name="Booked in My wedding · tulle:V101", total_cost=12345))
    assert hidden(row(Notes="tulle:V202", total_cost=2222))
    assert not hidden(row(vendor_id="V101", total_cost=12346))             # the couple typed their own price
    assert not hidden(row(vendor_id="V101", total_cost=15678))             # not the pre-filled figure (the low end is)
    assert not hidden(row(total_cost=12345))                               # "+ Add a cost": not linked to a vendor
    assert not hidden(row(Package_Name="Booked in My wedding · tulle:manual", total_cost=2222))
    assert not hidden(row(vendor_id="V9", total_cost=5000))                # the vendor has no hint: nothing to pre-fill
    assert not hidden(row(vendor_id="V404", total_cost=5000))              # vendor card not found
    assert not hidden(row(vendor_id="V101", total_cost=0))


def test_the_hint_is_read_for_booked_vendors_that_are_no_longer_saved(env):
    env["user"] = dict(env["user"], favorited_vendors=[])                  # un-saved after booking
    w = walk(mcp_server.get_my_wedding("Bearer good"))
    assert w["amounts_hidden"] == 2 and w["booked_total"] == 21505
    assert all(not c["saved"] for c in w["categories"])


@pytest.mark.parametrize("tier", list(TIERS))
def test_budget_status_is_the_couples_own_arithmetic(env, tier, monkeypatch):
    TIERS[tier](env)
    monkeypatch.setattr(xano, "user_packages", lambda tok, uid: [dict(p) for p in PACK if p["id"] in (1, 2, 3, 4)])
    b = walk(mcp_server.budget_status("Bearer good"))
    assert b["status"]["added"] == 21505 and b["status"]["item_count"] == 2 and b["amounts_hidden"] == 0 and "note" not in b
    w = mcp_server.get_my_wedding("Bearer good")
    assert w["amounts_hidden"] == 0 and "note" not in w


def test_profile_is_the_couples_own_answers(env):
    p = mcp_server.get_my_profile("Bearer good")
    assert p == {"profile": xano.profile_of(env["user"]), "plan": "full"} and p["profile"]["Wedding_Budget"] == 60000


# ---------------------------------------------------------------- find_vendor: a strong NAME match only (V3)

def test_find_vendor_exact_name_a_non_venue_and_a_miss(env):
    one = walk(mcp_server.find_vendor("Bearer good", "Kohl Mansion", "Texas"))
    assert one["status"] == "found" and [c["name"] for c in one["matches"]] == ["Kohl Mansion"]
    assert one["note"] == "Prices are not included." and env["searched"] == [("kohl mansion", "Venue")]     # one search
    assert mcp_server.find_vendor("Bearer good", "the kohl mansion")["status"] == "found"
    assert mcp_server.find_vendor("Bearer good", "Kohl Mansion", "Ohio")["status"] == "not_found"          # not in that place
    ph = mcp_server.find_vendor("Bearer good", "OliviaPhotos")
    assert ph["status"] == "found" and ph["matches"][0]["category"] == "Photography"
    env["searched"].clear()
    miss = mcp_server.find_vendor("Bearer good", "Zzyzx Ballroom")
    assert miss["status"] == "not_found" and miss["matches"] == [] and "candidates" not in miss
    assert miss["note"] == "No vendor with that name was found. The lookup matches a vendor's full name."
    assert len(env["searched"]) == 6 == mcp_server.MAX_SEARCHES                 # was 91 upstream searches for one miss
    assert len({c for _, c in env["searched"]}) == 6 and {t for t, _ in env["searched"]} == {"zzyzx ballroom"}


@pytest.mark.parametrize("query,location", [
    ("Brooklyn", ""), ("hotel", ""), ("barn", ""), ("hotel", "Texas"), ("barn texas", ""), ("the hotel", ""),
    ("garden", ""), ("Brooklyn NY", ""), ("New York", ""), ("winery", "Brooklyn"), ("wedding venue", ""), ("venues", "Texas"),
    ("Emma Garden", ""), ("Hill Barn", ""), ("Forest", ""), ("Winery Brooklyn", ""),
    ("Emma", ""), ("Kohl", ""), ("Ashton", ""), ("Hill", ""), ("Olivia", ""), ("Mansion", ""), ("Emma", "Texas")])   # F6: one word
def test_generic_or_partial_words_do_not_browse(env, query, location):
    """V3: 'Brooklyn' returned 3 names and 'hotel' + a state 2. The mocked search returns every vendor containing any
    word of the query - a real browse surface - and nothing may come back."""
    env["searched"].clear()
    r = walk(mcp_server.find_vendor("Bearer good", query, location))
    assert r["status"] == "not_found" and r["matches"] == [] and "candidates" not in r and "also" not in r
    assert "Brooklyn Winery" not in json.dumps(r) and "Hotel Emma" not in json.dumps(r)
    assert len(env["searched"]) <= mcp_server.MAX_SEARCHES


@pytest.mark.parametrize("query,name", [
    ("The Barn at Forest Hill", "The Barn at Forest Hill"),      # the full exact name
    ("barn at forest hill", "The Barn at Forest Hill"),
    ("Forest Hill", "The Barn at Forest Hill"),                  # two words of a three-word name
    ("Hotel Emma", "Hotel Emma"), ("OliviaPhotos", "OliviaPhotos"),   # a one-word query only as the exact, whole name
    ("Brooklyn Winery", "Brooklyn Winery"),                      # only place / type words, but it IS the exact name
    ("The Brooklyn Barn", "The Brooklyn Barn"), ("Garden Hotel", "Garden Hotel")])
def test_a_full_or_nearly_full_name_matches(env, query, name):
    r = walk(mcp_server.find_vendor("Bearer good", query))
    assert r["status"] == "found" and [c["name"] for c in r["matches"]] == [name], r
    assert len(r.get("also") or []) <= 2


def test_one_distinctive_word_cannot_enumerate_vendor_names(env, monkeypatch):
    """F6: with one word enough, 19 of 36 mock vendors were listed in 47 calls. Every single word of every known name is
    tried here; only a vendor whose whole name IS that one word may come back."""
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 10000)
    names = [v["name"] for v in list(CARDS.values()) + TWINS + BROWSE]
    monkeypatch.setattr(xano, "name_search", lambda term, token="", **k: [
        dict(v, category=k.get("category", "Venue")) for v in list(CARDS.values()) + TWINS + BROWSE
        if any(w in v["name"].lower() for w in term.split())])
    words = sorted({w for n in names for w in re.findall(r"[A-Za-z]{4,}", n)})
    found = set()
    for w in words:
        r = mcp_server.find_vendor("Bearer good", w)
        found |= {c["name"] for c in (r.get("matches") or []) + (r.get("candidates") or []) + (r.get("also") or [])}
    assert found == {"OliviaPhotos", "Bloomery"}, found          # the two one-word names, asked for exactly
    assert mcp_server.name_match("Oheka", "Oheka Castle") == 0 and mcp_server.name_match("Oheka Castle", "Oheka Castle") == 2
    assert mcp_server.name_match("Bloomery", "Bloomery") == 2 and mcp_server.name_match("Bloom", "Bloomery") == 0


def test_minimum_four_letters(env):
    for short in ("", " a ", "abc", "a b c", "---", "k.m."):
        with pytest.raises(mcp_server.MCPDenied, match="at least 4 letters"):
            mcp_server.find_vendor("Bearer good", short)
    assert env["searched"] == [] and mcp_server._LOOKUPS == {}                 # refused before the cap and any search


def test_several_matches_give_at_most_three_names_with_state_only(env):
    r = walk(mcp_server.find_vendor("Bearer good", "Ashton Gardens"))            # 4 venues share the name
    assert r["status"] == "choices" and r["matches"] == [] and r["note"] == "Several vendors match that name."
    assert len(r["candidates"]) == 3 and not ORDERS.search(r["note"])
    assert all(set(c) == {"name", "state"} and c["name"].startswith("Ashton Gardens") for c in r["candidates"])
    one = mcp_server.find_vendor("Bearer good", "Ashton Gardens", "Utah")        # the place picks one
    assert one["status"] == "found" and [c["name"] for c in one["matches"]] == ["Ashton Gardens South"]
    exact = walk(mcp_server.find_vendor("Bearer good", "Ashton Gardens North"))  # the exact name: one card + 2 others at most
    assert exact["status"] == "found" and [c["name"] for c in exact["matches"]] == ["Ashton Gardens North"]
    assert "also" not in exact                                                   # the others do not contain "north"
    assert mcp_server.find_vendor("Bearer good", "Ashton")["status"] == "not_found"      # one word of three: not a name


def test_never_more_than_six_upstream_searches(env, monkeypatch):
    calls = []
    monkeypatch.setattr(xano, "name_search", lambda term, token="", **k: calls.append(k.get("category")) or [])
    for q in ("Zzyzx Ballroom", "Jane Doe Photography", "Some Very Long Vendor Name With Many Words", "Qwfp Planning"):
        calls.clear()
        assert mcp_server.find_vendor("Bearer good", q)["status"] == "not_found"
        assert 1 <= len(calls) <= 6 and len(set(calls)) == len(calls), (q, calls)
    assert calls[0] == "Wedding Planner"                         # the type the name points at is searched first
    monkeypatch.setattr(xano, "name_search", lambda *a, **k: (_ for _ in ()).throw(xano.XanoError("down")))
    with pytest.raises(mcp_server.MCPDenied, match="could not run find_vendor"):   # a failed search is not "not found"
        mcp_server.find_vendor("Bearer good", "Zzyzx Ballroom")


# ---------------------------------------------------------------- the daily cap (V2: atomic, in process + persisted)

def _row(kind="mcp_find_vendor", day=T, status="ok", uid=7):
    return {"user_id": uid, "kind": kind, "usage_day": day, "status": status}


def test_daily_lookup_cap_under_at_and_over(env, monkeypatch):
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 3)
    # Not counted: other tools, other days, refused / failed calls.
    noise = [_row("mcp_get_my_wedding"), _row("mcp_list_saved_vendors"), _row(day="2026-11-14"), _row(status="error"),
             _row("refine"), _row("tool_estimate_cost")] * 5
    env["usage"] = noise + [_row()] * 2                                           # under: 2 of 3
    assert mcp_server.find_vendor("Bearer good", "Kohl Mansion")["status"] == "found"
    env["searched"].clear()
    for usage in (noise + [_row()] * 3, noise + [_row()] * 40, noise):           # at, over, and rows not written yet
        env["usage"] = usage
        for name in ("Kohl Mansion", "Zzyzx Ballroom"):
            with pytest.raises(mcp_server.MCPDenied) as e:
                mcp_server.find_vendor("Bearer good", name)
            assert str(e.value) == "Daily lookup limit reached." and e.value.status == 429
    assert env["searched"] == []                                                  # refused before any search
    assert mcp_server.list_saved_vendors("Bearer good")["saved_count"] == 3       # the other tools still answer
    assert mcp_server.get_my_wedding("Bearer good")["budget"] == 60000


@pytest.mark.parametrize("used,cap", [(24, 25), (0, 5), (3, 10), (25, 25), (40, 25)])
def test_fifty_parallel_lookups_cannot_pass_the_cap(env, monkeypatch, used, cap):
    """V2: the cap only counted usage rows, which are written after a call - 50 parallel calls at 24 used all ran."""
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", cap)
    env["usage"] = [_row()] * used                    # the rows on record; none of the new calls' rows arrive in time

    def one(i):
        try:
            return mcp_server.find_vendor("Bearer good", "Kohl Mansion")["status"]
        except mcp_server.MCPDenied as e:
            return str(e)
    with ThreadPoolExecutor(max_workers=50) as ex:
        got = list(ex.map(one, range(50)))
    assert got.count("found") == max(0, cap - used) and got.count("Daily lookup limit reached.") == 50 - max(0, cap - used)
    assert len([1 for t, c in env["searched"] if c == "Venue"]) == max(0, cap - used)


def test_cap_holds_when_the_usage_rows_are_never_written(env, monkeypatch):
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 2)
    monkeypatch.setattr(xano, "log_usage", lambda row: (_ for _ in ()).throw(xano.XanoError("table 79 is down")))
    assert mcp_server.find_vendor("Bearer good", "Zzyzx Ballroom")["status"] == "not_found"      # a miss counts
    assert mcp_server.find_vendor("Bearer good", "Kohl Mansion")["status"] == "found"
    with pytest.raises(mcp_server.MCPDenied, match="Daily lookup limit reached"):
        mcp_server.find_vendor("Bearer good", "Kohl Mansion")
    assert env["usage"] == []


def test_cap_is_per_account_and_per_utc_day(env, monkeypatch):
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 1)
    assert mcp_server.claim_lookup(7) is True and mcp_server.claim_lookup(7) is False
    assert mcp_server.claim_lookup(8) is True                                    # another account
    monkeypatch.setattr(xano, "today", lambda: "2026-11-16")
    assert mcp_server.claim_lookup(7) is True and list(mcp_server._LOOKUPS) == [(7, "2026-11-16")]   # yesterday is dropped


def test_persisted_rows_count_after_a_restart(env, monkeypatch):
    """The in-memory count is lost on a restart; the usage rows are not."""
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 2)
    monkeypatch.setattr(xano, "log_usage", lambda row: env["usage"].append(dict(row, usage_day=T)))
    assert mcp_server.find_vendor("Bearer good", "Zzyzx Ballroom")["status"] == "not_found"
    assert mcp_server.find_vendor("Bearer good", "Qwfp Manor")["status"] == "not_found"
    _drain(env)
    assert [(r["kind"], r["status"]) for r in env["usage"]] == [("mcp_find_vendor", "ok")] * 2
    mcp_server._LOOKUPS.clear()                                                   # the process restarts
    with pytest.raises(mcp_server.MCPDenied, match="Daily lookup limit reached"):
        mcp_server.find_vendor("Bearer good", "Kohl Mansion")


def test_cap_fails_closed_when_usage_cannot_be_read_and_zero_means_none(env, monkeypatch):
    monkeypatch.setattr(xano, "user_usage", lambda uid: (_ for _ in ()).throw(xano.XanoError("table 79 down")))
    with pytest.raises(mcp_server.MCPDenied, match="could not run find_vendor"):
        mcp_server.find_vendor("Bearer good", "Kohl Mansion")
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 0)
    with pytest.raises(mcp_server.MCPDenied, match="Daily lookup limit reached"):
        mcp_server.find_vendor("Bearer good", "Kohl Mansion")
    assert env["searched"] == []


def test_result_notes_and_refusals_are_neutral():
    """V5: no pointing at the site, no orders to the model, in anything a tool returns."""
    for text in (mcp_server.PRICE_NOTE, mcp_server.HIDDEN_NOTE, "Daily lookup limit reached.", "Several vendors match that name.",
                 "No vendor with that name was found. The lookup matches a vendor's full name.",
                 "A vendor name of at least 4 letters is needed."):
        assert not ORDERS.search(text) and "tulletogether.app" not in text, text
    assert "Prices are not included." in mcp_server.INSTRUCTIONS and "tulletogether.app)" in mcp_server.INSTRUCTIONS
    assert "names only" not in json.dumps(mcp_server.TOOL_TEXT)
    for n in ("get_my_wedding", "list_saved_vendors", "find_vendor"):            # descriptions say what a card holds
        assert "name, state, category, type and Tulle page link" in mcp_server.TOOL_TEXT[n][1]


# ---------------------------------------------------------------- logging

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
    monkeypatch.setattr(xano, "user_packages", lambda tok, uid: (_ for _ in ()).throw(xano.XanoError("500 secret")))
    with pytest.raises(mcp_server.MCPDenied) as e:
        mcp_server.budget_status("Bearer good")
    assert "secret" not in str(e.value) and "budget_status" in str(e.value)
    _drain(env)
    assert [(r["kind"], r["status"]) for r in env["logged"]] == [("mcp_budget_status", "error")]


# ---------------------------------------------------------------- end to end through the mounted ASGI app

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


@pytest.fixture
def client(env, monkeypatch):
    """A fresh FastAPI host with /mcp mounted (the real app's startup would start the pricing warmer)."""
    pytest.importorskip("mcp")
    monkeypatch.setattr(config, "MCP_PUBLIC_URL", "https://testserver")
    monkeypatch.setattr(config, "MCP_TOKEN_KEY", KEY)
    monkeypatch.setattr(config, "MCP_ALLOWED_HOSTS", [])        # the public URL's host is allowed by itself
    # The gate tests below pass the couple's raw Xano token: the local-testing switch. Without it /mcp takes only
    # OAuth access tokens - tests/test_mcp_oauth.py covers that path end to end.
    monkeypatch.setattr(config, "MCP_DEV_RAW_TOKEN", True)
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
    init = _rpc(client, "initialize", INIT["params"])["result"]
    assert init["serverInfo"]["name"] == "tulle" and init["instructions"] == mcp_server.INSTRUCTIONS
    tools = _rpc(client, "tools/list")["result"]["tools"]
    assert sorted(t["name"] for t in tools) == EXACT_TOOLS       # what a client really sees: no search, no pricing
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
    assert {t["name"]: (t["title"], t["description"]) for t in tools} == mcp_server.TOOL_TEXT
    schemas = {t["name"]: t["inputSchema"] for t in tools}
    assert schemas["find_vendor"]["required"] == ["name"]
    assert sorted(schemas["find_vendor"]["properties"]) == ["location", "name"]          # a name, never a filter
    assert all(not s.get("properties") for n, s in schemas.items() if n != "find_vendor")

    env["auth"].clear()
    ok = _rpc(client, "tools/call", {"name": "find_vendor", "arguments": {"name": "Kohl Mansion"}})
    assert not ok["result"].get("isError")
    walk(ok["result"])                                           # structuredContent AND the text copy of it
    assert ok["result"]["structuredContent"]["matches"][0]["name"] == "Kohl Mansion"
    assert env["auth"] == ["Bearer good"]                        # auth/me is read ONCE per request (gate -> tool)
    for name in ("get_my_wedding", "list_saved_vendors", "budget_status", "get_my_profile"):
        r = _rpc(client, "tools/call", {"name": name, "arguments": {}})
        assert not r["result"].get("isError"), r
        walk(r["result"]["structuredContent"])

    bad = _rpc(client, "tools/call", {"name": "find_vendor", "arguments": {"name": "a"}})     # a tool's own refusal
    assert bad["result"]["isError"] is True and "at least 4 letters" in json.dumps(bad["result"])
    gone = _rpc(client, "tools/call", {"name": "estimate_cost", "arguments": {}})
    assert gone.get("error") or gone["result"].get("isError")
    _drain(env)
    kinds = [(r["kind"], r["status"]) for r in env["logged"]]
    assert kinds[0] == ("mcp_find_vendor", "ok") and ("mcp_find_vendor", "error") in kinds


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
    assert r.status_code == 401 and "tools" not in r.text and "Ana" not in r.text
    assert 'resource_metadata="https://testserver/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]
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


@pytest.mark.parametrize("name,value,line", [("MCP_PUBLIC_URL", "", "REC_MCP_PUBLIC_URL is empty"),
                                             ("MCP_PUBLIC_URL", "http://mcp.tulle.test", "must be an https origin"),
                                             ("MCP_PUBLIC_URL", "https://mcp.tulle.test/mcp", "must be an https origin"),
                                             ("MCP_TOKEN_KEY", "", "REC_MCP_TOKEN_KEY is missing"),
                                             ("MCP_TOKEN_KEY", "short", "REC_MCP_TOKEN_KEY is missing"),
                                             ("MCP_TOKEN_KEY", "a" * 32, "not base64 of at least 32 bytes"),      # F2: was accepted
                                             ("MCP_TOKEN_KEY", "k" * 40, "not base64 of at least 32 bytes"),      # decodes to 30 bytes
                                             ("MCP_TOKEN_KEY", "not base64 at all !!" * 3, "not base64 of at least 32 bytes"),
                                             ("MCP_TOKEN_KEY_OLD", "oops", "REC_MCP_TOKEN_KEY_OLD is set but is not base64"),
                                             ("MCP_CONSENT_URL", "http://www.tulletogether.app/x", "REC_MCP_CONSENT_URL")])
def test_mount_adds_nothing_when_the_oauth_settings_are_incomplete(env, monkeypatch, capsys, name, value, line):
    monkeypatch.setattr(config, "MCP_PUBLIC_URL", "https://testserver")
    monkeypatch.setattr(config, "MCP_TOKEN_KEY", KEY)
    monkeypatch.setattr(config, "MCP_TOKEN_KEY_OLD", "")
    monkeypatch.setattr(config, name, value)
    host = FastAPI()
    before, routes = host.router.lifespan_context, len(host.routes)
    assert mcp_server.mount(host) is None
    assert len(host.routes) == routes and host.router.lifespan_context is before and not host.user_middleware
    out = capsys.readouterr().out
    assert line in out and out.count("/mcp is NOT mounted") == 1 and KEY not in out
    assert str(value) not in out or len(str(value)) < 8 or name != "MCP_TOKEN_KEY"      # a bad key is never echoed
    assert TestClient(host).post("/mcp", json=LIST).status_code == 404


@pytest.mark.parametrize("origin", ["https://editor.weweb.io", "https://tulletogether.app", "http://testserver", "null"])
def test_any_request_with_an_origin_header_is_refused(env, client, origin):
    """Browser pages (the app's CORS allows *.weweb.io and the live site) may not call /mcp, token or not."""
    r = client.post("/mcp", json=LIST, headers=dict(HEADERS, Authorization="Bearer good", Origin=origin))
    assert r.status_code == 403 and "web pages" in r.json()["error"] and "tools" not in r.text
    assert env["auth"] == []
