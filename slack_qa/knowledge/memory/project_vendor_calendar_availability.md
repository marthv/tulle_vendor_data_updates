---
name: project_vendor_calendar_availability
description: "Vendor calendar availability — Google OAuth (free/busy) is now the main path, .ics is the fallback. Busy = 4h+ of a day. Not couple-facing. Xano is at its 10-task cap."
metadata:
  node_type: memory
  type: project
---

Vendors paste the **secret .ics address** of a calendar they already keep; we read
**busy dates only** and store them. Shipped 2026-08-18 (portal `d959e3f`, dashboard `9adb531`).

## The product boundary came from Slack, not from me
- Des: *"I think the cal wouldn't be for booking just to show availability"* — so it is
  read-only free/busy and **never** a booking or intro-call surface.
- Des: *"if someone is set on a sat in may you can easily see if they're even an option…
  especially useful for photographers and musicians"*.
- Kate raised the discouragement risk (vendors demotivated when nothing books); Des's
  clarification resolves it, and v1 being non-couple-facing removes it entirely.

## Decisions and why
- **iCal, not Google OAuth.** OAuth needs a sensitive-scope consent screen: unverified apps
  cap at 100 users behind a warning, and verification is an external review of unknown
  length. .ics needs none of it and covers Apple/Outlook too. `vendor_calendar.provider`
  (`ics` | `google`) is the seam for OAuth later.
- **Not couple-facing in v1.** A wrongly-connected personal calendar is then a fixable
  embarrassment, not a public one.
- **Open to any claimed vendor.** So a multi-space venue can be "busy" and still available —
  the `represents` column captures intent. **Do not surface venue availability to couples
  without solving that.**

## Why this does not break the append-only invariant
ep247 is "the only write a vendor has" because **pricing** feeds percentiles and benchmarks
and venues have an incentive to misreport it. Availability has no derived analytics and
misreporting it only costs the vendor bookings, so it is vendor-authoritative and lives
outside the review queue. It writes to no live table (11/36/62/63). `busy_dates` is a derived
mirror, replaced wholesale each sync — there is no assertion to preserve, unlike a submission.

## Where it lives
| Piece | Where |
|---|---|
| Table | **70 `vendor_calendar`**, unique on (vendor_account_id, vendor_id) |
| Endpoints | **260** POST connect · **261** GET (self-scoped) · **262** disconnect · **263** `vendor_admin/calendars` · **264** `vendor_admin/calendar_sync` |
| Parser + SSRF guard | `tulle-vendor-portal/src/lib/ics.ts` (+ `ics.test.ts`, 18 tests, `npm test`) |
| Sync orchestration | `src/lib/calendarSync.ts`, route `src/app/api/cron/sync-calendars` |
| Vendor UI | `src/app/dashboard/calendar/page.tsx`, `src/components/calendarForms.tsx` |
| Ops view | `_render_calendars()` in `vendor_portal.py` |

## SECURITY — do not soften these
- **SSRF.** We fetch a user-supplied URL server-side. `assertSafeFeedUrl` requires https,
  resolves the host and rejects loopback / link-local / RFC1918 / CGNAT **including the
  `::ffff:` IPv4-mapped form**, and re-checks **every redirect hop** — validating only the
  first URL is useless when the attacker owns the redirecting server. `169.254.169.254` is
  the one that matters. Tested.
- **`ics_url` is a bearer credential** for the vendor's whole calendar. It is absent from the
  `VendorCalendar` type and from every vendor-facing output list, so it cannot leak by
  accident. Only ep263 returns it, only with `include_urls=true`. Never log it.
- The "we never see event titles" promise is enforced by **never reading**
  SUMMARY/DESCRIPTION/LOCATION/ATTENDEE.

## Parser traps already paid for
- **All-day `DTEND` is EXCLUSIVE.** Getting it wrong adds a phantom busy day to every booking.
- **`TRANSP:TRANSPARENT` and `STATUS:CANCELLED` are not bookings.** Google's public holiday
  feed is 317 events, *all* transparent — the correct output is zero busy dates. Flipping them
  to OPAQUE yields 43, which is how the parse path was proven rather than just the filter.
- Recurrence is expanded by `ical.js`. **Do not hand-roll RRULE**, and this is why parsing
  runs in Node rather than a XanoScript lambda.
- `node --test` uses strip-only TS: **TypeScript parameter properties are a hard error**
  there, hence `IcsError` assigns `code` in the body.

## Two bugs found by testing the failure path, both fixed 2026-08-18
1. **`db.edit` wiped `busy_dates`/`busy_count`** on the error branch of ep264 even though
   neither was in its `data` map — 3 dates went to 0 after a synthetic failure while the text
   columns survived. Now `db.patch`. See [[reference_xanoscript_traps]].
2. **An errored calendar was never retried.** ep263's sync list filtered `status == "active"`,
   and a failure sets `status = "error"` — so one flaky fetch excluded a feed from every future
   run. Now `status in ["active","error"]`; only `paused` (disconnected) is excluded.

Neither would have shown up without deliberately failing a sync and re-reading the row. The
happy path passed cleanly throughout.

## The admin secret is committed in plaintext
`[token]` is **hardcoded in ep249's XanoScript**
and is the fallback default in `vendor_portal.py:62`. It gates the vendor review queue,
`roadmap_orders_admin` and `analytics_users_export` (the full user export). Now also set as
`ANALYTICS_EXPORT_SECRET` in Xano env (ep263/264 read it from there) and as
`XANO_ADMIN_SECRET` on the portal service. **User was shown this; rotation not yet done.**
Rotating means: new value in Xano env only, change ep249's literal to `$env.…`, drop the
`vendor_portal.py` fallback so a missing var fails loudly, update both Railway services.


## GOOGLE OAUTH — BUILT 2026-08-19, NOT YET SWITCHED ON

Built because the .ics flow asked a vendor to paste the exact string Google prints a red
warning beside, ~10 clicks deep, and the result was unrevocable. OAuth is 3 steps and the
vendor revokes it themselves at myaccount.google.com/permissions.

**Scopes: `calendar.freebusy` + `calendar.calendarlist.readonly`.** freebusy is the whole
point — Google returns busy ranges and CANNOT return titles/guests/locations, so the privacy
promise stops being our word and becomes Google's authorisation layer. It also applies three
rules we could never apply to .ics: events marked free, cancelled events, and **declined
invitations** are already excluded (honouring a decline from .ics needs ATTENDEE, which the
promise forbids). calendarlist is what lets a vendor PICK the calendar — bookings usually
live in a secondary one, so reading only `primary` would be the personal calendar.

| Piece | Where |
|---|---|
| Google lib | `src/lib/googleCalendar.ts` |
| Token encryption | `src/lib/tokenCrypto.ts` (AES-256-GCM, `CALENDAR_TOKEN_KEY`) |
| OAuth cookies | `src/lib/oauthState.ts` |
| Routes | `/api/oauth/google/start`, `/api/oauth/google/callback` |
| Calendar picker | `/dashboard/calendar/choose` + `GoogleCalendarPicker` |
| Xano | **ep265** connect-google; ep261/262/263 updated; table 70 + 4 columns |
| Setup recipe | `docs/google-oauth-setup.md` |

**The refresh token is ciphertext in Xano and Xano holds no key.** This is the real security
win: ep263 hands rows to the sync job behind the admin secret that is *committed in
plaintext*, so today one leaked string is a key to every vendor's calendar. ep265 has a
`starts_with "v1."` precondition that REFUSES a bare token. Every vendor moved off .ics is
one fewer plaintext key. Losing/changing `CALENDAR_TOKEN_KEY` forces every vendor to
reconnect — unrecoverable from the DB.

### Two traps already paid for
- **7-day refresh token expiry.** While the consent screen is *Testing* + *External*, Google
  revokes refresh tokens after 7 days — vendors would drop off silently a week after
  connecting, each on a different day. **Must be published to Production.** Sensitive-scope
  verification (form + demo video, days–weeks) is NOT the restricted-scope security
  assessment; unverified Production = warning screen + 100 *new* user cap, which is not close.
- **`prompt=consent` + `access_type=offline` is mandatory.** Google only issues a refresh
  token on the FIRST authorisation per client+account, so a reconnecting vendor would come
  back with a 1-hour access token and no way to refresh. `exchangeCode` refuses a grant with
  no refresh token rather than storing something that dies tomorrow.

## THE 549-DATES BUG — the parse was right, the DEFINITION was wrong

2026-08-18: a personal Google calendar reported **549 taken dates in 18 months** — literally
every day. Reproduced exactly: **one recurring 30-minute event** (FREQ=DAILY) marked every
day taken, because ANY event marked the whole date busy. A 30-min dentist appointment blocked
a Saturday in May.

**Fix: `src/lib/busyDates.ts` — a date is taken once ≥4h of it is occupied, summed across
everything on that date.** Both providers converge here so they can never disagree; a vendor
switching from .ics to Google must not watch their availability change for no visible reason.
Verified: that same daily-30-min feed now yields **0**, while a 9h wedding, an all-day
booking and two stacked afternoon blocks all still count.

Also fixed in the same pass: **VTIMEZONE components are now registered with
`ICAL.TimezoneService`**. Without it a `TZID=America/New_York` start was treated as floating
and read in the SERVER's zone (UTC on Railway), pushing evening bookings onto the next day.

`scripts/diagnose-ics.ts` fetches a feed and prints per-event contribution — run it before
theorising about a wrong date count. `--titles` is opt-in and local-only.

**DO NOT "fix" the threshold by ignoring recurring events instead.** freeBusy gives opaque
intervals with no recurrence information, so that rule is unexpressible on the Google path and
would split the two providers' behaviour.

## OPEN
- ~~No nightly job~~ **DONE.** Task **16 `sync_vendor_calendars`**, daily 05:30 UTC, calls the
  portal's cron route. Task 4 (a completed, inactive backfill) was deleted to free the slot —
  its source is at `tulletogether/.claude/xano_backups/task4_…_deleted_2026-08-18.xs`.
  **Xano is back at its 10-task cap: adding another means freeing another slot.**
- ~~Env vars not set~~ **DONE.** `cron_secret` + `ANALYTICS_EXPORT_SECRET` in Xano env;
  `CRON_SECRET` + `XANO_ADMIN_SECRET` on the portal service. Full chain verified end to end
  through the real cron route against a live Google feed.
- **Browser path walked by the user 2026-08-18** — connect → parse → dates rendered. The
  feature is confirmed working through the UI, not just by API.
- **Google OAuth is code-complete but INERT until the console work is done**: create the
  OAuth client, set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` /
  `CALENDAR_TOKEN_KEY`, and **publish the app**. Until all four vars are set the Google card
  does not render and the portal falls back to .ics — a deploy cannot half-enable it.
  Recipe: `docs/google-oauth-setup.md`.
  **Do NOT reuse the existing "Tulle Railway App" OAuth client** — that one is the ADMIN
  DASHBOARD's Google login (`GOOGLE_CLIENT_ID`/`SECRET` live on the *Tulle Admin Dash*
  Railway service beside `ALLOWED_EMAILS`; `dashboard.py:405` uses `_APP_URL` as its
  redirect_uri). Sharing it would couple secret rotation to the admin login and put the
  portal's redirect URI on the dashboard's client. Make a NEW client in the SAME project:
  consent screen, scope list, publishing status and verification are all PROJECT-level and
  shared, so the expensive part is done once. The `vendor-portal` service currently has only
  `CRON_SECRET`, `XANO_ADMIN_SECRET`, `XANO_VENDOR_BASE` — no Google vars at all.
- **A QA calendar is still connected** (account 1 / V425, pointed at Google's public US-holiday
  feed, 0 busy dates because they are all TRANSPARENT). Harmless, but it is QA data.
- Couple-facing surface, and the multi-space problem it depends on.

Related: [[project_vendor_portal]], [[reference_railway_admin_dashboard]],
[[reference_xanoscript_traps]].
