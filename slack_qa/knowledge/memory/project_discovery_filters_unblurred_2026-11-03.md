---
name: project_discovery_filters_unblurred_2026-11-03
description: "11-03 owner: unblurred the locked advanced filters (venue/photo/entertainment/florals) on Vendor Discovery - 32 sections filter:none IN EDITOR, still locked (upsell on click); needs owner PUBLISH"
metadata:
  type: project
---
Owner 2026-11-03: "unblur those advanced filters on venue, photography, entertainment, florals" (screenshot of locked ADVANCED FILTERS
panel). Read as visual only: filters stay LOCKED for non-Forever (paywall card, lock icons, click -> 48be8a6c upsell, pointer rules
unchanged). Vendor Discovery 0d69d71b: 32 sections (desktop sidebar + mobile filter popup, inside "<Cat> Advanced Filters - Blur")
style.default.customCss.filter blur(3px) -> none; read-back verified. Panels' backdropFilter blur(10px) left alone. IN EDITOR, owner must
PUBLISH. Rollback file .claude/xano_backups/weweb_discovery_filter_blur_pre_unblur_2026-11-03.json (set filter back per uid).
**Why:** owner wants free users to see what the filters offer. **How to apply:** if asked to UNLOCK (free use), that is an entitlement
change - confirm first; Forever perk per [[project_hub_pricing_readout_2026-10-21]].
