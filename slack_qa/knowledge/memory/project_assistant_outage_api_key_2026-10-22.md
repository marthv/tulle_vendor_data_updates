---
name: project_assistant_outage_api_key_2026-10-22
description: "Tulle Assistant DOWN from 2026-10-22 ~03:01-06:22 UTC: every /rec/opening + /rec/refine fails Anthropic 401 invalid x-api-key; 96 failed requests / 40 users by 10-24 14:33 UTC"
metadata:
  node_type: memory
  type: project
  originSessionId: abb664ba-4018-4524-a407-838ba2ba7236
  modified: 2026-10-24T14:45:04.784Z
---

Found 2026-10-24 from the Tulle Ops daily brief (red "0 opening + 0 refine" on 10-23 and 10-24).
Xano table 79 rec_usage: last status=ok id 587 at 10-22 03:01 UTC; ids 588-683 ALL status=error
"401 authentication_error invalid x-api-key" (first 06:22 UTC). 96 requests, 40 distinct users.
Railway rec-engine /health still says ok+enabled (health does not call Anthropic), no rec_service
commits since 74c9eec (10-21) -> the key itself was revoked/rotated, not a code break. Cause of
revocation UNKNOWN (possible: key rotated in console, or auto-revoked after a leak - see
[[project_security_lockdown_2026-09-29]]).
Fix = owner makes a new Anthropic key and sets ANTHROPIC_API_KEY on Railway rec-engine; check any
other service/Xano env using the same key (ep157 PDF parse uses Xano's own setting - unverified).
**How to apply:** after the fix, verify by a new status=ok row in table 79, not /health. The daily brief
only flags this once a day - an immediate alert on the first auth error would have caught it ~2 days sooner.
Related: [[project_ai_recommendation_engine]]

**FIXED 2026-10-24:** owner set a new key on Railway; verified by table 79 rows 684 (opening) + 685 (refine), status ok, acct 14998, ~15:13 UTC.
