---
name: feedback_weweb_formula_no_leading_comment
description: A WeWeb formula (__wwtype "f") that starts with a // comment evaluates falsy - only "js" blocks may carry comments
metadata:
  type: feedback
---
Never put a `//` comment line in a WeWeb binding of `__wwtype: "f"`; it is evaluated as one expression, so the comment swallows it and
the binding returns undefined (element hidden / style dropped). Use `__wwtype: "js"` with `return ...` when a comment is wanted.

**Why:** 2026-11-08 the desktop left-rail 'My wedding' (dda178c5) never showed for anyone; owner found it on the free plan.
**How to apply:** after any editElement with a formula, check the type; read back and, if it has comments, make it `js`. See [[reference_weweb_component_gotchas]].

**2026-11-09 sibling trap - `js` formulas need `return`.** A `__wwtype: "js"` binding is a FUNCTION BODY: a bare expression like `(function(){ ... })()` returns undefined (falsy). Function "Planner Chat - Open Vendor Page" (7c3a1e52-...3a11) had its 'Venue?' if-check written that way, so EVERY Planner card opened the Other Vendors Page (workflow log proved it: category "venue" -> result false). `f` type = expression (no return, no comments); `js` type = statements, MUST `return`. Check execution logs (getWorkflowExecutionLogs) to verify branch results.
