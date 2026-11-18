---
name: project_pi_review_feedback
description: "Des + Kate's 2026-08-13 review of the venue Pricing Intelligence panel — which complaints were real, which two diagnoses were WRONG, and what shipped in response"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7f7fe7be-71cf-43ae-84a1-55f51168cd57
  modified: 2026-08-14T05:08:45.818Z
---

Des and Kate reviewed the venue Pricing Intelligence panel in Slack on 2026-08-13. Most of it was
accurate. **Two diagnoses were wrong, and implementing either as written would have made the numbers
worse.** Read this before acting on any similar report about this panel.

## Wrong, do not act on as written

**"These numbers are based on the most expensive package during peak season" (Kate) — FALSE.**
fn54 coalesces **field-wise, first-non-null** (Primary PDF → Supplemental → Past season, then source
precision, then recency); its docstring says *"Never array_max."* The only `max` in the build is
seated capacity. The real upward bias is the **slot, not the package**: fn54 filters t37 to
`day ∈ {saturday, everyday}` and `meal ∈ {dinner, ''}`, and every t36 column it reads is
`*_on_a_Peak_Season_Saturday`. So the accurate disclaimer is **"peak-season Saturday dinner"**, which
is what `headline_prefix` now says. Kate's cheaper "$450 package" at 1 Hotel Brooklyn Bridge is
almost certainly a lunch or non-Saturday rate, deliberately filtered out. Secondary bias: ranged /
prose cells (`'$4,000-$6,500'`, `'Inquire Within'`) are rejected by `numOf()`, so the **market median
stands in rather than the low end of the venue's own range**.

**"The Bowery double-counts the venue fee, and we don't have the per-person F&B" (Kate) — BOTH
FALSE for that venue.** Table 62 row 944 (`V425|boweryterrace2ndflooreventspace`) has
`pp_fb 425 · fb_min 100000 (not per-person) · venue_fee 20000 · has_venue_fee TRUE · ceremony_fee 25
per_person · service_pct 22 · guest_min 125 · capacity 200 · contract_count 2`. The $20,000 rental is
the venue's **own published figure** — that is why it badges rather than saying "Market estimate".
And **`FB_Spend_Min_Type = "Overall Min Spend"` means TOTAL, not all-inclusive** — it is the binary
opposite of `"Per Person Min"` (6,337 vs 5,822 rows workspace-wide, no third value). The source PDF
itemises the rental separately (`Additional_Fees`: *"Room rental fee of $15,000; Base production
package of $2,000…"*), so adding it on top is correct.
**Her proposed fix — pull minimums out of the F&B slot into a disclaimer — would understate the
Bowery by ~$68,000 at 75 guests.** The minimum IS the binding spend. Keep it in the math, explain it
in the copy. (Caveat still worth holding: "Overall Min Spend" is ambiguous English for an F&B-only
value; if some PDF used it to mean a true all-in floor we would overstate.)

## Right, and fixed in fn56 v5 / fn54 v2

- **"Sometimes the guest stepper does nothing" (Des) — correct, and not a bug.**
  `food = max(pp × g, fb_min)`. 1 Hotel South Beach break-even 218 guests (cap 250); Bowery 236 vs
  cap 200, so that one can never move. Now explained by `guest_sensitivity.note`.
- **"On the high side" vs "well above average" (Kate)** — same scale, but only the bottom two rungs
  carried the place suffix. All four are now scoped, `high` renamed "Above average in X".
- **"What you would pay" is unscoped (Kate)** — right, though her wording ("venue, food and
  beverage") still under-describes it; the total also includes ceremony and service charge.
- **"Add a tag that says comes from the venue" (Des)** — `source: 'venue' | 'market'` was already in
  the payload; only the negative case rendered. WeWeb-side.
- **Entertainment range $900–$9,500 too broad (Kate)** — right, it spans DJ through Live Band.
  ep207 / table 59; should lead with the median for the primary service type. NOT DONE.
- Mobile space chips cut off (Des) — WeWeb CSS. NOT DONE.

## Found while checking their claims — bigger than the feedback

**The total omits gratuity and tax entirely.** `total = (food + cer + vf) × (1 + service_pct/100)`.
1 Hotel Brooklyn Bridge's `service_pct = 9` is the *administration fee only*; its PDFs say
*"15% gratuity; 9% administration fee; 8.875% state tax"*. `tax_pct` sat in table 62 unused and
gratuity has no column anywhere. So "What you would pay / Based entirely on this venue's own
pricing" understated that venue by ~24 points. **Resolved as named excluded display lines, total
definition unchanged** (user's call, so table 63 benchmarks need no rebuild).

**Other open items:** V1 has `flt_space_count: 13` but ONE table-62 row, because every one of its
13 t36 rows has a blank `Venue_Space_Name` — a 13-space hotel collapsed to one chip. And the Bowery
ceremony line reportedly rendered **$1,071** where 25 × 75 = $1,875; the rendered service charge
($26,813 = 22% × 121,875) is self-consistent with $1,875, so if $1,071 is real the line item and the
total disagree. Read off a low-res thumbnail — verify on the live page before chasing it.

Related: [[project_pricing_intelligence_spaces]], [[project_partner_vendors]], [[project_pe_benchmarks]].
