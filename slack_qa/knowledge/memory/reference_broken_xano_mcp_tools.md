---
name: reference_broken_xano_mcp_tools
description: "Five broken xano MCP tools are TWO bugs — empty db.query table name, and a where/sort alias case mismatch"
metadata: 
  node_type: memory
  type: reference
  originSessionId: d3d425db-e9a4-482d-a40c-4ec0df0fef46
  modified: 2026-09-07T00:29:42.066Z
---

Five `mcp__xano__` tools fail. It is **not one bug**, and it is **not "everything that reads
the mappings table"** — `list_wptp_pdfs` reads WPTP PDFs and also fails. Diagnosed 2026-09-06.

**Bug 1 — empty table name.** `search_wptp_vendors` (tool 23), `list_wptp_pdfs` (22),
`get_vendor_pricing_details` (18) all call `db.query ""` / `db.get ""`. Referencing
`$db.<alias>.<column>` in the `where` does NOT bind a datasource; the table must be named.

**Bug 2 — alias case mismatch.** `tool_wedding_search_venues` (2) and
`tool_wedding_search_photographers` (4) name the table correctly but mix cases:
`where` uses `$db.WPTP_Updated_Mappings.*` while `sort` uses
`{wptp_updated_mappings.Clicks_on_Venue: "desc"}`.

Both are still UNFIXED — diagnosed only. Neither affects
[[project_slackbot_vendor_visibility]]'s ep279, which names the table explicitly.

**Why it matters beyond the tools:** these are exposed on a Xano MCP server whose connection
URL is an unauthenticated read key — see [[project_endpoint_auth_audit]]. Fixing them makes
that exposure more useful to an attacker, so gate the MCP server first.
