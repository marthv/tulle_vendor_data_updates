---
name: feedback_sonnet55_everywhere
description: "Owner wants Claude Sonnet 5.5 used for everything (extraction pipeline, pilots, assistant) - stated 2026-10-19"
metadata:
  node_type: memory
  type: feedback
  originSessionId: d461cc1d-f540-4ec1-bcdd-000d8a6a23c5
  modified: 2026-10-19T04:21:31.028Z
---

Use **Sonnet 5.5** for all LLM work going forward: PDF extraction (florals, venue facts, and the existing merged pipeline,
which still runs claude-sonnet-4-6), the assistant (already on claude-sonnet-5-5), and any new jobs.

**Why:** owner, 2026-10-19: "we want to use sonnet 5.5 for everything" (it's ~30% cheaper per token than 4.6).

**How to apply:** default new code to Sonnet 5.5 (check the claude-api skill for the exact id/params). Switching the EXISTING
production extraction off 4.6 is still a code change + deploy - validate on the pilot first and ask before pushing to main.
Related: [[project_florals_preferred_vendors_2026-10-19]] [[project_batch_extraction_filesapi]]
