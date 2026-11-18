---
name: project_open_product_scope
description: 2026-10-07 scope for "open product, sign-in only on actions" - page/endpoint inventory, blockers, phases; nothing built
metadata:
  type: project
---
Inventory 10-07 (read-only). Three gate layers: WeWeb router page access (MCP can't read/edit - UNVERIFIED today),
Signed-Out Guard app wf 5f0c2a71 (9 pages), page onload checks (Discovery e5656811 `__SIGNEDOUT_CHECK__`).
- Vendor pages: ep41/231/149/142/93 already answer without a token -> cheapest to open (M). Similar (ep119), ep107 user-only.
- Discovery: grid = ep119 auth=user (owner chose auth for anti-scraping; ran public 09-16..09-29). Biggest decision.
  ep121 map = never public. ep119 cache.auth=false so warmer keys survive making it public.
- Membership: plan cards come from ep82/ep284 (auth=user) -> empty signed out. My Wedding: keep private.
- PRE-REQ: ep142 (full table-36 rows) + ep93 (PDF_Link) leak paid data WITHOUT a token today -> lock before public pages.
- No return-to-intent anywhere: sign-up/Google/login all hard-route to Discovery; Sign In page logs out on load.
- Mixpanel: identify only, no alias; ID-merge mode UNVERIFIED. ~24 auto-fetch collections will 401 when signed out (noise).
Why it matters: [[project_signup_dropoff_2026-10-07]] (78% of /sign-up landers leave without typing).

## 2026-10-08 USER REVISION (supersedes the vendor-pages-first order above)
PUBLIC (as separate public COPIES; originals untouched): Vendor Discovery, Pricing/Membership, My Wedding, Tulle Assistant,
Cost Research. PRIVATE: Venue + Other Vendor pages (signed-out can browse Discovery + basic filters, cannot open a vendor).
Assistant ~fully locked signed-out. After sign-in = today's experience (light free usage, then paywalls). Need: a
comprehensive list of login-forcing actions (agent inventory -> scratchpad public_pages_inventory.md).
Parachute idea: undo = point signed-out traffic back to Sign In instead of the public copies.
TIMING (user 2026-10-08): planning only, NOTHING built. Build AFTER the pricing experiment (NY decision ~10-14) and the
Tulle Assistant beta settle. Inventory lives in session scratchpad (temp) - copy into a doc/repo before it's lost.
Inventory DONE 10-08: full copy at tulletogether/.claude/open_product_inventory_2026-10-08.md (local, not committed).
Data blockers: ep119 search, ep121 map, GET /donation_payments (plan prices), GET /number_of_guests +
budget_breakdown_current_budget_breakdown(_filter) (Cost Research). Checkout links need user.email + client_reference_id
-> a signed-out buy can't be credited -> checkout MUST gate. Advanced filters HIDE (not gate) when signed out (forever
flag compare). Possible LIVE leak, UNVERIFIED: ep196 GET /venue_calculator_entries_v1 auth=false, `==?` filter ignores
empty user_email -> may return every couple's entries today.
