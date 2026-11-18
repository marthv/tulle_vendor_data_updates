---
name: feedback_view_plans_lavender_in_panel
description: "The \"View Plans / Add Time\" button inside the green PDF panel on vendor pages must stay lavender, not green"
metadata:
  node_type: memory
  type: feedback
  originSessionId: a9483c7d-0298-48d3-b53d-715962768463
  modified: 2026-10-16T03:38:51.777Z
---

Keep the in-panel "View Plans / Add Time" buttons lavender (bg var(--9db63432…,#C7A3F5), text #3A1F73): venue page 6a24f714, other-vendors page 74c474ca. The rest of the 10-14 purple->green CTA restyle stays green.

**Why:** 2026-10-15 the user saw it "blending into background" after the 10-14 purple->green restyle made it the same #00A368 as the green "Pricing Data Submitted by Couples" panel. This overrides CLAUDE.md's "no purple" rule for this button.

**How to apply:** Any future CTA recolour must check what surface the button sits on. Never set a button to the panel's own green. Backup: .claude/xano_backups/weweb_view_plans_in_panel_pre_lavender_2026-10-15.json. Related: [[project_paywall_popup_redesign]]
