# Tulle Bot v2: deploy notes (owner decisions 2026-11-20)

Nothing here is deployed yet. These values go into Railway variables on **tulle-slack-qa** at deploy time; they are
deliberately NOT code defaults. Each step needs the owner's yes.

## Values (looked up 2026-11-20, read-only)

| What | Value | Source |
|---|---|---|
| Approvers (owner, Des, Kate) | `QA_APPROVERS=U05UJMCHUV6,U05U8AYKDFT,U06T0645VTM` | Slack user search in the Tulle workspace: owner U05UJMCHUV6, "Des" U05U8AYKDFT, "Kate" U06T0645VTM |
| Display names (fallback if users:read is missing) | `QA_USER_NAMES=U05UJMCHUV6=Vivek,U05U8AYKDFT=Des,U06T0645VTM=Kate` | same |
| Dedupe cards channel | `DEDUPE_CHANNEL=C06UB2V74GG` (#growth-strategy, private) | Slack channel search |
| #feedback watch | `FEEDBACK_CHANNEL=C07JML6NMLM` (#feedback, private), `FEEDBACK_WATCH=1` | Slack channel search |
| Scheduler | `QA_SCHEDULER=1` (nightly/weekly dedupe 08:00 UTC; roll-up only if `ROLLUP_ENABLED=1`) | |
| Dry runs (leave ON at first) | `DEDUPE_DRY_RUN`, `VENDOR_EDIT_DRY_RUN`, `TASK_DRY_RUN` unset (= dry run; only 0/false/no/off turns one off) | |
| Interactivity Request URL | `https://tulle-slack-qa-production.up.railway.app/slack/interactions` | Railway list-domains, service b4a51195-5da2-4575-a842-b1f335142b26 |
| Events Request URL (unchanged) | `https://tulle-slack-qa-production.up.railway.app/slack/events` | same |
| Engine answers | `EXP_ENGINE_URL=http://experiments-engine.railway.internal:8080`, `EXP_API_KEY=${{experiments-engine.EXP_API_KEY}}` (reference; never copy the value) | |

Cards never name people: the footer reads "Proposed by Tulle Bot. Only approvers can apply this." and a click by a
non-approver gets "Only approvers can apply or reject Tulle Bot changes".

## Slack app (owner, by hand)
1. Interactivity & Shortcuts: ON, Request URL above.
2. `/invite @Tulle Bot` in **#growth-strategy** and **#feedback** (both private; the #feedback webhook doesn't make the
   bot a member). Without this, card posts fail with `not_in_channel` (they're marked post_failed, not lost).
3. App Home: Messages Tab ON (roll-up drafts to the owner's DM).

## Order
1. Push `tulle-bot-v2` to main (avoid 13:30-13:45 UTC; a push also redeploys batch-ingest, batch-worker-cron and
   health-report-cron, which have no watch patterns).
2. Slack app steps 1-3.
3. Set the variables above (dry runs stay on). Check `/health`, then `@Tulle Bot how's the save offer experiment doing?`.
4. `@Tulle Bot scan duplicates` (an approver) -> cards in #growth-strategy; hand-check 50 (approving is a dry run).
5. Watch #feedback for proposed fixes for a few days before any dry-run flag is turned off.
6. Merges stay dry until the Xano pieces in `XANO_MERGE_PROPOSAL.md` are approved and built.
