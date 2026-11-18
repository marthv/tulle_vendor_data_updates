---
name: feedback_popups_in_front
description: "Owner rule (2026-11-11) - any popup is ALWAYS in front of floating UI (beacons, docks, pills); floating UI never sits over onboarding/filters/paywall popups"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 34930563-5246-4565-b223-1786355f5857
  modified: 2026-11-11T07:16:27.251Z
---

Any popup (onboarding, Filters, paywall, any WeWeb popup) must always be in front of floating buttons/beacons/docks. "General rule of thumb."

**Why:** the Ask Tulle pill sat on top of the onboarding popup and then the Filters popup (11-11); owner flagged it twice.

**How to apply:** floating elements need a z-index below WeWeb popups (popups sit above the nav at 100, so floating UI <= 99) AND, when the element is moved under <body> or has its own stacking context, an explicit check that hides it while a popup backdrop is up (ww-planner-chat 11-11: fixed element >= 90% of the screen at z >= 100 under the centre, tour tt-* excluded). Test with onboarding + Filters open before calling it done. Learned 11-11: WeWeb popups do NOT reliably sit above z 100 (the Filters popup didn't), and the popup store is not exposed to components - db0b8d1 detects any tinted/blurred full-screen layer in a fixed stack that doesn't contain the nav, and keeps the closed beacon at z-index 5. [[project_ask_tulle_bubble_2026-11-11]]
