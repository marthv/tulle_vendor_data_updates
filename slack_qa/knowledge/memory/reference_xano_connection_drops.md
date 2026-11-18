---
name: reference_xano_connection_drops
description: "The recurring \"Xano keeps dropping/losing connection\" has TWO distinct causes; how to tell them apart"
metadata: 
  node_type: memory
  type: reference
  originSessionId: d23ed926-30e8-4629-8b13-ebaeab6c171a
---

The recurring "Xano keeps losing connection / dropping" symptom has **two distinct causes** (both seen 2026-06-14/15):

1. **Xano admin/meta API outage** — MCP calls return nginx `503 Service Temporarily Unavailable`. Server-side, transient. The public data API (api:GynP5T1B/...) keeps serving fine during these.
2. **Local DNS/network blip on the user's machine** — MCP returns "Unable to connect / Was there a typo in the url or port?" and `curl` to the SAME Xano public URL fails with **exit code 6 (couldn't resolve host)**, HTTP 000. This also breaks unrelated sites.

**Diagnostic to tell them apart:** run `curl` against the Xano *public* endpoint AND `curl https://www.google.com`.
- Public Xano works but MCP 503s → cause #1 (Xano admin side, wait it out).
- Both fail with exit 6 / can't resolve → cause #1 is NOT it; it's local network (#2). Retrying usually recovers in seconds.

Mitigation when meta API is flaky: pure config changes (e.g. cache TTL) can be done in the Xano **dashboard** (Endpoint → Settings) instead of the MCP, which uses a different path. Related: [[project_tulle_focus]].
