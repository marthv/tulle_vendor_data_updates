---
name: known-open-items
description: "Running list of known-open, non-blocking defects in the Tulle app - moved out of MEMORY.md to keep the index under its size limit"
metadata:
  type: project
---

Known-open, none blocking (as of 2026-09-16). Moved here from MEMORY.md on 2026-09-16 to keep
the index under its 24.4KB cap.

the PDF popup still offers "Submit a pricing PDF -> 1 Week" to
  people who already have access, still routes "View Plans" to /pricing rather than one-click
  Stripe, and still uses a **purple** card (CLAUDE.md forbids purple); `/pricing?upgrade=unavailable`
  shows no message; signed-out buy buttons go dim instead of routing to sign-in; the
  `user_updates` realtime refresh (better fix for stale access state) is editor-only;
  **"+3 Months" and "+6 Months" both point at collection index [2]**; ep26 `POST /sessions` is
  unauthenticated and lets the caller set the price; the Slack webhook `B098L47TRGV` is still
  hardcoded in frontend workflow code and still needs rotating.

See also [[feedback_simplify_not_stack]] - several of these are deletions, not fixes.
