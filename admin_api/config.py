"""Settings for the admin API (Railway service behind admin.tulletogether.app)."""

import os

XANO_BASE = os.environ.get("XANO_BASE_URL_ADMIN",
                           "https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B")

# Same shared secret the Streamlit dashboard uses (cohorts.py / dashboard.py read
# ANALYTICS_EXPORT_SECRET). No default here: the service refuses to start data pulls
# without it rather than baking the secret into the repo again.
EXPORT_SECRET = os.environ.get("ANALYTICS_EXPORT_SECRET", "")

# The only credential the Cloudflare Pages proxy holds. Every /api/* request must carry it
# in X-Admin-Key; the proxy adds it only after Cloudflare Access has signed the user in.
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")

# How often the cached snapshots refresh in the background (seconds).
COHORTS_TTL = int(os.environ.get("ADMIN_COHORTS_TTL", str(6 * 3600)))
PRICING_TTL = int(os.environ.get("ADMIN_PRICING_TTL", str(30 * 60)))
