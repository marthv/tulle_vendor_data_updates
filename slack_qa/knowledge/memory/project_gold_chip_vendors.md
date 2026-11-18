---
name: project_gold_chip_vendors
description: gold_chip curated flag on table 11 + ep119/ep74 - SHIPPED & VERIFIED 2026-09-21; WeWeb filter UI still open and coupled to the cache warmer
metadata: 
  node_type: memory
  type: project
  originSessionId: f61915e0-4f86-4fea-bf5d-33724fe49bc9
  modified: 2026-09-22T02:37:55.353Z
---

**`gold_chip` (bool) on table 11 — LIVE and runtime-verified 2026-09-21.** Curated mark for
"gold chip" vendors so couples can filter to the best ones and we can charge a premium.

**It is CURATED, NOT DERIVED — that is the whole reason for the name.** `flt_*` and `mk_*`
are reserved prefixes meaning *recomputed by fn21 / trigger 5, manual writes get
overwritten* (fn62 declares them non-editable for that reason). `gold_chip` has no prefix
on purpose. **Never give it an auto-writer.** Single write path = the Xano table UI, the
same single-owner rule fn62 applies to `Validated_Data`.

What shipped:
- table 11: `gold_chip` bool, not-null, default false + btree index `78ee9a53`. Shaped on
  `flt_outdoor_ceremony`, the only other real bool.
- **ep119** (cache_v 18 → **19**): input `bool gold_chip_only?`, `$eff_gold` null sentinel,
  `gold_chip ==? $eff_gold` in the main where, `"items.gold_chip"` in **both** output lists.
- **ep74** favourites: `"gold_chip"` — **bare, no `items.` prefix**; it returns a plain list,
  not a paged envelope. Different shape from ep119, easy to get wrong.
- **ep41** needed no edit (whole-row passthrough) — which also means **gold_chip is
  PUBLICLY readable by anyone**, verified live. User accepted this. One line in ep41's
  `|unset:` blocklist would hide it.

Verified live 2026-09-21 (unauth HTTP, unique cache_v per call to dodge the 7100s cache):
field on 60/60 rows; `gold_chip_only=true` → 624 → 2 with zero non-gold leakage; explicit
`false` == unfiltered; **the fallback_all re-query carries the field too** (10/10). Two NY
venues (V1, V3) were flagged as the test and **reverted to false** — nothing is flagged yet.

**STILL OPEN — the WeWeb filter UI, and it is coupled to the cache warmer:**
> ep119 caches on the *resolved input set*; task 8 warms **15 literal URLs**. The moment
> WeWeb starts sending `gold_chip_only` on the default grid — **even sent empty** — every
> warmer URL stops matching and the warmer warms nothing. That is the 2026-09-12 regression
> again. Append the param to all 15 buckets in the same deploy.

Also: **the WeWeb toggle must send `fallback_all=false`.** The fallback re-query strips every
user filter (keeps only Category + Validated_Data + whitelist), so with it on and no gold
matches, non-gold vendors come back. Same for every other filter — not a gold-chip bug.

Deferred, each needing its own edit: ep121 map (output + cache_v 9→10), ep258 type-ahead
(output + a 7th slot in the positional-array lambda + 86400s cache_v), ep246 vendor portal,
fn56/ep231 panel. A `gold_first` sort goes in ep119's `$sort_cfg` chain; a gold facet count
goes in the facet lambda + facet output list.

Tooling: `.claude/xano_backups/apply_goldchip.py` — fetches, backs up, patches by anchored
string surgery with hard assertions, writes, re-reads. `--apply-schema`, `--apply-endpoints`,
`--restore-endpoints`. Backups: `table11_schema_pre_goldchip_2026-09-21.json`,
`ep119_/ep74_pre_goldchip_2026-09-21.xs`. [[reference_xano_mcp_quirks]]
[[project_flt_denorm_aggregates]] [[project_ep119_401_race]] [[project_slack_staged_vendor_edits]]
