"""
🩺 Health tab for the Cloudflare admin page - the data half of the three Streamlit panels:

  1. Endpoint health   endpoint_health.run_check_rows  (read-only probes of every Xano dependency)
  2. Daily report      perf_digest.run_health(write_history=False) + the table-78 history
  3. Perf digest       perf_digest.run(hours)

Same functions the Streamlit tab and the 13:32 UTC health-report-cron call, so the numbers
match. 2 and 3 read Xano request history with XANO_METADATA_TOKEN (must be set on this
service). On-demand runs never write the history table - only the daily cron does.
"""

import os

from . import config


def endpoint_checks():
    import endpoint_health
    rows = endpoint_health.run_check_rows(config.XANO_BASE, config.EXPORT_SECRET)
    writes = [dict(zip(["Endpoint", "Verb", "Gate", "Used by"], w)) for w in endpoint_health.WRITE_DEPS]
    return {"rows": rows, "writes": writes}


def daily_report():
    import perf_digest
    health, _warm, text, _short = perf_digest.run_health(write_history=False)
    return {"markdown": perf_digest._slack_to_markdown(text),
            "endpoints": [{k: v for k, v in h.items() if not k.startswith("_") and isinstance(v, (int, float, str, type(None)))}
                          for h in health]}


def history(days=30):
    import perf_digest
    token = os.environ.get("XANO_METADATA_TOKEN", "")
    if not token:
        raise RuntimeError("XANO_METADATA_TOKEN is not set on the admin API service.")
    table_id = os.environ.get("HEALTH_TABLE_ID", "78")
    rows = perf_digest.load_history(token, table_id, days=days)
    if rows is None:
        raise RuntimeError(f"Could not read history table {table_id}.")
    cols = ["report_date", "query_id", "requests", "failures", "fail_pct", "tokenless",
            "p50_s", "p95_s", "p99_s", "cache_hit_pct"]
    out = [{c: r.get(c) for c in cols} for r in rows]
    out.sort(key=lambda r: (str(r.get("report_date") or ""), -int(r.get("query_id") or 0)), reverse=True)
    return {"rows": out}


def perf(hours=24):
    import perf_digest
    summary, breaches, _slo = perf_digest.run(hours=int(hours))
    eps = [{"path": e["path"], "n": e["n"], "p50": round(e["p50"], 3), "p95": round(e["p95"], 3),
            "p99": round(e["p99"], 3), "max": round(e["max"], 3),
            "cache_pct": e["cache_hit_pct"], "race_401_pct": e["race_401_pct"]}
           for e in sorted(summary["endpoints"].values(), key=lambda x: -x["n"])]
    keep = ("requests_app", "p95", "count_5xx", "race_401_pct", "sessions", "sessions_with_401",
            "race_401", "signedout_401")
    return {"summary": {k: summary.get(k) for k in keep}, "breaches": breaches, "endpoints": eps,
            "hours": int(hours)}
