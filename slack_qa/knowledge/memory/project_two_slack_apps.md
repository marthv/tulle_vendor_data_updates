---
name: project_two_slack_apps
description: "TWO Slack apps exist — Tulle Ops (Railway bot) and Tulle Bot (incoming webhooks for Xano notifications). Never paste feedback_bot's manifest into Tulle Bot."
metadata: 
  node_type: memory
  type: project
  originSessionId: 2e73b80a-1c03-43cb-9246-20ab0885c66a
  modified: 2026-09-07T02:21:23.006Z
---

The Slack workspace has **two apps that look almost identical in the app picker**, and
pasting the wrong manifest into the wrong one is the failure mode to guard against.

| app | job | manifest source |
|---|---|---|
| **Tulle Ops** | the Railway bot — 7 slash commands, reaction hide/unhide, Approve/Discard buttons | `marthv/feedback_bot` `manifest.yml` |
| **Tulle Bot** (`A098KE08YTY`) | **incoming webhooks only** — Xano endpoints POST to `[webhook-url]` to announce events | hand-configured, NOT in any repo |

**Tell them apart by App ID, never by name.** The ID is in the config URL:
`app.slack.com/app-settings/T05U8AWH14Z/<APP_ID>/…`. Tulle Bot is `A098KE08YTY`;
Tulle Ops's ID is not recorded yet — grab it and put both in `feedback_bot/TESTING.md`.

**Why they are confusable:** every commit of `feedback_bot/manifest.yml` sets
`display_information.name: Tulle Ops`, so an app created from it at any point is named
"Tulle Ops". Tulle Bot's bot-user display name also got renamed to "Tulle Ops" by an
accidental paste on 2026-09-06 — so scope descriptions on Tulle Bot read "Send messages as
@Tulle Ops" while the app itself is still Tulle Bot.

**Why:** `feedback_bot`'s manifest declares no `incoming-webhook` scope. Applying it to
Tulle Bot and then reinstalling would revoke the webhook grant and silently kill every
Xano → Slack notification. Slack's yellow "you've changed the permission scopes, please
reinstall your app" banner actively invites exactly that.

**How to apply:**
- **Never paste `feedback_bot/manifest.yml` into Tulle Bot.** It belongs to Tulle Ops only.
- A manifest save changes only *declared* scopes; the installed grant survives until a
  **reinstall**. So the banner means the damage is staged, not done — fix the scope list
  before reinstalling and nothing breaks. Declared == granted makes the banner clear on
  its own, no reinstall needed.
- Tulle Bot's granted scopes (verified 2026-09-06): `app_mentions:read`, `channels:history`,
  `chat:write`, `groups:history`, **`incoming-webhook`**, `reactions:read`,
  `reactions:write`. No `commands` — so slash commands sitting on Tulle Bot are inert and
  cannot collide with Tulle Ops.
- Read the real granted list without guessing:
  `curl -sD - -o /dev/null -H "Authorization: [redacted] <xoxb>" https://slack.com/api/auth.test | grep -i '^x-oauth-scopes'`
  or workspace admin → Manage apps → the app.
- To tell which app a running service is bound to, compare its `SLACK_SIGNING_SECRET`
  against each app's Basic Information → Signing Secret. Unique per app.
- A reinstall may mint a **new** webhook URL rather than preserve the old one — record the
  existing URLs first, since Xano may have them hardcoded.

**RESOLVED 2026-09-06.** Tulle Bot's manifest was restored by hand (webhook-only: the 7
scopes above, no slash_commands, no event_subscriptions, no interactivity, names back to
"Tulle Bot"). Webhooks confirmed still firing by the user. The yellow scope banner does
**NOT** clear when declared and granted scopes match — it is a dismissible notice with an
×, not a live diff. Dismissing it is safe and is the right move, since it removes the
reinstall trap.

**Webhook URLs live in exactly TWO Xano env vars**, not scattered across endpoints:
`$env.slack_webhook_url` (fn4 `slack_webhook`) and `$env.slack_webhook_url_feedback`
(fn9 `slack_feedback_webhook`). Both functions declare a `url` input and then **ignore
it**. So rotating the exposed webhooks = 2 variable edits. Neither function inspects
`$slack.response`, and `api.request` does not throw on 4xx, so **a revoked webhook fails
completely silently** — the smoke test in TESTING.md is the only detection. Both also run
with `verify_host`/`verify_peer` = false. See [[reference_xanoscript_traps]].

**Open, deferred to a later session:** set `ALLOWED_CHANNEL_IDS` on the Railway service
(today anyone in any channel can hide a vendor); run TESTING.md tests 5 and 9 (the event
and interactivity paths, still unexercised); record both App IDs; optionally harden
fn4/fn9. User called the "upgrade path" as the next session's topic.

**Root cause of the 2026-09-06 incident:** a stale `tulle-slackbot.zip` sat in the repo root
containing an OLD manifest (no `commands` scope, `request_url: https://YOUR-APP.up.railway.app`).
Deleted 2026-09-06. If a zip export is ever recreated, do not paste its manifest — the repo
working tree is the source of truth.

Related: [[project_slackbot_vendor_visibility]], [[feedback_weweb_component_deploy]].
