# Memory Index

## PICK UP HERE NEXT SESSION
- **11-18 06:47 UTC PROD PUBLISH 1401 VERIFIED LIVE (component strings + page data): price report 2201271 (simple rows, locked card), planner-chat fbce08d (estimate what-if + home button), NY LP rebuild, LP 'real pricing documents' copy, shorter tours. #deployment bot posted its first real message 06:45 UTC (works; noise: ~44 image _wwcv 'tweaks' + tour steps not named - filter next). Registry 51d74d4: price report period 2 from 06:47. NEXT: Kate/Des replies (drafts given), bubble surface 'vendors/undefined' bug, beta-tester follow-up, Kate's 1,200-vendor upload, PDF backup worry.**
- **11-18 Tulle MCP connector (mcp.tulletogether.app/mcp) allowed for REC_MCP_USER_IDS=14998,2333,118 (owner, hello@ = 2333, Des = 118 desi.gaddis@gmail.com; desiree@tulletogether.com has NO Tulle account). Kate not added.**
- **11-18 ~05:40 UTC (owner): NO FREE FIRST VENDOR - ep230 allowance removed (LIVE, backup ep230_pre_no_free_vendor_2026-11-18.xs, paid path smoke-tested 200 entitled); EXP-2026-11-pi-first-vendor-free ENDED (registry 2699111). Price Report locked view = one unlock card (ww-price-report 2201271, owner must activate). REC_NAME_MATCH_V2=1 LIVE (Holly Hedge verified). Reads 11-18: 7-day pay rate by signup week ~2.2-2.6% since Oct (3.1% wk 09-29) - flat, no new drop; Planner question-askers/day 34 -> 11 since bubble launch 11-11 (bar was +15%); bubble surface logs 'bubble:/vendors/undefined' (59/81 rows); save offer 2/16; quote check ~0 use.**
- **11-18 05:31 UTC PUSHED (owner yes), NOT LIVE until owner activates + publishes (live/staging still 1400): server 1576c60 state_options (LIVE, verified) + 04e9776 name match v2 behind REC_NAME_MATCH_V2=0 (flip needs owner yes; HollyHedge Estate = one word; area-request heuristics untested on real logs); ww-planner-chat fbce08d (estimate what-if card + home 'What will my wedding cost?'); ww-price-report f646150 (simpler rows, 'Adds up to' total, split bar gone - Des feedback). WeWeb EDITOR: LP copy 'real pricing documents' (backup weweb_lp_copy_pre_real_pricing_docs_2026-11-18.json), shorter tours (backup weweb_tours_pre_shorter_copy_2026-11-18.json), /lp/new-york rebuild. Registry EXP-2026-11-price-report-vs-pi-card pushed 6166f57 (call 12-01) - log a changes[] entry when the simpler report publishes. 11-17 payment 'cliff' = normal variance (Mixpanel funnel steady).** [[project-estimate-card-whatif-2026-11-18]] ([file](project_estimate_card_whatif_2026-11-18.md))
- **11-18 #deployment STAGING-PUBLISH NOTIFIER LIVE (Railway weweb-deploy-notify-cron, */5, baseline build 1400). CHECK the first real staging publish posted a sensible note.** [[project-deploy-slack-notifier-2026-11-17]] ([file](project_deploy_slack_notifier_2026-11-17.md))
- **11-16 WHOLE-WEDDING PLANNER (Odessia-style) built + verified offline, local commits only (rec-whole-wedding 53c5576, chat whole-wedding 0cfffed); awaiting owner go-ahead to push switched off + enable for 14998; connector/ep93/texting state in same note.** [[project-whole-wedding-planner-2026-11-16]] ([file](project_whole_wedding_planner_2026-11-16.md))
- **11-17 PRICE REPORT now REPLACES the Pricing Intelligence card in the EDITOR on both vendor pages (owner's call; old card hidden, component 712fc75, report switched on). NOT published. On publish: verify live, log in the experiment registry (confounds EXP-2026-11-pi-first-vendor-free, call 11-23), check tour step + phone order + Mixpanel events. Score not yet backed by an exact rank (Xano change, not started). Sticky-bar Ask Tulle also hidden in editor.** [[project-price-report-redesign-2026-11-15]] ([file](project_price_report_redesign_2026-11-15.md))
- **11-14 TOUCHBASE decisions (Meta ads first, ads live by 11-30; Planner in all paid plans for now; no MCP until survey; vendor-site proof/media/pricing rules; Planner bar/ocean-map/grid bugs reported).** [[project-touchbase-2026-11-14]] ([file](project_touchbase_2026-11-14.md))
- **11-14 META ADS SETUP STARTED: pixel+attribution head block written (.claude/weweb_head_meta_pixel_2026-11-14.html, NOT pasted); waits on Pixel ID + admin access from Des; landing pages in WeWeb (owner). Next: paste+publish (ask), confirm PageView in Events Manager.** [[project_meta_ads_setup_2026-11-14]]
- **11-12 07:10 UTC OWNER SIGNED OFF after 2nd publish. PICK UP: (1) two-plan save offer ep313 v4 LIVE 06:40 UTC (done, registry 7c8f428); CHECK first real call returns 2 plans + a TT20 checkout works; owner must PUBLISH popup edits; (2) verify in Mixpanel: pricing_page_viewed by src, pi_gate_shown; table 1 pricing_viewed_at rows; task 10 hourly dry-run log (abandon_email scanned/eligible); (3) same-day email still OFF - owner flips ABANDON_EMAIL_LIVE=1 (test email approved); (4) ep46 relay: watch calls drop after publish, then rotate/revoke the 2 exposed Gmail app passwords; (5) Google ep7 fix: confirm first new-user 200 with agreed_terms=false; (6) bubble KILL/KEEP call due 11-12 19:07 ET (session cron - re-create if session died), judged on purchases; (7) owner must still PUBLISH popup edits when two-plan lands.** [[project_save_offer_60_forever_2026-11-11]] [[project_gates_to_pay_2026-11-12]] [[project_welcome_email_2026-11-12]]
- **11-12 WELCOME EMAIL swapped to Brevo (fn72 + ep2 LIVE); owner DELETED the 2 browser steps + will publish; stage line stays generic (owner: leave as is). VERIFY post-publish: single welcome per email sign-up, no ep46 call in live Sign Up data.** [[project_welcome_email_2026-11-12]]
- **11-12 PLG BATCH IN EDITOR (unpublished, UNVERIFIED): contextual /pricing card + src tracking + pi_gate_shown, venue-specific PDF limit card, 5-step tour, purchase->Planner, Google copy. Owner previews + publishes ONCE, then: log registry entries, verify pricing_page_viewed/pi_gate_shown in Mixpanel, Google ep7 fix confirm. #4 same-day email = owner undecided.** [[project_gates_to_pay_2026-11-12]]
- **11-12 OWNER #1 ASK: gates -> payment tracking; first read + Google terms bounce (19% of Google sign-ins fail) in [[project_gates_to_pay_2026-11-12]]. Offer: Mixpanel dashboard + daily-brief line; coral bubble proposal awaiting OK.**
- **11-12 PAID -> PLANNER redirect on /purchase-complete in EDITOR, owner PUBLISH + test with a real purchase; read Mixpanel rec_page_viewed with from=purchase.** [[project_ask_tulle_bubble_2026-11-11]]
- **11-12 ~19:00 ET: give owner KILL/KEEP call on Ask Tulle bubble - judge on PURCHASES not Planner usage (owner); day-1 purchases 6 (normal), Planner questions -80%; rule in [[project_ask_tulle_bubble_2026-11-11]] "11-12 decision".**
- **11-11 07:30 UTC overnight: ww-planner-chat 96f2cb2 (popups always in front, hide during onboarding via suppress) -> owner ACTIVATE + publish. Doc "Tulle: Meta ads test plan + agentic roadmap" https://claude.ai/artifact/H1GuFsZKVjkcsX5ndQGsMo - owner to decide: agents 1+2 (data-gap + name resolver), table-1 attribution cols + ep30 signature fix, ~$700 Meta test. Table 79 'surface' tracking LIVE + verified (bubble:/verify row).**
- **11-11 ~14:30 UTC bubble fix 6a6194c ACTIVATED + owner phone-tested "it works" (open = top layer, no auto keyboard on phones, keyboard fit). Greeting: owner did not single it out - confirm if it resurfaces.**
- **11-11 'ASK TULLE' BUBBLE + phone dock [Map/Table | Ask Tulle] + tour retarget - all in WeWeb EDITOR; owner activates ww-planner-chat 14aaafa + publishes; then set registry start/review. NPS 4-5 star comment optional built (ww-nps-survey 5349071, needs activate).** [[project_ask_tulle_bubble_2026-11-11]]
- **11-11 DAILY BRIEF "Experiments" block PUSHED main 90eec8d (experiments_brief.py: free daily Q 3-day return, TT60 offers, NPS+Trustpilot, PDF opens/questions); cron watch patterns += experiments_brief.py, ab_report.py, rec_service/plan.py. Check the 11-11 13:32 UTC brief rendered all 4 lines. Trustpilot prompt = 0 clicks of 57 asked, all time.**
- **11-11 05:14 UTC "TOO EXPENSIVE" OFFER = 60% OFF FOREVER, 12h, + EMAIL: ep313 v3 LIVE (TT60 codes, Brevo email from ep313); WeWeb card edits in editor -> owner PUBLISH; test acct 35757 (+offer60test) internal. Read ~12-02.** [[project_save_offer_60_forever_2026-11-11]]
- Older pick-up items (11-08, 11-09): see [index overflow](index_overflow.md#older-pick-up-items-3).
- Older pick-up items (11-07 and before): see [index overflow](index_overflow.md#older-pick-up-items-2).
Full dated handoff notes (Sep-Oct): [[project_session_log_2026-10]] - read it for any item below.

- **10-25 PRICING DECIDED - keep all prices; don't ask again.** [[project_hub_pricing_readout_2026-10-21]]
- **10-21 thumbs-down 33663 (Flower Mound): radius search + BYO fix pushed.** [[project_assistant_thumbsdown_33663_2026-10-21]]
- **Parked ideas** (comparable cos, mailbox, demo, REA, open product, dark mode, gifting, culture filters, A/B) - list in [index overflow](index_overflow.md).
- **SECURITY LOCKDOWN parked but CRITICAL:** public repo leaks shared secret, anon Stripe proxy ep27, ep21 to check. [[project_security_lockdown_2026-09-29]]
- **FUTURE (owner 10-19): full stack + product crawl to streamline and DELETE what isn't needed** (Xano, WeWeb pages/workflows, Railway services, tasks, tables). Keep a running "deletion candidates" list as we go; two-pillar product [[project_two_pillar_consolidation]] is the frame.
- **10-28 Xano: 31 unused objects DELETED; security batch 1 LIVE; NEXT ep30 webhook, leaked secret, relays (overflow).** [[project_endpoint_auth_audit]] [[feedback_simplify_not_stack]]
- **Learnings:** verify WeWeb publishes via live `/data/<pageId>.json` + main.js; Xano request-history created_at = FINISH time; Xano pushes don't clear response cache; push to marthv/* via `gh auth switch`.
- Settled, don't re-raise: admin mirror [[project_admin_mirror_mode]]; upgrade path [[project_upgrade_credit]]; ep20 [[project_ep20_delete_any_account]]; Google sign-in [[project_google_signin]]; known-open list [[project_known_open_items]]; perf needs Referer scoping [[project_perf_baseline_digest]].

## Read first
- [Price is not quality](feedback_price_is_not_quality.md) — owner 11-16: never frame expensive as bad or good (no green/red, no awards, no grades); any score must show its working
- [Build and test beats surveying](feedback_build_test_over_survey.md) — owner 11-15: ship lots, one structured experiment per build, clear durable reports
- [Popups always in front](feedback_popups_in_front.md) — owner rule: floating UI (beacons, docks) never over any popup
- [Product-development reading list](reference_product_development_reading.md) — owner 11-04: 10 links (Linear, Figma, Notion, Ramp, Duolingo, Shopify, Coda, Gong, Perplexity, PostHog) = backdrop for ALL product work
- [Launcher profiles](reference_launcher_profiles.md) — personal.bat = ~/.claude + GH_TOKEN marthv; healthie.bat = ~/.claude-healthie; Railway one global login; vendor_portal project DELETED 11-03
- [authToken var is email-login only](reference_authtoken_variable_email_only.md) — Google sessions only have the plugin accessToken; bind to pluginVariables, never to variables['6fcad7ad'] alone (planner-chat 'Sign in' bug 11-01)
- [Experiment registry](project_experiment_registry.md) — LOG EVERY experiment launch/read in tulle_vendor_data_updates/experiments/experiment_log.json + push; admin dash 🧪 tab
- [Explicit yes before any send](feedback_explicit_send_confirmation.md) — 'X is good' is NOT a send approval; ask 'send now to N people? yes/no'
- [No imposed budgets](feedback_no_imposed_budgets.md) — never show per-category targets the user didn't set; splits are on-request suggestions only
- [Sonnet 5.5 for everything](feedback_sonnet55_everywhere.md) — extraction, pilots, assistant; existing pipeline still on 4.6 until validated + approved
- [Muse-style agent](project_muse_style_agent.md) — north star: assistant works like Meta's Muse (long-running tasks) but wedding-specialized; idea only
- [Two-pillar product](project_two_pillar_consolidation.md) — fold Cost Tools + Budget Tracker into Tulle Assistant; product = research DB + AI planner
- [PDF opens = money metric](feedback_pdf_opens_money_metric.md) — lead readouts with % of new members opening a PDF (baseline 60% in 1 day); board 11591217
- [Sign-up page: no instant demo link, keep checkbox](feedback_signup_page_link_and_checkbox.md) — delayed/triggered link only; fix Google terms leak inside the checkbox
- [View Plans stays lavender in green panel](feedback_view_plans_lavender_in_panel.md) — 6a24f714/74c474ca; green-on-green was invisible (10-15)
- [Exclude internal accounts](feedback_exclude_internal_accounts.md) — drop vivek.marthi* / @tulletogether.com / ids 14998, 30614, 32636, 31619 + team 118 (Desiree), 5622 (Kate) and their test accts from real-user numbers
- [Ask before live or paid](feedback_ask_before_live.md) — user felt I ran "rampant" 10-04; ask before each deploy/push/spend
- [Secrets handling](feedback_secrets_handling.md) — read ~/.tulle_rec.env ONLY via Python; bash `source` leaked both secrets 10-03
- [Couples count = 60,000+ (from 11-12)](feedback_50k_users_claim.md) — owner: "60k everywhere"; pre-migration users not in table 1, never flag the gap vs table counts
- [Paywall popup redesign](project_paywall_popup_redesign.md) — 48be8a6c is ALSO the Discovery upsell; RESTYLE the CTA, never delete
- [Evidence + intention rules](feedback_evidence_and_intention.md) — "it parsed" is NOT evidence; quantify who it helps before building
- [Tulle Together focus](project_tulle_focus.md) — WeWeb + Xano; data-loss bugs and load times
- [Table 11 has no validators](reference_xano_table11_no_validators.md) — safe for full-schema replace
- [Xano MCP quirks](reference_xano_mcp_quirks.md) — `updateTableSchema` STRIPS validators; smoke-test hot eps after updateAPI
- [XanoScript traps](reference_xanoscript_traps.md) — `|filter:` needs `$$`; `api.request` doesn't throw on 4xx; all parse fine, fail at runtime
- [Xano write-access paths](reference_xano_write_access.md) — metadata REST can't write logic; only the MCP (curl-drivable)
- [WeWeb f-formula comment trap](feedback_weweb_formula_no_leading_comment.md) — a `//` comment in an `f` binding makes it falsy; use `js` type
- [WeWeb component gotchas](reference_weweb_component_gotchas.md) — values inside components = context.component.variables; customCss edits MERGE; X-Branch header routes to stale branch; never abbreviate uids for the owner
- [WeWeb MCP limits](reference_weweb_mcp_limits.md) — can't edit plugin-action workflows or collections; VERIFY a publish by grepping live main.js
- [Screenshots](user_screenshots.md) — `C:\Users\vivek\Pictures\Screenshots`

- [Brevo automations (what already emails non-payers)](reference_brevo_automations_2026-11-12.md) — CORRECTED: "Still thinking" lands day 5-6 not next day; 3-PDF wall emails same evening; DKIM ok, likely inbox

## Security / access
- [Slack posting](reference_slack_connector_wrong_workspace.md) — DM the owner (U05UJMCHUV6) for Tulle updates, not a channel; channel_not_found = connector drifted to Healthie workspace
- [Admin mirror mode](project_admin_mirror_mode.md) — DELETED 2026-09-19; history + rollback only
- [Pre-auth race SIGNS USERS OUT](project_preauth_race_signs_users_out.md) — fetchUser catch = `logout()`; fix = token-wait in `Open Channel`
- [Admin view-as from Slack](project_admin_view_as.md) — kill switch OFF; only WeWeb workflow remains
- [ep20 delete-any-account](project_ep20_delete_any_account.md) — FIXED; the Aug audit missed the DELETE
- [Endpoint auth audit](project_endpoint_auth_audit.md) — eps default `auth=false`; ep199 still dumps 27k pricing rows; anon writes to table 36 open
- [PDF corpus exposure](reference_pdf_corpus_exposure.md) — ep146 GATED (403); ep93 still gives PDF_Link anonymously; owner DEFERRED fix 11-09 - do NOT re-raise
- [Paid-access entitlement model](reference_access_entitlement_model.md) — `date_until_access` + `forever_access_purchased` only; `auth/me` reads live
- [Endpoint lockdown 401s](project_endpoint_lockdown_401s.md) — warmer left dead by user choice — do not re-raise
- [PDF dead click](project_pdf_dead_click.md) — `window.open` after an await is popup-blocked; needs publish
- [PDF warning at view 3](project_pdf_warning_view3.md) — PDF wall ~18% of purchases (CORRECTED 10-14 from a stale 40%); mobile opens PDF before entitlement check
- [Post-purchase access fix](project_post_purchase_access_fix.md) — "paid but locked" = WeWeb not re-fetching
- [ep30 BNPL in Slack](project_ep30_bnpl_slack.md) — ep30 still has NO Stripe signature verification
- [Stripe payment webhook](reference_stripe_payment_webhook.md) — ep30 silent-fails on bad `metadata.weeks`
- [Stripe writes go through Xano](reference_stripe_write_via_xano.md) — SUPERSEDED 10-02: claude.ai Stripe connector CAN create prices/links; Brevo 201 ≠ delivered
- [Pre-auth 401 fix + Slack webhook leak](project_preauth_401_fix.md) — user must still rotate exposed webhooks

## Pricing Intelligence (venue panel)
- PI panel memories (11 files) - list in [index overflow](index_overflow.md)

## Search / filters (ep119 / ep121)
- Search/filter memories (21 files, START HERE = package-grain filters) - list in [index overflow](index_overflow.md)

## Data model
- Data-model memories (13 files) - list in [index overflow](index_overflow.md)

## Extraction pipeline
- [PDF re-scrape scope](project_pdf_rescrape_scope.md) — top 3,000 by clicks + at least 10 per state/country (all if fewer); fields in DATA_WISHLIST.md
- [Extraction pipeline repo](reference_extraction_pipeline.md) — `tulle_vendor_data_updates`; tables 10/11/36
- [Xano POSTs drop rows](reference_xano_post_dropped_rows.md) — retry via `_post_row`
- [Batch extraction via Files API](project_batch_extraction_filesapi.md) — sonnet-4-6; 3 passes per PDF
- [Extraction venue pre-filter](project_extraction_venue_filter.md) — ep177 skips non-venues
- [Confidentiality flagging](project_confidentiality_flagging.md) — flag-for-review, NOT auto-hide

## Infra / ops
- [Daily brief cron watch patterns](reference_health_cron_watch_patterns.md) — push to main at ~13:35 UTC killed the 10-08 brief; new report modules MUST be added to watch patterns
- [Railway admin dashboard](reference_railway_admin_dashboard.md) — in tulle_vendor_data_updates; push needs marthv + credential override
- [Pipeline Flask app](feedback_pipeline_ignore.md) — SUPERSEDED; Railway does not deploy pipeline/app.py
- [Xano connection drops](reference_xano_connection_drops.md) — curl public + google to tell 503 from DNS blip
- [Xano compute reduction](project_xano_compute_reduction.md) — Phase B = enrichment denorm
- [Railway Xano load fix](project_railway_xano_load_fix.md) — ep199 slim; retry 502-504
- [Brevo sync tasks](project_brevo_sync_tasks.md) — task 3 imports ~20.5k; list 88
- [Hub mapping](project_hub_mapping.md) — tables 54/55/57; task 12 deleted
- [Dashboard theme modes](project_dashboard_theme_modes.md) — dataframes read config.toml, NOT CSS
- [Cohort analytics](project_cohort_analytics.md) — amount = DOLLARS; engagement→pay curve is CONFOUNDED
- [Mixpanel tracking gaps](project_mixpanel_tracking_gaps.md) — check day-of-week before calling a dip; project 3963130
- [Venue Pricing tab](project_venue_pricing_tab.md) — vibe gate is PER VENUE; helper above `_vp_load` steals its cache

## Product / other
- Product/other memories (24 files) - list in [index overflow](index_overflow.md#product--other).
