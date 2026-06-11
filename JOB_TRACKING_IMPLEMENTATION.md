# Persistent Job Tracking Implementation

**Date:** June 11, 2026  
**Status:** ✅ Deployed to production

## Problem Solved

Users were losing visibility into running extraction, scraping, and data-fetch jobs when they logged out. Job status was stored only in Streamlit session state, which is ephemeral and not shared across users.

## Solution Built

### 1. Persistent Storage Layer (Xano)
- **Table:** `pipeline_jobs` (ID: 51)
  - Stores job metadata: type, status, user_email, started_at, updated_at, is_active, result_summary, batch_id
  - Survives logouts, app restarts, and multiple concurrent users

### 2. API Endpoints (Xano)
- **POST `/admin/pipeline/job-status`** (ID: 186)
  - Creates or updates job status records
  - Called when job starts: `status="running", is_active=true`
  - Called when job ends: `status="completed/failed", is_active=false`

- **GET `/admin/pipeline/jobs`** (ID: 187)
  - Queries active jobs by type or status
  - Filters: `job_type`, `is_active`

### 3. Dashboard Integration
**Location:** `/dashboard.py`

**Functions added:**
- `_post_job_status(job_type, status, user_email, result_summary, batch_id)` 
  - POSTs job updates to Xano
  - Called at job start and completion
  
- `_get_active_job(job_type)`
  - GETs active jobs from Xano
  - Returns job dict or None

- `_format_job_display(job)`
  - Formats job info with elapsed time
  - Shows: Started, User, Status, Last updated, Elapsed time

**UI Changes:**
- **PDF Extraction tab**: Shows active extraction job with refresh button
- **Google Data & Images tab**: Shows active google_data OR vendor_images jobs
- **Vendor Scraper tab**: Shows active scraper job

**Refresh behavior:**
- Click 🔄 button to refresh status
- Shows spinner: "Fetching latest job status..."
- Displays elapsed time: "⏱️ 5m 23s elapsed"
- Auto-hides when job completes (is_active becomes false)

### 4. Code Changes Summary

**Files modified:**
- `dashboard.py` (+72 lines)
  - Added job tracking functions
  - Updated all 3 tabs with active job displays
  - Added manual refresh buttons with spinner feedback
  - Added elapsed time calculation

**Files created:**
- `runtime.txt` (specifies Python 3.11.5)
- `SETUP_JOB_TRACKING.md` (setup instructions)
- `JOB_TRACKING_IMPLEMENTATION.md` (this file)

**Commits:**
1. `895e0a9` — feat(dashboard): persistent job tracking + fix pending count filter
2. `fe0ad18` — fix(dashboard): remove python 3.10+ type hint syntax
3. `01cdb00` — fix(build): specify python 3.11 for railway compatibility
4. `3344a9a` — fix(build): use runtime.txt for python 3.11.5 compatibility
5. `5a9f75a` — feat(dashboard): display active extraction jobs from persistent store
6. `2392644` — feat(dashboard): add active job displays to all pipeline tabs
7. `72fdc7d` — feat(dashboard): add manual refresh buttons for active job status
8. `080fc19` — feat(dashboard): add spinner and elapsed time to job status refresh

### 5. Architecture

```
Dashboard (Streamlit)
  ↓
  _post_job_status() → POST /admin/pipeline/job-status → Xano API
                                                            ↓
                                                         pipeline_jobs table
                                                            ↑
  _get_active_job() ← GET /admin/pipeline/jobs ← Xano API
  ↑
Display with refresh button & elapsed time
```

**Data Flow:**
1. User starts extraction job
2. Dashboard calls `_post_job_status("extraction", "running", user_email)` 
3. Xano creates row in pipeline_jobs with is_active=true
4. Job runs (user can logout now)
5. Any user opening dashboard sees the active job
6. Click refresh 🔄 to get latest status
7. Job finishes
8. Dashboard calls `_post_job_status("extraction", "completed", user_email, result_summary)`
9. Xano sets is_active=false
10. Display auto-hides when query returns no active jobs

## What Still Needs Work

### Phase 2: Detailed Progress Tracking
- **Problem:** Currently only shows "RUNNING" with start time. Need: current PDF, success/failure counts
- **Solution:** Modify `extract_core.py` to send granular updates
- **Requires:**
  - Periodic `_post_job_status()` calls during extraction
  - Store in `result_summary`: `{current_pdf: "PDF_042", ok: 12, failed: 2, pending: 50}`
  - Display this detail in dashboard
  - Add "stuck job" detection: if `updated_at` too old, flag it
  
### Phase 3: Job History
- Store completed job results in separate table
- Dashboard can show "Last 10 extraction jobs"
- Performance metrics: average time, failure rate by PDF type

## Testing Checklist

- ✅ Deploy to production
- ✅ Start extraction job
- ✅ Log out
- ✅ Log back in
- ✅ Verify active job displays with correct user/timestamp
- ✅ Click refresh 🔄, see spinner and updated elapsed time
- ✅ Wait for job to complete, verify display auto-hides
- ⏳ Test with concurrent users (different jobs running simultaneously)
- ⏳ Test stuck job detection (manual mark as failed if > 2 hours)

## Environment Variables (Railway)

Required:
```
XANO_JOB_STATUS_ENDPOINT=https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B/admin/pipeline/job-status
XANO_JOBS_ENDPOINT=https://xqtb-2ma7-ijfy.n7e.xano.io/api:GynP5T1B/admin/pipeline/jobs
MISE_PYTHON_GITHUB_ATTESTATIONS=false
```

## Deployment Notes

- Latest commit: `080fc19`
- Branches: main (deployed) 
- Build status: ✅ Passing
- Both services live:
  - ✅ Tulle Admin Dash (dashboard.py)
  - ✅ extraction-batch (separate service)

## Known Issues

1. **Elapsed time calculation:** Currently only works if timestamp parsed correctly
   - Timestamps come in milliseconds (e.g., 1781155070508)
   - Need better timestamp parsing
   
2. **Job status not auto-updating:** Job won't show progress mid-run
   - Extract job runs to completion before updating status
   - Need periodic updates from extract_core.py

3. **No stuck job detection:** If job crashes, status stays "RUNNING" forever
   - Need alert if updated_at > 2 hours old
   - Need manual "Mark as failed" button

## Next Steps

1. Fix timestamp parsing in `_format_job_display()`
2. Modify `extract_core.py` to send periodic progress updates
3. Add "stuck job" detection and manual override
4. Build job history dashboard
5. Add granular error tracking (which PDFs failed, why)

---

**Shipped by:** Claude  
**Ready for Phase 2:** Detailed progress tracking
