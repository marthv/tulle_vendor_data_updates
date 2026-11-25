# What each row on the Experiments tab shows (EXP_TAB_V2)

One short row per running test; tap for the detail. Planned and decided tests are folded. The dot is the only colour
and is the health of the test, never its result or a price.

| Row | What it says | Where it comes from |
|---|---|---|
| Dot | green healthy, amber not fully checked, red problem, hollow not running | worst check |
| Name | `plain_name`, else a shortened `name` | registry |
| One line | The problem when red, else `plain_line`, else where it stands | checks, registry |
| Couples | Finished couples in the smaller version, of those needed | engine or `exposure_log`, sample plan |
| Date | Read date (splits) or review date | `launch_lock`, `read_date`, `review_date` |

| Detail | What it says |
|---|---|
| Result | "hidden until <date>", "Stopped early, result not read", or the one read. Tests that are not splits: as before |
| Versions | Joined, finished, and expected by now, for each version |
| Payments | Watch only, by version, with counts; can't be read |
| Checks | Amber and red ones with the reason; green ones on one line |
| Who counts, measured by, timing, next | In plain words |
| Grey list | "Shares couples with X"; a 2x2 table only when both tests have been read |
| Earlier versions | `family_history`: one test, many versions; entries in the same `family` fold in and are not counted |
| Technical notes | The registry's own name and `what_changed` |
| Notes | Written in the dash by people, so the lock can't filter them |
