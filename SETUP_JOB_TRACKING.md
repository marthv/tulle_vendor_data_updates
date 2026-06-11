# Pipeline Job Tracking Setup

This guide walks you through setting up persistent job tracking so extraction and scraping jobs survive logouts.

## What This Fixes

- ✅ Job status persists across logouts and Streamlit restarts
- ✅ Multiple users won't interfere with each other's session state
- ✅ You can see what jobs are running even after logging out and back in

## Step 1: Create the `pipeline_jobs` Table in Xano

Go to **Xano Dashboard → Database → Add Table**.

**Table Name:** `pipeline_jobs`

**Schema** (copy-paste into Xano table builder):

```xs
table "pipeline_jobs" {
  auth = false
  schema {
    uuid id {
      description = "Unique job identifier"
    }

    text job_type {
      description = "Type of job: extraction, scrape, google_data, vendor_images"
    }

    text status {
      description = "Current status: pending, running, completed, failed"
    }

    text user_email {
      description = "Email of the user who started the job"
    }

    timestamp started_at?=now {
      description = "When the job was started"
    }

    timestamp updated_at?=now {
      description = "When the job status was last updated"
    }

    json result_summary? {
      description = "JSON blob with job results (counts, errors, cost, etc)"
    }

    bool is_active?=1 {
      description = "True while job is running, false when completed"
    }

    text batch_id? {
      description = "Optional batch ID for batch extraction jobs"
    }
  }

  index = [
    {type: "primary", field: [{name: "id"}]}
    {type: "btree", field: [{name: "job_type", op: "asc"}]}
    {type: "btree", field: [{name: "is_active", op: "asc"}]}
    {type: "btree", field: [{name: "user_email", op: "asc"}]}
    {type: "btree", field: [{name: "started_at", op: "desc"}]}
  ]
}
```

## Step 2: Create API Endpoints in Xano

### Endpoint 1: POST /admin/pipeline/job-status

**Create new API:** `POST /admin/pipeline/job-status`

**Request body (JSON):**
```json
{
  "job_type": "extraction|scrape|google_data|vendor_images",
  "status": "running|completed|failed",
  "user_email": "user@example.com",
  "result_summary": {...optional JSON...},
  "batch_id": "...optional..."
}
```

**XanoScript (copy into the endpoint):**
```xs
/* Find any existing active job of this type */
let existing = db.pipeline_jobs
  ->search({job_type: request.body.job_type, is_active: true})

if (existing.length > 0) {
  /* Update existing job */
  db.pipeline_jobs -> update(existing[0].id, {
    status: request.body.status,
    updated_at: now,
    is_active: request.body.status == "running",
    result_summary: request.body.result_summary ?? null
  })
} else if (request.body.status == "running") {
  /* Create new job if it's a start event */
  db.pipeline_jobs -> create({
    job_type: request.body.job_type,
    status: "running",
    user_email: request.body.user_email,
    started_at: now,
    updated_at: now,
    is_active: true,
    batch_id: request.body.batch_id ?? null,
    result_summary: null
  })
}

-> response
```

### Endpoint 2: GET /admin/pipeline/jobs

**Create new API:** `GET /admin/pipeline/jobs`

**Query parameters:**
- `job_type` (optional): filter by job type
- `is_active` (optional): filter by active status (true/false)

**XanoScript:**
```xs
let filters = {}
if (request.query.job_type) { filters.job_type = request.query.job_type }
if (request.query.is_active != null) { filters.is_active = request.query.is_active == "true" }

db.pipeline_jobs
  ->search(filters)
  ->sort({field: "started_at", order: "desc"})
  -> response
```

## Step 3: Set Railway Environment Variables

Go to **Railway Dashboard → Project → Variables** and add:

```
XANO_JOB_STATUS_ENDPOINT=https://[your-xano-domain]/api:xxxx/admin/pipeline/job-status
XANO_JOBS_ENDPOINT=https://[your-xano-domain]/api:xxxx/admin/pipeline/jobs
```

Replace `[your-xano-domain]` with your actual Xano domain and `xxxx` with your actual API group identifier.

## Step 4: Deploy & Test

1. Commit the dashboard.py changes:
   ```bash
   cd tulle_vendor_data_updates
   git add dashboard.py
   git commit -m "feat(dashboard): add persistent job tracking across logouts"
   git push
   ```

2. Railway will auto-redeploy

3. Test:
   - Start an extraction job
   - While it's running, logout from Google and log back in
   - You should still see the job as "running" (no phantom restart)

## What Happens Now

### Job Start
```
User clicks "Run All Pending"
→ Dashboard calls: _post_job_status("extraction", "running", user_email)
→ Xano creates/updates pipeline_jobs row with is_active=true
→ Job runs...
```

### Job Completion
```
Job finishes
→ Dashboard calls: _post_job_status("extraction", "completed", user_email, result_summary)
→ Xano updates pipeline_jobs row with status="completed", is_active=false
```

### If User Logs Out Mid-Job
```
User logs out
→ Streamlit session cleared
→ BUT pipeline_jobs table still has is_active=true
→ User logs back in
→ Pipeline status refreshes from Xano table
→ Dashboard shows "extraction job still running" with timestamps
```

## Optional: Add Job Status Display

To show active jobs in the dashboard header (even after logout), add this near the top of the PDF Extraction tab:

```python
active_jobs = _get_active_job("extraction")
if active_jobs:
    with st.warning(f"⚠️ Active job: {active_jobs.get('job_type')} started at {active_jobs.get('started_at')}"):
        st.write(f"Status: {active_jobs.get('status')} | User: {active_jobs.get('user_email')}")
```

## Troubleshooting

- **No jobs being saved?** Check that `XANO_JOB_STATUS_ENDPOINT` is set correctly in Railway
- **Still losing status on logout?** Verify Streamlit session is being cleared but Xano table persists
- **Multiple concurrent jobs?** The table design allows this — just filter by `is_active=true` to see current jobs
