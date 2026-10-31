//   Computes the Lifetime upgrade offer for one user: how much they paid for the access they are
//   on RIGHT NOW, what that leaves to pay, and whether the offer is still open.
// 
//   SINGLE SOURCE OF TRUTH ON PURPOSE. GET /upgrade_offer (what the page shows),
//   POST /upgrade_session (what Stripe actually charges) and admin/customer_snapshot (what
//   /tulle view-as reports) all call this. If the rule lived in two places they would eventually
//   disagree and we would show a customer one price and charge another - the worst possible bug
//   for this feature.
// 
//   RULE (v2, 2026-09-11). The credit is the money paid for the access period the customer is
//   currently inside - not a rolling sum of everything they have ever spent. Payments are walked
//   oldest to newest and chained into access periods exactly the way ep30 webhooks builds them
//   (each payment extends from the current expiry, or from its own timestamp if access had
//   lapsed). A gap RESETS the credit. So a repeat buyer who lets a pass expire and comes back
//   starts again from full price, and only the still-live period is ever credited.
// 
//   v1 summed every non-roadmap payment in a 120-day window. That let Isabelle (13023, five
//   passes bought over five months, $190 lifetime) hit the credit cap and be offered Forever for
//   $15 - i.e. it capped a repeat buyer's lifetime value at the Forever price. It also credited
//   money paid for access that had long since expired, including for people whose CURRENT access
//   came from submitting a pricing PDF, which costs them nothing. Both are fixed by tying the
//   credit to the live access period.
// 
//   STRICT LIVENESS, no grace period: the offer disappears when the paid coverage runs out.
//   v1's 14-day floor from the last payment is deliberately gone.
// 
//   PRICES ARE SERVER-SIDE. Never accept a price or a credit from the client.
//   Verified against the live payment log 2026-09-06: forever = $129, 4 weeks = $55, 1 week = $30.
//   Geo tiers (Oct 2026): NY/CA/Intl $149, then NY alone $135 from 2026-10-06 - see the blocks below.
//   If the Lifetime price changes, change it HERE and nowhere else.
// 
//   Roadmap rows are excluded - a Budget Roadmap purchase is a different product and must not
//   discount a membership.
// 
//   Backup of v1: .claude/xano_backups/fn63_v1_120day_sum_2026-09-11.xs
function compute_upgrade_offer {
  input {
    int user_id
  }

  stack {
    var $forever_price {
      value = 129
    }
  
    // Stripe fees make a near-zero charge pointless, and it keeps the offer feeling like a
    // discount rather than a giveaway.
    var $min_charge {
      value = 15
    }
  
    db.get user {
      field_name = "id"
      field_value = $input.user_id
    } as $me
  
    precondition ($me != null) {
      error_type = "notfound"
      error = "User not found."
    }
  
    var $now_ms {
      value = now
    }
  
    //  GEO PRICING EXPERIMENT (Oct 2026). New York / California / International pay $149;
    //  everyone else, INCLUDING TEXAS, stays at $129. Texas is the BNPL control arm - it gets
    //  buy-now-pay-later at the unchanged price so the BNPL effect can be separated from the
    //  price effect. Do not "tidy" Texas into the raised list.
    // 
    //  Closes an arbitrage: without this a New York user buys 1 week at $30 and upgrades
    //  against a $129 list price, landing near $99 instead of $149.
    // 
    //  DATE GATE. 1790827200000 = 2026-10-01 00:00 America/New_York. Before that instant this whole
    //  block is a NO-OP and everyone keeps $129, which is why it is safe to have live in
    //  production ahead of launch - nothing has to be remembered on the day.
    //  geo_experiment_live / geo_raised_cohort are returned in the response only so the gate
    //  and the cohort rule stay testable without waiting for the clock.
    // 
    //  KILL SWITCH, no publish and no editor needed:
    //    python .claude/xano_backups/revert_geo_pricing.py
    var $geo_live_from {
      value = 1790827200000
    }
  
    var $geo_experiment_live {
      value = false
    }
  
    conditional {
      if ($now_ms >= $geo_live_from) {
        var.update $geo_experiment_live {
          value = true
        }
      }
    }
  
    // CONTAINS, NOT EQUALITY: some legacy rows pack several states into ONE list item, e.g.
    // "New York,Pennsylvania". An == test silently misses those users.
    api.lambda {
      code = """
          const RAISED = ['new york', 'california', 'international'];
          const raw = ($var.me || {}).Wedding_Location_Updated;
          const list = Array.isArray(raw) ? raw : (raw ? [raw] : []);
          const tokens = [];
          for (let i = 0; i < list.length; i++) {
            String(list[i] == null ? '' : list[i]).split(',').forEach(function (p) {
              const t = p.trim().toLowerCase();
              if (t) tokens.push(t);
            });
          }
          return tokens.some(function (t) { return RAISED.indexOf(t) !== -1; });
        """
    } as $is_raised_cohort
  
    conditional {
      if ($geo_experiment_live && $is_raised_cohort) {
        var.update $forever_price {
          value = 149
        }
      }
    }
  
    // NY TIER (2026-10-06). New York pays $135 Forever (1w $35 / 4w $60 on the page), CA and
    // International stay at $149. LIVE ONLY WHILE table 17 holds Product_ID 12 - the same row the
    // WeWeb price text and Stripe button look up - so one table-17 write launches or parks NY on
    // the page AND here together. Parked or missing row -> NY stays on $149, i.e. today's state.
    // Table name has spaces (no `where`), and it is ~12 rows, so read it whole.
    api.lambda {
      code = """
          const raw = ($var.me || {}).Wedding_Location_Updated;
          const list = Array.isArray(raw) ? raw : (raw ? [raw] : []);
          for (let i = 0; i < list.length; i++) {
            const parts = String(list[i] == null ? '' : list[i]).split(',');
            for (let j = 0; j < parts.length; j++) {
              if (parts[j].trim().toLowerCase() === 'new york') return true;
            }
          }
          return false;
        """
    } as $is_ny_cohort
  
    db.query "DONATION - Payments" {
      return = {type: "list"}
      output = ["Product_ID", "Stripe_Link"]
    } as $price_rows
  
    api.lambda {
      code = "  return ($var.price_rows || []).some(function (r) { return Number(r.Product_ID) === 12; });"
    } as $ny_tier_live
  
    conditional {
      if ($geo_experiment_live && $is_ny_cohort && $ny_tier_live) {
        var.update $forever_price {
          value = 135
        }
      }
    }
  
    // BASELINE PRICE MOVE (2026-10-14). Everyone outside NY / CA / International (Texas included)
    // moves from $129 to the NY $135 Forever price. LIVE ONLY WHILE table 17 Product_ID 5 points at
    // the $135 NY Forever link (14AaEW27r9rucCH3TW0Fi0A) - the same row the pricing-page button uses -
    // so pointing that row back at the old $129 link reverts this too, with no Xano push.
    api.lambda {
      code = "  return ($var.price_rows || []).some(function (r) { return Number(r.Product_ID) === 5 && String(r.Stripe_Link || '').indexOf('14AaEW27r9rucCH3TW0Fi0A') !== -1; });"
    } as $baseline_raised_live
  
    conditional {
      if ($baseline_raised_live && $forever_price == 129) {
        var.update $forever_price {
          value = 135
        }
      }
    }
  
    //  TEXAS-ONLY TIER (2026-10-31, owner request). A couple whose wedding location is Texas and
    //  ONLY Texas pays $89 Forever. Texas plus anything else (Texas + New York, Texas + Oklahoma,
    //  ...) is NOT this cohort and keeps whatever its cohort pays today. The rule is on the
    //  NORMALISED set of states: split comma-packed entries, trim, lower-case, drop empties, then
    //  every remaining token must be exactly "texas" (and there must be at least one).
    // 
    //  LIVE ONLY WHILE table 17 holds Product_ID 13 - the row the WeWeb Forever price text and
    //  button look up for this cohort. Parked (Product_ID 113) or missing row -> this block is a
    //  no-op and Texas-only stays on the baseline price. One table-17 write switches the page
    //  AND the upgrade path together, no WeWeb publish and no Xano push.
    //  Placed LAST so it is the final word on $forever_price for this cohort.
    //  texas_only_cohort / texas_tier_live are returned so the rule is testable from outside.
    api.lambda {
      code = """
          const raw = ($var.me || {}).Wedding_Location_Updated;
          const list = Array.isArray(raw) ? raw : (raw ? [raw] : []);
          const tokens = [];
          for (let i = 0; i < list.length; i++) {
            const parts = String(list[i] == null ? '' : list[i]).split(',');
            for (let j = 0; j < parts.length; j++) {
              const t = parts[j].trim().toLowerCase();
              if (t) tokens.push(t);
            }
          }
          if (tokens.length === 0) return false;
          for (let k = 0; k < tokens.length; k++) {
            if (tokens[k] !== 'texas') return false;
          }
          return true;
        """
    } as $is_texas_only
  
    api.lambda {
      code = "  return ($var.price_rows || []).some(function (r) { return Number(r.Product_ID) === 13; });"
    } as $texas_tier_live
  
    conditional {
      if ($texas_tier_live && $is_texas_only) {
        var.update $forever_price {
          value = 89
        }
      }
    }
  
    var $uid_text {
      value = $input.user_id|to_text
    }
  
    var $is_forever {
      value = false
    }
  
    conditional {
      if ($me.forever_access_purchased) {
        var.update $is_forever {
          value = true
        }
      }
    }
  
    // Whole log, trimmed. The table name contains spaces so it cannot be filtered in a `where`
    // clause - see ep216 for the full explanation. ~1,200 rows at ~1 payment/day.
    db.query "DONATION - Payment Log" {
      return = {type: "list"}
      output = ["id", "Client_Reference_ID", "Type", "Amount", "Time_of_Payment"]
    } as $all_payments
  
    // Done in a lambda rather than with chained filters: this is a stateful walk over an ordered
    // list, which the filter forms cannot express at all.
    api.lambda {
      code = """
          const uid = String($var.uid_text);
          const rows = $var.all_payments || [];
          const now = Number($var.now_ms);
          const u = $var.me || {};
          const SKIP = ['budget_roadmap', 'budget_roadmap_orphan', 'roadmap'];
        
          // MIRRORS ep30 webhooks EXACTLY (its add_secs_to_timestamp values, in ms). ep30 is what
          // actually grants access, so any other numbers here would reconstruct access periods
          // that never existed. Note the marketing names are generous: "1 week" is 8 days.
          const DUR = {
            '1 week':    691200000,
            '1 weeks':   691200000,
            '2 weeks':  1296000000,
            '4 weeks':  2505600000,
            '6 weeks':  3715200000,
            '12 weeks': 7344000000,
            '24 weeks': 14601600000
          };
        
          // Client_Reference_ID is TEXT and legacy Make-era rows carry leading whitespace.
          const mine = rows.filter(function (r) {
            return String(r.Client_Reference_ID == null ? '' : r.Client_Reference_ID).trim() === uid
              && SKIP.indexOf(r.Type) === -1;
          });
        
          mine.sort(function (a, b) {
            return (Number(a.Time_of_Payment) || 0) - (Number(b.Time_of_Payment) || 0);
          });
        
          let lastPaymentAt = 0;
          let firstPaymentAt = 0;
        
          // The walk. cursor = end of the access period built so far.
          let cursor = 0;
          let runTotal = 0;
          let runCount = 0;
          let runStart = 0;
        
          for (let i = 0; i < mine.length; i++) {
            const t = Number(mine[i].Time_of_Payment) || 0;
            const amt = Number(mine[i].Amount) || 0;
            if (t > lastPaymentAt) lastPaymentAt = t;
            if (t > 0 && (firstPaymentAt === 0 || t < firstPaymentAt)) firstPaymentAt = t;
        
            const type = String(mine[i].Type == null ? '' : mine[i].Type).toLowerCase().trim();
            const dur = DUR[type];
        
            // Forever rows, the two stray Amount:0 'database' rows and anything with an
            // unrecognised tier buy no timed coverage, so they cannot start or extend a period.
            // A comped $0 row of a KNOWN tier still chains - it adds no credit but it did grant
            // access, and dropping it would falsely look like a lapse.
            if (!t || !dur) continue;
        
            if (t > cursor) {
              // Access had lapsed before this payment: previous period is dead, credit resets.
              runTotal = 0;
              runCount = 0;
              runStart = t;
              cursor = t + dur;
            } else {
              // Top-up while still live: extends from the existing expiry, same as ep30.
              cursor = cursor + dur;
            }
        
            runTotal += amt;
            runCount += 1;
          }
        
          // date_until_access is a DATE column read back as "YYYY-MM-DD" (forever sentinel
          // "3000-01-01"). Parsed at END of day, as ep299 does, so the column's truncation
          // cannot shave a day off a genuine payer. Numbers are accepted too, defensively.
          const raw = u.date_until_access;
          let until = null;
          if (typeof raw === 'number') {
            until = raw;
          } else if (raw) {
            const parsed = Date.parse(String(raw).slice(0, 10) + 'T23:59:59Z');
            until = isNaN(parsed) ? null : parsed;
          }
        
          return {
            credit_raw: Math.round(runTotal * 100) / 100,
            credit_payment_count: runCount,
            coverage_started_at: runStart,
            coverage_ends_at: cursor,
            access_live: until !== null && until > now,
            last_payment_at: lastPaymentAt,
            first_payment_at: firstPaymentAt,
            payment_count: mine.length
          };
        """
    } as $agg
  
    var $eligible {
      value = false
    }
  
    var $credit {
      value = 0
    }
  
    // Four gates, each closing a different hole:
    //   is_forever       - someone who already owns Lifetime has nothing to upgrade to.
    //   credit_raw       - they actually paid for this period (a $0 comp earns no discount).
    //   coverage_ends_at - the pass they PAID for is still running. This is what excludes access
    //                      granted by a PDF submission or by hand, which costs them nothing.
    //   access_live      - and the account has not been revoked since.
    conditional {
      if ($is_forever == false && $agg.credit_raw > 0 && $agg.coverage_ends_at > $now_ms && $agg.access_live) {
        var.update $eligible {
          value = true
        }
      
        var.update $credit {
          value = $agg.credit_raw
        }
      }
    }
  
    // Unreachable now that only one live period is credited (max $55 today), but kept as a guard
    // so a future long tier can never produce a free or negative Forever.
    var $max_credit {
      value = $forever_price - $min_charge
    }
  
    conditional {
      if ($credit > $max_credit) {
        var.update $credit {
          value = $max_credit
        }
      }
    }
  
    var $final_price {
      value = $forever_price - $credit
    }
  
    // Stripe is charged in cents and must never disagree with the displayed dollars.
    var $final_price_cents_raw {
      value = $final_price * 100
    }
  
    var $final_price_cents {
      value = $final_price_cents_raw|round
    }
  }

  response = {
    eligible            : $eligible
    credit              : $credit
    forever_price       : $forever_price
    final_price         : $final_price
    final_price_cents   : $final_price_cents
    paid_in_window      : $agg.credit_raw
    payment_count       : $agg.payment_count
    credit_payment_count: $agg.credit_payment_count
    first_payment_at    : $agg.first_payment_at
    last_payment_at     : $agg.last_payment_at
    coverage_started_at : $agg.coverage_started_at
    window_ends_at      : $agg.coverage_ends_at
    already_forever     : $is_forever
    geo_experiment_live : $geo_experiment_live
    geo_raised_cohort   : $is_raised_cohort
    ny_cohort           : $is_ny_cohort
    ny_tier_live        : $ny_tier_live
    texas_only_cohort   : $is_texas_only
    texas_tier_live     : $texas_tier_live
  }
}