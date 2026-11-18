---
name: project_paywall_popup_redesign
description: "The Discovery/PDF-limit upsell popup (component 48be8a6c) redesigned to 'Take control of your budget' 2026-09-12 — editor-only, NOT published"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1c55a874-744a-4113-9ca4-188a29690ccd
  modified: 2026-09-12T16:22:00.223Z
---

2026-09-12. Replaced the purple two-card popup with **direction D, "Take control of your
budget."** Design + copy deck + token map: artifact
`https://claude.ai/code/artifact/53c6b15c-a60d-445e-b5c1-c266e7629a97`.

## ⚠️ EDITOR-ONLY — NOT PUBLISHED

All edits are in the WeWeb editor. Nothing is live until the app is published. If someone
publishes for an unrelated reason, this ships with it.

## The component is NOT what its name says

**`48be8a6c-fdf6-4d43-9609-c91896bb6784` is named "PDF Limit Reached"** and its root element
is named "UPDATED Upload Pricing Popup" — but it is **also the general Vendor Discovery
upsell**, which is where the screenshot came from. Editing it changes every surface that
opens it. User chose to edit in place rather than fork (2026-09-12).

Do not confuse it with the separate **Discount Popup `4df324b9`**, which shares the old
"You're making progress!" headline but offers a promo code (TULLE10) instead.

## Two bindings are CONTEXTUAL — never hardcode them

Both read `variables['603f27b8-faf2-4e9f-8acf-5723b3f442c0']` (the ep284 upgrade offer):

| Element | Non-eligible | Upgrade-eligible |
|---|---|---|
| Title `9b667e98` | "Take control of your budget." | "Make your access permanent" |
| CTA label `5ef7a2b2` | "Unlock full access" | "Upgrade your plan" |

I nearly hardcoded these. Checking first is what caught it — an existing paying customer
would otherwise have been told to "take control" of a budget they already track.

## The CTA element carries the workflow — RESTYLE, never delete

`136398bb` (was the purple card, now the green CTA) owns the **`Nav Pricing`** workflow:
`change-page -> bc8cb393` (/pricing, `?calculator=estimate`) + `close-all-popup` x2 +
`fetch-collection` + Mixpanel **`clicked_membership_for_pricing_pdf_limit_popup`**.
The MCP **cannot recreate workflows containing plugin actions**, so deleting that element
would destroy the navigation and the tracking permanently. Same for `5a3c8eef` (secondary),
which opens the Upload Pricing popup `f827810a`. **The CTA target was already /pricing** — no
workflow edit was needed.

## What changed

Kept both card elements and restyled them; hid the sub-parts with
`style.default.conditionalRendering = false` rather than deleting (reversible):
- Header `e9536838` → dark green gradient; title white Lora 22px
- Anchor table = **real elements** (`064ae6a8` box + 2 rows + 4 texts). $36,000 / $30.
  `f4f33cca` (the old body copy) demoted to just the source line.
- `136398bb` → full-width green `--g500` button, radius 12, **purple #C7A3F5 gone**
- `5a3c8eef` → plain underlined text link "or submit a pricing PDF for a free week"
- Badge row → trust strip (**50,000+ couples**, the canonical number)
- Hidden: both feature lists, both icons, the "Or" divider, and the **Close footer**
  `8a6ae59c` (the ✕ already dismisses)

## ⚠️ ww-text STRIPS inline `style` ATTRIBUTES — do not fake layout in HTML

First attempt put the anchor table in ONE ww-text as inline-styled HTML with
`content.default.sanitize = false`. **The div structure survived but every `style`
attribute was dropped**, so it rendered as two unstyled lines with the value jammed onto
the label ("...in 2026$36,000"). `<span style="color:...">` DOES survive elsewhere in this
app, so colour may pass while display/border/background do not — do not rely on it.
**Build layout as real elements.** `addElements` schema: JSONL, one element per line, you
supply each `uid` yourself, and `slots` is an ARRAY (`[{slotKey, elements:[uid,...]}]`),
with children referenced by uid on their own lines — nested objects are rejected.

Second gotcha: the CTA and the secondary link sat side by side because their parent
`4b7e5ea3` was still the old two-card flex ROW. Set `flexDirection: column`.

## VERIFIED IN THE EDITOR 2026-09-12

`conditionalRendering:false` **does hide elements** — confirmed in the editor: the bullet
lists, icons, "Or" divider and Close footer are all gone. Header gradient, white Lora
title, green CTA and trust strip all render correctly.

## STILL UNVERIFIED

The rebuilt anchor table and the stacked CTA/link have **not** been seen rendered yet — they
were fixed after the screenshot that exposed the problem. Re-check before publishing.
Also unverified: the upgrade-eligible branch of both bindings.

## Editing notes

- `editElement` rejects a partial binding edit: you must write the whole
  `content.default.text.en` object including `__wwtype: "js"`, not just `.code`.
- One `editElement` returned **502 Bad Gateway**; a straight retry worked.

## Still open on this popup

It still offers the free-PDF path to people who **already have access** — a known bug,
unfixed. See [[project_pdf_warning_view3]].

Backup of the pre-redesign tree:
`.claude/weweb_backups/component_48be8a6c_pdf_limit_reached_pre_redesign_2026-09-12.md`
(tree + uids only, not per-element styles — real rollback is WeWeb version history, or
simply not publishing).
