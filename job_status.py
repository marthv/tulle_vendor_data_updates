"""job_status.py — post job status to Xano. Deliberately free of Streamlit.

WHY THIS MODULE EXISTS
    extract_core used to reach this function via `from dashboard import
    _post_job_status`. Streamlit runs dashboard.py as the main script under a
    synthetic module name, so importing it BY NAME re-executes the whole file as a
    second, separate module instance — which re-creates every widget on the page and
    raises StreamlitDuplicateElementKey (it surfaced on key='tt_mode_pick').

    That exception landed inside the batch-submit path's try/except, so EVERY batch
    submitted from the dashboard lost its job record and was orphaned: batch_id +
    pdf_map were never persisted, and poll_and_ingest_batches — which finds work by
    reading XANO_JOBS_ENDPOINT — reported `checked=0` forever. Recovery then required
    a human running ingest_batch.py by hand. Batch msgbatch_01MrZwJwTFGYRvwgshRnVM6W
    (1188 PDFs, 2026-09-12) is the one that exposed it.

    So: never `import dashboard` from extract_core. Anything both sides need lives
    here, and NOTHING here may import streamlit.
"""
import os
import time

import requests

# Same value as the dashboard's EXPORT_SECRET and extract_core's XANO_MACHINE_SECRET.
EXPORT_SECRET = os.environ.get(
    "ANALYTICS_EXPORT_SECRET",
    "ttv_export_da19ae7c3fbcdd2c51747199117a63a33f848ca9",
)


def post_job_status(job_type: str, status: str, user_email: str,
                    result_summary: dict = None, batch_id: str = None) -> bool:
    """
    Post job status to Xano for persistence across logouts.
    Returns True if successful, False otherwise.
    """
    job_endpoint = os.environ.get("XANO_JOB_STATUS_ENDPOINT", "")
    if not job_endpoint:
        return False
    payload = {
        "job_type": job_type,
        "status": status,
        "user_email": user_email,
        "result_summary": result_summary,
        "batch_id": batch_id,
    }
    # Retry on transient 5xx / network errors. This write is load-bearing for
    # batches: it persists the batch_id + pdf_map, and if it's lost the dashboard
    # can't map results back (the batch is orphaned). A Xano 503 blip must not
    # orphan a batch, so retry with backoff. (Upsert endpoint, so a duplicate from
    # a lost-response retry is harmless — resume dedups by batch_id.)
    for i in range(4):
        try:
            r = requests.post(job_endpoint, json=payload,
                              params={"secret": EXPORT_SECRET}, timeout=10)
            if r.status_code == 200:
                return True
            if r.status_code < 500:
                return False  # 4xx won't self-heal
        except Exception:
            pass
        if i < 3:
            time.sleep(min(8, 1.5 * (i + 1)))
    return False
