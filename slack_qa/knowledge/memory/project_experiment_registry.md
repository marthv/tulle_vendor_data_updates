---
name: project_experiment_registry
description: "Experiment registry LIVE 2026-10-25: tulle_vendor_data_updates/experiments/experiment_log.json + Experiments tab in the admin dash; record every experiment launch/read there and push to main"
metadata:
  type: project
---

Owner 10-25: many sequential experiments coming, mostly launched via Claude Code; wants goal, dates, results, themes,
confounds, stat-sig in one place. Built + pushed main 7b9a167 (tulle_vendor_data_updates; worktree ../tulle_dash_exp,
branch dash-experiments): `experiments_tab.py` (🧪 Experiments tab, 2nd tab in dashboard.py) reads
`experiments/experiment_log.json` - no network. UI kept simple at owner's request: Running/Past/All switch, timeline
(bars + dashed shipped-change lines), one table w/ latest result, detail expanders (auto-flagged overlapping
experiments/changes, results, "Check significance" two-proportion z-test), "What we've learned" collapsed.
Seeded 18 entries: 9 running/planned + 9 historical (only outcomes recorded at the time).

**How to apply:** whenever an experiment launches, is read, concludes or a confounding change ships -> edit the JSON
(experiments[] or changes[]; results[].counts {control:{n,conv},treatment:{n,conv}} enables the z-test), push to main
(avoid ~13:32-13:40 UTC: daily brief cron). Dash service has NO watch patterns -> redeploys on any push. Dash runs in
europe-west4 (likely cause of slowness complaints - backburner). The older copy at tulletogether/.claude/experiments/ is
superseded. [[project_backburner_2026-10-25]] [[feedback_evidence_and_intention]]
