---
name: project_mobile_discovery_layout_review_2026-11-01
description: Owner asked (11-01) to make mobile Vendor Discovery more accessible / easier to navigate, modelled on Expedia + Airbnb mobile (screenshots 11-01 14:04 / 14:11). Review delivered, nothing built yet.
metadata:
  type: project
---

**Ask (2026-11-01):** owner screenshotted Expedia mobile results + Airbnb mobile home and asked for a mobile
Vendor Discovery pass focused on accessibility + navigation. Current mobile Discovery (screens 13:43/13:51):
first vendor card starts ~680px down (category dropdown, search input, Filters/Search/reset buttons, big
AI-Beta card, count+sort, pagination ABOVE the list). Visible bugs: stray leading bullet before
"International", empty bordered box under the selected-filter chip, names truncated to one line.

**Recommendations given (ranked):** 1 collapse search into one summary pill + move pagination below the
list + shrink the AI card to one line -> first card above the fold; 2 horizontal filter-chip row that
applies on tap (gated chips keep the lock/upsell); 3 cards anchor on price + doc count, 2-line names;
4 persistent bottom tab bar (Discover / Planner / Saved / Budget) = CLAUDE.md Phase 2 mobile-bottom-nav;
5 a11y: 44px targets, icon-button labels, contrast on ink5 text, View Map pill overlap.
**Why:** mobile = ~72-77% of traffic/sign-ups but only 59% of Clicked-to-Pay. Lift unquantified; baseline
to read first = mobile vs desktop card-click rate and filter-use rate in Mixpanel.
Related: [[project_advanced_filters_paywall]], [[project_map_clustering_idea]], [[project_planner_chat_component_2026-10-31]].

**BUILT 2026-11-01 (editor only, UNPUBLISHED, owner publishes):** Discovery page 0d69d71b, backup
`.claude/weweb_discovery_mobile_top_pre_items123_2026-11-01.json` (158 elements, verbatim).
- Item 1: new `Mobile Search Summary Pill` db2ae832 first child of Search and Filters 8b0cfdb5 (label =
  category c8cc3b96 + ' · ' + saved_name_search ce4c0aa9, Filters badge with the same count JS as 35862546,
  chevron bound to mobileSearchOpen). Filters Container 0b635eae + Mobile Container a900edc7 now render on
  the mobile breakpoint only when `mobileSearchOpen` b8dbf5ff is true. Workflows: pill click 6f1c2a84 toggles
  the var; 2nd click workflow 7a2d3b95 on Search Vendors caf3b791 sets it false. The variable (and
  `mobileLoadedVendors` 1abbddfd, 'Show more' append idea) were created earlier the same day by a session
  that crashed before wiring anything; mobileLoadedVendors is still unused.
- Item 2: top pager 178295e2 mobile conditionalRendering now also needs width>600; bottom pager 29a96fed
  mobile customCss conditional-rendering lost its width>600 clause (it was the hidden one on phones).
- Item 3: mobile Rec Entry Banner 4bb59821: text row 1784fa95 + Open Tulle Planner button 5490e79b
  (attributes.id tour-open-planner-b, no tour step on this page references it) hidden on mobile; padding 8px
  10px; input 9016b182 placeholder = 'Ask Tulle Planner about pricing, vendors or your budget…' (Enter still
  navigates to the Planner with ?ask=).
- CORRECTION: the 'empty bordered box under a Right Hand Side chip' in the owner's 13:43 screenshot was the
  WeWeb editor's selection outline of div 6b5a45e4, not a production bug. The stray bullet before
  'International' is still unverified.
- Verify after publish: phone width, first card position vs ~680px before; pill toggles; search collapses
  the controls; pager at the bottom only; AI card = single input line. Baseline/readout: mobile card-click +
  filter-use rates in Mixpanel.
- 15:55 owner: 'updated mobile view is great' but ~100px gap + line above the first card = Command Bar Row
  07d2758a (default height 56px + borderBottom cascading to phones with every child hidden once the top pager
  went). Fixed in editor: mobile height auto, margin 0 5px, borderBottom none. Pager is now at the BOTTOM of
  the list on phones (29a96fed) - owner to confirm by scrolling; if absent, restore 178295e2 on phones.
