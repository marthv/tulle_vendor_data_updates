---
name: project_search_typeahead_dropdown
description: "Vendor Discovery type-ahead dropdown — ep258 + 3 WeWeb variables SHIPPED, the UI and workflows are NOT built. START HERE before touching either search box."
metadata:
  node_type: memory
  type: project
---

Goal: make the two Vendor Discovery search boxes behave like the vendor portal's
`VenueSearch.tsx` — type, see matching venues in a dropdown, click one to open it.

## What already exists (built 2026-08-18, ~02:20 EDT, never noted until now)

**ep258 `venue/suggest_index`** (API group 10, `api:GynP5T1B`, public, cached 24h).
Verified live 2026-08-18: 4,000 rows, warm response 0.45s, `has_more:true` on page 1.
Deliberately NOT a per-keystroke endpoint — WeWeb pulls the whole category once and
filters in the browser, so typing costs no network and ep119 is left alone.
Row shape is a compact ARRAY, not an object:
`[0] id (=vendor_idx) · [1] vendor_id ("V425") · [2] name · [3] state · [4] locality · [5] quality`

**BOTH ids are load-bearing** — the Vendor Discovery card navigates with `Vendor_ID` AND
`vendor_idx`, so a suggestion carrying only one lands on a broken page.

**Three page variables** on Vendor Discovery (`0d69d71b-834d-4f2f-a13a-6d6ac66905d5`):
`suggestIndex` `7bcf7ce1-e2d7-4faa-a12f-9bc1964337f1` ·
`suggestResults` `057d9238-0d43-4aa3-b7e1-26c9cfd83642` (top 3) ·
`suggestOpen` `40a9fad8-f860-4f2c-99dc-67e9131f91b3`

## Built 2026-08-18 (this session) - all read back and confirmed, not just "success" strings

| Piece | id |
|---|---|
| `Load Suggest Index` function workflow | `b1f0c2d3-7a41-4e2b-9c55-1d0e5a6f7b21` |
| Page onload trigger that calls it | `c2e1d4f5-8b52-4f3c-ad66-2e1f6b7a8c32` |
| Desktop ranking (appended to the existing change workflow) | `9a214515-313b-4b5b-9c4f-ae1b4593e61d` |
| Mobile ranking (new change workflow) | `d3a2e5b6-9c63-4a4d-be77-3f2a7c8b9d43` |
| Desktop dropdown / row | `ec3aaa99-2c79-4ad4-90ee-c4d37eff20e0` / `77408b13-5ee0-49c3-8fd2-88ee4f2a82cd` |
| Mobile dropdown / row | `0b45039b-d655-4e8f-b860-560b8013372f` / `7ece0231-c442-48b7-8ced-a70480534305` |
| Click - the full card workflow, pasted by hand on BOTH rows | desktop `158701a9-f311-497c-ab36-cf1e100c4205` · mobile `1c30ce1c-1374-45b1-8037-581c7865c6e7`. The two navigation-only workflows originally added by MCP were both DELETED - see the field-name section. |

Ranking: quiet under 2 chars, name-startsWith > name-includes > locality-startsWith, ties
broken by `quality` then alphabetically. Top 3 only. Dropdown is absolutely positioned and
`conditionalRendering`-bound to `suggestOpen`; the repeat is bound to `suggestResults`.

**The loader is a plain `fetch` in a `custom-js` action, NOT a Xano plugin action.** ep258 is
public so no auth is involved, and this keeps the loader MCP-editable instead of becoming
hand-maintenance forever. Same reasoning would apply to any other public endpoint.

The mobile ranking workflow deliberately does NOT write the shared search term
`ce4c0aa9-…` - mobile commits its search on Enter (its onEnterKey workflow sets it), and
writing it on every keystroke would silently turn mobile into live-filtering.

## THE SUGGESTION OBJECT MUST USE THE CARD'S FIELD NAMES

Fixed 2026-08-18 after a real failure: clicking a suggestion opened a BLANK vendor page.

Cause: the ranking workflows first emitted `{id, vendor_id, name, state, locality}`, but the
click workflow is **copied verbatim from the vendor card**, and the card reads
`context.item.data?.Vendor_ID` and `?.['Name']`. Those keys did not exist on a suggestion, so
navigation ran with `Vendor_ID = undefined`. `vendor_idx`/`id` was the one field that matched
by luck, which is why it rendered a page at all instead of 404ing.

**The suggestion object is now `{id, Vendor_ID, Name, State, locality}`** - deliberately the
card's names. Rename any of them and every copied card workflow silently breaks the same way.
The row text bindings read `['Name']`, `['locality']` and `['State']` and must be changed in
lockstep. The intent is written into the ranking code comments as well.

General rule: **when a workflow is going to be copied between two repeats, the two repeats'
item objects must share field names.** Shape the data to the workflow, not the workflow to the
data - the workflow is the thing that gets duplicated by hand and drifts.

Also: two click workflows on one row means two competing `change-page` actions. The
navigation-only workflow originally added on the desktop row was DELETED once the full card
workflow was pasted in; only `158701a9-f311-497c-ab36-cf1e100c4205` remains there.

## Still NOT built - needs the editor by hand

- ~~ep150 increment + fetchUser on suggestion click~~ **DONE 2026-08-18** - the whole card
  workflow was pasted onto both rows by hand and verified action-by-action. `editFrontendWorkflow`
  REJECTS these plugin action types, so any future change to them is hand-work too.
  **NOTE: the pasted workflow embeds the hardcoded Slack webhook** (500-view alert) - it now
  exists in three places, which matters for the pending webhook rotation in
  [[project_preauth_401_fix]].
- **Category change must re-run the loader.** The index is per-category; the loader was made
  a callable function precisely so this is a one-action add to the category change workflow.
- ~~Escape does not close the new dropdown~~ **FIXED 2026-08-18** — new page workflow
  `Close Suggestions On Escape` `a7d9e0b1-4c26-4f8a-9e31-6d5b8c2f1a70` (keydown → if
  `event?.['key']==='Escape'` → `suggestOpen = false`).
  **The old `Hide Results On Escape` `76c681f5-…` was deliberately NOT repointed.** It targets
  `5928a018-117a-4b18-99d8-a9821ae8d670`, a dead leftover, and
  `searchFrontendAppPageFunctionWorkflows` returns it TWICE under one id in two conflicting
  shapes — once as `if` + legacy `variable`, once as a single `custom-js`. Editing it risks
  clobbering whichever the editor treats as real, so a second workflow on the same trigger was
  added instead (a page can hold several workflows per trigger).
- **Still no blur/click-outside handler** — the dropdown stays open until Escape, a pick, or
  the query dropping under 2 characters.

## What was NOT built as of the start of this session


No loader workflow, no ranking workflow, no dropdown UI, no click behaviour. Both
`ww-div`s named **"Search and dropdown"** contain ONLY an icon + the input — the name is
aspirational, the results list was never added.

- Desktop input `d00e176b-eb40-45aa-9293-7c17607fa5ef` "Search Input Map/Grid", in
  `b2e328b8-2fa5-42fd-a1f9-a45dc81fc86a`. Already `debounce:true / 150ms`.
  Workflows: `a6f9969d` onEnterKey (full search) + `9a214515` change ("Update Search Term",
  sets search term var `ce4c0aa9-9148-46f6-b5ee-9e0df2d043ef`).
- Mobile input `40eab76a-0401-4d70-87f9-e8f54ec8a7cd` "Search Input", in
  `520b8093-2511-42a6-8c9f-30abe7fe928c`. **onEnterKey only — no change workflow.**

## The click must replicate "Select Vendor Location"

That is the card-click workflow on Vendor Card 1 `bcde5f71-e65c-4230-a401-b119bc31a860`.
Live chain (most early actions are `disabled:true` but `next` still traverses):
`change-page` → pageId `e49b4e74-…` with queries `Vendor_ID` and `vendor_idx`
→ if 500-views → **ep150** `PATCH /user/update_vendor_views` at `api:WGW_G49d`, body
`{id, vendor_id, vendor_name, total_vendor_views+1}` → `fetchUser` → mixpanel
`viewed_vendor_page`. This Discovery card DOES send `vendor_id`, so it is one of the four
good ones in the [[project_popularity_recency_sorts]] coverage gap.

## MCP reachability — what has to be done by hand

`editFrontendWorkflow` rejects plugin/legacy action types (`cd33cf33-…-request`,
`f5856798-…-fetchUser`, `fetch-collections`, `variable`) and replaces the whole action map.
So:
- The `9a214515` change workflow is **editable** — it holds only `update-variable`.
- The `a6f9969d` onEnterKey workflow is **not** (`fetch-collections`, `variable`).
- The ep150 + fetchUser actions on the suggestion click must be added **by hand**.

**Loader does NOT need a plugin action or a collection**: ep258 is public, so a plain
`custom-js` `fetch()` loop over pages works and sidesteps both the plugin-action limit and
[[reference_weweb_mcp_limits]]'s "cannot edit collections".

ep150 is `auth: user`, so **logged-out suggestion clicks will not increment** — same
limitation card clicks already have, not a regression.

Related: [[project_popularity_recency_sorts]], [[reference_weweb_mcp_limits]],
[[project_vendor_portal]].

**2026-08-24: the same BOTH-ids trap bit the partner-vendor chips** — a link carrying only
the text `Vendor_ID` lands on a vendor page that fetches nothing, because collection
`Get Vendor Data` fetches `/wptp_updated_mappings/{id}` keyed on the **`vendor_idx`** query
param. Fixed by storing the int row id. See [[project_partner_vendors]].
