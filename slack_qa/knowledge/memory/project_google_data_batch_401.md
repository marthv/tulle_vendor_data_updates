---
name: project-google-data-batch-401
description: "The admin dashboard's Google Data batch wrote NOTHING from 2026-08-30 to 2026-09-11 while reporting success — it proxied to an endpoint the auth hardening had locked. Fixed by inlining the fetch."
metadata:
  node_type: memory
  type: project
  originSessionId: 48f611b8-9c31-47c9-a494-e7c30f6efa52
  modified: 2026-09-11T08:30:00.000Z
---

**Symptom the user reported:** Vendor Images in the Tulle admin dash said "Image 1/2/3 —
0 vendors updated" for vendor IDs 14697-14900. The images step was CORRECT: it requires
cached Google data and there was none.

**Root cause:** `ep122 google_data_batch` fanned out over HTTP to
`ep107 google_data_for_vendor`. The 2026-08-30 hardening gave ep107 `auth = "user"`, and the
internal `api.request` carries no token — so **every sub-call had returned 401 since that
date**. Because `api.request` does not throw on 4xx ([[reference_xanoscript_traps]]) the loop
swallowed them, and the endpoint still returned its candidate list, which `dashboard.py`
rendered as "N vendors processed". The count was never "updated", it was "intended".
Request history confirmed it: 100% 401, all from the internal IP with no auth header.

**Exactly the same failure as the ep119 cache warmer** killed by that same pass
([[project_endpoint_lockdown_401s]]) — a machine caller hitting a newly-authed endpoint and
failing silently. **When auditing auth, check who calls the endpoint from INSIDE Xano.**

**Fix (ep122 v2, 2026-09-11):** the Places call is inlined; ep107 is untouched for the
frontend. Also `Place_ID != ""` (an empty Place_ID is a guaranteed INVALID_REQUEST and writing
its reply back is how the empty shells in [[project_google_data_empty_shells]] were created),
write only on Google `status == "OK"` so a failure stays retryable, and return real
`candidates / updated / failed / errors`. Backup:
`.claude/xano_backups/ep122_pre_inline_google_2026-09-11.xs`.

**Also fixed:** `update_vendor_image_two` (ep133) and `_three` (ep134) tested
`id < ending_index` while `_one` (ep132) tested `id <=`. "Run all 3 in order" over one range
silently gave the last vendor image_1 and nothing else. All three are inclusive now.

**Verified end to end on 14697-14900:** 42 candidates, 42 updated, 0 failed; then images 1/2/3
each wrote 42-43 rows, and the range drains to 2 leftovers (real Google data, no photos).

**STILL OPEN and worth knowing:** only **42** of the ~204 rows in that range had a Place_ID at
all. The rest (e.g. id 14900 "Bella by Sara") were submitted 2026-09-10 with `Place_ID: ""`, so
this tool can never enrich them — they need a Places *text search* by name+address first, which
nothing in the pipeline does today.
