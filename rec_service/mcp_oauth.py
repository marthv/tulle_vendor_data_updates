"""'Sign in with Tulle' for the MCP prototype (2026-11-15): the OAuth 2.1 side of mcp_server.py, so Claude (claude.ai,
Desktop, mobile, Claude Code) can add <REC_MCP_PUBLIC_URL>/mcp as a custom connector. PRIVATE PROTOTYPE, off unless
REC_MCP=1 and REC_MCP_PUBLIC_URL and REC_MCP_TOKEN_KEY are both set (anything missing: nothing is mounted).

Written against (read 2026-11-15): claude.com/docs/connectors/building/{authentication,lazy-authentication,testing,
troubleshooting}.md and the MCP authorization spec 2025-11-25.

THE FLOW
    1. Claude POSTs to /mcp with no token. The gate (mcp_server._Gate, before the MCP SDK runs) answers 401 with
       WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource/mcp".
    2. Claude reads that document (RFC 9728: resource = <base>/mcp exactly, authorization_servers = [<base>]) and then
       <base>/.well-known/oauth-authorization-server (RFC 8414), which advertises CIMD + a public client ("none") +
       PKCE S256 - the two values Claude needs before it uses a Client ID Metadata Document.
    3. The browser opens GET /authorize. Claude's client_id is an HTTPS URL: its host must be in REC_MCP_CLIENT_HOSTS
       (default claude.ai), the document behind it is fetched (HTTPS only, 5 s, 64 KB, no redirects, public addresses
       only) and must name itself and list the redirect_uri (exact match; loopback hosts match with the port ignored,
       as Claude Code needs). The validated request is sealed into a 10-minute blob and the browser is sent to the
       consent page on the Tulle web app: <REC_MCP_CONSENT_URL>?req=<blob>. There is no client database and no DCR.
    4. The consent page (WeWeb - the couple's Tulle session lives there, and a third of couples have no password)
       calls GET /oauth/request?req= for what to show, and on Approve POST /oauth/approve with the couple's own Xano
       token. That is checked by mcp_server.caller (app._auth, the allowlist, the kill switch) and answered with
       {"redirect": "<redirect_uri>?code=...&state=..."}.
    5. Claude POSTs /token (form-encoded): the code + its PKCE verifier -> an access token (1 h) and a refresh token.
       Refresh rotates: a new refresh token each time, the old one is refused with invalid_grant.
    6. Every /mcp request carries the access token. The gate opens it, checks it was issued for <base>/mcp, takes
       the couple's Xano token out of it and runs the SAME mcp_server.caller as before - so auth/me, the allowlist
       and the kill switch are re-checked on every call. A Xano token that Xano no longer accepts -> 401 with the
       header again, and Claude re-runs sign-in. A raw Xano token is NOT accepted on /mcp any more (only with
       REC_MCP_DEV_RAW_TOKEN=1, for local testing).

TOKENS ARE STATELESS AND ENCRYPTED (this service restarts on every deploy and has no session store)
    AES-256-GCM (the `cryptography` package), key = SHA-256 of REC_MCP_TOKEN_KEY. One format for all four kinds -
    request blob (10 min), code (60 s), access (1 h), refresh (until 14 days after approval: the assumed life of the
    Xano token inside; rotation never extends it). The kind is bound into the authenticated data, so an access token
    can never be opened as a refresh token or a code. The payload holds the user id, the couple's Xano token (the
    tools need it downstream; it never leaves this service in the clear), client_id, redirect_uri and PKCE challenge
    (codes), the audience, issued-at, expiry and a random id. Nothing here is ever logged.
    KNOWN LIMITS of having no store:
      - "used once" (codes) and "rotated away" (refresh tokens) are remembered in memory only. After a restart a
        code could be replayed inside its 60 s life (it still needs the PKCE verifier), and an OLD refresh token
        becomes usable again until its own expiry. More than one worker process has the same gap all the time.
      - There is no revocation: disconnecting in Claude does not kill a token here. Signing out of Tulle (the Xano
        token dies) or changing REC_MCP_TOKEN_KEY (everyone signs in again) does.
      - Two refreshes racing with the same token: the second gets invalid_grant and the couple signs in again.

ORIGIN HEADERS
    /oauth/request and /oauth/approve are the only routes a web page may call, and only from the origin of
    REC_MCP_CONSENT_URL (anything else, or no Origin, is 403). /authorize, /token and /mcp refuse any request that
    carries an Origin not listed in REC_MCP_ALLOWED_ORIGINS (default: none). NOTE: Claude's testing doc lists "strict
    Origin-header validation" as a cause of failed connections - if Anthropic's servers do send an Origin, set that
    variable (e.g. https://claude.ai). Unverified either way.

RATE LIMITS
    Per client address, in memory: /authorize and /oauth/approve 30 a minute, /token 120 a minute (hosted Claude
    calls it from Anthropic's shared egress range). The address is X-Real-IP, else the last X-Forwarded-For entry,
    else the socket peer - which of these the proxy in front sets is not verified.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import config

REQUEST_S = 600
CODE_S = 60
ACCESS_S = 3600
REFRESH_S = 14 * 86400          # assumed life of the couple's Xano token, counted from approval
CIMD_TIMEOUT_S = 5
CIMD_MAX_BYTES = 64 * 1024
CIMD_CACHE_S = 600
MAX_PARAM = 2048                # longest state / client_id / redirect_uri accepted
LOOPBACK = ("localhost", "127.0.0.1", "[::1]", "::1")
PRM_PATHS = ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource")
ASM_PATH = "/.well-known/oauth-authorization-server"
CONSENT_PATHS = ("/oauth/request", "/oauth/approve")
# What the consent page lists (it is what the five tools return - keep in step with mcp_server.TOOLS).
SHARES = ["Your wedding profile: location, guest count, date, budget and style",
          "Your wedding plan and budget as you entered them: what you booked or are considering, costs you typed in, notes",
          "The vendors you saved on Tulle (name, state and type)",
          "Vendors you ask about by name (name, state and type)"]
NOT_SHARED = "Vendor prices and pricing documents are never shared."
RATE = {"authorize": (30, 60.0), "approve": (30, 60.0), "token": (120, 60.0)}     # (requests, per seconds)


class OAuthError(Exception):
    """An OAuth error response: error code, a description safe to show, the HTTP status."""

    def __init__(self, error, description="", status=400):
        super().__init__(error)
        self.error, self.description, self.status = error, description, status


class BadToken(Exception):
    """A sealed value that is not ours, not of the asked kind, tampered with or expired. Never says which."""


# ---------------------------------------------------------------- configuration

def base_url():
    return str(config.MCP_PUBLIC_URL or "").strip().rstrip("/")


def resource():
    return base_url() + "/mcp"


def public_host():
    return (urlsplit(base_url()).netloc or "").lower()


def consent_origin():
    u = urlsplit(str(config.MCP_CONSENT_URL or "").strip())
    return "%s://%s" % (u.scheme, u.netloc) if u.scheme and u.netloc else ""


def not_ready():
    """Why the OAuth-protected MCP endpoint cannot be mounted ([] = it can). Checked once, at mount."""
    why = []
    u = urlsplit(base_url())
    if not base_url():
        why.append("REC_MCP_PUBLIC_URL is empty")
    elif not u.netloc or u.path or u.query or u.fragment or not (
            u.scheme == "https" or (u.scheme == "http" and (u.hostname or "") in LOOPBACK)):
        why.append("REC_MCP_PUBLIC_URL must be an https origin with no path, e.g. https://mcp.tulletogether.app")
    if len(str(config.MCP_TOKEN_KEY or "")) < 32:
        why.append("REC_MCP_TOKEN_KEY is missing or shorter than 32 characters")
    if not consent_origin().startswith("https://"):
        why.append("REC_MCP_CONSENT_URL must be an https URL")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except Exception:
        why.append("the `cryptography` package is not installed")
    return why


def canonical(url):
    """RFC 8707 canonical form for comparing resource values: lowercase scheme and host, no trailing slash."""
    u = urlsplit(str(url or "").strip())
    if not u.scheme or not u.netloc or u.fragment:
        return ""
    return "%s://%s%s%s" % (u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), "?" + u.query if u.query else "")


def _same(a, b):
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


# ---------------------------------------------------------------- sealed values (AES-256-GCM)

def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s):
    s = str(s or "")
    if not s or len(s) > 8192 or not re.fullmatch(r"[A-Za-z0-9_-]+", s):
        raise BadToken()
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except Exception:
        raise BadToken()


def _aead():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(hashlib.sha256(b"tulle-mcp-oauth-v1|" + str(config.MCP_TOKEN_KEY).encode("utf-8")).digest())


def _aad(kind):
    return ("tulle-mcp-oauth-v1:%s:%s" % (kind, resource())).encode("utf-8")


def seal(kind, payload, ttl, now=None):
    """payload (a small dict) -> an opaque token of this kind that expires ttl seconds from now."""
    now = int(time.time() if now is None else now)
    body = dict(payload, k=kind, iat=now, exp=now + int(ttl), jti=secrets.token_urlsafe(12))
    nonce = os.urandom(12)
    return _b64(nonce + _aead().encrypt(nonce, json.dumps(body, separators=(",", ":")).encode("utf-8"), _aad(kind)))


def unseal(token, kind, now=None):
    """The payload of a token of exactly this kind, or BadToken (wrong kind, wrong key, altered, cut short, expired)."""
    raw = _unb64(token)
    if len(raw) < 12 + 16 + 2:
        raise BadToken()
    try:
        body = json.loads(_aead().decrypt(raw[:12], raw[12:], _aad(kind)).decode("utf-8"))
    except Exception:
        raise BadToken()
    now = time.time() if now is None else now
    if not isinstance(body, dict) or body.get("k") != kind or not isinstance(body.get("exp"), int) or body["exp"] <= now:
        raise BadToken()
    return body


class _Used:
    """Token ids that must not be accepted again, until they would have expired anyway. In memory (module docstring)."""

    def __init__(self, cap=200000):
        self._d, self._lock, self.cap = {}, threading.Lock(), cap

    def claim(self, jti, exp, now=None):
        """True the first time this id is seen (and remembers it), False on every later call."""
        now = time.time() if now is None else now
        with self._lock:
            if len(self._d) >= self.cap:
                for k in [k for k, e in self._d.items() if e <= now]:
                    del self._d[k]
                while len(self._d) >= self.cap:                    # still full: forget the ones closest to expiry
                    del self._d[min(self._d, key=self._d.get)]
            if self._d.get(jti, 0) > now:
                return False
            self._d[jti] = exp
            return True

    def clear(self):
        with self._lock:
            self._d.clear()


USED = _Used()


class _Buckets:
    """Token bucket per (endpoint, client address)."""

    def __init__(self):
        self._d, self._lock = {}, threading.Lock()

    def allow(self, name, addr, now=None):
        limit, per = RATE[name]
        now = time.time() if now is None else now
        with self._lock:
            if len(self._d) > 50000:
                self._d.clear()
            tokens, last = self._d.get((name, addr), (float(limit), now))
            tokens = min(float(limit), tokens + (now - last) * limit / per)
            ok = tokens >= 1.0
            self._d[(name, addr)] = (tokens - 1.0 if ok else tokens, now)
            return ok

    def clear(self):
        with self._lock:
            self._d.clear()


BUCKETS = _Buckets()


# ---------------------------------------------------------------- the client: a Client ID Metadata Document

def _is_public_ip(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return bool(a.is_global) and not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
                                      or a.is_reserved or a.is_unspecified)


def resolve(host):
    """Every address the host resolves to (strings). Patched in tests."""
    return sorted({i[4][0] for i in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def check_client_url(client_id):
    """The client_id must be an https URL with a path on a host we accept (REC_MCP_CLIENT_HOSTS). No network."""
    cid = str(client_id or "")
    u = urlsplit(cid)
    host = (u.hostname or "").lower()
    if (len(cid) > MAX_PARAM or u.scheme != "https" or not host or u.username or u.password or u.fragment
            or u.port not in (None, 443) or not u.path or u.path == "/" or ".." in u.path.split("/")):
        raise OAuthError("invalid_client", "client_id must be an https URL of a client metadata document")
    if host not in {h.lower() for h in config.MCP_CLIENT_HOSTS}:
        raise OAuthError("invalid_client", "this client is not accepted here")
    return host


def fetch_cimd(url):
    """GET the document: HTTPS, public addresses only, 5 s, 64 KB, no redirects, JSON object. Patched in tests.
    (The addresses are checked before the request; the HTTP library resolves again - with the host allowlist in
    front of this, a DNS answer changing in between is an accepted gap.)"""
    import requests
    host = urlsplit(url).hostname or ""
    try:
        addrs = resolve(host)
    except Exception:
        raise OAuthError("invalid_client", "the client metadata document could not be fetched")
    if not addrs or not all(_is_public_ip(a) for a in addrs):
        raise OAuthError("invalid_client", "the client metadata document is not on a public address")
    try:
        with requests.get(url, timeout=CIMD_TIMEOUT_S, allow_redirects=False, stream=True,
                          headers={"Accept": "application/json", "User-Agent": "tulle-mcp-oauth"}) as r:
            if r.status_code != 200:
                raise OAuthError("invalid_client", "the client metadata document could not be fetched")
            body = r.raw.read(CIMD_MAX_BYTES + 1, decode_content=True)
    except OAuthError:
        raise
    except Exception:
        raise OAuthError("invalid_client", "the client metadata document could not be fetched")
    if len(body) > CIMD_MAX_BYTES:
        raise OAuthError("invalid_client", "the client metadata document is too large")
    try:
        doc = json.loads(body.decode("utf-8"))
    except Exception:
        raise OAuthError("invalid_client", "the client metadata document is not JSON")
    return doc


_CIMD, _CIMD_LOCK = {}, threading.Lock()


def client_document(client_id, now=None):
    """The validated metadata document of this client (cached 10 minutes). OAuthError invalid_client otherwise."""
    check_client_url(client_id)
    now = time.time() if now is None else now
    with _CIMD_LOCK:
        hit = _CIMD.get(client_id)
        if hit and hit[0] > now:
            return hit[1]
    doc = fetch_cimd(client_id)
    uris = doc.get("redirect_uris") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or not isinstance(doc.get("client_id"), str) or not _same(doc["client_id"], client_id):
        raise OAuthError("invalid_client", "the client metadata document does not match its URL")
    if not isinstance(uris, list) or not uris or not all(isinstance(x, str) and x for x in uris):
        raise OAuthError("invalid_client", "the client metadata document lists no redirect_uris")
    doc = {"client_id": client_id, "redirect_uris": list(uris)[:20]}
    with _CIMD_LOCK:
        if len(_CIMD) > 200:
            _CIMD.clear()
        _CIMD[client_id] = (now + CIMD_CACHE_S, doc)
    return doc


def _loopback(u):
    return u.scheme == "http" and (u.hostname or "").lower() in ("localhost", "127.0.0.1", "::1")


def redirect_allowed(doc, redirect_uri):
    """Exact match against the document's redirect_uris; a loopback URI (RFC 8252 7.3, and localhost for Claude Code)
    matches with the port ignored. Anything not https must be loopback."""
    ru = str(redirect_uri or "")
    if not ru or len(ru) > MAX_PARAM:
        return False
    try:
        u = urlsplit(ru)
        u.port                                      # raises on a malformed port
    except ValueError:
        return False
    if u.fragment or u.username or u.password or not (u.scheme == "https" or _loopback(u)):
        return False
    for reg in doc.get("redirect_uris") or []:
        if _same(reg, ru):
            return True
        try:
            r = urlsplit(reg)
        except ValueError:
            continue
        if (_loopback(u) and _loopback(r) and (u.hostname or "").lower() == (r.hostname or "").lower()
                and u.path == r.path and u.query == r.query):
            return True
    return False


def _with_params(url, params):
    return url + ("&" if "?" in url else "?") + urlencode(params)


# ---------------------------------------------------------------- the endpoints, as plain functions

def protected_resource_metadata():
    return {"resource": resource(), "authorization_servers": [base_url()], "bearer_methods_supported": ["header"]}


def authorization_server_metadata():
    b = base_url()
    return {"issuer": b, "authorization_endpoint": b + "/authorize", "token_endpoint": b + "/token",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],      # Claude's CIMD client is a public client
            "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": True}


def challenge(error="", description=""):
    """The WWW-Authenticate value of a 401 from /mcp: always points at the protected resource metadata."""
    parts = []
    if error:
        parts.append('error="%s"' % error)
    if description:
        parts.append('error_description="%s"' % description.replace('"', "'"))
    parts.append('resource_metadata="%s"' % (base_url() + PRM_PATHS[0]))
    return "Bearer " + ", ".join(parts)


def authorize(params):
    """GET /authorize -> the URL to send the browser to. OAuthError = show an error page (the client or its redirect
    URI is not trusted, so we never redirect there); every later problem goes back to the client as ?error=."""
    client_id, redirect_uri = params.get("client_id", ""), params.get("redirect_uri", "")
    doc = client_document(client_id)
    if not redirect_allowed(doc, redirect_uri):
        raise OAuthError("invalid_request", "redirect_uri is not registered for this client")
    state = params.get("state", "")

    def back(error, description):
        p = {"error": error, "error_description": description}
        if state and len(state) <= MAX_PARAM:
            p["state"] = state
        return _with_params(redirect_uri, p)
    if len(state) > MAX_PARAM:
        return back("invalid_request", "state is too long")
    if params.get("response_type") != "code":
        return back("unsupported_response_type", "only response_type=code is supported")
    if params.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.get("code_challenge", "")):
        return back("invalid_request", "PKCE with code_challenge_method=S256 is required")
    if params.get("resource") and not _same(canonical(params["resource"]), canonical(resource())):
        return back("invalid_target", "this authorization server only issues tokens for %s" % resource())
    blob = seal("request", {"cid": client_id, "ru": redirect_uri, "cc": params["code_challenge"], "st": state},
                REQUEST_S)
    return _with_params(str(config.MCP_CONSENT_URL).strip(), {"req": blob})


def _request(blob):
    try:
        return unseal(blob, "request")
    except BadToken:
        raise OAuthError("invalid_request", "This sign-in request has expired or is not valid. Start again from Claude.")


def request_info(blob):
    """GET /oauth/request: what the consent page shows. The app is named by the HOST of its client_id URL (the
    document's client_name is self-asserted), and the redirect host is always shown (MCP spec)."""
    r = _request(blob)
    ru = urlsplit(r["ru"])
    deny = {"error": "access_denied", "error_description": "The couple did not approve the connection"}
    if r.get("st"):
        deny["state"] = r["st"]
    return {"client_host": urlsplit(r["cid"]).hostname or "", "client_id": r["cid"],
            "redirect_host": ru.hostname or "", "redirect_is_loopback": _loopback(ru),
            "shares": list(SHARES), "not_shared": NOT_SHARED, "expires_in": max(0, int(r["exp"] - time.time())),
            "deny_redirect": _with_params(r["ru"], deny)}


def approve(blob, authorization):
    """POST /oauth/approve: the couple (their own Xano token, checked by mcp_server.caller) approves this request ->
    where the page must navigate next, carrying a single-use 60-second code."""
    import mcp_server
    r = _request(blob)
    try:
        token, user = mcp_server.caller(authorization)
    except mcp_server.MCPDenied as e:
        if e.status == 401:
            raise OAuthError("sign_in_required", "Sign in to Tulle, then approve again.", 401)
        if e.status == 403:
            raise OAuthError("not_available", "Tulle for Claude is not available for your account yet.", 403)
        raise OAuthError("temporarily_unavailable", "Tulle could not complete this right now. Try again in a moment.", 503)
    now = int(time.time())
    code = seal("code", {"uid": int(user["id"]), "xt": token, "cid": r["cid"], "ru": r["ru"], "cc": r["cc"],
                         "rexp": now + REFRESH_S}, CODE_S, now)
    p = {"code": code}
    if r.get("st"):
        p["state"] = r["st"]
    return {"redirect": _with_params(r["ru"], p)}


def _tokens(uid, xt, cid, rexp, now):
    """A fresh access + refresh pair. The refresh token dies at rexp (14 days after approval), however often rotated."""
    left = int(rexp - now)
    if left <= 0:
        raise OAuthError("invalid_grant", "the sign-in has expired")
    base = {"uid": uid, "xt": xt, "cid": cid}
    return {"access_token": seal("access", base, min(ACCESS_S, left), now), "token_type": "Bearer",
            "expires_in": min(ACCESS_S, left), "refresh_token": seal("refresh", dict(base, rexp=int(rexp)), left, now)}


def token(form):
    """POST /token (form-encoded): authorization_code (+ PKCE verifier) or refresh_token (rotating)."""
    import mcp_server
    grant = form.get("grant_type", "")
    now = int(time.time())
    if form.get("resource") and not _same(canonical(form["resource"]), canonical(resource())):
        raise OAuthError("invalid_target", "this authorization server only issues tokens for %s" % resource())
    if grant == "authorization_code":
        if not form.get("client_id") or not form.get("code") or not form.get("code_verifier"):
            raise OAuthError("invalid_request", "client_id, code and code_verifier are required")
        try:
            c = unseal(form["code"], "code", now)
        except BadToken:
            raise OAuthError("invalid_grant", "the authorization code is not valid or has expired")
        verifier = form["code_verifier"]
        ok = bool(re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier))
        ok = _same(_b64(hashlib.sha256(verifier.encode("ascii", "ignore")).digest()), c.get("cc")) and ok
        ok = _same(form["client_id"], c.get("cid")) and ok
        ok = (not form.get("redirect_uri") or _same(form["redirect_uri"], c.get("ru"))) and ok
        if not ok:                                   # one answer for every mismatch
            raise OAuthError("invalid_grant", "the authorization code is not valid or has expired")
        if not USED.claim(c["jti"], c["exp"], now):
            raise OAuthError("invalid_grant", "the authorization code is not valid or has expired")
        return _tokens(c["uid"], c["xt"], c["cid"], c["rexp"], now)
    if grant == "refresh_token":
        if not form.get("refresh_token"):
            raise OAuthError("invalid_request", "refresh_token is required")
        bad = OAuthError("invalid_grant", "the refresh token is not valid or has expired")
        try:
            r = unseal(form["refresh_token"], "refresh", now)
        except BadToken:
            raise bad
        if form.get("client_id") and not _same(form["client_id"], r.get("cid")):
            raise bad
        # Still signed in to Tulle and still allowed? (the same check every /mcp call runs)
        try:
            _, user = mcp_server.caller("Bearer " + r["xt"])
        except mcp_server.MCPDenied as e:
            if e.status in (401, 403):
                raise bad
            raise OAuthError("temporarily_unavailable", "try again in a moment", 503)
        if int(user.get("id") or 0) != int(r["uid"]):
            raise bad
        if not USED.claim(r["jti"], r["exp"], now):
            raise bad
        return _tokens(r["uid"], r["xt"], r["cid"], r["rexp"], now)
    raise OAuthError("unsupported_grant_type", "authorization_code and refresh_token are supported")


def bearer_caller(authorization):
    """The /mcp gate's auth: an access token issued here for this resource -> mcp_server.caller with the couple's
    Xano token from inside it. MCPDenied with status 401 carries .challenge (the WWW-Authenticate value)."""
    import mcp_server

    def deny(message, error="", description=""):
        e = mcp_server.MCPDenied(message, 401)
        e.challenge = challenge(error, description)
        return e
    if not isinstance(authorization, str) or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise deny("Sign in with Tulle to use this connector.")
    try:
        a = unseal(authorization[7:].strip(), "access")
    except BadToken:
        if config.MCP_DEV_RAW_TOKEN:                 # local testing only: the couple's raw Xano token, as before
            try:
                return mcp_server.caller(authorization)
            except mcp_server.MCPDenied as e:
                if e.status == 401:
                    raise deny(str(e), "invalid_token", "The access token is invalid or expired")
                raise
        raise deny("The access token is not valid or has expired.", "invalid_token", "The access token is invalid or expired")
    try:
        token_, user = mcp_server.caller("Bearer " + a["xt"])
    except mcp_server.MCPDenied as e:
        if e.status == 401:                          # Xano no longer accepts the token inside: sign in again
            raise deny("The Tulle sign-in has ended. Sign in again.", "invalid_token", "The Tulle sign-in has ended")
        raise
    if int(user.get("id") or 0) != int(a["uid"]):
        raise deny("The access token is not valid or has expired.", "invalid_token", "The access token is invalid or expired")
    return token_, user


# ---------------------------------------------------------------- HTTP (Starlette routes on the host app)

def _addr(request):
    h = request.headers
    xff = [x.strip() for x in (h.get("x-forwarded-for") or "").split(",") if x.strip()]
    return (h.get("x-real-ip") or "").strip() or (xff[-1] if xff else "") or (request.client.host if request.client else "?")


def _origin_refused(request):
    o = request.headers.get("origin")
    return o is not None and o not in config.MCP_ALLOWED_ORIGINS


class ConsentCors:
    """Outermost ASGI middleware for the two routes the consent page calls. Only REC_MCP_CONSENT_URL's origin may
    call them: its preflight is answered here, any other Origin (or none) is 403, and the Origin header is removed
    before the request goes inward so the host app's own CORS rules never apply to these routes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in CONSENT_PATHS:
            return await self.app(scope, receive, send)
        headers = scope.get("headers") or []
        origin = next((v.decode("latin-1") for k, v in headers if k == b"origin"), None)
        allowed = consent_origin()
        cors = [(b"access-control-allow-origin", allowed.encode("ascii")), (b"vary", b"Origin")]
        if origin is None or not allowed or not _same(origin, allowed):
            body = b'{"error":"origin_not_allowed"}'
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            return await send({"type": "http.response.body", "body": body})
        if scope.get("method") == "OPTIONS":
            await send({"type": "http.response.start", "status": 204, "headers": cors + [
                (b"access-control-allow-methods", b"GET, POST"),
                (b"access-control-allow-headers", b"Authorization, Content-Type"),
                (b"access-control-max-age", b"600"), (b"content-length", b"0")]})
            return await send({"type": "http.response.body", "body": b""})
        inner = dict(scope, headers=[(k, v) for k, v in headers if k != b"origin"])

        async def send_cors(message):
            if message.get("type") == "http.response.start":
                message = dict(message, headers=list(message.get("headers") or []) + cors)
            await send(message)
        await self.app(inner, receive, send_cors)


def install(app):
    """Add the metadata documents, /authorize, /token and the two consent-page routes to the host app."""
    import anyio
    from starlette.responses import JSONResponse, RedirectResponse
    from starlette.routing import Route

    no_store = {"Cache-Control": "no-store", "Pragma": "no-cache"}

    def err(e):
        return JSONResponse({"error": e.error, "error_description": e.description}, status_code=e.status, headers=no_store)

    def limited(name, request):
        if not BUCKETS.allow(name, _addr(request)):
            return JSONResponse({"error": "slow_down", "error_description": "too many requests, try again shortly"},
                                status_code=429, headers=dict(no_store, **{"Retry-After": "30"}))
        return None

    async def prm(request):
        return JSONResponse(protected_resource_metadata(), headers={"Cache-Control": "public, max-age=300"})

    async def asm(request):
        return JSONResponse(authorization_server_metadata(), headers={"Cache-Control": "public, max-age=300"})

    async def authorize_ep(request):
        if _origin_refused(request):
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
        params = {k: v for k, v in request.query_params.items()}
        try:
            return limited("authorize", request) or RedirectResponse(
                await anyio.to_thread.run_sync(authorize, params), status_code=302, headers=no_store)
        except OAuthError as e:
            return err(e)

    async def token_ep(request):
        if _origin_refused(request):
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
        stop = limited("token", request)
        if stop:
            return stop
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        raw = await request.body()
        if ctype != "application/x-www-form-urlencoded" or len(raw) > 16384:
            return err(OAuthError("invalid_request", "send application/x-www-form-urlencoded"))
        try:
            form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=20).items()}
            return JSONResponse(await anyio.to_thread.run_sync(token, form), headers=no_store)
        except OAuthError as e:
            return err(e)
        except (UnicodeDecodeError, ValueError):
            return err(OAuthError("invalid_request", "the request body could not be read"))

    async def request_ep(request):
        try:
            return JSONResponse(request_info(request.query_params.get("req", "")), headers=no_store)
        except OAuthError as e:
            return err(e)

    async def approve_ep(request):
        stop = limited("approve", request)
        if stop:
            return stop
        try:
            body = json.loads((await request.body())[:16384].decode("utf-8") or "{}")
            blob = body.get("req", "") if isinstance(body, dict) else ""
        except (UnicodeDecodeError, ValueError):
            blob = ""
        try:
            return JSONResponse(await anyio.to_thread.run_sync(approve, blob, request.headers.get("authorization")),
                                headers=no_store)
        except OAuthError as e:
            return err(e)

    routes = [Route(p, prm, methods=["GET"]) for p in PRM_PATHS] + [
        Route(ASM_PATH, asm, methods=["GET"]), Route("/authorize", authorize_ep, methods=["GET"]),
        Route("/token", token_ep, methods=["POST"]), Route("/oauth/request", request_ep, methods=["GET"]),
        Route("/oauth/approve", approve_ep, methods=["POST"])]
    app.router.routes.extend(routes)
    app.add_middleware(ConsentCors)
    return [r.path for r in routes]
