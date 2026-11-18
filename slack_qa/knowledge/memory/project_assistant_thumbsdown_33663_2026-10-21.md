---
name: project_assistant_thumbsdown_33663_2026-10-21
description: "First real assistant thumbs-down (user 33663, chat 247, 10-21 02:35 UTC) - \"no venues near Flower Mound\" was FALSE (17 seat 175+ within 15 mi); root cause = no radius search"
metadata:
  node_type: memory
  type: project
  originSessionId: 3e51a800-0cac-4b55-9fac-eb4b17d2fe6f
  modified: 2026-10-21T04:33:56.657Z
---

User 33663 (4-week plan, $60, signed up ~20 min before chat; profile = Texas only, $15k, 175 guests; prefs in
rec_memory 80: Country Club/Barn/Hotel, high ceilings + light, BYO caterer -> "Raw Space", ceremony elsewhere).
Rated 1/5, "Not great suggestions".

Measured 10-21 via ep119 with REC_TEST_TOKEN (ep119 returns lat/lng): 503 TX venues seat 175+; **17 within 15 mi
of Flower Mound, 71 within 25** (Hotel Vin 5.7, Sparrow on Main 6.4, Denton CC 9.2, Coyote Ridge GC 9.2, Las Colinas
CC 14.0 - several match her style). Assistant said none in Lewisville/Denton, but keyword "Lewisville" alone returns 2,
"Denton" 4. Which filters it actually sent is UNKNOWN - service doesn't log tool inputs (only the card search's filters).
- Opening scattered Abilene/San Antonio/Plano because the profile has no city - never asked.
- Dropped the BYO-caterer (Raw Space) filter first: pricing_models Raw Space = only 8 TX 175+ venues, while Venue_Type
  says "Raw space" on many more (Las Colinas CC, Windsor at Hebron Park...) - facet/field mismatch, unverified.
- Raw-space estimates ($48-51k) apply market catering, so her chosen path looked the most expensive.
**10-21 BUILT, NOT DEPLOYED:** commit 54e7ae6 on branch rec-near-radius (worktree ../tulle_rec_near, off
origin/main b233d3c = live). Radius search (Open-Meteo geocode + table-11 bbox via metadata API) + STATE ONLY ask.
Replay evals/replay_247.py: Flower Mound -> 13.8-16 mi venues; opening asks city 3/3. Deploy = push to main (Railway
auto-deploys main); rollback = redeploy b233d3c. Full-suite test ordering failures pre-exist (15 on b233d3c).
**10-21 04:18 UTC PUSHED to main (owner OK'd)**; Railway deploys SUCCESS on 3 of 4 services at 04:20, live chat not smoke-tested.
**Raw Space delta (measured 10-21, ep119 TX 175+, 503 venues):** prefs.py maps "Bring our own caterer" -> "Raw Space" only,
which filters flt_venue_offerings = 8 venues. The classifier's own rule makes Raw Space = ZERO services (no tables/chairs),
while "Semi-Inclusive" = outside catering ALLOWED (and the default when the PDF is silent): 348. Legacy Venue_Type text says
"raw space" on 150 (133 of those are offerings=Semi). Fix 74c9eec PUSHED to main 10-21 04:35 UTC (owner OK, skipped the PDF spot-check); rollback = redeploy 54e7ae6.
Replay: all turns keep types+vibes+BYO (before: dropped styles or BYO). Prices still > $15k. 20-PDF spot-check of Semi not done.
Original proposal: radius search (city -> lat/lng, filter on ep119 lat/lng or ep121 bbox). [[project_assistant_usage_review_2026-10-19]]
