---
name: project_google_data_empty_shells
description: "Google data cache has \"empty shell\" rows — non-null cache with no real data; current batch never retries them"
metadata: 
  node_type: memory
  type: project
  originSessionId: e65ca513-385d-4e99-9403-13e7992bca2f
---

In WPTP Updated Mappings (Xano table 11), `google_data_cache` being non-null does NOT mean real data was pulled. The `google_data_for_vendor` endpoint (ep 107) always writes a `{name,rating,user_ratings_total,reviews,photos,url,website}` object built from `$google_api_result.response.result.result.*` — so a Places fetch that returned nothing still leaves a cache with all keys null. The reliable "real data" discriminator is `google_data_cache.name != null`.

As of 2026-06-20 (7,774 total vendors): **7,194 real**, **179 never run** (cache null), **401 empty shells** (cache present, name null). Image-remaining (real data but no image_1) = 299.

The `google_data_batch` (ep 122) selects `google_data_cache == null`, and `google_data_for_vendor` skips any row where `google_data_cache != null`. So the 401 empty shells are **never retried** by the normal batch — re-pulling them needs a logic change (deferred: user chose "visibility only" 2026-06-20). Coverage endpoint `admin/google/coverage` (ep 191) uses nested-JSON `cache.name` filters and feeds the dashboard "Coverage" section. Nested-JSON where clauses (`$db.WPTP_Updated_Mappings.google_data_cache.name`) DO work in XanoScript db.query. Related: [[reference_extraction_pipeline]], [[reference_xano_mcp_quirks]].
