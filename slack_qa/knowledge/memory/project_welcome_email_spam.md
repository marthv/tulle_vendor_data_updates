---
name: project_welcome_email_spam
description: "Welcome email lands in Yahoo spam - tulletogether.com SPF omits Google and there is no Google DKIM, yet welcome mail is sent via Gmail SMTP"
metadata:
  node_type: memory
  type: project
  originSessionId: 0c05ebfb-a977-4a0f-84cf-5b564a9affe4
  modified: 2026-09-28T14:37:11.814Z
---

2026-09-28, Kate reported the welcome email in Yahoo spam. DNS checked via 8.8.8.8:
- MX = Google Workspace (smtp.google.com)
- SPF = `v=spf1 include:_spf.wpcloud.com ~all` - **no `include:_spf.google.com`**
- `google._domainkey` does NOT exist (Brevo DKIM brevo1/brevo2 does)
- DMARC `p=none`, rua to Brevo

Welcome mail goes out through Gmail SMTP (Xano fn 72 nodemailer GMAIL_*, and the Sign Up v2 browser step), so
it fails SPF and has no aligned DKIM -> DMARC fails. Brevo campaign mail is fine (DKIM aligned).

**Why:** Yahoo/Gmail bulk-sender rules penalise unauthenticated mail; adding an image does not help.
**How to apply:** fix = add `include:_spf.google.com` to SPF + generate DKIM in Google Admin (Gmail > Authenticate
email) and publish `google._domainkey`. Verify with raw headers (spf/dkim/dmarc=pass) before claiming fixed.
DNS appears hosted at WordPress.com (wpcloud). Related: [[project_google_signin]]
