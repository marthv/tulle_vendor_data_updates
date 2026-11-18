---
name: reference-xanoscript-traps
description: "XanoScript language traps that each caused a live 500 or a silent wrong answer in this workspace — $$ vs $this in filters, spaced table names in where clauses, filter-into-arithmetic, invalid ops in preconditions. Read before writing any db.query filter or expression."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 15e56bb6-cbbd-4d5f-8c5c-e6d27694b014
  modified: 2026-08-10T14:44:25.714Z
---

Language-level traps, distinct from the MCP quirks in [[reference-xano-mcp-quirks]]. Every one of
these was found by a live smoke test, not by the editor — **XanoScript parses and publishes fine
and then fails at runtime**, so always call an endpoint after `updateAPI`.

**`|filter:` uses `$$`, NOT `$this`.** The official XanoScript docs show `$this`. In this build
`$this` matches **zero rows and does not error** — the worst failure mode, because the endpoint
returns a confident empty answer. Verified side by side: `$$.Field == "x"` → 1 row,
`$this.Field == "x"` → 0. Expressions passed to `|filter:` / `|sort:` must be **backtick-wrapped**:
`` value = `$rows|filter:($$.Type != "roadmap")` ``

**A table name containing spaces cannot appear in a `where` expression.**
`$db."DONATION - Payment Log".Client_Reference_ID` → *"Invalid pattern"*; the backticked variant
**fatals at runtime**. There is no working escape. Options: filter in memory after a
`db.query` (use `output = [...]` to keep the payload small), or rename the table. `db.add` /
`db.get` / `db.query` accept the quoted name fine — it is only the `$db.<table>.<field>`
expression form that breaks.

**A filter piped straight into arithmetic is a syntax error.**
`($x|to_decimal) / 100` fails. Split it: one var for the filtered value, another for the maths.
This took down ep 30 on a live deploy.

**Operators are context-specific.** `includes` works in a `db.query where` but is *"Invalid op"*
inside a `precondition`. This build also has no `starts_with` / `strpos` filter — do string-shape
validation in the calling client instead.

**Dot-notation on a possibly-absent key fatals.** `$session.metadata` throws *"Unable to locate
var"* when Stripe omits `metadata`. Use `|get:"metadata"` and guard, especially in webhooks where
you do not control the payload.

**`plan` is a RESERVED WORD and cannot be an object key.** Returning `{ plan: [...] }` from an
`api.lambda`, or reading `$calc.plan`, is a syntax error — and the reported line/column points at
*neighbouring* statements, not at `plan`, so it sends you chasing the wrong thing. Found 2026-08-10;
cost several failed pushes before bisecting. **When a XanoScript syntax error points at a line that
is obviously fine, suspect a reserved word nearby and bisect by cutting the script down** rather
than rewriting the flagged line. Renaming to `rows_todo` fixed it instantly.

**`sort` WITHOUT a `return` block is SILENTLY IGNORED.** Found 2026-08-31 building ep271:
a `db.query` with `sort = {Feedback.id: "desc"}` and only an `output` allowlist came back in
**heap order, oldest row first, no error**. The sort only takes effect alongside
`return = {type: "list"}` — which is why ep119 and ep213 both have it and both sort correctly.
Same failure family as the paged-envelope trap below: a confident wrong answer, not an error.
Any endpoint whose caller relies on ordering must keep the `return` block.

**A PAGED `db.query` returns an ENVELOPE, and the `output` allowlist must use `items.*` prefixes.**
With `paging: {...}` the result is `{itemsReceived, curPage, …, items: [...]}`. An allowlist written
as `output = ["id","State"]` matches nothing in that envelope, so **`items` comes back empty and the
query silently returns zero rows** — no error. Correct form is
`output = ["itemsReceived", "items.id", "items.State", …]`, then read `$page.items`. (ep 119's main
query is the reference example.) Related: **`itemsTotal` only exists when paging sets
`totals: true`** — otherwise `$page.itemsTotal` fatals with "Unable to locate var".

**An unpaged `db.query` over table 11 (13k blob-heavy rows) returns 502.** Always page it. Same
family as the scheduled-task silent abort in [[reference-xano-mcp-quirks]] §11.

**`db.edit` single-field writes**: copy ep 150's shape —
`field_name` / `field_value` / `enforce_hidden_fields = false` / `data = {Col: $var}` /
`output = ["id"]`. Hoist the value into a plain `var` first; `data = {Col: $p.some_key}` with a
dotted element reference is fragile.

**Sort keys**: `sort = {table.field: "desc"}` works for a normal table; for in-memory lists use
`` `$list|sort:"field":"desc"` ``.

`function.run "name" { input = {...} } as $x` is the call syntax (names with slashes work quoted).

**`api.request` DOES NOT THROW ON 4xx/5xx — a `try_catch` alone proves nothing.** Measured
2026-08-18: a deliberately malformed Brevo POST returned `status: 400` with a JSON error body
and the `try` block ran to completion. Any code shaped like "call in `try`, mark success on the
next line, mark failure in `catch`" therefore records **success for every server-side rejection**
— rejected sender, revoked webhook, bad payload, suspended account. This bit two vendor-portal
endpoints at once (ep255 `email_sent`, ep250 `slack_delivered`), and both columns exist precisely
so ops can find undelivered messages, so the false positive erased the only signal of the failure.

Always branch on the status: `if ($r.response.status >= 200 && $r.response.status < 300)`.
`$r.response.status` and `$r.response.result` are the accessors; `($r.response.result|json_encode)`
is safe for stashing the error body in a text column. Note some APIs *also* answer 200 with an
error payload — Slack's `chat.postMessage` returns `ok:false` — so for those check the body too.
`try_catch` still earns its place, but only for genuine transport failures (DNS, timeout).

**`db.edit` CLEARS json/int COLUMNS YOU DID NOT LIST; `db.patch` DOES NOT.** Measured
2026-08-18 on table 70 `vendor_calendar`: an edit whose `data` map contained only
status/last_sync_* reset `busy_dates` to `[]` and `busy_count` to `0`, while the **text**
columns (`label`, `represents`) in the same row survived untouched. So it is not a clean
full-replace either — the damage is selective, which is what makes it easy to miss.

Consequence in the wild: a failed calendar sync erased availability that had been correct a
minute earlier, and the vendor's page then read "no dates taken" — indistinguishable from a
genuinely free calendar. **Use `db.patch` for any partial update** (ep255 already did; ep264
now does). Reserve `db.edit` for writes that supply every column you care about.

**A status column used BOTH as a health flag and as a work-queue filter stops the work.**
Same feature, same day: `vendor_admin/calendars?include_urls` selected `status == "active"`
so the nightly sync could find feeds, and the sync wrote `status = "error"` on failure. One
transient network blip therefore removed a calendar from every future run — nothing retried
it, and only the vendor re-pasting their link could recover it. The fix is to include the
error state in the work query (`status in ["active","error"]`) and exclude only the terminal
one (`paused` = deliberately disconnected). **Whenever a job filters on the same column it
writes failures to, check the failure state is still selected.**

**The delete statement is `db.del`, NOT `db.delete`.** Found 2026-09-10 while auditing every
authenticated writer in the workspace: a scan regex built around `db.delete` matched nothing and
silently under-reported four live DELETE endpoints. There is no error to notice — the word simply
does not exist. When grepping XanoScript for writes, the real verb set is
`db.add / db.edit / db.patch / db.del / db.add_or_edit / db.bulk* / db.truncate`.

**Bare `now + <milliseconds>` fails at RUNTIME with `"Not numeric."`** in a `var` expression —
`now` is not an integer there. Epoch arithmetic has to go through a lambda:
`api.lambda { code = "return Date.now() + 600000;" } as $deadline`. Same workaround ep3 `auth/me`
already used for its daily gate; it cost a v2 on two new endpoints on 2026-09-10. Comparisons have
the same problem, so do those inside the lambda too
(`return Date.now() < $var.session.redeem_by;`).

**`util.get_env` returns EVERY workspace secret and NO request metadata.** Measured 2026-09-10:
one call returns `stripe_api_secret`, `brevo_api_key`, `anthropic_key`, `Google_Refresh_Token`,
`magic_jwt_secret`, all four Slack webhook URLs, the SendGrid keys and `cron_secret` in a single
object — and the output is **byte-identical between a GET and a POST**, so there is no HTTP verb,
IP or header in it. Two consequences: **never return it from an endpoint** (any endpoint that does
leaks the whole workspace at once), and it is useless for anything needing to know about the
request. This is also why a verb-based middleware could not be built.

**Custom auth claims survive, but reading them naively is FATAL.** `security.create_auth_token`
accepts `extras = {imp: true, ...}` and the claim reads back intact. But every other mint site in
this workspace passes `extras = {}`, and on such a token
`$auth.extras.imp` -> `ERROR_FATAL "Unable to locate auth: extras.imp"`. A guard written that way
would 500 **100% of real traffic** on `auth/me` and `user/update_vendor_views`. The only safe form
is **`$auth.extras|get:"imp":false`** — true for a token carrying the claim, false otherwise, never
an error. Also: `$auth.extras` alone is safe (returns `[]` on a plain token, so only the *second*
level faults), **bare `$auth` is not addressable at all** (`var $x { value = $auth }` ->
"Unable to locate auth:"), and `$auth` **is** in scope inside a `function.run`, so a shared guard
helper needs no input. See [[project_admin_view_as]].

**A table's auto-created `created_at` cannot be indexed in the same `addTable` call** — the index
block is evaluated before the implicit column exists ("Unable to locate index expression"). Create
the table, then add that index.

## Substring search in a `where` clause: the operator is `includes` — NOT `like`/`ilike`

Found 2026-09-12 adding contains-search to ep306, after four wrong guesses. Table 1, 26,395 rows:

| operator | result |
|---|---|
| `ilike` | **PARSES, then throws at runtime**: `Param: search - Invalid filter:` |
| `contains` | same — throws |
| `like` | **parses AND runs, matches NOTHING** — with `%..%`, `*..*` or a bare string. The silent-zero failure, and the dangerous one |
| `regex`, `starts_with` | run, return 0 |
| **`includes`** | **CORRECT.** Real case-insensitive substring match |

```xanoscript
where = $db.user.email includes $q_text || $db.user.name includes $q_text
```

Verified live: `email includes "vivek.marthi"` → id 14998 (0.3s); `"gaddis"` → 44 rows (0.16s).
This is the same operator ep119 uses — the "substring `includes` full scan" in
[[project_search_n1_not_bottleneck]] is this, not a JS filter.

**Do NOT fall back to a lambda full-scan.** Measured: pulling id/email/name for all 26,395 rows
and filtering with JS `indexOf` took **15.1 seconds**. `includes` filters in the database and the
same query returns in under 0.5s.

**How to test an operator without shipping it:** a throwaway workspace function with the
`db.query` inside `try_catch`, run via `runWorkspaceFunction` — several candidates per run, and
it reports which throw versus which silently return 0. `db.query` needs no auth there, unlike
`security.create_auth_token`, which mints a token endpoints REJECT in that context
([[project_perf_baseline_digest]]). Delete the function afterwards.

**2026-10-26:** `if (($auth.id in [14998]) || $x)` PARSES but fails at runtime with ERROR_FATAL "Invalid op: in" -> use
`$auth.id == 14998`. For hot endpoints (ep119/121) test new code on a temporary COPY endpoint (no cache) with real tokens
before touching the live one; push the live one with an auto-rollback script.

- **`0 == null` is TRUE (2026-11-09).** `$input.n == null` / `$user.n != null` treat 0 as null. Use `($raw_input|has:"n")` and `(x|is_null)`. This silently broke ep21 free-view countdowns for 12 days.
