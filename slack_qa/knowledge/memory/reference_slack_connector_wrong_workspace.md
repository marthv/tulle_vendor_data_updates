---
name: reference_slack_connector_wrong_workspace
description: "Slack connector was signed into Healthie (not Tulle) until the owner re-authed 2026-10-17 via /mcp; Tulle channel C07D9U0FZF0 now works. If channel_not_found recurs, it has drifted back - never post Tulle data into Healthie"
metadata:
  node_type: memory
  type: reference
  originSessionId: d461cc1d-f540-4ec1-bcdd-000d8a6a23c5
  modified: 2026-10-19T01:49:16.323Z
---

Checked 2026-10-17: slack_list_user_channels shows Healthie channels (#h-general, #team-rnd...). U05UJMCHUV6 DMs and Tulle channel C07D9U0FZF0 return channel_not_found.
**How to apply:** don't promise Slack DMs/posts for Tulle until the connector is reconnected to the Tulle workspace or a Tulle incoming-webhook URL is provided. Never post Tulle data into the Healthie workspace. Scheduled checks that "DM the user" will fail the same way.
Related: [[project_two_slack_apps]]

**2026-10-17 later:** owner ran /mcp, re-authenticated; post to Tulle channel **C07D9U0FZF0** succeeded (owner said 'post here'). Use C07D9U0FZF0 for Tulle updates; U05UJMCHUV6 DMs were the failing target.
**2026-10-19 owner: DMs I send via the connector come FROM the owner's own account -> NO notifications.** Automated/recurring
updates must be sent by the **Tulle Ops** bot (token = [redacted] var REC_SLACK_BOT_TOKEN on rec-engine) via conversations.open U05UJMCHUV6.
**Owner preference 2026-10-17: send Tulle status updates as a DM to the owner (U05UJMCHUV6, DM channel D05TSPS8J4D) - NOT to C07D9U0FZF0.** DM verified working after re-auth. Tulle Ops app is the other acceptable sender (token only in Railway; don't pull it locally).

**2026-11-03:** root cause of the drift = personal.bat and healthie.bat shared one ~/.claude, so the Slack plugin login was shared. Fixed with separate profiles ([[reference_launcher_profiles]]); this profile re-authed to the Tulle workspace and verified (profile shows "Tulle", vivek.marthi@gmail.com).
