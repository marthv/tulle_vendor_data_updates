---
name: project_rea_app_mapping_parked
description: "PARKED 10-14 by user - use REA (github.com/morluto/rea) to map Tulle's own live pages -> Xano endpoints (dead eps, security map, Xano breakdown doc)"
metadata:
  node_type: memory
  type: project
  originSessionId: 93475281-a9c4-43d5-99ec-8907e3098322
  modified: 2026-10-14T20:25:32.397Z
---

User shared REA (agent reverse-engineering MCP/CLI; website mode = page structure, scripts, network calls) on 2026-10-14 and said "table this, backburner, work on it later".

Proposal on the table (not started): run REA's website mode read-only on our own pages (start: Vendor Discovery + venue page) to record which Xano endpoints each page really calls, join with the Xano endpoint list -> dead-endpoint list, open-but-used vs open-but-unused security map (ep27/ep94/ep199), and the Xano breakdown doc [[project_endpoint_auth_audit]].
Rejected parts: using it on competitors (legal/ToS risk); WeWeb->code rebuild doesn't need REA (we already have page JSON + XanoScript).
Caveats: ArtCraft page doesn't claim REA built it; REA's 238k stars unverified.

**Why:** cleanup/security, no measured customer impact.
**How to apply:** don't raise until the user brings it back; remind only alongside the Xano breakdown doc reminder.
