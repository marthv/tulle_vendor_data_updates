---
name: reference_xano_post_dropped_rows
description: Single-attempt POSTs to Xano silently drop rows under burst 5xx — extraction writers now retry
metadata: 
  node_type: memory
  type: reference
  originSessionId: 9657cfb4-9eb1-4b0a-96ed-3cc944fe3ad1
  modified: 2026-07-27T04:31:05.771Z
---

Xano's public API intermittently returns 502/503 under burst load. A plain single-attempt
`requests.post` therefore **silently drops the row**: the run reports "partial" and the data is
simply gone, with nothing in the logs identifying which rows were lost.

Observed live 2026-07-27: posting 16 package rows in a tight loop, the **first two** failed with
a 5xx while the other 14 succeeded. Uniform payload shape, so it was pure transience — nulls
were not the cause (Xano coerces null → 0/"" on non-nullable columns just fine).

Fixed in `tulle_vendor_data_updates/extract_core.py` with `_post_row(endpoint, payload)`:
4 attempts, exponential backoff with jitter, gives up immediately on a 4xx other than 429
(bad payload won't improve on retry). All three writers now route through it —
`_post_summary`, `_post_pricing_grid`, and the new `_post_packages`. The venue writers had the
same latent bug for as long as they have existed.

Lesson for any new Xano writer here: never single-shot a POST in a loop. Related:
[[reference_xano_connection_drops]], [[project_railway_xano_load_fix]].
