---
name: project-admin-view-as
description: "Admin 'view as user' from Slack. The in-browser half was ABANDONED (WeWeb router race, unwinnable); /tulle view-as now returns a server-side SNAPSHOT instead. ep297 mint is disabled."
metadata: 
  node_type: memory
  type: project
  originSessionId: 48f611b8-9c31-47c9-a494-e7c30f6efa52
  modified: 2026-09-10T06:04:49.112Z
---

Support could not answer "what is this customer actually seeing?" — the old `impersonate`
endpoint was deleted in the 2026-08-30 audit and passwords are hashed. Rebuilt narrow:
`/tulle view-as <email>` returns an ephemeral single-use link that loads the app as that
customer, read-only, every use logged to `admin_audit` (table 72).

**Built and verified 2026-09-10 (Xano):** `fn imp_write_blocked` (fn 67), `impersonation_session`
(table 76), `POST admin/impersonate/mint` (ep297, slack group, its own secret), `POST
admin/impersonate/exchange` (ep298, **in the WeWeb group so browser CORS works**), and write
guards on **ten** endpoints — ep3, ep150 (both writes), ep109, ep21 (+realtime), ep145, ep158
silent-skip; ep285, ep212, ep72, ep20 hard-block. Backups:
`.claude/xano_backups/ep<N>_pre_impguard_2026-09-10.xs`.

**THE KILL SWITCH IS ON — the feature is LIVE as of 2026-09-10 06:15 UTC** (ep297 v4,
`$enabled = true`), flipped only after the bot deployed. Set it to `false` to kill the whole
feature instantly. Setting it false disables the whole feature end to end with no deploy and no
publish, because the frontend is inert without a valid nonce — this is the rollback for everything.

**Slack half SHIPPED 2026-09-10:** merged to `main` and pushed (`c48726b`); Railway builds
`marthv/feedback_bot` **main**, so a feature branch alone would NOT have deployed. Railway env
vars `IMPERSONATE_USER_IDS` + `XANO_IMPERSONATE_KEY` were set by the user and applied. `IMPERSONATE_USER_IDS` is **deny-on-empty**, deliberately inverting the
empty-means-allow convention of `ALLOWED_USER_IDS` two lines above it — do not "fix" it. Admins:
`U05UJMCHUV6,U06T0645VTM,U05U8AYKDFT`. Secret env var `XANO_IMPERSONATE_KEY`, separate from
`XANO_API_KEY` on purpose. `call()` in `lib/edits.js` now takes the secret as a parameter; the
default is unchanged so existing callers are untouched.

**Deployment order was: Railway env vars → merge+push main → flip `$enabled` to true.**

⚠️ **`ALLOWED_USER_IDS` and `ALLOWED_CHANNEL_IDS` are STILL UNSET** (confirmed via Railway
`variableNames` 2026-09-10), so every existing `/tulle` vendor command remains runnable by
anyone in any channel the bot is in. Separate from view-as, still open.

**WeWeb is BUILT (2026-09-10) and sitting in the draft, awaiting the user's publish.** App
workflow `Admin View-As Session` `a7c3f2e8-5b91-4d06-8e34-9f1c7b2d4a60` (the earlier id `d4e91b07-…` was deleted after it ended up with THREE live duplicates - see [[reference_weweb_mcp_limits]]), `onload-app`, one
`custom-js` action, no plugin actions so it stays MCP-editable. Two entry paths: `?view_as=`
exchanges the nonce then **reloads once** so the resume path can set the token synchronously
ahead of the other onload-app workflows; the stored marker resumes on every later load — without
that path a refresh strips the param and the banner vanishes while the admin is still browsing as
the customer. Token is held in `localStorage` (15 min) so resume works — hence the "use a private
window" line in the Slack reply. Exit clears the marker, nulls the token, calls plugin logout,
removes only keys/cookies containing the auth plugin id, redirects to `/sign-in`.
The duplicated `Fetch Access Tier` (`7e2a91c4-…`, two bodies under one UID) was left untouched.

**There is no app-publish MCP tool — the user publishes in the editor.**

Language traps this cost are recorded in [[reference_xanoscript_traps]]. Related:
[[project_endpoint_auth_audit]], [[reference_weweb_mcp_limits]], [[project_ep20_delete_any_account]].

---

## RESOLVED DIFFERENTLY 2026-09-11 — the browser half is dead, the feature ships as a snapshot

**The in-browser session was abandoned after five attempts, and it is not a fixable bug.** The
impersonation chain always worked: with a minted token `auth/me`, `/upgrade_offer` and
`my_access_summary` all returned 200 as the target. The deciding diagnostic was taken at boot,
before any workflow ran: `cookieAtEntry: true`, `tokenAtEntry: true`, `verify ok: true` — and
`title: "Tulle Together: Sign In"`. **WeWeb's router evaluates page access once at boot, before
the auth plugin's async restore resolves, and nothing re-asks it.** A re-navigation re-runs the
same race. No `onload-app` workflow can win it. Do not retry this.

**What shipped instead:** `ep299 GET admin/customer_snapshot` (group 3, secret-gated, read-only,
mints nothing). `/tulle view-as <email>` now replies with tier, days left, whether Pricing
Intelligence is unlocked (`total_amount_paid >= 50`), free PDF views left, activity counts, and
exactly what the upgrade banner would offer — computed by the SAME `fn compute_upgrade_offer`
the live app calls, so the quoted credit cannot drift. Strictly more useful for support than the
screen: it shows the *cause* ("PI needs $50+, they've paid $30"), not the symptom. Shipped in
`feedback_bot` commit `537bb3b`, Railway deploy `cd6e2fb5` SUCCESS 2026-09-11 08:17 UTC.

**ep297 mint is now DISABLED** (v5, `$enabled = false`, verified returning "Admin view-as is
currently disabled"). It has no caller, and minting real customer auth tokens with no consumer
is pure attack surface. ep298 exchange is inert without a nonce.

**The ten write guards STAY.** They are no-ops while no impersonated token can exist, and
[[project_ep20_delete_any_account]] — the real vulnerability that scan found — depends on ep20's
fix, which is independent of all this.

**The two WeWeb workflows are inert, not deleted:** `Admin View-As Session`
`c9e21a54-8b07-4f36-a1d2-73b6e0c94f18` and `Auth Token Early Restore`
`f1a6b93d-47c2-4e58-b0d1-2c9a7e5f8033`. Safe to delete at the next publish. The `Open Channel`
token-wait JS the user added by hand DID work and should be kept.
