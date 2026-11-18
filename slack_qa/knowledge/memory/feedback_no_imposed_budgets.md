---
name: feedback_no_imposed_budgets
description: "Never show per-category budget targets the user didn't enter themselves (no default/balanced splits as targets, no \"over plan\"/\"not started\" against them)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: d461cc1d-f540-4ec1-bcdd-000d8a6a23c5
  modified: 2026-10-20T04:08:53.557Z
---

Owner 2026-10-20: "i think we shouldnt put any budgets on people that they're not putting in themselves. they'll be very opinionated on budgets"

**Why:** couples are opinionated about money; a target they didn't choose (e.g. Tulle's Balanced split, 20% venue) reads as Tulle judging them ("Over by $450").

**How to apply:** Budget tab shows only what the user entered: their own total budget, what they've added per category, paid/owed. No suggested per-category targets, progress-vs-target bars, "Over by" or "Not started" chips unless the user set that category amount themselves. Splits (Cost tools, assistant) stay as on-request suggestions the user can choose to adopt; never applied by default. Related: [[project_two_pillar_consolidation]]
