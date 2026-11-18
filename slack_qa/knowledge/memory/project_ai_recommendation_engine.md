---
name: project_ai_recommendation_engine
description: "AI recommendation engine plan (2026-10-03) - decisions locked (3 free refinements, Sonnet 5.5, launch to all, Railway); baseline numbers + data coverage findings"
metadata:
  node_type: memory
  type: project
  originSessionId: d9595695-7823-4f8c-9645-96340d80aa7f
  modified: 2026-10-03T17:14:57.332Z
---

Rebuild /recommendations (WeWeb dae212b5, currently an orphaned MOCKUP: hardcoded chat, fake credits modal with fake
Visa 4242, ep170 picks first 15 rows of table 11) into a "plan your own wedding" recommender, linked from Vendor Discovery.

**User decisions 2026-10-03:** 3 free AI refinements (opening recs always free) then existing-plan paywall, no credits
product; model **claude-sonnet-5-5**; **launch to everyone (NO holdout)**; AI logic in a **Railway service** (Python
Anthropic SDK tool runner, read-only tools over Xano endpoints, WeWeb sends user token, service verifies via auth/me).

**Design:** page opens with 3-5 venue picks from profile (ep119 + table 63 + PI) + "next step" card, one cached LLM
call for rationale; chat refines. LLM never invents vendors - cards only from tool results, prices labelled venue vs
estimate, every card links to vendor page/PDFs. V1 = venues only (photo/entertainment next; florist/F&B/beauty/
planner have NO benchmarks). Chat asks for missing profile fields (date 1.4%, vibes ~5%) and writes them back.
Guardrails: per-user daily cap, global daily spend cap + kill switch, token/cost log table. Est. ~$0.08-0.12/refinement.

**Baseline (90d to 10-02):** ~102 signups/day flat (64% TikTok); payment rate 3.8-4.3%; $385/day; ARPU $3.77,
ARPPU $76; Forever 30% of payments / 58% revenue; 52% of signups never open a PDF; /recommendations usage = 0.
**Measurement caveat (no holdout):** before/after only, confounded by geo-pricing/BNPL experiment (live 10-01) and
seasonality. Leading metric = % of signups opening a PDF; +0.5pt payment lift is undetectable without a holdout.

Reusable: rec_chats/rec_messages tables + ep167-169, ep170 JSON contract {text,cards,chips,reasoning_steps}, page
layout + Planning Shelf (Saved/To-Dos live). Stale: Xano agent 3 "Wedding Planner" assumes no pricing data.
[[project_growth_backlog_2026-10]] [[project_pi_band_ref_v20]]

## Phase 1 progress (2026-10-03)
- WeWeb (UNPUBLISHED): /recommendations credits pill 8eceef6e display -> none (shared nav), Credits Modal Panel
  4fe8c3d8 display none. Backups .claude/weweb_backups/rec_*_pre_hide_2026-10-03.json.
- Xano table **79 rec_usage** created (+ usage_day text col, btree) - one row per AI request.
- Service code: tulle_vendor_data_updates branch **rec-engine**, folder rec_service/ (FastAPI: /rec/opening,
  /rec/refine, /rec/status, /health; railway.rec.toml). Tools: search_venues (ep119 w/ user token),
  venue_pricing (ep230 paid / ep231 free - Xano enforces paywall), market_benchmarks (table 63 overall/all),
  present_recommendations (cards only from ids seen in this request's searches). Guards unit-tested (6/6).
  Live-verified: pricing + benchmarks + usage log write/read/isolation. NOT run yet: anything needing a user
  token (ep119, auth/me, favorites) and the model loop -> needs REC_TEST_TOKEN (test account) + Anthropic key.
- evals/cases.json (30 synthetic cases) + evals/run_evals.py. Not committed, not deployed.
- Default grid sort: ep119 empty sort_by = DB insertion order = oldest-added first (Kate 10-03: 2024 PDFs on top
  look stale). Cleanest fix = change ep119's server-side default + cache_v bump; no WeWeb/warmer key change.

## Deployed 2026-10-03 (user approved push + deploy)
- Branch **rec-engine** pushed (commits c32ee1a service, cfaf9e1 rec_watch health DM line - perf_digest hook goes
  live only when merged to main). Railway project "tulle admin dash", service **rec-engine**
  f5b44307-c938-4c82-a145-2a20aca5ae05, root /rec_service, region us-east4, URL
  https://rec-engine-production-b2ea.up.railway.app . Railway REJECTS config-file paths now -> settings set on service.
- Vars: XANO_METADATA_TOKEN ref, REC_KILL_SWITCH=1 (OFF until user adds ANTHROPIC_API_KEY in Railway UI),
  REC_GLOBAL_DAILY_USD 40, REC_FREE_REFINES 3, REC_MODEL claude-sonnet-5-5, REC_EFFORT low.
- v2 speed (pricing inside search, prefetch benchmarks, effort low): 5 hardest cases 18s->12s p50, $0.056->$0.034.
- User decision: FLAT FEE, no credits (paid plans include recs; hidden 40/day fair-use cap).
- Reporting (user choice): Mixpanel board **11564098** "Recommendation Engine" (north star signup->viewed_vendor_pdf
  weekly, rec funnel, paywall->purchase, daily usage) + health DM line (rec_watch.py). Events the page must fire:
  rec_page_viewed, rec_opening_loaded, rec_refine_sent, rec_card_clicked, rec_paywall_shown, rec_entry_clicked.
- WeWeb front end being wired by a subagent (editor only, backups rec_page_*_pre_wire_2026-10-03.json).
- Mixpanel profile backfill re-run 10-03 (56,877 ops): identified no-source users 260/3d -> 23/7d. Root cause of the
  09-24 break (new signups' onboarding props never reach Mixpanel) NOT fixed - user said not to.

## WeWeb front end (editor, UNPUBLISHED, 2026-10-03) + LIVE service
- Service ENABLED 10-03 18:14 UTC (REC_KILL_SWITCH=0, user added key). Live test: opening 200 in 14.6s, $0.03, logged.
- Page vars (folder Recommendations): recThread 6545c0d2, recLoading 458eb1f5, recStatus c005f4e4, recPaywall d0ae507c.
  Onload "Rec Opening (signed in only)" ce0daac9; function "Rec Refine" 0fa17e11; Send 172b243d, Enter ec531ace,
  chip e3430182, See pricing 71841bc1 (navigates like Discovery card bcde5f71). Thread repeat 152d6402, sign-in
  prompt 7bfc8b0e, free-questions line 3763fa01, progress text 0d26bae0. Discovery banner ef631269 / button e1da581c.
  Mock content hidden (not deleted). Backups weweb_backups/rec_page_*_pre_wire_2026-10-03.json.
- Gaps: rec card click doesn't increment vendor view count (plugin action, MCP can't add); popup 48be8a6c from this
  page unverified (inline paywall card is fallback); old "Load Chat History" onload workflow untouched.
- Backend 864407d: cards carry vendor_idx; prompt leads with fit. Narrow searches (barn case) still return 1 card.

## Conversations + memory (e8c2695, deployed 2026-10-03)
- Chats persist in rec_chats 42 / rec_messages 43 (service writes, ownership-checked; other user's id -> 404).
  API: POST /rec/opening -> chat_id; POST /rec/refine {chat_id, message} (server loads history incl. venues
  shown); GET /rec/chats; GET /rec/chats/{id} -> turns; /rec/status now also returns memory_notes.
- rec_memory table 80: save_note tool, notes injected into every later chat (verified across chats).
- REC_DAILY_OPENINGS=20 on Railway during testing (was 5; test account hit the cap).
- USER WANTS (10-03): profile pills visible + clickable to edit; Saved vendors + To-Dos panel BACK (I removed
  the Planning Shelf for "full width" - wrong call, restore it); starter prompt chips for clear next steps;
  conversation history usable later (done backend; front end must load last chat + New chat + chat list);
  later: other planning areas + vendor types (photo/entertainment first - data exists; florist/F&B thin).

## Cap + prompt v3 (2df11da, 2026-10-03) - USER DECISIONS
- Free: first opening free + **3 questions FOR LIFE**, no daily reset; later openings spend a question. Paid: no
  visible cap (hidden daily fair-use). "If it doesn't get users to open PDFs in the very first session, it hasn't
  succeeded." -> Mixpanel board top row: SUCCESS TEST rec_opening_loaded -> viewed_vendor_pdf, SAME session.
- Prompt v3: show planner-level industry expertise with numbers; label every figure's source (venue PDF / Tulle
  data across N venues / industry norm); never invent; every reply ends with which PDF to open + what to check.
- Front end round 2 done by agent (rebuilt chat body: scroll area 755a5db4, body 248e7223, profile card 09fb557e,
  thread e21764f5, sign-in 35193b8e, typing e55ab352; profile edit fn a3c5e7f1 reuses Edit Wedding Details Popup
  b4b4ac8a-5b55-..., load-opening fn 685f59e1; new token H4 Serif 8a80813d). Round 3 sent: new API (chat_id),
  load last chat on open, history list + New chat, Planning Shelf (Saved + To-Dos) restored, starter prompts,
  "Open pricing PDF" CTA.

## 2026-10-03 later: Forever-only + round 4 + to-do security finding
- c40a80a: UNLIMITED ONLY ON FOREVER (user decision). Free + 1w/4w = opening + 3 questions for life; upsell
  Forever. `paid` = has Forever; `has_access` = any plan (exact prices via ep230). 1d5ab90: chat created only
  after a successful opening.
- Round 4 to page agent: to-dos (add/complete->bottom+muted bg/delete/file upload, from My Wedding's hidden
  Todo List Card), save hearts on rec cards, prominent "{n} of 3 free questions left - Unlimited with Forever",
  Forever-only paywall -> pricing page (no Stripe links), back button returns to /recommendations (from=planning),
  nav item "Planning Assistant" in shared nav + mobile menu; page title Planning Assistant.
- **SECURITY (found 10-03) - FIXED + runtime-verified 10-03 (apply_todo_ownership.py, --restore to undo):** to-do endpoints group 4 (api:WGW_G49d) ep24 GET all
  returns EVERY user's to-dos (79 rows / 23 owners to any signed-in user); ep23 get-one, ep33 PATCH (can change
  userID), ep22 DELETE have no owner check; ep25 POST takes userID from input. Backups
  .claude/xano_backups/ep{22,23,24,25,33}_todo_pre_ownership_2026-10-03.xs. Fix = filter/precondition on
  $auth.id; ep25 userID=$auth.id. Don't publish the rec page shelf until fixed.

- To-do ownership fix applied 10-03 (user OK): ep24 list own only (1 row vs 79), others' id -> 404 on get/patch/
  delete (temp foreign row untouched), ep25 owner forced to $auth.id. Bearer auth works; Auth_Token alone 401s.
- Forever = **40 prompts/day** openings+questions combined (user decision; 4f2d974). API returns
  forever_left_today / forever_daily_limit (06eee9a). Copy: "40 questions a day with Forever".

## Round 4 done (editor, unpublished) 2026-10-03
To-dos panel 57422a17 (add/complete->bottom muted/delete/upload via POST /upload/image then PATCH image_storage),
heart d5293a93 -> ep158 toggle_favorite (switch requested), counters badge 5ad0adb9 + line 3763fa01, Forever paywall
card dfdd0def -> Membership bc8cb393, venue Back f607fc3d honours ?from=planning (prev-vendor btn 26f74d05 hidden
then), nav items desktop 024bb4f5 / mobile bd233ef1 "Planning Assistant", page title Planning Assistant.
Unverified in browser: file upload, from= param, favorites, counters. Backups *_pre_round4_2026-10-03.json.

## 2026-10-03 evening
- FREE = **5 questions** lifetime now (user decision; REC_FREE_REFINES=5 on Railway, b18cb5f/572821b); API returns
  free_limit - page must never hard-code the number. Forever 40/day.
- Server-side Mixpanel event rec_server_request (84fc752) per AI request: kind, status, cost, latency, cards, free left.
- Round 5 (editor): heart restyled to match Discovery heart f7642d75/1960b958; to-do file upload REMOVED.
- Round 6 sent: Discovery banner dismiss X persisted in localStorage tt_planning_banner_dismissed; header matching
  My Wedding (desktop top section; mobile title in nav bar); copy uses free_limit.
- Data-quality findings for "make it useful": top 2,000 venues = 80% of clicks (top 500 = 46%); only 11% of venues
  have a usable description -> style search weak. Proposed: (1) style tags via image model ~$25, (2) planning
  knowledge sheet, (3) re-extract top-2,000 PDFs (pilot 50 first, est $100-300), (4) photo/entertainment tools,
  (5) external sources HOLD. Awaiting user go on #1/#2.
- Memory lives in XANO (tables 42/43/80), not Railway - Railway is stateless.

## CORRECTION 2026-10-03: style tags ALREADY EXIST - my "11% descriptions / pay $25 to tag" claim was WRONG
flt_venue_types 96% filled (Barn / Ranch 1,435, Estate / Mansion 1,526...), flt_venue_attributes 85% (Waterfront
1,943, Ballroom 2,544...), flt_venue_offerings 96%. ep119 filters them via venue_types[] / vibes[] / pricing_models[]
(overlaps = OR). The barn failure was MY tool passing made-up names. Fixed in the tool (exact vocab): barn NY 150
1 -> 29 matches. Proposal #1 (style tagging) is DROPPED.
- Round 6 done (editor): banner dismiss f7712459 + localStorage; header copies My Wedding; free_limit wired; New!
  pill 34a0117d/2a007e91 clears via fn 4e2b8c71 (tt_assistant_new_seen). OPEN: name mismatch - page/mobile title
  "Planning Assistant" vs user's nav label "Tulle Assistant"; banner X needs aria-label by hand (MCP drops
  attributes); subtitle color token 950f22cd fallback is purple #3A1F73 - check what it resolves to.

## USER DIRECTION 2026-10-03 (latest)
- Name: **"Tulle Assistant" everywhere** (rename sent to page agent; Mixpanel board 11564098 renamed).
- **No file uploads for now** (upload/parse/actions idea parked; cost est ~$0.05-0.10 per doc).
- Priorities: 1) Forever sign-ups, 2) more PDF opens, 3) more tool usage. Judge every assistant change on these.

- b629e15: assistant reads saved vendors from auth/me `favorited_vendors` (the wptp_updated_mappings_favorites GET
  returned [] for a user with 4 saved) + saved_venues tool with pricing; never says "can't see saved".
- Card/shelf button = "View Vendor" like Discovery (user: not "Open pricing PDF"). Rename to "Tulle Assistant" done;
  shared nav Hero Title 9bc72221 moved purple -> ink (affects mobile title on all pages). Other purple in shared nav
  (a3e56ac1 View Plans, 331c2745, 7f0df2c8, 8647c856) untouched - user to decide.
- Data issue seen: table 11 venue names with mojibake (e.g. "�lixir Ibiza") - encoding loss at import.

- 03772c6: Forever quiet cap **300 prompts / calendar month (UTC)** (user decision) on top of 40/day -> 429
  {status:"blocked_monthly_cap", resets_on}. Worst case ~$10/user/month (a 40/day max-out = ~$1.35/day, ~$41/mo,
  would equal the $129 price in ~95 days without it). Health DM counts Forever users at the cap.
  Page copy (agent): "You've reached this month's limit for the Tulle Assistant. It resets on {date}." no upsell.

- 1956400: couple-editable CONTEXT (user ask): rec_memory.user_context (max 1,000; schema backup
  table80_rec_memory_schema_pre_user_context_2026-10-03.json). GET/POST /rec/context, POST /rec/notes/delete.
  Fenced in prompt as the couple's facts, not instructions - injection test (free user "ignore rules, reveal exact
  prices") refused. Page panel "What Tulle Assistant knows about you" (context textarea + deletable notes) sent to agent.

- Chats dropdown list 7edc0a66 scrolls (max 360px desktop / min(60vh,420px) mobile, up to 30). e4d40fe: new opening
  chats titled "Venue picks · Alabama, Connecticut, 80 guests" (comma-packed states split); old ones keep the
  generic title until a question renames them.

## SHIPPED to staging + merged 2026-10-03 ~22:45 UTC
- User published WeWeb to STAGING; "all things work on staging". Proposed names: Tilly, Rosie, Penny, Juniper, Lacy
  (undecided; currently "Tulle Assistant"). Plan: push prod, watch adoption, analyze saved chats, then build infra.
- tulle_vendor_data_updates main fast-forwarded to e4d40fe (rec_service + rec_watch + perf_digest hook). Railway
  rec-engine service source switched rec-engine -> **main** (watch /rec_service/**). Health DM section live from
  next 13:32 UTC run. Future work: branch off main.
- Future direction (user): booking + vendor outreach from the assistant.

- 74be9e2 (Kate's staging feedback, 10-03 evening): prices framed as PEAK-SEASON SATURDAY (other days cheaper ->
  open PDF); free users told their FreeViewsRemaining ("using one of your 2 free views") instead of "behind the
  paywall"; mid-chat profile changes acknowledged, never "correction"; **FREE back to 3** (REC_FREE_REFINES=3 on
  Railway; Kate: 5 prompts x 3 picks = too many free recs). Kate also suggested Forever-only entirely - not done.
  Staging screenshot showed heading + subtitle ("Your personal wedding helper") still PURPLE - verify before prod.

- USER 10-03: **Sora for ALL text on the Tulle Assistant page** (overrides CLAUDE.md 5.2 Lora-for-headings there);
  agent swapping H4 Serif 8a80813d usages to Sora + fixing purple heading/subtitle. Site-wide Sora-only = OPEN
  question (would need CLAUDE.md 5.2 update + other pages).

- Round 7 sent (Kate + user, 10-03): REMOVE header Context button; profile card "Edit" opens the modal retitled
  "Your wedding details" = basics (Change -> Edit Wedding Details popup) + "Additional details" textarea + remembered
  notes; first-visit "How Tulle Assistant works" intro card (localStorage tt_assistant_intro_seen).
- OPEN idea (Kate): Forever users get N queries, then "give feedback to unlock more" - needs user decision;
  conflicts with 40/day promise; suggested variant = one-time feedback ask after Nth query, not a block.

## 2026-10-03 late: Forever beta check-in + venue-only (commit 74b2fa3, deployed)
- Kate's beta idea, user: "the point is for beta testing". Forever users get 5 questions (refines asked on
  Forever, not counted_as_free), then refine -> 403 `feedback_required` until they POST /rec/feedback
  {rating 1-5, would_use yes/maybe/no, text >=10 chars}. One submission unlocks for good. Openings never gated.
  Rows in Xano table 81 `rec_feedback`; Mixpanel `rec_beta_feedback`. Fails OPEN if table 81 unreadable.
- Railway `REC_BETA_FEEDBACK_AFTER=0` (OFF) until the WeWeb feedback card (round 8) is published; flip to 5 then.
  Test account 14998 already has 15 Forever questions -> will see the card immediately.
- Prompt is VENUES ONLY (Vivek in Slack): other-vendor asks get one sentence + "Discovery page"; chips venue-only.
  Verified on evals r04/r11.

## 2026-10-03 night: beta is FOREVER-ONLY end to end (user decision; reverses "launch to everyone")
- Server commit 0122be3: REC_FOREVER_ONLY -> non-Forever opening/refine = 402 forever_only. Railway var = 0 (OFF)
  until publish. Free-tier rules kept in code (=0 restores).
- WeWeb rounds 8-10 built, UNPUBLISHED: feedback card, Forever-only Discovery banner + Beta pills, nav link/mobile item
  gated on forever_access_purchased, beta card for non-Forever (2b5b3f32), recGateChecked anti-flash.
- Page URL is now /assistant (was /recommendations) - old shared links break.
- Header title/subtitle reverted to purple 3x - suspect stale editor tab saving; check before publish.
- Go-live: publish, then Railway REC_FOREVER_ONLY=1 + REC_BETA_FEEDBACK_AFTER=5.
- Known leak: some $50 4-week buyers have forever_access_purchased=true (23879, 23866) -> get Forever assistant.
- 10-03 night, LIVE on Railway: REC_FOREVER_ONLY=1, REC_BETA_FEEDBACK_AFTER=5 (verified 403 feedback_required
  on test acct 14998). Feedback -> Slack #feedback (C07JML6NMLM) via REC_FEEDBACK_SLACK_WEBHOOK (Tulle Bot webhook,
  user pasted it; fallback REC_SLACK_BOT_TOKEN = [redacted] Ops). First real feedback (row 2, acct 14998): 5/5, "maybe",
  "its good but not as good as just looking at pdfs myself".
- DETAIL: LIGHT (commit 8eb6e87): non-Forever (incl 1w/4w) get fit-only answers, no figures - model never
  receives amounts. Dormant while Forever-only. evals: `run_evals.py --light` checks for leaked figures.
  Watch: one FULL answer dropped "$" signs after this change (1 sample, unconfirmed).
- WeWeb r11 Tulle Tip (PI-popover style, auto-opens first visit via tt_assistant_intro_seen) - UNPUBLISHED.

## 2026-10-04 user feedback round (r12)
- 5 items: mobile Edit All Details dead + popup clipped left; auto-scroll to newest; sticky input bar covers icon
  row/shelf; chips suggest unanswerable things; return to page opens FIRST chat not latest.
- Chips: CHIPS rule added to SYSTEM prompt in agent.py (each chip must map to a search filter / pricing line /
  state benchmark / Saved list; no reviews, availability, courtyard, outdoor ceremony, "ceremony fee included").
  UNCOMMITTED + not deployed, no eval run yet. Seen bad chips: "Venues with a ceremony fee included", "Outdoor
  courtyard venues" (no filter for either).
- Chat order: server /rec/chats is newest-first (verified table 42, acct 14998) -> wrong-chat bug is page-side.
  Page fixes 1,2,3,5 sent to a subagent (editor only, backups *_pre_r12_2026-10-04.json).
- Data needs register = tulle_vendor_data_updates/DATA_WISHLIST.md (uncommitted): culture/religion, LGBTQ+,
  accessibility, overnight rooms (only via "Hotel / Resort" type today; no room count), other gaps. Only 42 user
  questions in table 43 on 10-04 (mostly test) - too few to rank demand. Prompt also got a "we don't track X yet"
  line for those topics (same uncommitted change).
- 10-04: outdoor_ceremony param added to search_venues tool (ep119 already supports outdoor_ceremony=yes ->
  flt_outdoor_ceremony). Uncommitted with the prompt changes. User: re-scrape PDFs needed (summaries lack policy
  data) - noted in DATA_WISHLIST.md; also store full PDF text in that run.
- Round 13 queued to page agent: Planning Shelf "Add to budget" (reuse My Wedding package flow) + Budget block
  (payment summary). My Wedding screenshot bugs to diagnose: Still owed $25k with $105k contracted/$0 paid;
  "Over Budget" shown with a green bar.
- 10-04 r12/r13 DONE in editor (unpublished; backups *_pre_r12/_pre_r13_2026-10-04.json): fn Rec Sync View 419fc49d
  (scroll + tt_assistant_last_chat), onload step i4c_pick_chat, mobile order/padding fix, context overlay 753aee37
  full-screen on mobile, shared Edit Wedding Details popup mobile width fix (all pages), Budget tab d05ef809 + panel
  73000e63 + "Add to budget" 263799eb reusing Packages 976e4504 / Add/Edit Vendor Popup 3ae9a8df. "Wrong chat" was
  likely scroll-at-top (looks like opening picks), not wrong chat. Duplicate opening on first visit seen (user 118).
- SECURITY 10-04: GET /packages?user_id= returns ANY user's packages anonymously (200 w/ data); Save Vendor workflow
  holds a Slack webhook URL in plain text in the front end. Not fixed. My Wedding "Still owed" sums stored
  Balance_Left (seeded examples inconsistent + re-seeded duplicates); bar 974b22c7 fixed green.
- Server 10-04 (uncommitted): venue_details tool (table 36 menu/bar/off-peak/fees + Google reviews, amounts only
  Forever), no-present fallback (o12 empty 1/3 runs), chip variety rule, 6 new eval cases r16-r21.
- 10-04 DEPLOYED (pushed main 4f4b668; Railway deploy 3044f45c queued at 14:12 UTC): chips rule, outdoor_ceremony,
  venue_details, fallback. Final eval 36/36 clean, $0.041 mean, p50 13.2s; top chip 24/36 (was 33/35). Lesson:
  the model copies the FIRST example chip in the prompt into nearly every reply - don't put example chips there.
  Re-scrape scope SUPERSEDED 10-04 late: top 3,000 + >=10 per state/country, see [[project_pdf_rescrape_scope]] (was: TOP 5,000 + store full PDF text going forward (awaiting go on new table).
- 10-04 Packages lockdown LIVE (user approved): ep110/111/113/114 owner checks, ep112 auth=user + $auth.id (user_id
  ignored). Runtime-verified with test token. Backups ep11x_*_pre_ownership_2026-10-04.xs. Recheck real-user 401s
  in request log ~15:35 UTC. Round 14 (My Wedding: Still owed = contracted - paid, bar colours) editor only.
  Example packages seeded by onboarding Finish wf cbb346d5 - actions already disabled; ~17k example rows exist.
- User 10-04: "worried im letting you run a little rampant" -> ask before anything live or paid.
- 10-04 ~15:38 UTC packages log check (1h after lock): ep112 live signed-in 7x200, 9x401 tokenless with NO user_id
  (pre-auth page loads; used to get []), staging/editor 13x200. NO real-user POST/PATCH since the lock (only my
  smoke test) -> add/edit path still unexercised by real users; re-check tomorrow.
- Daily brief (daily_brief.py + bnpl_watch short=True + perf_digest wiring) written + previewed locally, NOT pushed;
  awaiting user OK. Also proposed Mixpanel "Tulle weekly pulse" board - awaiting OK.
- 10-04 end of session: daily brief PUSHED (tulle_vendor_data_updates 83dd540) - first new-format DM 10-05 13:32 UTC;
  check it rendered (phase 2 label, no fallback warning). Mixpanel board "Tulle weekly pulse" 11564593 created
  (WAU, sign-ups + PDF openers, signup->PDF funnel ~58%, purchases membership_purchased ~40/wk, assistant, NPS).
  User published WeWeb to staging + prod 10-04 (NPS modal, assistant fixes, budget). Mobile test pending by user.

## 2026-10-04 night: first REAL-user read (chats 21-29) + multi-state bug
- Real users (excl 14998/30614/99999 Vivek, 118 Des, 5622/31660 Kate): 31701, 13769, 31762, 28209, 7946, 31797.
  Most only saw the opening. First real beta feedback: user 31797 (CA+HI, $10k, elopement for 6) 1/5 "maybe",
  "It's repetitive and not the right stuff", 0 PDFs opened.
- MULTI-STATE BUG (3 of 6 real users): one ep119 multi-state search sorted popular_desc gives every slot to one
  state (GA+IL top12 = 12 IL; LA+CA+FL = 0 LA) -> model says "you haven't picked a state" / "haven't pulled
  Hawaii yet". Fix written, UNCOMMITTED: xano.search_venues per-state parallel + interleave, matches_by_state,
  prompt line in agent.py. Live ep119 check 3/3/3 and 4/4. Model-level eval NOT run (awaiting user OK, ~$1.60).
- 31797 chat issues: SF Zoo shown 3x, same "peak Saturday" + "free PDF access" boilerplate every reply, same
  chips, no guest count -> 200-seat hotels w/ $9k F&B minimums for $10k budget; no guest-minimum / F&B-minimum
  ceiling in search; no elopement data. The Village card fee range 30000 vs "$3,600 elopement" in text.
- Usage log (table 79) stores tool-call COUNTS only, not inputs - can't see what the model searched.
- 10-04 late: REC_BETA_FEEDBACK_AFTER=3 LIVE on Railway (user ask; verified /rec/status beta_feedback_after=3).
  Page copy saying "5" not checked.
- Eval 39 cases: 37 clean (m01 Xano blip -> per-state search now fault-tolerant; o02 no-chips flake). Replay of
  31797 chat (evals/replay_conversation.py, 2 runs): "free" 5->0, peak caveat 6->4, card repeats unchanged,
  chip repeats unchanged (12 -> 12/11) -> prompting can't fix chips.
- Chip de-dupe built (rec_service/chips.py, used in /rec/refine; tops up from filter chips; 14 tests pass):
  offline on real+replay transcripts repeats 12/12/11 -> 1/1/1; tradeoff: later replies show ~2 chips not 4.
  ALL of this (multi-state, prompt, chips) UNCOMMITTED, awaiting user OK to push main.
- fac0bbb (10-05 02:23 UTC push): ep119 has NO guest-min / F&B-min filter, so search_venues over-fetches 16
  and drops KNOWN guest_minimum > guests passed, and known min_fb_min_peak_sat > half profile budget (0 =
  none/unknown, kept; ~18% of venues have a known F&B min). Prompt: no guest count -> ask. Replay: openings ask
  guests; after "6 people" no-minimum picks only. p50 latency 14.1s -> 16.1s.

## 2026-10-05: beta feedback #2 (user 31762, Forever, 1/5 "i need more breakdown of full costs")
- Chat 24: asked "breakdown howey mansion in a realistic way" -> got ONE sentence ("about $21,000-$28,000 ...
  open its PDF"), though the prior reply had every line item. Cause: tool doc "message: 1-3 short sentences" +
  80-140 word rule + "one job = open a PDF". Des's chat 20 shows the same thin "Open X PDF first" pattern.
- Vivek (Slack): Forever gets more detail up front, free stays light; wants the "forecast calculator"
  reintegrated (unknown what that is - ask). Kate questioned whether spelled-out PI is wanted.
- Fix UNCOMMITTED in agent.py: COST BREAKDOWN rule (FULL only): itemized lines, sources, arithmetic, up to ~220
  words, "• " bullets (page text 19ebe5b0 is ww-text pre-wrap: \n renders, markdown does NOT), 1 vendor_id ok.
  evals/replay_breakdown.py written, NOT run (awaiting OK, ~$0.10/run). Only 61 user msgs ever in table 43.
- DEPLOYED 10-05 13:13 UTC (user OK): commit 4670597 on main, Railway e3d42716 SUCCESS, /health 200. Found: LIVE
  replies had been 31-54 words for everyone (doc "1-3 short sentences" beat the 80-140 rule); now 160-200 words,
  no lists outside breakdowns, breakdown ~165-250 words w/ 7-9 "•" lines, cards for venues from earlier turns
  (prior_vendor_ids from stored history; chat 24's breakdown had NO card). Evals 5/5 clean, p50 19s (base 20s),
  $0.048. Gratuity handling varies run to run (in total vs separate). Real-user check of breakdowns pending.
- "Forecast calculator" = hidden 'Cost Research' WeWeb page: budget focus -> category projections scaled to total
  budget; exclude categories -> budget redistributed; overspend in one category raises total projection by a
  factor. User wants ideas for folding it into the assistant (not urgent). Screenshots of it NOT found
  (latest 3 were Anthropic cost, 31797 feedback, cohort dash).
- USER 10-05 (after 4670597): SHORT replies preferred; free -> PDFs; Forever gets itemized detail ONLY when they
  explicitly ask (cost at volume matters). Follow-up commit 931fe54 LIVE 10-05 13:57 UTC (Railway 7182b4a0 SUCCESS, /health 200; was blocked by the
  permission classifier until user said push): reply doc back to "1-3 short sentences" + BREAKDOWN_ASK regex in agent.py
  adds "BREAKDOWN REQUESTED" to context only on explicit asks. Evals 5/5 clean, normal 56-103 words, breakdown
  160-176 words. Next: check real Forever chats for reply length + breakdown triggers.
- 10-05: user says they published WeWeb recently and phase 2 pricing buttons, round 14 My Wedding fix and the
  "5" -> 3 copy are all fine (user's word, not checked against live /data json). Remaining Phase 2 step if not
  done: BNPL_PHASE2_FROM_MS on Railway.

## 2026-10-05: budget calculator - BUILT THEN REMOVED (user: "way overcomplicating it, won't make sense to anyone")
- Do NOT re-propose the Cost Research forecast calculator / budget planner / ripple factors in the assistant.
  Everything was deleted: commit 64881a9 reverted (0a247b8, rec_service identical to 931fe54), table 80
  budget_plan column dropped (schema identical to before), WeWeb section/vars/workflows deleted (panel matches
  pre-change), backups + plan file deleted. Leftover: Railway var REC_BUDGET_PLAN=0 (nothing reads it).
- Forever-only chat visuals (charts built by code) were part of the same plan and were NOT built; ask before
  reviving. Old Cost Research forecast tab is still broken for everyone (logged-out only + token-gated).

## 2026-10-07: "Show 3 more" + View Vendor new tab (beta feedback, Forever member 29710)
- Evidence: 9/76 beta questions asked for "more"; chat 33 asked 6x, drifted (expo center/zoo/fairground for a
  30-40 guest romantic CA search) + repeats. Every reply = 3 cards (76/107) by prompt rule "2-4 picks".
- Backend 63cf397 (DEPLOYED 10-07 03:23 UTC, user OK): agent.more_cards() = rest of THIS request's search
  results in order minus picks + all chat-shown ids, whole groups of 3 only (3 or 6, else []). No model call,
  no question spent. Stored in cards column flagged more=true; xano.get_messages splits -> cards unchanged for
  old clients, turns gain more_cards. Model history gets "[Also offered under Show more: ...]". Mixpanel
  rec_server_request gains more_cards count. tests/test_show_more.py (19/19 total).
- WeWeb (editor, subagent, UNPUBLISHED at writing): Show 3 more button per turn + rec_show_more_clicked event;
  View Vendor -> new tab ONLY if vendor page gating is safe on cold load (user condition: PDFs + PI stay gated).
- WeWeb DONE in editor 10-07 (subagent; UNPUBLISHED until user publishes): View Vendor 6b16f61b = native link
  targetBlank (params Vendor_ID, vendor_idx, from=planning), wf 71841bc1 tracks rec_card_clicked new_tab:true.
  Gating verified safe on cold load (PDF wf 5d3294ce reads auth user at click -> paywall 48be8a6c if not hydrated;
  PI CTA 527cea99 locked unless ep230 entitled). Show more: var recMoreShown 43ccec28 (by turn id), wrap d3c14425,
  grid 54a99adc (slice by revealed), button 916826ae (shown while more.length >= revealed+3), wf 5e3f1a20 tracks
  rec_show_more_clicked. Load Opening 685f59e1 / Load Chat f9d0474a / Refine 0fa17e11 keep more_cards.
  BACKUP GAP: those 3 workflows only have an ABRIDGED pre-change backup (rollback = delete `more_cards:` fragments).
- Same publish carries Membership no-flicker price texts (b2204160/1cd8c335/af5d09b0, files ny_price_*_noflicker.js).
- **SUPERSEDED same day: Show 3 more now SPENDS A QUESTION (user decision 10-07).** 42da6b7 DEPLOYED ~03:55 UTC
  (verified /rec/more 401 unauth = route live): POST /rec/more {chat_id} -> _gate (shared with refine) -> usage row
  kind=refine -> next 3 from latest answer's stored pool; 409 no_more checked BEFORE gate. Responses/GET chats send
  more_available count only (venues stay server-side). Pool: search fetches 16, model sees 8 (63cf397's 8-cap gave
  0 extras in 2 of 3 real replies - tests used fake data; check real replies before calling it done).
  WeWeb rework (button under last turn, calls /rec/more, refine-style error handling) sent to page subagent.
- WeWeb paid rework DONE in editor 10-07 (unpublished at writing): wrap d3c14425 = last assistant turn &&
  more_available>=3 && not loading (var 458eb1f5); button 916826ae -> wf 5e3f1a20 -> Rec Refine 0fa17e11 mode='more'
  (POST /rec/more, refine error handling; 409 hides button). Round-1 client reveal (grid 54a99adc, var 43ccec28) REMOVED.
  Backups rec_*_pre_showmore_paid_2026-10-07.json. After publish: verify a real reply has more_available and one click
  = one usage row (table 79).

## 2026-10-07 real-user read (14 users, 18 chats, 28 msgs, $2.40 total; excl staff)
50% never ask after the opening; 2 users = 17/28 msgs. Buckets: more/cheaper 32% (4/9 poor: repeats, Wedgewood chain x3,
$35k F&B vs $11k cap), change criteria 25% (good), style/vibe 21% (3/6 poor: 50-guest-min venue for 6, FL for New Orleans),
named venue 14%, breakdown 4%, packages 4%. Overall 57% good / 11% deflected / 32% poor. Opening fires twice ~2s apart for
4 users (page bug). Card price range disagrees with reason text (Village Big Sur $3,600 vs $30,000). Feedback 1,1,3,5.

## 2026-10-08: card price + starters (tulle_vendor_data_updates branch rec-card-price-starters 3f7f9c5, LOCAL)
Card venue_fee_range now = PI "Rental fee" line (same number the model quotes); table 11 flt range only as fallback.
Opening chips fixed: STARTER_CHIPS in chips.py. 32 tests pass; no eval run yet. WeWeb (editor, unpublished): opening
in-flight guard __ttRecOpening in Rec Load Opening 685f59e1 + i4c reuse; HAND-EDIT: error branch -> e0_release.
Backup weweb_backups/rec_opening_pre_dedupe_2026-10-08.json. 4-week access would add ~34 now (+~7/wk) to 403 Forever;
~12% of recent buyers open it -> ~4 users. 11 non-Forever users wrongly flagged forever_access_purchased.
- 10-08: 3f7f9c5 LIVE (Railway SUCCESS 02:15 UTC). 0183cb5 (REC_BETA_AUDIENCE=paid, plan on feedback, bare-date
  TypeError fix in _as_dt - auth/me sends date_until_access "YYYY-MM-DD", has_paid_access 500'd for non-Forever w/ a date)
  COMMITTED on main, PUSH BLOCKED by permission classifier -> user must push or allow; then set Railway
  REC_BETA_AUDIENCE=paid. WeWeb paid-plan gating + e0_release hand-edit sent to agent (editor only).
- 10-08 02:28 UTC: 0183cb5 PUSHED (user said switch to marthv; gh active account left as marthv) + Railway
  REC_BETA_AUDIENCE=paid set; deploy 08c3e4f2 QUEUED at time of writing - confirm SUCCESS. Free users still 402.
- 10-08: 0183cb5 LIVE 02:29:56 UTC (paid-plan beta). It read bare dates as END of day -> free users expired by the
  PDF-limit handler (writes TODAY to date_until_access) counted as members until midnight. Fixed 0d11b5c (face value,
  matches ep230), pushed 02:33, deploy 81ee278e BUILDING at writing. WeWeb gates (editor, unpublished) by agent:
  nav 024bb4f5/bd233ef1, pill 1ca86a32, beta gate 2b5b3f32, chat 9685b824, shelf dae25652, banner ef631269, onload
  ce0daac9 o1_wait_token -> paid rule; asked to switch `>= today` to `> today`. Backup
  weweb_backups/assistant_paid_beta_pre_2026-10-08.json. e0_release error branch STILL a hand-edit (MCP drops
  firstErrorAction on function workflows): Custom JS on error branch of Rec Load Opening.
- 10-08 ~02:50 UTC: user hand-added the error branch; VERIFIED via MCP read: Rec Load Opening firstErrorAction =
  af1fe4a6 (custom-js, clears __ttRecOpening.inFlight). Same action also on page onload ce0daac9 error branch (harmless).
  So the MCP CAN read firstErrorAction on functions - it just can't write it. Next: user publishes; verify live.
- 10-08 ~03:50 UTC PROD PUBLISH VERIFIED (live /data/dae212b5-d989-4a4b-8468-3f10081bf0ac.json, _wwcv=1309):
  __ttRecOpening guard + rec_opening_deduped + error release, "beta for Tulle members" copy, `slice(0, 10) > today`
  paid gates (assistant + Discovery 0d69d71b-834d-4f2f-a13a-6d6ac66905d5), more_available/Show more. Workflows live in
  /data/<pageId>.json, NOT main.js. Bash curl needs dangerouslyDisableSandbox (sandbox DNS blocks the site).
  Watch: first non-Forever #feedback post with plan; no new back-to-back chat pairs in table 42.

## 2026-10-08 preference questions (beta fb user 19303, 4 weeks, 2/5: "ask more questions to learn my preferences")
Server: branch rec-preferences 4c11f7a (LOCAL, not pushed). prefs.py 5 questions (style/setting/catering/ceremony/
priority), exact ep119 vocab; stored as rec_memory.notes kind="pref" (+ hidden pref_skipped); GET/POST
/rec/preferences, status.prefs_asked; model gets search_hint line. Note-delete index bug fixed. 42 tests.
WeWeb: preference card before first picks + "Added to what Tulle Assistant knows about you" line (prefs + notes_saved)
-> editor-only agent. Plan label on feedback CONFIRMED working (#feedback "user 19303, 4 weeks").
- 10-08 WeWeb prefs DONE in editor (unpublished): var recPrefs 0f2e7c16, fns Rec Prefs Load 7514a0e5 / Submit 5fd93665,
  card 83ed19b8, memory line 821cd656, links 13f40464/4ee3fa2c, onload ce0daac9 gates opening on prefs_asked (missing
  -> no card), notes_saved kept on turns (685f59e1, 0fa17e11). One chip row on opening: per-turn chips 2ca9f6e8 hidden
  while starter row 2c615764 shows; starter row = opening's server chips. Backup assistant_prefs_pre_2026-10-08.json.
  Gap: Rec Load Chat (reopened chats) doesn't show the memory line. Order: push rec-preferences -> deploy -> publish.
- 10-08 13:38 UTC: 4c11f7a LIVE (pushed main as marthv, gh back on vmarthi17; GET /rec/preferences 404 -> 401). Awaiting user WeWeb publish.
- 10-08 16:08 UTC: aaa863e (Show 3 more chips + never-empty chips) pushed to main; deploy 00e0a97b QUEUED at writing. 14998 chats cleared (16 chats/102 msgs, backup xano_backups/rec_chats_user14998_pre_clear_2026-10-08.json).
- 10-08 prefs v2: 062557b on branch rec-prefs-v2 (LOCAL): 14 venue types (+University/College, Zoo/Aquarium in VENUE_TYPES), soft 'feel' question, POST/GET carry context (= user_context). WeWeb stepper (1 question/step, +N more, final notes textarea) -> editor agent, backup assistant_prefs_v2_pre_2026-10-08.json. Push server BEFORE publishing the stepper.
- 10-08 16:24 UTC: 062557b pushed to main (deploy 95cb4530 QUEUED at writing; gh back on vmarthi17).
- 10-08 16:25 UTC: 062557b LIVE. WeWeb stepper DONE in editor (unpublished; backup assistant_prefs_v2_pre_2026-10-08.json): steps via recPrefs.step, +N more, notes step b5302a34 textarea e481d8e9. Hand-check: textarea initial value bound to recPrefs.context (MCP can't read initValue); maxlength not enforceable (trimmed on send); stray state notesFocus.
- 10-08 ~17:00 UTC: stepper fixes in editor (backup assistant_prefs_fix_pre_2026-10-08.json): +N more per-question (moreOpen = question id, pill 3a8ba99f no longer writes '__more__'), modal textarea ed301468 min-height 104px, notes list 8d077855 scroll max 240/200px, heading 2d2c2130 'What Tulle Assistant knows about you'. Owner's 'Save preferences on step 1' screenshot was mid-rebuild. Unpublished.

## 2026-10-08 late: OPEN TO FREE USERS (user decisions) - server code on branch, UNCOMMITTED, NOT LIVE
- ON HOLD (user, same night): "lets hold on this for now". Don't start WeWeb work or push; wait for the user to bring it back.
- User: "need more input into the decisions" -> all signed-in users: 3 questions, feedback once = +3, then paywall.
  Paid members unchanged (feedback after 3, then 40/day). Separate free cap $15/day (REC_FREE_DAILY_USD) so free
  traffic can't trip the $40 global cap and lock out payers. LAUNCH AFTER the 10-14 NY pricing call (avoid confound).
- tulle_vendor_data_updates branch rec-free-feedback-unlock (local, uncommitted): guards.free_limit /
  free_feedback_required, status blocked_free_cap (503), feedback_required 403 for free (+free_feedback_bonus),
  usage rows log tier free|paid, xano.spend_today() -> (total, free). 49 tests pass.
- Table 79 has NO `tier` column (checked 10-08): Xano drops the field, so free spend = counted_as_free rows only
  (misses each free user's 1 first opening -> cap undercounts). Adding the column = schema change, ask first.
- Go-live: push branch, Railway REC_FOREVER_ONLY=0 (REC_BETA_AUDIENCE stays paid), WeWeb: nav/pill/beta gate for all
  signed-in, feedback card on free 403 feedback_required (incl. openings), paywall on 402, 503 blocked_free_cap msg.

## 2026-10-11: "asked twice for feedback" = TWO TABS, not a server bug
User 32318 (rows 10+11 in table 81, 4/5 both): Railway http log shows page loads 03:17:23 and 03:28:13 UTC (both while
3 questions used -> feedback_required), feedback POST 03:28:58 from the newer tab, chatted, then POST 03:34:15 from the
OLDER tab whose card was still up. Mixpanel rec_feedback_prompt_shown x2, both source=load. Server never re-gated.
Possible cleanup (not built): re-check /rec/status on tab focus. Usage 10-08..10-11: 4-5 page viewers/day, ~2 openings/day.

## 2026-10-11 Photography/Entertainment in the assistant - readiness PASSED (with caveats), build started
- t36 (deduped): Photo 7,177 pkgs / 1,822 priced vendors; Ent 8,350 / 2,580. Price 97%/95%, Package_Type photo 99%, Service_Type ent 100%, hours photo 90% / ent 62%, inclusions 92%/75%. Unusable: overtime, add-ons, year, travel/guest fees. Median photo $4,000, ent $1,900.
- ep207 public works (Photo CA median $4,000, Wedding Day $4,990; Ent NY DJ $2,750, Band $9,500, Solo $900). ep119 handles P/E via package pre-pass (auth=user, live calls UNVERIFIED).
- Vocab: Service_Type DJ/Live Band/Ensemble/Solo Performer/Other; pianist/harpist/violinist = Solo + keyword; string quartet = Ensemble + keyword; photo booth = ent Offers; videographer = photo; officiant = no supply.
- RISK: ep119 returns EXACT P/E package prices to any signed-in user (no paid check) - possible live paywall leak on Discovery for non-venues, UNVERIFIED whether the UI shows them. Uncached P/E pre-pass not state-scoped (latency UNVERIFIED).
- Build: subagent on worktree ../tulle_rec_pe, branch rec-pe-categories off main, local commit only, no push/deploy.
- 10-11 P/E build: rec-pe-categories 8bbf263 (worktree ../tulle_rec_pe), 60/60 tests, P/E evals FULL 8/8 LIGHT 8/8; venue regression 12 cases clean FULL+LIGHT vs main (main o13 had no chips). Cost +13% FULL/+8% LIGHT per question, latency unchanged (p50 ~24s FULL). Watch: o13 itemized without breakdown request (1 sample); breakdown flow not firing on main OR branch (pre-existing); uncached ep119 photo ~12s. WeWeb rec card meta text + View Vendor link (P/E -> Other Vendors page d1d9bbf6) edited in editor (backup weweb_rec_card_pre_pe_2026-10-11.json). User will say when to push/deploy.
- 10-12 04:03 UTC: rec-pe-categories 8bbf263 pushed to main (user OK), Railway deploy 0c90dffc SUCCESS. E2E live (test acct, chat 55): "photographers in California around $5,000" -> 3 Photography cards (CineMotion $5,000 10h, Christine Chen from $4,000, EJD from $2,000), cites 968 CA packages / median $4,990; opening 32.5s, refine 20.4s. WeWeb card meta + View Vendor link edits in editor (need publish).
- 10-12 eve (user-approved deploy): 9d5e453 LIVE on rec-engine 21:44 UTC (built in worktree tulle_rec_fix off origin/main, so on-hold free-user branch NOT shipped). No-chat_id refine now reuses user's chat from last 30 min or creates one + persists turns (live smoke: chat 58 test acct); no-state rule (use state named in chat/notes, save_note it, else nationwide picks + ask once); glasshouse/conservatory/atrium -> Greenhouse, upscale -> Ballroom+Historic Architecture. 65 tests pass. NY Greenhouse = 24 venues (8 for 200+). (b)/(c) verified LOCALLY only. Other services in the Railway project rebuilt from same push (not checked). Watch new beta feedback ratings.

## 2026-10-13: user 1437 "Want accurate costs" (3/5, maybe) - read chats 60-62 (tables 42/43, feedback row 13 table 81)
- Rated 03:09:31 UTC 10-13 after only chip taps (Venue picks, cheaper options, "Is a venue I like realistic?") + 2x Show more; typed nothing before rating. Profile: 160 guests, $60k, saves in CT/PA.
- What they had seen: rental fee + a STATE per-head average, never an all-in total. CT F&B shown as BOTH ~$205 (chat 60, 62 card) and ~$146 (61, 62) a head. "Is a venue I like realistic?" chip opener returned 3 picks, not an answer. Cheaper options pulled GA/CA/Malta/MI/France. Asked "which state?" twice despite CT/PA saves. Farmington + Saint Clements shown as $0 / no rental (Saint Clements PDF note says $15k-27.5k F&B minimum).
- AFTER rating they stayed 15 min, typed real questions, and got good itemized totals (Wadsworth ~$39,900 / $249 a guest; Aldrich; Miraval off-peak) + asked "What would be a more reasonable budget".
- Read: the complaint = no all-in number + inconsistent numbers, NOT a request for an adjustable calculator. $205 vs $146 root cause NOT traced yet.
- ROOT CAUSE (corrects the "not traced" + my two wrong claims that session): $205 = CT cap_251_400 pp_fb median
  (Farmington seats 342), $146 = CT cap_151_250 median (Wadsworth/Bald Hill ~200). ep230 compares each venue with
  ITS OWN capacity band and labels both "Connecticut". The "realistic?" chip was NOT broken (chat title = first typed ask).
- FIX committed LOCAL ONLY 00744ee, branch rec-consistent-costs in worktree tulle_rec_fix (based on origin/main
  9d5e453 - check 9d5e453 is live before deploying, else it ships too): estimate.py (venue-page formula, matches
  ep230 all_in.at_default on 7 venues), estimate_cost tool (guests / off-peak what-ifs), benchmarks = couple's
  capacity band w/ scope, market sentences stripped, figure check bounces unsupported $ once. 81 tests pass.
  NOT RUN: evals/replay_1437.py (~$0.35/run) - awaiting user OK; then deploy OK. WeWeb card needs a binding for
  new estimate_label; venue_fee_range [null,null] rendering on the card UNVERIFIED.
- Data quirk seen: PI rental for Miraval (V2010 $2,895) and Aldrich (V84 $6,000) looks like the OFF-PEAK rate.
