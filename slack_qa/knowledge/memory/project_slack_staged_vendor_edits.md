---
name: project_slack_staged_vendor_edits
description: "Staged vendor-edit flow (table 74 + fn62 + eps 280-283) — propose/approve, never direct write; Slack side not built"
metadata: 
  node_type: memory
  type: project
  originSessionId: d3d425db-e9a4-482d-a40c-4ec0df0fef46
  modified: 2026-09-07T01:03:31.449Z
---

Arbitrary vendor-field edits from Slack go through **propose → diff → explicit approve**, never
through the 🫣 reaction path ([[project_slackbot_vendor_visibility]]). A reaction carries no
parameters and writes immediately — fine for a reversible boolean, wrong for data. Same shape
the feedback-triage tab and vendor portal already use.

Built 2026-09-06, **both sides complete**. `/tulle edit V4341 Field = value` stages and posts a
diff with Approve/Discard buttons; only Approve writes. `/tulle pending` / `applied` read the queue.

**Bolt's ExpressReceiver serves events, slash commands AND interactivity on the SAME
`/slack/events` path** — interactivity does NOT need a second request URL, and manifest.yml
lists that one URL three times. Adding the `commands` scope forces a Slack reinstall, which
**changes SLACK_BOT_TOKEN** — copy the new one into Railway or the service crashloops.

`REQUIRE_SECOND_APPROVER` (default off) blocks self-approval; the button `value` packs
`editId:proposerId` so the check needs no extra Xano round trip. Discarding your own proposal
stays allowed.

- **table 74 `pending_edit`** — proposals. A row has NOT touched live data.
- **fn62 `vendor_edit_field_spec`** — the whitelist, case-insensitive, returns canonical name.
- **ep280 stage / ep281 apply / ep282 discard / ep283 pending** — group 3 (`api:aow91bcd`),
  same `secret` body gate as ep279. **ep281 is the ONLY live write in the flow.**

**The whitelist is duplicated on purpose and this is the main maintenance trap.**
`db.edit`'s `data` block will NOT take a variable, so there is no dynamic-key write —
ep281 spells out every writable column in an if/elseif chain. Widening fn62 without adding
a branch throws a loud `configerror` (the trailing `else`) instead of silently no-op'ing.
Inline `data = {}|set:$var:$val` causes an **ERROR_FATAL** on createAPI — don't retry it.

Editable: Name, Website, Description, Contact_Information, Max_Capacity_Seated, Venue_Type,
Type_of_{Photography,Entertainment,Beauty}. Excluded with reasons in fn62's description —
notably `Validated_Data` (ep279 owns it; two write paths to one column is an undebuggable race),
`Category` (gates the PI panel — [[project_pi_panel_nonvenue_bug]]), State/Country (denormalised
into flt_states), and all pricing (percentiles derive from it).

**Drift guard is the load-bearing safety property:** apply re-reads the live value and refuses
if it changed since staging, so an approver can never apply a diff different from the one they
saw. Also refuses non-pending rows, and re-validates the field at apply time rather than
trusting the staged row. `previous_value` is retained after apply as the rollback source.

Verified end to end on V4341 incl. drift, supersede, double-apply refusal and rollback.
