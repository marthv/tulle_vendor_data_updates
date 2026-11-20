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
    enum status?=applied { values = ["applied", "undone", "partial"] }
    json counts?                            // {table_id: rows repointed}
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

Inputs: `mode` (preview | apply | undo), `survivor_vendor_id`, `loser_vendor_ids[]`, `proposal_id`, `approved_by`,
`expected_counts` (json, from the card), `secret`.

Preconditions (all modes): secret matches; survivor exists in t11 and `merged_into` is null; every loser exists, has
`merged_into` null (apply) or `== survivor` (undo), is not the survivor, and has the same `Category` as the survivor;
at most 3 losers.

**preview**: for each table below, count rows whose column equals a loser id. No writes. Returns `{counts}`.
The bot can call this at card time once it exists, replacing its own paged counts (which skip table 1).

**apply** (order matters; backup-first means a mid-run failure is still fully undoable):
1. Refuse if a `vendor_merge_candidate` row with this `proposal_id` exists (double apply).
2. Insert `vendor_merge_candidate` (status `partial`).
3. For each table/column below, for each matching row: insert `vendor_merge_backup` (old, new), then `db.edit` the
   row's column to the survivor. `text[]` columns (`user.favorited_vendors`) replace the loser id with the survivor
   and de-duplicate. If a write would clash with a unique index (e.g. `vendor_claim` already has the same account on
   the survivor), skip it and record it in `conflicts`.
4. Loser rows in t11: backup `Validated_Data` + `merged_into`, then set `merged_into = survivor`,
   `Validated_Data = "0"`. Never delete.
5. Update the candidate row: status `applied`, counts, conflicts. Return
   `{merge_id, backup_id: merge_id, counts, repointed_total, conflicts}`.
6. Drift check: if any live count is more than 2x `expected_counts` for that table (the data moved a lot since the card
   was posted), refuse BEFORE step 2 with a message the bot shows verbatim.

**undo**: for the candidate row with `proposal_id` (status `applied`): for each backup row in reverse id order, restore
`old_value` **only if the current value still equals the `new_value` we wrote** (otherwise someone changed it since;
record a conflict and leave it). Mark backups `restored`, candidate `undone`, `undone_by`, `undone_at`.

`db.edit`'s `data` block won't take a variable (project_slack_staged_vendor_edits), so each table/column is spelled out
in its own block, the same way ep281 spells out its whitelist. Repoint list:

| table | column(s) | notes |
|---|---|---|
| 10 WPTP PDFs | Vendor_ID | the PDFs themselves |
| 36 Extracted PDF Data | VENDOR_ID | |
| 37 venue_pricing | Vendor_ID | |
| 62 venue_spaces | VENDOR_ID | also rebuilt nightly from 36/37 |
| 87 venue_pdf_facts | Vendor_ID | |
| 92 vendor_price_quotes / 93 vendor_price_items | Vendor_ID | |
| 84 floral_quotes / 85 floral_items | Vendor_ID | |
| 71 venue_preferred_vendors | VENDOR_ID, matched_vendor_id | `matched_vendor_idx` (int t11 id) too |
| 86 venue_pdf_preferred_vendors | venue_vendor_id, matched_vendor_id | |
| 90 rec_wedding_plan | vendor_id | couples' Planner plans |
| 65 vendor_claim, 66 vendor_submission, 67 vendor_message, 70 vendor_calendar | vendor_id | vendor portal; unique (account, venue) clashes -> conflicts |
| 73 feedback_triage_state, 74 pending_edit | vendor_id | |
| 33 Packages, 31 Venue Calculator Entries | vendor_id | |
| 1 user | favorited_vendors (text[]), last_viewed_vendor_id | couples' saved lists |
| — left as-is — | 91 venue_lookup_log.best_vendor_id (history), 57 hub_stats.top_vendor_id (recomputed nightly) | |

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
    precondition ($input.mode == "preview" || $input.mode == "apply" || $input.mode == "undo") { error = "bad mode" }
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
    // ... same for 36, 37, 62, 87, 92, 93, 84, 85, 71 (2 columns), 86 (2), 90, 65, 66, 67, 70, 73, 74, 33, 31, 1 ...
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
