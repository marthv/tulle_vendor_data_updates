# Tulle Bot - Slack Q&A (`slack_qa/`)

An always-on FastAPI service that answers questions about **recent changes** (deploys, WeWeb publishes,
experiments, commits, what is on/off) in the Slack channels Tulle Bot is invited to. It is **read-only**:
it posts replies in threads and never changes code, settings or data.

## How it behaves

| Situation | What it does |
|---|---|
| `@Tulle Bot <question>` | Always replies in thread. If the knowledge doesn't cover it: "I don't have that ... Vivek can confirm." |
| `@Tulle Bot mute` / `unmute` / `status` / `help` | Per-channel setting, saved on the volume. |
| Channel **muted** (default) | Only @-mentions are answered. |
| Channel **unmuted** | Every human message is screened: rules (looks like a question, not personal) -> Haiku triage (category + confidence) -> Sonnet answer. It replies only if triage confidence >= `QA_SCAN_THRESHOLD` (0.75) **and** the answer call says the knowledge answers it with the same confidence. Otherwise silent. |
| Message from Vivek (`U05UJMCHUV6`) | Never auto-answered in scan mode; answered when he @-mentions the bot. |
| Bot posts (incl. the #deployment notifier), edits, deletes, joins, DMs | Ignored. |
| Personal / interpersonal / owner-decision questions | Silent in scan mode; on @-mention: "I only cover product changes, experiments and their outcomes". |

Every answer ends with an "Automated answer from Tulle Bot ... Vivek can confirm" footer and a Sources line
(registry id, #deployment post time, commit hash, note title). Replies are capped at ~160 words and passed through
`scrub.scrub_output` (tokens, keys, webhook URLs, emails, phone numbers, user ids, @-mentions) before posting.

## Knowledge

* `experiments/experiment_log.json` - the registry, whole, in the prompt-cached static block (with MEMORY.md's
  "PICK UP HERE" section). Railway watch paths include it, so a registry push redeploys the bot.
* Slack, live (cached 3 min): the thread or channel being answered, #deployment's last 30 posts, and the last 15
  messages (7 days) of every other channel the bot is in, minus personal lines.
* GitHub, live when `GITHUB_TOKEN` is set (cached 5 min): last 14 days of commits on the default branch of
  `marthv/tulle_vendor_data_updates`, `marthv/ww-price-report`, `marthv/ww-planner-chat`, plus the file list/stat of
  up to 3 commits the question names or clearly matches. Without the token: the synced commit logs.
* `slack_qa/knowledge/` (synced, committed): scrubbed Claude Code memory notes (`memory/*.md`) and 60-day commit
  logs (`commits_*.md`). The notes are ~1.4 MB, so each question gets the best-matching chunks by BM25 (~24 KB).

### Refreshing the synced knowledge (local machine)

```
python slack_qa/sync_knowledge.py
git add slack_qa/knowledge && git commit -m "chore(slack-qa): refresh knowledge" && git push
```

The script drops personal/interpersonal lines, scrubs secrets/PII, re-checks every file, and refuses to replace
anything if a secret-looking string survives (exit 1). Check before committing:

```
grep -rEn '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}|eyJ[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]|xox[abposr]-|ghp_|hooks\.slack\.com/' slack_qa/knowledge
```

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `SLACK_SIGNING_SECRET` | - (required) | Verifies every request; bad/old (>5 min) signatures get 401. |
| `SLACK_BOT_TOKEN` | - (required) | `xoxb-` token for chat.postMessage and history reads. |
| `ANTHROPIC_API_KEY` | - (required) | Claude key (a separate key makes the spend visible). |
| `STATE_DIR` | `/data`, else `slack_qa/.state` | Where `slack_qa_state.json` (mute settings, spend ledger) lives. |
| `QA_ENABLED` | `1` | `0` = kill switch: Slack is acked, nothing else happens. |
| `QA_DEFAULT_MUTED` | `1` | Channels with no explicit setting answer @-mentions only. |
| `QA_DAILY_USD` | `2` | Estimated Claude spend cap per UTC day (scan goes silent; mentions get a "cap reached" note). |
| `QA_SCAN_THRESHOLD` | `0.75` | Minimum confidence to answer unprompted. |
| `QA_CHANNEL_HOURLY` | `6` | Max replies per channel per rolling hour. |
| `QA_MODEL` / `QA_TRIAGE_MODEL` / `QA_EFFORT` | `claude-sonnet-5-5` / `claude-haiku-5-5` / `low` | Models and answer effort. |
| `GITHUB_TOKEN` | unset | Fine-grained, read-only (Contents: read, Metadata: read) on the 3 repos. |
| `QA_GITHUB_REPOS` | the 3 repos above | Comma list `owner/repo`. |
| `QA_USER_NAMES` | `U05UJMCHUV6=Vivek` | `Uxxx=Name,...` fallback if `users:read` is not granted. |
| `QA_DEPLOY_CHANNEL` / `QA_OWNER_USER_ID` / `SLACK_BOT_USER_ID` | `C0AML8KKF4Z` / `U05UJMCHUV6` / from event | Overrides. |

## Slack app setup (owner, by hand)

The existing **Tulle Bot** app (`A098KE08YTY`) has incoming webhooks; check whether it already has a **Bot User
OAuth Token** (OAuth & Permissions page). If not, adding the scopes below and reinstalling creates one.

1. api.slack.com/apps -> Tulle Bot -> **OAuth & Permissions** -> Bot Token Scopes: `app_mentions:read`,
   `channels:history`, `groups:history`, `chat:write`, `channels:read`, `groups:read` (list the channels the bot is
   in), `users:read` (first names in conversation context; optional).
2. **Event Subscriptions** -> ON. Request URL `https://<railway domain>/slack/events` (the service must be deployed
   with `SLACK_SIGNING_SECRET` set first, so the url_verification challenge passes). Subscribe to bot events:
   `message.channels`, `message.groups`, `app_mention`. Save.
3. **Reinstall to Workspace** (Slack prompts after scope changes). Incoming webhooks keep working.
4. Copy **Basic Information -> Signing Secret** -> Railway `SLACK_SIGNING_SECRET`; **Bot User OAuth Token** ->
   `SLACK_BOT_TOKEN`.
5. In Slack: `/invite @Tulle Bot` in #deployment and #product-development (and any other channel to cover).
6. Optional: create a fine-grained GitHub token (read-only, the 3 repos) -> Railway `GITHUB_TOKEN`.
7. Test: `@Tulle Bot status`, then `@Tulle Bot what shipped yesterday?`. `@Tulle Bot unmute` to start scanning.

## Tests

```
cd slack_qa && python -m pytest tests -q
```
