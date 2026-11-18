---
name: reference-xano-mcp-quirks
description: Known limitations and gotchas of the xano-meta MCP when reading/writing endpoints in workspace 1
metadata: 
  node_type: memory
  type: reference
  originSessionId: 327d9694-3b5d-4d90-8d1e-e8b64eaa0e95
  modified: 2026-08-11T04:17:50.957Z
---

## 1. Some endpoints can't be read back as XanoScript ~~(now resolved for 119/121)~~

Historically endpoints **119 (`wptp_updated_mappings_search`)** and **121 (`wptp_map_light`)** in workspace 1, apigroup **10** ("WeWeb Transparency Project") returned `{"code":"ERROR_CODE_NOT_FOUND"}` from `getAPI` and were silently omitted from `listAPIs` with `include_xanoscript=true`. **As of 2026-05-20 both are readable again via `getAPI` with `include_xanoscript=true`** — Xano evidently fixed the serializer. If a future endpoint hits the same symptom, the workaround is still: ask the user to paste current XanoScript from the dashboard before modifying.

## 2. `updateAPI` requires canonical table-name casing in `$db.X` references — even on round-tripped XanoScript

This caused a brief production outage on 2026-05-18. The endpoint's XanoScript (pasted from the dashboard) had three references like `$db.wptp_updated_mappings.Name` (lowercase). The visual editor accepted them, but when I re-pushed verbatim via `updateAPI`, the parser rejected them with:

```
ERROR_CODE_INPUT_ERROR: Unsupported parameter reference - xdo.wptp_updated_mappings.Name
```

Every request returned 400 until I pushed corrected XanoScript using `$db.WPTP_Updated_Mappings.Name` (canonical PascalCase with underscores). The same `WPTP_Updated_Mappings.id` casing also needed for `sort = {...id: "asc"}`.

**How to apply:** Before any `updateAPI` call, scan the XanoScript and normalize all `$db.<table>.<field>` and `sort = {<table>.X: ...}` references to the canonical table-name casing as it appears in the Xano dashboard table list. Inconsistent casing within a single query is the smell to watch for.

## 3. `apigroup_id` for endpoints 119 and 121 is 10, NOT 4

`listAPIs` with `apigroup_id=4` returned these endpoints in early exploration but `updateAPI` with `apigroup_id=4` fails: `"The API doesn't belong to the API group"`. Authoritative location is apigroup 10. Always trust the `api_group = "..."` line inside the XanoScript itself — that's the truth.

## 4. `db.query <table>` name is case-sensitive and fails SILENTLY — wrong casing → empty `dbo` → runtime error

Sibling to section 2, but worse because it's silent. On 2026-06-05, building MCP tools `query_all_referrals` (#28) / `query_all_venue_pricing` (#29) for the **Claude MCP server (id 2)** in workspace 1, I wrote `db.query referrals` (lowercase). The `createTool`/`updateTool` call **succeeded** but the parser normalized the unrecognized name to `db.query ""` — no error at write time. At runtime the tool then failed with:

```
ERROR_CODE_INPUT_ERROR: Invalid syntax: missing parameter - dbo
```

because an empty table name leaves `dbo` unbound. By contrast `db.query venue_pricing` (a real table name) stuck fine. Adding a `where = $db.referrals.id != null` did NOT fix it — lowercase doesn't bind there either.

Root cause: the table is actually named **`Referrals`** (capital R, table id 12); `venue_pricing` is lowercase. Fix was `db.query Referrals` + `$db.Referrals.id`, after which the compiled XS showed `db.query Referrals` (name retained, not `""`) — that retention is the success tell.

**How to apply:** before writing any `db.query <table>` or `$db.<table>.<field>`, look up the EXACT table-name casing via `getTables` (search by prefix). If the compiled XanoScript comes back with `db.query ""` where you wrote a name, the name was wrong/unresolved — do not ship it. Also: the older `list_user_referrals` (#20) uses lowercase `db.query "" ... $db.referrals.user_id` and is probably broken the same way — patch to `Referrals` if it errors.

## 5. `updateTableSchema` (JSON) silently STRIPS ALL field validators — never use it on tables with validators

Discovered 2026-07-20 while adding hub columns to the `user` table (table 1). The JSON-schema tool dropped every validator (email trim/lower, password min:8/minAlpha/minDigit, all trims) even when passed back verbatim in the `validators` key. **Use `updateTable` (XanoScript) for schema changes instead** — `filters=trim|lower|min:8|minAlpha:1|minDigit:1` round-trips correctly there, and indexes can be added in the same call.

Related asymmetry: the XS parser accepts `visibility = "private"` (works, coexists with inline filters) but silently ignores `visibility = "internal"` (field becomes public!) — even though Xano's own XS export EMITS `visibility = "internal"`. If a field must be `internal`-access, the JSON tool can set it but kills validators; pick `private` + validators via XS instead (user.password is now private, not internal, for this reason — password type is never serialized into API output anyway).

## 6. `|get:` filter treats dots as path separators — never key lookups by email

`$map|get:$user.email` always misses because emails contain dots (each dot = a nesting level). Bit me in fn 13 (HUB lookup came back null for every contact). Use an `api.lambda` for dictionary lookups with dotted/dynamic keys.

## 7. Metadata-API writes (addTableContent/patchTableContentBulk) do NOT fire table triggers

Verified 2026-07-20: inserting/patching `user` rows via MCP content tools never invoked trigger 4; the same edit through a function-stack `db.edit` fired it instantly. Consequences: (a) backfills via `patchTableContentBulk` are trigger-silent (usually what you want); (b) you CANNOT test a trigger via metadata writes — use a scratch workspace function with `db.edit` (run through `runWorkspaceFunction`, which is allowed as long as the function has no nested `function.run`).

**Table triggers run ASYNCHRONOUSLY** (confirmed 2026-08-10 on table-11 trigger 5). A `db.get` in
the same request that made the write still sees the pre-trigger value — that looks exactly like "the
trigger never fired". Poll from outside instead; it settled in under 4s. Good self-contained test:
have a temp endpoint write a deliberately WRONG value via `db.edit`, then poll the row from outside
until it either self-corrects (trigger works) or stays wrong. Keep polling ~20s afterwards to prove
it settles rather than oscillating, which is the failure mode when the trigger lacks a write gate.

## 12. The RAW metadata API can update an endpoint's description but SILENTLY IGNORES xanoscript

Found 2026-07-27 while trying to avoid re-typing 618 lines of ep119. The token in `.mcp.json`
(`mcpServers["xano-meta"].headers.Authorization`) works against
`https://<instance>/api:meta/workspace/1/...` — `GET .../apigroup/10/api/119?include_xanoscript=true`
returns the full script, which is a great way to snapshot an endpoint to a file.

But **PUT does not apply the script.** It requires `name` + `description` (400s without them),
then returns **200 while applying only the metadata** — the description updated to the new v14
header while the body stayed v13, leaving the endpoint self-describing as a version it wasn't.
Tried `{"xanoscript": "<str>"}`, `{"xanoscript": {"value": ...}}`, `{"script": ...}`,
`{"code": ...}` — all 200, none applied. There is no `/xanoscript` sub-path (404).

**How to apply:** use the raw API for READS (snapshot to a file, diff against backups) but always
push script changes through the `updateAPI` MCP tool. Verify by re-reading the script and grepping
for a marker you changed — a bumped `cache_v` default in the returned `input` list is the cheapest
tell. A 200 from this API is not evidence the change landed.

### 12b. …UNLESS you send `Content-Type: text/x-xanoscript` — then the raw API DOES apply it

**Correction found 2026-08-10.** The failures above were all `application/json`. The meta OpenAPI
spec (`GET /apispec:meta?type=json`) shows `PUT /workspace/{w}/apigroup/{g}/api/{a}` accepts three
content types, and the **`text/x-xanoscript`** one takes the raw script as the request body:

```python
requests.put(f"{META}/workspace/1/apigroup/10/api/119?publish=true",
             headers={"Authorization": tok, "Content-Type": "text/x-xanoscript"},
             data=xs.encode("utf-8"))
```

This shipped ep 119 v22 (43 KB) cleanly. It matters because it lets you **build the new script with
a local script and exact string surgery** instead of retyping tens of thousands of characters into
a tool call — write a builder that asserts on match counts so a silent no-op is impossible, diff it
against the backup, then PUT. `POST .../api` (create) accepts the same content type; with JSON it
requires `name` and creates only a stub.

Xano reformats whitespace on save, so `deployed == local` will be False — compare with
`diff -qbB` and always re-assert on the **run stack** afterwards.

Also useful: the MCP `getAPI`/`listAPIs` tools intermittently fail with "Unable to get successful
response" on large scripts (ep 119 at 42 KB) while `getWorkspace` succeeds — that is a transport
size problem, not an outage. The raw `GET ...?include_xanoscript=true` is the reliable read path.
Note `?type=xanoscript` is NOT the right param (returns the row with `xanoscript: null`);
`?include_xanoscript=true` returns `{"xanoscript": {"value": "...", "status": "ok"}}`.

## 13. MULTI-KEY `search` on searchTableContent / patchTableContentBySearch is SILENTLY IGNORED — a bulk patch would hit the WHOLE table

Found 2026-08-02 planning the Timing_List backfill. `search` with ONE key filters correctly
(`{"VENDOR_ID":"V4916"}` → itemsTotal 2; `{"Vendor_Category":"Photography"}` → 1213). Add a
second key and **the filter is dropped entirely, with no error**: a 3-key search on table 36
returned `itemsTotal 26571` — every row in the table — and the first item was a venue row that
matched none of the criteria. `{"field":{"$eq":v}}` is rejected outright (`ParseError: Invalid
value for param`), so there is no escape hatch to an explicit operator.

**Danger:** `patchTableContentBySearch` takes the same `search` object. A multi-key patch reads
as correctly scoped and would write to all ~26.5k rows, silently clobbering every venue row.

Related, found 2026-08-10: `PATCH /table/{t}/content/{id}` is **404** (no such route), and
`PUT /table/{t}/content/{id}` with a partial body returns **200 while changing nothing**. So there
is no working partial row write on the metadata API — another reason to drive edits through a
function stack.

**How to apply:** only ever use metadata-API search/patch with a SINGLE key, and verify the
returned `itemsTotal` looks like a subset before any write. For anything needing AND-ed
conditions, write a workspace **function** and drive it with `runWorkspaceFunction` (server-side,
no round trips, no rate limits) — that is how `backfill_timing_list` was done. Give it a
`dry_run` input so the first call reports counts without writing.

## 14. `updateAPI` with `publish: false` writes a DRAFT — and its response looks exactly like a successful publish

Found 2026-08-11 shipping ep 119 v23. Using `publish: false` as a safety step is *correct* and it
works — the live run stack is untouched — but the tool's **response is the draft's**: it echoes the
new description, the new `input` list (my bumped `cache_v` default 18), `updated_at` **unchanged**,
and `"draft_updated_at": null`. Every visible signal reads "published". I then smoke-tested, got
answers identical to the old version, and concluded (wrongly) that it had gone live and was a clean
no-op. Burned a full 51-case parity run and a live-data behaviour test against the OLD version.

**The tells, in order of reliability:**
- `getAPI` with `include_draft=true` → **`draft_updated_at` non-null means a draft is pending and
  live is still the old script.** This is the only trustworthy check.
- `updated_at` on the endpoint does NOT move on a draft write; it moves on publish.
- The `updateAPI` response's own `draft_updated_at: null` is **not** evidence — it lied here.

There is **no publish-draft tool** in the MCP (searched: only release/tenant deploy tools). To
promote a draft you re-send the entire script with `publish: true`. So `publish:false` costs you
sending the script twice unless you use the raw `text/x-xanoscript` PUT from §12b — which is the
better path anyway for anything over a few hundred lines, since it reads the script from a file
instead of retyping it into a tool call. **I retyped 1,386 lines here when §12b already existed;
don't repeat that.**

**How to apply:** after ANY `updateAPI`, before believing a behaviour test, call
`getAPI(include_draft=true)` and assert `draft_updated_at` is null. A behaviour test that shows
"no change" is ambiguous between "correct no-op" and "never deployed" — disambiguate at the
metadata layer, not by staring at responses.

## 8. `api.lambda` (JS) is available and is the sane way to do map lookups / loops

Used in fn 33 `utils/resolve_hub` and fn 13. Lambda code accesses stack vars as `$var.<name>`. Sub-millisecond for small maps. Real `Date.now()` works inside Xano lambdas.

**Inputs are NOT addressable from a lambda.** `$var.input.state` throws at runtime
(`Cannot read properties of undefined (reading 'state')`, 2026-08-02 on ep207) — the lambda sees
`$var`, not `$input`. Hoist every input you need into a `var` first and read `$var.<that>`.

Two more operator limits found the same day:
- There is **no `contains` op** for "is this string in that array" inside a `conditional`
  (`Invalid op: contains`), and `in` there wants a literal list. Compute set differences in a
  lambda and return the ids to act on.
- `{type: "unique", ...}` is **not a valid index type** in table XanoScript (`Invalid index type.`)
  — use `createUniqueIndex`, or enforce uniqueness in the writer.

## 11. Scheduled tasks SILENTLY ABORT on large unpaged db.query results — <0.1s runs, no error anywhere

Cost a 5-firing debugging saga on 2026-07-21 (task 12/13 refresh_hub_stats). A scheduled task whose stack (or any function it calls via function.run) materializes a large unpaged `db.query` result (~10.5k rows × 6 cols) dies in 0.02–0.1s: history shows a normal-looking entry (no status/error field), include_output gives null, debug.log invisible, try_catch does NOT catch it. The same code runs fine via runWorkspaceFunction (17s). Threshold is between ~5.5k id-only rows (task 3's user query, works) and ~10.5k×6-col rows (dies). **Fix: page the query inside the task** (task 13 pages 12×1000 and runs 52s happily; task 10 pages at 400).

Debugging technique that cracked it: db.edit breadcrumb markers on scratch rows before/after suspect steps — timing alone is NOT discriminating (small queries are <50ms), and history/logs are blind. Also learned: api.lambda, db.add/db.edit, small db.query, and multi-entry schedules all work fine directly in task stacks; `input = {}` on function.run to a zero-input function is stripped at compile (harmless).

## 10. Clearing endpoint auth requires `auth = ""` — omitting the line is STICKY, `auth = false` is a parse error

Found 2026-07-21 flipping ep 31 `states` public. Pushing XanoScript with the `auth = "user"` line simply deleted left the endpoint still auth=1 (updateAPI succeeded, response metadata showed auth:1, unauth curl got 401). `auth = false` fails parse: `Invalid kind for auth - static:bool`. The working form is `auth = ""` — after that, response shows auth:0 and unauth requests return 200. Always verify with an unauthenticated curl, not just the updateAPI response.

## 9. `timestamp_add_days` / `timestamp_add_hours` work ONLY inside db.query where clauses — NOT in var expressions

Caused a ~5-minute production 500 outage on auth/me (2026-07-20 23:19–23:24 UTC). `var $x { value = "now"|timestamp_add_days:-1 }` parses/publishes fine but 500s at runtime with `Unable to locate func entry: timestamp_add_days` (same for `timestamp_add_hours`, which doesn't exist anywhere). They only resolve when compiled into a db.query `where =` (as in task 3). For time math in stack expressions, use an `api.lambda` with `Date.now()` instead. Lesson: a published endpoint ≠ a working endpoint — smoke-test hot-path endpoints (auth/me!) immediately after updateAPI.


## 15. `runWorkspaceFunction` has NO AUTH CONTEXT — and tokens it mints are REJECTED

Found 2026-09-11 building admin mirror mode.

Any function that reads `$auth.id` (or `$auth` at all) fails under `runWorkspaceFunction` with
`ERROR_CODE_ACCESS_DENIED` / "Access Denied". So **$auth-dependent functions cannot be unit-tested
through the MCP at all** — they have to be exercised through a real authenticated HTTP request.

Worse, the obvious workaround does not work either: a throwaway function containing
`security.create_auth_token { table = "user", id = <N> }`, run via `runWorkspaceFunction`,
returns a perfectly well-formed JWE that the API runtime then rejects with
**`{"code":"ERROR_CODE_UNAUTHORIZED","message":"Invalid token."}`** on every endpoint, including
`auth/me`. The execution contexts differ.

**How to apply:** to smoke-test anything auth-dependent, create a TEMPORARY secret-gated endpoint
that mints the token (hardcode a single user id so it can never be pointed at a customer), call it
over HTTPS, test with the returned bearer token, then DELETE the endpoint and verify it 404s. That
path works and is what proved mirror mode end to end. Do not leave it live — it is an
unauthenticated token minter behind nothing but a shared string.

## Metadata table search filter shapes (MEASURED 2026-10-03, POST /table/{id}/content/search)
- `{"search": {"a": 1}}` single key = works.
- `{"search": [{"a": 1}, {"b": "x"}]}` list of single-key dicts = works as AND.
- `{"search": {"a": 1, "b": "x"}}` TWO keys in one dict = SILENTLY IGNORED -> returns the WHOLE table.
- A value containing `|` (e.g. bucket_key 'Venue|MA|...') -> 400 "Invalid filter" (parsed as operator).
- Range operators like `"col|>=": v` -> filter ignored. Always re-filter rows in code.
