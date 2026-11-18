---
name: pi-panel-nonvenue-bug
description: FIXED 2026-08-25 — the Pricing Intelligence panel invented a whole venue out of state medians on photographer/band/florist pages. fn56 v15 + ep231 cache_v 15 gate on Category; the WeWeb card now binds conditional rendering to the new is_venue key
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-26T03:44:25.101Z
  originSessionId: bee362a9-3340-4a1d-8dfd-92ba65d8f986
---

Reported by **Des in Slack on 2026-08-25**, following a partner-vendor chip off a venue page:
"i went to a linked photographer and the pricing intelligence is totally a venue's even
though its tagged photo". It was not a partner-vendor bug. It hit **every non-venue vendor
page** and predates the partner feature.

## What it actually rendered

`/vendors/V14273` is *Peach Plum Pear Photography*, correctly `Category: "Photography"` in
table 11. The panel showed:

```
"A peak-season Saturday dinner in this venue, all in"   $12,000-$20,000
Food and drink   - "Comparable Pennsylvania venues charge about $170 a head"
Ceremony on site - "around $1,250 if you marry on site"
Rental fee       - "around $3,500 to book the room"
Service charge   - "assume around 20% on top"
```

**Every figure was a Pennsylvania VENUE median attributed to a named photography business.**
`field_count: 0`, `contract_count: 0`, `source_pdf_ids: []` - the business disclosed nothing.

## Root cause, in two parts

1. **fn54 writes an empty `__WHOLE__` space row (table 62) for any vendor a table-36 row
   touches**, non-venues included. V14273's row had every price field 0.
2. **fn56 could not tell that empty shell apart from a venue that had disclosed nothing**, so
   it took the `!rows.length` guard as passed and filled all four components from
   `estPp/estCer/estSvc/estVf` - the state venue medians.

## Scale, measured before the fix

- **3,518 of 14,681 vendors are non-venues** (11,161 are `Venue`).
- Sampled 56 of them against live ep231: **18 (~32%) returned a rendered panel.**
- Category vocabulary: Venue 11161, [name] 1998, Entertainment 1144, Flowers 162,
  Food and Beverage 124, Beauty 73, Wedding Planner 9, plus singletons.
  **`Both` is NOT a venue - it means photo+video** (V2860 Susan Shek, V3131). The test is
  `Category` contains `venue`, case-insensitive; equality would be wrong the moment a
  compound value appears (`Entertainment, Photography` already exists).

## The fix

- **fn 56 v15** gained one indexed read of `WPTP Updated Mappings` on `Vendor_ID` (btree
  `636c0705`) for the single field `Category`, and a third early-return: non-venues get
  `is_venue:false, found:false, spaces:[], space:null, cta:null`. **The partner list still
  ships on that path** - it is a property of the vendor, not of pricing.
  `cta` is null deliberately: 'Submit a PDF' would be inviting a couple to upload venue
  pricing for a photographer.
- Gated in **fn56, not fn54** - the same trade `is_link` makes. The table records what was
  extracted, the panel decides what is safe to render, so tightening it is a `cache_v` bump
  instead of a rebuild of tables 62 **and 63** (63 derives from 62).
- **ep231 `cache_v` -> 15.** ep230, the paid twin, has no cache so it needed no bump; it
  picks up `is_venue` automatically because both endpoints call fn56.
- Backup of the previous body: `.claude/xano_backups/fn56_v14_pre_isvenue_2026-08-25.xs`.

Verified live after publish: V14273 / V13483 / V2860 -> `is_venue:false`, no space, no
numbers. **Venue regressions all clean: V1 ok, V382 = 90 partners / 5 linked / "Show all 90",
V14516 = 8 partners** - the same checks [[project_partner_vendors]] requires.

## The WeWeb half, and the trap in it

The PI card `073b9a22-…` on the Venue Vendor Page **already had a conditional rendering -
but only on the `mobile` breakpoint, and it tested the WRONG variable**:
`variables['c8cc3b96-…'] == "Venue"` is **`ChosenCategory`, the SEARCH FILTER**, not the
vendor's own category (default `"Venue"`). So it was absent on desktop and meaningless on
mobile - Des had filtered Venue, so it passed.

Both breakpoints now bind to the payload instead:
`collections['0ef36c86-790a-42a6-8820-42454161e7e8']?.data?.['is_venue'] === true`
(`0ef36c86-…` = `Pricing_Intelligence`, the public collection, `isAutoFetch: true`).

**Gated on the PAYLOAD, not on `vendorSelection.Category`, on purpose.** vendorSelection is
set by several different click workflows and is not guaranteed to carry `Category` on every
navigation path - gating on it would hide the panel on real venue pages. The public
collection is always fetched and always correct for the vendor being viewed. `=== true`
rather than `!== false` so the card stays hidden while the collection is still loading
instead of flashing a venue panel.

**NEEDS A WEWEB PUBLISH** - the Xano half is live, the card gate is not until then.

Related: [[project_partner_vendors]], [[project_pricing_intelligence_spaces]],
[[reference_weweb_mcp_limits]], [[project_pi_panel_infographic]].


## 2026-09-06 — PUBLISHED
User confirmed this is published and live in production. Do not re-raise it as pending.
