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
| 2 | **What does it impact?** Systems touched, who sees it, and the one thing it is meant to improve (its bucket). | `surfaces[]`, `audience`, `bucket` |
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
  "bucket": "planner_engagement",                // payment_funnel | planner_engagement | search | data |
                                                 // reliability | onboarding | retention (derived if missing)
  "in_rotation": true,                           // one of the <= 3 experiments called each week (section 12)
  "notes_synced_through": "2026-11-23T15:00:00Z",  // decision notes up to here are already in the registry
  "paused_ranges": [{"from": "2026-11-07", "to": null}],  // optional; paused_on also works
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

One screen, no sub-tabs (owner 2026-11-19: "too many tabs and too many places to look").

1. **Calendar strip** (top): one bar per experiment over time, with a line for today. Clicking a bar,
   or a week, filters the grid below to that experiment or to what was running then. A chip shows
   the filter and clears it.
2. **Planner scorecard**: one quiet strip of three tiles (see section 13), collapsible.
3. **Toolbar**: a search box (name, change, hypothesis, what it tests, metric, bucket, notes); status
   chips (All · Needs a call · Running · Planned · Decided) with counts; a bucket filter; and a sort
   (Needs a call first · Ending soonest · Newest · Oldest · Chance it's better · Bucket · A-Z).
4. **Card grid**: one card per experiment. Each card shows status, the in-rotation marker, what it
   tests, its bucket, its time bound (start -> end, day X of Y), the starting point -> now, the
   result and the recommended call. A card opens the **detail drawer**.
5. **Detail drawer**: the outcome first; then **Why it is / isn't working** (TL;DR plus the reason for
   the recommended call, and the step that unsticks it when stale); then **How we'll make the call**
   (verdict.how_we_call, section 12); then notes, and the seven sections collapsed.

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
- **Weekly call** (Monday): the agenda. Each in-rotation experiment's TL;DR and recommended call,
  then the backlog to clear (top 5, each with its recommended call), then what was decided last
  week. Decisions are made by adding a **Decision** note in the dash (section 12).
- The daily digest lists each item with its recommended call (section 12), and stale items with
  the concrete step that moves them along ("Next: ...").
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
- **Sync decisions** made in the dash: `GET /decisions/pending` on the engine lists decision notes not
  yet in the registry. For each, set `status`/`outcome`/`decided_on`/`decision` (or a new `review_date`
  for keep_running, `status: live` for resume), then set `notes_synced_through` to the note's `ts`.

## 12. Verdicts, notes and the weekly rotation (2026-11-19)

### Verdicts and next steps

Every row in the snapshot carries `verdict` (experiments_engine/verdict.py), so nobody has to work out
what a result means:

- `result`: Ahead · Behind · No clear difference · Too early · Not measured · No data · Biased metric
  (decided rows: Shipped · Reverted · Shelved · Inconclusive · Concluded).
- `tldr`: one plain sentence that starts from where it began. "Started at 5.1% (20 of 395, 10-20..11-03)
  of couples who hit the PDF limit paid within 1 day; after the change 5.9% (22 of 371, 11-03..11-19):
  70% chance it's genuinely better; money tests need 95%." Before/after reads add "treat this as
  evidence, not proof". With no starting point the TL;DR says "No starting point recorded".
- `numbers`: baseline {label, value, rate, source: readout control | registry baseline}, before/after
  strings and rates, chance it's better, the bar (for charts).
- `recommend`: {call (ship | revert | shelve | inconclusive | keep_running | read_out | fix_tracking |
  resume_or_shelve | complete_spec | split_test), label (30 chars or so), why}. `unstick`: who does what, and how long it takes, when the experiment
  is stale or past its review date. `confidence_note`: pending couples, small numbers, defaulted windows.

Rules, first match wins:

| situation | call | label |
|---|---|---|
| draft / planned | complete_spec / keep_running | "Finish the spec before launch" (always for drafts) / "Launch when ready" |
| paused | resume_or_shelve | "Resume or shelve"; "Shelve it (or resume)" after 14 days paused |
| guardrail breach | revert | "Revert now" |
| approximate (biased) metric | split_test | "Judge it with a fair test" (no call on the numbers) |
| metric failing, no numbers | fix_tracking | "Fix the metric (or shelve)" |
| zero conversions in both periods | fix_tracking | "Check the tracking (or shelve)" |
| site-wide count metric, before/after or benchmark, inside its run | keep_running | "Keep running (N days left)"; "Keep running (set a review date)" with no planned run |
| site-wide count metric (purchases / revenue / ratings per day) on a before/after or benchmark read, past its run | inconclusive | "Keep it, close inconclusive" (leaning better) / "Close inconclusive"; never ship or revert, because a whole-site count can't credit one change (run a split test if it matters) |
| chance >= tier bar, >= 7 days, expected loss <= max_loss (section 4) | ship | "Ship it"; "Keep the change (ship)" when confounded |
| chance <= stop bar (loss callable or past planned run) | revert | "Revert it" |
| between the bars, inside the planned run | keep_running | "Keep running (N days left)" |
| between the bars (or over the bar with too much expected loss), past the run (or starved), chance >= 50%, guardrails ok | inconclusive | "Keep it, close inconclusive" |
| between the bars, past the run, chance < 50% | revert | "Revert (leaning worse)" (why: unless there is a product reason) |
| manual, no numbers, due (review passed / overdue / stale) | read_out | "Read it out, then decide" |
| manual, not due | keep_running | "Keep running (N days left)" |
| spec incomplete, nothing to read | complete_spec | "Complete the spec (or shelve)", naming the top 2 missing fields |

"Past the run" means days_left is 0 (a planned run or review date exists and has passed), a call is due,
or the experiment is starved or a zombie. No planned run or review date is not "past the run".

Result label from the chance it's better: >= 80% Ahead, <= 20% Behind, otherwise No clear difference;
under 7 days running is Too early.

### Buckets (what it is meant to improve)

`bucket` groups experiments for the dash: payment_funnel · planner_engagement · search · data ·
reliability · onboarding · retention. Set it in the registry (question 2); when missing the engine derives
it (experiments_engine/buckets.py: performance -> reliability; sign-up/T&C/welcome -> onboarding; a money
metric -> payment_funnel; NPS/Trustpilot/email -> retention; Planner -> planner_engagement; search ->
search; extraction/data -> data; paywall/pricing -> payment_funnel). `validate.py` warns when a v2 entry
has no bucket. `snapshot.buckets` lists running / waiting / decided counts per bucket, in a fixed order.
Each row also carries calendar dates: `start_date`, `review_date`, `max_end_date`, `decided_on`,
`paused_ranges`.

### Notes: a live record of what we discussed

Anyone on the dash can add a note to an experiment: `note` (discussion), `next_step`, or `decision`
(with a call: ship | revert | shelve | inconclusive | keep_running | resume). The dash sends a
`client_id`, so a retried POST returns the stored note instead of adding a duplicate. Notes live on the engine
volume (`notes.jsonl`), never in the registry, and show at once (attached when the snapshot is served).
A decision note newer than the entry's `notes_synced_through` marks the row **decided in dash, registry
update pending**: it leaves Decide now, the queue and the backlog until Claude Code syncs it (section 10).
Engine endpoints (private network + key): `GET/POST /experiments/{id}/notes`, `GET /decisions/pending`.

### The weekly rotation

- At most **3** experiments are **in rotation** (`in_rotation: true`, live) at any time. `validate.py --id`
  fails a fourth.
- Each in-rotation experiment has a run of **14 days or less** (planned_days or review_date), so it comes
  up for a call within two weeks.
- **Calls happen every Monday** (the Weekly call DM is the agenda). Each in-rotation experiment gets a
  call or an explicit "keep running". Anything else live is **backlog**: clear it (ship as is, revert,
  shelve) or promote it into a free slot.
- New launches go into rotation only when a slot is free. A change shipped outside rotation is logged as
  a `changes[]` entry or a rollout, not as an experiment that needs a call.

## 13. Geo cohorts, Planner segments and the Planner scorecard (2026-11-19)

**Geo cohorts.** `purchases_per_day` and `revenue_per_day` accept `params.cohort`: texas_only, texas,
ca/california or ny/new_york. A payer's cohort comes from `Wedding_Location_Updated` on their account,
using the same rule as the app's price buttons: items split on commas, trimmed and lower-cased;
`texas_only` means the set is exactly {texas}.
- Locations are looked up once per couple. The source is the table-1 rows the run already loaded,
  else `locations.json` on the engine volume (refreshed every 30 days), else at most 30 `get_row`
  calls per run.
- While under 95% of payers are resolved, the readout is held back. The verdict then says
  "Measuring" with a progress count, never a guessed number.
- A geo-filtered count is not a whole-site count, so it can be shipped or reverted like any other
  metric (money tier: 95%).
- Caveat: location is read as it stands today, not as it was at payment time.

**Planner segments.** `planner_return_within_days`, `planner_second_question` and
`planner_pdf_open_rate` accept `params.segment`: all, free, paid or forever. The segment comes from
the couple's table-79 rows: `forever` on any row, `has_access`, and counted-free or free-limit rows.

**Planner scorecard** (`snapshot.scorecards.planner`). It is the owner's success test for the Planner,
by who uses it. Every full run computes it from the same usage load, comparing the last 7 complete days
with the 7 days before:
- Free couples who open a PDF from the Planner within 3 days (the Planner's job for free couples
  is to get them to pricing, on the way to the paywall).
- Forever couples who come back to the Planner within 3 days (retention).
- Forever couples who ask a second question within 3 days.

It is a health read, not an experiment: no ship or revert comes from it.

**How we'll make the call** (`verdict.how_we_call`). Every verdict states its decision rule in plain
words: `bar`, `ship_if`, `revert_if`, `otherwise` and `now`. There is no standard time box. Each
experiment carries its own rule and its current position against it.

## 14. EXP_TAB_V2: one read per split, setup checks, one row per test (2026-11-25)

Behind `EXP_TAB_V2`: `1`, `on`, `true` or `yes` turns it on. Unset or anything else = sections 1-13, unchanged (`0`
still means the old tab). **Before deploying, check that no service already has `EXP_TAB_V2=1`: it used to mean the
same as unset.** Set it on experiments-engine, the Streamlit dash, Tulle Bot and health-report-cron, engine first.
`validate.py` ignores the switch.

**Read lock (splits).** One source on every surface: the registry entry and its `launch_lock`
(`python experiments/validate.py --lock <id>`, pasted in at launch; validate.py refuses a live split without one, or
one that no longer matches). Read date = the lock's date; a later date on the entry also counts, an earlier one never
opens anything. Without a lock: `read_date`, else the later of `review_date` and start + planned days; Tulle Bot's text
and the registry-only tab then never open it. Once a split, always a split: changing `assignment.type`, or
the status, opens nothing. Not started, running before the read date, and closed before the read date ("Stopped early,
result not read", for good) are hidden in the snapshot, HTTP API, history rows, tab, Slack messages, Bot answers and
prompt, and the brief's A/B block. On the read date: ship at 97.5%, revert at 2.5%, else no clear difference;
computed as of 00:00 UTC and kept (`reads.json`). Manual tests: the first `results[]` with counts dated on or after the
read date. A red balance, mixing, settings, coverage or data check makes the read "can't be trusted". The engine also
keeps the settings it first saw (`settings_lock.json`), so replacing the lock itself is red there.
Not covered: free text people write (dash notes, `changes[]`, MEMORY.md, commits), the public registry file itself, and
admin-api (branch `admin-cloudflare`). An edit made before the lock is stored can't be seen.

**Harm stop (splits).** One look per UTC day (`harm_looks.json`); a stop that fires stays. It fires when P(the new
version is within 10% of the old one, or better) <= 0.5% divided by the number of watched measures, with 40+
couples per version and 12+ conversions. "Not checked" (too few couples, no outcome feed, metric failed, read by
hand) is amber with the reason, never green. Why not the plain P(better) <= 0.5%: A/A runs, 28-35 days, 4 measures:

| rule | looks | false stops (4 measure mixes) | 60% drop caught in 14 days |
|---|---|---|---|
| plain P <= 0.5% each | daily | 4.8 - 12.3% | 97-100%, 36% on a 3% measure |
| plain P <= 0.5% each | every 6 h | 7.2 - 16.6% | not measured |
| plain P <= 0.125% each | every 6 h | 2.2 - 5.2% | 98-100%, 19% on a 3% measure |
| **this rule** | daily | 0.3 - 1.0% | 93-100%, 13% on a 3% measure |
| **this rule** | every 6 h | 0.6 - 1.2% | not measured |

(8,000 runs each, seed 11, normal approximation to the posterior; the exact function is simulated in tests/test_exp_tab_v2.py.)

**Balance.** Exact binomial. Red at once at p < 0.001 once 75% of the planned couples (at most 1,500) are in, and on
the read date; at p < 0.00001 from 100 couples. Earlier p < 0.001 is amber. Measured (100,000 runs): 55/45 at 2,000
couples red in 87%; true 50/50 at 2,000 red in 0.1%, and red at any of the daily looks of a whole run in 0.2-0.4%. Red
at once from the first couple would do that in 0.6-0.9% of runs, above the 0.5% limit.

**Other checks.** Both versions / repeat accounts: recorded-version mismatches and accounts with the same normalised
e-mail (keyed hash in memory only), left out of the read, red over 1%. Where no version is stored the check says
"can't be checked" (amber). Settings: any change to rule, salt, percent, start, measure, read date or exposure is red.
Exposure: `exposure = {"source": "xano:table79" | "xano:table1" | "mixpanel:<event>", "surfaces", "kinds"}`; "none", "todo",
"everyone by default" and the like are refused. 48 h with nobody is amber or red by expected volume. Coverage of PDF opens under
98% is red. Sample pace counts only couples whose follow-up is over (`metric.follow_days`, params, or "within N days" in
the label). Manual splits log couples in `exposure_log` (no outcomes).

**Guardrails and payments.** Computed for manual entries too. Inside a split nothing is compared before/after. Payments
are shown by version, watch-only (a declared payment guardrail can still trip the harm stop); when the measure itself is
money-like (paid, upgraded, subscribed, plan, ...) they are shown in total only until the read.

**Overlap.** The 2x2 table appears only when both tests have been read.

**Sample plan (splits).** Two-sided 5%, 80% power, both arms' variance: n = (1.96 + 0.84)^2 [p1(1-p1) + p2(1-p2)] / d^2.

**Event-based tests (Price Report v2).** `python -m experiments_engine.event_read`: version from `split_arm(account id)`, every
card-seen view kept, members left out by account using the purchase log at first card-seen, late-id couples left out by account in
both versions. `access_settled` and `arm != late_arm` are never filters; the settled share is shown only after the read.

New additive registry keys: `read_date`, `exposure`, `exposure_log`, `launch_lock`, `family`, `family_card`,
`family_history`, `plain_name`, `plain_line`, `metric.follow_days`. Rows: `experiments/CARDS.md`.
