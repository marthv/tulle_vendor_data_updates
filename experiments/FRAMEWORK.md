# Tulle experimentation framework (v2)

Owner brief, 2026-11-19: every experiment is recorded in the Tulle Admin Dash and updated
automatically; the dash is uncluttered; it says when an experiment is going stale and should
be shelved; best-in-class reporting; covers Railway, GitHub, WeWeb, Xano and Slack.
Decisions taken: Bayesian statistics, alerts by Slack DM to the owner, built and deployed on
internal tooling only. **Nothing in this system writes to the production app** (WeWeb site,
Xano tables/endpoints the app uses, the Planner `rec-engine`). It only reads Xano, at low
volume and off-peak.

---

## 1. The seven things every experiment must state

An experiment cannot go `live` in the registry without all seven. The engine flags any live
experiment missing one as **Spec incomplete**.

| # | Question | Registry field(s) |
|---|---|---|
| 1 | **What is the experiment?** The concrete change couples see. | `name`, `what_changed`, `links` |
| 2 | **What does it impact?** Systems touched and who sees it. | `surfaces[]`, `audience` |
| 3 | **Hypothesis**: "If we X, then metric Y moves Z, because W." | `hypothesis` |
| 4 | **What is it trying to test?** The single question it answers. | `tests` |
| 5 | **Success metric**: primary metric, direction, the smallest lift worth having, decision thresholds, guardrails, couples impacted. | `metric`, `guardrails[]`, `decision_rule` (defaults by tier), `audience.est_daily_exposed` |
| 6 | **How long will it run?** Planned days, review date, and the hard stop. | `start_date`, `planned_days`, `review_date`, `max_days` |
| 7 | **How is it reported?** Automatic (engine computes it from Xano) or manual (Claude Code appends `results[]`). | `reporting` |

Plus `assignment` (how couples are put in arms), `rollback` (how to undo it) and `tier`.

## 2. Registry (source of truth)

`experiments/experiment_log.json` on `main` of `tulle_vendor_data_updates`. It holds the
**spec** (what was decided, by humans and Claude). It never holds computed numbers that change
daily. Those go in the readout history (section 5).

v2 adds keys and never removes or renames v1 keys, so every existing reader keeps working
(Streamlit 🧪 tab, admin-api, Tulle Bot, daily brief). `"framework_version": 2` marks an entry
written to this standard.

```jsonc
{
  "id": "EXP-2026-11-free-daily-question",      // EXP-YYYY-MM-slug, never reused
  "name": "...",                                 // v1
  "status": "live",                              // see lifecycle (section 3)
  "tier": "money" | "ux" | "infra",              // sets the default decision thresholds
  "themes": ["planner"],                         // v1
  "what_changed": "Free couples get 3 Planner questions for life, then 1 per UTC day.",
  "surfaces": ["rec-engine", "weweb:planner", "xano:table79"],
  "audience": {"segment": "free couples who open the Planner", "est_daily_exposed": 35},
  "hypothesis": "If free couples get a daily question, more of them return within 3 days, because there is a reason to come back.",
  "tests": "Does a daily allowance bring free Planner couples back?",
  "metric": {                                    // primary metric
    "key": "planner_return_within_days",         // catalog key (experiments_engine/metrics.py) or "manual"
    "params": {"days": 3},
    "direction": "increase",
    "baseline": 0.042,                           // rate before, with its source in metric.baseline_note
    "mde_rel": 0.5,                              // smallest relative lift worth acting on
    "label": "came back within 3 days"
  },
  "guardrails": [{"key": "purchases_per_day", "max_drop_rel": 0.25}],
  "assignment": {"type": "pre_post", "pre": ["2026-10-31", "2026-11-05"], "post_start": "2026-11-09T01:11:05Z"},
  //  or {"type": "split", "rule": "user_id_parity", "treatment": "even"}
  //  or {"type": "split", "rule": "hash_pct", "salt": "...", "pct": 50}   (must mirror the shipped code exactly)
  //  or {"type": "benchmark", "benchmark": {"n": 33, "conv": 1}}           (one group vs a fixed past rate)
  //  or {"type": "rollout"}                                               (no comparison; guardrails only)
  "decision_rule": {"ship_p": 0.95, "stop_p": 0.10, "max_loss": 0.002},   // optional; tier default otherwise
  "start_date": "2026-11-09", "planned_days": 14, "review_date": "2026-11-23", "max_days": 42,
  "reporting": "auto" | "manual",
  "rollback": "Set REC_FREE_DAILY_MODE=off on rec-engine",
  "links": ["rec e29f61a"],
  "goal": "...", "primary_metric": "...", "guardrail": "...",   // v1 free-text, kept
  "results": [ {"date": "...", "note": "...", "counts": {...}} ],  // v1: manual readouts
  "decision": "", "decided_on": null, "outcome": null,          // outcome: shipped|reverted|shelved|inconclusive
  "framework_version": 2
}
```

`changes[]` (v1) stays: one entry per shipped change that could confound a live experiment.
v2 adds `source` (`weweb|rec-engine|railway-var|xano|github|brevo|stripe|manual`) and `ref`
(commit, publish id, endpoint id).

## 3. Lifecycle

```
draft -> planned -> live -> (paused) -> decided: shipped | reverted | shelved | inconclusive
```

- **draft**: idea; the spec may be incomplete. **planned**: spec complete and built, not switched on.
- **live**: `start_date` = the real UTC live date. The engine reads it every day.
- **decided**: the owner makes the call. Claude Code records `outcome`, `decided_on` and `decision` (why).
  - `shipped`: kept for everyone. `reverted`: rolled back. `shelved`: stopped without a verdict,
    because it can't reach one or isn't worth the time. `inconclusive`: ran to `max_days` with no clear winner.
- Legacy `concluded` reads as decided, with outcome unknown.

## 4. Statistics (Bayesian)

All rate metrics use a Beta-Binomial model with a flat Beta(1,1) prior. Count metrics (purchases/day)
use Gamma-Poisson. For each experiment the engine reports:

- **Chance treatment beats control**: P(T > C). This is the headline number.
- **Lift**: median relative lift with a 95% credible interval.
- **Expected loss**: what we lose, in absolute rate, if we ship and we're wrong.
- **Couples in each arm**, conversions, and the daily exposure rate.

**Decision rule** (defaults per tier, overridable per experiment):

| tier | ship when | stop for futility when | max expected loss |
|---|---|---|---|
| money (payments, pricing, paywall) | P(beat) >= 0.95 | P(beat) <= 0.10 after >= 50% of planned sample | 0.2 pts |
| ux | P(beat) >= 0.90 | P(beat) <= 0.10 after >= 50% of planned sample | 0.5 pts |
| infra (speed, reliability) | P(beat) >= 0.90 | same | 0.5 pts |

A verdict is **callable** only when the threshold is met, the loss bar is met, and the experiment
has run at least 7 days (one full week, so weekday mix is covered).

**Pre/post experiments are observational.** The engine computes the same numbers but labels
them "evidence, not proof" and lists every `changes[]` entry inside the windows. With 2 or more
overlapping changes the verdict is capped at **Confounded**, and the dash says what would unconfound it.

**Sample planning** (shown at launch and on the dash): for baseline p and lift worth detecting d,
the planning estimate is n per arm = (1.645 + 0.84)^2 * 2p(1-p) / d^2. That gives about 80%
chance of reaching P(beat) >= 0.95 if the true lift is d. Days needed = n / daily exposure per arm.
If days needed > `max_days`, the experiment is flagged **Can't reach a verdict** at launch, before
anyone waits six weeks to find out. Reality check: about 100 new accounts and 5 to 8 purchases a day.
Purchases are a guardrail, not a primary metric, for anything shorter than about 8 weeks.

## 5. The engine (automatic updates)

`experiments_engine/` (Python, no web framework) runs in its own Railway service, **experiments-engine**
(project "tulle admin dash", deployed from `main`, watch patterns `/experiments_engine/**` and
`/experiments/**` only, volume at `/data`). The service has a small HTTP API on the private
network that the Streamlit dash reads. The snapshot persists on the volume, so a restart
(including one triggered by a registry push) reloads the last snapshot and does **not** re-read
Xano unless the data is older than 6 h. It is deliberately **not** inside `admin-api`
(recon 2026-11-19: moving admin-api to main would re-pull the users export and ep199 on every
registry push). Steps:

1. **Load** the registry (the copy deployed with the service, from `main`).
2. **Validate** each entry against the seven questions and produce `spec_issues[]`.
3. **Compute** each auto metric from Xano, read-only: incremental reads, page size 500, paced,
   once every 6 h plus on demand. Metric definitions live in `metrics.py` (one function per
   catalog key) and always exclude internal accounts.
4. **Score**: posterior, lift, loss, sample progress, projected days to a verdict.
5. **Health and recommendation** (section 6).
6. **Change feed**: collect shipped changes (section 8) and match them to `changes[]`.
7. **Persist** one readout row per experiment per day to the service volume
   (`/data/experiments/readouts.jsonl`) for trends and staleness checks. Serve it as JSON to the dash.

## 6. Health: when an experiment is going stale

Every live experiment gets exactly one **health** state (the worst that applies) and one
**recommended action**:

| health | rule | action shown |
|---|---|---|
| `guardrail_breach` | A guardrail crossed its threshold (e.g. purchases/day down > 25%, P > 0.9) | **Pause or revert now** (urgent DM) |
| `callable_win` | Ship threshold + loss bar + >= 7 days | **Call it: ship** |
| `callable_loss` | P(beat) <= `stop_p` after >= 50% of sample, or P(beat) <= 0.05 at any point after 7 days | **Call it: revert** |
| `needs_call` | Past `review_date` with no decision | **Owner call due** (stale after 3 days overdue) |
| `starved` | Projected days to a verdict > `max_days`, or exposure < 5 couples/day/arm over 7 days | **Shelve or redesign** |
| `no_data` | No new exposure for 3 days, or the auto metric failed (from the first failed run; stale after 2 failed runs in a row; a registry-only rebuild is not a new run) | **Check tracking / is it switched on?** |
| `zombie` | Live > 2x `planned_days` | **Shelve** |
| `manual_overdue` | Manual reporting and no `results[]` entry for 7 days | **Read it out** |
| `confounded` | Pre/post with >= 2 overlapping changes (see below) | **Evidence only; consider a split test** |
| `spec_incomplete` | Missing any of the seven | **Complete the spec** |
| `guardrail_unmonitored` | A guardrail the engine could not compute (unsupported param, unknown metric, error). Manual guardrails are listed as `manual`, not flagged | **Fix the guardrail metric** |
| `paused` | status `paused` (replaces every other state; no starved / no_data / zombie / manual_overdue flags) | **Resume or shelve** (stale after 14 days paused) |
| `on_track` | none of the above | Keep running, ~N days left |

**Overlapping change** (for `confounded`): a `changes[]` entry inside [pre start, today] that either
names the experiment in `affects`, or has no `affects` and touches the same surface family as the
experiment's `surfaces` (from its v2 `source`, else keywords in its text; no family on either side
means it does not count). The experiment's own launch never counts: an entry dated on its
`start_date` whose `affects` is exactly [it], or whose ref/what shares a commit sha, `REC_*` flag,
epN/fnN or cacheVersion with its links/what_changed.

Every experiment also carries `guardrail_status` (one row per guardrail: ok | breach | unmonitored | manual).

**Stale** = `needs_call` 3+ days overdue, `starved`, `no_data` (2+ failed runs, or no exposure for 3
days), `zombie`, `manual_overdue`, or `paused` for 14+ days.
Stale experiments appear first in the dash action queue and in the daily DM until they are
decided or shelved.

## 7. Reporting in the Tulle Admin Dash (Experiments tab)

The design principle is one question per screen region. Nothing is shown that doesn't lead to an action or a learning.

1. **Today strip**: four numbers that never overlap and add up to the live count. Decide now ·
   Stale · Fix the setup · No action due. A guardrail breach also gets a red banner on top.
2. **Action queue**: grouped by what to do, worst first inside each group.
   - **Decide now**: guardrail breach, ready to ship, likely worse, or call due.
   - **Stale: call it or shelve**: each row says why it is stuck. Rows have tick boxes, and
     **Copy shelve list for Claude Code** turns the selection into a ready-to-paste instruction.
   - **Fix the setup**: spec gaps, metric not read, guardrail unmonitored.
   - **Before/after only**: no action needed.
   Each row has one verb ("Ship it", "Call it or shelve", "Get a readout").
3. **Running**: compact rows. Name · day X of Y (progress bar) · primary metric control -> treatment ·
   chance it's better (gauge) · health pill. A row opens the **detail drawer**.
4. **Detail drawer**: the seven sections in order (What · Impacts · Hypothesis · Tests · Success metric
   & stats · Timeline · Reporting), plus a trend chart of P(beat) and couples-in over time,
   overlapping changes, the readout log and the decision.
5. **Decided**: a learnings library. Outcome badges, filterable by theme and outcome, each with
   its one-line takeaway.
6. **Changes**: the shipped-change feed. Unlogged changes are highlighted.
7. **Rules**: this framework, rendered.

## 8. Change feed (Railway, GitHub, WeWeb, Xano, Slack)

| source | how it is detected | needs |
|---|---|---|
| WeWeb publishes | Same method as `weweb_deploy_notify.py` (build hash of the public page), polled every 30 min. That is one anonymous CDN GET, the same as one visitor | none |
| rec-engine / Railway deploys + REC_* variable flips | Railway **project webhook** -> `POST /hooks/railway` on the engine. Variable flips show up as redeploys of the same commit. No Railway token: tokens can redeploy rec-engine | webhook (no secret exposure) |
| GitHub | Public commits API for `tulle_vendor_data_updates` (unauthenticated, 4 calls per 6 h). Private repos are skipped unless the owner adds a read-only fine-grained PAT (`GITHUB_READ_TOKEN`) | optional PAT |
| Xano | Workspace audit log via the metadata token, read-only. Degrades to "unavailable" if the token lacks the scope | existing token |
| Slack | DMs are the output channel (health-report bot token + its DM conversation id) | existing bot |

An **unlogged change** is a detected change with no `changes[]` or experiment entry within 24 h
that touches a user-facing surface. It appears on the Changes tab and in the daily DM until it is logged.

## 9. Alerts (Slack DM to the owner)

- **Daily digest**, ~14:00 UTC, sent only when something needs action: calls due, stale,
  callable, unlogged changes, and a one-line "N running healthy".
- **Urgent**: a guardrail breach, checked every 6 h. Sent once per breach per experiment
  (de-duplicated on the volume).
- **Weekly** (Monday): what was decided last week and what was learned.
- Every message links to the dash. No message is sent when nothing changed.
- Sent with `HEALTH_SLACK_BOT_TOKEN` to `HEALTH_SLACK_USER_ID` (an existing DM conversation id;
  Tulle Bot lacks `im:write`). These are Railway reference variables to health-report-cron.

## 11. Production-safety guarantees

- The engine's only network writes are Slack DMs to the owner and its own volume.
- Xano: read-only metadata calls, incremental, paced (>= 0.3 s apart), at most ~60 per 6 h run,
  with a hard per-run budget that aborts the run when it is hit.
- It never touches rec-engine, WeWeb or the Xano endpoints the app calls.
- The registry stays small. Readout history lives on the engine volume, never in
  `experiment_log.json`, because Tulle Bot loads that file into every prompt.
- Pushes avoid 13:30-13:45 UTC (daily brief window).

## 10. Process (Claude Code)

- **Launch**: write the full v2 entry and run `python experiments/validate.py` (it must pass;
  it prints the sample plan and days needed), then push. Launches run
  `python experiments/validate.py --id <new id>`, which must pass strictly (`spec_gaps` are not excused). A change that confounds a live
  experiment gets a `changes[]` entry in the same push.
- **Read out**: auto experiments need nothing. Manual ones get a `results[]` entry.
- **Call**: owner decides. Claude Code records `outcome`, `decided_on` and `decision`, and
  sets `status` to the outcome.
- **One change per experiment.** Split tests use a per-experiment salt.
