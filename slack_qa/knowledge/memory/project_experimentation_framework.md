---
name: project_experimentation_framework
description: "A/B testing framework — code complete and CI-tested in the repo, 0% live until the WeWeb head block is pasted; smoke_test flag is the proof-of-chain"
metadata:
  type: project
---

2026-09-06. The experimentation framework is **code complete and inert**. Everything lives in
`tulletogether` on branch **`ab-testing-framework`** (commit af36130, off `xano-cache-optimization`).

**The last mile is a MANUAL PASTE, and nothing reaches production without it.** `web/README.md`
line 12: WeWeb's GitHub integration pushes **out** only and never reads this folder back. The
file to paste is the generated `web/weweb-custom-code.html`, into WeWeb → Settings → Custom code
→ Head. **The editor already holds an older generated block, so this is a re-paste, not a
replacement of unknown legacy code.** Diffed 2026-09-06: only **6 hunks**, all inside sections 5
and 6 — sections 1-4 and 7 (CONFIG, design tokens, responsive CSS, viewport meta, Chameleon) are
**byte-identical**, so a paste changes no styling. The three real deltas: the `smoke_test` entry,
the surfaceless-exposure fix, and one comment. Rollback artifact (with live tokens, gitignored):
`.claude/weweb_backups/weweb_head_pre_tulle_2026-09-06.html`. **The MCP cannot read head custom
code**, so any future backup is still a manual copy.
Two token placeholders must be filled: Mixpanel `[hex-redacted]` and the
Chameleon token from the old block. Then publish.

**CORRECTED 2026-09-06 — the module IS ALREADY LIVE, and it is failing.** An earlier paste
reached production: `flag_resolution` is set on real events, 728 unique users in 7 days, and it
reads **`timeout` for 100% of them — `network` has never been recorded once.** No
`$experiment_started` has ever fired. The head code now in the WeWeb editor is itself a
generated Tulle block (an older one) and DOES set `flags: true`, so the editor is ahead of
production. Leading hypothesis: the published bundle still runs the OLD `mixpanel.init` with
Tulle appended after it, exactly the state `web/README.md` section 1 warns about — in which case
publishing is the whole fix. **The one console line that separates the causes:**
`typeof mixpanel.flags` — `"undefined"` means `flags: true` never reached production, `"object"`
means the API is there and the fetch is failing or slow. The `smoke_test` flag is NOT the
problem: enabled, client-served, `rollout_percentage: 1`, all traffic to `treatment`.

**Mixpanel state:** one flag `smoke_test` (id `498cbafe-…`, enabled 2026-09-01, workspace
**4458885** — not the project id 3963130), and **zero experiments**. Nothing in the app calls
`tulle.variant('smoke_test')`, so it bills no MEU; it is exercised from the browser console.

## VERIFIED WORKING END TO END — 2026-09-06

First time the chain has ever run. Published, then in an incognito window:
`resolution: 'network'`, **`assignmentMs: 557`**, `registry` carrying `smoke_test`,
`variants: {}` on a cold load (the MEU guard holding), then `tulle.variant('smoke_test')` →
`'control'` with **`sources: {smoke_test: 'network'}`** and `exposed: ['smoke_test']`; a second
call returned the cache and fired no second exposure.

**`variant_source` is the field that proves the chain, NOT the variant value.** `network` means
Mixpanel decided; `fallback` means it did not. `'control'` here is a flag-config outcome — the
`smoke_test` rollout is gated on a "New User" cohort an anonymous visitor does not match. **A
real experiment wants rollout 100% with a 50/50 split, not a cohort rule.**

**557 ms also settles the diagnosis for good:** comfortably inside the ORIGINAL 1500 ms budget,
so "the fetch lands too late" was definitively wrong. The stub lookup was the entire bug.

`smoke_test` is deliberately LEFT IN PLACE for now: nothing in the app calls
`tulle.variant('smoke_test')`, so it bills no MEU and is inert. Retire it with the next paste
rather than spending a publish on it.

Server-side confirmation is a NEXT-DAY check — the Mixpanel lexicon and hourly buckets lag by
hours, so `experiment_exposure` / `$experiment_started` not appearing minutes later means
nothing. What to check tomorrow: the `flag_resolution` breakdown should show `network` replacing
`timeout` for new sessions.

## ROOT CAUSE of the 100% timeouts, found 2026-09-06

**THE FLAGS API DOES NOT EXIST WHEN `init()` RUNS.** `awaitAssignments()` looked up
`mixpanel.flags` **once** and gave up immediately when it was absent. The Mixpanel snippet
installs a **STUB** synchronously and loads the real SDK asynchronously; the stub proxies only a
fixed method list (`track identify reset alias register unregister get_distinct_id ...`) and has
**no `flags` property**. `tulle.init()` runs on the very next line of the same head block, so
that lookup was always null — an instant, permanent `timeout` for every user on every page load,
independent of connection. Fixed by polling for the API to **APPEAR** as well as to become ready
(same wait, same deadline, still no getter, so no exposure and no MEU).

**THE TELL WAS `assignmentMs: 0`.** Zero means it never waited at all, which no network story can
produce. Add a duration next to any status field for exactly this reason — the boolean
`resolution: 'timeout'` was compatible with three different diagnoses, and the number killed two
of them instantly.

**TWO EARLIER DIAGNOSES WERE WRONG. Both looked well-evidenced.**
1. *"The SDK never auto-fetches"* — derived in a browser **with AdBlock on**, where the request
   died as `ERR_BLOCKED_BY_CLIENT` and looked like it was never issued. Disproved in incognito:
   the SDK issues **two `/flags` calls, both HTTP 200, unprompted**. **Always retest in incognito
   with extensions off before blaming an SDK.** (An ad blocker also cannot explain a production
   aggregate: blocked users send no events, so they never appear in the numbers at all.)
2. *"The fetch lands after the 1500 ms budget"* — plausible because `resolution` was `timeout`
   under a Fast 4G profile while the Network tab showed 200s. Wrong, but the change it motivated
   was kept on its own merits: **`timeoutMs` (render deadline, 1500) and `assignmentTimeoutMs`
   (assignment deadline, 5000, clamped never below `timeoutMs`) are now separate**, so a
   genuinely slow fetch can no longer be mislabelled. `load_flags()` was also kept as a safety
   net but **delayed behind a 600 ms grace period** — calling it unconditionally duplicates the
   SDK's own request on every page load. Its promise **REJECTS** on a blocked request, so the
   `.catch` is load-bearing or every ad-blocking user gets an unhandled rejection.

**`flag_assignment_ms` is now registered alongside `flag_resolution`** so this is tunable from
real traffic instead of argued from first principles.

**A PASTE WENT LIVE WITH THE TOKEN PLACEHOLDERS UNREPLACED** (2026-09-06): the flags URL carried
`token=[redacted]`, so every event went to a non-existent project, Mixpanel
rejected the requests (surfacing as CORS errors, because a rejected request carries no
`Access-Control-Allow-Origin`), and Chameleon threw in `messo.min.js` for the same reason. The
CONFIG block's placeholder guard does `console.error` loudly — **read the console immediately
after publishing.** Tokens: Mixpanel `[hex-redacted]`, Chameleon
`S120hOSh...` (both public client-side keys; in
`.claude/weweb_backups/weweb_head_pre_tulle_2026-09-06.html`, which is gitignored).

**The SDK version is still unpinned:** the page loads the rolling `mixpanel-2-latest` alias, now
**2.83.0** (README recorded 2.82.0 on 2026-08-31) — it moved under a live deployment. Pinning
must be its own change and must go via jsDelivr; Mixpanel's own CDN 404s **silently** on
per-version filenames.

**Second, independent fault:** production's `TULLE_FLAGS` is EMPTY (`registry: Array(0)`) —
every entry is commented out there, so `smoke_test` was never registered and nothing could have
evaluated even with working assignments. Both faults had to be fixed for the chain to work.

**Method worth reusing:** none of this was findable by reading code or by the WeWeb MCP. It came
from one console battery on the live site (`typeof mixpanel.flags`, the proto method list,
`are_flags_ready()` at T+0 and T+5s, then `load_flags()`) plus a Mixpanel breakdown of real
events by `flag_resolution`. **Breaking down live events by a super property is the cheapest way
to tell "not deployed" from "deployed and failing".**

## PAYWALL IMPRESSION INSTRUMENTATION — built 2026-09-06, NEEDS A PUBLISH

Phase 1 of the first real experiment: get a correct denominator before designing anything.
**No flag is registered against the `paywall` surface, so this bills ZERO MEU** — `tulle.paywall()`
fires only `surface_shown` + `paywall_shown` until an experiment is attached.

**The impression event was provably undercounting.** 30d uniques:
`pdf_limit_popup_triggered` **271** vs `clicked_membership_for_pricing_pdf_limit_popup` **367** —
more clicks than impressions. Cause: the old event fires from a branch gated on
`FreeViewsRemaining == 0`, but `d653f81b` drives the counter to **-1** on the first block, so
**every repeat block was a click with no impression.** The four PDF Limit Trigger workflows use
`<= 0`, matching the overlay, so they count correctly. **The old event was deliberately left
alone** so its historical series stays continuous.

Four workflows gained `__PDFL_CHECK_EDITOR__` → `__PDFL_TRACK_IMPRESSION__` appended after
`__PDFL_CLOSE_OLD__` (whose `next` was null): `d1f7e001`/`d1f7e002` on Venue Vendor
(`94885c62`, `bd4d43da`), `d1f7e003`/`d1f7e004` on Other Vendors (`b0fd4fee`, `462ef392`).
Full inventory + unwind: `.claude/weweb_backups/paywall_impression_instrumentation_2026-09-06.md`.

**`advanced_filters` (455 clickers/30d) was deliberately NOT instrumented.** Its impression has
no discrete moment — it would be a page-load workflow gated on `hasPaid`, which is not reliably
populated at onload, and it would bucket ~3,000 users/month against a 5,000 MEU allowance while
diluting the sample with people who never engaged a paywall. If added later, use
`tulle.surface('advanced_filters')`, **NOT** `tulle.paywall()`, so it gets its own surface name.

## POWER REALITY — most experiments here CANNOT conclude

Measured 2026-09-06: 3,472 monthly uniques, 19,370 vendor views across 2,713 users
(**7.1 views/user**), 142 purchasers/30d, PDF paywall 271 impressions/30d.

- **PDF-paywall purchase conversion: ~9 MONTHS per arm.** 271/mo split two ways at a 12% base
  rate needs ~1,437/arm for +30%. Untestable. The best-converting surface is also the smallest.
- **Per-user RATE metrics are the only well-powered shape**: ~1,350 users/arm/month against a
  mean of 7.1 detects ~20% in about three weeks. `experiments.yml` already models this as
  `rate: {event, per: user}`.
- Broadening to ALL paywall surfaces (~1,000 impressions/mo) makes a click-through test read in
  ~5-6 weeks; `paywall_context` still allows a per-surface breakdown afterwards.

**Always compute power from the SURFACE's real volume, not sitewide traffic.** The runbook's
tables assume everyone entering the funnel is bucketed, which is never true of a paywall.

## Architecture, in one line each

- **FETCH EAGERLY, EVALUATE LAZILY.** `init()` waits for assignments (fires nothing); a Mixpanel
  flags getter is called only at the surface under test. Breaking this burns the 5,000/mo MEU
  allowance against ~3,449 monthly uniques AND dilutes a sample that yields ~13 purchases/week.
- Adding an experiment = one entry in `web/tulle-flags.js` + one in `experiments.yml`. No other
  file should ever change.
- `tulle.surface(name)` is the single call site: it fires `surface_shown` plus the exposure for
  every flag registered against that surface.
- **The report and verify scripts key off Mixpanel's own `$experiment_started`**, not the custom
  `experiment_exposure` event. `experiment_exposure` exists because it carries `variant_source`
  and `flag_resolution` — the two properties that let fallback-served and timed-out users be cut
  from the analysis.
- Backend branching is a WRITTEN SPEC ONLY (`docs/xano-experiment-spec.md`). **Xano has no
  experiment infrastructure at all** — no table, endpoint, function or column. A client-side
  test needs none.

## Fixed 2026-09-06

`evaluate()` never emitted `experiment_exposure`, so a flag with an empty `surfaces` array —
the smoke test, and every future backend-driven experiment — fired no exposure, contradicting
both `tulle-flags.js` and the Xano spec §8. Now `evaluate()` calls `exposureOnce()` for a
surfaceless flag, and **`exposureOnce()` claims its guard BEFORE evaluating** — otherwise the
nested call emits a second event for the same read. An in-memory guard sits alongside the
sessionStorage one so dedupe also holds when storage is blocked. Tests 24 → 31; new
`.github/workflows/js-tests.yml` runs them and fails if `weweb-custom-code.html` is stale
against its two sources.

## Traps recorded in the code, worth not rediscovering

- **Never write a literal HTML closing tag anywhere in `tulle-analytics.js`, comments included.**
  WeWeb splices head code at the closing head tag; one in a comment ended the head early and
  rendered the module as visible page text. Broke production 2026-09-01. `build-custom-code.py`
  now hard-fails on it.
- **Do not pin the Mixpanel SDK by guessing a filename on Mixpanel's CDN** —
  `mixpanel-2-2.82.0.min.js` 404s, and a 404 there is SILENT (the stub keeps queueing, nothing
  sends, the page looks fine). Pin via `cdn.jsdelivr.net/npm/mixpanel-browser@2.82.0`.
- **Simplified ID Merge:** one `mixpanel.identify(userId)` and nothing else. `alias()` and
  identifying at init both set `errAnonDistinctIdAssignedAlready` on every event while appearing
  to work. `tulle_did` is a local debugging key that is never sent.
- `node --test web/tulle-analytics.test.js` — **the path matters**; directory mode tries to
  execute `tulle-flags.js`, which references `window`.

## Deploy verification (do these in order)

1. Console on a cold load: `tulle.debug()` → `fetched:true`, `resolution:'network'`, and
   **`variants` MUST be empty**. Non-empty = evaluation moved to init = money and power gone.
2. `tulle.variant('smoke_test')` → a variant, `sources` entry `network` not `fallback`.
3. Mixpanel receives `$experiment_started` AND `experiment_exposure`; super property
   `experiment_smoke_test` set on later events. Reload does not re-fire; a new tab does.
4. After 24h `python scripts/verify_instrumentation.py` exits 0 (check 7 = MEU burn).
5. Retire: drop the `smoke_test` entry, archive the flag, regenerate, re-paste.

**Unverified prerequisite:** the runbook requires a version-polling refresh banner before any
real experiment, so a killed treatment cannot live on in a cached bundle. App workflow
`ac00a6bd-…` does poll `/app-version` into `latest_version` every 5 min — but **no element was
confirmed to render a banner off it**, and the poller's `wwLib.wwVariable.updateValue('latest_version', …)`
passes a NAME where a uid is usually required, so the 5-minute updates may silently no-op (the
initial `update-variable` action uses the proper uid and does work). Not blocking for a smoke
test that changes no UI; blocking before the first real experiment.

Related: [[project_mixpanel_tracking_gaps]], [[reference_weweb_mcp_limits]],
[[project_cohort_analytics]], [[reference_access_entitlement_model]].

## FIRST REAL EXPERIMENT BUILT — `pricing_anchor_strikethrough`, 2026-09-14

**Everything is wired and verified EXCEPT two manual steps: paste the head block, publish
WeWeb, then LAUNCH the Mixpanel experiment (it is in DRAFT).** Launch last — `endAfterDays`
counts from launch, so launching before the publish burns the window on zero traffic.

**Production health, measured 2026-09-14 (supersedes the "100% timeout" panic above):**
`flag_resolution` over 7d = **network 834 / timeout 277 / undefined 60**. The "wait for the
flags API to appear" fix is live and working for ~75% of users. **The remaining 25% timeout
is the open problem** — they get the fallback (control) with no real assignment and must be
cut via `variant_source` before reading results. They skew slow-connection, so the cut is
not neutral; report it both ways.

**What the test does.** Control = three plain prices on the `/pricing` cards. Treatment =
the former price struck through beside each: $40->$30, $75->$55, $199->$129. User confirmed
2026-09-14 these are prices actually charged before (an earlier answer said otherwise; the
later statement stands).

**The exact wiring — four pieces:**
1. `web/tulle-flags.js`: `{ key: 'pricing_anchor_strikethrough', surfaces: ['pricing'], type: 'gate' }`.
   `smoke_test` entry REMOVED here and the Mixpanel flag ARCHIVED.
2. Mixpanel experiment `e3ce940d-1f5f-4e37-8eab-2e67f0ecf8ca`, flag `c072519c-7ff1-4ae2-a9f7-f5e29cc5cea5`,
   key `pricing_anchor_strikethrough`, variants control/treatment 50/50, sequential, 35 days,
   excludeQA, SRM on. **Created with `rollout: []` (empty = nobody).** Had to
   `Update-Feature-Flag {ruleset:{rolloutPercentage:1}}` to get
   `rollout_percentage: 1` with an EMPTY `cohort_definition` — that is what avoids the
   smoke_test "New User" cohort trap. **Always re-read the flag after Create-Experiment
   and check the rollout block.**
3. WeWeb variable **`abPricingAnchorOn` `007ee77f-2185-4423-bda9-6cdee625d83c`** (boolean,
   default false) + page-onload workflow **`ab01f1c7-9f2a-4e55-9d3b-2ab6f0c51a31`**
   "AB - Pricing anchor exposure" on Membership `bc8cb393`: awaits `tulle.ready`, calls
   `tulle.surface('pricing')`, writes the resolved gate into the variable.
4. Three text elements bound to that variable's conditional rendering:
   `ddafd147` ($40), `c4e82d3e` ($75), `4cf7829b` ($199), all in card grid `76c71e54`.
   Same uids serve desktop and mobile.

**THE DESIGN POINT WORTH KEEPING: do NOT bind a conditional-rendering formula directly to
`window.tulle.isOn(...)`.** A WeWeb formula has no reactive dependency on `window`, so if it
evaluates before assignments land it latches to control and never re-renders — silently
serving control to treatment users and destroying the test while looking fine. Always route
the flag through a WeWeb VARIABLE set by a workflow that awaited `tulle.ready`. Everything
fails safe to control (no strikethrough), because showing a former price is a pricing claim.

**Power, from real data (`viewed_page` filtered to `/pricing` -> `membership_purchased`):**
1,654 unique /pricing viewers / 30d, 126 purchasers = **8.0% base**, ~827 per arm per month.
Read date 2026-10-19 (5 weeks, ~965/arm) powers only about **+48% relative**. A null result
then means "no LARGE effect", NOT "no effect"; +30% needs ~2,273/arm, i.e. run to December.

Paste-ready head block with real tokens: `.claude/weweb_backups/weweb_head_PASTE_ME_2026-09-14.html`
(`.claude/weweb_backups/` was NOT gitignored despite an earlier note here — a rule was added
to `.gitignore` on 2026-09-14). `build-custom-code.py` resets the tokens to placeholders every
run, so regenerate THEN re-fill, and read the console right after publishing.

### LAUNCHED 2026-09-14 05:15 UTC — and two launch-time traps

Head block pasted and published; verified live from the browser on `/pricing`:
`typeof mixpanel.flags` = `'object'`, `resolution:'network'`, **`assignmentMs: 801`**,
`registry` = 1 entry, `exposed: ['pricing_anchor_strikethrough']`. On a page that is NOT
`/pricing`, `exposed` is `[]` — correct, and a useful reminder that the surface call is
per-page. (First console check was run on the wrong page and looked like a failure.)

**TRAP 1: `Update-Experiment action:"launch"` does NOT enable the linked flag**, despite the
tool description saying "Enables linked flag." After launch the experiment read `status:
active` while the flag still read `status: "disabled"`, `enabled_at: null`. Left alone this
runs the `endAfterDays` clock while serving nothing to everybody. **Always re-read the flag
after launching and, if needed, `Update-Feature-Flag {status:"enabled"}` separately.**

**TRAP 2: `Create-Experiment` silently drops the `metrics` array** if the create is retried
with a trimmed payload — the experiment launched with NO guardrail and Mixpanel returned a
`missing_guardrails` warning in `validationFindings`. Re-add with `Update-Experiment
{metrics:[...]}`; `validationFindings` goes `null` when clean. **Read `validationFindings`
on every create and launch.**

Mixpanel saved metrics only support `unique|total|session|dau|wau|mau` — **there is no
sum-of-property math**, so revenue-per-visitor CANNOT be a Mixpanel metric. The revenue
guardrail lives in `experiments.yml` for the repo report script; the Mixpanel guardrail is
`clicked_to_pay_for_lifetime_access` (unique, up) as a proxy for mix-shift off Forever.

A flag's per-flag `sources` entry reading `'fallback'` while `resolution` reads `'network'`
is the exact signature of **flag fetched fine, this flag not being served** — i.e. disabled
or not in the rollout. Two different fields, two different failures; check both.

## 2026-09-14 (evening): THE EXPERIMENT IS SERVING 100% CONTROL — SRM CATASTROPHE

Measured same-day, ~20h after launch: `experiment_exposure` broken down by `variant` =
**control 53, treatment 0**. Broken down by `variant_source` = **network 48, fallback 8** —
so Mixpanel genuinely DECIDED and returned control every single time. This is not a
fallback/timeout artifact and not a rendering bug: the flag is enabled and serving, but the
variant allocation never splits.

**Consequence: the strikethrough has been shown to NOBODY.** `/pricing` has rendered
identically to the pre-launch page for 100% of users since launch. Any same-day revenue
change therefore CANNOT be caused by the treatment — a useful elimination, and the reason
to check the arm split before blaming an experiment for a business metric.

Same signature as `smoke_test` on 2026-09-06 (`tulle.variant('smoke_test')` -> `'control'`).
Two for two, so treat "flag enabled + rollout set + always control" as the DEFAULT failure
mode of this setup, not a surprise. Suspect the experiment's variant allocation is not
actually linked to the flag's ruleset; `rollout_percentage: 1` alone does not produce a split.
**Re-read the flag AND assert a non-zero treatment count within an hour of launch** — the
35-day `endAfterDays` clock runs from launch and is currently being burned on an unusable
100%-control sample.

Diagnostic that settles it in one call, cheaper than any code reading:
`Run-Query insights experiment_exposure, breakdowns [{metric:{type:'property',propertyName:'variant'}}]`
(note: `resourceType` is NOT a permitted field inside the breakdown metric).

Related: [[project_tulle_focus]].

## MALFORMED FLAGS REQUEST — caught in a live browser 2026-09-14 20:49

On `/pricing`, signed in, incognito, the console shows TWO failing flag fetches per load:

```
GET https://api-js.mixpanel.com/flags/?context=%7B%22distinct_id%22%3A29566%2C%7D&token=[redacted]
net::ERR_FAILED 400 (Bad Request)
Access to fetch ... blocked by CORS policy: No 'Access-Control-Allow-Origin' header
```

`context` decodes to **`{"distinct_id":29566,}` — a trailing comma, so it is invalid JSON** and
Mixpanel rejects it with 400. As recorded earlier in this file, a rejected Mixpanel request
carries no CORS header, so **the CORS error is a SYMPTOM, not the cause** — do not chase it.
The real defect is whatever serializes that `context` object emitting a dangling comma.

This is the best available explanation for the 100%-control result above: if the flags call
400s, nothing can be served and every read falls back to control. **Note the contradiction not
yet resolved:** `experiment_exposure` still recorded `variant_source: network` for 48 of 56
events today, which should be impossible if every fetch 400s. Either the SDK's second call
sometimes succeeds, or `variant_source` is being reported wrongly. Settle that before trusting
either field.

**What was RULED OUT the same evening, all verified in a live browser — do not re-investigate:**
the `/pricing` page renders correctly (all three cards, correct prices); the three buy buttons
are `disabled:false` / `opacity:"1"` for a hydrated user; clicking reaches a correct Stripe
checkout at the right price on BOTH desktop and iPhone SE. The purchase path is intact.

**Separate, pre-existing, still open:** the 1-week Stripe link in table 17 (row id 1) is
`...?prefilledemail =` — a space before the `=`. So the param is named `prefilledemail ` and
**Stripe never prefills the buyer's email**; the checkout renders with an empty email field.
`wwFormulas.trim()` around the concatenation cannot fix an interior space. Rows 2/3 end with a
trailing NEWLINE instead. This is Xano data, untouched by any WeWeb publish.

**Attribution trap that cost time:** distinct_id `29566` in Mixpanel is the OWN TEST ACCOUNT
`[email]`, not a customer. A session replay showing a 14-second
`/pricing` navigation and an immediate bounce was that test, and was briefly misread as
customer behaviour. **Check a distinct_id against `client_reference_id` in the Stripe URL
before treating a replay as a real user.**

**Epistemic limit worth remembering:** `surface_shown` is the only true count of `/pricing`
loads, and it did not exist before 2026-09-14 — so "pricing traffic is normal today" CANNOT be
established from it. Arrival counts derived from `clicked_membership_for_pricing_*` assume
those CTAs navigate rather than open a modal, which is unverified for the
`venue_pricing_intelligence` one (21 of today's 44).

## ROOT CAUSE OF THE 100% CONTROL — the flag was NEVER ENABLED (2026-09-15 00:57 UTC)

`Update-Feature-Flag {status:"disabled"}` returned the full flag, and it read **`enabled_at: null`**.
The flag had never been switched on at any point since creation. So every read fell back to
control, for every user, for the whole run. **The speculation written above — "the variant
allocation never splits" — is WRONG; disregard it.** The ruleset was correct all along:
control/treatment `split: 0.5` each, one rollout group with `rollout_percentage: 1` and an empty
`cohort_definition`. It would have split 50/50 the moment it was enabled.

This is TRAP 1 from the launch notes recurring: `Update-Experiment action:"launch"` does not
enable the linked flag, and the note claiming it had been enabled separately was not true of the
live object. **`enabled_at` is the field that settles it — `status` alone is not enough, because a
never-enabled flag and a deliberately-disabled one both read `disabled`.** Check `enabled_at`
after every launch.

Flag set to `disabled` deliberately on 2026-09-15 at the user's instruction. The experiment
`e3ce940d` is still `active` and still burning its 35-day `endAfterDays` clock on a run that
served nothing — stop it before it silently "concludes".

## DUPLICATE FLAG + CLEANUP PERFORMED 2026-09-15 01:02 UTC

`Create-Experiment` was retried on 2026-09-14 and created **TWO flags 0.87s apart**, both
named "Pricing anchor strikethrough":
- `c072519c` key `pricing_anchor_strikethrough` (04:57:43.390) — the real one, bound to
  experiment `e3ce940d`, 48 evaluations on launch day. This is the key `tulle-flags.js` registers.
- `f1c3982e` key `pricing_anchor_strikethrough_Zn2LiG` (04:57:42.520) — ORPHAN,
  `experiment_id: null`, 0 evaluations, never enabled.

Mixpanel auto-suffixes a colliding flag key, so **a retried Create-Experiment silently leaves a
duplicate flag behind. Always `List-Feature-Flags` after a retry** — the same retry is what
dropped the `metrics` array recorded above.

**THE SPLIT-BRAIN WORTH REMEMBERING.** The experiment's own `exposures_cache` read
`treatment: 20, control: 12, $overall: 32` while our `experiment_exposure` events read
**control 53, treatment 0**. Mixpanel's engine bucketed users into treatment; the client read
control for every one of them and rendered no strikethrough. So "Mixpanel says the arms are
balanced" and "the app served both arms" are INDEPENDENT claims — check both. The experiment
object also had **`variants: []`** (empty) even though the flag's ruleset carried
control/treatment 50/50; same retry damage as the metrics array.

**Before any relaunch, fix:** (1) the empty `variants` array, (2) the client-reads-control /
server-says-treatment disagreement. **Add a launch assertion: `enabled_at != null` AND a
non-zero treatment count within one hour.** That single check catches every failure in this file.

Cleanup done at the user's instruction: flag `c072519c` → disabled; flag `f1c3982e` → archived;
experiment `eb0abbd7` ("Smoke Test Experiment", a draft shell from 2026-09-06 that the earlier
note wrongly said did not exist) → archived; experiment `e3ce940d` → **concluded** with a message
recording that the run is invalid and not a result.

## EVERY identify CALL SITE IN THE APP — audited 2026-09-15

The malformed flags request (`{"distinct_id":29566,}` -> 400) is caused by
`mixpanel.identify(<number>)` being called DIRECTLY from WeWeb workflows, which
`web/README.md:338` forbids. `tulle.identify()` already does `String(userId)` correctly
(`web/tulle-analytics.js:450`) — the app was simply bypassing it. Full inventory:

| Where | Element / workflow | Action | State |
|---|---|---|---|
| Sign In (`e96e1872`) | form `76288cba`, "Log In" `8561fd53` | `7d105ac1` | FIXED 2026-09-14 -> `window.tulle?.identify` |
| Sign In (`e96e1872`) | same workflow | `129b9934` | `chmln.identify` — DELETE, it is the only real Chameleon call left |
| Sign Up (`4a9de110`) | form `93b78c12`, "Sign Up" `14b28b0a` | `4578283e` | **STILL NUMERIC `mixpanel.identify`** |
| Sign Up (`4a9de110`) | form `1b21a127`, "Sign Up v2" `f7e97a41` | `4578283e` | already correct (`window.tulle.identify`) |

**The Sign Up page has TWO form containers** (responsive twins, both `submit`), so every
signup change must be made twice. Note both use the SAME action id `4578283e` in different
workflows — do not assume one edit covers both.

**Chameleon:** exactly ONE real call app-wide (Sign In `129b9934`). The only other mention is
the Admin Mirror Banner app workflow `7c4e1a90`, guarded by `if (win.chmln)`, which safely
no-ops once the loader is gone. **Sign Up has none.**

**Two defects in "Sign Up v2" action `4578283e` worth fixing while in there:** it ships four
`console.log('[MP DEBUG] ...')` lines to production, and its `else` branch skips the whole
block when `window.tulle` is missing — so a failed module load silently drops `signed_up`
entirely rather than falling back to `mixpanel.track`.

**CORRECTION to the Chameleon note in commit 04bb9be:** that message claims the `chmln.identify`
throw "aborts every action after it in that workflow". It does NOT — tracing `8561fd53`, that
action is TERMINAL (`next: null`) and the post-login redirect already fired five actions
earlier. The throw is console noise, not a functional break. Do not treat it as a revenue lead.

### Verified state 2026-09-15 01:4x UTC (supersedes the table above)

- Sign In `76288cba` / `7d105ac1` — FIXED, routes through `window.tulle?.identify`.
- Sign In / `129b9934` (`chmln.identify`) — DELETED. `7d105ac1` is now terminal (`next` absent)
  and no action references the removed id, so the chain is intact.
- Sign Up **v2** `1b21a127` / `4578283e` — FIXED, and the `[MP DEBUG]` console.logs are gone.
- Sign Up `93b78c12` / `4578283e` — **STILL NUMERIC, outstanding.**

**Tell the two signup forms apart by WORKFLOW NAME, not action id:** `1b21a127` = "Sign Up v2"
(its action is titled "MixPanel Identify Call"); `93b78c12` = "Sign Up" (its action is unnamed).
They share the action id `4578283e`, which is exactly how an edit meant for one landed on the
other.

## 2026-10-07: NEVER paste the generated weweb-custom-code.html over the live Head
The live WeWeb Head ALSO holds the auth `#app:empty` splash and the app_ready load-timing snippet, which the
generator does NOT include - a wholesale paste deletes them. Read the live head from the published HTML
(`curl https://www.tulletogether.app/sign-up`, everything before `</head>`) and give the user a minimal edit.
Registry swap done as a ONE-LINE edit: `{ key: 'pricing_anchor_strikethrough', ...}` -> `{ key:
'signup_venue_preview', surfaces: ['signup'], type: 'gate' }` (framework code otherwise identical, fdfe631).
signup_venue_preview Mixpanel flag id 3394bf2c-6165-42e5-894f-964289ffdd44 - DISABLED until that head edit is live.
- 2026-10-07 LATER: user dropped the A/B - the sign-up venue preview ships to EVERYONE. Block 17afd7e8 conditional
  rendering removed; wf 8e3f6b21 gate = state picked only (backup weweb_backups/signup_preview_pre_focusfix_2026-10-07.md).
  Head-code one-line edit NOT needed; Mixpanel flag signup_venue_preview stays DISABLED (no experiment).
  experiments.yml on branch signup-venue-preview still lists it as LIVE experiment - stale, fix if that branch is merged.
  User redesigned the block in the editor (headline "Browse Venues", headline+select in one row, source line c439dec2
  "real pricing PDFs and contracts... Sign up free to open them" DELETED). Measure: before/after /sign-up -> signed_up
  (Mixpanel funnel; compare whole weeks, day-of-week swings).
- 10-07: carousel -> TWO venues (user), all devices: repeat e39a3d4e slice(0,2), cards calc(50%-5px) side by side,
  "...and N more" (total_in_state - shown) full-width line below; 3rd skeleton hidden. 2x2 via display:contents FAILED
  (3rd card overflowed the hero panel) - don't retry it. Block now sits in the LEFT hero panel (user moved it).
