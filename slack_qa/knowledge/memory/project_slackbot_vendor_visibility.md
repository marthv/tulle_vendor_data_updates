---
name: project_slackbot_vendor_visibility
description: "Slack bot hides vendors via ep279; visibility is table 11 Validated_Data (TEXT \"1\"/\"0\"), NOT a column called visibility"
metadata: 
  node_type: memory
  type: project
  originSessionId: d3d425db-e9a4-482d-a40c-4ec0df0fef46
  modified: 2026-09-07T00:29:34.254Z
---

Repo `marthv/feedback_bot` (Node, Railway). React 🫣 on a Slack message containing a vendor
ID → POST to Xano → vendor hidden. Removing the reaction unhides (`ENABLE_UNDO=true`).
Two vendor IDs in one message = refused, never guessed.

**Visibility on table 11 is `Validated_Data` — a TEXT column holding `"1"` (visible) /
`"0"` (hidden). There is NO column called `visibility`.** Confirmed 2026-09-06: ep119 and
the `tool_wedding_search_*` tools all filter `Validated_Data == 1`, and there is a
composite index on `(Category, Validated_Data, id)`. Write the STRING — not an int, not a
bool. Vendor key is `Vendor_ID` (text, e.g. `V2574`); table 36 uses `VENDOR_ID` instead.

The table's display name is `WPTP Updated Mappings` (with spaces). `wptp_updated_mappings`
is only the snake_cased XanoScript alias — that is why it shows up in error strings.

`ep279 POST /vendor/visibility` lives in **API group 3 (`slack`, `api:aow91bcd`)**, not the
WeWeb group. Gated by a `secret` **body** field compared to a literal, copying ep205 —
**XanoScript has no documented way to read request headers**, so the bot's `X-Api-Key`
header is sent but ignored. Smoke-tested all paths incl. a full round trip on V4341.

**Open:** ep119 caches 7100s (~2h), so a hidden vendor keeps appearing in search long after
the bot says ✅. Either say so in the Slack copy or bump ep119's `cache_v` after a write.

**Leave alone:** `ENABLE_ASK=false`. Turning it on exposes a Xano MCP connection URL that is
currently an unauthenticated read key to the whole pricing dataset — see
[[project_endpoint_auth_audit]]. Related: [[reference_broken_xano_mcp_tools]],
[[reference_table11_state_multivalue]].
