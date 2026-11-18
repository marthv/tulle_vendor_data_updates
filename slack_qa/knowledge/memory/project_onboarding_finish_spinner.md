---
name: project_onboarding_finish_spinner
description: Onboarding Popup Finish button spins forever on ANY error - its error branch sets onboarding_loader TRUE (named "loader off"); found 2026-10-03, needs hand edit
metadata:
  type: project
---

Found 2026-10-03 from the user's own Google sign-in onboarding (screens 15:30:38-15:31:10 UTC, spinner stuck).
Popup `Onboarding Popup` bbdfe17d, Finish button `77dea4e0-4ffd-4e9d-a1ed-ba22621a8111`. Live definition lives in
`/data/d0f4f382-9c28-4cf6-9ea9-894269b291d9.json` (Google Sign-In Return page), NOT the Discovery page json.

Workflow: loader on -> PATCH update_user_attributes -> fetchUser -> selectedState -> 2 fetch-collections -> map
lat/lng (getByIndex on collection 37874812) -> loader off -> close popup -> mixpanel. **ON ERROR action is named
"loader off" but sets `onboarding_loader` (387a63e6) to TRUE** -> any failing step = permanent spinner, popup never
closes. Server side was fine: the PATCH returned 200 in 0.04s at 15:30:00. Which step threw: UNKNOWN.

Also: `mixpanel.track("finished_onboarding"), {...}` - props object is outside the call, so no properties are sent.

**How to apply:** fix is one value (error branch -> false, ideally also close popup); MCP can't edit popup element
workflows, so it's a hand edit + publish. Verify by grepping the live json above for the error action's varValue.
