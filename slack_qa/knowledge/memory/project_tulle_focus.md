---
name: Tulle Together - Current Project Focus
description: Active goals and context for tulletogether.app (WeWeb + Xano)
type: project
---

App is tulletogether.app, built on WeWeb + Xano.

## Active Focus (as of 2026-03-22)
Working on fixing Xano/WeWeb performance and data reliability. Pipeline app is NOT a priority right now.

## Key Problems to Solve
1. **Data loss / unreliable saves** — TOP PRIORITY
   - User profile names dropping off
   - Favorited vendors not saving
   - Packages not saving
2. **Long load times** — page switches trigger large data loads
   - Loading full vendor objects everywhere — should use `/wptp_map_light` for lists/maps
   - Need filter endpoints so WeWeb fetches only what each page needs
   - WeWeb collections all load on page change instead of on demand
   - Images being loaded in bulk instead of via Xano CDN URLs
3. **Payment workflow consolidation** — currently: Stripe → Make.com → Xano → WeWeb refresh
   - Eventually consolidate to just WeWeb + Xano (remove Make.com dependency)

## Plan
- First: audit Xano APIs for errors causing data loss
- Then: identify slow/heavy endpoints
- Then: add lightweight/filtered endpoints for WeWeb collections
- Later: consolidate payment workflow

**Why:** User feedback that UI/UX is slow and data feels unreliable. Business-critical trust issue.
**How to apply:** Always prioritize data integrity fixes over performance optimizations.
