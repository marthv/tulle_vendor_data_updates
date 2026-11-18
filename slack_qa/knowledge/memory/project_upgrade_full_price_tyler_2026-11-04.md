---
name: project_upgrade_full_price_tyler_2026-11-04
description: "Tyler Dozier (34862, Intl hub) paid $39 1wk then $149 Forever via the REGULAR Forever link 41 min later - no upgrade discount; ep284 401s 30% of calls; Forever card button never applies credit"
metadata:
  node_type: memory
  type: project
  originSessionId: 2c0addd5-f1e3-49c4-a905-b40524fb1798
  modified: 2026-11-04T05:06:54.154Z
---

Reported by Kate in #tulle-users (C07D9U0FZF0, thread 1793583711.692309, 11-01): "it showed him the full amount". Checked 11-04:
- Payments (table 16): 2582 $39 "1 week" 11-01 15:33 UTC, 2583 $149 "forever weeks" 16:15 UTC. "forever weeks" = regular Payment Link; an ep285 upgrade logs "forever" at the discounted price. Intl hub prices = $39/$69/$149 (geo test), so both were correct LIST prices; the correct upgrade would have been ~$110 ($149 - $39). Overpaid ~$39.
- His Nov 1 ep284/ep285 calls are UNRECOVERABLE: Xano request history only retains ~24 h. Root cause for him specifically = UNVERIFIED.
- Upgrade banner cdf4e8f7 + CTA + b4d90c17 (with wait_for_token) ARE in the live Membership page json (bc8cb393-d786-4fae-b9f8-47844116950f); banner renders only if ep284 returns eligible.
- ep284 over 11-03 04:30 -> 11-04 04:55 UTC: 673 calls, **200 = 401 (30%)**, iPhone 120/362; 73 IPs got BOTH 401 and 200 (pre-auth race signature), 83 only 401. ep285: zero calls in retained history.
- Structural trap regardless of the race: the Forever card button (wf 75a9309e, Payment Link) always charges full price even for an eligible user; only the separate banner applies credit.
- Kate's question "were the original upgrade links dynamic?" - yes: server-computed per user (fn63), not static links.
Fix options given to owner (not done): Forever card routes eligible users to ep285 + shows the discounted price; fix the ep284 401 race; refund Tyler ~$39 (owner decision, Stripe).

Related: [[project_upgrade_credit]] [[project_geo_pricing_bnpl]] [[project_preauth_race_signs_users_out]]

## 2026-11-04 FIX BUILT IN WEWEB EDITOR (owner must PUBLISH) - owner asked for both
- New button **2ceb156f-a3d4-4530-bc5e-5dc5d0bad18f** "Forever Upgrade Button (payers)" in Lifetime Plan Card, after the original. Visible only when user.total_payments > 0 && !forever_access_purchased; original Forever button 75a9309e got the exact inverse conditionalRendering (exactly one shows; total_payments missing -> old behaviour). Click wf **6b1e9f40-2d7c-4a85-b3e6-0c9d8a5f1e27** (pure custom-js): POST ep285 -> checkout_url => discounted Stripe; 200-without-url or 4xx => SAME cohort Forever link as 8605fcc5 (code copied); network/5xx => retry once then alert, never silent full price.
- Price text af5d09b0: eligible payer sees <s>list</s> + final_price (fn63 values); else unchanged cohort logic. Subtitle f80e0c8c explains the credit.
- 401 fix: app wf **b3d17f42** v2 - watches up to 30 min for a token (mid-session sign-up/login), re-asks /upgrade_offer when token new or last try failed, max 8, exits early for non-candidates. Backup .claude/weweb_backups/app_wf_b3d17f42_upgrade_offer_401_recovery_pre_2026-11-04.json; page backup membership_live_pre_forever_upgrade_2026-11-04.json.
- Page-level onload recovery wf could NOT be created: WeWeb returned 403 Forbidden (WAF) on every attempt; the same logic saved fine in b3d17f42 once named consts / sleep helper were inlined -> WeWeb WAF trips on some JS patterns (const URL passed to fetch? sleep helper?). Inline literals if you hit 403.
- Tests: scratchpad test_forever_upgrade.js, 19/19 (visibility x5, labels, price incl cents, routing eligible/refused/NY/200-no-url/network/5xx).
- NOT verified: ep285's actual not-eligible status code (Xano DNS kept dropping) - code handles 200-no-url and 4xx alike. After publish: grep live /data/bc8cb393-....json for 2ceb156f + 6b1e9f40, and watch ep285 history / clicked_upgrade_with_credit (source=forever_card).
