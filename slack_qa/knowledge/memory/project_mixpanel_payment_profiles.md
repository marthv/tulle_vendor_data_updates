---
name: project_mixpanel_payment_profiles
description: "2026-09-24 Mixpanel USER PROFILE properties for payers + refreshed signup props - ep30 sets them live, .claude/mixpanel_profile_backfill.py seeded history"
metadata:
  node_type: memory
  type: project
  originSessionId: f6b3a600-b10a-4933-976b-8c9142c30e73
  modified: 2026-09-28T01:58:30.682Z
---

Mixpanel (project 3963130) had purchase EVENTS but no profile property saying who paid.
distinct_id everywhere = `String(user.id)`.

**Profile props (payers):** has_paid, forever_access, access_until (2999-12-31 = forever), last_plan
("forever" normalised from "forever weeks"; legacy "database" = 52 old payments, meaning unknown),
first_paid_at ($set_once), last_paid_at, payment_count + lifetime_spend ($add in ep30, ABSOLUTE in
backfill), bought_roadmap, last_payment_method, used_bnpl (only ever flips ON).
**Signup props (everyone)** reuse the CLIENT's existing names: $email $name email wedding_date
wedding_location (comma-joined states) wedding_guest_count wedding_budget planning_phase age source
peak_season major_city vendor_or_planner, + new signed_up_at. Client sets them once at identify and never
again, so the backfill is the only refresh. Blank DB values are skipped; wedding_date is $unset when blank
(profiles carried literal "undefined").

**ep30 SHIPPED 2026-09-24** (`ep30_mpprofile_2026-09-24_as_stored.xs`; rollback
`ep30_live_pre_mpprofile_2026-09-24.xs`, apigroup 7): /engage call after each purchase track, in
try_catch, AFTER access is granted. **Every op carries "$ip":"0"** or Mixpanel geolocates the profile to the
server and overwrites $city. xano_push reported MISMATCH only because Xano REFORMATS (splits `|set`
chains onto lines, drops parens) - semantically identical; diff before panicking.
**VERIFIED LIVE 2026-09-28**: 15 real payers since 09-25 have last_paid_at/last_plan/payment_count set by
ep30 (e.g. 28530 "1 week" matches table 16 row 2383; 5232 payment_count 3). Query: $all_events unique,
user filter last_paid_at "was since" date, breakdown $distinct_id + user props (resource:"user").

**Backfill ran 2026-09-24**: 27,683 users, 1,064 payers from payment log table 16 (1,299 rows, 9
unmatched). 54,951 ops, all status 1. Re-runnable (absolute values). **Page Xano meta search SORTED by
id** - unsorted paging silently dropped ~1/3 of users and made 359 payments look orphaned.
Mixpanel property lookups lag a few minutes after an engage write - empty result right after is not failure.
