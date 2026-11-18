---
name: reference_table11_state_multivalue
description: Table 11 State is a COMMA-SEPARATED list of every state a vendor serves — never group or match it as a single value
metadata: 
  node_type: memory
  type: reference
  originSessionId: 78f47256-3c8d-43c8-b4cf-a251c544931a
  modified: 2026-08-03T01:30:22.973Z
---

Table 11 (`WPTP Updated Mappings`) `State` holds a **comma-separated list**, not one state.
Measured 2026-08-02 across validated Photography+Entertainment vendors: 68 distinct values, of
which 37 contain "New York".

```
New York                                     306
Illinois                                     195
New York, New Jersey                          11
New York, New Jersey, Connecticut              9
New York, International                        8
Connecticut, New Jersey, New York              6
New Jersey, New York, Pennsylvania, Connecticut, Maryland, Delaware, Virginia, ...   1
```

Order is not normalized ("New York, New Jersey" and "Connecticut, New Jersey, New York" both
occur), and the list can include the pseudo-tokens `US` and `International`.

**Why it matters:** this is why ep119/ep121 match state with `includes` (substring), never `==`.
Any new code that groups by, joins on, or equality-matches `State` is silently wrong — it drops
every multi-state vendor from the state they serve and invents junk composite buckets.

**How to apply:** `String(State).split(",").map(trim).filter(nonempty)` and treat the vendor as
belonging to EVERY token. State-scoped aggregates therefore overlap by design; only a national
scope counts each vendor once.

Cost a real bug: the first build of [[project_pe_benchmarks]] grouped on the raw string and
under-counted the New York photography sample by 27% (157 packages vs the 216 ep119's own facet
block reports) while creating ~17 junk buckets like `Photography|New York, New Jersey|...`.
Reconciling published sample sizes against ep119's facet counts is the cheap way to catch this
class of error — they should agree to within the unpriced-package residual (~2%).

See [[project_package_grain_filters]], [[reference_xano_mcp_quirks]].
