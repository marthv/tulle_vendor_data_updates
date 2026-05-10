# Tulle Admin Dashboard

Internal Streamlit app for the Tulle Together team. Hosted on Railway, protected by Google OAuth.

**What it is:** A single-URL web tool for running and monitoring the PDF data pipeline that powers tulletogether.app. Couples submit vendor pricing PDFs → this app extracts the pricing data using Claude → structured rows land in Xano → WeWeb surfaces them to users.

---

## What each tab does

### Admin
Timebound reporting for a chosen date range: new signups, to-dos created, packages created, and payments made. Also contains a **Data Explorer** for browsing and editing Xano tables directly (WPTP Updated Mappings, WPTP PDFs, Users, Extracted PDF Data, Venue Pricing).

### PDF Extraction
Runs extraction on a batch of PDFs. Downloads PDFs from Google Drive, sends each one to Claude (4 passes: summary, grid structure, pricing grid, classification), then POSTs the structured rows into Xano. Shows a live log as it runs, cost per run, and a per-venue result card when done.

Use this tab for targeted re-runs or one-off batches. Use **Pipeline** for production runs.

### Google Data
Triggers a Xano batch job that fetches Google Places data (rating, review count, address) for vendors in WPTP Updated Mappings that have a Place ID but no cached data yet. Takes a start/end vendor ID range.

### Vendor Images
Pulls photos from Google Places API and saves them into WPTP Updated Mappings. Runs 3 image slots (one, two, three) individually or all in sequence. Requires Google Data to have been run first.

### Sync Collections
Takes a list of Vendor IDs, reads the `CATEGORY` field from Extracted PDF Data for each one, and writes it into the `Collection` array on WPTP Updated Mappings. Run this after extracting new PDFs to keep the WeWeb collection filters in sync.

### Pipeline
Production extraction dashboard. Shows a live status overview across all 6,700+ rows in `wptp_pdfs` (Pending / Extracted / Partial / Failed / Skipped). Run modes:

- **All pending** — processes every PDF not yet extracted, skipping already-done rows
- **Re-run all failed** — retries every row marked `failed`
- **Specific PDF IDs** — runs named PDFs (e.g. `P19, P42`) regardless of current status
- **Row range** — processes a slice of the `wptp_pdfs` table by row index

After a run, shows per-venue result cards and a "View rows written to Xano" expander with the actual rows posted to `extracted_pdf_data` and `venue_pricing`.

---

## Extraction pipeline (what actually happens per PDF)

```
wptp_pdfs row (PDF_Link → Google Drive)
    ↓
Download PDF bytes via service account
    ↓
Pass 1 — Claude: summary fields (venue type, pricing year, admin fee, peak/off-peak fees)
Pass 2 — Claude: map pricing grid structure (spaces, seasons, day columns)
Pass 3 — Claude: extract every pricing row (venue fee, F&B min, per-person, by month+day)
Pass 4 — Claude: classify venue offering + attributes + category
    ↓
POST rows → Xano /extracted_pdf_data  (summary: 1–N rows per space)
POST rows → Xano /venue_pricing       (grid: up to ~96 rows per PDF)
PATCH     → Xano /wptp_pdfs/{id}      (sets extraction_status, cost, timestamp, attempts)
```

Model: `claude-sonnet-4-20250514`. Typical cost: ~$0.20–0.40 per PDF with prompt caching.

---

## Xano tables written to

| Table | Written by | What's in it |
|---|---|---|
| `extracted_pdf_data` | PDF Extraction | One row per venue space — summary fields, classification, pricing year |
| `venue_pricing` | PDF Extraction | One row per space × day × month — all dollar values |
| `wptp_pdfs` | PDF Extraction | `extraction_status`, `last_extracted_at`, `extraction_cost_usd`, `extraction_attempts`, `last_error` |
| `WPTP Updated Mappings` | Google Data, Vendor Images, Sync Collections | Google cache, images, Collection tags |

---

## Environment variables

Set these in the Railway dashboard under Variables.

| Variable | What it's for |
|---|---|
| `GOOGLE_CLIENT_ID` | OAuth 2.0 Web client ID (Google Cloud Console) |
| `GOOGLE_CLIENT_SECRET` | OAuth 2.0 Web client secret |
| `APP_URL` | Full public Railway URL, e.g. `https://tulle-pipeline.up.railway.app` |
| `ALLOWED_EMAILS` | Comma-separated list of Google emails that can log in |
| `ANTHROPIC_API_KEY` | Claude API key |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON string of the service account key (for Drive downloads) |
| `XANO_GET_ENDPOINT` | `https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B/wptp_pdfs` — used for GET list, status table, and PATCH writeback |
| `XANO_SUMMARY_ENDPOINT` | POST endpoint for `extracted_pdf_data` rows |
| `XANO_PRICING_ENDPOINT` | POST endpoint for `venue_pricing` rows |
| `XANO_BASE_URL` | Base URL for enrichment endpoints (`/google_data_batch`, `/sync_collections`, etc.) |
| `DASHBOARD_PASSWORD` | Fallback password auth (only used if Google OAuth is not configured) |

---

## Running locally

```bash
pip install -r requirements.txt

# Copy and fill in your env vars
cp .env.example .env  # or set them manually

streamlit run dashboard.py
# Opens at http://localhost:8501
```

For local dev without Google OAuth, set `DASHBOARD_PASSWORD` and leave `GOOGLE_CLIENT_ID` unset — the app falls back to password login.

---

## Deployment

Hosted on Railway. Every `git push origin main` triggers a redeploy. Start command (in `railway.toml`):

```
streamlit run dashboard.py --server.port $PORT --server.headless true --server.address 0.0.0.0
```

No build step — Railway installs `requirements.txt` and runs directly.

---

## File structure

```
tulle_vendor_data_updates/
├── dashboard.py       — Streamlit app (all tabs, auth, UI)
├── extract_core.py    — Extraction logic (Claude calls, Drive download, Xano posts)
├── requirements.txt
├── railway.toml       — Railway deploy config
└── schema/            — Xano schema reference docs
```

`extract_core.py` is a pure generator/function module — no Streamlit imports. The dashboard imports `run_extraction` and `get_pipeline_status` from it. This keeps the extraction logic testable and runnable independently of the UI.
