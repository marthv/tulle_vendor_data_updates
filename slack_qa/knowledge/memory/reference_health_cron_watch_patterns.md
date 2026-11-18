---
name: reference_health_cron_watch_patterns
description: health-report-cron (daily brief DM) was killed mid-run by a push to main; now has Railway watch patterns - add any new report module to them
metadata:
  node_type: memory
  type: reference
  originSessionId: e6a0bf25-b3e3-4750-8cf2-d08a897bf751
  modified: 2026-10-09T02:51:34.120Z
---

2026-10-08: the 13:32 UTC daily brief DM never arrived. Railway logs: container started 13:35:50, finished
endpoint health 13:38:26, then "Stopping Container" 13:38:37 - a rec-engine push to main (4c11f7a, 13:36 UTC)
redeployed EVERY service on marthv/tulle_vendor_data_updates, and the redeploy killed the running cron before
the DM step. Any push to main in ~13:32-13:40 UTC used to drop that day's brief.

Fix 2026-10-09 ~02:45 UTC: health-report-cron (service aa8ee1ae, project "tulle admin dash" f13ce2ce) watchPatterns =
/perf_digest.py /bnpl_watch.py /daily_brief.py /rec_watch.py /perf_slo.json /requirements.txt /runtime.txt
(the full local import graph of perf_digest.py --health, checked by AST walk). Set on the service, not in
railway.health.toml (Railway rejects config-file paths on these services - see railway.rec.toml note).

**How to apply:** if a new local module or data file is imported by the report, ADD it to the watch patterns,
or edits to it will silently never deploy. A missed brief can't be re-run from the MCP (no "run now"); it just
runs next day. Related: [[project_geo_pricing_bnpl]] (bnpl_watch NY section lives in this report).
