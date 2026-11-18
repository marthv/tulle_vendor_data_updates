---
name: project_auth_page_speed
description: "2026-09-28 sign-in/sign-up speed measured in real Chrome - phone floor is WeWeb runtime CPU, not Xano or data calls; fix shipped as a CSS splash in head code (user pastes)"
metadata:
  node_type: memory
  type: project
  originSessionId: 06c27a2a-b960-40ba-a903-9be6c3720ccd
  modified: 2026-09-28T02:48:20.472Z
---

Measured 2026-09-28 (puppeteer-core + local Chrome, scripts in session scratchpad `perf/`; phone = CPU 4x +
~1 MB/s, 150ms latency, 390px viewport):
- Form visible: desktop cold 1.2-1.4s / warm 0.5-1.0s; phone warm 2-3.5s, phone cold 4G ~7s.
- Xano auth/login median ~0.1s (40 real logins); Discovery data after login ~0.35s cached.
- Phone cold visit = BLANK WHITE SCREEN for ~7s (WeWeb ships empty `<div id="app">`, no prerender).
- CPU profile (phone, warm): 2.7s total, ~2.0s is WeWeb runtime (main-*.js, vue helper, parse);
  Stripe 97ms, mapbox 54ms, page chunk 45ms, mixpanel 24ms. The 8 anonymous Xano calls on Sign Up
  (incl. ep119 Onboarding_Recommendation page_size 3, fetched because the Onboarding popup is on the
  page) do NOT gate the form. RETRACTED my earlier estimate that removing them saves 0.5-1.5s.
**Fix:** pure-CSS `#app:empty` splash (cream bg, "Tulle Together", green spinner) -
`.claude/weweb_head_splash.html`; user pastes into WeWeb Custom code > Head. Tested by HTML injection:
splash at 1.2s, disappears by itself when app mounts (verified `:empty` false after). Perceived only -
real time-to-form unchanged. Verify after publish: `curl -sL www.tulletogether.app | grep "#app:empty"`.
User PASTED the splash into WeWeb Head 2026-09-28 (unpublished).
**Mixpanel can't see load abandonment:** track_pageview:false and `viewed_page` never fires on
/sign-up or / (3 users in 7d), and nothing fires before WeWeb boots. Built
`.claude/weweb_head_load_timing.html` (goes BELOW mixpanel.init): app_shell_loaded / app_ready{load_ms} /
left_before_app_ready{waited_ms, sendBeacon}. Tested by injection with Mixpanel calls intercepted: full load
-> app_ready load_ms=7317 (phone profile); leaving at ~3s -> left_before_app_ready. Not yet pasted.
**Staging check 2026-09-28:** splash WORKS on staging (form 6.7s, splash cleared). Timing snippet sent NO
events - user pasted it ABOVE mixpanel.init (early-returns); told user to move it to the bottom of Head.
**WeWeb pre-rendering: REMOVED by WeWeb Nov 2024** (staff, community thread 14384); no SSR as of 2026.
Don't re-research.
**STAGING VERIFIED 2026-09-28 after user moved snippet below init:** splash clears at mount; full load ->
app_shell_loaded + app_ready load_ms=7302; leave at 3s -> left_before_app_ready waited_ms=4472 (Mixpanel
calls intercepted in test). Only the prod publish remains. Only options: splash (done), lightweight auth page outside WeWeb, or a proxy/prerender
in front of the domain (complex; snapshot still not interactive until app boots).
Real speedup would need a lighter entry page outside WeWeb (not proposed/decided).
[[project_login_speed]] [[project_google_signin]]

## 2026-10-14 TO FIX (user): two near-identical "signing you in" loading screens on login
User sees two sequential loading screens on login that look almost the same but not exactly - wants ONE clean screen.
Likely cause (UNVERIFIED): screen 1 = our head-code CSS `#app:empty` splash (.claude/weweb_head_splash.html), screen 2 = a
WeWeb-built "Signing you in" loader/element (Sign In page / Signed-Out Guard / auth return page) shown after mount.
Fix direction: make them pixel-identical (same bg, logo text, spinner, copy, position) so the hand-off is invisible, or
have the WeWeb loader reuse the splash markup/CSS; check Google sign-in return page too. Not started.
- 10-14 DIAGNOSED (subagent, live read): only on GOOGLE sign-in return /google-redirects. Screen A = head-code html.tt-gsi card
  (#app:empty pseudo-elements, Georgia 20px, 26px track spinner, 376px card); Screen B = WeWeb page "Google Sign-In Return"
  d0f4f382 (card 361f6354 420px, loader 7f125bf6 40px circle, title f9c311ce Lora 22px, sub 7ba6a322 Sora) while onload
  "Complete Google Sign-In" runs. Email login has no full-screen loader. FIX DRAFTED (head code, user pastes + publishes):
  .claude/weweb_head_splash_gsi_one_screen.html - real overlay on <html> kept until path leaves /google-redirects (15s cap).
  UNVERIFIED: test real Google return + cancelled (?error=access_denied) on staging.
