"""'Sign in with Tulle' (mcp_oauth.py, 2026-11-15): the OAuth flow Claude runs to connect to /mcp. Offline: real
AES-GCM, Xano mocked, the client metadata documents served from fixtures (never fetched)."""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app as appmod
import config
import mcp_oauth
import mcp_server
import xano

pytest.importorskip("mcp")
pytest.importorskip("cryptography")

BASE = "https://mcp.tulle.test"
MCP_URL = BASE + "/mcp"
PRM_URL = BASE + "/.well-known/oauth-protected-resource/mcp"
CONSENT = "https://www.tulletogether.app/connect-claude"
ORIGIN = "https://www.tulletogether.app"
KEY = "test-key-" + "x" * 40
# Claude's two client metadata documents, as published 2026-11-15 (copied here; tests never fetch them).
HOSTED = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CODE_CLI = "https://claude.ai/oauth/claude-code-client-metadata"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
DOCS = {
    HOSTED: {"client_id": HOSTED, "client_name": "Claude", "client_uri": "https://claude.ai",
             "redirect_uris": [CALLBACK],
             "grant_types": ["authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:jwt-bearer"],
             "response_types": ["code"], "token_endpoint_auth_method": "none"},
    CODE_CLI: {"client_id": CODE_CLI, "client_name": "Claude Code", "client_uri": "https://claude.ai",
               "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
               "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
               "token_endpoint_auth_method": "none"},
}
REAL_FETCH = mcp_oauth.fetch_cimd           # the real one; the env fixture swaps in the fixtures above
VERIFIER = "v" * 43 + "-._~" + "A1" * 10
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
USERS = {"xano-good": {"id": 7, "first_name": "Ana", "Wedding_Guest_Count": 120},
         "xano-other": {"id": 8, "first_name": "Bo"}}
H_MCP = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
CALL = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "get_my_profile", "arguments": {}}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


@pytest.fixture
def env(monkeypatch):
    st = {"valid": dict(USERS), "auth": [], "fetched": [], "docs": {k: dict(v) for k, v in DOCS.items()}, "down": False}

    def auth(authorization):
        st["auth"].append(authorization)
        if st["down"]:
            raise OSError("https://xqtb.xano.io/api:v1/auth/me timed out")
        token = (authorization or "").removeprefix("Bearer ").strip()
        if token not in st["valid"]:
            raise HTTPException(401, "sign in required")
        return token, dict(st["valid"][token])

    def fetch(url):
        st["fetched"].append(url)
        if url not in st["docs"]:
            raise mcp_oauth.OAuthError("invalid_client", "the client metadata document could not be fetched")
        return st["docs"][url]
    for name, value in (("MCP_PUBLIC_URL", BASE), ("MCP_TOKEN_KEY", KEY), ("MCP_CONSENT_URL", CONSENT),
                        ("MCP_CLIENT_HOSTS", ["claude.ai"]), ("MCP_USER_IDS", frozenset({7})), ("MCP_ALL_USERS", False),
                        ("MCP_ALLOWED_HOSTS", []), ("MCP_ALLOWED_ORIGINS", []), ("MCP_DEV_RAW_TOKEN", False),
                        ("KILL_SWITCH", False)):
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(appmod, "_auth", auth)
    monkeypatch.setattr(appmod, "_submit_usage", lambda row: None)
    monkeypatch.setattr(mcp_oauth, "fetch_cimd", fetch)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    for store in (mcp_oauth.USED, mcp_oauth.BUCKETS):
        store.clear()
    mcp_oauth._CIMD.clear()
    yield st
    mcp_oauth._CIMD.clear()
    mcp_oauth.USED.clear()
    mcp_oauth.BUCKETS.clear()


def _host():
    host = FastAPI()

    @host.get("/health")
    def health():
        return {"ok": True}
    assert mcp_server.mount(host) is not None
    return host


@pytest.fixture
def c(env):
    with TestClient(_host(), base_url=BASE, follow_redirects=False) as client:
        yield client


def q(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def authz(c, **over):
    p = {"response_type": "code", "client_id": HOSTED, "redirect_uri": CALLBACK, "code_challenge": CHALLENGE,
         "code_challenge_method": "S256", "state": "st-123", "resource": MCP_URL}
    p.update(over)
    return c.get("/authorize", params={k: v for k, v in p.items() if v is not None})


def blob_of(c, **over):
    r = authz(c, **over)
    assert r.status_code == 302 and r.headers["location"].startswith(CONSENT + "?req="), (r.status_code, r.text)
    return q(r.headers["location"])["req"]


def approve(c, blob, token="xano-good", origin=ORIGIN):
    h = {}
    if token:
        h["Authorization"] = "Bearer " + token
    if origin:
        h["Origin"] = origin
    return c.post("/oauth/approve", json={"req": blob}, headers=h)


def code_of(c, **over):
    r = approve(c, blob_of(c, **over))
    assert r.status_code == 200, r.text
    return q(r.json()["redirect"])["code"]


def token(c, **form):
    return c.post("/token", data=form)       # form-encoded, as Claude sends it


def exchange(c, the_code, **over):
    f = {"grant_type": "authorization_code", "code": the_code, "code_verifier": VERIFIER, "client_id": HOSTED,
         "redirect_uri": CALLBACK, "resource": MCP_URL}
    f.update(over)
    return token(c, **{k: v for k, v in f.items() if v is not None})


def signed_in(c):
    r = exchange(c, code_of(c))
    assert r.status_code == 200, r.text
    return r.json()


def mcp(c, bearer=None, body=CALL, **headers):
    h = dict(H_MCP, **headers)
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    return c.post("/mcp", json=body, headers=h)


def assert_challenge(r, error=None):
    """The one response that makes Claude start (or re-run) sign-in: 401 + this header, before the MCP SDK runs."""
    assert r.status_code == 401, (r.status_code, r.text)
    w = r.headers["www-authenticate"]
    assert w.startswith("Bearer ") and 'resource_metadata="%s"' % PRM_URL in w
    assert ('error="%s"' % error in w) if error else ("error=" not in w)
    assert "result" not in r.text and "Ana" not in r.text


# ---------------------------------------------------------------- the whole flow, as Claude runs it

def test_happy_path_end_to_end(env, c, capsys):
    # 1. no token -> 401 with the pointer, for initialize, tools/list and a tool call alike
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-ai", "version": "1"}}}
    for body in (init, LIST, CALL):
        assert_challenge(mcp(c, body=body))
    assert mcp(c).headers["www-authenticate"] == 'Bearer resource_metadata="%s"' % PRM_URL

    # 2. discovery: exactly the documents the connector docs describe
    prm = c.get("/.well-known/oauth-protected-resource/mcp")
    assert prm.status_code == 200 and prm.json() == c.get("/.well-known/oauth-protected-resource").json() == {
        "resource": MCP_URL, "authorization_servers": [BASE], "bearer_methods_supported": ["header"]}
    asm = c.get("/.well-known/oauth-authorization-server").json()
    assert asm == {"issuer": BASE, "authorization_endpoint": BASE + "/authorize", "token_endpoint": BASE + "/token",
                   "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                   "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"],
                   "client_id_metadata_document_supported": True}
    assert "registration_endpoint" not in asm                     # CIMD only: no DCR, no client database

    # 3. /authorize -> the consent page on the Tulle web app, carrying a sealed request
    r = authz(c)
    assert r.status_code == 302 and r.headers["cache-control"] == "no-store"
    loc = urlsplit(r.headers["location"])
    assert "%s://%s%s" % (loc.scheme, loc.netloc, loc.path) == CONSENT and list(q(r.headers["location"])) == ["req"]
    blob = q(r.headers["location"])["req"]
    assert env["fetched"] == [HOSTED] and CHALLENGE not in blob and "st-123" not in blob     # opaque

    # 4. the consent page reads what to show ...
    info = c.get("/oauth/request", params={"req": blob}, headers={"Origin": ORIGIN})
    assert info.status_code == 200 and info.headers["access-control-allow-origin"] == ORIGIN
    d = info.json()
    assert (d["client_host"], d["redirect_host"], d["redirect_is_loopback"]) == ("claude.ai", "claude.ai", False)
    assert d["client_id"] == HOSTED and d["shares"] == mcp_oauth.SHARES and "never shared" in d["not_shared"]
    assert 0 < d["expires_in"] <= 600 and "client_name" not in d          # the self-asserted name is never shown
    assert q(d["deny_redirect"]) == {"error": "access_denied", "state": "st-123",
                                     "error_description": "The couple did not approve the connection"}
    assert d["deny_redirect"].startswith(CALLBACK + "?")

    # ... and on Approve sends the couple's own Tulle token
    ok = approve(c, blob)
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == ORIGIN
    assert list(ok.json()) == ["redirect"] and ok.json()["redirect"].startswith(CALLBACK + "?code=")
    back = q(ok.json()["redirect"])
    assert set(back) == {"code", "state"} and back["state"] == "st-123"

    # 5. Claude exchanges the code (form-encoded, public client, PKCE)
    t = exchange(c, back["code"])
    assert t.status_code == 200 and t.headers["cache-control"] == "no-store"
    tok = t.json()
    assert set(tok) == {"access_token", "token_type", "expires_in", "refresh_token"}
    assert (tok["token_type"], tok["expires_in"]) == ("Bearer", 3600)
    for secret in ("xano-good", KEY):
        assert secret not in json.dumps(tok) and secret not in back["code"] and secret not in blob

    # 6. the MCP call with the access token; the couple's Xano token goes through the same caller() as ever
    env["auth"].clear()
    r = mcp(c, tok["access_token"])
    assert r.status_code == 200 and r.json()["result"]["structuredContent"]["profile"]["first_name"] == "Ana"
    assert env["auth"] == ["Bearer xano-good"]                    # auth/me once per request, re-checked every call
    names = [x["name"] for x in mcp(c, tok["access_token"], body=LIST).json()["result"]["tools"]]
    assert sorted(names) == sorted(mcp_server.TOOLS)

    # 7. refresh rotates: a new pair, and the old refresh token is refused with invalid_grant
    r2 = token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=HOSTED, resource=MCP_URL)
    assert r2.status_code == 200
    new = r2.json()
    assert new["refresh_token"] != tok["refresh_token"] and new["access_token"] != tok["access_token"]
    old = token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=HOSTED)
    assert old.status_code == 400 and old.json()["error"] == "invalid_grant"
    assert mcp(c, new["access_token"]).status_code == 200
    assert token(c, grant_type="refresh_token", refresh_token=new["refresh_token"]).status_code == 200   # and again

    # nothing secret was printed along the way
    out = capsys.readouterr().out + capsys.readouterr().err
    for secret in (tok["access_token"], tok["refresh_token"], back["code"], blob, "xano-good", KEY):
        assert secret not in out


def test_claude_code_flow_with_a_loopback_redirect_on_any_port(env, c):
    redirect = "http://localhost:3118/callback"
    blob = blob_of(c, client_id=CODE_CLI, redirect_uri=redirect)
    d = c.get("/oauth/request", params={"req": blob}, headers={"Origin": ORIGIN}).json()
    assert (d["client_host"], d["redirect_host"], d["redirect_is_loopback"]) == ("claude.ai", "localhost", True)
    back = approve(c, blob).json()["redirect"]
    assert back.startswith(redirect + "?code=")
    t = exchange(c, q(back)["code"], client_id=CODE_CLI, redirect_uri=redirect)
    assert t.status_code == 200 and mcp(c, t.json()["access_token"]).status_code == 200


def test_the_client_document_is_cached_briefly(env, c):
    for _ in range(3):
        blob_of(c)
    assert env["fetched"] == [HOSTED]


# ---------------------------------------------------------------- /authorize: the client and its redirect URI

@pytest.mark.parametrize("client,redirect", [
    (CODE_CLI, "http://localhost/callback"), (CODE_CLI, "http://localhost:1/callback"),
    (CODE_CLI, "http://localhost:65535/callback"), (CODE_CLI, "http://127.0.0.1:54321/callback"),
    (CODE_CLI, "http://127.0.0.1/callback"), (HOSTED, CALLBACK)])
def test_redirect_uris_that_are_accepted(env, c, client, redirect):
    assert authz(c, client_id=client, redirect_uri=redirect).status_code == 302


@pytest.mark.parametrize("client,redirect", [
    (HOSTED, None), (HOSTED, ""), (HOSTED, CALLBACK + "/"), (HOSTED, CALLBACK + "?x=1"), (HOSTED, CALLBACK + "#f"),
    (HOSTED, "https://claude.ai.evil.example/api/mcp/auth_callback"), (HOSTED, "https://evil.example/api/mcp/auth_callback"),
    (HOSTED, "http://claude.ai/api/mcp/auth_callback"), (HOSTED, "https://claude.ai:8443/api/mcp/auth_callback"),
    (HOSTED, "https://CLAUDE.AI/api/mcp/auth_callback"), (HOSTED, "http://localhost:3118/callback"),    # not in ITS document
    (CODE_CLI, CALLBACK),
    (CODE_CLI, "http://localhost:3118/other"), (CODE_CLI, "http://localhost:3118/callback/"),
    (CODE_CLI, "http://localhost:3118/callback?x=1"), (CODE_CLI, "http://localhost:3118/callback#f"),
    (CODE_CLI, "https://localhost:3118/callback"), (CODE_CLI, "http://localhost.evil.example/callback"),
    (CODE_CLI, "http://localhost.evil.example:3118/callback"), (CODE_CLI, "http://127.0.0.2:3118/callback"),
    (CODE_CLI, "http://[::1]:3118/callback"), (CODE_CLI, "http://user@localhost:3118/callback"),
    (CODE_CLI, "http://localhost:99999/callback"), (CODE_CLI, "http://localhost:abc/callback"),
    (CODE_CLI, "http://0.0.0.0:3118/callback"), (CODE_CLI, "http://evil.example/callback"),
    (CODE_CLI, "javascript:alert(1)"), (CODE_CLI, "x" * 3000)])
def test_bad_redirect_uri_is_an_error_page_never_a_redirect(env, c, client, redirect):
    r = authz(c, client_id=client, redirect_uri=redirect)
    assert r.status_code == 400 and "location" not in r.headers
    assert r.json()["error"] == "invalid_request" and "redirect_uri" in r.json()["error_description"]


@pytest.mark.parametrize("client_id", [
    None, "", "claude", "http://claude.ai/oauth/mcp-oauth-client-metadata", "https://claude.ai", "https://claude.ai/",
    "https://user:pw@claude.ai/oauth/x", "https://claude.ai/oauth/x#frag", "https://claude.ai:8443/oauth/x",
    "https://claude.ai/oauth/../admin", "https://evil.example/client.json", "https://claude.ai.evil.example/client.json",
    "https://sub.claude.ai/client.json", "https://169.254.169.254/latest/meta-data", "https://localhost/client.json",
    "https://10.0.0.5/client.json", "https://claude.ai/" + "x" * 3000])
def test_client_ids_refused_before_anything_is_fetched(env, c, client_id):
    r = authz(c, client_id=client_id)
    assert r.status_code == 400 and r.json()["error"] == "invalid_client" and "location" not in r.headers
    assert env["fetched"] == []


def test_client_host_allowlist_is_configurable(env, c, monkeypatch):
    other = "https://clients.example.org/app.json"
    env["docs"][other] = {"client_id": other, "redirect_uris": ["https://clients.example.org/cb"]}
    assert authz(c, client_id=other, redirect_uri="https://clients.example.org/cb").status_code == 400
    monkeypatch.setattr(config, "MCP_CLIENT_HOSTS", ["claude.ai", "clients.example.org"])
    assert authz(c, client_id=other, redirect_uri="https://clients.example.org/cb").status_code == 302


@pytest.mark.parametrize("doc", [
    {"client_id": CODE_CLI, "redirect_uris": [CALLBACK]},                     # names a different client
    {"client_id": HOSTED + "/", "redirect_uris": [CALLBACK]}, {"redirect_uris": [CALLBACK]},
    {"client_id": HOSTED}, {"client_id": HOSTED, "redirect_uris": []}, {"client_id": HOSTED, "redirect_uris": CALLBACK},
    {"client_id": HOSTED, "redirect_uris": [CALLBACK, 7]}, {"client_id": 7, "redirect_uris": [CALLBACK]},
    ["not", "an", "object"], "text", None])
def test_client_document_that_does_not_match_is_refused(env, c, doc):
    env["docs"][HOSTED] = doc
    r = authz(c)
    assert r.status_code == 400 and r.json()["error"] == "invalid_client" and "location" not in r.headers


def test_unreachable_client_document_is_refused(env, c):
    del env["docs"][HOSTED]
    r = authz(c)
    assert r.status_code == 400 and r.json()["error"] == "invalid_client"


@pytest.mark.parametrize("over,error", [
    ({"code_challenge_method": "plain"}, "invalid_request"), ({"code_challenge_method": None}, "invalid_request"),
    ({"code_challenge": None}, "invalid_request"), ({"code_challenge": "short"}, "invalid_request"),
    ({"code_challenge": CHALLENGE + "="}, "invalid_request"),
    ({"response_type": "token"}, "unsupported_response_type"), ({"response_type": None}, "unsupported_response_type"),
    ({"resource": "https://other.example/mcp"}, "invalid_target"), ({"resource": BASE}, "invalid_target"),
    ({"resource": BASE + "/mcp/extra"}, "invalid_target")])
def test_request_errors_go_back_to_the_trusted_redirect_uri_with_state(env, c, over, error):
    r = authz(c, **over)
    assert r.status_code == 302 and r.headers["location"].startswith(CALLBACK + "?")
    back = q(r.headers["location"])
    assert back["error"] == error and back["state"] == "st-123" and "code" not in back and "req" not in back


@pytest.mark.parametrize("resource", [None, MCP_URL, MCP_URL + "/", "HTTPS://MCP.TULLE.TEST/mcp"])
def test_resource_is_compared_in_canonical_form(env, c, resource):
    assert authz(c, resource=resource).headers["location"].startswith(CONSENT)


def test_state_is_optional_and_round_trips(env, c):
    back = approve(c, blob_of(c, state=None)).json()["redirect"]
    assert set(q(back)) == {"code"}
    odd = "a b&c=d/é?#"
    assert q(approve(c, blob_of(c, state=odd)).json()["redirect"])["state"] == odd


# ---------------------------------------------------------------- fetching the document: SSRF and size guards

class FakeResponse:
    def __init__(self, status=200, body=b"{}"):
        self.status_code, self._body = status, body
        self.raw = self

    def read(self, n, decode_content=True):
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def real_fetch(monkeypatch):
    """The real mcp_oauth.fetch_cimd with DNS and HTTP faked."""
    import requests
    st = {"addrs": ["160.79.104.10"], "response": FakeResponse(200, json.dumps(DOCS[HOSTED]).encode()), "calls": []}
    real = REAL_FETCH
    monkeypatch.setattr(mcp_oauth, "resolve", lambda host: st["addrs"])

    def get(url, **kw):
        st["calls"].append((url, kw))
        if isinstance(st["response"], Exception):
            raise st["response"]
        return st["response"]
    monkeypatch.setattr(requests, "get", get)
    st["fetch"] = real
    return st


def test_fetch_reads_a_public_https_document_with_tight_limits(real_fetch):
    assert real_fetch["fetch"](HOSTED) == DOCS[HOSTED]
    url, kw = real_fetch["calls"][0]
    assert url == HOSTED and kw["timeout"] == 5 and kw["allow_redirects"] is False and kw["stream"] is True


@pytest.mark.parametrize("addrs", [
    ["10.0.0.5"], ["172.16.3.4"], ["192.168.1.1"], ["127.0.0.1"], ["169.254.169.254"], ["100.64.0.1"], ["0.0.0.0"],
    ["::1"], ["fe80::1"], ["fc00::1"], ["::ffff:10.0.0.5"], ["224.0.0.1"], ["160.79.104.10", "10.0.0.5"], [],
    ["not-an-ip"]])
def test_fetch_refuses_hosts_that_resolve_to_non_public_addresses(real_fetch, addrs):
    real_fetch["addrs"] = addrs
    with pytest.raises(mcp_oauth.OAuthError) as e:
        real_fetch["fetch"](HOSTED)
    assert e.value.error == "invalid_client" and real_fetch["calls"] == []       # refused before any request


@pytest.mark.parametrize("response", [
    FakeResponse(302, b""), FakeResponse(301, json.dumps(DOCS[HOSTED]).encode()), FakeResponse(404, b"{}"),
    FakeResponse(500, b"{}"), FakeResponse(200, b"not json"), FakeResponse(200, b"\xff\xfe"),
    FakeResponse(200, b'{"a": "' + b"x" * (70 * 1024) + b'"}'), OSError("connection reset"), TimeoutError("slow")])
def test_fetch_refuses_redirects_errors_non_json_and_oversized_bodies(real_fetch, response):
    real_fetch["response"] = response
    with pytest.raises(mcp_oauth.OAuthError) as e:
        real_fetch["fetch"](HOSTED)
    assert e.value.error == "invalid_client" and "reset" not in e.value.description


def test_dns_failure_is_refused(real_fetch, monkeypatch):
    monkeypatch.setattr(mcp_oauth, "resolve", lambda host: (_ for _ in ()).throw(OSError("NXDOMAIN")))
    with pytest.raises(mcp_oauth.OAuthError):
        real_fetch["fetch"](HOSTED)
    assert real_fetch["calls"] == []


# ---------------------------------------------------------------- the consent page's two routes

def test_approve_needs_the_couples_own_valid_tulle_token(env, c):
    blob = blob_of(c)
    answers = []
    for tok in (None, "nope", "xano-good-but-wrong", ""):
        r = approve(c, blob, token=tok)
        assert r.status_code == 401 and r.json()["error"] == "sign_in_required" and "redirect" not in r.text
        answers.append(r.json())
    assert all(a == answers[0] for a in answers)                 # one answer: nothing to tell accounts apart by
    r = c.post("/oauth/approve", json={"req": blob}, headers={"Origin": ORIGIN, "Authorization": "xano-good"})
    assert r.status_code == 401                                  # the literal "Bearer " prefix is required


def test_approve_for_an_account_that_is_not_on_the_allowlist(env, c, monkeypatch):
    r = approve(c, blob_of(c), token="xano-other")
    assert r.status_code == 403 and r.json() == {
        "error": "not_available", "error_description": "Tulle for Claude is not available for your account yet."}
    monkeypatch.setattr(config, "MCP_ALL_USERS", True)           # REC_MCP_USER_IDS=*
    assert approve(c, blob_of(c), token="xano-other").status_code == 200


def test_star_in_the_env_var_means_everyone():
    import importlib
    import os
    old = os.environ.get("REC_MCP_USER_IDS")
    try:
        for raw, everyone, ids in (("*", True, set()), (" 7, * ,9", True, {7, 9}), ("7,9", False, {7, 9}), ("", False, set())):
            os.environ["REC_MCP_USER_IDS"] = raw
            cfg = importlib.reload(config)
            assert (cfg.MCP_ALL_USERS, set(cfg.MCP_USER_IDS)) == (everyone, ids), raw
    finally:
        os.environ.pop("REC_MCP_USER_IDS", None) if old is None else os.environ.__setitem__("REC_MCP_USER_IDS", old)
        importlib.reload(config)


def test_approve_kill_switch_and_xano_outage(env, c, monkeypatch):
    blob = blob_of(c)
    env["down"] = True
    r = approve(c, blob)
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable" and "xano" not in r.text.lower()
    env["down"] = False
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    assert approve(c, blob).status_code == 503
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    assert approve(c, blob).status_code == 200


@pytest.mark.parametrize("origin", [None, "https://tulletogether.app", "https://editor.weweb.io", "https://claude.ai",
                                    "http://www.tulletogether.app", "https://www.tulletogether.app.evil.example", "null"])
def test_only_the_consent_pages_origin_may_call_the_consent_routes(env, c, origin):
    blob = blob_of(c)
    h = {"Origin": origin} if origin else {}
    for r in (approve(c, blob, origin=origin), c.get("/oauth/request", params={"req": blob}, headers=h),
              c.options("/oauth/approve", headers=dict(h, **{"Access-Control-Request-Method": "POST"}))):
        assert r.status_code == 403 and r.json() == {"error": "origin_not_allowed"}
        assert "access-control-allow-origin" not in r.headers and "redirect" not in r.text
    assert env["auth"] == []


def test_preflight_from_the_consent_page(env, c):
    r = c.options("/oauth/approve", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 204 and r.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in r.headers["access-control-allow-methods"] and "Authorization" in r.headers["access-control-allow-headers"]
    assert r.headers["vary"] == "Origin"


def test_expired_tampered_or_foreign_request_blobs(env, c):
    good = blob_of(c)
    stale = mcp_oauth.seal("request", {"cid": HOSTED, "ru": CALLBACK, "cc": CHALLENGE, "st": "s"}, 600, now=time.time() - 601)
    code = code_of(c)
    for blob in (stale, good[:-2], good[:40], good + "A", "A" + good[1:], good[:60] + ("B" if good[60] != "B" else "C") + good[61:],
                 "", "not a blob", code, signed_in(c)["access_token"]):
        for r in (approve(c, blob), c.get("/oauth/request", params={"req": blob}, headers={"Origin": ORIGIN})):
            assert r.status_code == 400 and r.json()["error"] == "invalid_request" and "redirect" not in r.json()
    r = c.post("/oauth/approve", content=b"not json", headers={"Origin": ORIGIN, "Authorization": "Bearer xano-good"})
    assert r.status_code == 400


# ---------------------------------------------------------------- /token

def test_wrong_pkce_verifier(env, c):
    code = code_of(c)
    for verifier in ("w" * 43, VERIFIER + "x", VERIFIER[:-1], CHALLENGE, "short", "bad chars " + "x" * 40):
        r = exchange(c, code, code_verifier=verifier)
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant", verifier
    assert exchange(c, code).status_code == 200                  # failed guesses did not burn the code


def test_a_code_works_once(env, c):
    code = code_of(c)
    assert exchange(c, code).status_code == 200
    again = exchange(c, code)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant" and "access_token" not in again.text


def test_expired_code(env, c):
    old = mcp_oauth.seal("code", {"uid": 7, "xt": "xano-good", "cid": HOSTED, "ru": CALLBACK, "cc": CHALLENGE,
                                  "rexp": int(time.time()) + 1000}, 60, now=time.time() - 61)
    r = exchange(c, old)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


@pytest.mark.parametrize("over", [{"client_id": CODE_CLI}, {"client_id": HOSTED + "x"},
                                  {"redirect_uri": "http://localhost:1/callback"}, {"redirect_uri": CALLBACK + "/"}])
def test_code_is_bound_to_its_client_and_redirect_uri(env, c, over):
    r = exchange(c, code_of(c), **over)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


@pytest.mark.parametrize("over,error", [
    ({"client_id": None}, "invalid_request"), ({"code_verifier": None}, "invalid_request"), ({"code": None}, "invalid_request"),
    ({"grant_type": "client_credentials"}, "unsupported_grant_type"), ({"grant_type": "password"}, "unsupported_grant_type"),
    ({"grant_type": None}, "unsupported_grant_type"), ({"resource": "https://other.example/mcp"}, "invalid_target")])
def test_token_request_errors(env, c, over, error):
    r = exchange(c, code_of(c), **over)
    assert r.status_code == 400 and r.json()["error"] == error and "access_token" not in r.text


def test_token_endpoint_takes_form_encoding_only(env, c):
    code = code_of(c)
    r = c.post("/token", json={"grant_type": "authorization_code", "code": code, "code_verifier": VERIFIER, "client_id": HOSTED})
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"
    assert c.get("/token").status_code == 405
    assert exchange(c, code).status_code == 200


def test_every_kind_of_token_only_works_as_its_own_kind(env, c):
    code, blob = code_of(c), blob_of(c)
    tok = signed_in(c)
    access, refresh = tok["access_token"], tok["refresh_token"]
    for wrong in (access, code, blob, "xano-good"):              # ... as a refresh token
        r = token(c, grant_type="refresh_token", refresh_token=wrong, client_id=HOSTED)
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    for wrong in (access, refresh, blob):                        # ... as a code
        assert exchange(c, wrong).json()["error"] == "invalid_grant"
    for wrong in (refresh, code, blob):                          # ... as an access token
        assert_challenge(mcp(c, wrong), "invalid_token")
    assert mcp(c, access).status_code == 200 and exchange(c, code).status_code == 200


def test_tampered_truncated_and_foreign_tokens(env, c, monkeypatch):
    tok = signed_in(c)
    a, r = tok["access_token"], tok["refresh_token"]
    flip = lambda s, i: s[:i] + ("B" if s[i] != "B" else "C") + s[i + 1:]       # noqa: E731
    for bad in (a[:-1], a[:-8], a[:30], a + "A", flip(a, 3), flip(a, len(a) // 2), flip(a, len(a) - 2), "", "a.b.c",
                "A" * 200, a + "=", a.replace("-", "+")):
        assert_challenge(mcp(c, bad), "invalid_token" if bad else None)
    for bad in (r[:-1], r[:30], r + "A", flip(r, 5), flip(r, len(r) // 2), "A" * 200):
        x = token(c, grant_type="refresh_token", refresh_token=bad)
        assert x.status_code == 400 and x.json()["error"] == "invalid_grant"
    monkeypatch.setattr(config, "MCP_TOKEN_KEY", "another-key-" + "y" * 40)      # issued under another key
    assert_challenge(mcp(c, a), "invalid_token")
    assert token(c, grant_type="refresh_token", refresh_token=r).json()["error"] == "invalid_grant"
    monkeypatch.setattr(config, "MCP_TOKEN_KEY", KEY)
    monkeypatch.setattr(config, "MCP_PUBLIC_URL", "https://other.tulle.test")    # issued for another resource
    with pytest.raises(mcp_oauth.BadToken):
        mcp_oauth.unseal(a, "access")
    monkeypatch.setattr(config, "MCP_PUBLIC_URL", BASE)
    assert mcp(c, a).status_code == 200


def test_expired_access_token_and_the_14_day_ceiling(env, c):
    now = int(time.time())
    base = {"uid": 7, "xt": "xano-good", "cid": HOSTED}
    assert_challenge(mcp(c, mcp_oauth.seal("access", base, 3600, now=now - 3601)), "invalid_token")
    assert mcp(c, mcp_oauth.seal("access", base, 3600, now=now - 3500)).status_code == 200
    stale = mcp_oauth.seal("refresh", dict(base, rexp=now - 5), 14 * 86400, now=now - 14 * 86400 - 5)
    assert token(c, grant_type="refresh_token", refresh_token=stale).json()["error"] == "invalid_grant"
    # 10 minutes of sign-in left: the access token is cut to it, and rotation never extends the refresh token
    near = mcp_oauth.seal("refresh", dict(base, rexp=now + 600), 600, now=now)
    t = token(c, grant_type="refresh_token", refresh_token=near).json()
    assert 590 <= t["expires_in"] <= 600
    assert mcp_oauth.unseal(t["refresh_token"], "refresh")["exp"] <= now + 601
    fresh = mcp_oauth.unseal(signed_in(c)["refresh_token"], "refresh")
    assert abs(fresh["rexp"] - (now + 14 * 86400)) < 30 and fresh["exp"] <= fresh["rexp"] + 1


def test_refresh_is_bound_to_its_client(env, c):
    tok = signed_in(c)
    r = token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=CODE_CLI)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert token(c, grant_type="refresh_token").json()["error"] == "invalid_request"
    assert token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"], client_id=HOSTED).status_code == 200


# ---------------------------------------------------------------- the Tulle sign-in behind the token

def test_xano_token_expired_mid_life_asks_claude_to_sign_in_again(env, c):
    tok = signed_in(c)
    assert mcp(c, tok["access_token"]).status_code == 200
    del env["valid"]["xano-good"]                                # signed out of Tulle / the Xano token expired
    assert_challenge(mcp(c, tok["access_token"]), "invalid_token")
    r = token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"])
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"         # -> Claude starts sign-in again


def test_account_removed_from_the_allowlist_or_killed_mid_life(env, c, monkeypatch):
    tok = signed_in(c)
    monkeypatch.setattr(config, "MCP_USER_IDS", frozenset({99}))
    r = mcp(c, tok["access_token"])
    assert r.status_code == 403 and "www-authenticate" not in r.headers and "Ana" not in r.text
    assert token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"]).json()["error"] == "invalid_grant"
    monkeypatch.setattr(config, "MCP_USER_IDS", frozenset({7}))
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    assert mcp(c, tok["access_token"]).status_code == 503


def test_xano_outage_does_not_destroy_the_sign_in(env, c):
    tok = signed_in(c)
    env["down"] = True
    r = mcp(c, tok["access_token"])
    assert r.status_code == 503 and "xano" not in r.text.lower()
    r = token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"])
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable"
    env["down"] = False
    assert token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"]).status_code == 200   # not burned


def test_a_token_minted_for_another_user_id_is_refused(env, c):
    forged = mcp_oauth.seal("access", {"uid": 8, "xt": "xano-good", "cid": HOSTED}, 3600)
    assert_challenge(mcp(c, forged), "invalid_token")


def test_raw_xano_token_is_no_longer_a_bearer_on_mcp(env, c, monkeypatch):
    assert_challenge(mcp(c, "xano-good"), "invalid_token")
    assert env["auth"] == []
    r = c.post("/mcp", json=CALL, headers=dict(H_MCP, Authorization="xano-good"))
    assert_challenge(r)
    monkeypatch.setattr(config, "MCP_DEV_RAW_TOKEN", True)       # REC_MCP_DEV_RAW_TOKEN=1: local testing only
    assert mcp(c, "xano-good").status_code == 200
    assert_challenge(mcp(c, "nope"), "invalid_token")


# ---------------------------------------------------------------- Origin, rate limits, routes

def test_origin_is_refused_everywhere_but_the_consent_routes(env, c):
    tok = signed_in(c)
    code = code_of(c)
    for origin in (ORIGIN, "https://claude.ai", "https://editor.weweb.io"):
        assert mcp(c, tok["access_token"], Origin=origin).status_code == 403
        assert c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
                      headers={"Origin": origin}).status_code == 403
        assert c.get("/authorize", params={"client_id": HOSTED}, headers={"Origin": origin}).status_code == 403
    assert exchange(c, code).status_code == 200                  # nothing was consumed by the refused calls
    assert token(c, grant_type="refresh_token", refresh_token=tok["refresh_token"]).status_code == 200


def test_allowed_origins_setting_lets_a_named_origin_through(env, monkeypatch):
    monkeypatch.setattr(config, "MCP_ALLOWED_ORIGINS", ["https://claude.ai"])
    with TestClient(_host(), base_url=BASE, follow_redirects=False) as c:
        tok = signed_in(c)
        assert mcp(c, tok["access_token"], Origin="https://claude.ai").status_code == 200
        assert mcp(c, tok["access_token"], Origin="https://evil.example").status_code == 403
        assert c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
                      headers={"Origin": "https://claude.ai"}).status_code == 200


def test_host_header_must_be_the_public_host(env, c):
    tok = signed_in(c)
    assert mcp(c, tok["access_token"], Host="evil.example").status_code == 421
    assert mcp(c, tok["access_token"], Host="mcp.tulle.test").status_code == 200


def test_rate_limits(env, c, monkeypatch):
    monkeypatch.setattr(mcp_oauth, "RATE", {"authorize": (3, 60.0), "approve": (2, 60.0), "token": (2, 60.0)})
    assert [authz(c).status_code for _ in range(5)] == [302, 302, 302, 429, 429]
    blob = blob_of(c, **{}) if False else mcp_oauth.seal("request", {"cid": HOSTED, "ru": CALLBACK, "cc": CHALLENGE, "st": ""}, 600)
    got = [approve(c, blob, token="nope").status_code for _ in range(4)]
    assert got == [401, 401, 429, 429]
    got = [token(c, grant_type="refresh_token", refresh_token="x").status_code for _ in range(4)]
    assert got == [400, 400, 429, 429]
    r = token(c, grant_type="refresh_token", refresh_token="x")
    assert r.headers["retry-after"] == "30" and r.json()["error"] == "slow_down"
    # per client address: another address still gets through
    assert c.post("/token", data={"grant_type": "refresh_token", "refresh_token": "x"},
                  headers={"X-Real-IP": "203.0.113.9"}).status_code == 400
    b = mcp_oauth._Buckets()
    assert [b.allow("token", "a", now=0) for _ in range(3)] == [True, True, False]
    assert b.allow("token", "a", now=31) is True                 # refills: 2 a minute -> one every 30 s


def test_exactly_these_routes_are_added(env):
    plain = FastAPI()
    before = {getattr(r, "path", "") for r in plain.routes}
    host = _host()
    added = sorted({getattr(r, "path", "") for r in host.routes} - before - {"/health"})
    assert added == ["/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-protected-resource/mcp", "/authorize", "/mcp", "/oauth/approve",
                     "/oauth/request", "/token"]
    with TestClient(host, base_url=BASE, follow_redirects=False) as c:
        assert c.get("/health").json() == {"ok": True}           # the host app is untouched
        assert c.get("/health", headers={"Origin": "https://anything.example"}).status_code == 200
        for path in ("/register", "/.well-known/openid-configuration", "/oauth/deny", "/revoke"):
            assert c.get(path).status_code == 404 and c.post(path).status_code == 404
        assert c.post("/authorize").status_code == 405 and c.post("/oauth/request", headers={"Origin": ORIGIN}).status_code == 405


def test_used_ids_store(env):
    u = mcp_oauth._Used(cap=3)
    assert u.claim("a", exp=100, now=0) is True and u.claim("a", exp=100, now=1) is False
    assert u.claim("a", exp=200, now=150) is True                # past its expiry the id no longer matters
    for i in range(10):
        assert u.claim("k%d" % i, exp=1000 + i, now=200) is True
    assert len(u._d) <= 3                                        # bounded


def test_seal_is_opaque_and_kind_bound(env):
    t = mcp_oauth.seal("access", {"uid": 7, "xt": "xano-good", "cid": HOSTED}, 60)
    assert "xano-good" not in t and "claude" not in t
    assert "xano-good" not in base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)).decode("latin-1")
    assert mcp_oauth.unseal(t, "access")["xt"] == "xano-good"
    for kind in ("refresh", "code", "request"):
        with pytest.raises(mcp_oauth.BadToken):
            mcp_oauth.unseal(t, kind)
    assert mcp_oauth.seal("access", {"uid": 7}, 60) != mcp_oauth.seal("access", {"uid": 7}, 60)     # fresh nonce + id
