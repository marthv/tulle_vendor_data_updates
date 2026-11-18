---
name: hub-mapping-shipped
description: "Geographic hub mapping SHIPPED+VERIFIED 2026-07-20 — tables 54/55, user.hub_id/hub_key, trigger 4, fn33 resolve_hub, fn35 sync_brevo_hub, Brevo HUB attr, 14.7k users backfilled"
metadata: 
  node_type: memory
  type: project
  originSessionId: bea6f86c-5fae-4b7c-b192-1c78e9f7c89a
  modified: 2026-08-12T02:57:13.789Z
---

Hub mapping (wedding-location → geographic hub → Brevo HUB attribute) shipped & verified 2026-07-20, workspace 1.

**What exists:**
- Table 54 `hubs` (18 rows, ids 1=ny…18=international, priority higher-wins), table 55 `hub_state_map` (25 full-name states incl. Connecticut→new_england; "Washington DC" is the DC spelling in live data). Tables are the human-editable source of truth; runtime uses baked-in copies.
- `user.hub_id` (int nullable, FK 54, btree-indexed) + `user.hub_key` (text nullable); exposed via auth/me output (added 2026-07-20).
- fn 33 `utils/resolve_hub` — api.lambda with baked-in map. Precedence (user's explicit choice): **International wins over US states**; else highest priority; "Not Sure"/blank/unmapped → nulls.
- Table trigger 4 `hub_sync_on_user_location_write` (user insert+update): guard 1 exits unless Wedding_Location_Updated changed (also kills recursion), guard 2 write-gates on same hub_id, then db.edit + try/catch fn 35.
- fn 35 `integrations/sync_brevo_hub` — single-contact HUB upsert (display_name; '' clears).
- fn 13 extended with HUB attribute (db.query hubs once per batch + per-user lambda lookup). Backup: `.claude/xano_backups/fn13_sync_users_to_brevo_pre_hub_2026-07-20.xs`.
- Brevo `HUB` text attribute created via API.

**Backfill result:** 20,917 users scanned, 14,713 hubbed (70.3%), 6,204 null. by_hub: ny 3288, international 2673, florida 2287, texas 2249, chicago 1238, new_jersey 669, norcal 589, new_england 566, philly 383, dmv 277, atlanta 102, north_carolina 86, michigan 74, colorado 68, pacnw 52, ohio 48, arizona 38, minneapolis 26. All 20,914 email-holding users pushed to Brevo (import processIds 132–143), spot-verified (HUB "New York"/"International" on real contacts).

**Key facts:** `Wedding_Location_Updated` is a text[] of FULL state names (not comma string). Only live writer is ep 109 `user/update_user_attributes`, but the trigger catches any writer. Editing hubs/states = edit table 54/55 AND the baked maps in fn 33 + (if ids change) fn 13.

**Phase 2 Layer A SHIPPED 2026-07-20 (evening):**
- Table 57 `hub_stats` (18 rows, unique hub_id): vendor_count, venue_count, vendor_count_by_category json, top_vendor_id/name/clicks, denormed hub_key+display_name. Counts use the product's live rule (Validated_Data == 1) with exact state-TOKEN matching (not ep-119's substring — avoids "Washington DC"→PacNW leak); multi-state vendors count in every hub; International = Country set and not US-spelled. NY: 1166 vendors/589 venues, top = Brooklyn Botanic Garden.
- fn 44 `utils/recompute_hub_stats` (table-driven, lambda, upserts). **2026-07-21: nightly task is now TASK 13 `refresh_hub_stats_v2`** (04:30 UTC, verified 52s run) — task 12 NEVER worked (scheduled tasks silently abort on fn44's unpaged 10.5k-row query, see [[reference-xano-mcp-quirks]] §11) and is deleted/pending deletion. Task 13 INLINES fn44's logic with the vendor query paged 12×1000 — **keep task 13 and fn44 in sync when logic changes** (fn44 = manual-run copy; has dummy `noop` input, keep it). Raise task 13's page loop if vendor count nears 12k.
- Brevo attrs HUB_VENDOR_COUNT/HUB_VENUE_COUNT (float) + HUB_TOP_VENUE (text); fn 13 + fn 35 now read hub_stats and send them (fn 35 derives from user.hub_id; its hub_display_name input is deprecated/ignored, trigger 4 unchanged). Full ~21k push done. fn13 backup: fn13_sync_users_to_brevo_pre_hubstats_2026-07-20.xs.
- Product endpoint **200 `GET hub_stats`** (group 4 Weweb, public, cache 1h): `?hub_id=` → stats row, null when 0/unmapped. WeWeb binds user.hub_id from auth/me.

**Phase 2 Layer B SHIPPED+VERIFIED 2026-07-20 (night):**
- user columns: `last_active_at`, `last_viewed_vendor_id`, `last_viewed_vendor_name`, `last_viewed_at` (all nullable timestamps/text; validators re-verified intact).
- ep 3 auth/me: daily-gated last_active_at touch (gate is an **api.lambda** — timestamp_add_* filters don't exist in var expressions, see [[xano-mcp-quirks]] §9; caused a ~5min auth/me 500 outage 23:19–23:24 UTC before fix) + 4 new fields in output.
- ep 150 update_vendor_views: optional `vendor_id`/`vendor_name` inputs → writes last_viewed_* + last_active_at; backward compatible. **WeWeb TODO (user): bind vendor_id/vendor_name in the vendor-open workflow** — until then only last_active_at populates.
- fn 13: LAST_ACTIVE / LAST_VIEWED_VENDOR / LAST_VIEWED_AT attrs (dates Y-m-d, nulls ignored). Backup: fn13_sync_users_to_brevo_pre_activity_2026-07-20.xs. Brevo attrs created by user in UI.
- task 3 filter: added `last_active_at >= now-2d` term (dormant-recapture gap closed); cohort now ~5,564. No backfill (fields populate organically).

**Hub display names (2026-08-11):** the label couples see in email copy is single-sourced from `hubs`.display_name → denormed into `hub_stats`.display_name (task 13, 04:30 UTC) → Brevo `HUB` attr (task 3, 05:00 UTC) → `{{contact.HUB}}` in templates. **No Brevo template hardcodes a hub name** (verified: 0 literal hits, 37 `contact.HUB*` placeholders), and `user` stores only hub_id/hub_key — so renaming a hub is a one-field edit in table 54 (mirror it into 57 for same-day effect). hub_key stays internal, don't rename it.
- Hub 9 renamed **"Nor Cal" → "California"** on 2026-08-11 (Des asked in Slack; hub_state_map maps hub 9 to exactly one state = California, so "Nor Cal" was factually wrong, not just an abbreviation).
- Still-abbreviated display names that read badly in email copy: **DMV, PacNW, Philly** ("886 venues in PacNW"). Flagged to user, not changed.
- Meta REST (`/api:meta/workspace/1/table/{t}/content/{id}`) **PATCH silently no-ops** — returns nothing, no error, row unchanged. Use **PUT with the full record** (returns 200 + the row). Same class of trap as [[reference_xano_mcp_quirks]].

**2026-08-24 — TASK 13 HAD BEEN PAGING TABLE 11 WITH NO `sort`, AND IT IS FIXED.** Its
`for (12) { db.query "WPTP Updated Mappings" ... paging: {page: $pg, per_page: 1000} }` loop
had no sort clause, so it walked **heap order**. Heap order is reshuffled by ALTER TABLE, so
pages could silently skip and repeat vendors both within a run and between runs — quietly
under- or over-counting every hub, which then flows hub_stats → task 3 → Brevo `HUB_*` attrs →
email copy. Same class of bug as the one fixed in `recompute_vendor_filter_aggregates` v6.
Found by an audit run before adding columns to table 11; fixed by adding
`sort = {WPTP_Updated_Mappings.id: "asc"}`. **THE SORT IS LOAD-BEARING, do not remove it**, and
if fn44 is ever paged it needs the same. Backup:
`.claude/xano_backups/task13_refresh_hub_stats_pre_sortfix_2026-08-24.xs`.
I did NOT try to quantify how far past hub counts drifted.

Still unsorted and paged, same one-line fix, NOT done (admin backfills, self-converging because
of their `image_N == null` predicate, so the symptom is "processes fewer rows than you think"
rather than corruption): **eps 132 / 133 / 134 `update_vendor_image_one|two|three`**,
`paging: {page: 1, per_page: 500}`.
