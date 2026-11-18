---
name: project_endpoint_auth_audit
description: "2026-08-30 Xano endpoint auth audit — what was closed, what is deliberately still open, and why Xano keeps regressing to open"
metadata: 
  node_type: memory
  type: project
  originSessionId: df4bbc27-5cff-41f5-9cdc-b99202babd82
  modified: 2026-08-31T00:33:59.748Z
---

2026-08-30, triggered by two public Apify actors scraping the site (#feedback thread).
Audited all 140 endpoints in API groups 4 (Weweb) and 10 (WPTP).

**ROOT CAUSE, and the thing to remember: Xano endpoints default to `auth = false`.**
Every endpoint ever added started public. This is opt-in security, so the surface reopens
every time anyone adds an endpoint. Fix the default at the API-GROUP level
(`updateApiGroupSecurity`), not endpoint by endpoint.

CLOSED 2026-08-30 (verified 401):
- **ep148 `POST /impersonate` — DELETED.** It was full account takeover. `auth=false` plus it
  role-checked `$input.current_user_id`, a caller-supplied value, then minted a 24h token for
  any `target_user_id`. Setting `auth=user` did NOT fix it — that only proves the caller holds
  *some* token, so any free signup could still take over any account. **If anything like this
  is rebuilt, key it on `$auth.id`, never on an input id.** Same class of bug as
  [[project_referrals_self_referral]].
- ep166 payment_log, ep84/ep83 donation_payment_log (list AND by-id), ep82, ep137/ep139
  referrals reads, ep136/ep138 referrals writes, ep24 to_do_items.
  **ep83 mattered as much as ep84** — ids are sequential, so a by-id read reconstructs a list.
- ep146 wptp_pdfs (the 8.6 MB / 13,907 Drive-link dump), ep121 wptp_map_light.
- ep152 `_clone` + ep159 `_Backup` DELETED. **Authenticating ep121 alone was bypassed by its
  own clones** — they served the same data on another path. Delete dead copies, don't auth them.

CLOSED later on 2026-08-30, round 2:
- **ep199 venue_pricing_dashboard — SECRET-GATED** (403 without it). It had been returning
  **27,287 pricing rows / 10,777 vendors in 6 anonymous requests**, and its `section=pdfs`
  also handed out Drive links. Uses the shared `ANALYTICS_EXPORT_SECRET`. **dashboard.py in
  tulle_vendor_data_updates was edited to pass `&secret=` at all 3 call sites — DEPLOY IT or
  the Venue Pricing tab 403s.** `_loadtest_ep199.py` still needs the same param.
- ep141 extracted_pdf_data/{id} and ep122 google_data_batch → `auth = "user"`.

STILL OPEN ON PURPOSE (verified reasons — do NOT "fix" these blind):
- **ep258 venue/suggest_index** — 250 KB, all 14k vendors. **All 200 sampled calls were
  anonymous, ZERO Authorization headers ever**, so authenticating it breaks type-ahead for
  PAYING users too, not just logged-out ones. Needs a WeWeb change + publish first.
- **ep177 wptp_venue_categories** — its own docstring: the extraction pipeline's
  `_fetch_vendor_categories` FAILS CLOSED, so auth here aborts every extraction run
  ("vendor category map unavailable"). Same trap as ep146.
- **ep207 pe_benchmarks** — deliberately PUBLIC by product decision; powers the free
  market-rate panel and exposes no per-vendor data.
- **Anonymous WRITES to table 36 still open: ep143 POST, ep144 PATCH, ep140 DELETE**, plus
  ep85 POST donation_payment_log, ep151, ep163, ep132-134, ep123, ep172.
  Data poisoning is the bigger risk than theft here and it is still unaddressed.

**THE SCRAPERS WERE NEVER HITTING THE API — CONFIRMED 2026-08-30 from an actual Apify run.**
`quarterly_jingo/tulletogether-scraper` crawls **tulletogether.com — the BLOG**, not
tulletogether.app and not Xano. Its `sourceUrl` column is blog posts
(`/2026/02/19/popular-los-angeles-area-wedding-venues-and-how-much-they-cost/`), its
`location` column is the site NAV MENU scraped verbatim ("Home Budget Tools Resources
Methodology Locations New York City..."), and its `pricingRange` values are the prices
published in the articles. One run: 17/23 pages, 46 items, $0.006.
**So none of the endpoint lockdown stopped this scraper, and it could not have.** It is
public SEO content behaving exactly as published. The only real levers are Cloudflare bot
rules on the .com, an Apify abuse report, or an editorial decision to stop printing prices
in posts — all trade against SEO. Do NOT re-litigate this as an API problem.
The audit was still worth it: it found `/payment_log` serving every customer email+amount
and `/impersonate` minting tokens for any account. Those were real, severe, unrelated to the
scraper, and live for months.

**ep146 wptp_pdfs IS LOAD-BEARING FOR batch-ingest.** Putting `auth="user"` on it CRASHED the
Railway `batch-ingest` service ("Could not fetch wptp_pdfs (Xano down?): 401 ... INGEST
FAILED"). It is now secret-gated, and `_fetch_xano_pages` in extract_core.py — the single
chokepoint for every pipeline list read — sends `ANALYTICS_EXPORT_SECRET` on every call.
Endpoints with no `secret` input ignore it, and Xano's cache keys on DECLARED inputs, so an
ignored param cannot fragment their cache.

CONFIRMED BY USER 2026-08-30: **tulletogether.app does NOT render pricing to a logged-out
browser.** That closes the second scrape route — a page scraper gets listings only. So after
the ep199 gate, BOTH the API path and the HTML path are shut for pricing. Vendor names /
cities are still public by design (ep258 + public pages) and that is expected, not a hole.

**CORRECTION (2026-08-30, same night): closing ep146 did NOT break Kate's Drive crawler.**
That script is `drive_download_restriction.gs` (untracked in `tulle_vendor_data_updates`) — a
pure Apps Script Drive-API sweep that walks the Drive FOLDER_ID with a page token and sets
`copyRequiresWriterPermission`. It contains **no `UrlFetchApp`, no HTTP call, no Xano
reference at all**, so it never needed ep146. It is resumable via ScriptProperties against the
6-minute Apps Script cap — which is why Kate saw rows "skipped". Related:
[[reference_pdf_corpus_exposure]].

So the `Python-urllib/3.12` + `curl/8.5.0` traffic from Google Cloud (35.237.153.65 /
35.196.153.210 / 34.139.224.102) that hit ep146/ep141/ep143/ep172 is **something else** —
Apps Script identifies as `Mozilla/5.0 (compatible; Google-Apps-Script; ...)`, never as
Python-urllib. Signature was empty-`{}` POSTs that create rows, backwards id enumeration
(42050, 42049, ...), and `PATCH /wptp_pdfs/notanid`. Most likely a cloud-hosted AI agent
session probing the write surface (Des linked a claude.ai/cowork session in #feedback that
evening). **Unconfirmed — if it was not one of the team, treat it as hostile reconnaissance.**

## WRITE SURFACE — 2026-08-30 round 3 (SECRET-GATED, verified both directions)

`POST/PATCH/DELETE extracted_pdf_data` (143/144/140), `POST venue_pricing` (163),
`PATCH wptp_pdfs/{id}` (172), `POST admin/pipeline/job-status` (186),
`POST update_vendor_image_one|two|three` (132/133/134). All return 403 anonymously; all pass
the gate with the secret (400 on missing params / 200 with 0 rows / 404 on a bad id).

- **`POST extracted_pdf_data` had been set to `auth="user"`, which silently 401'd EVERY
  extraction write.** Same trap as ep146/batch-ingest. Machine callers must get the secret.
- `update_vendor_image_*` fans out to the Google Places photo API on the billed key — leaving
  it anonymous was denial-of-wallet, not just a data hole.
- Xano reshapes a `dblink` input to `dblink_0__ required:true` when you rewrite the script.
  It looks alarming and is cosmetic — verified with a real create/delete (row 42052) that the
  pipeline's POST body still maps to columns correctly.
- The secret goes as a QUERY param on writes (`params={"secret": ...}`), because Xano merges
  query and body into inputs and the body shape varies per call site. On ep144 that also
  matters for correctness: `data` is built from `$raw_input` (BODY keys only), so a query
  secret cannot leak into the patched columns.
- extract_core.py sends it from `_fetch_xano_pages` (all reads) plus `_post_row`, the dedupe
  DELETE and the status PATCH; dashboard.py from the job-status POST and the image POSTs.
  **Ship the code BEFORE gating** or writes 403 in the gap.

### STILL ANONYMOUS — the remaining write surface (all verified ZERO real traffic)
Destructive: `DELETE user_vendor_comments` (95), `DELETE feedback` (125), `DELETE packages`
(110), `DELETE wptp_upload_record` (178), `DELETE venue_calculator_entries` (75).
Integrity: `POST wptp_updated_mappings` (151), `PATCH …_lat_lng` (123), `POST sync_collections`
(164), **`POST donation_payment_log` (85) — anyone can insert fake payment rows**,
`POST referrals_Clone` (161). Plus the POST/PATCH halves of comments, pending_uploads,
upload_record, packages, calculator entries.
Planned treatment: **`auth = "user"`**, not the secret — if WeWeb does use any of them a
logged-in user still works, and the only exposure left would be a logged-out path.
NOT DONE: item 2, the `admin/rebuild_*` + `admin/backfill_*` GETs that have WRITE side effects
and are unauthenticated compute-burn (188, 229, 266, 267), plus small admin reads
(165, 187, 268, 269).

Group-10 `feedback` CRUD (125-129) is a DUPLICATE — the app posts to group 4 ep130, which is
already authed. Same for user_vendor_comments / upload/image / parse-vendor-pdf: zero traffic.

## ROUND 4 — 2026-08-31 (DELETEs + item 2 admin endpoints)

State now: **135 endpoints — 66 user-auth, 27 secret, 42 anonymous** (was 64 anonymous).
**All destructive DELETEs are closed.** 19 anonymous WRITES remain, all verified zero-traffic:
ep69, 79, 113, 114, 157, 161 (g4); ep44, 45, 98, 99, 123, 128, 129, 151, 164, 181, 182, 184,
185 (g10). Planned treatment `auth = "user"`.

- DELETEs → `auth = "user"`: ep95 user_vendor_comments, ep125 feedback, ep178
  wptp_upload_record, ep110 packages, ep75 venue_calculator_entries. All 401 anonymously.
- **Item 2 DONE — every `admin/*` GET is secret-gated**: 187 pipeline/jobs, 191 google/coverage,
  188 rebuild_state_benchmarks, 229 rebuild_venue_spaces, 266 backfill_vendor_match_keys,
  267 rebuild_preferred_vendors, 268 preferred_vendors_stats, 269 preferred_vendors_for_vendor,
  165 admin_metrics. All 403 anonymously. **ep187 + ep191 are DASHBOARD/PIPELINE deps** — the
  secret ships from dashboard.py (2 jobs URLs + coverage) and extract_core.py (1 jobs URL).
  The rebuild_* ones are GETs that WRITE; leaving them open was unmetered compute burn.
- endpoint_health.py now covers 12 deps, **12/12 verified OK**. When you gate an endpoint the
  health registry entry must be updated too, or it reports SECRET BROKEN against a correctly
  configured endpoint — the exact false alarm that gets a panel ignored.

**BUTTON CONTRAST FIX (theme.py).** The primary button rendered dark-on-dark-green. The
palettes were already right; Streamlit wraps button labels in
`<div data-testid="stMarkdownContainer"><p>` and config.toml's `textColor` lands on that `<p>`,
so `color` on the `<button>` never reaches the label — **paint the descendants**. Both
`kind="primary"` and `data-testid="stBaseButton-primary"` are matched (the attribute was
renamed across Streamlit versions). **DARK MODE DELIBERATELY KEEPS DARK INK**: its brand
#2FA96B scores 6.25:1 with dark ink and only 3.00:1 with white.

**launch_parallel.py interlock SHIPPED**: `extraction-batch` is now a NO-OP unless
`CONFIRM_EXTRACTION_RUN=1`. That is intentional — but it means a deploy no longer extracts.

## ROUND 5 — 2026-08-31: **ZERO ANONYMOUS WRITES REMAIN**

**135 endpoints — 80 user-auth, 33 secret, 22 anonymous (all GETs).**

`auth = "user"` (browser): ep69 upload/image, 79 + 75 venue_calculator_entries, 113/114/110
packages, 157 parse-vendor-pdf, 161 referrals_Clone, 98/99/95 user_vendor_comments,
128/129/125 feedback, 151 wptp_updated_mappings, 181/182/178 wptp_upload_record,
45 wptp_pending_uploads POST.

`secret` (machine): ep43 + ep44 wptp_pending_uploads GET/PATCH (**Make.com**),
123 lat_lng bulk backfill, 164 sync_collections, 184 photographer_observation,
185 photographers_worklist.

### THE MAKE.COM DEPENDENCY — a THIRD system nobody listed
`PATCH /wptp_pending_uploads/{id}` was called **26x by `Make/production`, anonymously**.
Scenario **"Integration Xano, Webhooks" id 4281646** (team 737786, org 1822845), on-demand:
module 58 `GET /wptp_pending_uploads` → 59 iterator → 4 fetch file → 5 Drive upload →
**module 61 `PATCH /wptp_pending_uploads/{{59.id}}`** → 14 Sheets log.
**Both 58 and 61 need `?secret=` or PDF ingestion 403s on the next run.** Make's
`scenarios_update` REPLACES the blueprint wholesale (32 KB incl. an 11 KB designer canvas),
so this was left as a MANUAL two-field edit rather than risk a transcription error.

### EVIDENCE METHOD THAT WORKED (the WeWeb MCP was DOWN all session)
Could not read WeWeb workflows, so used **request-log headers** instead: ep114 packages and
ep181 wptp_upload_record both showed live `tulletogether.app` traffic **with `Authorization`
on every call** — proof the app authenticates those flows before gating them. Stronger than
reading the workflow, but only sees a ~2h window.

### TRAPS FOUND
- **Xano validates REQUIRED INPUTS BEFORE auth and before the stack.** An anonymous call with
  a bad body returns 400, NOT 401/403 — so a gate looks absent when testing with `{}`.
  Always retest with required inputs satisfied.
- `PATCH photographers_worklist/1` with an EMPTY body returned **200 and the row** while
  anonymous — a live working write, not a theoretical one.
- **ep157 parse-vendor-pdf spends YOUR ANTHROPIC CREDITS per call** (claude-opus-4-5) and
  interpolates a caller-supplied `file_path` onto the Xano host for Claude to read. It was
  the most expensive anonymous endpoint in the workspace.
- **ep45 carries `WPTP_Access` / `Date_of_Access`** — an anonymous INSERT was a potential
  PAYWALL BYPASS, so it got user auth rather than the Make secret.
- Parallel `updateAPI` calls make the Xano ADMIN api 502/503 while the public API stays 200.
  Go serial and retry.

### STILL ANONYMOUS — 22 READS, several leak PII. Next batch:
ep180/179 wptp_upload_record and ep127/126 feedback and ep124 (**submitter emails**),
ep112/111 packages (**couples' budgets**), ep97/96 user_vendor_comments,
ep77/76/196/197 venue_calculator_entries, ep183 photographers_worklist, ep200 hub_stats.
Deliberately public: ep31 states, ep70, ep92, ep135 app-version, ep177 (pipeline fails
closed), ep207 pe_benchmarks, ep258 suggest_index.


## ep34 `/admin_choices` — DO NOT make public (checked 2026-09-15)

Tempting, because its two siblings already are: **ep31 `/states` and ep70
`/admin_location_adjustment` are both `auth=0` with a 1h response cache**, made that way on
2026-07-21 for exactly this reason ("WeWeb fetched this pre-auth on page load and got 401s").
ep34 was left `auth=1`, `cache.active=false` — and it must stay authed.

**Why: it is a BLOCKLIST, not an allowlist.** The XanoScript dumps the whole `ADMIN - Choices`
table (table 2, **67 columns**) and only strips two:

```
db.query "ADMIN - Choices" { return = {type: "list"} } as $model
foreach ($model) { each as $row {
  array.push $out { value = $row |unset:"Slack_Webhooks" |unset:"Promo_Slack" }
}}
```

So every column that is not those two is returned, and **any column added to the table in
future is exposed automatically**. What is in there beyond the onboarding dropdown options
(`Where_are_you_in_your_journey`, `Age_Range`, `How_they_found_us`, `Venue_Type`):

- **Aggregate business figures** — `Sum_of_Booked_Package_Cost`, `Sum_of_Selected_Package_Cost`,
  `Total_Booked_Cost`, `Total_Selected_Cost`, `Percent_of_Booked_Cost`,
  `Percent_of_Selected_Cost`, `Count_of_Packages`, `Count_of_PDFs`, `Count_of_Age_Range`,
  `Count_of_Planning_Phase_Users`
- **Payment links** — `Stripe_15_Donation`, `Stripe_30_Concierge_Report`, `Payhere_Link`,
  `Payhere_Payment_Link`, `Donation_Payment_Link`, `Concierge_Report_Payment_Link`
- **Unreleased promo config** — `Promo_Template`, `Promo_Date`, `Day_Difference_for_Promo`

**PRESENT ISSUE, not hypothetical:** all of that is already served to ANY authenticated user,
i.e. all ~26k accounts. The 2026-07-21 v2 fix closed the Slack webhooks and stopped there.

**The right fix is a NEW narrow endpoint**, allowlisting only the onboarding option columns,
`auth=0` + 1h cache, with collection `056030cf` (ADMIN - Guest Count Adjustment) repointed at
it. Keep ep34 authed for admin surfaces. That also removes the duplicate: `056030cf` and
`4e8c1c16` (ADMIN - Choices) currently call the SAME endpoint, measured twice one second apart
on one real signup at ~0.45s each, `X-App: hit` (so the cost is round-trip, not compute) and
`Cache-Control: private, no-cache, no-store` (so the browser can never reuse it).

**USER DECLINED the narrow-endpoint work on 2026-09-15 — do not re-raise it unprompted.** The
finding stands (65 columns, incl. booked-cost totals and segment counts, served to every
authenticated user), but it is a known, accepted state, not an open task.

## 2026-10-28 XANO CLEANUP - 31 objects DELETED (owner chose "Delete the safe list")
Endpoints (14): 26,27,28,29 (stripe /sessions, retired same day), 297,298 (view-as), 19 (Process_PDFs template), 71 (anon Reset_link mailer), 52 (anon migration write), 105 (anon image upload), 96,97 (user_vendor_comments reads), 184 (photographer_observation), 118 (send_daily_slack test).
Functions (11): 4,5,8,9,10,11,14,15,17,44,48. Tables (6): 8 session(0), 13 quick feedback(104), 14 general feedback(13), 18 forecast(13), 22 expense projections(13), 44 shelf_items(0).
Each had 0 callers in: 20 live WeWeb page JSONs + main.js, tulle_vendor_data_updates, tulle-vendor-portal, tulletogether, and nothing in Xano referenced it (inventory). Full backups (code + schema + ALL rows): `.claude/xano_backups/deletions_2026-10-28/`. Restore = recreate from those files.
KEPT on purpose (callers found): ep81/94 contact relays (4 pages - fix as security relay instead), ep197 (Cost Research + Planner), ep103 PATCH (2 pages), ep213/214 + table 60 (roadmap_orders.py, refunds 6&7 open), table 9 (eps 35-39), table 30 (ep62), table 32 (eps 95-99), table 73 feedback_triage_state (likely the triage tool's state; source not in local repo).
**Tool trap:** xano_push.unwrap() does NOT report `{"isError":true,"text":"ERROR_CODE_NOT_FOUND"}` as an error - check the raw text for ERROR_CODE_NOT_FOUND when verifying deletes.
pipeline security_baseline.json still lists 105/96/97/184 - expect it to report them missing.
