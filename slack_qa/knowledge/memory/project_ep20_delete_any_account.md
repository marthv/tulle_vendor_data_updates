---
name: project-ep20-delete-any-account
description: "ep20 DELETE /user/{user_id} let ANY logged-in user delete ANY account and exfiltrate their PII to a Google Sheet — found and fixed 2026-09-10. The August audit missed it because it is a DELETE, not a PATCH."
metadata: 
  node_type: memory
  type: project
  originSessionId: 48f611b8-9c31-47c9-a494-e7c30f6efa52
  modified: 2026-09-10T06:05:01.113Z
---

Found 2026-09-10 while scanning every authenticated writer for the admin view-as work — not by
looking for it. **ep20 `DELETE /user/{user_id}` (group 1, Authentication) was `auth = "user"` but
keyed the `db.get`, the Google Sheets export AND the `db.del` on `$input.user_id`, with no
comparison to `$auth.id`.** Any logged-in user could delete any other account by walking ids —
and the endpoint first appends that victim's name, email, wedding budget, location, wedding date
and total amount paid to the offboarding Google Sheet, so it was a PII exfiltration primitive as
well as an irreversible delete.

Same class as ep145 / ep87 / ep66 / ep21 / ep150, all fixed in the August 2026 hardening.
**ep20 was missed almost certainly because it is a DELETE and that pass swept PATCHes** — worth
remembering the next time an audit is scoped by verb.

Fixed by keying everything on `$auth.id` and ignoring the retained path param, the identical
remedy those siblings use. Backup: `.claude/xano_backups/ep20_pre_auth_fix_2026-09-10.xs`.

**Verified at runtime, not just read:** with two throwaway accounts, A's token plus **B's id in
the path** deleted **A** and left B alive. Before the fix that call would have destroyed B.
Unauthenticated is still 401.

It now also hard-blocks admin view-as sessions ([[project_admin_view_as]]) — with the ownership
fix, an impersonated token resolves to the *customer*, so an unguarded call would delete the very
person being helped.

**Open, and nobody has checked it:** whether other survivors of that verb-scoped audit exist. The
full authenticated-writer inventory is **51 endpoints** across groups 1, 4, 5, 7, 10, 12; only the
ten reachable by clicking in the app were reviewed for this work. Most of the other 41 are Xano's
auto-generated table CRUD (to_do_items, packages, venue_calculator_entries, concierge_reports,
feedback, user_vendor_comments, wptp_upload_record) that the August pass gave `auth` to rather
than deleting — a cleanup worth doing on its own. See [[project_endpoint_auth_audit]].
