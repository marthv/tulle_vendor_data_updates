---
name: feedback_ask_before_live
description: Ask before any change that goes live or spends money, even mid-task; list what's pending instead of chaining
metadata:
  type: feedback
---

Ask the user before each action that goes live (deploys, Xano pushes, repo pushes WeWeb builds from) or costs money
(eval runs, extraction), even inside an approved task. Editor-only and local changes are fine.

**Why:** 2026-10-04 the user said "worried im letting you run a little rampant.." after a session where I deployed
the rec-engine and widened an approved one-endpoint lockdown to five without asking each time.

**How to apply:** when a task produces a live step, stop and list it with its risk and rollback; give a ledger of
live / editor-only / local changes when work piles up. [[project_ai_recommendation_engine]]
