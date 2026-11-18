---
name: project_free_views_survive_deletion_2026-10-24
description: "LIVE 2026-10-24 + 11-01 GMAIL FIX: table 88 deleted_account keyed by fn157 auth/email_key (gmail dots/+tag stripped before sha256) written by ep20, read by ep2/ep7; abuser l.b.vannostrand rotated dot variants (22 deletes), canonical row 22 = 0 views forever"
metadata:
  node_type: memory
  type: project
  originSessionId: abb664ba-4018-4524-a407-838ba2ba7236
  modified: 2026-10-24T15:23:13.385Z
---

Owner asked 10-24 after one user deleted + re-signed-up 19 times for fresh free views (found via the
"Tulle Users Deleted" Google Sheet pivot that ep20 appends to; current acct 33915).
**LIVE 10-24 ~15:20-15:22 UTC:** table 88 `deleted_account` (email_key = sha256(trim(lower(email))), Xano
`|sha256` == Python hexdigest, verified); ep20 upserts before db.del (min views, OR bonus, count+1);
ep2 (now lowercases email - mixed-case dupes now refused) + ep7 new-user branch start at stored views and
set bonus_views_at if bonus used. ALL THREE wrap the new logic in try_catch so a failure never blocks
deletion/sign-up. Backfill rows 3 (19 deletes) + 4 (8 deletes), 0 views, bonus used.
**Incident:** first ep20 push WITHOUT try_catch returned 500 "Invalid name: mvpw1:0" for ~70s
(15:17:43-15:18:55); rolled back; only my curl test was hit. Same logic ran fine 2 min later in a scratch
function and in the live endpoint -> likely the new table not yet visible to the API runtime right after
addTable (UNVERIFIED cause). Lesson: wait / test before pointing a live endpoint at a brand-new table.
**Verified live:** ep20 add + edit paths, ep2 new email = 3 views, ep2 re-signup = carried views (1) +
bonus_at set. **UNVERIFIED:** ep7 new-user path (needs a real Google sign-up; check request history).
Not covered: Gmail dot/+alias variants, ep5/ep6/magic link, and ep145/ep21 still let a signed-in user
PATCH their own FreeViewsRemaining (browser-side enforcement) - parked, owner not yet asked.
Test rows/accounts cleaned up; offboarding Google Sheet got ~5 test rows (fvtest+1024*@tulletogether.com).
Backups: .claude/xano_backups/api{20,2,7}_prepush_*_free_views_survive_2026-10-24.xs

**11-01 GAP FOUND (owner: "this girl keeps joining and deleting"):** same abuser rotates GMAIL DOT VARIANTS -
l.b.vannostrand (row 3, 20 deletes, 0 views), lbvannostrand (row 22, deleted 10-27), l.bvannostrand (row 27,
deleted 11-01 22:19 UTC as user 34393, IP 24.91.230.38 Comcast, macOS Chrome). Gmail ignores dots/+tags, sha256
does not -> each variant = 3 fresh views. PROPOSED (awaiting owner yes): one Xano function email_key that strips
dots + "+tag" for gmail.com/googlemail.com before sha256, used by ep20 (group 1) + ep2 (group 1) + ep7 (Google
sign-up, NOT in group 1 - find it); backfill row 22 (= the normalized key) bonus_used=true, delete_count 22.
IP block NOT recommended (residential/dynamic, household could still pay); offered: log sign-up IP + brief alert.
Sign-up (ep2) currently ignores IP entirely. Backups needed per CLAUDE.md 12.5 before pushing.

**11-01 23:46 UTC LIVE (owner "yes push the gmail fix"):** fn157 `auth/email_key` {key, normalized} created +
verified against Python sha256 on 5 inputs (gmail +tag/dots/googlemail collapse; outlook/empty/no-@ unchanged).
ep20/ep2/ep7 now `function.run "auth/email_key"` inside the existing try_catch (pushed via .claude/xano_push.py,
only diff = blank-line whitespace). Backups: .claude/xano_backups/api{20,2,7}_prepush_*_gmail_key_2026-11-01.xs
(+ api*_prepush_api*_... duplicates written by the helper). **Verified live:** ep2 sign-up 200 -> ep20 delete wrote
row w/ expected key -> row set to 1 view/bonus used -> mixed-case re-signup got FreeViewsRemaining=1 + bonus_views_at
(user 34854; test user + table-88 row 28 cleaned up). **UNVERIFIED:** ep7 Google path (same pattern, needs a real
Google sign-up). Backfill: row 22 (lbvannostrand = canonical key) bonus_used=true, delete_count=22; rows 3/27 are now
dead keys, left for history. Still open by owner choice: no IP logging/blocking; a non-Gmail address still gets 3 views.
