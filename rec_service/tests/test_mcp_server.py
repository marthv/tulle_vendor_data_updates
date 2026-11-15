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
        "Wedding_Budget": 60000, "favorited_vendors": ["V1", "P1", "F9"], "email": "ana@example.com"}
PACK = [{"id": 1, "user_id": 7, "vendor_name": "Example 1: Venue", "total_cost": 30000, "Selected": True, "Category": "Venue"},
        {"id": 2, "user_id": 7, "vendor_name": "Kohl Mansion", "total_cost": 12505, "Balance_Paid": 4000,
         "Selected": True, "Category": "Venue", "Notes": "tulle:V1", "Contact_Information": "ask for Dana",
         "Files": {"name": "contract.pdf", "url": "https://files.example/vault/SECRETFILE/contract.pdf"}},
        {"id": 3, "user_id": 7, "vendor_name": "Catering Co", "total_cost": 9000, "Selected": True,
         "Category": "Food and Beverage", "Notes": "deposit due in May"},
        {"id": 4, "user_id": 7, "vendor_name": "Old DJ", "total_cost": 900, "Selected": False, "Category": "Entertainment"}]


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


CARDS = {"V1": tulle_row("V1", "Kohl Mansion"),
         "P1": tulle_row("P1", "OliviaPhotos", category="Photography"),
         "F9": tulle_row("F9", "Bloomery", category="Beauty", city="Dallas")}
TWINS = [tulle_row("A%d" % i, "Ashton Gardens %s" % n, state=st, city=c)
         for i, (n, st, c) in enumerate([("North", "Georgia", "Atlanta"), ("West", "Ohio", "Columbus"),
                                         ("East", "Ohio", "Dayton"), ("South", "Utah", "Provo")], 1)]
FORBIDDEN_NUMBERS = ("12345", "12,345", "15678", "2222", "2,222", "4444", "31313", "5151", "5,151", "6161", "41414",
                     "41,414", "7373", "987651", "287", "233", "311", "4321", "78701", "555-0199", "30.26", "97.74")
CARD_KEYS = {"name", "city", "state", "category", "venue_type"}              # the public card: nothing else, ever
CARD_PLACES = {"matches": CARD_KEYS, "vendors": CARD_KEYS, "saved": CARD_KEYS, "saved_other": CARD_KEYS,
               "candidates": {"name", "city", "state"}, "also": {"name", "city", "state"}}
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
            for x in (v if isinstance(v, list) else [v]):
                assert isinstance(x, str) and "$" not in x and not re.search(r"\d", x), (where, k, x)

    def go(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in CARD_PLACES:
                    assert isinstance(v, list), (path, k)
                    for c in v:
                        card(c, CARD_PLACES[k], path + "." + k)
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
            return ([dict(CARDS["V1"])] if "kohl" in term else [dict(v) for v in TWINS] if "ashton" in term else [])
        return [dict(CARDS["P1"])] if cat == "Photography" and "olivia" in term else []

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
    return st


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


def test_the_instructions_tell_the_client_model_there_are_no_prices():
    for line in ("This connector never returns prices.", "For pricing, send the couple to tulletogether.app.",
                 "Do not guess or estimate prices from general knowledge when asked what a Tulle vendor costs."):
        assert line in mcp_server.INSTRUCTIONS


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
    assert out["find_vendor"]["matches"] == [{"name": "Kohl Mansion", "city": "Austin", "state": "Texas",
                                              "category": "Venue", "venue_type": ["Estate / Mansion"]}]
    assert out["list_saved_vendors"]["vendors"][1] == {"name": "OliviaPhotos", "city": "Austin", "state": "Texas",
                                                       "category": "Photography", "venue_type": ["Documentary"]}
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
    ok = {"matches": [{"name": "Kohl Mansion", "city": "Austin", "state": "Texas", "category": "Venue"}]}
    walk(ok)
    for bad in ({"matches": [dict(ok["matches"][0], max_capacity_seated=200)]},
                {"matches": [dict(ok["matches"][0], description="Lovely")]},
                {"vendors": [dict(ok["matches"][0], venue_type=["From $9 a head"])]},
                {"saved": [dict(ok["matches"][0], city="12 Main St")]},
                {"candidates": [dict(ok["matches"][0])]},                      # a candidate is name + place only
                {"x": {"y": [{"also": [{"name": "A", "vendor_id": "V1"}]}]}},
                {"note": "Rental starts at $12,345."}, {"anything": {"deep": ["LEAK"]}}):
        with pytest.raises(AssertionError):
            walk(bad)


def test_public_card_copies_nothing_but_its_five_fields():
    c = mcp_server.public_card(CARDS["V1"])
    assert c == {"name": "Kohl Mansion", "city": "Austin", "state": "Texas", "category": "Venue",
                 "venue_type": ["Estate / Mansion"]} and set(c) <= set(mcp_server.CARD_FIELDS)
    assert "tulle_url" not in c                                  # no page-link builder exists in the server
    odd = mcp_server.public_card({"name": "Barn 9", "state": ["Ohio"], "address": "77 Elm Rd",
                                  "venue_type": ["Barn / Ranch", "From $5,000", 7, "Seats 200"], "category": "Venue"})
    assert odd == {"name": "Barn 9", "state": "Ohio", "category": "Venue", "venue_type": ["Barn / Ranch"]}
    assert mcp_server.public_card({"name": "X", "address": "12 Main St, Suite 4"}) == {"name": "X", "category": "Venue"}


# ---------------------------------------------------------------- the couple's own numbers ARE returned

@pytest.mark.parametrize("tier", list(TIERS))
def test_get_my_wedding_returns_what_the_couple_entered(env, tier):
    TIERS[tier](env)
    w = walk(mcp_server.get_my_wedding("Bearer good"))
    assert (w["budget"], w["booked_total"], w["left"], w["paid_total"]) == (60000, 21505, 38495, 4000)
    cats = {c["key"]: c for c in w["categories"]}
    booked = cats["Venue"]["booked"]
    assert (booked["name"], booked["price"], booked["paid"], booked["contact"]) == ("Kohl Mansion", 12505, 4000, "ask for Dana")
    assert booked["file"] == {"name": "contract.pdf"} and "vendor_id" not in booked     # their file's name, not its link
    assert "SECRETFILE" not in json.dumps(w)
    assert [(e["name"], e["price"], e["notes"]) for e in w["extras"]] == [("Catering Co", 9000, "deposit due in May")]
    assert [(q["name"], q["price"]) for q in cats["Entertainment"]["quotes"]] == [("Old DJ", 900)]
    assert w["slices"] == [{"label": "Venue", "amount": 12505}, {"label": "Food and Beverage", "amount": 9000}]
    # Tulle's vendors in it: public cards only, no price hint (the app's screen shows "about $12,345" to plan holders)
    assert cats["Venue"]["saved"] == [mcp_server.public_card(CARDS["V1"])]
    assert cats["Photography"]["saved"][0]["name"] == "OliviaPhotos"
    assert [c["name"] for c in w["saved_other"]] == ["Bloomery"]
    assert "price" not in json.dumps(cats["Venue"]["saved"]) and "show_prices" not in w


@pytest.mark.parametrize("tier", list(TIERS))
def test_budget_status_is_the_couples_own_arithmetic(env, tier):
    TIERS[tier](env)
    b = walk(mcp_server.budget_status("Bearer good"))
    assert b["status"] == {"budget": 60000, "added": 21505, "paid": 4000, "owed": 17505, "remaining": 38495,
                           "pct_of_budget": 36, "item_count": 2,
                           "by_category": [{"category": "Venue", "added": 12505, "paid": 4000, "owed": 8505},
                                           {"category": "Food and Beverage", "added": 9000, "paid": 0, "owed": 9000}]}
    assert "$21,505" in b["text"]


def test_profile_is_the_couples_own_answers(env):
    p = mcp_server.get_my_profile("Bearer good")
    assert p == {"profile": xano.profile_of(env["user"]), "plan": "full"} and p["profile"]["Wedding_Budget"] == 60000


# ---------------------------------------------------------------- find_vendor: by name only, 3 candidates, daily cap

def test_find_vendor_one_match_a_non_venue_and_a_miss(env):
    one = mcp_server.find_vendor("Bearer good", "Kohl Mansion", "Austin")
    assert one["status"] == "found" and len(one["matches"]) == 1 and "never returns prices" in one["note"]
    ph = mcp_server.find_vendor("Bearer good", "OliviaPhotos")
    assert ph["status"] == "found" and ph["matches"][0]["category"] == "Photography"
    miss = mcp_server.find_vendor("Bearer good", "Zzyzx Ballroom")
    assert miss["status"] == "not_found" and miss["matches"] == [] and "candidates" not in miss
    with pytest.raises(mcp_server.MCPDenied, match="name"):
        mcp_server.find_vendor("Bearer good", " a ")
    assert env["searched"]


def test_several_matches_give_at_most_three_name_only_candidates(env):
    r = walk(mcp_server.find_vendor("Bearer good", "Ashton Gardens"))            # 4 venues share the name
    assert r["status"] == "choices" and r["matches"] == [] and "more specific name" in r["note"]
    assert len(r["candidates"]) == 3
    assert all(set(c) == {"name", "city", "state"} and c["name"].startswith("Ashton Gardens") for c in r["candidates"])
    one = mcp_server.find_vendor("Bearer good", "Ashton Gardens", "Utah")        # the place picks one
    assert one["status"] == "found" and [c["name"] for c in one["matches"]] == ["Ashton Gardens South"]
    two = mcp_server.find_vendor("Bearer good", "Ashton Gardens", "Ohio")
    assert len(two.get("matches") or []) + len(two.get("candidates") or []) + len(two.get("also") or []) <= 3


def _row(kind="mcp_find_vendor", day=T, status="ok", uid=7):
    return {"user_id": uid, "kind": kind, "usage_day": day, "status": status}


def test_daily_lookup_cap_under_at_and_over(env, monkeypatch):
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 3)
    # Not counted: other tools, other days, refused / failed calls.
    noise = [_row("mcp_get_my_wedding"), _row("mcp_list_saved_vendors"), _row(day="2026-11-14"), _row(status="error"),
             _row("refine"), _row("tool_estimate_cost")] * 5
    env["usage"] = noise + [_row()] * 2                                           # under: 2 of 3
    assert mcp_server.find_vendor("Bearer good", "Kohl Mansion")["status"] == "found"
    env["usage"] = noise + [_row()] * 3                                           # at the cap
    env["searched"].clear()
    for name in ("Kohl Mansion", "Zzyzx Ballroom"):
        with pytest.raises(mcp_server.MCPDenied, match="limit of 3 vendor lookups.*tulletogether.app"):
            mcp_server.find_vendor("Bearer good", name)
    env["usage"] = noise + [_row()] * 40                                          # over
    with pytest.raises(mcp_server.MCPDenied, match="limit of 3"):
        mcp_server.find_vendor("Bearer good", "Kohl Mansion")
    assert env["searched"] == []                                                  # refused before any search
    assert mcp_server.list_saved_vendors("Bearer good")["saved_count"] == 3       # the other tools still answer
    assert mcp_server.get_my_wedding("Bearer good")["budget"] == 60000


def test_not_found_lookups_count_toward_the_cap(env, monkeypatch):
    """End to end over the real logging: each call's own usage row is what the next call counts."""
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 2)
    monkeypatch.setattr(xano, "log_usage", lambda row: env["usage"].append(dict(row, usage_day=T)))
    assert mcp_server.find_vendor("Bearer good", "Zzyzx Ballroom")["status"] == "not_found"
    _drain(env)
    assert mcp_server.find_vendor("Bearer good", "Qwfp Manor")["status"] == "not_found"
    _drain(env)
    with pytest.raises(mcp_server.MCPDenied, match="limit of 2"):
        mcp_server.find_vendor("Bearer good", "Kohl Mansion")
    _drain(env)
    assert [(r["kind"], r["status"]) for r in env["usage"]] == [("mcp_find_vendor", "ok")] * 2 + [("mcp_find_vendor", "error")]


def test_cap_fails_closed_when_usage_cannot_be_read_and_zero_means_none(env, monkeypatch):
    monkeypatch.setattr(xano, "user_usage", lambda uid: (_ for _ in ()).throw(xano.XanoError("table 79 down")))
    with pytest.raises(mcp_server.MCPDenied, match="could not run find_vendor"):
        mcp_server.find_vendor("Bearer good", "Kohl Mansion")
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(config, "MCP_DAILY_LOOKUPS", 0)
    with pytest.raises(mcp_server.MCPDenied, match="limit of 0"):
        mcp_server.find_vendor("Bearer good", "Kohl Mansion")
    assert env["searched"] == []


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
    init = _rpc(client, "initialize", INIT["params"])["result"]
    assert init["serverInfo"]["name"] == "tulle" and "never returns prices" in init["instructions"]
    tools = _rpc(client, "tools/list")["result"]["tools"]
    assert sorted(t["name"] for t in tools) == EXACT_TOOLS       # what a client really sees: no search, no pricing
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
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
    assert bad["result"]["isError"] is True and "at least 3 letters" in json.dumps(bad["result"])
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
