---
name: project_trustpilot_reviews_2026-10-21
description: "Trustpilot ask 10-21 - profile tulletogether.com claimed Sep 2026, 0 reviews; NO gift cards, NO promoter-only ask (Trustpilot Jun 2026 rules); NPS thank-you link + Brevo email DRAFTED, nothing live"
metadata:
  node_type: memory
  type: project
  originSessionId: 32283418-b757-47fb-a4ff-b60727497ac2
  modified: 2026-10-21T04:54:43.600Z
---

Owner wanted (10-21) to push NPS promoters to Trustpilot and give a gift card for emailing their review.
Both banned by Trustpilot Guidelines for Businesses (Jun 2026): no incentive of ANY kind for a review (even
neutral), and no selective invites ("review if happy, contact us if not" is their own breach example).
Told the owner; they agreed to the compliant version:
- NPS component (tulletogether nps-survey branch, LOCAL, uncommitted 10-21): thank-you step shows the SAME
  Trustpilot ask to every rater 1-5 (prop reviewUrl, default trustpilot.com/evaluate/tulletogether.com, empty =
  off); no auto-close when shown; Mixpanel nps_review_offered / nps_review_clicked. Builds clean; not rendered.
  Deploy = copy to marthv/ww-nps-survey (normalise LF) -> owner activates + publishes. See [[project_nps_survey]].
- Brevo email draft: tulletogether/.claude/brevo_trustpilot_review_request_draft.html (all buyers ~5 days
  after purchase; plain-note layout like template 417). Not created in Brevo; trigger undecided.
**Why:** violating gets reviews removed + action on the profile. **How to apply:** never branch the ask on
score, never attach a reward; re-raise if anyone proposes either.

**2026-10-24 SHIPPED:** NPS component Trustpilot ask pushed marthv/ww-nps-survey 689cd46 (owner activated, staging). Owner
asked for 5-star-only link -> I pushed back (gating banned); owner accepted the all-raters version.
Brevo template **418** "Review ask - Trustpilot" (ACTIVE; sender hello@, tag review-ask). Past-buyer backfill sent 10-24 ~19:05 UTC
to **148** buyers (paid 3-30d ago, internal excluded) via one-off Xano fn 104 zz_oneoff_review_ask_backfill (tag
review-ask-backfill-2026-10); first run died after 84 (connection reset, ~35s) - resumed from the alphabetical cut, 64 more all 201.
Results .claude/xano_backups/review_ask_backfill_batch1_results_2026-10-24.json. **CANCELLED 10-24 (owner did not mean to send; 418 deactivated, fn 104 deleted):** the 13 in
review_ask_backfill_batch2_2026-10-27.json (chunks of 25), then DELETE fn 104. Ongoing trigger = owner builds Brevo automation
(entry TOTAL_PAYMENTS updated >0, once per contact, wait 5d, send 418) - not confirmed built.

**10-24 final:** owner OK'd the 148 after the fact; batch 2 (13) = NO (owner); template 418 re-ACTIVATED (owner yes) for the Brevo automation, which the owner builds in the Brevo UI (not confirmed built).

**10-24 owner TEST (after 2 warnings incl. FTC review-gating guidance):** NPS Trustpilot ask only at 5 stars - prop reviewMinScore (default 5; 1 = everyone) pushed ww-nps-survey 7964bf1; owner must activate. Revert = set prop to 1. Brevo email stays all-buyers.

**2026-11-03 AFS EMAIL BUILT (switch OFF):** owner asked for an email after a 5-star in-app rating that BCCs Trustpilot AFS
(tulletogether.com+f43884eb79@invite.trustpilot.com) so Trustpilot sends its verified invite. Told the owner it is selective
inviting under Trustpilot rules; owner's call = 5-star (`$tp_min_score` 5; set 1 for everyone). ep308 v6 LIVE 05:08 UTC:
email looked up from table user by user_id (never client email), Brevo /v3/smtp/email, tag trustpilot-invite, once per user
since `$tp_since_ms`. `$tp_enabled=false` -> only vivek.marthi@gmail.com gets it, WITHOUT the BCC. Test 11-03: response
`sent_test_no_bcc`, email landed in owner Gmail; test row 76 deleted, row 24 created_at shifted -5d for the test and restored.
Backups: .claude/xano_backups/ep308_nps_response_pre_trustpilot_invite_2026-11-03.xs (rollback) + _v6_ file.
FLIP (needs explicit owner yes): $tp_enabled=true AND $tp_since_ms=flip time. BCC path itself untested until flip.
Owner told to set AFS frequency to max once / 12 months.
**11-03 copy v2 (05:13 UTC):** subject "Thanks for rating Tulle Together!"; greeting "Hi <first word of user.name>," only when name present, else body starts at "Thank you". Test delivered (Gmail Updates tab). ~8% of names are lowercase -> "Hi sarah,"; no-name path untested.
**11-03 05:15 UTC SWITCH ON (owner "yes, start sending"):** $tp_enabled=true, $tp_since_ms=1793682915796, min score 5; verified in live script. Pre-flip copy: ep308_nps_response_v6_pre_flip_on_2026-11-03.xs (switch-off rollback). Status is NOT stored in table 77 - check = 5-star rows since since_ms vs Brevo tag trustpilot-invite vs Trustpilot invitation history.
**11-03 05:2x UTC real-path test:** owner alt account user 31619 soccerdog333@gmail.com (name Vivek Marthi = INTERNAL, exclude from real-user numbers) -> ep308 returned trustpilot "sent" (Brevo 2xx WITH BCC). Test row 78 in table 77 (page claude-test-trustpilot) - delete after owner confirms the Trustpilot invite arrived (deleting it lets 31619 be invited again on a future 5-star).
**11-03 ~05:30 UTC VERIFIED END-TO-END:** owner set AFS timing Immediately; Trustpilot invite email arrived for 31619. Test row 78 DELETED. AFS frequency left at "every purchase" (our code caps once/user); 1-week reminder ON. Quota 150/mo (100 extra expire Feb 1); 90-day window to CSV-invite past customers (offered, not done). 1-star review by "Logan" 11-02: homepage says 60,000+ couples / 20,000+ vendors vs methodology page 45,000+ / 8,000+ (canonical = 50,000+), asks about PDF retention/deletion, notes profile is all 5-star + "not recently invited" warning.
