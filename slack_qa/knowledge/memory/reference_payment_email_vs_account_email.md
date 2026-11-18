---
name: reference_payment_email_vs_account_email
description: "Payment log 'User' + Slack payment ping show the STRIPE CHECKOUT email, not the account email - look buyers up by Client_Reference_ID (= user id)"
metadata:
  type: reference
---

2026-10-28 (Marta Zewdu): paid $129 forever 10-14 as marta.zewdu@gmail.com at Stripe, but her account was user 32920
nigelandmarta@gmail.com (credited correctly via client_reference_id). On 10-27 she Google-signed-in as marta.zewdu@ ->
brand-new empty account 34460 -> "I paid but it says free plan". Searching table 1 by the Slack/payment-log email finds
the WRONG row. Always resolve via table 16 `Client_Reference_ID`. Nothing links a later sign-up under the checkout email
to the paid account (product gap; ideas: flag new sign-ups whose email is in table 16, put account email in the Slack ping).
Owner fixed 34460 by hand (date 3000-01-01); its $129 double-counts table-1 revenue.
Contrast Amanda Melin 34250 (same day): single account, backend correct, auth/me returned forever 12:42-12:46 UTC 10-27 -
client-side lockout, cause unknown (logs before 12:35 UTC gone). PDF gate a97818d6 is still date-only (owner fixing 10-28).
Related: [[reference_access_entitlement_model]], [[reference_weweb_component_gotchas]].
