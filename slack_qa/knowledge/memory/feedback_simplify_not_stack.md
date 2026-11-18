---
name: simplify-not-stack
description: "User's standing directive: stop stacking client-side patches on the same root cause - remove the cause instead, and trim what is unnecessary"
metadata:
  type: feedback
---

**Prefer removing a cause over adding another workaround. Actively trim.** Stated
2026-09-16: *"we need to simplify and trim away at all the unnecessary things. i worry that
we're stacking a lot of crap onto more crap."*

**Why:** measured that day in the LIVE bundle, SIX separate client-side workarounds already
exist for ONE root cause (WeWeb cannot reliably supply the auth token at page load, and
endpoints require it): `auth-early` cookie restore, `Open Channel` token wait, `Wait for the
login token` (upgrade offer), an entire second workflow `Re-request the offer if it 401'd`
that exists only because the first guard fails, `Fetch Access Tier` + backoff retries, and
`still racing - try again`. A 7th was about to be added for ep119.

**How to apply:** before proposing a patch, count the existing patches for the same root
cause. If there are already several, say so and propose removing the cause. Say the net
change in moving parts (e.g. "-1 pending patch, -2 future patches"), not just the benefit.
Also flag things safe to DELETE, not only things to add. Do not patronize or flatter -
he has asked for this directly. See [[project_ep119_401_race]].
