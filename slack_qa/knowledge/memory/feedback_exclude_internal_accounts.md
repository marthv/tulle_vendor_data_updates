---
name: feedback_exclude_internal_accounts
description: "Always exclude internal accounts from user-facing numbers: vivek.marthi* / @tulletogether.com emails; ids 14998, 30614, 32636 (owner/tests), 118 (Desiree O'Riley/Gaddis), 5622 (Kate Beckman) + their test accts 34249, 33167, 185"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 197623b5-67b9-4b3f-ae23-e4a6497efc21
  modified: 2026-10-27T01:43:42.376Z
---

When reporting real-user data (goals, NPS, assistant usage, signups, buyers, A/B arms), drop internal accounts:
- any email containing `vivek.marthi` (owner signs up as vivek.marthi+<tag>@gmail.com) or ending `@tulletogether.com`;
- ids 14998 (owner rec test account), 30614 (vivek.marthi+test_pi), 32636 (onboarding goals test 2026-10-12), 31619 ([email], owner alt "Vivek Marthi", found 2026-11-03);
- team (owner 2026-10-27): **118** Desiree O'Riley / [name] ([email], admin), **5622** Kate Beckman ([email], admin); their test/old accounts 34249 ("Des", desi.gaddis…@gmail.com), 33167 ("Katest", kstebeckman.kb+485), 185 (katekingsings, old Kate).
**Why:** 2026-10-12 owner asked to remove his own test signup from a goals count; 2026-10-27 asked to add Desiree and Kate after they turned up in the Railway search A/B arm.
**How to apply:** filter by email when the data has it; when it doesn't (Mixpanel by distinct_id), filter these ids. Code copy: tulle_rec_planner/rec_watch.py INTERNAL_USER_IDS.
