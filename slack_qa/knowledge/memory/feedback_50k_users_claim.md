---
name: feedback_50k_users_claim
description: "50,000+ users/couples" is correct - many pre-migration users were never ported into Xano table 1 (28.6k rows); don't flag the mismatch
metadata:
  type: feedback
---

The "50,000+" user/couple count on the site and in CLAUDE.md is right even though Xano table 1 has ~28.6k rows.

**Why:** user (2026-10-03): a lot of users weren't ported over when migrating systems.
**How to apply:** don't raise the table-1 vs 50k gap again; use table 1 only for rates/cohorts, not total-user claims.

**UPDATE 2026-11-12:** owner chose **60,000** when asked about the plans page ("Trusted by over 60,000 couples.", ww-text 16e5c4e5 on Membership). Owner then said '60k everywhere' -> DONE in editor 11-12: Other Vendors PDF-limit social proof 29c12b5a, shared 'Venue Type Badge' 6a745784 (Discovery/Membership/Planner/vendor pages), venue-page social proof 704ebc3f (owner edited it himself); CLAUDE.md line 14 + NOTE + paywall-modal trust line updated (uncommitted). 60,000+ is canonical from 2026-11-12. Backup weweb_couples_count_50k_to_60k_2026-11-12.json. Superseded text follows: (PDF limit card "Trusted by 50,000+ couples", pricing/venue pages, CLAUDE.md canonical note). Until then: plans page says 60,000, elsewhere 50,000+.
