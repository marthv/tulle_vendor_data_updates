---
name: project-favorites-wipe-ep109
description: 2026-08-04 SHIPPED ep109 v2 - favorites reconcile guard; WeWeb writes the WHOLE favorited_vendors array so a stale client wipes it; ep158 toggle_favorite exists but has NEVER been called
metadata: 
  node_type: memory
  type: project
  originSessionId: 6b270b6b-d5ed-4b28-b2aa-3c25ba9a263e
  modified: 2026-08-04T05:43:53.622Z
---

Saved vendors vanishing (Brittany, user 19442, lost 21) = **WeWeb PATCHes the entire
`favorited_vendors` array** from its local copy of the user object to ep109
`/user/update_user_attributes`. Any client staleness (reload, auth refresh, 2nd tab) becomes the
new truth. Her log: 21 vendors → `["V48"]` → `[]` in 11 minutes.

Two defects, both fixed in **ep109 v2** (2026-08-04, backup
`.claude/xano_backups/ep109_v1_pre_favorites_reconcile_2026-08-04.xs`):

1. The old guard was a no-op — `$input.favorited_vendors|first_notnull:$input.favorited_vendors`
   fell back to **the input itself**. Omitted list input resolves to `[]`, so any profile save or
   onboarding call erased favorites. (ep21 `update_record` had it right all along.)
2. Whole-array replace is unsafe. Now **reconciled** via one `api.lambda`: empty/omitted → keep
   stored; ≤1 removal → accept (normal toggle); **≥2 removals → write the UNION**. The UI can only
   toggle one vendor at a time, so ≥2 removals can only be a stale client.

Also fixed in the same pass: `Wedding_Date` and `Wedding_Budget` had **no fallback at all**, so
every favorites-only call nulled the date and zeroed the budget. That is why so many user rows have
empty wedding fields — it was not users skipping the form.

**Use `first_notempty`, never `first_notnull`, for lists/ints here** — `[]` and `0` are empty but
not null, and onboarding sends `favorited_vendors: []` explicitly.

## The real fix, not yet done (needs WeWeb)

**ep158 `POST /user/toggle_favorite` already exists, takes only `vendor_id`, does the
read-modify-write server-side — and has ZERO requests in its entire history since 2026-03-31.**
Built and never wired up. Point the hearts at it and delete the whole-array PATCH; the v2 guard is
a backstop, not a cure. Known hole in the guard: a client stale by several *adds* that then
unfavorites one reads as multi-removal and gets unioned, so that removal is silently ignored.

## Gotchas found while doing this

- **ep109 request-history retention is only ~24h** (437 records covering 2026-08-03 06:43 →
  2026-08-04 05:41). Damage older than a day is not recoverable from the log — sweep fast.
- Sweep result: Brittany was the only confirmed loss in the window. 103 "at-risk" ids were almost
  all a contiguous block of same-day signups (25064–25169) in onboarding with nothing to lose.
- To smoke-test an authed endpoint: create a throwaway row via `addTableContent` (metadata API
  writes don't fire triggers), then `POST /impersonate {target_user_id, current_user_id}` with an
  **Admin-role** user as `current_user_id` (e.g. 118) to mint a 24h token. Delete the row after.

See [[reference_access_entitlement_model]], [[reference_xano_mcp_quirks]].

## 2026-10-03: ep158 FIXED + first caller
ep158 had never run; live test showed 'toggle off' PUSHED a duplicate - unwrapped `|lambda_filter:$$==$input.vendor_id`
matches nothing. Fixed with backtick `|filter:($$ == $input.vendor_id)` (apply_ep158_fix.py; snapshot
ep158_toggle_favorite_snapshot_2026-10-03.xs, --restore). Verified add once / remove all copies / other user fields
untouched. Planning Assistant heart (WeWeb wf b8a1e301) switched to ep158 = its first real caller. Discovery and
other hearts STILL use the ep109 whole-array PATCH - switch them too when touching those pages.
