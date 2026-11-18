---
name: xano-write-access-paths
description: "The Xano metadata REST API CANNOT write function/endpoint logic - only the MCP can, and the MCP can be driven over raw HTTP with curl when it isn't connected to the session"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 548bb6e7-2cb7-40b4-8faa-c1145ceeeaa3
  modified: 2026-08-12T05:48:47.747Z
---

Two separate Xano write channels, and they are not equivalent.

**Metadata REST API** (`https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta/workspace/1/...`, bearer token in `.mcp.json` → `mcpServers.xano-meta.headers.Authorization`). 215 paths. Good for tables and rows, useless for logic:
- `POST /table`, `PUT /table/{id}/schema`, `PUT /table/{id}/index`, `GET|POST|PUT|DELETE /table/{id}/content[/{row}]`, `POST /table/{id}/content/search`.
- **Body shape for schema/index writes is `{"schema":[...]}` / `{"index":[...]}`, NOT a bare array** — a bare array returns `Unable to locate input: schema`.
- **`PUT /table/{id}/schema` silently STRIPS validators** (trim etc.), confirming [[reference_xano_mcp_quirks]]. Normalize in code instead.
- A **nullable timestamp with `default:"null"` makes the content API reject inserts** that omit it (`Invalid timestamp format`). Use non-nullable `default:"now"`.
- **`PATCH /table/{id}/content/{id}` silently no-ops** — returns nothing, no error, row unchanged. Use **PUT with the full record**.
- `GET /function/{id}` and `GET /table/{id}` return **`xanoscript: null`** — bodies are not readable here.
- **There is NO route that writes function/endpoint logic.** `POST/PUT /function` accepts a `xanoscript` field and silently ignores it (the function is created but `input` stays `[]`). Verified against the OpenAPI spec at `GET /apispec:meta?type=json`.

**MCP server** (`https://xqtb-2ma7-ijfy.n7e.xano.io/x2/mcp/meta/mcp/stream`) — 220 tools, the ONLY way to author XanoScript: `createFunction`, `updateFunction`, `createAPI`, `updateAPI`, `createTask`, `createTableTrigger`, `runWorkspaceFunction`, `searchTableContent`, `documentation`.

**If the MCP is not connected to the session, drive it over raw HTTP with curl** — it is a standard streamable-HTTP MCP:
1. `POST` an `initialize` JSON-RPC body → read the **`mcp-session-id` response header**.
2. `POST` `notifications/initialized` with that header.
3. `POST` `{"method":"tools/call","params":{"name":...,"arguments":{...}}}`.
Responses are **SSE** (`data:` lines), so parse them; `getFunction`/`getAPI` return **`xanoscript` as `{value, status}`, not a bare string**. A working harness lives in the session scratchpad as `xmcp.sh` + `mcp_parse.py`.

**Sandbox constraint:** Python has **no direct network access** (`getaddrinfo failed`); only curl is proxied. So the working split is curl for I/O, Python for JSON — sharing files through a path both can see (the scratchpad), because bash `/tmp` is not visible to Windows Python.

**Never build JSON payloads containing XanoScript in a bash heredoc** — bash collapses backslashes, which silently corrupts lambda regexes. Build them in a Python script file.

**Live XanoScript lambdas in this workspace contain ZERO backslashes** (checked fn21, fn44, fn49). Backslashes do not survive the `"""` block intact, so write character loops instead of regex — see `fn54_rebuild_venue_spaces_v1_2026-08-12.xs` for the pattern.
