# Vendor merge in Xano: proposal for owner approval (2026-11-20)

**Status: NOT BUILT.** Nothing in this file has been created in Xano. Tulle Bot's "Approve and apply" on a merge card
is a dry run (`DEDUPE_DRY_RUN=1`, the default) until (1) the owner approves this design, (2) the pieces below exist,
(3) 50 suggested groups have been hand-checked, and (4) `DEDUPE_DRY_RUN=0` + `XANO_MERGE_URL` + `XANO_MERGE_SECRET`
are set on tulle-slack-qa.

Before every write below: back up per CLAUDE.md 12.5 (`.claude/xano_backups/<name>_pre_<change>_<date>.xs`, in full).
The XanoScript is a draft: validate it against the Xano `documentation` tool (`table`, `api`, `database`) before
`createAPI` / `addTable`. XanoScript parses fine and fails at runtime (reference_xanoscript_traps), so the endpoint
is smoke-tested in `preview` mode on a hand-picked pair before any `apply`.

## 1. Schema changes (3)

### 1a. Table 11 `WPTP Updated Mappings`: one new column
```
// Vendor_ID of the record this one was merged into. Set ONLY by POST /vendor/merge. Null = a live record.
// A retired record keeps every original field (rollback source) and is hidden from search via Validated_Data "0".
text merged_into? filters=trim
```
plus a btree index on `merged_into`. Table 11 has no validators, so adding the column via a schema-element add is
safe (`updateTableSchema` would strip validators on other tables; reference_xano_mcp_quirks). Back up the table
schema first. Search (ep119) already filters `Validated_Data == 1`, so retired rows vanish from search without
touching ep119.

### 1b. New table `vendor_merge_candidate` (one row per merge the bot proposed and a human approved)
```
// One row per vendor merge approved in Slack (Tulle Bot dedupe). Written ONLY by POST /vendor/merge.
// status: applied -> undone. The rows it moved are in vendor_merge_backup (merge_id = this id).
table vendor_merge_candidate {
  auth = false
  schema {
    int id
    timestamp created_at?=now
    text survivor_vendor_id filters=trim
    text[] loser_vendor_ids
    text proposal_id? filters=trim          // Tulle Bot proposal id (Slack card)
    text approved_by? filters=trim          // Slack user id
    enum status?=applied { values = ["partial", "applied", "partial_undo", "undone"] }
    json counts?                            // {table_id: rows repointed}
    json snapshots?                         // both full table-11 rows exactly as they were before the merge
    json counters?                          // per rule column: {rule, before: {vendor_id: v}, after}
    json conflicts?                         // rows skipped (unique-index clash) or not restored on undo
    text undone_by? filters=trim
    timestamp undone_at?
  }
  index = [
    {type: "primary", field: [{name: "id"}]}
    {type: "btree", field: [{name: "survivor_vendor_id", op: "asc"}]}
    {type: "btree|unique", field: [{name: "proposal_id", op: "asc"}]}   // a proposal can only apply once
  ]
}
```

### 1c. New table `vendor_merge_backup` (one row per field changed, written BEFORE the change)
```
// Rollback source for vendor merges: the old value of every field POST /vendor/merge changed, written before the
// change. Never edited or deleted. Undo restores from here.
table vendor_merge_backup {
  auth = false
  schema {
    int id
    timestamp created_at?=now
    int merge_id                 // vendor_merge_candidate.id
    int table_id                 // e.g. 10, 37, 1
    int row_id                   // id of the row in that table
    text column filters=trim     // e.g. Vendor_ID, favorited_vendors
    json old_value?              // exact previous value (text or text[])
    json new_value?
    bool restored?=false
  }
  index = [
    {type: "primary", field: [{name: "id"}]}
    {type: "btree", field: [{name: "merge_id", op: "asc"}]}
  ]
}
```

## 2. Endpoint `POST /vendor/merge` (API group 3 `slack`, `api:aow91bcd`, same `secret` body gate as ep279-283)

Inputs: `mode` (preview | apply | undo | status), `survivor_vendor_id`, `loser_vendor_ids[]`, `proposal_id`, `approved_by`,
`expected_counts` (json, from the card), `secret`.

Preconditions: secret matches; at most 3 losers; every loser is not the survivor and has the survivor's `Category`.
- `apply` (new): survivor and every loser have `merged_into` null. **Chained merges:** a survivor that was itself
  merged away is refused, so A→B then B→C can't happen silently; merge into the final record instead.
- `apply` (resume): a candidate row with this `proposal_id` and status `partial` exists → continue it. Resume
  compares each row's LIVE value, never "a backup exists": live == the new value → already done, skip; live == the
  backed-up old value → write it (reusing that backup row); anything else → someone changed it since, record a
  conflict and leave it. Status `applied`/`undone` → refuse (no double apply).
- `undo`: the candidate row is `applied`, **`partial`** (a half-finished merge is undoable) or `partial_undo`
  (finishes an interrupted undo). Refused if the
  survivor has since been merged into something else (undo that later merge first).
- `status` and `preview`: read-only, no state preconditions beyond the secret.

**preview**: for each repoint column below, count rows matching a loser. No writes. Returns `{counts}`.

**apply** (backup-first, one row at a time, so any interruption leaves a `partial` merge that can be resumed or undone):
1. Drift check before anything is written: if a live count is more than 2x `expected_counts` for a table, refuse with
   a message the bot shows verbatim.
2. Insert `vendor_merge_candidate` (status `partial`, `approved_by`) or reuse the partial one. **The endpoint reads
   the survivor and loser rows LIVE at this moment and computes `snapshots` (minus User_Email and google_data_cache)
   and the column plan (`counters`) itself.** The bot's scan-time snapshots and counters are display-only and are not
   sent.
3. For each **repoint** column in the generated table below, for each matching row: insert a `vendor_merge_backup`
   row (table, row id, column, old value, new value) **then** write the new value. Arrays: replace + de-duplicate,
   order kept. JSON: replace the exact V-id token and any int `*vendor_idx` equal to a loser's table-11 row id
   (t43 cards carry both `vendor_id` and `vendor_idx`; t66 payload carries `venue.vendor_id`, `matched.vendor_idx`).
   A unique-index clash (t65 / t70 same account on the survivor) is skipped and recorded in `conflicts`.
4. **rebuild** tables (t62 venue_spaces, t71 venue side) are not touched: their unique keys `space_key`
   ("V3794|grandhall") and `pv_key` ("V1|lulucakeboutique") embed the venue's Vendor_ID, so repointing would leave keys
   naming the old record or collide with the survivor's own rows. The nightly denorm regenerates them for the
   survivor from the moved t36/t37 rows. **UNVERIFIED, check before build:** whether that rebuild deletes the
   retired record's now-orphaned rows (if it doesn't, the retired rows stay but are never shown, because the record is
   hidden and search filters on it). Test on one hand-picked pair first.
5. Table 11, per the generated column rules (computed from the live rows): back up every column that changes, then
   write it. Each loser's Clicks_on_Venue is set to 0 after backup so clicks are never counted twice. Losers get
   `merged_into = survivor` (backed up). Never delete. **Validated_Data and gold_chip are owner-only and are never
   written by a merge**: the card and Details say when a duplicate was visible so a person checks visibility after
   merging. Hiding the retired record needs an explicit owner exception (asked 2026-11-20; pending).
6. Candidate row: status `applied`, counts, conflicts. Return `{merge_id, backup_id, counts, repointed_total,
   conflicts, status}`.

**status** (Re-check): look up `vendor_merge_candidate` by `proposal_id`; return `{status: applied | partial |
undone | partial_undo | none, merge_id, repointed_total}`. Read-only. **The mode check must accept `status`.**

**undo** (exact, from the backup only): for each backup row of this merge not yet `restored`, newest first, write
`old_value` back **only if the current value still equals the `new_value` we wrote**; otherwise record a conflict and
leave it. Mark each restored. Candidate → `undone`, or `partial_undo` if conflicts or an interruption left rows
unrestored (running undo again finishes it).
- **Counters on undo:** each loser's counter is set back to its backed-up value. The survivor's becomes
  `live - merged_value + survivor_before` (merged_value = what the merge wrote, survivor_before = its backed-up value),
  so clicks the survivor received after the merge are kept and nothing is counted twice. Counters and
  scrape_last_attempted_at are exempt from the "current value still equals what we wrote" guard (they legitimately
  move after a merge); every other column keeps the guard.
- **`partial_undo` is always finishable:** running undo again continues from the backup rows not yet `restored`,
  with the same rules.
- Rows deleted between merge and undo (a couple deletes a package, the nightly rebuild drops a t62 row) are skipped
  and listed in `conflicts`; nothing is re-created.
- **Direct links to the retired id** (emails, last_viewed, old chat cards) are repointed where stored; links outside
  Xano (sent emails) still use the old id. **UNVERIFIED:** whether the vendor page renders a hidden record or empty;
  if empty, the page should follow `merged_into` (a WeWeb change, separate approval).

`db.edit`'s `data` block won't take a variable (project_slack_staged_vendor_edits), so each table/column is spelled out
in its own block, the same way ep281 spells out its whitelist.

### Generated from dedupe.py: references, name-keyed fields, column rules

Bot side: every JSON column is matched by KEY only (never by searching text), the payload to this endpoint carries
no snapshots, and nothing the bot stores or shows contains User_Email, google_data_cache or any email address.

The block below is written by `python gen_merge_doc.py` from `dedupe.REF_TABLES`, `NAME_KEYED` and `COUNTER_RULES`;
a test fails if it drifts from the code. Table schemas: all 58 tables of workspace 1, read 2026-11-20 (read-only).
Checked and holding no vendor reference: 7 To Do Items, 35 Feedback, 42 rec_chats, 79 rec_usage (tool names and
categories only), 81 rec_feedback, 12 Referrals, and the remaining admin / benchmark tables. **Verify before build:**
the element format of `user.favorited_vendors` (Vendor_ID text assumed) on a test account.

<!-- generated:start (python gen_merge_doc.py) -->
#### Every vendor reference (from `dedupe.REF_TABLES`)

| table | label | column | type | action | rule | couple-side |
|---|---|---|---|---|---|---|
| 10 | PDF files | Vendor_ID | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 36 | Extracted PDF data | VENDOR_ID | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 37 | Venue pricing | Vendor_ID | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 62 | Venue spaces | VENDOR_ID | text | **not repointed**: regenerated for the survivor by the nightly denorm (unique key embeds the Vendor_ID) | - |  |
| 87 | Venue PDF facts | Vendor_ID | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 92 | Service price quotes | Vendor_ID | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 93 | Service price items | Vendor_ID | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 84 | Floral quotes | Vendor_ID | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 85 | Floral items | Vendor_ID | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 71 | Venue preferred vendors (venue side) | VENDOR_ID | text | **not repointed**: regenerated for the survivor by the nightly denorm (unique key embeds the Vendor_ID) | - |  |
| 71 | Venue preferred vendors (as a named partner) | matched_vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 71 | Venue preferred vendors (as a named partner) | matched_vendor_idx | int row id | repoint | replace the loser's table-11 row id with the survivor's |  |
| 86 | Preferred vendors named on PDFs | venue_vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 86 | Preferred vendors named on PDFs | matched_vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 65 | Vendor portal claims | vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 66 | Vendor portal submissions | vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 66 | Vendor portal submissions | payload | json | repoint | by KEY only: a string under vendor_id / best_vendor_id / matched_vendor_id / venue_vendor_id equal to a loser id, and an int under any *vendor_idx key equal to a loser row id. Free text is never searched |  |
| 67 | Vendor portal messages | vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 70 | Vendor portal calendars | vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 73 | Feedback triage | vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 74 | Staged vendor edits | vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's |  |
| 1 | Couples' saved lists, last viewed, pricing-page context (users) | favorited_vendors | text[] | repoint | replace in place, de-duplicate, keep order | **yes** |
| 1 | Couples' saved lists, last viewed, pricing-page context (users) | last_viewed_vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's | **yes** |
| 1 | Couples' saved lists, last viewed, pricing-page context (users) | pricing_viewed_ctx | json | repoint | by KEY only: a string under vendor_id / best_vendor_id / matched_vendor_id / venue_vendor_id equal to a loser id, and an int under any *vendor_idx key equal to a loser row id. Free text is never searched | **yes** |
| 90 | Couples' Planner plans | vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's | **yes** |
| 33 | Couples' budget packages | vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's | **yes** |
| 31 | Couples' venue calculator entries | vendor_id | text | repoint | replace the loser Vendor_ID with the survivor's | **yes** |
| 43 | Planner chat cards | cards | json | repoint | by KEY only: a string under vendor_id / best_vendor_id / matched_vendor_id / venue_vendor_id equal to a loser id, and an int under any *vendor_idx key equal to a loser row id. Free text is never searched | **yes** |
| 91 | Planner venue lookup log | best_vendor_id | text | **keep** (history / derived) | - |  |
| 91 | Planner venue lookup log | alternatives | json | **keep** (history / derived) | - |  |
| 72 | Admin audit log | target_id | text | **keep** (history / derived) | - |  |
| 57 | Hub stats (top venue) | top_vendor_id | int row id | **keep** (history / derived) | - |  |

#### Fields keyed by vendor NAME (from `dedupe.NAME_KEYED`; not reachable by an id repoint)

| table | column | handling |
|---|---|---|
| 1 user | last_viewed_vendor_name | updated to the survivor's name when last_viewed_vendor_id is repointed |
| 90 rec_wedding_plan | vendor_name | updated with vendor_id; free-text items (vendor_id empty) are name-only |
| 33 Packages | vendor_name | manual packages have no vendor_id: name-only, left as typed |
| 31 Venue Calculator Entries | venue_name | updated with vendor_id when present |
| 65 vendor_claim / 74 pending_edit | venue_name / vendor_name | denormalised; updated with vendor_id |
| 36 Extracted PDF Data / 37 venue_pricing | VENUE_NAME / Venue_Name | the name printed on the PDF; left as-is |
| 71 venue_preferred_vendors | partner_key / partner_name / matched_vendor_name / venue_name | partner_key is a NAME/domain-derived identity: rows that collide after the repoint are combined |
| 86 venue_pdf_preferred_vendors | vendor_name_raw / vendor_name_norm | as printed on the PDF; left as-is |
| 45 WPTP_Upload_Record | Vendor_Name | upload log with NO vendor id at all: unreachable by id |
| 6 WPTP - Pending Uploads | Name | pre-ingest submissions, name only |
| 43 rec_messages / 80 rec_memory | content / notes | free text written by the Planner/couple: left as-is |
| 57 hub_stats | top_vendor_name | recomputed nightly |
| 11 WPTP Updated Mappings | mk_name_key | recomputed for the survivor after the merge |

#### Table 11 column rules on merge (from `dedupe.COUNTER_RULES`)

| column | rule | note |
|---|---|---|
| Clicks_on_Venue | **sum** S + L; each L set to 0 | couples' clicks add up; each loser's own count is set to 0 after backup (no double count), restored on undo |
| Collection | **union**, S order first | every collection tag, survivor's order first |
| Flags | **union of tokens** | every flag, de-duplicated |
| Max_Capacity_Seated | **max** | largest stated capacity |
| Time_of_Submission | **earliest** | first time this venue was submitted |
| google_data_last_fetched | **from the record whose Place_ID is kept** | taken from the record whose Place_ID is kept |
| scrape_last_attempted_at | **most recent** |  |
| scrape_cost_usd | **sum** S + L; each L set to 0 | money already spent scraping both |
| Website | **fill if empty** (S kept; L only where S is blank) |  |
| Description | **fill if empty** (S kept; L only where S is blank) |  |
| Contact_Information | **fill if empty** (S kept; L only where S is blank) |  |
| Place_ID | **fill if empty** (S kept; L only where S is blank) |  |
| Address | **fill if empty** (S kept; L only where S is blank) |  |
| Venue_Type | **fill if empty** (S kept; L only where S is blank) |  |
| Type_of_Photography | **fill if empty** (S kept; L only where S is blank) |  |
| Type_of_Entertainment | **fill if empty** (S kept; L only where S is blank) |  |
| Type_of_Beauty | **fill if empty** (S kept; L only where S is blank) |  |
| image | **fill if empty** (S kept; L only where S is blank) |  |
| image_1 | **fill if empty** (S kept; L only where S is blank) |  |
| image_2 | **fill if empty** (S kept; L only where S is blank) |  |
| image_3 | **fill if empty** (S kept; L only where S is blank) |  |
| flt_space_count | **recompute** for S after the merge | re-derived from the merged pricing rows by the nightly denorm |
| Validated_Data, gold_chip | **never auto-written** (owner-only) | card/Details say if a duplicate was visible so a person checks visibility after merging; pending an explicit owner exception |
| User_Email, google_data_cache | **never stored, shown or sent** | User_Email is the submitting couple's email; the Google cache follows the kept Place_ID server-side |
| Name, Category, State, Country, lat, lng, scrape_status, scrape_last_error | **keep S** | |
| every other flt_* / mk_* denorm column | **recompute** for S | derived from the moved rows |
| merged_into (L only) | := S Vendor_ID | |
<!-- generated:end -->

Counters outside table 11: venue_preferred_vendors.source_row_count and venue_spaces.contract_count are regenerated
by the rebuild; hub_stats.top_vendor_clicks is recomputed nightly. Per-couple counters on table 1
(total_vendor_views, total_pdf_views, FreeViewsRemaining) count a couple's actions, not a vendor's, so they don't change.

### Full record kept (exact undo)
- `vendor_merge_candidate.snapshots`: both table-11 rows, every field, as they were the moment before the merge.
  `vendor_merge_candidate.counters`: each rule column's rule, value per record before, and the value written.
- `vendor_merge_backup`: every changed row/column (old and new), user-side fields included. Undo reads only this.
- The bot's proposal store keeps the same snapshots, the per-table row ids it found, and the counter plan; the card's
  **Details** button shows them.


### Draft XanoScript (pattern; two tables shown, the rest repeat block 3)
```
query "vendor/merge" verb=POST {
  api_group = "slack"
  input {
    text mode filters=trim
    text survivor_vendor_id filters=trim
    text[] loser_vendor_ids
    text proposal_id filters=trim
    text approved_by? filters=trim
    json expected_counts?
    text secret
  }
  stack {
    precondition ($input.secret == "<same literal as ep279>") { error_type = "accessdenied" error = "bad secret" }
    precondition ($input.mode == "preview" || $input.mode == "apply" || $input.mode == "undo" || $input.mode == "status") { error = "bad mode" }
    db.query WPTP_Updated_Mappings { where = $db.WPTP_Updated_Mappings.Vendor_ID == $input.survivor_vendor_id
      return = {type: "single"} } as $survivor
    precondition ($survivor != null && $survivor.merged_into == null) { error = "survivor missing or already merged" }
    // ... per-loser checks (exists, same Category, merged_into null for apply / == survivor for undo) ...

    // block 3 (repeated per table/column): table 10 WPTP PDFs.Vendor_ID
    foreach ($input.loser_vendor_ids) { each as $loser {
      db.query WPTP_PDFs { where = $db.WPTP_PDFs.Vendor_ID == $loser return = {type: "list"} } as $rows
      conditional { if ($input.mode == "apply") {
        foreach ($rows) { each as $r {
          db.add vendor_merge_backup { data = {merge_id: $merge.id, table_id: 10, row_id: $r.id, column: "Vendor_ID",
                                               old_value: $r.Vendor_ID, new_value: $input.survivor_vendor_id} }
          db.edit WPTP_PDFs { field_name = "id" field_value = $r.id data = {Vendor_ID: $input.survivor_vendor_id} }
        }}
      }}
      // preview: add ($rows|count) to $counts["10"]
    }}
    // ... same for every repoint column in the generated table (NOT t62 / t71 venue side: rebuild) ...
  }
  response = {merge_id: $merge.id, backup_id: $merge.id, counts: $counts, repointed_total: $total,
              conflicts: $conflicts}
}
```

## 3. Rollout (each step asks the owner first)
1. Approve this design; back up t11 schema; add the column and the two tables (additive; the live app never reads them).
2. Create `POST /vendor/merge`; smoke-test `preview` on Allied Arts Guild (V105 / V13433) and one non-venue pair.
3. Turn on suggestions only: set `DEDUPE_CHANNEL` (dry run still on). Hand-check 50 cards (approve = dry run, so
   nothing moves); measure how many were true duplicates. Bar: at least 48 of 50 correct, else tighten the rules.
4. `apply` + `undo` end to end on ONE pair, verify in WeWeb (PDFs show on the survivor, the loser is gone from search,
   a couple's saved list shows the survivor), then undo and verify it comes back.
5. `DEDUPE_DRY_RUN=0`.

## 4. Stop the backlog refilling
The duplicate pairs in the 2026-11-20 sample look like PDF submissions creating a new vendor row instead of matching an
existing one. Until the submission path matches on Place ID / name+city first, the nightly scan will keep finding new
pairs. That fix lives in the submission flow (Xano + WeWeb) and needs its own approval.
