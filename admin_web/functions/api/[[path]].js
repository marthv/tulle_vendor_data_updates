// /api/* proxy: browser → (Cloudflare Access) → this Pages Function → Railway admin_api.
//
// Cloudflare Access sits in front of admin.tulletogether.app, but a Pages project is also
// reachable at <project>.pages.dev and preview URLs. So this function does NOT trust that
// Access ran: it verifies the Access JWT itself (signature against the team's public keys,
// audience = this Access application, not expired) and only then adds the X-Admin-Key the
// Railway service requires. The browser never sees that key.
//
// Env (Pages → Settings → Variables; secrets marked *):
//   ACCESS_TEAM_DOMAIN   e.g. https://tulle.cloudflareaccess.com
//   ACCESS_AUD           the Access application's "Application Audience (AUD) Tag"
//   ADMIN_API_ORIGIN     e.g. https://tulle-admin-api-production.up.railway.app
//   ADMIN_API_KEY *      same value as the Railway service's ADMIN_API_KEY
//   ALLOWED_EMAILS       optional extra allow-list (comma-separated); Access policy is primary

let jwksCache = { at: 0, keys: null };

function b64urlToBytes(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}
const decodePart = (s) => JSON.parse(new TextDecoder().decode(b64urlToBytes(s)));

async function getKeys(team) {
  if (jwksCache.keys && Date.now() - jwksCache.at < 3600_000) return jwksCache.keys;
  const res = await fetch(`${team}/cdn-cgi/access/certs`);
  if (!res.ok) throw new Error("could not fetch Access certs");
  const { keys } = await res.json();
  jwksCache = { at: Date.now(), keys };
  return keys;
}

async function verifyAccessJwt(token, env) {
  const [h64, p64, s64] = token.split(".");
  if (!h64 || !p64 || !s64) throw new Error("malformed token");
  const header = decodePart(h64), payload = decodePart(p64);
  const team = env.ACCESS_TEAM_DOMAIN.replace(/\/$/, "");
  const jwk = (await getKeys(team)).find((k) => k.kid === header.kid);
  if (!jwk || header.alg !== "RS256") throw new Error("unknown signing key");
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlToBytes(s64), new TextEncoder().encode(`${h64}.${p64}`));
  if (!ok) throw new Error("bad signature");
  const now = Math.floor(Date.now() / 1000);
  if (payload.exp && payload.exp < now) throw new Error("expired");
  const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!aud.includes(env.ACCESS_AUD)) throw new Error("wrong audience");
  if (payload.iss && payload.iss !== team) throw new Error("wrong issuer");
  return payload;
}

function cookie(req, name) {
  const m = (req.headers.get("cookie") || "").match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
  return m ? m[1] : null;
}

export async function onRequest({ request, env, params }) {
  for (const k of ["ACCESS_TEAM_DOMAIN", "ACCESS_AUD", "ADMIN_API_ORIGIN", "ADMIN_API_KEY"])
    if (!env[k]) return Response.json({ error: `server misconfigured: ${k} not set` }, { status: 500 });

  // Local `wrangler pages dev` only: no Access in front of localhost. Requires BOTH the
  // env flag (never set in the Pages dashboard) and a loopback hostname.
  const host = new URL(request.url).hostname;
  const localDev = env.DEV_LOCAL_BYPASS === "1" && (host === "localhost" || host === "127.0.0.1");

  let claims;
  if (localDev) claims = { email: "local-dev@localhost" };
  else {
    const token = request.headers.get("cf-access-jwt-assertion") || cookie(request, "CF_Authorization");
    if (!token) return Response.json({ error: "not signed in (Cloudflare Access)" }, { status: 401 });
    try { claims = await verifyAccessJwt(token, env); }
    catch (e) { return Response.json({ error: `Access check failed: ${e.message}` }, { status: 403 }); }
  }

  const email = String(claims.email || "").toLowerCase();
  const extra = (env.ALLOWED_EMAILS || "").toLowerCase().split(",").map((s) => s.trim()).filter(Boolean);
  if (extra.length && !extra.includes(email)) return Response.json({ error: "not on the allow-list" }, { status: 403 });

  const url = new URL(request.url);
  const path = Array.isArray(params.path) ? params.path.join("/") : (params.path || "");
  const target = `${env.ADMIN_API_ORIGIN.replace(/\/$/, "")}/api/${path}${url.search}`;

  const headers = new Headers();
  for (const k of ["content-type", "accept", "accept-encoding"]) {
    const v = request.headers.get(k); if (v) headers.set(k, v);
  }
  headers.set("x-admin-key", env.ADMIN_API_KEY);
  headers.set("x-admin-user", email);

  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
  });
  const out = new Response(upstream.body, upstream);
  out.headers.set("cache-control", "no-store");
  return out;
}
