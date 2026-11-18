---
name: project_interactive_demo_idea
description: Interactive demo BUILT + LIVE (user 10-16, demo.tulletogether.com) but ON HOLD - no promotion/links until the product opens up; was parked idea 10-15
metadata:
  type: project
---

User (2026-10-15): "instead of the free tier, lets build a really compelling interactive demo. Lets allow the user to
build their own demo really quickly, and we'll prove value quickly and get them to sign up" - modelled on
go.gethealthie.com/feel-healthie (could not fetch it: session DNS fails; ask user for a screenshot when un-parked).
TABLED - not scoped or built.

**Why:** the sign-up wall loses ~30 people/day (est., 10-15: 76% of recorded /sign-up visitors sign up within 1d,
measured via $mp_session_record because NO sign-up page-view event exists; 10-07 replays: 78% of leavers never typed,
gone in ~6s). Demo = value-before-wall without giving away the PDF corpus (the moat).
**How to apply:** when un-parked, likely shape = 3-4 taps (city, guests, budget, vibe) -> instant aggregate result
(ranges / what your budget buys, maybe 1 blurred real vendor) -> sign up to see real vendors + 1 free PDF. Keep raw
PDFs and vendor-level prices behind sign-up. Don't launch before the ~10-28 pricing read (confound). Add a
signup_page_viewed event first so the before/after is measurable. Related: [[project_signup_dropoff_2026-10-07]]
[[project_open_product_scope]] [[project_experimentation_framework]]

**10-15 user direction:** build as plain HTML, host on its own page (subdomain, e.g. demo.tulletogether.app - .app DNS
is Cloudflare; .com is WordPress.com DNS; Railway or Cloudflare Pages), track users/activity, final action = drive to
sign-up on www. Same Mixpanel project 3963130 (shared cookie across *.tulletogether.app so demo -> sign-up joins).
Data = PRE-BAKED aggregate JSON (ranges by city/guests), no live endpoint to scrape. Don't copy another org's demo
files/data (IP) - rebuild the pattern.

**10-15 BUILD STARTED (user: "build for our own product now"; decided: REAL venue names + prices only as ranges;
build now, NO traffic until after ~10-28).** Agent building at C:\Users\vivek\Documents\GitHub\tulle_demo (local git,
no push). Gate -> www.tulletogether.app/sign-up?src=demo&state&guests&budget&gate&utm_source=demo (no lead form).
signup_page_viewed: editor wf a7d3f2c9 "MP - signup_page_viewed" on Sign Up page 4a9de110 (props src, from_path,
vendor_id, utm_*, device, signed_in); UNPUBLISHED. Backup weweb_signup_onload_wfs_pre_signup_page_viewed_2026-10-15.json.
FOUND: /sign-up onload "Check if Authenticated" bcee9287 LOGS OUT signed-in visitors (like the old "/" bug) - not
fixed, raise with user.
- 10-15: /sign-up logout FIXED in editor (user OK'd): bcee9287 now s0_check (same as "/" fix, key tt_signup_signedin_redirect_at,
  skips ?google_error=terms) -> signed in = Vendor Discovery. UNPUBLISHED. Backup weweb_signup_check_auth_bcee9287_pre_signedin_redirect_2026-10-15.json.
  Verify after publish: grep live /data/4a9de110-...json for tt_signup_signedin_redirect_at; signed-in /sign-up -> Discovery.
- 10-15 DEMO BUILT: C:\Users\vivek\Documents\GitHub\tulle_demo (local commit 6b39ac6, no remote). 8 states x 40 venues,
  data/demo_data.json 126KB (rounded prices, ranges +/-12%), scripts/build_data.py (read-only Xano), check.cjs, screenshots/.
  Prices = midpoint of min/max per venue; tax = ESTIMATED state rate (not in our data); budget split from table 19.
  Events demo_* (13) + super prop demo_session_id; local = console only. Demo->app Mixpanel identity likely NOT joined
  (app uses localStorage) -> attribute via signup_page_viewed{src:demo}. Sign-up page ignores state/guests/budget params.
  Not deployed; launch after 10-28. Publish 10-15 VERIFIED live: signup_page_viewed, /sign-up redirect, NPS age 1,
  fallback links NY, green CTAs on venue page.
- 10-15 v2 ca74432 PUSHED to PRIVATE github.com/marthv/tulle-demo (main). 35 states (>=25 priced) + Intl (8 countries + "More
  destinations"), per-region lazy JSON, Leaflet 1.9.4 + OSM tiles (light-use policy - pick keyed provider before big launch),
  6-step tour, checklist/budget/assistant/shortlist REMOVED. Cloudflare Pages (user sets up): Connect to Git, build
  `node scripts/make_dist.cjs`, output `dist`, branch main, custom domain demo.tulletogether.app, Access policy until launch.
  User asked: should product match demo? My rec: port winning pieces one at a time after demo measured (cards price chip +
  median badge; venue all-in breakdown + hidden-fee check; gate style); match handoff screens (sign-up + Discovery) at launch.
  User to send product screenshots for handoff matching.
- 10-15 v3 d52161b PUSHED: restyled to match live product (34 product screenshots; spec tulle_demo/docs/product_style_spec.md),
  + wrangler.jsonc (assets ./dist) + package.json (npm run build). Cloudflare: user made a WORKER (not Pages) in account
  Vivek.marthi@gmail; tulletogether.app zone is NOT in that account -> custom domain/Access on demo.tulletogether.app need
  the other CF account. Interim: Access app on tulle-demo.tulletogether.workers.dev + enable workers.dev.
- 10-15 LIVE at demo.tulletogether.app (Worker in the CF account that owns tulletogether.app; Access login verified via curl 302,
  workers.dev off). User: "we want everyone and their mother to access this page" -> told them to delete/Bypass the Access app.
  Still: noindex kept + NO links from app until ~10-28; OSM tiles -> Mapbox key if traffic grows. User said demo "doesn't look
  like the app" -> agent doing option A (slim banner, real counts, pagination/sort/assistant bar, descriptions, gallery,
  breakdown below PDF box w/ guest stepper). Pushes to main auto-deploy LIVE.
- 10-15 USER DECISION (supersedes "real names + ranges"): ALL demo pricing FAKE + clearly labelled. Fictional venues generated
  from REAL state aggregates, stock photos (no real venue photos), "Sample venue"/"Illustrative" labels, real aggregate counts
  kept as the hook. ONE mock PDF viewer (HTML, whole doc readable, SAMPLE watermark) instead of a real vendor PDF.
  Reason I gave: fake prices next to real venue names misrepresent real businesses. Keep Access lock ON until fake version deploys
  (d52161b live behind lock still has real names). PDFs live in Xano table 10 "WPTP PDFs" (Vendor_ID, Show_PDF).
- 10-15 ~03:00: user said "don't fall asleep" -> I set lid-close action to DO NOTHING (AC + DC) via powercfg on scheme Dell
  49ef8fc0 (previous value unreadable/hidden; Windows default = 1 Sleep) + 4h SetThreadExecutionState keep-awake. RESTORE:
  powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1; same /setdcvalueindex ... 1; powercfg /setactive SCHEME_CURRENT.
  Laptop was on BATTERY.
- 10-15 ~09:25: 7c814e6 PUSHED (fictional venues 702 across regions, 44 local stock photos, mock 3-page PDF viewer w/ SAMPLE
  watermark, "Sample venue"/"Illustrative" labels). VERIFIED locally: 0 of 1,597 old real names, 0 external/real image URLs,
  npm run build checks pass. Deploy behind Access UNVERIFIED (can't pass login). Nit: "View Vendor" button clipped at 1366.
  Agent stalled after push (watchdog). Keep-awake job ended; lid action still DO NOTHING (restore = LIDACTION 1).
- **10-16 USER: "the interactive demo is live and updated: demo.tulletogether.com. we're going to hold on that, not very useful now until we start to open that up."** Status = built + live, ON HOLD (not a parked idea any more). Don't push traffic, links or launch work until the user opens up the product ([[project_open_product_scope]]). Earlier notes say demo.tulletogether.app - the user named .com; confirm which before linking.
