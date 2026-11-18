---
name: index_overflow
description: Full one-line hooks for memory index sections collapsed on 2026-10-20 (PI panel, data model)
metadata:
  type: reference
---

## Pricing Intelligence (venue panel)
- [PI band ref + before-tax v20](project_pi_band_ref_v20.md) — 10-03 band at headline count ; v23 = row detail + verdict RESTORED (user values explanatory copy; trim one item at a time); WeWeb trim UNPUBLISHED
- [PI guest_min + capacity filter](project_pi_guest_min_and_capacity_filter.md) — Christina refund = guest_min ignored; CURRENT venue-page PI uids live here
- [PI panel non-venue bug](project_pi_panel_nonvenue_bug.md) — gate on `Category`; `Both` is NOT a venue
- [PI panel infographic redesign](project_pi_panel_infographic.md) — START HERE for PI copy; don't fix clarity with more prose (venue-page uids there are STALE)
- [F&B min mistyped as per-head](project_fb_min_type_bug.md) — fix in fn54 not fn56 (WeWeb recomputes client-side); rebuild table 63 after
- [PI space-grain redesign](project_pricing_intelligence_spaces.md) — tables 62/63, fn54-56, ep230/231; read before touching ep149/table 52
- [PI review — Des & Kate](project_pi_review_feedback.md) — two of their diagnoses are WRONG; acting on either worsens numbers
- [Venue page second Back button](project_vendor_back_button.md) — TWO buttons, don't collapse; capture must stay first in `Load Extracted Data`
- [Partner vendors](project_partner_vendors.md) — SHIPPED; card already built, don't rebuild
- [P/E market-rate benchmarks](project_pe_benchmarks.md) — only WeWeb left; Band/Soloist need alias map
- [Benchmarks API redesign](project_benchmarks_redesign.md) — per-space restructure; 3 open questions

## Data model
- [Staged vendor edits from Slack](project_slack_staged_vendor_edits.md) — ep281 only live write; whitelist duplicated in fn62 AND ep281
- [Two Slack apps](project_two_slack_apps.md) — tell apart by APP ID; ALLOWED_CHANNEL_IDS unset
- [Slack bot vendor visibility](project_slackbot_vendor_visibility.md) — visibility = `Validated_Data` TEXT "1"/"0"; hidden vendor lingers ~2h in cache
- [Broken xano MCP tools](reference_broken_xano_mcp_tools.md) — TWO bugs; MCP URL is an unauthenticated read key
- [Table 11 State multi-value](reference_table11_state_multivalue.md) — never `==` or group on it
- [Two pricing tables](reference_pricing_two_tables.md) — filters read table 36 peak-Saturday cols
- [Table 36 row dedupe](project_extracted_pdf_data_dedupe.md) — dedupe by PDF_ID + latest `last_extracted_at`
- [Unsorted query drift](reference_xano_unsorted_query_drift.md) — diff lists as SETS
- [Xano cache keys](reference_xano_cache_resolved_inputs.md) — keys on resolved inputs; warmer freq must exceed ttl
- [Place_ID backfill](project_place_id_backfill.md) — matcher must be equal-or-prefix + state
- [Google Data batch 401](project_google_data_batch_401.md) — sub-calls 401'd silently for 12 days; inlined
- [Google data empty shells](project_google_data_empty_shells.md) — real signal is `cache.name != null`
- [Frontend blank images](project_frontend_images_issue.md) — UNRESOLVED; ep119 accessor hypothesis is WRONG


## Moved from MEMORY.md 2026-11-02 (full lines)
- **10-28 FLORALS DONE + LIVE (owner published); preview switch stays staging-only.** Old note: **10-27 FLORALS on staging: filters (typical total, min spend, item prices, tier) + benchmark vendor block; gated filters blurred + click-upsell across ALL categories; owner publishes. Forever buyers with past date_until_access hit PDF wall (WeWeb View PDF gate checks date only, not forever flag) — ALL 16 backfilled to 3000-01-01 on 10-27 (backup xano_backups/table1_forever_past_date_rows_pre_fix_2026-10-27.json); recurs if a gift/manual path sets forever without the date.** [[reference_weweb_component_gotchas]]
- **Railway search: Discovery ep119 $rw_pct = 100 since 10-27 16:10 UTC (verified live 11-02); Planner REC_RAILWAY_SEARCH_PCT 100. 11-02 read (100 min, 472 searches): Railway arm 93%, p50 0.21 s / p90 1.07 s; fallbacks 7% (http_0 21, 502 9, 503 4) p50 7.9 s, 15 took >10 s (up to 169 s, 03:06 UTC burst) -> the slow tail IS the fallback path.** [[project_backburner_2026-10-25]]
- **10-28 Xano: 31 unused objects DELETED (14 eps, 11 fns, 6 tables; backups .claude/xano_backups/deletions_2026-10-28/); security batch 1 LIVE (ep26-29 retired, ep21/ep145 can't self-grant; ep21 paid-date guard was broken since 08-16, fixed). NEXT security: ep30 webhook (same broken date check!), leaked secret, relays.** Older: XANO IS TOO BIG - wants a breakdown doc. [[project_endpoint_auth_audit]] [[feedback_simplify_not_stack]]
- [PI band ref + before-tax v20](project_pi_band_ref_v20.md); [PI guest_min + capacity filter](project_pi_guest_min_and_capacity_filter.md); [PI panel non-venue bug](project_pi_panel_nonvenue_bug.md); [PI panel infographic redesign](project_pi_panel_infographic.md); [F&B min mistyped as per-head](project_fb_min_type_bug.md); [PI space-grain redesign](project_pricing_intelligence_spaces.md); [PI review — Des & Kate](project_pi_review_feedback.md); [Venue page second Back button](project_vendor_back_button.md); [Partner vendors](project_partner_vendors.md); [P/E market-rate benchmarks](project_pe_benchmarks.md); [Benchmarks API redesign](project_benchmarks_redesign.md) — details in [index overflow](index_overflow.md)
- [Staged vendor edits from Slack](project_slack_staged_vendor_edits.md); [Two Slack apps](project_two_slack_apps.md); [Slack bot vendor visibility](project_slackbot_vendor_visibility.md); [Broken xano MCP tools](reference_broken_xano_mcp_tools.md); [Table 11 State multi-value](reference_table11_state_multivalue.md); [Two pricing tables](reference_pricing_two_tables.md); [Table 36 row dedupe](project_extracted_pdf_data_dedupe.md); [Unsorted query drift](reference_xano_unsorted_query_drift.md); [Xano cache keys](reference_xano_cache_resolved_inputs.md); [Place_ID backfill](project_place_id_backfill.md); [Google Data batch 401](project_google_data_batch_401.md); [Google data empty shells](project_google_data_empty_shells.md); [Frontend blank images](project_frontend_images_issue.md) — details in [index overflow](index_overflow.md)
- **Parked:** comparable companies [[project_comparable_companies]]; wedding mailbox [[project_wedding_mailbox_negotiation_idea]]; interactive demo LIVE but ON HOLD (10-16) [[project_interactive_demo_idea]]; REA mapping [[project_rea_app_mapping_parked]]; open product [[project_open_product_scope]]; dark mode [[project_dark_mode_parked]]; ask someone to pay + GIFTING (10-16) [[project_ask_someone_to_pay]]; culture filters [[project_category_breadth_culture_filters]]; A/B framework [[project_experimentation_framework]].

## Search / filters (ep119 / ep121)
- [ep119 cache ttl 24h](project_ep119_cache_ttl_24h.md) — 10-03 ttl 86300; warmer every 6h (user choice, retry loop); hit-rate gain unverified until ~10-05
- [Gold chip vendors](project_gold_chip_vendors.md) — CURATED; WeWeb filter breaks the warmer unless all 15 task-8 URLs get the param
- [ep119 category cache gap](project_ep119_category_cache_gap.md) — `X-Query-Cache` = hit flag; non-Venue miss only ~0.2s
- [Image delivery cost](project_image_delivery_cost.md) — don't just lower `maxwidth: 800`; waste is JPEG quality + no-store 303
- [Advanced-filters paywall card](project_advanced_filters_paywall.md) — highest-volume paywall CTA; inherited `pointer-events:none`
- [Type-ahead dropdown](project_search_typeahead_dropdown.md) — ep258 exists, UI doesn't; suggestions need BOTH ids
- [Package-grain P/E filters](project_package_grain_filters.md) — START HERE for filters
- [Location whitelist ep119 v23](project_location_whitelist_ep119.md) — unticking is ~1s FASTER, measured
- [flt_states denorm v22](project_flt_states_denorm.md) — read measurements before optimising ep119; payload trimming ≈ 0
- [Similar Vendors price band](project_similar_vendors_price_band.md) — WeWeb rewiring open; strip ALL inherited globals at once
- [Popularity + recency sorts](project_popularity_recency_sorts.md) — new sort keys go in BOTH ep119 output lists
- [ep119 facets](project_ep119_facets.md) — optional `include_facets`; 121 not done
- [flt_* denorm aggregates](project_flt_denorm_aggregates.md) — price aggregates from PRIMARY PDF only; fn21 capacity sync is FILL-ONLY
- [Max-filter fix v4](project_maxfilter_fix_v4.md) — cache masks fixes
- [WPTP search joins](project_wptp_search_joins.md) — exact-string match; `outdoor_ceremony` is TEXT; `max_capacity` = seats AT LEAST N
- [Search N+1 not the bottleneck](project_search_n1_not_bottleneck.md) — cost is the substring `includes` scan
- [ep119 v12 / ep121 v9](project_119_121_dropfallback.md) — dead fallback removed; warmer realigned
- [International filter bug](project_international_filter_bug.md) — FIXED
- [map_light wide-bbox perf](project_maplight_widebbox.md) — bind `flt_venue_types`, not `Venue_Type`; 10-19 slim ep121 LIVE (no flt_glance), ~25-45% faster on mid/wide boxes
- [Multi-category backfill](project_multicategory_filter_remap.md) — its "no Xano work needed" claim is WRONG
- [Multi-category extraction](project_multicategory_extraction.md) — background only

## Older pick-up items
(moved from MEMORY.md 2026-11-07)
- **11-02 PLANNER OPEN WEEK (to ~11-09, lower pay rate accepted) confounds payment reads; vendor-views CSV delivered (no per-PDF ids pre-Nov).** [[project_planner_open_week_2026-11-02]]
- **11-02 OPENING PREWARM server LIVE (main 3a7ad85); onboarding Finish wf 5f2c9e41 in EDITOR, owner must PUBLISH; read in 1-2 days.** [[project_opening_prewarm_2026-11-02]]
- **11-01 MOBILE DISCOVERY review (Expedia/Airbnb model) delivered, nothing built; owner to pick items.** [[project_mobile_discovery_layout_review_2026-11-01]]
- **10-31 FOCUS LIST (owner): florals KILLED/done; all work = roadmap item 2 Tulle Planner (up-front questions, quick profile updates, speed via filters, open to everyone + Google Sheet log, Claude-like mobile UI); Texas Forever -> $89 test; PDF views 3-mo data; influencers + Trustpilot.** [[project_focus_2026-10-31]]
- **10-27 Planner feedback one-tap: server LIVE (49d3c83, bonus 1, check-in after 2); WeWeb card edits need owner PUBLISH; read in ~1 wk (baseline free 1/58).** [[project_planner_feedback_onetap_2026-10-27]]
- **10-27 Meilisearch: old `meilisearch` service DELETED 23:13 UTC; search runs on `meilisearch-b` (vars inlined, verified). Owner still to delete detached volume meili-data-a0Lh.**
- **10-28 FLORALS LIVE; forever buyers with past date_until_access hit PDF wall (16 backfilled 10-27) - detail in [index overflow](index_overflow.md).** [[reference_weweb_component_gotchas]]
- **10-28 Planner speed 2a READ: fresh opening p50 12.7->8.1 s, p90 21.0->18.5 s; Discovery misses p50 0.45/p90 1.70 s, fallback 3.2% (new http_502s).** [[project_planner_speed_2a_2026-10-27]]
- **10-28 'paid but free plan': payment log/Slack email = Stripe checkout email, look up by Client_Reference_ID; PDF gate still date-only (owner fixing).** [[reference_payment_email_vs_account_email]]
- **BACK POCKET 10-28 vendor network v1 (roadmap #4): 'Recommended by N venues' + sort, outreach to most-named businesses -> portal claim, give-to-get. Data (full read): 37k businesses, 7.2k named by 2+ venues, 811 by 5+, 759 on Tulle.** [[project_vendor_network_recommended_by]]
- **10-24 assistant outage 10-22..10-24 FIXED (revoked key, 40 users); alerting gap open.** [[project_assistant_outage_api_key_2026-10-22]]
- **11-01 GMAIL DOT/+ FIX LIVE (fn157 auth/email_key in ep20/ep2/ep7; abuser row 22 = 0 views forever; ep7 path unverified).** [[project_free_views_survive_deletion_2026-10-24]]
- **10-24 review email went to 148 buyers unintentionally -> explicit yes before any send.** [[feedback_explicit_send_confirmation]]
- **10-27 map clustering LIVE; 10-30 v5 fix (bubbles missing at browser zoom != 100%) in editor, NEEDS PUBLISH.** No bubbles in editor = reload the tab first (10-28). [[project_map_clustering_idea]]
- **10-25 onboarding vibes/venue answers not saved 07-10..10-26 (~10.2k users); fix PUBLISHED 10-26, verify a real sign-up.** [[project_onboarding_vibes_not_saved_2026-10-25]]

## Product / other
- [Quote check idea](project_quote_check_idea.md) — 'is this quote a deal?'; latency measured 10-14; NO website change until user launches
- [AI recommendation engine](project_ai_recommendation_engine.md) — START HERE for recs; decisions locked 10-03 (3 free, Sonnet 5.5, no holdout, Railway)
- [Growth backlog 2026-10](project_growth_backlog_2026-10.md) — AI rec engine is the focus; Reddit via Upworkers before local PR; F&B most-clicked after venues
- [Onboarding Finish spinner](project_onboarding_finish_spinner.md) — error branch sets loader TRUE; any failure = endless spin; hand-edit fix pending
- [Welcome email spam](project_welcome_email_spam.md) — SPF lacks Google + no Google DKIM, but welcome mail sends via Gmail
- [/pricing buy on unhydrated user](project_pricing_buy_undefined_user.md) — `undefined (undefined)` = customer could NOT buy; unfixed
- [Lifetime upgrade credit](project_upgrade_credit.md) — Forever $129; credit = current access period only
- [Tier-aware Access Badge](project_tier_access_badge.md) — rebuild UNPUBLISHED; ep216 401s
- [A/B testing framework](project_experimentation_framework.md) — inert until head block pasted
- [Feedback triage queue](project_feedback_triage.md) — PI needs `total_amount_paid >= 50`; #39 security report unanswered
- [Referrals credited wrong person](project_referrals_self_referral.md) — ep270 never errors by design; pending publish
- [Vendor calendar availability](project_vendor_calendar_availability.md) — "Taken" = 4h+; don't soften SSRF guard
- [Vendor portal](project_vendor_portal.md) — START HERE for vendor-facing; group 13 = `api:BjliGATR`
- [Budget Roadmap relaunch](project_budget_roadmap_relaunch.md) — KILLED 2026-09-27 (dash tab removed); Xano/WeWeb side still live; refund orders 6&7 open
- [Favorites wipe ep109](project_favorites_wipe_ep109.md) — real fix is ep158 toggle_favorite
- [Login speed](project_login_speed.md) — WeWeb serial fetches, not Xano
- [Auth page speed 2026-09-28](project_auth_page_speed.md) — phone floor = WeWeb runtime CPU; fix = CSS splash in head code (user pastes)
- [WeWeb component deploy](feedback_weweb_component_deploy.md) — each component needs its own repo
- [Session state 2026-07](project_session_state_2026-07.md) — #4 vendor categorization; ask for taxonomy first

## Older pick-up items 2
(moved from MEMORY.md 2026-11-08)
- **11-07 22:21 ET LIVE main 1f994f7: FREE = 1 question/day (Railway REC_FREE_DAILY_MODE=1, REFINES=1, FREE_PRICING=none; only questions count, not openings/clarifying lookups); "Next for your wedding" OFF; "My wedding" plan ROLLED BACK (REC_PLAN_AB_PCT=0 on rec-engine + health-report-cron; off for team too; verified plan_enabled False). Component ww-planner-chat 2c3fa29 (free daily cap -> plans card, "Show 3 more <type>") PUSHED -> owner activate+publish. NEXT: owner reacts to "My wedding" mock (Overview budget hero + dense categories; Saved = one heart everywhere; Mark booked -> budget table 33); THEN chat file/screenshot upload. Category density 11-07: venues 13k, DJ/band 3k, photo 2.4k, florists 734 (CA 0), catering 149, beauty 72, planners 11.** [[project_two_pillar_consolidation]]
- **11-08 03:01 UTC CALIFORNIA FOREVER $149 -> $135 (owner; 1w $39 / 4w $69 KEPT, Intl stays $149): fn63 LIVE (CA joins $is_ny_cohort; verified CA/CA+Intl 135, NY 135, Intl 149, TX-only 89, FL 135; backup .claude/xano_backups/fn63_pre_ca135_2026-11-07.xs). WeWeb Membership Lifetime Price af5d09b0 + Forever button wf 8605fcc5 (CA uses PID 12 first) EDITED in editor, read back OK -> owner PUBLISH. My own CA read (cohort $/signup) did NOT support "CA resisted" - owner call on simplicity. Open: CA buyers paid $30/$55/$135 since 10-01 (non-CA prices) - check routing.** [[project_geo_pricing_bnpl]]
- **11-07 21:44 ET LIVE server main fb52e54: "My wedding" plan for EVERYONE (no A/B, owner; table 90 rec_wedding_plan; REC_PLAN_AB_PCT=100), vendor-type lookup, fresh-screen question = new chat (owner screenshot bug), compact daily brief (revenue+signups, plan adoption, alerts only; BRIEF_MODE=full restores). tulle-search 7b35477 shadow DM only on problems. Component ww-planner-chat ab459e8 (sources + plan UI) PUSHED -> owner ACTIVATE + PUBLISH; tours split in editor -> owner PUBLISH. CHECK: a "test" refine returned empty once (curl 60 s) then 18.8 s - watch latency; plan adoption in brief from 11-08.** [[project_planner_open_week_2026-11-02]]
- **11-07 late: server main 461b380 LIVE (lookup best/no "which one", clarify lookups free, sourced answers, web search OFF - owner: 19 s too slow). Vendor-type lookup c4ad97d on branch rec-vendor-lookup NOT pushed. Component ww-planner-chat f01b919 (sources pills) PUSHED -> owner activate+publish. Tours split in EDITOR (no auto-click on Discovery; vendor tour on first vendor page, requireCompletedKey "") -> owner PUBLISH; backup .claude/xano_backups/weweb_tours_pre_split_no_force_2026-11-07.json. NEXT: Phase 2 "My wedding" plan (free for all, new Xano table OK'd). Router stays OFF; pricing precompute ~0.6 s win.** [[project_planner_open_week_2026-11-02]]
- **11-07 LOOKUP "best guess" commit 16f2464 on branch rec-lookup-best (tulle_rec_planner5), NOT pushed - owner OK needed. Instant venue lookup ALREADY existed since 10-26 (p50 0.45 s); leak was "which one did you mean?" (11/22 left, 0 taps). Read after push: lookup_status best vs PDF opens.** [[project_planner_open_week_2026-11-02]]
- **11-07 RE-GATED Planner LIVE (Railway vars set by me, verified: free 2 + rating + 1, paid 40/day, check-in after 2). OPEN-ENDED server LIVE main e80a904 (verified live: no closing ?, chips [], 3 cards). Component main 28df614 PUSHED (home screen, starters off, price line) -> owner ACTIVATE + PUBLISH, then I verify live main.js. Experiment registry entry NOT logged yet.** [[project_planner_open_week_2026-11-02]]
- **11-07 PLANNER: 5 fixes PUSHED (server d076d45, component a892363 -> owner ACTIVATE+PUBLISH). HOME SCREEN (no auto opening) built on component branch home-screen c3831ff, NOT pushed. Baseline 32% couples open a PDF.** [[project_planner_subscription_readout_2026-11-07]]
- **11-06 GOOGLE SIGN-IN BROKEN 11-04..now = my b3d17f42 v2 30-min awaited wait on app load; v3 PUBLISHED 11-07 04:29 UTC; existing-account Google login verified (ep7 200, 0.47 s); NEW-account Google sign-up still to verify.** [[project_google_signin_blocked_by_app_wf_2026-11-06]]
- **11-05 VENDOR PORTAL LIVE main afe4d62 (proof upload + Kate dashboard; eps 565 v3, 568, 569). Signed-in pages + saves UNVERIFIED -> test acct 8.** [[project_vendor_connections_graph_2026-11-04]]
- **11-04 VENDOR PORTAL: My connections + feedback round 1 LIVE (main 271576a); ffd7df4 mobile fix local, owner push; signed-in pages + Xano save of new fields unverified.** [[project_vendor_connections_graph_2026-11-04]]
- **11-04 VENDOR NETWORK PORTAL LIVE at https://vendors.tulletogether.app (Railway main 76a5dc5, verified end to end). Open: no review screen, tag/invite emails + Founding vendor promised but unbuilt, slow business search.** [[project_vendor_network_portal_2026-11-03]]
- **11-04 UPGRADE FIX BUILT in WeWeb editor (Forever card applies credit at click via ep285 + 401 watcher b3d17f42 v2) - OWNER MUST PUBLISH, then verify live json. Tyler refund still owner call.** [[project_upgrade_full_price_tyler_2026-11-04]]
- **11-04 Trustpilot: Elijah Morales 34912 5-star 04:02 UTC -> ep308 trustpilot "sent" (Brevo accepted w/ BCC).**
- **11-03 ALL LIVE + VERIFIED: limit cards "Check out our plans" (submit link hidden), vibe fix cd4b209, Planner Google Sheet log (server 5b103e1, component 175818c incl. f252a02; real rows landing 20:53 UTC). READ limit-card conversion ~11-17. Sheet SKIPS internal accts by design (31619 un-skipped); Device+Browser cols N:O server e303ca7 LIVE, owner pasting new .gs.** [[project_limit_cards_plans_revert_and_vibe_lookup_2026-11-03]]
- **11-03 Discovery advanced filters UNBLURRED (32 sections, still locked) IN EDITOR - owner PUBLISH.** [[project_discovery_filters_unblurred_2026-11-03]]
- **11-03 TRUSTPILOT AFS email SENDING (switch ON 05:15 UTC, 5-star only); VERIFIED end-to-end 05:30 (timing Immediately). 1-star review flags site stat mismatch 60k/45k couples.** [[project_trustpilot_reviews_2026-10-21]]
- **11-03 TEXAS-ONLY FOREVER $89 LIVE (~06:00 UTC, row 16 PID 13; rollback --tx-off). Read Texas-only Forever rate in 2-3 wks.** [[project_texas_only_forever_89_2026-10-31]]
- **PLANNER CHAT 11-03: component main = f252a02 (rating card after 3rd question) PUSHED, owner ACTIVATE + PUBLISH; 8b33d02 scroll ROOT-CAUSE fix LIVE (WeWeb atomic layer forced display:block); prev 30fe0f8 (desktop chip arrows/wheel) PUSHED, owner ACTIVATE + PUBLISH; server main = 1518511 LIVE. CHECK 11-03 13:32 UTC daily brief + weekly digest; OPEN: details-sheet Save sends no request, 2 chats per page load. Full commit ledger in topic file.** [[project_planner_chat_component_2026-10-31]]
- **10-27 ROADMAP (owner, in order): 1 florals filters (WIP, staging) -> 1.5 Tulle Assistant UI/UX (+ mobile rethink, in-chat profile updates, 10-28) -> 2 Muse-like negotiation agent -> 3 collaboration mode -> 4 vendor network (info for placement, no money).** [[project_roadmap_2026-10-27]]
- **START HERE 10-26 handoff: NEXT PUSH = DATA (florals filters, preferred vendors, assistant questions); owner + my to-do lists; checkbox sign-up tracking (expected lift only ~1-2%).** [[project_handoff_2026-10-26]]
- **Railway search 100% (ep119 + Planner); slow tail = fallback path (11-02 read in overflow).** [[project_backburner_2026-10-25]]
- **BACKBURNER (owner 10-25, don't start unprompted):** tours, Railway search move, assistant subscription, assistant UI, EXPERIMENT REGISTRY in admin dash, admin dash speed, collaboration mode (after full budget planning). [[project_backburner_2026-10-25]]
- Older pick-up items (10-xx .. 11-02): see [index overflow](index_overflow.md#older-pick-up-items).

## Older pick-up items 3
(moved from MEMORY.md 2026-11-15)
- **11-09 14:45 UTC Ask-Tulle-from-vendor fix PUSHED ww-planner-chat 4586d2b (owner ACTIVATE+publish): Planner mounts chat TWICE per load (cause unknown) - check rec_ask_resumed vs gave_up in Mixpanel.** [[project_tour_picker_2026-11-08]]
- **11-09 14:12 UTC PI = 1 FREE VENDOR LIVE (ep230; owner reversed 08-17 paid-only); ONLY 2 auto tours (Discovery + vendor page = 1 PDFs step); Planner + My wedding auto OFF - PUBLISHED + verified live 11-09 ~14:55 UTC; read Mixpanel 11-10 (tours, rec_ask_resumed vs gave_up).** [[project_tour_picker_2026-11-08]]
- **11-09 04:35 UTC ep21 FIX LIVE: `0 == null` is TRUE in XanoScript -> free views never reached 0 since 10-28 (site-wide PDF leak). Confirm a real 0-write now stores 0. Tours: legacy browser keys removed, vendor PI->Ask->PDFs, My wedding tour via 64570531 (editor, needs publish).** [[project_tour_picker_2026-11-08]]
- **11-08 21:15 UTC TOUR PICKER in EDITOR (unpublished): 'Take a Tulle Tour' -> picker popup 61100bcc, 6 tours via shared instance 3ee46d5b, Mixpanel tour_launched. Planner-chat slash-menu c7af807 PUSHED as marthv/ww-planner-chat f51794c (owner ACTIVATE) ('/' menu + Tools button + estimate Peak season/Major city toggles) . Forever button dup Mixpanel step removed (Slack ping kept).** [[project_tour_picker_2026-11-08]]
- **11-08 20:55 UTC MY WEDDING v3 LIVE: server main 8434d1c (existing Budget-tab packages shown: 261 couples no longer empty; live 7/7 + 10/10); NATIVE My wedding (old Budget panel, recTopTab='budget') in editor; server dc568a0 + component ww-planner-chat ed39d70 -> owner ACTIVATE+publish. Mobile = hamburger only (bar hidden). Was: server wedding-v2 3f76a4f (notes/file/pie slices, POST /rec/wedding/item) + component wedding-v2 440c2a1 (pie, notes, files, unsave, Add from PDF, free tools always under chat). Editor: top tabs + To-dos hidden (unpublished). NPS DONE. Vendor QA rows deleted.** [[project_two_pillar_consolidation]]
- **11-08 ADMIN DASH -> admin.tulletogether.app (Cloudflare Pages + Access, Railway FastAPI). Reason = Streamlit reruns, NOT speed. Worktree tulle_admin_cf branch admin-cloudflare 4c1f4da NOT pushed; Cohorts DONE + parity-verified; 4 tabs left; owner must wrangler login + Access app.** [[project_admin_dash_cloudflare_2026-11-08]]
- **11-08 ALL OF MY WEDDING READY IN EDITOR, owner will PUBLISH ONCE: + desktop left-nav 'Budget Tracker' dda178c5 -> 'My wedding' (/assistant?view=wedding, signed-in); NEW Mobile Bottom Bar bc90548a in shared Navigation Bar Section (Discover/Planner/My wedding/Menu; mobile+signed-in; hidden while chatting in Planner); component 0169d45 (bottom padding) -> owner ACTIVATE. Watch after publish: bar overlap with Discover map/list pill + vendor-page mobile CTA. All rollback values in .claude/xano_backups/weweb_nav_pre_my_wedding_2026-11-08.json.** [[project_two_pillar_consolidation]]
- **11-08 11:12 ET MY WEDDING + FREE TOOLS SHIPPED (owner "all at once, with rollback"): server main 1cea241 LIVE (/rec/wedding, /book, /unbook on table 33; undo = Selected false; REC_WEDDING=0 = off) verified live. Component ww-planner-chat 124feb9 PUSHED (free tools w/ visual cards + 'view=wedding' My wedding) -> owner ACTIVATE+PUBLISH. WeWeb EDITOR (unpublished): chat f9ec7409 view = query.view; mobile 'My Budget'->'My wedding' (/assistant?view=wedding), Saved Vendors + Cost Tools hidden ('false &&'); desktop tab Budget->'My wedding', Cost Tools tab hidden, underlines follow view. Backup + ROLLBACK ORDER: .claude/xano_backups/weweb_nav_pre_my_wedding_2026-11-08.json. Rollback component: 2c3fa29 / 754f3f6. NOT done: mobile bottom bar, Budget Tracker/Payment Planning desktop nav items, old Budget panel still in page.** [[project_two_pillar_consolidation]]
- **11-08 STEP 1 BUILT (not pushed): free cost tools in Planner w/ visual cards - component tulle_planner_chat5 branch free-tools 754f3f6 (home row + free daily-cap card buttons, POST /rec/tool no AI/no question). Owner approved direction: nav = Discover · Planner · My wedding (+Menu on mobile); My wedding = prototype (scratchpad mock/proto.html: one screen, budget left-to-spend, 4 dense categories inline, heart = save, Mark booked inline, + Add a cost); fold Saved/My Budget/Budget Tracker/Payment Planning/Budget Roadmap into it; Cost Tools/Cost Research -> Planner free tools; To-dos KILLED. Step 2 = WeWeb build after step 1 ships.** [[project_two_pillar_consolidation]]
- **11-08 VENDOR ONBOARDING 1+2 LIVE + VERIFIED (main 5923966; eps 564v3/565v4/569v2; live e2e 21/21). ITEM 3 (drafts + progress hub) LIVE main f8e0abe, live e2e 30/30. Open: reminder emails (no approval step), cross-device drafts (needs Xano table). QA rows to clean up w/ owner OK.** [[project_vendor_onboarding_light_2026-11-07]]
