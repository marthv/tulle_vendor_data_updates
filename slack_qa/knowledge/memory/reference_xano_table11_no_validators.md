---
name: reference_xano_table11_no_validators
description: "table 11 has NO column validators, so the updateTableSchema strip-validators trap does not apply there - and how to check any other table"
metadata: 
  node_type: memory
  type: reference
  originSessionId: f61915e0-4f86-4fea-bf5d-33724fe49bc9
  modified: 2026-09-22T02:38:01.168Z
---

**There is no "add one column" tool in the Xano MCP.** `updateTableSchema` REPLACES the whole
schema, which is why the strip-validators trap in [[reference_xano_mcp_quirks]] matters.

**How to check before a schema write:** `getTableSchema` (the FULL-table read) *does* return
`validators` — verified 2026-09-21 by reading table 1, which shows them on ~20 columns
(`email` → `{"trim":true,"lower":true}`, `password` → `{min, minAlpha, minDigit}`). So the
read is trustworthy: no `validators` key in the output means there genuinely are none.

**Measured 2026-09-21: table 11 (WPTP Updated Mappings) has ZERO validators on all 52
columns.** So a full-schema replace there cannot strip anything. Re-check per table — this
does NOT generalise; table 1 would lose a lot.

Also corrected 2026-09-21: **table 11 had 20 indexes, not 21** (an earlier count was off by
one). 21 now, after `gold_chip`'s btree `78ee9a53`. `createBtreeIndex` is additive — it did
not disturb the composites (`Category/Validated_Data/id` `9e94d970`, the `search` index
`31e46fd2`, `lat/lng` `918f78bb`).

Safe pattern, used by `.claude/xano_backups/apply_goldchip.py`: read the live schema, write
it to disk verbatim as the rollback artifact, append the one new column dict in code, write
back, then re-read and assert every original column name survives. **Never retype the column
list by hand** — that is the real risk, not the tool. [[project_gold_chip_vendors]]
