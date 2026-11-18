---
name: project_planner_subscription_readout_2026-11-07
description: "11-07 readout for 'make Tulle Planner usable enough to charge a subscription': 122 couples 11-03..11-06, next-day return 4-9%, 1 in 5 hit a dead end, top asks = venue-name lookup + price; recs given, nothing built"
metadata:
  type: project
---

Owner 11-07 goal: get Tulle Planner to a usable spot so it can be sold as a SUBSCRIPTION. Sources: Planner Activity Log sheet
([token], 796 rows 11-03 15:53..11-06 23:45 ET, internal accts skipped), Xano table 1 stage
(Planning_Phase), Mixpanel rec_* events, #product-development 11-01..11-06. Confounded by the open week [[project_planner_open_week_2026-11-02]].
Key numbers: 122 couples; 62% iPhone; stage 60% just started / 24% not engaged / 5% booking / 11% blank; 30% did nothing after the
opening; median 2 actions; 93% active on one day only; Mixpanel next-day return 4-9% (day-0 ~50-60%). 43% clicked a vendor or PDF
(101 free PDF opens). Booking-stage: 83% typed, 17% clicked (Planner doesn't serve them). 26/122 (21%) hit a real dead end (no vendors
10, not in Tulle 10, retyped same 8, slow 4); 34 got a duplicate chat (47 same-minute openings). 162/290 openings say "Forever unlocks"
(Kate flagged it). Typed asks: venue names most common, ~1 in 5 about price, 12 other-category asks. 4/107 free users paid later
(2 Forever $135) - no baseline. Weekly (Mixpanel wk of 11-02): 178 viewed, 124 asked, 58 clicked a card.
Recs given (not built): fix dupes/lookup/dead ends; drop Forever line; price ranges for free; other categories; build a reason to
return (shortlist the Planner works on, quote check, date flexibility per Des) and gate any subscription test on D7 return.

## 11-07 BUILT (owner "do all 5") - NOT DEPLOYED, owner to push
Server worktree C:/Users/vivek/Documents/GitHub/tulle_rec_planner5, branch rec-planner-five = origin/main e303ca7 + 4:
7f2bbba dead ends (one chat per opening 120 s window; conversational/complaint/place msgs not lookups; single strong
match = found; named venue survives food/price caps via agent.named_fully; nothing-new -> best 3 already-seen; not-in-
Tulle fallback filtered by guests) / 7d90a6f no Forever in openings (REC_OPENING_NO_FOREVER + upsell.strip_forever) +
LIGHT price bands (REC_LIGHT_PRICE_BANDS; also fixed LIGHT venues all labelled "rental not in this venue's PDF") /
f02acca florist pricing from flt_fl_* (REC_FLORIST_PRICING) + neighbors.py nearest-state offer on zero matches /
d076d45 returning-couple shortlist opening (REC_RETURNING_OPENING) + date flex (REC_DATE_FLEX). All flags default ON,
rollback = set flag 0 on Railway or Railway rollback to e303ca7. Suite 668 pass (+1 pre-existing origin_main diff fail).
Component worktree C:/Users/vivek/Documents/GitHub/tulle_planner_chat5, branch planner-five = 175818c + a892363
(bandLine under place: price_note / price_band; hidden when reason has $). Push: git push
https://github.com/marthv/ww-planner-chat.git planner-five:main, then owner ACTIVATE + PUBLISH.
LIVE-VERIFIED locally: lookup Dinolfos->found, Gather green->found; florist notes on real NJ rows; light opening had no
Forever + bands; light price question quoted band exactly, no figure bounce ($0.07). UNVERIFIED: returning-couple opening
(saved cards need XANO_METADATA_TOKEN, Railway only), one-chat dedupe in the real page, date flex, nearest-state.
Not built: "new since last visit" (needs a Xano date field) and re-engagement email (outbound send = explicit yes).

## 11-07 PUSHED + HOME SCREEN
Server main d076d45 pushed ~17:20 UTC (health ok; new-code behaviour still to see in the sheet). Component main a892363 pushed;
owner ACTIVATE + PUBLISH. Then owner asked: no automatic opening - ChatGPT/Claude-style blank start (Tulle mark, "How can we get
started?" variants, starter questions; goals = free couples open more PDFs, paid couples come back). Built on component branch
home-screen = c3831ff (NOT pushed): homeScreen prop (default ON, OFF = old opening), logoUrl prop, starters (venues_fit->opening,
compare_saved when 2+ saved, resume last chat, lookup, prefs/cost, photographer), events rec_home_shown/rec_starter_clicked.
Previewed locally with mocks (Playwright, desktop+phone): no /rec/opening on load, tap -> POST /rec/opening. Baseline to beat
(sheet 11-03..11-06): 39/122 couples (32%) opened a PDF, 105 opens; 29 of those on opening cards; 35/122 did nothing after opening.
