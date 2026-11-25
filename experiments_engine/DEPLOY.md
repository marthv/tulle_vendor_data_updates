# Deploying experiments-engine on Railway

Spec: `experiments/FRAMEWORK.md` sections 5, 8, 9, 11. These are internal tools. Nothing here
touches rec-engine, WeWeb or the Xano endpoints and tables the app uses.

## What gets built

| Item | Value |
|---|---|
| Project | "tulle admin dash" `f13ce2ce-fa36-4539-b8c2-e573c97fa51e`, environment `production` |
| Service name | `experiments-engine` (private DNS: `experiments-engine.railway.internal`) |
| Source | `marthv/tulle_vendor_data_updates`, branch `main` |
| Root directory | `/` (the build context must hold both `experiments_engine/` and `experiments/`) |
| Builder | Dockerfile at `experiments_engine/Dockerfile`. Set it with the variable `RAILWAY_DOCKERFILE_PATH=experiments_engine/Dockerfile` |
| Watch patterns | `/experiments_engine/**`, `/experiments/**`. **Set these before the first deploy.** A service with no watch patterns redeploys on every push, the way batch-ingest does |
| Region | `europe-west4`, the same as the Streamlit dash and health-report-cron |
| Volume | mount path `/data`. Files go in `/data/experiments/`: snapshot, readouts.jsonl, alerts_sent.json, changefeed/, pdf_first_open.json |
| Replicas | 1. The scheduler runs in-process, and a volume rules out overlapping deploys anyway |
| Healthcheck | path `/health`, timeout 120 s |
| Start | `CMD` in the Dockerfile: `uvicorn experiments_engine.service:app --host :: --port $PORT --workers 1`. Leave the Railway start command empty |
| Public domain | **Only if the Railway webhook is wanted.** See "Railway webhook" below |

Why not use the root `requirements.txt` or a Railpack build: the engine needs fastapi and uvicorn.
Adding them to the root `requirements.txt` would redeploy weweb-deploy-notify-cron and
health-report-cron, plus admin-api once it is on main. With root dir `/`, Railpack would read that
root file, so the engine uses its own `experiments_engine/requirements.txt` through a Dockerfile.
The Dockerfile is **not** at the repo root, because Railway auto-detects a root `Dockerfile` for
every service built from `/` (the Streamlit dash, the crons, batch-*, tulle-slack-qa), and that
would replace their builds.

## Variables

| Variable | Value | Notes |
|---|---|---|
| `RAILWAY_DOCKERFILE_PATH` | `experiments_engine/Dockerfile` | not needed if the config file is set |
| `PORT` | `8080` | a fixed port, so the dash can call `http://experiments-engine.railway.internal:8080` |
| `EXP_API_KEY` | generate: `python -c "import secrets;print(secrets.token_urlsafe(32))"` | the dash sends it as `X-Exp-Key` |
| `EXP_HOOK_SECRET` | generate the same way, as a different value | only for `POST /hooks/railway?k=` |
| `EXP_DATA_DIR` | `/data/experiments` | |
| `XANO_METADATA_TOKEN` | `${{health-report-cron.XANO_METADATA_TOKEN}}` | the name exists on health-report-cron (recon_infra.md section 1) |
| `SLACK_BOT_TOKEN` | `${{health-report-cron.HEALTH_SLACK_BOT_TOKEN}}` | the name exists (recon section 1) |
| `SLACK_DM_CHANNEL` | `${{health-report-cron.HEALTH_SLACK_USER_ID}}` | an existing D... DM conversation id. Do NOT use the U... user id: Tulle Bot lacks `im:write` |
| `DRY_RUN` | `1` for the first day, then `0` | with `1` (the code default) messages go to the logs, not Slack |
| `DASH_URL` | `https://admin.tulletogether.app` | optional; this is the default |
| `EXP_XANO_BUDGET` | optional, default 60 | hard per-run Xano request cap (section 11) |
| `GITHUB_READ_TOKEN` | optional fine-grained PAT, read-only Contents + Metadata | without it, only the public repo is read |
| `EXP_GITHUB_REPOS` | optional, e.g. `marthv/tulletogether,marthv/tulle-search` | used only when `GITHUB_READ_TOKEN` is set |
| `EXP_DISABLE_SCHEDULER` | leave unset | `1` turns the scheduler off (debugging only) |

Do not reference anything on rec-engine.

## Steps (in this order)

Do not push or deploy between 13:30 and 13:45 UTC, while the daily brief runs.

1. Merge the `exp-framework` branch to `main`. It touches no service except the always-deploy ones
   (Tulle Admin Dash, batch-worker-cron, batch-ingest, which is a no-op while `INGEST_BATCH_ID` is
   parked) and, through `experiments/**`, tulle-slack-qa and health-report-cron if
   `experiment_log.json` changed. rec-engine should show SKIPPED.
2. Create the service **without a source** first: Railway, project "tulle admin dash", New, Empty
   Service, named `experiments-engine`. Connecting the repo at creation triggers a build right away,
   before the Dockerfile path and watch patterns are set. That build would fall back to Railpack
   with the root requirements and crash.
3. On the empty service:
   - Settings, Region: `europe-west4`.
   - Add a volume with mount path `/data`.
   - Set the variables in the table above. Start with `DRY_RUN=1`.
   - Settings, Build: builder Dockerfile, path `experiments_engine/Dockerfile` (config-as-code files are not
     accepted for new services). Watch paths: `/experiments_engine/**`, `/experiments/**`.
   - Settings, Deploy: healthcheck path `/health`, restart on failure.
   - Networking: no public domain yet (see step 6).
4. Connect the source: repo `marthv/tulle_vendor_data_updates`, branch `main`, root directory `/`.
   This starts the first deploy.
5. Verify, and give the evidence for each check:
   - The deploy log shows `Uvicorn running on http://[::]:8080`, and the healthcheck passes.
   - The first run starts at once, because there is no snapshot yet. In the logs, look for the
     refresh, then the `xano` request count in `/snapshot` → `engine.xano.requests` (must be at or
     below 60).
   - From a Railway shell on the Streamlit service:
     `curl -s -H "X-Exp-Key: $EXP_API_KEY" http://experiments-engine.railway.internal:8080/snapshot | head -c 400`
   - The change-feed notes in `/snapshot` → `change_feed.notes` show whether the Xano audit log is
     available. A 401/403/404 note means the token lacks the scope or the path
     `XANO_AUDIT_PATH=/workspace/1/audit_log` is wrong. Both are UNVERIFIED until this runs.
   - At 14:00 UTC with `DRY_RUN=1`, the log shows `DRY_RUN: would DM the owner:` followed by the
     digest. If nothing is actionable, the log says `nothing actionable, not sent` instead.
6. Optional, Railway deploy webhook (section 8). This needs a public URL, because Railway calls the
   webhook from outside the private network.
   - Networking, Generate Domain gives something like `experiments-engine-production.up.railway.app`.
     Through any non-`.railway.internal` host, the service answers only `/health` and
     `/hooks/railway`; every other path returns 404 (`EXP_PRIVATE_HOSTS` middleware), and they
     still need `X-Exp-Key` as well.
   - Project Settings, Webhooks, Add: URL
     `https://<generated-domain>/hooks/railway?k=<EXP_HOOK_SECRET>`, then "Send test". A test or any
     other non-deploy event returns `{"ok":true,"ignored":true}`.
   - Without this step, rec-engine *code* deploys are still seen through GitHub commits touching
     `rec_service/**`, but `REC_*` variable flips, which redeploy the same commit, are missed.
7. Point the dash at the service. On the Streamlit service (this redeploys it, which is internal):
   `EXP_ENGINE_URL=http://experiments-engine.railway.internal:8080` and
   `EXP_API_KEY=${{experiments-engine.EXP_API_KEY}}`.
8. After one day of clean dry-run logs, and with the owner's yes, set `DRY_RUN=0`. Alert keys
   written in dry run carry a `:dry` suffix, so that day's digest still sends once.

## Rollback

- Stop alerts at once: set `DRY_RUN=1` (this redeploys the engine only).
- Remove the service: delete `experiments-engine`. That removes the volume and its history. Then
  delete the project webhook if step 6 was done.
- The engine writes nothing outside its volume and the owner's Slack DM, so there is nothing else
  to undo.

## Load (section 11)

- Xano: one full run every 6 h, plus `POST /refresh`. Reads go through `xano.XanoReadOnly`, which
  allows only GET and search, waits at least 0.3 s between requests, and aborts the run at
  `EXP_XANO_BUDGET`. The Xano audit-log GET adds 1 call per run on top of that budget. A restart
  (including one caused by a registry push) does **not** read Xano while the snapshot is under
  6 h old. A changed registry is rescored from the cached readouts.
- WeWeb: one anonymous GET of `https://tulletogether.app/` at most every 30 min. That is about 48
  page loads a day.
- GitHub: at most 4 unauthenticated calls per 6 h run (1 conditional list plus up to 3 commit
  details).

## Watch patterns for the read lock (2026-11-25)

Tulle Bot and health-report-cron (daily brief) import `experiments_engine/readlock.py`. Their watch patterns are set on the
Railway service by hand (config files are not read there), so add, before the first push that needs them:

| Service | Add |
|---|---|
| tulle-slack-qa (Tulle Bot) | `experiments_engine/**` |
| health-report-cron | `/experiments_engine/**`, `/experiments/**`, `/ab_report.py`, `/experiments_brief.py` where missing |

What a push then redeploys: `experiments_engine/**` -> experiments-engine, Tulle Bot, health-report-cron. `experiments/**`
-> experiments-engine, health-report-cron, and Tulle Bot for `experiment_log.json`. `slack_qa/**` -> Tulle Bot. Any push to
main also redeploys services without watch patterns: Tulle Admin Dash (Streamlit), batch-worker-cron, batch-ingest.
rec-engine should show SKIPPED. Read from the repo's notes, not from Railway: check each service before pushing.
