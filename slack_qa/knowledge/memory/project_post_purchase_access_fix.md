---
name: project_post_purchase_access_fix
description: Fix for paid users still seeing locked PDFs — decided approach is WeWeb-side auth/me re-fetch; no Xano change
metadata: 
  node_type: memory
  type: project
  originSessionId: 3c867b82-8eeb-4100-b7f9-5d1396fbd6c5
  modified: 2026-07-27T16:20:25.157Z
---

Post-purchase "paid but still locked" bug (raised 2026-07-22). Root cause and fix
decided; user is implementing in WeWeb themselves.

**Root cause:** staleness is 100% WeWeb-side. `auth/me` (ep 3) reads access live and is
uncached; the auth token carries no access (`extras={}`). WeWeb fetches the user object
once at login into a variable and never re-fetches after purchase. See
[[reference_access_entitlement_model]].

**Approved fix (WeWeb only, NO Xano change):**
1. New `/purchase-complete` route (Stripe `success_url`, frontend-controlled via ep 26
   `sessions` call) → on mount re-call `auth/me`, overwrite the login user variable, then
   route to Vendor Discovery.
2. Re-fetch `auth/me` on gated-page mount (Vendor Discovery, vendor detail, Membership) —
   covers other-tab / returning users. Guard against WeWeb double-fire (once per mount).
3. 401 branch on `auth/me` → clear state + route to login (this is C — keep 24h token,
   just handle expiry; do NOT force re-login otherwise).
4. Ensure WeWeb `auth/me` binding is NOT "fetch once"/cached, else re-fetch no-ops.

**2026-07-27 MEASURED UPDATE (Becky Schmitt, user 22617) — the mechanism is a pre-auth
race, not staleness.** Live repro captured before anyone touched her access:
- DB row AND live `auth/me` (called with her token) both return
  `date_until_access:"3000-01-01"` + `forever_access_purchased:true`. Endpoint is honest.
- Ruled out empirically: endpoint caching (`auth/me` cache.active=false; workspace has
  **zero middlewares**) and incomplete webhook write (both fields set).
- **Real finding:** of 400 consecutive `auth/me` calls (2026-07-27 13:22–16:16 UTC),
  **62 were 401 — and 56 of those 62 carried NO `Authorization` header at all.** WeWeb
  fires `auth/me` unauthenticated on page load. At 15:51:20 UTC her iPhone produced a
  200 (with token) *and two 401s (no token) in the same second*. She reported at 15:57
  UTC; iPhone no-token 401s at 15:57:57 / 15:59:00 / 15:59:32.
- So the Membership page consumes whichever answer wins the race → plan picker. Explains
  the randomness, why it hit a *fresh* device login, and why cache-busting is a no-op.
- Also: every token minter (`auth/login` ep1, `auth/signup` ep2, `auth/magic-login` ep18)
  uses `expiration = 86400` (24h). 6 of the 62 401s DID carry a token = [redacted] sessions.
  Becky paid 2026-07-23 04:17 UTC, reported 07-27 → 4.5 days, far past 24h.
- Ticket premise "gap was 11.5 hours" was wrong; payment log id 1989 = 2026-07-23 04:17 UTC.
- Possible double-purchase symptom in payment log: John Lee ($30 then $119, 100s apart),
  Cassandra Hendon ($30 then $119, 216s apart) — users re-buying when access doesn't show.

Rejected: forced re-login every purchase (friction, token isn't the problem). Parked:
extending token TTL; realtime subscribe to `user_updates` channel (webhook already
publishes it) as instant-update enhancement.

**D parked (not being worked):** read-only payment-vs-access audit; webhook ep30
silent-fail hardening (bad `metadata.weeks`/null `client_reference_id`);
`forever_access_purchased` anomaly — single-payment 4-week buyers with flag TRUE (users
23879, 23866) matters because that bool is the only field that could gate the filter tier.

Plan file: `.claude/plans/ok-continue-dont-ethereal-widget.md`.
