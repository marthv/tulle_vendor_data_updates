---
name: project_pi_guest_min_and_capacity_filter
description: "2026-09-23 diagnosis — PI panel ignores guest_min (Christina refund, AMNH), current PI WeWeb uids, and the 4 guest-capacity-filter defects on Vendor Discovery"
metadata:
  node_type: memory
  type: project
  originSessionId: f6b3a600-b10a-4933-976b-8c9142c30e73
  modified: 2026-09-24T03:16:32.235Z
---

**PI "inaccurate" complaint = Christina (user 30207, li.christina33@gmail.com), #feedback 2026-09-20,
refund request, "$60k for a venue closer to $120k".** Guest count 0 → panel default 100. Last viewed
V122 AMNH (fav V28). **V122 Milstein Hall has `guest_min` 200 but the panel priced 100 guests**:
paid total $87,644 @100 vs ~$146k @200. Exact screen she saw is UNVERIFIED (neither venue = $60k @100).
- **fn56 `allIn(g)` and the WeWeb paid recompute both ignore `guest_min`** — it is only echoed as text.
  1,836 priced table-62 spaces at 1,032 vendors have guest_min > 100.
- V122 `service_pct` 8.875 = NY sales tax mis-extracted as service charge. 595 spaces have tax-like
  service_pct (6/4/7/8.875) — many may be real admin fees; NOT proven.
- Table 62 is 33,169 rows / 13,196 vendors (includes non-venues), not the ~3k older notes imply.

**Venue Vendor Page PI uids CHANGED — the ones in [[project_pi_panel_infographic]] are stale for this page**
(mirror page d1d9bbf6 still matches). Current: Stepper `b2b5d522`, Minus `1429297b`, Plus `6269d1b0`,
Count Label `e9c912b2`, Headline Value Row `932f2bde` (entitled-only), Headline Amount `4ac060e3`,
Per Guest `fb36481c`, Component Value `ffe34ed1`, Band Block `83a695ed`, Band Track `d1049362`
(3 EQUAL thirds, no numbers), Band Labels `6e19cb4d` (static Budget/Average/Luxury), Sensitivity
Note `bb00423d`, Header Title `783a87ab`. Component Strip is GONE. Guest clamp
`Math.max(25, Math.min(400, …))` is copy-pasted in 6 places; +/- fall back `|| 50`, label `|| 100`.
No tooltip pattern exists anywhere on the page.

**Guest capacity filter (Vendor Discovery 0d69d71b), all 4 dropdowns write `min_capacity` ac799270,
sent as ep119 `max_capacity` (= seats AT LEAST N — correct):**
1. 189 of 624 venues (30%) have Max_Capacity_Seated 0 and vanish under ANY capacity filter. Only 8
   recoverable from table 62 — backfill won't fix it; treat 0 as unknown in ep119.
2. Chip X `a3e92ce6` resets the dropdown's element value, NOT ac799270 → chip vanishes, filter stays.
3. Unblur rail `4de1cb15` is Forever-only; 1w/4w payers get the dead Blur rail `31959483`.
4. Mobile popup (component ed90717a) may carry a `width >= 600` rule → hidden on phones. INCONCLUSIVE.

## SHIPPED 2026-09-24 (Xano, all byte-verified by xano_push)
- **fn56 v19** guest floor + **ep231 cache_v 17->18**. Verified: V122 opens at 200 guests, paid $145,893
  (was $87,644 @100); free $109k-$175k. 13 sample venues: every no-floor venue byte-identical except the
  2 new keys. `guest_floor` = guest_min unless >400 or >capacity_seated. Band ranking NOT floored (mirrors fn55).
- **ep119 + ep121 capacity RANGE**: new input `capacity_ceiling` (0 / >=10000 = no cap); `max_capacity`
  (ep119) / `min_capacity` (ep121) remain the FLOOR. Cap 0/null venues always kept. Measured (Venue):
  none 624, >=100 364->553, 100-200 417, <=150 389; P/E unchanged. **TRAP HIT: in ep119 a 0 floor resolves
  to NULL**, so gating the ceiling on the floor silently ignored "up to N" — ceiling is gated on
  `$is_pe == false` instead. cache_v not bumped on either.
- Rollbacks: `.claude/xano_backups/{fn56_live_2026-09-23, ep231_live_pre_guestfloor_2026-09-23,
  ep119_live_pre_capunknown_2026-09-23, ep121_live_pre_capunknown_2026-09-23}.xs`.

## WeWeb (editor, needs publish): PI Guest Min Warning 630476c6 (venue) / da1af559 (mirror) — amber,
paid-only, ONLY when floor known and planned count < floor (user: say nothing when no minimum on file).
Guest-capacity RANGE sliders (two stacked ww-input-range, 0-500 step 25, top=10000) replaced the 4
dropdowns, which were DELETED (backup `.claude/weweb_backups/discovery_caprange_DELETED_dropdowns_restore_2026-09-23.json`).
New var `capacity_ceiling` 8a3491e2. **Collections Search Vendors 5c91206d + MapLight a5f0a42c need a
MANUAL `capacity_ceiling` param** (MCP can't edit collections).

## END OF SESSION 2026-09-24 ~00:40 — OPEN FOR NEXT SESSION
- User confirmed the mobile popup + locked-rail sliders work in preview. **WeWeb NOT yet published.**
- **Manual step (user):** add `capacity_ceiling` = `variables['8a3491e2-dc36-471c-9855-23f82d0ee8b8'] ?? 10000`
  to collections Search Vendors 5c91206d AND MapLight a5f0a42c. Until then the max handle is ignored —
  verified via ep119 request history (app sent max_capacity=125, no capacity_ceiling, showed 8k-seat venues).
- Offered, not answered: make ALL locked filter sections open the paywall on click (currently desktop:
  guest + base fee do, food doesn't; popup: only guest does).
- Popup sliders (de222e81/51c2b285) read `context.event.value` — if labels show "Any", switch to `event.value`.
- Popup Cancel does not revert sliders (same as the existing fee/food sliders) — by design for now.
- Next task user queued: wire the NPS survey WeWeb half ([[project_nps_survey]]); uncommitted edits exist in
  components/nps-survey/ — review before wiring.

## 2026-09-24 ~04:48 UTC — capacity v2 SHIPPED (user chose option 3)
Unlisted capacity (0) is EXCLUDED once any floor/ceiling is set, kept when no guest filter. Reason: they
were 189 of 304 (62%) of a 125-175 search. `$cap_unknown` = 0 (no filter) / -1 (filter set); where =
`(X >= floor && X <= ceil) || X == $cap_unknown`. Table 11 has ZERO null capacities among 16,433 validated
rows, so no null branch. Verified: Venue none 624, >=100 364, 125-175 → only 125..175 seats, P/E 227/154/56.
Files `ep119_caprange_v2_2026-09-24.xs` / `ep121_caprange_v2_2026-09-24.xs`. Collections DONE by user
(capacity_ceiling arrives in request history). A burst of 10 uncached test calls got gateway 502s that
never appeared in request history — sequential re-test all 200; don't mistake that for a regression.

## 2026-09-27 FIXED in editor (was: diagnosed) - both steps now read context.event.value; backup .claude/weweb_backups/popup_capacity_slider_wfs_pre_eventfix_2026-09-27.json; read back OK. STILL UNPROVEN on a phone + NOT PUBLISHED.
## (original diagnosis) — mobile popup capacity sliders write the WRONG values
Filter Popup ed90717a. Each slider's change workflow is HALF-fixed from 09-24 (descriptions claim both read the event):
- Min slider de222e81, wf c0a0e0f1-...0021: step `a_floor` (Set floor -> ac799270) reads `variables['94cde6f8-...-value']`
  = the DESKTOP min slider (not rendered on mobile). `a_ceil` correctly reads context.event.value.
- Max slider 51c2b285, wf c0a0e0f1-...0022: step `a_ceil` (Set ceiling -> 8a3491e2) reads `variables['4b517e2b-...-value']`
  = the DESKTOP max slider. `a_floor` correctly reads context.event.value.
Symptom (user screenshot): header "125-400" (stale desktop values) while handles sat ~180 / ~85; search filtered by
the stale range -> "numbers aren't accurate". Fix: both steps -> Number(context?.event?.value) (max: >=500 -> 10000).
Workflows are plain update-variable/custom-js -> MCP-editable. context.event.value itself is UNPROVEN in popups -
test on phone after fix (drag 150/300 -> header must read 150-300). Secondary: handles don't re-sync after the
cross-correction (can display crossed). Check desktop sliders the same way.

## 2026-09-27 later — context.event.value is EMPTY in the popup (header showed "Any" after the first fix)
All 4 popup slider steps now read `(typeof event !== 'undefined' && event) ? event : context?.event` -> ['value'].
The 09-24 note predicted this exactly; heed it next time. Second slider pair in the popup (94cf0e3c / a66c19a1,
"locked twin") is the blurred paywall copy - no workflows, not the bug. STILL UNPROVEN until user drags + header updates.
