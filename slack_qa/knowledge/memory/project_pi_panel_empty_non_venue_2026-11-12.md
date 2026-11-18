---
name: project_pi_panel_empty_non_venue_2026-11-12
description: PI panel showed an empty "Locked" card for non-venue vendors (ep231 found:false/is_venue:false) reached via related-vendor cards; PRE-EXISTING; card 31d086ae now hidden once PI answers found:false (editor); PI collections auto-fetch still ON (owner must turn off)
metadata:
  type: project
---
2026-11-12 owner saw "Pricing Intelligence [Locked]" + empty body ("Edit HTML in settings" placeholder) on /vendors/V16219 (Georgia, NOT a venue) after a related-vendor click. Cause: locked-body ww-html binding returns '' when `p.is_venue === false` / not found, but card 31d086ae had conditionalRendering true -> empty frame. Pre-existing (identical binding in live page data; today's edits don't touch it). Related-vendor cards c24e8f85 send every vendor type to the venue page e49b4e74.
FIX (editor, unpublished): card 31d086ae conditionalRendering = hide once the PI payload answered found === false; visible while loading. Backup .claude/weweb_pi_panel_pre_locked_fix_2026-11-12.json. Verify: V16219 -> no PI card; V2631 / V185 -> PI card as before.
OWNER CLICK NEEDED: collections Pricing_Intelligence 0ef36c86 + Pricing_Intelligence_Full 2ff85018 have AUTO-FETCH ON (page note from 09-16 says OFF) -> empty-vendor_id calls interleave with real ones (seen 05:59-06:07 UTC) and can blank the panel for a real venue. Turn auto-fetch off (onload workflow fetches explicitly). V16515 returned null body twice from ep231 - check.
- 11-12 owner: auto-fetch NOT turned off for either yet (MCP cannot edit collections - owner click only). Still open.
