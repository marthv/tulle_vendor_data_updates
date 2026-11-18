---
name: project_endpoint_lockdown_401s
description: "The Aug-30/31 endpoint lockdown left five endpoints 401-ing in production, including ep119 search and the now-dead cache warmer"
metadata: 
  node_type: memory
  type: project
  originSessionId: 30aa8b0d-371d-4c16-8392-19a194145536
  modified: 2026-09-07T16:12:41.735Z
---

2026-09-07. The Aug-30/31 auth hardening ([[project_endpoint_auth_audit]]) set `auth: 1` on
endpoints that had unauthenticated callers. Live 401s over a 2.4h sample:

| Endpoint | 401s | `updated_at` |
|---|---|---|
| ep119 `wptp_updated_mappings_search` | 58 | 2026-08-30 |
| ep3 `auth/me` | 30 | pre-existing pre-auth race |
| ep34 `admin_choices` | 25 | 2026-07-21 |
| ep74 `wptp_updated_mappings_favorites` | 24 | 2026-08-30 |
| ep82 `donation_payments` | 13 | 2026-08-31 |
| **ep231 `venue/pricing_intelligence`** | — | **2026-08-30 23:33 — FIXED 2026-09-07** |

## ep231 was the worst of them, and it was silent

Found 2026-09-07 only because `.claude/tier2_check.py` could not fetch a single panel:
every call returned 401. **ep231 is the PUBLIC tier of the Pricing Intelligence panel** and
its own description says why it exists: *"TWO ENDPOINTS RATHER THAN ONE: Xano has no
optional auth, and this panel has to render for logged-out visitors. The paid twin is
venue/pricing_intelligence_full."* Locking it made it a duplicate of ep230 and broke the
free tier outright.

**Impact while it was locked (Aug 30 – Sep 7):** logged-out visitors got **no Pricing
Intelligence card at all** — not degraded, absent. The WeWeb card binds conditional
rendering to `is_venue`, so with no payload that is `undefined` → falsy → the whole section
does not render. Venue pages are the organic landing surface, so this hit exactly the
traffic that is not paid for. Invisible to anyone with a session, which is why nobody
caught it for eight days.

**Fixed 2026-09-07** — set back to public. Leave ep230 (`_full`, `auth=user`) locked; that
one is correct.

**Lesson worth keeping: the lockdown swept up more than the audit intended.** Four
endpoints confirmed. Nobody has diffed the whole Aug 30–31 audit-log window — do that
before assuming the rest are fine.

**Task 8, the ep119 cache warmer, sends NO Authorization header** — so every warm has 401'd
since Aug 30. Its signature in the logs: bursts every ~2h replaying TX/CA/FL/PA/NY/IL x
page 1-2 x page_size 4/12 with `fallback_all=1`, each bucket exactly 4x.

**User decided 2026-09-07 to leave the warmer dead and make NO ep119 changes.** The
reasoning, which held up: cache ttl is 7100s, so the warmer only ever helped the FIRST user
per bucket per 2h; v21 (db-side sort) and v22 (flt_states) already removed the pathological
cold cases; and it had been dead 8 days with zero speed complaints. **Do not re-raise the
warmer.**

## What is genuinely still open

Real users are still 401-ing on ep119 — the non-warmer rows carry real filters
(`states[]=Maryland&max_capacity=50&pricing_models[]=All-Inclusive`, `similar_to_vendor_id`).
There is **no anonymous browsing to support** (Sign In owns the root path `home`; Discovery
is built around a logged-in user), so the cause is the **pre-auth race** — WeWeb fires the
collection before the token is attached, the same shape as [[project_preauth_401_fix]].
That is a client-side fix, not an endpoint change.

## Do NOT confuse this with the PDF dead click

Separate bug. Opening a PDF is `window.open` on a Google Drive URL and calls no Xano
endpoint, so auth cannot break it — see [[project_pdf_dead_click]].

## The security calibration, if it comes up again

ep119 returns per vendor: Name, Website, Address, Country, lat/lng, State, Category,
Venue_Type, Max_Capacity_Seated, Description, Clicks_on_Venue, flt_min/max_venue_fee,
image_1/2/3, and **`flt_glance`** — which carries min/max fee, F&B minimum, per-person F&B
at peak Saturday, capacity, offerings/attributes, pricing_years, and **contact_information**.
It does **NOT** return `PDF_Link`, Drive URLs, or raw table-36 rows.

Two calibrating facts: it was **public from Dec 2025 to Aug 30 2026**, so the lock does not
undo prior exposure; and **`page_size` has no maximum**, so `page_size=10000` returns a whole
category in one call. Capping page_size is a bigger anti-scraping win than auth, and works
against authenticated scrapers too. User declined both for now.

## 2026-09-10 — THE ep119 CACHE WARMER IS FIXED (user reopened it)

It was worse than "dead": **it silently reported success for ten days.** All 23 `api.request`
calls in task 8 returned 401 after the Aug-30 hardening, but **`api.request` does not throw on
4xx**, so the `try_catch` never fired, `$warmed` still reached 23, and every run logged
`"Warmed 23/23 search cache buckets"`. The only tell was run DURATION: ~3s, where 23 genuine
cold ep119 calls take 60-90s. **Treat a suspiciously fast scheduled job as a failure signal.**

It also polluted analysis: those 23 failures per run are ~92% of all ep119 401s, and I briefly
mis-read them as user-facing "vendors not loading". Split 401s by client IP / User-Agent before
attributing them to users - `10.0.224.169` with no User-Agent is this task.

**Fix (no ep119 changes, per the standing decision):**
- Dedicated system account **user 29130**, `role: "system"`,
  `system+cache-warmer@tulletogether.invalid` (reserved TLD, can never receive mail).
  Deliberately NOT a real person's row, which would silently kill the task if deleted.
- Task 8 mints a 900s token for it via `security.create_auth_token` and sends a bearer header.
- **Only valid because ep119 has `cache.auth = false`** - the response cache is not keyed per
  user, so one warmed entry serves everyone. If `cache.auth` is ever switched on, delete this
  task rather than "fix" it.
- Success is now decided by `$resp.response.status`, failures counted separately, and any
  failure posts to `$env.slack_webhook_url`. No more decade-long silence.

**Verified live 2026-09-10:** no token -> 401 (0.22s); system token -> 200 (2.40s cold);
immediately again -> 200 (**0.31s, cache hit**); unauthenticated still 401, so the endpoint's
auth is intact. That is a **7.7x** speedup on a warmed bucket.

Backup: `.claude/xano_backups/task8_pre_auth_fix_2026-09-10.xs`.
⚠️ The system account's password was set through the metadata API (which hashes it), so the
plaintext exists in that session transcript. Zero-privilege account, but rotate it server-side
if that matters.

## 2026-09-12 — the warmer works, but warms keys NO USER EVER REQUESTS

Measured against a day of real traffic: **23 warmer cache keys vs 89 app cache keys, overlap
= 0.** Not one of 188 real search calls was served by a warmed entry. Xano keys on RESOLVED
inputs, and the two callers spell every empty filter differently — warmer `fallback_all=1`,
app `fallback_all=false`; warmer omits `max_capacity`, app sends `0` or `""`; likewise
`base_fee_max` and `Country_Input`. Absent, `""` and `"0"` are three different keys.

So task 8 spends 60-90s every 2h populating entries nobody can hit, and real users still see
only a **45.7%** hit rate (p95 3.7s, max 6.6s). Fix = make the warmer replay the app's exact
spellings; **still no ep119 change**, so the standing decision holds. See
[[project_perf_baseline_digest]] for the full baseline and the perf_digest check.

## 2026-09-16 — "ep119 is stalling" is NOT a latency problem. It is the 401s.

Measured from Xano request history, scoped to `Referer=tulletogether.app`, two windows
~20h apart (page 1: 18:13-20:16 UTC; page 6: 2026-09-15 23:34 - 09-16 01:38):

| window | app n | 401 | p50 | p95 | p99 | cache hit | 5xx |
|---|---|---|---|---|---|---|---|
| now    | 460 | 113 (**24.6%**) | 0.09s | 1.86s | 4.21s | 59.9% | 0 |
| ~20h ago | 476 | 188 (**39.5%**) | 0.09s | 1.88s | 3.19s | 65.6% | 0 |

**Latency and cache are BOTH INSIDE their perf_slo.json budgets** (p95 budget 3.0s, cache
budget 55%). There is no latency regression to find. Do not go optimising the query again.

The user-visible failure is that **1 in 4 to 1 in 3 Discovery searches returns 401**, which
renders as an empty grid — indistinguishable from a hang. Race-401 rate (same IP had an
authed 200 within 120s) = **21.5%, against a 2.0% budget — a 10x breach**, and the single
worst number on the endpoint.

**Mechanism: every one of the 113 401s carried NO `Authorization` header. Zero of them had
a token.** So it is not token rejection — it is the pre-auth race
([[project_preauth_race_signs_users_out]]): WeWeb fires ep119 before the auth plugin restores
the token, and ep119 has had `auth: 1` since the 2026-08-30 hardening.

UNEXPLAINED, and it slightly weakens the above: **45 tokenless requests returned 200** (all
cache MISSES, `X-Query-Cache: 0`, 0.9-1.9s), structurally identical URIs and headers to the
401s. Xano's history may not record the header consistently. Direction is still decisive
(0 of 113 401s had a token), but the mechanism is not 100% nailed.

### Nothing recent to revert — checked, this is the answer to "can we undo it?"
- ep119 `updated_at` = **2026-08-30 23:26:58**. Untouched for 17 days.
- Workspace audit log back to 2026-09-11 shows **no** `query:update` on ep119.
- Only recent related edit is **task 8 on 2026-09-13** (category buckets 10-15). It is
  additive, runs 20-30s every 2h, zero failures — it cannot produce 401s. Leave it.

So the two real levers are (a) set ep119 `auth: 0` — kills 100% of these 401s instantly but
re-opens anonymous access, i.e. reverts the Aug-30 hardening, a security call not a perf one;
or (b) the WeWeb token-wait guard, which is the correct fix and is editor-only + publish.

### 2026-09-16 — the fix pattern ALREADY WORKS, it just isn't on Discovery

Same measurement, same day, ep216 (`my_access_summary`): **195 app calls, 11 401s = 5.6%**,
and again **0 of the 11 carried a token**. Same race, but ~4.5x rarer than ep119's 24.6%.

⚠️ **NUMBER CORRECTION:** the 2026-09-13 note in [[project_tier_access_badge]] says ep216
"401s on 100% of sampled calls". **That is no longer true — it is 5.6% as of 2026-09-16.**
Something (most likely the token-wait guard publish) fixed it. Do not re-plan off the 100%.

That asymmetry is the actionable finding: **the token-wait guard demonstrably works where it
is applied, and the Discovery search collection is not covered by it.** The fix for ep119 is
to extend the existing guard to that collection — not a new mechanism, and not an ep119 change.

Corroborating user reports (Slack #tulle-users, 2026-09-16, Des + Kate): Discovery renders
**"0 vendors shown"** with the **Member** badge visible — signed in, empty grid, i.e. the 401,
not a sign-out. Des: "worked for the first 2 then back to this" (intermittent = race).
NOT REPRODUCIBLE ON DEMAND by design: it needs a cold load where ep119 fires before token
restore. Repro recipe = hard-refresh /discovery (or new private window, signed in, throttled
network) and retry; expect it ~1 in 3-4 cold loads.

### 2026-09-16 — the THREE collections that call ep119, and why they 401

Found via WeWeb `getCollections` (project d830a133-815b-419b-8efe-a0f104b3ece2). All three
pass `Auth_Token: [redacted]'f5856798-485d-47be-b433-d43d771c64e1']['accessToken']`,
which is EMPTY until the auth plugin restores. That empty header is the tokenless 401.

| collection | id | isAutoFetch | page_size |
|---|---|---|---|
| **Search Vendors** (Discovery grid) | `5c91206d-2060-4f2e-b94e-7b4605f4c423` | false | 12 |
| **Onboarding_Recommendation** | `9c28f379-2bb7-4106-877c-46beef397af5` | **TRUE** | 3 |
| **Similar_Vendors** | `26a214bf-e4e3-4228-8a02-433653e1edfa` | **TRUE** | 24 |

**The two `isAutoFetch: true` ones fire on page load, before any token exists — they are the
structural cause.** This matches the measured page_size mix exactly (12 = 597 reqs,
24 = 326, 3 = 77 of 1,000) and the 401 samples carried page_size 3 and 12.

⚠️ **There is NO MCP tool to edit a WeWeb collection** (confirms [[reference_weweb_mcp_limits]]).
`createTableIntegration`/`updateTableViewIntegration` are for WeWeb Tables, not Xano REST
collections. So the token-wait guard on these three is **EDITOR-ONLY, by hand.** Do not spend
another session trying to automate it.

### The Xano-side write channel is ALSO blocked in a sandboxed session
- `curl` -> `schannel: SEC_E_WRONG_PRINCIPAL` (TLS interception, cert hostname mismatch).
- Python -> `CERTIFICATE_VERIFY_FAILED ... not valid for 'xqtb-2ma7-ijfy.n7e.xano.io'`.
  (Python DOES have network now — the old "getaddrinfo failed" note is stale; it is TLS, not DNS.)
- `updateAPISecurity` takes ONLY a `guid` — it CANNOT flip the auth table. Dead end.
- So changing ep119 auth requires a full `updateAPI` with the entire ~53KB script inlined by
  hand. **ep119 v24 is BUILT AND VERIFIED ON DISK BUT NOT APPLIED:**
  `.claude/xano_backups/ep119_v24_public_paywall_gate_2026-09-16.xs` (diff vs v23 is exactly
  4 changes: drop `auth = "user"`; `page_size filters=min:1|max:100`; delete both the
  include_pdf_data and include_pricing db.query blocks). Pre-change backup:
  `ep119_v23_pre_auth_public_2026-09-16.xs` + `ep119_meta_pre_auth_public_2026-09-16.json`.

## The auth-off sweep broke ep230 — FIXED + VERIFIED 2026-09-18

**NEVER flip `auth` off on an endpoint that reads `$auth`.** On a Xano endpoint with auth
OFF, *any* `$auth` access throws `ERROR_CODE_ACCESS_DENIED` → a bare **403 with an empty
message** for every caller. It is not a token problem and no token can fix it, which is what
makes it read like a WeWeb/collection bug.

After ep119 went public, six more endpoints were flipped public on 2026-09-17 00:11–01:04 UTC:
ep34, ep41, ep142, ep93, ep149, and **ep230 `venue/pricing_intelligence_full`**. The first six
are stateless and are correctly public. ep230 reads `$auth.id` directly *and* via
`function.run mirror_target`, so Pricing Intelligence 403'd for **100% of traffic — paid,
unpaid, logged out — for ~22 hours**, surfacing as a 403 on the WeWeb `Pricing_Intelligence_Full`
collection. Fixed by restoring `auth = "user"`; verified live: token → 200 with
`entitled:true`, no token → 401 (not 403), OPTIONS preflight → 200, ep231 → 200.

- **ep230 can never be public.** Entitlement is computed from the caller's user row, so
  there is no caller without auth. Logged-out visitors are what ep231 (the free twin) is for.
- **The preflight-401 fear did NOT apply here** — measured: an OPTIONS carrying
  `authorization` against an authed endpoint (ep216) returns 200. Restoring auth was safe.
- Broken version saved at
  `.claude/xano_backups/ep230_v8_live_public_broken_pre_auth_restore_2026-09-18.xs`.
- Sweep to run before any future auth-off batch: for each candidate, grep its stack for
  `$auth` **and** for `function.run` targets that read `$auth` (mirror_target, imp_write_blocked).

Related: [[project_ep119_401_race]], [[project_admin_mirror_mode]], [[project_pi_panel_infographic]]
