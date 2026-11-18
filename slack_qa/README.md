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
| Channel **unmuted** | Every human message is screened: rules (looks like a question, not interpersonal) -> Haiku triage (category + confidence) -> Sonnet answer. It replies only if triage confidence >= `QA_SCAN_THRESHOLD` (0.75) **and** the answer call says the knowledge answers it with the same confidence. Otherwise silent. |
| Message from Vivek (`U05UJMCHUV6`) | Never auto-answered in scan mode; answered when he @-mentions the bot. |
| Bot posts (incl. the #deployment notifier), edits, deletes, joins, DMs | Ignored. |
| Interpersonal questions (opinions about people, blame, motives, team disagreements) or owner decisions | Silent in scan mode; on @-mention: "I only cover product changes, experiments and their outcomes ...". Facts from the notes - including customer names, emails, account ids and who said what - are allowed. |

Every answer ends with an "Automated answer from Tulle Bot ... Vivek can confirm" footer and a Sources line
(registry id, #deployment post time, commit hash, note title). Replies are capped at ~160 words and passed through
`scrub.scrub_output` (tokens, keys, passwords, webhook URLs, Stripe ids, phone numbers; Slack @-mentions are neutralised so answers never ping anyone) before posting.

## Xano reports + CSV exports (@-mention only)

`@Tulle Bot <data request>` routes (Haiku, structured output) to one named report, reads Xano **read-only**, and
posts a short summary + the CSV in the thread. Never from scan mode. Ambiguous request -> one-line question.

| Report | Data | Params |
|---|---|---|
| `payments` | table 16 (Amount = dollars, Type 1 week / 4 weeks / forever) | dates, `group_by` day/week/plan, `plan` |
| `signups_and_pay_rate` | table 1 sign-ups by week + share with a first table-16 payment within 7 days | dates |
| `planner_usage` | table 79: couples, questions (opening/refine, status ok), per couple, `counted_as_free`, kinds | dates, `group_by` day/week/surface |
| `venue_requests` | `venue_lookup_log` (looked up by name; "not available yet" if missing) | dates |
| `save_offer` | table 1 TT60-/TT20- codes + table 16 purchases after the offer (offer time = expiry - 12 h) | dates |
| `nps` | table 77 ratings: count, average, 1-5 distribution, rows with comments | dates |
| `export` | raw rows of an allowlisted table: users (1), payment_log (16), nps_response (77), rec_usage (79), venue_lookup_log | `table`, dates, `columns` |

* Internal/test accounts are excluded by default (ids 14998, 30614, 32636, 31619, 118, 5622, 35757, 2333, 34429,
  36173, 35866, 34249, 33167, 185 + `QA_INTERNAL_IDS`; emails `vivek.marthi*` and `@tulletogether.com`); say
  "include internal" to keep them. The reply says how many rows were excluded.
* Never exported or shown: `password`, `token_session`, `token_session_axpires`, `magic_link`, `google_oauth` and any
  column whose name contains token / secret / password / oauth / hash (even if asked for). Customer names and emails are
  allowed. CSV string cells also pass through the secret scrubber.
* Read-only by construction: `xano_ro.py` only calls `session.get`, and only on `/table`, `/table/{id}/schema`,
  `/table/{id}/content` (anything else raises before a request). Rows are read 500/page and cached 10 min.
* CSV capped at `QA_REPORT_MAX_ROWS` (50,000) - the reply says so if truncated. "Working on it" is posted if a report
  takes more than 5 s. `QA_DAILY_REPORTS` (30) runs per UTC day; the routing call counts toward `QA_DAILY_USD`.

Example requests:
* `@Tulle Bot payments by day for the last 2 weeks`
* `@Tulle Bot revenue by plan since Nov 1, include internal`
* `@Tulle Bot sign-ups and 7-day pay rate by week for October`
* `@Tulle Bot planner usage by surface last week`
* `@Tulle Bot how did the TT60 save offer do?`
* `@Tulle Bot export nps responses since 11-01 as csv`
* `@Tulle Bot export rec_usage for yesterday, columns user_id, kind, status, surface`

## Knowledge

* `experiments/experiment_log.json` - the registry, whole, in the prompt-cached static block (with MEMORY.md's
  "PICK UP HERE" section). Railway watch paths include it, so a registry push redeploys the bot.
* Slack, live (cached 3 min): the thread or channel being answered, #deployment's last 30 posts, and the last 15
  messages (7 days) of every other channel the bot is in.
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

The script scrubs SECRETS only (tokens, keys, passwords, webhook URLs, Stripe ids, private keys, long random strings; also phone numbers) - customer names, emails, ids and anecdotes are kept (owner 2026-11-18). It re-checks every file and refuses to replace
anything if a secret-looking string survives (exit 1). Check before committing:

```
grep -rEn 'eyJ[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]|xox[abposr]-|ghp_|hooks\.slack\.com/' slack_qa/knowledge
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
| `XANO_METADATA_TOKEN` | unset (reports off) | Xano metadata token (`${{Tulle Admin Dash.XANO_METADATA_TOKEN}}`); used GET-only. |
| `XANO_META_BASE` / `XANO_WORKSPACE_ID` | Tulle `api:meta` / `1` | Only if Xano moves. |
| `QA_DAILY_REPORTS` | `30` | Report runs per UTC day. |
| `QA_REPORT_MAX_ROWS` | `50000` | CSV row cap. |
| `QA_INTERNAL_IDS` | (none) | Extra internal user ids to exclude, comma separated. |
| `QA_DEPLOY_CHANNEL` / `QA_OWNER_USER_ID` / `SLACK_BOT_USER_ID` | `C0AML8KKF4Z` / `U05UJMCHUV6` / from event | Overrides. |

## Slack app setup (owner, by hand)

The existing **Tulle Bot** app (`A098KE08YTY`) has incoming webhooks; check whether it already has a **Bot User
OAuth Token** (OAuth & Permissions page). If not, adding the scopes below and reinstalling creates one.

1. api.slack.com/apps -> Tulle Bot -> **OAuth & Permissions** -> Bot Token Scopes: `app_mentions:read`,
   `channels:history`, `groups:history`, `chat:write`, `channels:read`, `groups:read` (list the channels the bot is
   in), `files:write` (CSV attachments for reports), `users:read` (first names in conversation context; optional).
2. **Event Subscriptions** -> ON. Request URL `https://<railway domain>/slack/events` (the service must be deployed
   with `SLACK_SIGNING_SECRET` set first, so the url_verification challenge passes). Subscribe to bot events:
   `message.channels`, `message.groups`, `app_mention`. Save.
3. **Reinstall to Workspace** (Slack prompts after scope changes). Incoming webhooks keep working.
4. Copy **Basic Information -> Signing Secret** -> Railway `SLACK_SIGNING_SECRET`; **Bot User OAuth Token** ->
   `SLACK_BOT_TOKEN`.
5. In Slack: `/invite @Tulle Bot` in #deployment and #product-development (and any other channel to cover).
6. Optional: create a fine-grained GitHub token (read-only, the 3 repos) -> Railway `GITHUB_TOKEN`.
   For reports: Railway `XANO_METADATA_TOKEN = ${{Tulle Admin Dash.XANO_METADATA_TOKEN}}`.
7. Test: `@Tulle Bot status`, then `@Tulle Bot what shipped yesterday?`. `@Tulle Bot unmute` to start scanning.

## Tests

```
cd slack_qa && python -m pytest tests -q
```
