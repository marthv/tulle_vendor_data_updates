---
name: project_handoff_2026-10-26
description: "Session handoff 2026-10-26 ~02:00 UTC: what shipped 10-24..10-26, owner to-dos, my to-dos, NEXT PUSH = DATA (florals filters, preferred vendors, assistant questions), checkbox sign-up tracking plan"
metadata:
  node_type: memory
  type: project
  originSessionId: abb664ba-4018-4524-a407-838ba2ba7236
  modified: 2026-10-26T02:24:00.465Z
---

**NEXT PUSH (owner 2026-10-26) is about DATA:**
1. **Florals - filters for florals** (desktop rail, mobile popup, gated). Start from [[project_florals_preferred_vendors_2026-10-19]] (brief 10-15, extraction + calculator plan).
2. **Preferred vendors** - [[project_preferred_vendors]], [[project_gold_chip_vendors]] (curated gold chip; WeWeb filter breaks the warmer unless all task-8 URLs get the param).
3. **Tulle Assistant questions** - review real questions/answers; start from [[project_assistant_usage_review_2026-10-19]] (131 questions, 'Show 3 more' constraints, region location, free budget policy) + table 79 rows since 10-24.

**Shipped + verified 10-24..10-26** (details in [[project_backburner_2026-10-25]], [[project_two_pillar_consolidation]]):
- Railway search at **20%** (assistant REC_RAILWAY_SEARCH_PCT=20 + Discovery ep119 `$rw_pct` 20, same sha256 bucket), owner forced on; hourly sync. Kill switch search-api SEARCH_ENABLED=0. First 40 Discovery requests after 20%: all 200, 4 railway 0.05-1.3s.
- Prod publish 10-26 ~00:30 (Planner rename + 3 tabs, tours chain, Stop/progress, chips, cost cards, consent line no checkbox, /terms, softer popups, ep109 vibes/customization fix) + ~01:51 (Planner venue cards: meta line hidden, address -> 'City, ST'; verified in live page data).
- Experiment log pushed 601b2b6 (tulle_dash_exp worktree of tulle_vendor_data_updates); dash deploy SUCCESS.
- VERIFY TRAP: live `/data/<pageId>.json` with no query = stale CloudFront copy; add any query string (WeWeb uses `?wwlang=en&_wwcv=`).

**Owner to-dos:** publish Discovery default sort + hand edit (Sort select 41ca2182 Initial value = `variables['185c7a24-8257-4ea9-9400-2f14f5121db7']`); Brevo automation (TOTAL_PAYMENTS>0, once, wait 5d, template 418); rotate Gmail app pw (Sign Up v2 f7e97a41) + Slack webhooks incl B098L47TRGV; end Trustpilot 5-star-only test; aria-labels (Stop, Show all); "Tulle Users Deleted" test rows.

**My to-dos:** Railway search in each brief/session + Discovery X-Search-Engine split in brief, ramp to 25% at >=50 reqs / fallback <5% / >=30% faster; verify real sign-ups (vibes/customization, terms_version + TC time incl Google, finished_onboarding props); assistant speed (lighter pricing 1.5-2.5s, skip opening model turn 1.5-2.7s); UI leftovers (split-card subtitle, Upload Vendor Pricing purple, compare-venues totals vs popup); security list (ep196, /packages?user_id=, ep145/ep21 FreeViewsRemaining). Xano breakdown doc reminder.

**CHECKBOX SIGN-UP TRACKING (owner asked 10-26 "can we track how many sign up because of no checkbox"):** live since 10-26 ~00:30 UTC (EXP-2026-10-signup-checkbox, pre/post).
- Metric: /sign-up page -> signed_up within 1d (baseline 62% for 09-28..10-05, [[project_signup_dropoff_2026-10-07]]); compare 4 weeks pre vs 2+ weeks post, same weekdays. Direct check: ep2 "must agree to Terms" errors should now be 0.
- EXPECTED EFFECT IS SMALL: 10-07 data showed 96% of people who submit get an account; checkbox errors hit 28 people and 26 recovered (~1 day). So the checkbox cost ~2 of ~124 submitters (~1-2%), far below the owner's +10-15% estimate and below what pre/post can detect with daily signups swinging 75-130. 78% of losses leave without typing. Tell the owner this plainly when reading; add a result note to the experiment log (needs a push -> ask).

**10-26 ~20:15 UTC first-day read (Mixpanel days are ET):** tours fire once per user per tour (no doubles); Discovery tour 79 started / 80 onboarded.
Planner tabs have NO tab-switch event, so tab use can't be measured. Cost tools in chat: 0 of 39 rec requests called a cost tool, widget null on all. `rec_action_chip` has never arrived in Mixpanel.
`pdf_chain_shown` 54 users -> 0 `pdf_chain_clicked`. Google `TERMS_REQUIRED` (ep7 400, 9 on 10-26) = new users pressing Google on SIGN IN (agreed_terms=false) -> bounced to Sign Up; most retry and succeed within ~1 min. By design, minor friction.
Trap: raw expanded signup_page_viewed rows show `$device:` ids for recent sign-ups (merge lag), which is NOT a sign-up outage; check hourly signed_up instead.
