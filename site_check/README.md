# site_check: daily QA of tulletogether.com

This job crawls the WordPress marketing site once a day. It sends one message to Slack #growth-strategy, and only when
something changed since the last run: a new failure, a fix, or a page added to or removed from the sitemap. Open failures
that have not changed are not posted again every day. They appear in the Monday weekly summary. The job is read-only and
needs no WordPress or GA credentials.

## What it checks (canonical values live in `config.json`)
| Check | Severity |
|---|---|
| Pages from the sitemap and key pages (`/`, `/blog/`, `/timed-plans/` and others) return a status under 400 | fail |
| Internal links resolve | fail |
| Couples count equals `couples.canonical` (60,000+). Any other count of 10,000 or more next to "couples/users/..." fails (35,000, 50,000). A missing "+" is a warning | fail / warn |
| Pricing pages (by path, or auto-detected when 2 or more plan labels sit next to a $ amount): every $ amount falls in a plan range, every plan's range appears, and retired plans ("2 weeks", "+2 weeks") are gone | fail |
| Placeholder text: "Accordion Item", "Lorem ipsum", "Add Your Heading Text Here" and similar. The YouTube `/vi/ID/` in the HTML | fail |
| `G-7BRVVCRKX1` and `GTM-5HRS82NQ` appear in each page's source | fail |
| No links to `tulle.glide.page` | fail |
| Key pages have a tulletogether.app link. Each app link returns 200 and does not redirect to the bare host (losing `/sign-up/`) | fail |
| App links carry `utm_source`, `utm_medium` and `utm_campaign`. With `utm.mode=auto`, this is enforced once any site link carries UTMs | fail |
| Leftover `/elementor-####/` slugs in links | warn |
| TTFB over `ttfb.threshold_s` on a key page, or on more than `fail_share` of all pages | fail |
| Homepage mentions "Planner" and "Price Report" | warn |
| Outbound links to other sites (404/410/DNS). Weekly run only, capped at 300 | warn |

Finding keys never contain measured values, so a TTFB that moves from 2.1s to 2.4s does not trigger a new alert.

## Run locally
```
pip install -r requirements-dev.txt
python -m pytest -q tests
python run.py                    # DRY_RUN defaults to 1: prints the Slack text and saves no state
python run.py --max-pages 20     # quick check
python run.py --save-state       # dry run that keeps state in ./state, to see the diff on the next run
python run.py --weekly           # force the weekly summary and the outbound-link check
```

## Railway (service-level settings; see railway.toml for the full list)
Config-as-code files stop applying to existing Railway services on 2026-12-01, and new services already reject a
config path. So set these on the service by hand:
- New service from GitHub `marthv/tulle_vendor_data_updates`, branch `main`
- Root Directory `/site_check`, Builder Dockerfile
- **Watch Paths: `site_check/**` only**
- Cron `0 13 * * *`, Restart Policy Never
- Volume mounted at `/data`
- Variables: `DRY_RUN=1` (first week), `SITE_CHECK_SLACK_TOKEN` (the Tulle Bot xoxb token, which needs chat:write and must be in
  the channel), `SITE_CHECK_SLACK_CHANNEL` (the #growth-strategy channel ID)

Exit code 2 means the check could not run (home page or sitemap unreachable, or the Slack post failed). The state is not
advanced, so the next run reports again.
