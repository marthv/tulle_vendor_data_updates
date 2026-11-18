---
name: partner-vendors-venue-page
description: "SHIPPED 2026-08-24 — venue_preferred_vendors (table 71) + fn60/fn61 + eps 266-269 resolve each named partner to a platform vendor; fn56 v14 reads the table, links carry BOTH ids, matched chips sort first. Two items still need the WeWeb editor"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6762263d-b5fd-4e4d-98e7-c2fe235941e3
  modified: 2026-08-25T02:02:55.393Z
---

Surfacing each venue's partner list on the venue detail page, and linking a partner to its own
platform page where one exists. **Xano side COMPLETE 2026-08-24. WeWeb card is live** (it was
built at some point and no note recorded it — do not "build" it again).

**Source of truth: table 36 `Extracted PDF Data` → `Preferred_Vendors`**, a single
**comma-separated** text blob of business names, joined by `VENDOR_ID`. Only ~24% of rows are
non-empty because the field shipped 2026-05-27; it is written **only** by the venue-PDF path
(`Vendor_Category`/`Package_Name` are `""` on every non-empty row). Prompt at
`extract_core.py:560-611`, writer at `:1733`.

Product decisions (user, locked): **names only, no category labels** · **free, not paywalled** ·
**matched chips DO link** (this reversed the earlier "display first, linking second" call, which
is why the link table had to exist first).

## What exists now

- **Table 71 `venue_preferred_vendors`** — one row per (venue, business). Structural twin of
  table 62: `pv_key = '{VENDOR_ID}|{partner_key}'` **unique** (add_or_edit matches one field
  only), `has_*` presence flags never `>0` tests, `computed_at` for GC. Indexes: unique
  `pv_key`; `(VENDOR_ID, sort_rank)` = the hot read; **`(matched_vendor_id, venue_name)` = the
  designed-for reverse view "venues that recommend you", no join because `venue_name` is
  denormed onto the row**; `partner_domain`; `partner_key`; `computed_at`.
- **fn 60 `utils/vendor_match_keys`** — THE master normalizer. `keyOf`/`isUrl`/`squash`/
  `splitList`/`polish`/`better` ported **verbatim** from fn56, plus a new `domainOf`
  (registrable domain, keeps the TLD, handles `co.uk`/`com.mx` via an SLD set).
- **fn 61 `utils/rebuild_venue_preferred_vendors`** — per vendor, idempotent, upserts on
  `pv_key`, sweeps orphans. **Exactly 5 DB round trips regardless of partner count** (max
  observed 105): table 36 paged, the venue's table-11 row, existing rows, then **one `in?`
  lookup for all domains and one for all name keys**.
- **Table 11 gained `mk_name_key` / `mk_domain` / `mk_keys_at`** + btrees `ffdebcb6`
  (mk_domain) and `701ff23c` (mk_name_key). `Website` is stored raw with query strings, so an
  equality match needed a denormed column — same trade `flt_states` made for ep119.
- **Table trigger 5** now maintains those three columns **and** `flt_states`, with the gate
  extended to all three. **Trigger 3** gained a third call to fn61 in **its own** `try_catch`.
- Endpoints (group 10, public, ep229 pattern): **266** `admin/backfill_vendor_match_keys`,
  **267** `admin/rebuild_preferred_vendors`, **268** `admin/preferred_vendors_stats`,
  **269** `admin/preferred_vendors_for_vendor`.
- **fn 56 v14** reads table 71 instead of parsing table 36. `partner_vendors` items are now
  `{key,name,is_url,is_link,href,vendor_id,vendor_idx,vendor_name}` + root
  `partner_vendors_linked_count`. **ep231 `cache_v` → 14.** Backups
  `fn56_v11_pre_prefvendor_table_` / `_v12_prefvendor_table_` / `_v13_linked_first_` /
  `_v14_vendoridx_`.
- **v13 ORDERS LINKED PARTNERS FIRST**, alphabetical within each group (a stable partition of
  table 71's `sort_rank`). Pure alphabetical made the links effectively invisible: only ~2.55%
  of entries link, the client renders 5 before expanding, and **measured on V382 all five links
  sat at ranks 9/29/54/61/78 of 90 — not one was reachable without pressing "Show all".**
  Done in fn56, not in `sort_rank`, so reverting is a `cache_v` bump not a rebuild. Verified
  live: V382's five links now occupy positions 0-4, and headline / `more_label` are unchanged.

## Numbers, measured on the full backfill

Table 11 is **14,681 rows** (older notes saying 13,374 are stale). 44,719 partner rows across
**3,897 venues**. Matched **1,140 = 2.55%** — 866 at confidence 60 (same state), 267 at 40
(cross-state), **7 by domain**.

**THE DOMAIN PATH WAS A BAD BET AND THE REASON IS STRUCTURAL: only 194 of 44,719 entries
(0.43%) carry a usable domain at all.** Venues write business NAMES, not URLs. The `mk_domain`
column + index bought 7 matches. It still earns its keep as the substrate for the
"new vendor joins → re-match existing venues" heal path, but do not expect matches from it.
**Do not re-litigate this by adding more URL parsing — the input has no URLs.**

**>20% matched would be a FALSE-POSITIVE ALARM, not success.** The pool caps it: only ~2,594
of 14,681 table-11 rows are non-venues, while partner lists are caterers/florists/bakeries.

## The trust rule, and where it lives

Confidence tiers: 100 unique domain · 80 ambiguous domain · 60 name+same-state · 40 name
cross-state · 0 none. **Ambiguous NAME matches are rejected outright (93 of them), never
ranked into an answer** — a wrong link sends a paying couple to the wrong vendor's page.

**Confidence 40 is where the false positives concentrate, specifically cross-state matches onto
other VENUES** — chain and common names: "JW Marriott", "Wine Country Inn", "Stonehouse". Same
state would have disambiguated them. Cross-state SERVICE vendors (a photographer at a
destination wedding) are legitimate and stay linked.

**So `is_link` excludes `confidence <= 40 && matched_category contains "venue"`, and that rule
lives in fn 56, NOT in the table.** Deliberate: the table records what matched, the panel
decides what is safe to link, so tightening the rule is a `cache_v` bump instead of a
14.7k-venue rebuild.

## Verification that must be repeated after any change here

- **V14516** (The Aviator) → exactly **8** partners. **V1730** → 29 raw rows collapse to **one**
  row, `partner_key='undercovertents'`, name `Undercover Tents` (the human name beats the bare
  URL), `source_row_count` 26, `partner_domain='undercover-tents.com'`.
- **The domain is taken from ANY rendering, not the winning one** — `better()` prefers a name
  over a domain, so the winner usually has no domain. V1730 is the proof.
- **Parity gate before ever changing fn56's partner block: 60 venues, table vs live ep231, same
  keys/names/order.** It passed clean; it is the only thing standing between an edit and every
  partner section silently vanishing.
- **`VISIBLE = 5` in fn56 must equal the chips the client renders.** Boundary verified live:
  5 → `more_label` empty, 6 → "Show all 6".
- Re-running a backfill page must report `unchanged`, 0 written. That single check also proves
  trigger 5's inline normalizer copy agrees with fn60 — if they drifted, the trigger would
  overwrite the backfill's values and the re-run would show writes.

**Trigger 5 carries an INLINE COPY of keyOf/domainOf** (a `function.run` hop would run on every
vendor pageview). Drift between it and fn60 is silent — match rate just goes to zero. The
idempotence check above is the guard.

## Still open — THREE editor-only items, the MCP cannot do any of them

(The third, the `vendor_idx` binding, is described in the 2026-08-24 late section below.)
See [[reference_weweb_mcp_limits]]. 1) The card lives in the **sticky Sidebar**, which the spec
warned against; `editElement` refuses `slots`, so re-parenting into Main Content is a manual
drag, and its mobile `order` (currently 2, colliding with the PI card) should be set after.
2) The expander is one-way and its workflow has **no `id`**, so it cannot be edited or deleted
via MCP.

**PUBLISHED 2026-08-24.** The chip changes ARE live: matched chips navigate (both ids), linked
chips render green/pointer, and the `vendor_idx` binding was corrected before the publish
(verified live on `Select Vendor Location` `fc7b324f-…`, and only ONE click workflow remains).
**Partner clicks now consume a metered vendor view**, same as a vendor card — deliberate.

STILL LIVE AND UNFIXED, all cosmetic/structural rather than broken:
- the card is in the **sticky Sidebar**, so a long expanded list can outgrow the viewport and
  kill the stick (median 2 partners, p90 29, max 105 — a tail case);
- the expander is **one-way**: once expanded there is no collapse, the button just hides;
- the card's mobile `order` is **2, the same as the PI card** — it renders correctly only
  because equal orders fall back to DOM order.

Unrelated live bug, still live: the Submit PDF `Container` `b597947d-…` has **no mobile
order**, so it defaults to 0 and renders above everything on mobile.

Related: [[project_pricing_intelligence_spaces]], [[project_extracted_pdf_data_dedupe]],
[[reference_extraction_pipeline]], [[reference_xano_mcp_quirks]].

## 2026-08-24 late — `vendor_idx` IS LOAD-BEARING FOR NAVIGATION (user caught this)

**THIS IS AN APP-WIDE RULE, NOT A ONE-OFF — [[project_search_typeahead_dropdown]] already
recorded it ("suggestions must carry BOTH ids or the vendor page breaks") and it was missed
here anyway. ANY feature that navigates to a vendor page must carry BOTH ids.**

A partner link needs **both** ids. The venue page's `Set Vendor View Page` workflow sets its
Vendor Index from the **`vendor_idx` query param**, and collection `Get Vendor Data`
`bd7af501-…` fetches **`/wptp_updated_mappings/{id}`** keyed on that variable. So a link
carrying only the text `Vendor_ID` lands on a page that **fetches nothing**. Vendor Card 1
always passed both; I under-copied it and shipped one.

Fixed properly: table 71 gained **`matched_vendor_idx`** (int, 0 when unmatched), fn 61 stores
it at all three acceptance sites, full rebuild re-run (identical totals — 44,719 / 1,140 /
3,897, so idempotent), **fn 56 v14** emits `vendor_idx` per item, **ep231 `cache_v` → 14**.

**Stored, NOT derived by slicing the leading "V" off `Vendor_ID`.** That pattern holds on every
row checked (id 573 ↔ V573), but one row breaking it would navigate a paying couple to the
wrong vendor — the same trust-failure class as a bad match.

**`is_link` now also requires `vendor_idx > 0`**, so a match without a usable index renders as
plain text rather than a dead link. That also made the rebuild window safe: links simply did
not appear until each venue was reprocessed.

**WeWeb: there are now TWO click workflows' worth of history on the chip — keep only ONE.**
The user built `Select Vendor Location` `fc7b324f-…` in the editor (mirrors Vendor Card 1:
sets selection/index/id/name, fetches collections, `is_link` gate, navigate, then
`update_vendor_views` + the 500-view Slack alert + mixpanel). Mine, `Partner Vendor Open`
`3f0c9a71-…`, was **deleted**. Decision: partner clicks **do** count against metered vendor
views, same as a vendor card.
**Its `vendor_idx` query is still bound to `context.item.data?.['id']`, which does not exist on
a partner item — it must be `['vendor_idx']`.** That workflow contains plugin actions so
`editFrontendWorkflow` cannot touch it: hand edit only. See [[reference_weweb_mcp_limits]].

## 2026-08-24 final — WORKING IN PRODUCTION

Partner chips navigate correctly on the live site. The last bug was **not** data: ep231 was
serving `vendor_idx` fine and the queries were right. The `change-page` action's `parameters`
key was `Vendor_ID`, which matches nothing, so the route param resolved to the literal string
`undefined` (`/vendors/undefined?Vendor_ID=V573&vendor_idx=573`) and no navigation happened.
**The key is `param`.** Full write-up in [[reference_weweb_mcp_limits]].

Two things that made this slow, worth remembering: it **worked in the WeWeb editor** the whole
time (the editor re-evaluates far more eagerly than the published router), and a failed
`change-page` **does not halt the workflow** - the actions after it kept firing, so every dead
click still consumed a metered vendor view and logged `viewed_vendor_page`. The network trace
showing `update_vendor_views` firing is what proved the workflow was running and narrowed it to
the navigation itself.

## 2026-08-25 — REVIEW FEEDBACK FROM DES, and what is and is not actionable

Three points, in Slack, after using the shipped chips:

1. **"I think you'd want a way to easily go back to the main vendor you were just looking at.
   Kind of jarring to only be able to go back to main wptp."** Correct and unfixed. The
   venue page's `Back Button` **`f607fc3d-b324-4ce3-9649-07e5ac3c3bd1`** is hardcoded -
   label "Back to Transparency Project", click workflow navigates to page
   `0d69d71b-…` (Vendor Discovery) - so a partner hop always dead-ends at search.
   **The obvious capture point does NOT work: `Select Vendor Location` `fc7b324f-…`
   contains plugin actions, so `editFrontendWorkflow` cannot touch it.** The route that
   avoids it entirely is a **page-load workflow on the venue page holding two slots -
   `prevVendor = lastVendor; lastVendor = currentVendor`, in that order** - which needs no
   edit to any plugin workflow, and then a conditional label + branch on the Back Button.
   Not built; it changes navigation semantics app-wide, so it wants a decision first.

2. **"Some way of knowing what categories these other listed vendors are. Boc Wed has 90
   recommended vendors so its a little overwhelming when you expand."** Boc Wed is **V382**,
   the 90-partner venue. **THE CATEGORY DATA MOSTLY DOES NOT EXIST AND CANNOT BE DERIVED.**
   Table 36 `Preferred_Vendors` is a bare comma-separated list of business NAMES; only the
   **2.55% that resolve to a platform vendor** carry a real `matched_category`.
   **Measured 2026-08-25 over 90 sampled venues / 377 unique partner names: a keyword
   classifier over the name labels only 61.8%, and 19 names matched more than one
   category.** The 38% it cannot touch are personal and abstract brand names - *Alisa Tongg,
   Konditor Meister, Rosina DiBello, Corn & Rye, Bentley Meeker, Lokal, D24k*. Labelling 6
   in 10 and leaving a dash on the rest reads worse than labelling none, and inventing a
   category for a named third-party business is the same trust-failure class as a bad link.
   **What IS honest and cheap: label the ~2.5% matched chips from their real table-11
   Category, and make 90 scannable rather than labelled** - a filter box, plus fixing the
   one-way expander (below) so it can be collapsed again.

3. **"the pricing intelligence is totally a venue's even though its tagged photo"** - a real
   and much wider bug, now FIXED. It was never a partner-vendor bug; it hit every non-venue
   vendor page. Full write-up in [[project_pi_panel_nonvenue_bug]].

Des's point 2 also **reinforces the still-open two-way expander** at the top of MEMORY.md:
with 90 entries and no way to collapse, expanding is a one-way trip.
