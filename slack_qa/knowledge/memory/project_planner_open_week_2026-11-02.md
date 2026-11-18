---
name: project_planner_open_week_2026-11-02
description: "Owner 11-02: Tulle Planner opened to many more people + much looser free-user limits for ~1 week (to ~11-09) to gather speed/accuracy feedback; lower payment rate accepted; then close back up. Also: 3-mo vendor views CSV delivered; Mixpanel has no per-PDF ids before Nov 2026."
metadata:
  node_type: memory
  type: project
  originSessionId: 42710918-a22b-465e-b785-4afbba11bb9b
  modified: 2026-11-03T04:57:03.866Z
---

Owner 2026-11-02: opened Tulle Planner to a broader audience and let free users use it much more, to improve
1) speed and 2) accuracy from feedback. Explicitly accepts a LOWER PAYMENT RATE during the test; plan = ~1 week
(to ~2026-11-09), then close it back up. Exact before/after free limits NOT yet recorded - ask, then log in the
experiment registry [[project_experiment_registry]].

**How to apply:** any payment-rate read covering 11-02..~11-09 is confounded by this - say so. The Texas Forever $89 test
[[project_texas_only_forever_89_2026-10-31]] launches the same week; Texas vs non-Texas comparisons still work (Planner
change hits all states) but raw before/after Texas numbers do not.

**Vendor views CSV (11-02):** `.claude/exports/most_viewed_vendors_2026-08-03_to_2026-11-01.csv` (3,804 vendors, internal
accounts excluded, from top 3,000 Mixpanel URL rows = ~80% of views) + `..._pdfs_...csv` (each vendor's listed PDFs).
Mixpanel `viewed_vendor_pdf.pdf_id` is empty before Nov 2026, so per-PDF open counts for Aug-Oct do NOT exist; only
per-vendor. Top of list is alphabetical (V1 1 Hotel Brooklyn Bridge, 1840s Plaza, 3 West Club, Abbazia...) = list-position
bias from default A-Z order, not pure demand.
[[project_focus_2026-10-31]]

## 11-07 CLOSING THE OPEN WEEK (owner: "set up the gating again")
Target = pre-open state: free 2 questions -> one-tap feedback -> 1 more -> paywall; paid 40/day. Live /rec/status 11-07 (test
acct 14998) showed open-week values: free_daily_mode true (40/day), free_limit 10, free_feedback_required false, bonus 1,
paid daily limit 50, beta_feedback_after 100000. OWNER sets on Railway rec-engine (my CLI is on the Healthie account):
REC_FREE_DAILY_MODE=0, REC_FREE_REFINES=2, REC_FREE_FEEDBACK_REQUIRED=1, REC_FREE_FEEDBACK_BONUS=1, REC_PAID_DAILY_REFINES=40,
(pre-open also REC_BETA_FEEDBACK_AFTER=2; pools REC_FREE_DAILY_USD 15 / REC_GLOBAL_DAILY_USD 40 if raised to 30/60).
Side effect: daily mode off -> free couples drop from "light" to price-free "free" detail (no price bands) - app._detail.
Plus "open-ended" replies: server branch rec-open-ended e80a904 (REC_OPEN_ENDED default ON, no closing question/chips/profile
questions) and component home-screen 28df614 (showStarters default OFF). Neither pushed at time of writing.
**DONE 11-07 ~17:55 UTC (owner yes, I set via CLI):** rec-engine lives in Railway project "tulle admin dash" (service
f5b44307...). Set REC_FREE_DAILY_MODE=0 (was unset=1), REC_FREE_REFINES=2 (was 10), REC_FREE_FEEDBACK_REQUIRED=1 (was unset),
REC_FREE_FEEDBACK_BONUS=1, REC_PAID_DAILY_REFINES=40 (was 50), REC_BETA_FEEDBACK_AFTER=2 (was 100000). Left REC_FREE_DAILY_USD 30 /
REC_GLOBAL_DAILY_USD 60 (owner didn't ask). Backup .claude/railway_rec_engine_gating_pre_regate_2026-11-07.json. VERIFIED live
/rec/status: daily_mode False, free_limit 2, forever_daily_limit 40, beta_feedback_after 2, bonus 1. Railway CLI now on the
personal (gmail) login. Free walk-through with a real free account (2 -> rating -> 1 -> paywall) NOT yet run.
