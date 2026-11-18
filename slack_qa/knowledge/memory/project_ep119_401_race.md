---
name: ep119-401-race
description: "Discovery shows '0 vendors shown' because ~26% of ep119 calls 401 with no Authorization header; the token-wait guard is saved but unpublished, and the simpler fix is turning ep119 auth off"
metadata:
  type: project
---

**STATUS 2026-09-16: UNFINISHED. Remind at the start of every session until closed.**

## The symptom
Signed-in users get **"0 vendors shown"** and a permanently stuck **"Loading…"** on Vendor
Discovery. Reported all day 2026-09-16 by Des and Kate in #product-development (channel
`C065B5YEG1G`).

The stuck spinner is diagnostic: the page-load workflow sets `loading = true`, fetches, then
sets `loading = false`. **The fetch erroring halts the chain, so the flag never clears.**

## Measured, not guessed (2026-09-16, scoped to `Referer=tulletogether.app`)
- ep119 app calls: **26.5% 401 pre-publish, 34.7% post**; p95 1.86s and cache ~60% were both
  INSIDE budget. **Latency was never the problem — do not re-optimise the query.**
- **Every single 401 carried NO `Authorization` header.** Not one had a token.
- By collection (page_size identifies which): Search Vendors `12` = 26%,
  Similar_Vendors `24` = 41%, Onboarding_Recommendation `3` = 67%.
- Kate's IP 68.173.114.127 at **23:20:30 — same second**: `page_size=24` sent a token and got
  200, `page_size=12` sent none and got 401. The token EXISTED. Consistent with a race, but
  also consistent with that collection's header binding not being applied at all. **Unresolved.**

## The three callers (all bind `Auth_Token` to `pluginVariables['f5856798-…']['accessToken']`)
| Collection | id | isAutoFetch |
|---|---|---|
| Search Vendors | `5c91206d-2060-4f2e-b94e-7b4605f4c423` | false — fetched by the page workflow |
| Onboarding_Recommendation | `9c28f379-2bb7-4106-877c-46beef397af5` | **true** |
| Similar_Vendors | `26a214bf-e4e3-4228-8a02-433653e1edfa` | **true** |

Search Vendors is fetched by page workflow **`e5656811-32c4-4330-adac-2ee467ab24f9`
("Updated Collections", onload)** on page `0d69d71b-834d-4f2f-a13a-6d6ac66905d5`.

## What is built but NOT live
A `Fix Race Condition` custom-js token-wait (action `1c9f39ec-5f01-4716-9fec-c079d8766a0f`)
is **saved in the editor** immediately before the Search Vendors fetch. **It is NOT in
production** — verified by fetching `https://www.tulletogether.app/assets/main-B_LfEGFd.js`
and grepping: its unique strings ("Vendor Discovery fires this on page load",
"empty Auth_Token header") return 0, while the pre-existing guards' strings all return 1.
A publish from the Centurion Lounge wifi silently failed.

**VERIFY PUBLISHES THIS WAY.** Grep the live bundle for a unique string. Do not trust that
Publish worked, and do not judge a fix by a teammate's impression of the UI.

## The simpler fix the user prefers — turn ep119 auth OFF
He is right that this is stacking. **Six** client-side workarounds for this ONE root cause
are ALREADY live: `auth-early` cookie restore, `Open Channel` token wait, `Wait for the login
token`, the entire `Re-request the offer if it 401'd` workflow, `Fetch Access Tier` backoff,
`still racing - try again`. The guard would be the seventh.

ep119 already has **`cache.auth: false`** — Xano serves ONE cached response to every user
regardless of identity, so the data is NOT user-specific and auth protects nothing. Turning
it off kills the 401s for all three collections and removes the need for three guards.

Ready on disk: `.claude/xano_backups/ep119_v24_public_paywall_gate_2026-09-16.xs`
(exactly 4 edits vs live v23: drop `auth = "user"`; `page_size filters=min:1|max:100`; delete
the `include_pdf_data` and `include_pricing` db.query blocks — those are unguarded bools that
would otherwise expose table 36 + venue_pricing publicly; the app sends NEITHER, measured over
1,000 requests). Backup: `ep119_v23_pre_auth_public_2026-09-16.xs`. Applier + rollback:
`apply_ep119_v24.py --apply | --restore`. **Do NOT bump cache_v** (responses byte-identical
for real traffic; bumping causes a cold-cache stampede).

## Still unexplained — do not claim it is solved
- **`auth/login` itself returned "Unauthorized - Authentication Required"** for Kate once, then
  worked on retry. Login needs no token, so a race cannot explain it. Check whether auth got
  set at the **API-GROUP** level.
- 45 tokenless requests returned **200** (all cache misses), structurally identical to the 401s.
- The logout class (Kate "signs me out when I click around") is `auth/me`/`fetchUser`, a
  DIFFERENT endpoint — ep119 changes will not touch it. See [[project_preauth_race_signs_users_out]].

Related: [[feedback_simplify_not_stack]], [[project_endpoint_lockdown_401s]], [[project_endpoint_auth_audit]]

---

## RESOLVED 2026-09-16 23:46 UTC — and the root cause was NOT what we chased all day

**ep119 auth is OFF and v24 is live.** Verified against the live endpoint, not assumed:
`auth = "user"` absent, both corpus fetches absent, `page_size filters=min:1|max:100` present;
unauthenticated search returns 200; the paywall flags return **2.7KB instead of 75KB** on a
fresh cache key; `page_size=5000` returns 400.

### THE REAL ROOT CAUSE: Xano 401s the CORS preflight
The collections send a custom `Auth_Token` header, which forces the browser to send an
**OPTIONS preflight first. A preflight carries NO Authorization header — by specification.**
Xano saw no token on an auth-required endpoint and returned 401, so **the browser blocked the
real request entirely.** That is why the app saw `ERR_NETWORK` with an empty `request:{}` and
NO `response` field, rather than a 401 it could handle. Measured: **43 of 53 preflights (81%)
failed.** They are rare in logs only because a success is cached 24h
(`Access-Control-Max-Age: 86400`) — which is exactly the "weirdly inconsistent" behaviour.

**NO CLIENT-SIDE TOKEN-WAIT CAN EVER FIX THIS.** You cannot make a preflight carry a token.
The `Fix Race Condition` guard was the wrong shape of fix and measurably changed nothing
(Search Vendors 401 rate: 26% before publish, 26% after).

### Two verification lessons that cost hours
1. **A WeWeb publish can silently fail.** One published from an intercepting network never
   reached prod. Verify by fetching the live bundle and grepping for a unique string.
2. **PAGE workflows are NOT in `main.js` NOR in the per-page chunk** — only APP workflows are
   inlined there. Grepping main.js for a page workflow proves nothing. This invalidated one of
   my own conclusions mid-session; check an existing page workflow first as a control.

### Still open
- **DELETE the `Fix Race Condition` action** (`1c9f39ec-5f01-4716-9fec-c079d8766a0f` in page
  workflow `e5656811-32c4-4330-adac-2ee467ab24f9`). With auth off it is not merely useless —
  a visitor with no token waits the **full 5 seconds** before the grid fetches.
- The search grid is now enumerable at 100/page unauthenticated (names, locations, summary
  price chip). User accepted this; the paid corpus is NOT exposed.
- `auth/login` 401'd once for Kate — unexplained, possibly API-GROUP-level auth. Unresolved.
