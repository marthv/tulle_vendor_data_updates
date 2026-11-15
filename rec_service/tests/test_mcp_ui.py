"""Tulle panels for the MCP connector (mcp_ui.py + mcp_ui/*.html, 2026-11-15). Offline. What is checked here: the
resources and the tool links on the wire, the gate in front of them, and static rules on the HTML. What is NOT: how
the panels look or behave in a browser - the scripts are small, were run by hand against a stub DOM, and
rec_service/mcp_ui/preview.html is there to look at them (no Node tooling in this repo, so no JS test here)."""
import json
import re
import subprocess
import sys

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app as appmod
import config
import mcp_oauth
import mcp_server
import mcp_ui
import xano

pytest.importorskip("mcp")
pytest.importorskip("cryptography")

BASE = "https://mcp.tulle.test"
MIME = "text/html;profile=mcp-app"
H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
PANEL_FILES = ["my-wedding.html", "vendors.html"]
EXPECTED_LINKS = {"get_my_wedding": "ui://tulle/my-wedding", "budget_status": "ui://tulle/my-wedding",
                  "list_saved_vendors": "ui://tulle/vendors", "find_vendor": "ui://tulle/vendors"}
CARD = {"vendor_id": "V101", "name": "Kohl Mansion", "state": "Texas", "category": "Venue",
        "venue_type": ["Estate / Mansion"], "venue_fee_range": [12345, 15678], "description": "LEAK $12,345"}


@pytest.fixture
def c(monkeypatch):
    for name, value in (("MCP_PUBLIC_URL", BASE), ("MCP_TOKEN_KEY", "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s="), ("MCP_USER_IDS", frozenset({7})),
                        ("MCP_TOKEN_KEY_OLD", ""), ("MCP_ALL_USERS", False), ("MCP_ALLOWED_HOSTS", []), ("MCP_ALLOWED_ORIGINS", []),
                        ("MCP_DEV_RAW_TOKEN", False), ("KILL_SWITCH", False)):
        monkeypatch.setattr(config, name, value)

    def auth(authorization):
        if authorization != "Bearer xano-good":
            raise HTTPException(401, "sign in required")
        return "xano-good", {"id": 7, "first_name": "Ana", "favorited_vendors": ["V101"], "Wedding_Budget": 60000}
    monkeypatch.setattr(appmod, "_auth", auth)
    monkeypatch.setattr(appmod, "_submit_usage", lambda row: None)
    monkeypatch.setattr(xano, "vendor_card", lambda vid: dict(CARD))
    monkeypatch.setattr(xano, "cached_vendor_card", lambda vid, stats=None: dict(CARD))
    monkeypatch.setattr(xano, "user_packages", lambda tok, uid: [])
    host = FastAPI()
    assert mcp_server.mount(host) is not None
    with TestClient(host, base_url=BASE) as client:
        client.token = mcp_oauth.seal("access", {"uid": 7, "xt": "xano-good", "cid": "c"}, 600)
        yield client


def rpc(c, method, params=None, token="", raw=False):
    h = dict(H)
    token = c.token if token == "" else token
    if token:
        h["Authorization"] = "Bearer " + token
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers=h)
    return r if raw else r.json()


# ---------------------------------------------------------------- on the wire

def test_each_tool_advertises_its_panel_in_the_documented_shape(c):
    """quickstart.md: `_meta.ui.resourceUri` on the tool in tools/list (+ the legacy flat key the helper also writes)."""
    tools = {t["name"]: t for t in rpc(c, "tools/list")["result"]["tools"]}
    assert sorted(tools) == sorted(mcp_server.TOOLS)
    for name, uri in EXPECTED_LINKS.items():
        assert tools[name]["_meta"] == {"ui": {"resourceUri": uri}, "ui/resourceUri": uri}, name
    assert "_meta" not in tools["get_my_profile"] or not tools["get_my_profile"]["_meta"]      # no panel for it
    assert mcp_ui.TOOL_UI == EXPECTED_LINKS and set(mcp_ui.TOOL_UI) <= set(mcp_server.TOOLS)
    assert mcp_ui.tool_meta("get_my_profile") == {} and mcp_ui.tool_meta("nope") == {}


def test_panels_are_listed_and_read_as_mcp_app_resources(c):
    listed = {r["uri"]: r for r in rpc(c, "resources/list")["result"]["resources"]}
    assert sorted(listed) == ["ui://tulle/my-wedding", "ui://tulle/vendors"] == sorted(set(EXPECTED_LINKS.values()))
    assert rpc(c, "resources/templates/list")["result"]["resourceTemplates"] == []
    for uri, r in listed.items():
        assert r["mimeType"] == MIME and r["title"] and r["description"]
        got = rpc(c, "resources/read", {"uri": uri})["result"]["contents"]
        assert len(got) == 1
        item = got[0]
        assert item["uri"] == uri and item["mimeType"] == MIME and item["text"] == mcp_ui.html(uri)
        # resource _meta.ui: a bordered card, and NO csp / permissions / domain (no external origin is ever needed)
        assert item["_meta"] == {"ui": {"prefersBorder": True}} == r["_meta"]
    missing = rpc(c, "resources/read", {"uri": "ui://tulle/preview"})
    assert "error" in missing and "result" not in missing                     # preview.html is not a resource


@pytest.mark.parametrize("method,params", [("resources/list", {}), ("resources/read", {"uri": "ui://tulle/vendors"}),
                                           ("resources/read", {"uri": "ui://tulle/my-wedding"}), ("tools/list", {})])
@pytest.mark.parametrize("token", [None, "xano-good", "garbage"])
def test_resources_sit_behind_the_same_gate_as_the_tools(c, method, params, token):
    """resources/list and resources/read are POSTs to /mcp like any tool call: no valid access token, no HTML."""
    r = rpc(c, method, params, token=token, raw=True)
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]
    assert "<html" not in r.text and "ui://" not in r.text


def test_gate_rules_apply_to_resource_reads_too(c, monkeypatch):
    read = {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "ui://tulle/vendors"}}
    ok = dict(H, Authorization="Bearer " + c.token)
    assert c.get("/mcp", headers=ok).status_code == 405
    assert c.post("/mcp", json=read, headers=dict(ok, Origin="https://evil.example")).status_code == 403
    assert c.post("/mcp", json=read, headers=dict(ok, Host="evil.example")).status_code == 421
    monkeypatch.setattr(config, "MCP_USER_IDS", frozenset({99}))             # no longer on the allowlist
    assert c.post("/mcp", json=read, headers=ok).status_code == 403
    monkeypatch.setattr(config, "MCP_USER_IDS", frozenset({7}))
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    assert c.post("/mcp", json=read, headers=ok).status_code == 503
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    assert c.post("/mcp", json=read, headers=ok).status_code == 200


def test_tool_results_carry_nothing_extra_for_the_panels(c):
    """Data rule: a panel draws what the tool already returns. The result over MCP is the plain function's result."""
    for name, args, direct in (("list_saved_vendors", {}, mcp_server.list_saved_vendors("Bearer xano-good")),
                               ("get_my_wedding", {}, mcp_server.get_my_wedding("Bearer xano-good")),
                               ("budget_status", {}, mcp_server.budget_status("Bearer xano-good"))):
        res = rpc(c, "tools/call", {"name": name, "arguments": args})["result"]
        assert not res.get("isError") and res["structuredContent"] == direct, name
        assert not res.get("_meta")
        assert "LEAK" not in json.dumps(res) and "12345" not in json.dumps(res)
    card = rpc(c, "tools/call", {"name": "list_saved_vendors", "arguments": {}})["result"]["structuredContent"]["vendors"][0]
    assert card == {"name": "Kohl Mansion", "state": "Texas", "category": "Venue", "venue_type": ["Estate / Mansion"],
                    "tulle_url": "https://www.tulletogether.app/vendors/V101"}


def test_flag_off_nothing_about_panels_is_loaded():
    code = ("import sys, types\n"
            "sys.modules.setdefault('anthropic', types.ModuleType('anthropic')).beta_tool = lambda f: f\n"
            "import app\n"
            "assert not {'mcp', 'mcp_ui', 'mcp_server', 'mcp_oauth'} & set(sys.modules)\n"
            "assert not [r for r in app.app.routes if 'mcp' in getattr(r, 'path', '')]\n"
            "print('OK')")
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("REC_MCP")}
    r = subprocess.run([sys.executable, "-c", code], cwd=str(mcp_ui.DIR.parent), env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-1500:]


# ---------------------------------------------------------------- static rules on the HTML

def _panel(name):
    return (mcp_ui.DIR / name).read_text(encoding="utf-8")


def test_the_panel_folder_holds_exactly_these_files_and_is_not_a_package():
    assert sorted(p.name for p in mcp_ui.DIR.iterdir()) == ["my-wedding.html", "preview.html", "vendors.html"]
    assert sorted(v[0] for v in mcp_ui.PANELS.values()) == PANEL_FILES        # preview.html is never served


@pytest.mark.parametrize("name", PANEL_FILES)
def test_panel_makes_no_network_request_of_any_kind(name):
    src = _panel(name)
    urls = re.findall(r"https?://[^\s\"'<>)]*", src)
    assert all(u == "https://www.tulletogether.app/" for u in urls), urls       # only the origin used to VALIDATE links
    assert not re.search(r"""["'(]//[a-z0-9.-]+\.[a-z]{2,}""", src)               # no protocol-relative URLs either
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "importScripts", "import(",
                   "<link", "<img", "<iframe", "<object", "<embed", "<video", "<audio", "<form", "@import", "url(",
                   "@font-face", " src=", "srcset", "navigator.", "localStorage", "sessionStorage", "document.cookie",
                   "window.open", "location.href", "location.assign", "location.replace", "BroadcastChannel"):
        assert banned not in src, (name, banned)
    assert re.search(r'<script(?![^>]*\bsrc=)[^>]*>', src) and src.count("<script") == 1 and "type=\"module\"" not in src
    assert '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; ' \
           'style-src \'unsafe-inline\'; img-src data:; base-uri \'none\'; form-action \'none\'">' in src


@pytest.mark.parametrize("name", PANEL_FILES)
def test_panel_never_builds_markup_from_data(name):
    src = _panel(name)
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\"",
                   "DOMParser", "createContextualFragment", "srcdoc", ".href =", "javascript:", "on" + "click=", "onerror"):
        assert banned not in src, (name, banned)
    assert "textContent" in src and "ev.source !== window.parent" in src       # text only; messages from the host only


def test_vendor_links_open_only_tulle_pages_through_the_host():
    src = _panel("vendors.html")
    assert 'var TULLE = "https://www.tulletogether.app/";' in src and "u.indexOf(TULLE) !== 0" in src
    assert 'p.hostname === "www.tulletogether.app"' in src and 'p.protocol === "https:"' in src
    assert 'p.pathname.indexOf("/vendors/") === 0' in src and "!p.port" in src      # a vendor page, nothing else on the host
    assert 'request("ui/open-link", { url: safe })' in src and "<a " not in src and "target=" not in src
    assert "ui/open-link" not in _panel("my-wedding.html")                      # the plan panel opens nothing


def test_vendors_panel_has_no_price_anywhere():
    src = _panel("vendors.html")
    script = src[src.index("<script>"):]
    for word in ("price", "amount", "cost", "fee", "budget", "money", "paid"):
        assert word not in script.lower(), word
    assert not re.search(r"\$\s?\d", src)


@pytest.mark.parametrize("name", PANEL_FILES)
def test_panel_speaks_the_mcp_apps_protocol_by_hand(name):
    src = _panel(name)
    for needed in ('"ui/initialize"', 'protocolVersion: "2026-01-26"', "appInfo:", "appCapabilities:",
                   'availableDisplayModes: ["inline", "fullscreen"]', '"ui/notifications/initialized"',
                   '"ui/notifications/tool-result"', '"ui/notifications/host-context-changed"',
                   '"ui/notifications/tool-cancelled"', '"ui/notifications/size-changed"', "structuredContent",
                   'window.parent.postMessage(msg, "*")', 'jsonrpc: "2.0"'):
        assert needed in src, (name, needed)
    assert "clientInfo" not in src and "unpkg" not in src and "ext-apps" not in src


@pytest.mark.parametrize("name", PANEL_FILES)
def test_panel_follows_the_tulle_design_tokens_and_the_host_theme(name):
    src = _panel(name)
    for token in ("--g900:#0A3D24", "--g800:#0D5C3A", "--g700:#0F7348", "--g500:#00A368", "--g400:#1DBD7E", "--g300:#52D19A",
                  "--g200:#A8EAC8", "--g100:#D4F5E4", "--g50:#EDF9F3", "--ink:#18191A", "--ink7:#E8EAEE", "--cream:#FAF8F4",
                  "--amber:#D97706", "--r8:8px", "--r12:12px", "--r16:16px", '--font-d:"Lora", Georgia', '--font-u:"Sora", system-ui'):
        assert token in src, (name, token)
    assert '<meta name="color-scheme" content="light dark">' in src
    assert "html, body { margin: 0; background: transparent; }" in src         # transparent-theming.md
    assert ':root[data-theme="dark"]' in src and "prefers-color-scheme: dark" in src and "var(--color-text-primary" in src
    assert "prefers-reduced-motion" in src and ":focus-visible" in src and 'name="viewport"' in src and 'lang="en"' in src
    colours = set(re.findall(r"#[0-9A-Fa-f]{6}\b", src))
    for c in colours:                                                            # no purple, no blue
        r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        assert not (b > g + 25 and b > 110), (name, c)


def test_my_wedding_panel_shows_hidden_amounts_in_words():
    src = _panel("my-wedding.html")
    assert 'item.amount_hidden === true) return el("span", "hid", "Amount in Tulle")' in src
    assert "v.hidden > 0" in src and "only shown in the Tulle app" in src and "Your plan is empty" in src
    assert "namespaceURI" in src and "createElementNS(SVG_NS" in src and "left to spend" in src and "total budget" in src
    for i in range(6):
        assert ".s%d " % i in src                                                # the fixed ordered slice palette
    assert "i + 1));   // numbered: not colour alone" in src


def test_a_hidden_row_renders_no_number_at_all():
    """G3: the panel printed `paid` on a row whose amount is hidden, had the server ever sent one."""
    src = _panel("my-wedding.html")
    assert "var paid = c.booked.amount_hidden === true ? null : num(c.booked.paid);" in src
    assert src.count("num(c.booked.paid)") == 1 and "item.paid" not in src and "x.paid" not in src   # the only per-row paid
    row = src[src.index("function itemRow(item)"):src.index("function chips(")]
    assert "amountCell(item)" in row and "paid" not in row and "price" not in row
    pre = _panel("preview.html")
    assert "amount_hidden: true, price: 888888, paid: 999999" in pre             # the fixture that shows it


def test_a_hidden_row_never_carries_paid_or_price_from_the_server(c, monkeypatch):
    rows = [{"id": 5, "user_id": 7, "vendor_name": "Kohl Mansion", "total_cost": 12345, "Balance_Paid": 500, "Balance_Left": 11845,
             "Selected": True, "Category": "Venue", "vendor_id": "V101"},
            {"id": 6, "user_id": 7, "vendor_name": "Kohl Mansion", "total_cost": 12345, "Balance_Paid": 700, "Selected": False,
             "Category": "Venue", "Notes": "tulle:V101"}]
    monkeypatch.setattr(xano, "user_packages", lambda tok, uid: [dict(r) for r in rows])
    w = mcp_server.get_my_wedding("Bearer xano-good")
    venue = next(x for x in w["categories"] if x["key"] == "Venue")
    assert venue["booked"] == {"name": "Kohl Mansion", "category": "Venue", "amount_hidden": True}
    assert venue["quotes"] == [{"name": "Kohl Mansion", "category": "Venue", "amount_hidden": True}]
    assert (w["booked_total"], w["paid_total"], w["amounts_hidden"]) == (0, 0, 2)
    dump = json.dumps(w)
    assert not any(n in dump for n in ("12345", "500", "700", "11845"))
    over_mcp = rpc(c, "tools/call", {"name": "get_my_wedding", "arguments": {}})["result"]
    assert over_mcp["structuredContent"] == w and not any(n in json.dumps(over_mcp) for n in ("12345", "500", "700", "11845"))


def test_preview_is_a_stub_host_with_every_fixture_in_both_themes():
    src = _panel("preview.html")
    for needed in ("my-wedding.html", "vendors.html", '"ui/initialize"', "ui/notifications/tool-result", "ui/notifications/size-changed",
                   "ui/notifications/host-context-changed", "ui/open-link", 'sandbox", "allow-scripts"', '["light", "dark"]',
                   "full plan", "hidden amounts", "empty plan", "saved list", "single match", "candidates", "not found"):
        assert needed in src, needed
    assert all(u.startswith(("https://www.tulletogether.app/vendors/", "https://www.tulletogether.app.evil.example/",
                             "https://www.tulletogether.app/pricing", "https://www.tulletogether.app:8443/vendors/"))
               for u in re.findall(r"https?://[^\s\"'<>)]*", src))               # the preview loads nothing remote either
