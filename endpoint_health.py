"""Preflight health check for every Xano endpoint this dashboard depends on.

WHY THIS EXISTS
---------------
On 2026-08-30 the Xano API surface was audited after two public Apify actors were found
scraping the site, and a batch of endpoints were switched from anonymous to authenticated.
Xano creates every endpoint with `auth = false`, so security here is opt-in and the whole
surface drifts open over time — but the correction has a matching failure mode: this
dashboard holds NO Xano user token, so any endpoint switched to `auth = "user"` breaks a
tab silently, and you only find out when someone clicks the button.

That happened the same night to `google_data_batch` (ep122): it was given user auth, which
this app cannot satisfy, and the "Run Google Data Batch" button would have started
returning 401 with no other signal. It was re-gated on the shared secret instead.

THE RULE THIS PANEL ENFORCES
----------------------------
    machine callers (this dashboard, the pipeline) -> `secret` gate
    browser callers (WeWeb, real logged-in users)  -> `auth = "user"`

Mixing them up is the bug. A 401 below means someone put user auth on a machine endpoint;
a 403 means the secret is wrong or missing on our side. Both are actionable and neither is
visible from the tab itself until it fails in front of you.

Probes are cheap and read-only: every GET asks for the smallest page it can, and writes are
listed but never called.
"""

import concurrent.futures
import os

import pandas as pd
import requests
import streamlit as st

# (label, base_key, path, params, tab, note)
#   base_key: "xano"  -> group 10, WeWeb Transparency Project (api:GynP5T1B)
#             "vendor"-> group 13, Vendor Portal (api:BjliGATR) — different canonical base
#   params with the literal "<SECRET>" get the shared ANALYTICS_EXPORT_SECRET substituted.
CHECKS = [
    ("venue_pricing_dashboard (ep199)", "xano", "venue_pricing_dashboard",
     {"section": "pricing", "page": 1, "per_page": 1, "secret": "<SECRET>"},
     "💰 Venue Pricing", "secret-gated 2026-08-30; was leaking the whole pricing corpus"),
    ("venue_pricing_dashboard pdfs (ep199)", "xano", "venue_pricing_dashboard",
     {"section": "pdfs", "page": 1, "per_page": 1, "secret": "<SECRET>"},
     "💰 Venue Pricing", "same gate; this branch returns Drive links"),
    ("analytics_users_export (ep205)", "xano", "analytics_users_export",
     {"secret": "<SECRET>", "page": 1, "per_page": 1},
     "📈 Cohorts", "secret-gated"),
    ("analytics_hub_stats (ep206)", "xano", "analytics_hub_stats",
     {"secret": "<SECRET>"},
     "📈 Cohorts", "secret-gated"),
    ("roadmap_orders_admin (ep213)", "xano", "roadmap_orders_admin",
     {"secret": "<SECRET>"},
     "🗺️ Roadmap Orders", "secret-gated"),
    ("google_data_batch (ep122)", "xano", "google_data_batch",
     {"starting_index": 1, "ending_index": 1, "secret": "<SECRET>"},
     "🔍 Google Data & Images", "secret-gated 2026-08-30 (briefly had user auth — broke this tab)"),
    ("admin/google/coverage (ep191)", "xano", "admin/google/coverage",
     {"secret": "<SECRET>"},
     "🔍 Google Data & Images", "secret-gated 2026-08-30; a dozen full-table counts per call"),
    ("admin/pipeline/jobs (ep187)", "xano", "admin/pipeline/jobs",
     {"job_type": "extraction", "is_active": "true", "secret": "<SECRET>"},
     "📄 PDF Extraction / pipeline", "secret-gated 2026-08-30; read by BOTH the dashboard and "
                                    "the pipeline via XANO_JOBS_ENDPOINT"),
    # PIPELINE dependencies, not the dashboard's own. They are checked here because this is
    # the only place anyone looks: on 2026-08-30 wptp_pdfs was switched to auth="user" and
    # the batch-ingest service died with "INGEST FAILED - 401 Unauthorized", which surfaced
    # nowhere until someone noticed the Railway service had crashed hours later.
    ("wptp_pdfs (ep146) — PIPELINE", "xano", "wptp_pdfs",
     {"secret": "<SECRET>"},
     "batch-ingest / extraction", "secret-gated; ~8.6MB/call, streamed here. NOT user auth."),
    ("wptp_venue_categories (ep177) — PIPELINE", "xano", "wptp_venue_categories",
     {"page": 1, "per_page": 1},
     "batch-ingest / extraction", "anonymous; _fetch_vendor_categories FAILS CLOSED, so "
                                  "gating this aborts every extraction run"),
    ("vendor_admin/queue (group 13)", "vendor", "vendor_admin/queue",
     {"secret": "<SECRET>"},
     "🏛️ Vendor Portal", "secret-gated; note the BjliGATR base, not GynP5T1B"),
    ("vendor_admin/calendars (group 13)", "vendor", "vendor_admin/calendars",
     {"secret": "<SECRET>"},
     "🏛️ Vendor Portal", "secret-gated"),
]

# Endpoints the dashboard writes through. Listed for completeness, never probed — a health
# check must not mutate production. update_vendor_image_* were still anonymous as of
# 2026-08-30; if they are ever gated, they need the secret passed at their call site too.
WRITE_DEPS = [
    ("roadmap_orders_admin_update (ep214)", "POST", "secret", "🗺️ Roadmap Orders"),
    ("update_vendor_image_one/two/three (ep132-134)", "POST", "anonymous", "🔍 Google Data & Images"),
    ("vendor_admin/submission_update, claim_update, message_update", "POST", "secret", "🏛️ Vendor Portal"),
]


def _probe(base, path, params, timeout=90):   # coverage (ep191) legitimately takes ~60s
    """One read-only GET. Returns (status_label, detail). Never raises.

    Streamed and closed immediately: we only need the status line, and one of these
    endpoints (wptp_pdfs) returns ~8.6 MB per call — downloading that on every health check
    would make the check itself the most expensive thing on the page."""
    try:
        r = requests.get(f"{base}/{path}", params=params, timeout=timeout, stream=True)
        r.close()
    except Exception as e:
        return "UNREACHABLE", str(e)[:120]

    if r.status_code == 200:
        return "OK", "200"
    if r.status_code == 401:
        return "AUTH BROKEN", "401 — endpoint now requires a USER token this app cannot supply"
    if r.status_code == 403:
        return "SECRET BROKEN", "403 — gate rejected our secret (check ANALYTICS_EXPORT_SECRET)"
    if r.status_code == 404:
        return "MISSING", "404 — endpoint deleted or renamed"
    if 500 <= r.status_code < 600:
        return "SERVER ERROR", f"{r.status_code} — Xano side, usually transient"
    return "UNEXPECTED", f"{r.status_code}: {r.text[:100]}"


def run_checks(xano_base, vendor_base, secret):
    """Probe every read dependency in parallel. Returns a DataFrame."""
    bases = {"xano": xano_base, "vendor": vendor_base}

    def one(chk):
        label, base_key, path, params, tab, note = chk
        p = {k: (secret if v == "<SECRET>" else v) for k, v in params.items()}
        status, detail = _probe(bases[base_key], path, p)
        return {"Endpoint": label, "Status": status, "Detail": detail,
                "Used by": tab, "Note": note}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(one, CHECKS))
    return pd.DataFrame(rows)


def render_endpoint_health(xano_base, vendor_base, secret):
    """Collapsed panel. Cheap when closed — nothing runs until the button is pressed."""
    with st.expander("🔐 Endpoint health — check every Xano dependency", expanded=False):
        st.caption(
            "Xano endpoints default to **open**, so the surface drifts insecure; but this app "
            "holds no user token, so locking one with `auth = \"user\"` breaks a tab silently. "
            "Machine callers must use the **secret** gate. Run this after any endpoint change."
        )

        if st.button("▶ Run endpoint health check", key="eh_run"):
            with st.spinner("Probing…"):
                df = run_checks(xano_base, vendor_base, secret)
            st.session_state["eh_df"] = df

        df = st.session_state.get("eh_df")
        if df is None:
            st.info("Not run yet.")
        else:
            bad = df[df["Status"] != "OK"]
            if bad.empty:
                st.success(f"All {len(df)} read dependencies OK.")
            else:
                st.error(f"{len(bad)} of {len(df)} dependencies are failing.")
                for _, r in bad.iterrows():
                    st.markdown(f"- **{r['Endpoint']}** → `{r['Status']}` — {r['Detail']}  \n"
                                f"  used by *{r['Used by']}*")
            st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("**Write dependencies** (listed, never probed — a health check must not mutate prod):")
        st.dataframe(
            pd.DataFrame(WRITE_DEPS, columns=["Endpoint", "Verb", "Gate", "Used by"]),
            use_container_width=True, hide_index=True,
        )
