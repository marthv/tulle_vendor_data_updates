---
name: pipeline-flask-app-status
description: pipeline/app.py is the Railway admin dashboard — in scope as of 2026-06-19 (was previously off-limits)
metadata: 
  node_type: memory
  type: feedback
  originSessionId: d846aec4-97a0-4767-b1ed-49dd4a3832b8
---

The Flask app at `pipeline/app.py` + `pipeline/templates/index.html` is the **Railway-deployed internal admin dashboard** (tabbed UI: Review Queue, Pipeline A extraction, Pipeline B enrichment, Email List, and now Venue Pricing). It talks to Xano via `xget/xpost/paginate` helpers (Bearer `XANO_API_TOKEN`), base `XANO_ADMIN = https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B`.

**History:** Earlier (March 2026) the user said to ignore this directory and focus only on Xano API perf. That changed on **2026-06-19**: the user explicitly asked to build a new **Venue Pricing tab** here, so the directory is now active work.

**How to apply:** Treat `pipeline/` as editable when the user directs work there. Still don't go reworking it unprompted — but it is no longer blanket off-limits. See [[project_venue_pricing_tab]].
