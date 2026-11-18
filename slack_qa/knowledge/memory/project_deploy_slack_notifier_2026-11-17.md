---
name: project-deploy-slack-notifier-2026-11-17
description: "WeWeb staging publish -> #deployment Slack note (AI-written diff vs previous staging build); built + committed locally 279ab16, not pushed/deployed"
metadata:
  node_type: memory
  type: project
  originSessionId: a03a31fa-d97b-4e0c-8697-0f5cdbf6e87b
  modified: 2026-11-18T04:10:59.886Z
---

Owner 2026-11-17: every WeWeb publish to STAGING should post a short "what changed vs the previous push" note to Slack #deployment. Owner chose: AI-written summary (Sonnet 5.5), staging only (not production).

- Code: tulle_vendor_data_updates worktree `C:/Users/vivek/Documents/GitHub/tulle_ww_deploy_notify`, branch `ww-deploy-notify`, commit 279ab16 (off origin/main). Files `weweb_deploy_notify.py`, `railway.wwdeploy.toml` (cron */5).
- Staging host: `d830a133-815b-419b-8efe-a0f104b3ece2-staging.weweb.io` (WeWeb designId d830a133...). No staging.tulletogether.app. 24 pages. Build id = `_wwcv` + entry-client hash in home HTML.
- Published /data/<pageId>.json has wwObjects, workflows, variables, formulas, collections; workflows carry NO names (described by step types). Page list = `pages:[{id:...,name:...,paths:{default:...}}]` in main-*.js.
- Verified offline only: same build twice -> "no differences"; planted changes -> correct plain + AI message. NEVER run on a real staging publish yet.
- **LIVE 2026-11-18 04:10 UTC** (owner sent webhook = go): pushed main 279ab16; Railway project "tulle admin dash", service `weweb-deploy-notify-cron` (acd01b2d-4c86-4a8a-875c-19541995c070), cron */5, europe-west4, volume weweb-deploy-snapshots at /data, vars DEPLOY_SLACK_WEBHOOK_URL, ANTHROPIC_API_KEY=${{health-report-cron.ANTHROPIC_API_KEY}}, SNAPSHOT_DIR=/data, MISE_PYTHON_GITHUB_ATTESTATIONS=false (without it the Railpack build fails on python@3.11.5 attestations - needed on every new python service). First run saved baseline build 1400. Webhook validated (empty POST -> invalid_payload, no message sent). NOT yet seen: a real post on a staging publish.
- Original go-live plan: Slack incoming webhook for #deployment (Tulle workspace, not Healthie), push to main, Railway cron service with Config File railway.wwdeploy.toml + volume at /data + DEPLOY_SLACK_WEBHOOK_URL + ANTHROPIC_API_KEY, run once (saves baseline, posts nothing), then confirm the first real staging publish posts.
- Local DNS on this machine fails for weweb hosts; test with scratchpad wrapper that pins the IP (not in prod code).
