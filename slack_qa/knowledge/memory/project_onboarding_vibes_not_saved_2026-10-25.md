---
name: project_onboarding_vibes_not_saved_2026-10-25
description: "Onboarding step-2 vibes + venue-customization answers NOT saved for anyone since 2026-07-10 (id 22887+, ~10.2k users): Finish PATCH ep109 omits them; both selects also have broken mappings (hardcoded vibes array)"
metadata:
  type: project
---

Found 10-25 (read-only). Onboarding Popup bbdfe17d step 2: vibes select 11e769c8 and venue-customization select
4065d427 have mappingLabel/mappingValue = hardcoded 10-vibe array (should be context.mapping); 4065d427 should also be
selectType single. Finish wf cbb346d5 action c39ddb16 PATCH /user/update_user_attributes (ep109) sends only location,
budget, guests, wedding_vendor, name, id - NOT vibes/customization. ep109 already accepts `vibes` (text[]) and
`customization` (text). Data: last saved value id 22886 (07-10 12:29 UTC); 0 of 10,204 onboarded since; pre-July data clean
(1,303 vibes / 1,233 customization, 43% / 41% answer rates). Unrecoverable - re-ask via assistant prefs.
Downstream: rec-engine PROFILE_FIELDS (empty since July); Discovery Onboarding_Recommendation collection defaults
pricing_models to "Semi-Inclusive" when customization empty.
Fix order: (1) mappings on both selects + 4065d427 single (editor) BEFORE (2) adding vibes/customization to the c39ddb16 body
(plugin action = owner hand edit), (3) error branch b8c8973a loader true->false ([[project_onboarding_finish_spinner]]).
Verify: live /data/d0f4f382-*.json has "vibes" in c39ddb16 body; new table-1 rows get values.
