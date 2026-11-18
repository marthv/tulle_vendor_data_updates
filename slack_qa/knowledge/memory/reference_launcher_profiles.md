---
name: reference_launcher_profiles
description: Desktop personal.bat vs healthie.bat - separate Claude profiles (CLAUDE_CONFIG_DIR) + per-window GH_TOKEN; Railway is one global login
metadata:
  node_type: memory
  type: reference
  originSessionId: 2c0addd5-f1e3-49c4-a905-b40524fb1798
  modified: 2026-11-04T03:50:11.998Z
---

Set up 2026-11-03 so the owner never has to switch accounts by hand.

- `Desktop\personal.bat` (Tulle): clears CLAUDE_CONFIG_DIR -> default `~\.claude` (this memory, Xano/WeWeb/Mixpanel/Stripe, Slack plugin should be the TULLE workspace). Sets `GH_TOKEN` = `gh auth token -u marthv` for the window. Checks `railway whoami` = [email] and warns otherwise.
- `Desktop\healthie.bat` -> `healthie-launcher.ps1`: `CLAUDE_CONFIG_DIR=~\.claude-healthie` (own Slack = Healthie workspace, Notion, Healthie project memory copied 11-03), `GH_TOKEN` for vmarthi17, Anthropic API key (plaintext in the bat - owner told to rotate/move).
- Global `gh auth switch` state no longer matters; don't run `gh auth switch` to fix pushes - check GH_TOKEN instead.
- Railway CLI holds ONE login (`~\.railway\config.json`, OAuth accessToken, no `token`). `railway login` fails under `!` (non-interactive) -> owner runs it in a normal terminal. `railway logs` prints nothing non-interactively; use GraphQL `httpLogs`/`deploymentLogs` with the accessToken (read via Python). `railway delete --project <id>` said "not found" -> used GraphQL `projectDelete`.
- Launcher backups: `~\.claude\backups\launchers_pre_2026-11-03\`.

Railway projects (11-03): tulle search, tulle slackbot, tulle vendor portal (the live portal), tulle admin dash. `vendor_portal` (old Flask portal from repo `vendor-portal/`, 0 external requests in 7-day log window) DELETED 11-03; config backup `~\.claude\backups\railway_vendor_portal_pre_delete_2026-11-03\`. See [[reference_slack_connector_wrong_workspace]] [[project_vendor_portal]].
