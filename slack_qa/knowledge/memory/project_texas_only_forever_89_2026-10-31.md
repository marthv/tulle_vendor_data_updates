---
name: project_texas_only_forever_89_2026-10-31
description: "Texas-ONLY Forever $89 test (owner 10-31): fn63 LIVE with switch OFF (gated on table-17 Product_ID 13; row 16 parked as 113), Stripe $89 price/link NOT created, WeWeb Membership bindings need a paste + publish; flip = revert_geo_pricing.py --tx-on"
metadata:
  type: project
---

Owner 2026-10-31: Forever $89 for couples whose Wedding_Location_Updated is Texas and ONLY Texas; Texas + anything else keeps
its cohort price ($135 Rest+TX / NY-CA-Intl rules). Built by a subagent the same day, switch OFF.

- Rule: normalised location tokens (split commas, trim, lower) == {"texas"}. Counts 10-31 (table 1, 31,599 users): Texas-only
  3,129 (902 signed up in 90d); Texas+other 508.
- **fn63 pushed LIVE 18:08 UTC** (backup `.claude/xano_backups/*_pre_texas89_2026-10-31.*`; live copy fn63_texas89_LIVE_2026-10-31.xs):
  `$forever_price = 89` only when Texas-only AND table 17 has Product_ID 13; returns texas_only_cohort / texas_tier_live.
  Verified with runWorkspaceFunction on internal test rows: OFF -> every cohort unchanged ($135); armed copy -> Texas-only $89,
  Texas+NY / Texas+OK / NY / MD / none unchanged.
- Table 17 **row 16 = Product_ID 113 (parked)**, Description $89, Stripe_Link `PENDING-STRIPE-LINK-89`.
- **Stripe NOT done** (connector unauthenticated): need an $89 price under Forever product [stripe-id] + payment link
  (metadata.weeks "forever weeks", promo on, auto tax, redirect /purchase-complete?session_id={CHECKOUT_SESSION_ID}, no
  setup_future_usage, no BNPL) -> put `<url>?prefilled_email=` into row 16.
- **WeWeb NOT done (required before flip):** Membership page hard-codes $135 for non-raised users and the button looks up
  PID 7->5. Paste `texas_price_forever.js` into Lifetime Price text af5d09b0 and `texas_button_forever.js` into button
  75a9309e workflow 8605fcc5 action 052a0e9c External URL (files in .claude/xano_backups/), then publish.
- Flip: `python .claude/xano_backups/revert_geo_pricing.py --tx-on` (row 16 113->13); rollback `--tx-off`. Script also has
  DNS fallback + updated FN63_CURRENT.
- Experiment log entry committed f10a945 on main in worktree tulle_dash_exp - PUSH PENDING (owner).
- Baseline Texas-only (gross incl tax): Aug4-Sep30 Forever 0.05/d, $1.82/signup; Oct14-30 ($135) Forever 0.06/d, $2.51/signup.
  ~1 Forever per 2-3 weeks -> a 2-3 week read is directional only. Nov-4 Rest+TX re-read must exclude Texas-only.
- Risks: flipping before the WeWeb publish = page charges $135 while upgrade path quotes $89.
[[project_focus_2026-10-31]] [[project_geo_pricing_bnpl]] [[project_hub_pricing_readout_2026-10-21]]

**2026-11-03 STRIPE DONE (owner authorized):** price [stripe-id] ($89, [stripe-id], metadata arm tx_only_89) +
payment link [stripe-id] https://buy.stripe.com/8x2bJ08vP5becCHgGI0Fi0C (cloned from ny_135 link: promo on,
auto tax, redirect /purchase-complete, card/link/cashapp, no setup_future_usage, metadata weeks "forever weeks"). Table 17 row 16
Stripe_Link set to `<url>?prefilled_email=`, still Product_ID 113 (parked). Backup table17_rows_pre_tx89_link_2026-11-03.json.
Checkout page load NOT verified (session DNS). REMAINING before flip: WeWeb pastes (af5d09b0 + 8605fcc5/052a0e9c) + publish, then --tx-on.
**2026-11-03 WEWEB EDITS IN EDITOR (owner yes), UNPUBLISHED:** Lifetime Price af5d09b0 text + Forever button wf 8605fcc5 action 052a0e9c
External URL now carry texasOnly (PID 13 first). Applied as a diff to the live bindings (old comments kept), read back from the editor = exact.
Node mock test (scratchpad tx89_test.js): row 16 parked -> all 9 location cases identical to today; armed -> Texas-only $89 + $89 link,
Texas+other/NY/other unchanged; price text == link price in 18/18. Full pre-edit backup weweb_membership_texas89_pre_2026-11-03.json.
Side effect: Texas-only visitors now wait for the price list (invisible placeholder) instead of instant $135 - same as NY/CA.
NEXT: owner PUBLISH -> verify live /data/bc8cb393...json has "texasOnly" -> owner yes -> --tx-on.
**2026-11-03 ~06:00 UTC TEXAS $89 LIVE (owner published + "run the code"):** live /data/bc8cb393...json (query-busted) had texasOnly x7,
`[13, 7, 5]` x1, `has(13) ? $89` x1 BEFORE the flip. `revert_geo_pricing.py --tx-on` -> row 16 Product_ID 13, all arms ARMED. Owner
screenshot 00:59 local shows Membership Forever $89 (Texas profile). Rollback = --tx-off. READ: Texas-only Forever/day + $/signup vs
baseline (0.05-0.06/d) after 2-3 wks (directional); log in experiment registry. $89 checkout page itself still not opened by me.
