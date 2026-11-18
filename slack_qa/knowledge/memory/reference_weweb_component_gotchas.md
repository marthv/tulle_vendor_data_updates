---
name: reference_weweb_component_gotchas
description: "WeWeb traps hit 2026-10-27: element values inside a library component, customCss edits MERGE, X-Branch header silently routes to an old Xano branch, never abbreviate uids in formulas handed to the owner"
metadata:
  node_type: memory
  type: reference
  originSessionId: 197623b5-67b9-4b3f-ae23-e4a6497efc21
  modified: 2026-10-27T23:01:02.407Z
---

- **Inside a library component (popup / no-code component, e.g. the mobile Filter Popup `ed90717a`), an element's exposed value is `context.component?.variables?.['<uid>-value']`, NOT `variables['<uid>-value']`.** The page-style path is undefined there, so every mobile florals slider wrote 0 ("Any") until fixed. Page elements use `variables['<uid>-value']`.
- **`editElement` on `style.default.customCss` MERGES keys, it does not replace the object.** Setting `{userSelect, pointerEvents}` left an old `filter: blur(3px)` in place. To remove a key, set that nested path (`style.default.customCss.filter`) to null.
- **`editFrontendWorkflow` with scope `componentElement` needs `elementUid` as well as `componentUid`**; `open-popup` actions need `waitClosing` at the action root; `states` in addElements JSONL must be objects `{id:'_wwHover', label:'Hover'}`, not strings.
- **Search Vendors collection had an `X-Branch: florals` header** -> every request went to the stale branch copy (api 453, group 25) that ignores new inputs. Symptom: filters change chips but counts don't move, and v1 ep119 request history shows NO matching requests. Check `getApiRequestHistory` with `branch:"florals"` / api 453.
- **Never abbreviate uids (`c8cc3b96-…`) in a formula the owner will paste.** It got pasted literally and silently disabled two bindings. Always give full uids. Collection params bound in JS mode need a leading `return`.
- Owner-chosen look for gated advanced filters (all categories, desktop + mobile popup): sections `filter: blur(3px)`, sections clickable -> popup 48be8a6c + `filter_popup_triggered`; paywall card template green "Filter <category> by price / Included in 'Forever' Plan / Unlock filters". Inventory in `tulletogether/.claude/weweb_florals/ROLLBACK_weweb_2026-10-27.json`. Related: [[project_advanced_filters_paywall]].

- **2026-11-01 (planner-chat 042ef96): a non-ASCII character in a coded component's `<style scoped>` (a `content: "·"` separator, also `A` escapes which the minifier turns into the character) made `weweb build` prepend a BOM to the whole stylesheet (`'﻿@layer ww-style-component{...'`); the browser then dropped EVERY rule and the component rendered unstyled (giant SVGs, no layout). `npx weweb build` prints no error. Rule: no non-ASCII and no CSS escapes in the scoped sheet; draw separators/chevrons with CSS. Check: `grep -c 'ufeff@layer' dist/manager.js` must be 0. Fixed in 341e88b.

- **tulle-tour deploy path (2026-11-01):** the component repo marthv/ww-tulle-tour has its OWN history with files at the root; the tulletogether branch tulle-tour-account-claim mirrors it under components/tulle-tour but cannot be pushed as-is. To ship: `git clone https://github.com/marthv/ww-tulle-tour.git`, edit, `npm i`, `npx weweb build --name=tulle-tour --type=wwobject`, commit, `git push origin HEAD:main`.

**WeWeb atomic style layer beats coded-component root CSS (2026-11-03):** the element's style panel compiles to atomic classes in
cascade layer "atomic", ABOVE the component layer, so e.g. display:block (Display unset) overrides `.root{display:flex}` regardless of
specificity. Any root layout property WeWeb sets (display, height, min-height, flex, width) must be `!important` in the component, or
set in the element's style panel. Harnesses without WeWeb's layers will NOT reproduce it - test on the live page (cookie
`ww-auth-access-token` = a test token) and read getComputedStyle + matching rules incl. @layer.
