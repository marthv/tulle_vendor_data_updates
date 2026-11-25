# What each card on the Experiments tab shows (EXP_TAB_V2)

One card per test. The only colour is the health of the test. Results and payments are plain text.

| Line | What it says | Where it comes from |
|---|---|---|
| Pill | Healthy / Needs a look / Problem (the worst check below); Planned tests show their status | `row.light`, `row.checks` |
| Day X of N | Days since the start, out of the days to the read date | `start_date`, read date |
| What changed | The change couples see (long text folds) | `what_changed` |
| Who counts | Audience, and the only place couples are counted from | `audience.segment`, `exposure` |
| Couples in each version | So far, against what was expected by now; the plan below it | engine, or `exposure_log`; `audience.est_daily_exposed` |
| Read date | The one day a split is read (review date for other tests) | `read_date`, else `review_date` |
| Result | Split: "hidden until <date>", then one read. Other tests: as before | `verdict` |
| Payments | Watch only, by version, with counts. Never decides a test | table 16 by account id |
| Next | What to do | `verdict.recommend` |
| Checks | Balance, both versions / repeat accounts, settings, exposure, coverage, sample, harm stop | `experiments_engine/v2.py` |
| Grey strip | "Shares couples with X"; a 2x2 table at the final read only, when both tests have per-couple data | `row.overlaps` |
| Earlier versions | `family_history`: one test, many versions. Entries in the same `family` fold into it and are not counted | registry |
| Notes | Notes added in the dash. Written by people, so not filtered by the lock | engine volume |

Below the cards: decided tests, the Planner scorecard and unlogged changes, folded.
