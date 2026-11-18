---
name: project_pi_band_ref_v20
description: fn56 v20+v21 (2026-10-03) - PI band now ranked at ref >= headline count (was always 75) + headline says "before tax and gratuity"; ep231 cache_v 19; WeWeb caption edit UNPUBLISHED
metadata:
  type: project
---

LIVE 2026-10-03 ~16:00 UTC via `.claude/xano_backups/apply_fn56_v20_band_ref.py` (`--restore` reverts fn56 and
bumps cache_v again). Backups: `fn56_pre_band_ref_allin_2026-10-03.xs`, `ep231_pre_band_ref_allin_2026-10-03.xs`.
Read-back byte-identical to intent; live ep231 smoke test 200 on V2013/V3794/V122/V236/V179/V1/V2235.

**#1:** old code picked REFS [75,125,200] nearest DEFAULT_GUESTS=100 -> 75/125 tie kept 75 -> EVERY space ranked
at 75 while headline priced at DEFAULT_AT (100 or floor rounded up to 25). Now ref = smallest REF >= DEFAULT_AT,
capped at capacity_seated (largest REF <= cap; cap < 75 keeps 75); venue priced at max(ref, GUEST_FLOOR) / ref.
Predicted ~1,742 of 12,168 placed spaces (14%) change label; V236 + V179 luxury->average confirmed live. V2013
(screenshot venue) now ref 125, band $143-$334, still "middle".
**#2:** headline_prefix ", all in" -> ", before tax and gratuity" (total was always pre-tax; only 17% have tax_pct).

**WeWeb (UNPUBLISHED until user publishes):** caption `PI Band Scale Caption` 5dddf445 now "Per guest at N guests,
before tax and gratuity, across..." (was "All-in per guest at N guests"). Backup
`.claude/weweb_backups/pi_band_caption_5dddf445_pre_allin_2026-10-03.json`. Caption already read
band.reference_guests, so the guest number was correct without the edit.

**Still open (user saw list):** guest minimum stated 3x (fn56 guest_floor_note + WeWeb banner + stepper label);
F&B "below about N guests" line ignores floor; "From this venue" twice; DEFAULT_AT rounds 120 -> 125 so caption
says 125 while stepper shows 120; band fixed server-side while stepper moves; fn55 all_in_pg_* ignore floors;
cap < 75 venues (935 placed) still ranked at 75. [[project_pi_panel_infographic]] [[project_pi_guest_min_and_capacity_filter]]

## v21 TRIM (same day, user-approved "save space and make it punchy")
fn56 v21 LIVE, ep231 cache_v 20 (`apply_fn56_v21_trim_copy.py`; `--restore` = back to v20 + cache bump).
Paid row sentences -> facts ("$54 a head · $6,700 minimum", "22% · not a tip", rental/flat ceremony ''),
tax "7%", gratuity 1 line, band "Middle of the pack for X", footer "From the pricing PDF a couple uploaded
for this space." (count dropped - caption has it), headline_note/guest_floor_note shortened. FREE-TIER unlock
copy untouched. **User: KEEP the per-row source chips** ("from this venue vs not ... helps").
WeWeb (UNPUBLISHED at write time), backups `.claude/weweb_backups/*_pre_trim_2026-10-03.json`:
total row hidden (9b6f0ad0), PI Provenance Row d5cde70f hidden for paid, badge legend 31e9d28a -> false,
guest-floor line skipped while Guest Min Warning shows (bb00423d mirrors 630476c6's condition),
caption 5dddf445 "Per guest at N guests · 134 X spaces · typical = middle half".

## v22 — row detail RESTORED (same day). LIVE fn56 = v22, ep231 cache_v 21.
User after v21: "we're losing a lot of good intel on the line by line items ... people want to see that,
that was great context and detail". `apply_fn56_v22_restore_row_detail.py`: v20 + only v21's non-row edits
(band verdict, footer, headline_note, guest_floor_note). Row sentences byte-identical to v20 (diff-verified).
**Feedback: don't trim the per-row explanations (food/ceremony/rental/service/tax/gratuity) - user values them.**
WeWeb trims (total row, paid provenance line, badge legend hidden; guest-floor dedupe; short caption) kept.

## v23 — verdict sentence RESTORED (same day). LIVE fn56 = v23, ep231 cache_v 22.
User: "lets add the verdict back in too". Band sentence back to v20 wording ("This sits in the middle for X.
Plenty of venues cost more, plenty cost less."). `apply_fn56_v23_restore_verdict.py` (`--restore-v22`).
Still trimmed from v21: footer, headline_note, guest_floor_note. Lesson: user prefers the explanatory copy;
propose trims one at a time with before/after rather than a bulk cut.
