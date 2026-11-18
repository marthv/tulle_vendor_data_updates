---
name: project_engagement_baselines_2026-10-09
description: "30-day Mixpanel baselines (return rates, mobile share, time-to-purchase) used to kill mobile app / email experiments on 10-09"
metadata:
  node_type: memory
  type: project
  originSessionId: 19a743a7-7cf3-49db-91d4-fd2388656e0b
  modified: 2026-10-09T04:50:45.564Z
---

Pulled 2026-10-09 from Mixpanel project 3963130 (30d unless noted):
- 4,194 users with sessions, 2,783 sign-ups, 170 membership_purchased (~5.7/day; I had guessed 7-8, wrong).
- Sign-ups barely return (signed_up -> viewed_page): ~4% day 1, ~5% week 1, ~3% week 2, 1-2% after. Payers: ~28% wk1, ~22% wk2, 10-15% wk3-4.
- 69% of sign-up-linked purchases happen within 1h of sign-up (134/195, 60d window).
- iOS = 66% of session users, Android 4%; mobile = 72% of sign-ups but 59% of "Clicked to Pay" (mobile 2.5% vs desktop 3.6% per session user).
- favorited_vendor 118 users, click_to_refer 2 users, submitted_vendor_pricing 151 events, paywall_shown 291 users.
- Caveats: `$session_start` returns all-zero retention (don't use it); viewed_page covers ~84% of session users; membership_purchased is server-side (no $os).
- SOURCES: use user property `source` (self-reported at sign-up, 78% answer), NOT $initial_referring_domain (97% = tulletogether.com). 30d: TikTok 1,467/2,785 sign-ups (53%) + 76/155 buyers; Instagram 275/26; Google 66/8; Reddit 53/11 (~21 buyers per 100 vs TikTok ~5). 90d TikTok = 60%.
- Brevo Aug-Sep campaigns: 15-17% reported opens (mostly Apple MPP), 3-5% verifiable, 1-2% clicks; spring was 33-50%.
- 10-09 user: "hubs build" = more PDFs in geo hubs; WordPress runs tulletogether.com; Phase 2 pricing NOT live; open product build ~1 day.
- Plan doc: https://claude.ai/code/artifact/bdebe00d-7285-476f-91b7-3ac7d086b454 (order: NY 10-14 -> open product ~10-20 -> Assistant free ~11-24; pricing-page A/B ~10-27).

**Why:** the product is effectively one-session; this decided against a mobile app and email-led experiments from the 30-apps list.
**How to apply:** prioritise first-session / mobile-web experiments; purchase-metric A/Bs are underpowered at ~6/day. See [[project_signup_dropoff_2026-10-07]], [[project_experimentation_framework]].

## 2026-10-11 time-to-purchase redo (CORRECTS the 69% above)
- membership_purchased only exists from ~2026-08-04 (0 events before). Clean cohort sign-ups 08-05..10-11: 63% of buyers buy <1h (138/219), 75% <24h; matured cohort 08-05..09-10 = 65% <1h (78/120). Hours 1-12 add only ~8%.
- Not a load artifact: 0 purchases within 1 min of sign-up; 1-3m 33, 3-10m 73, 10-30m 23, 30-60m 9 (avg ~9 min).
- First hour, sign-ups 09-11..10-10 (2,772): 56% open >=1 PDF (avg 3.5 min), 36% >=2, 24% >=3; 44% open none. 58 bought <1h, only 24 opened a PDF BEFORE buying (PDF openers 1.6% vs non-openers 2.8% - confounded by intent). paywall_shown <1h 144 users -> 1 bought after (UNVERIFIED what that surface is).
- User 10-11: wants first-day feedback + more first-hour conversion (assistant or faster PDFs). Evidence does NOT yet support "more PDFs" as the lever.

## 2026-10-11 first-hour replays + funnel (sign-ups 09-11..10-10, 1h window)
- signed_up 2,772 -> vendor page 80% (avg 2.6 min) -> PDF 52%. Biggest leak = vendor page but no PDF: ~34% of vendor-page viewers (~25/day); ~20% never reach a vendor page (~19/day). Onboarding 96% (54s).
- Replays are TEXT ONLY via MCP (taps/scrolls, no screen content); ~5 of 7 sampled had recordings.
  32551: 6 venue pages / 8 min, tapped PI membership CTA, no PDF, then had to LOG IN AGAIN 16s later; console "Unable to update variable selectedState ... got ['International']". 32610: ~9 min tapping map markers, console error (main-*.js Bd/jc/Ac AsyncFunction) on nearly every tap, only 1 vendor page. 32593: 1 venue page, left in ~15s.
- 5% of sign-ups log_in again within 1h (127/2772); 1 of them bought. Residual of the 09-10 sign-out race OR TikTok in-app browser - UNVERIFIED.
