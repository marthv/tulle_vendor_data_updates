---
name: project-whole-wedding-planner-2026-11-16
description: "Odessia-style \"Plan my wedding\" Planner flow - built + independently verified, local commits only, awaiting owner go-ahead to push off / enable for test account 14998"
metadata:
  node_type: memory
  type: project
  originSessionId: fead1d70-18ab-4e2e-aa7a-769452b8b5e7
  modified: 2026-11-17T16:48:16.794Z
---

Whole-wedding Planner flow (owner's "Odessia for Tulle" goal, started 2026-11-15 in the session named tulle-agent-team-plan, which the owner closed by accident; recovered 11-16).

**State 2026-11-16:** built and verified offline, NOT pushed, NOT live, never run against live Xano / a model / a browser.
- Server: worktree `C:/Users/vivek/Documents/GitHub/tulle_rec_whole_wedding`, branch `rec-whole-wedding`, commits 1cc23b1, d8479f8, 53c5576 on origin/main of tulle_vendor_data_updates. Flag `REC_WHOLE_WEDDING` (default 0), `REC_WHOLE_WEDDING_PCT`, `REC_WHOLE_WEDDING_USER_IDS`.
- Chat component: worktree `C:/Users/vivek/Documents/GitHub/tulle_chat_whole_wedding`, branch `whole-wedding`, commit 0cfffed (prop `wholeWedding`, default off; events `rec_ww_*`).
- Verifier verdict at 53c5576: safe to push with flag off; safe for one internal test account; not for real couples yet. Suite 1226 passed + 1 known failure (test_next_step_chips).

**UPDATE 11-16 ~14:45 UTC (owner said yes):** server pushed to main (53c5576, then 5b2d593) and deployed; Railway rec-engine vars set REC_WHOLE_WEDDING=1, REC_WHOLE_WEDDING_PCT=0, REC_WHOLE_WEDDING_USER_IDS=14998; component pushed to ww-planner-chat main 0cfffed (owner must ACTIVATE, set `wholeWedding` on, publish). Live assignment UNVERIFIED: test token expired (owner runs `python .claude/refresh_test_token.py`). Not in experiment registry yet (internal account only) - add an entry before any real-couple %. Round-2 fixes in progress locally on `rec-whole-wedding` (branch is 1 behind main: rebase before pushing). ep177 secret-gated 11-16 (backup `.claude/xano_backups/ep177_pre_secret_gate_2026-11-16.xs`; 403 without / 200 with secret verified; pipeline run after the gate NOT yet observed). Texting: owner said go; plan = Twilio toll-free number + verification first.

**UPDATE 11-17 05:45 UTC:** owner activated component + tested in browser; said "Tulle holds no price for them yet" is false (listed = Tulle has their pricing PDF; only the extracted number is missing for flowers/planner/beauty/catering) and asked for images/icons. Rounds 3+4 pushed with owner yes: server main 7242add (deployed, live check on 14998 OK: PDF wording correct, pdfs=1 on 4 estimate rows, 2.2s, chat id recorded), component ww-planner-chat 9d7e274 (owner must activate again). Live finding: row `image` is EMPTY for all 7 picks, so only category icons show - investigate listing image field. Open for real couples: `text_for` (whole_wedding.py ~1163) not reader-aware for free/no-views; venue PDF claim rests on fee-range proxy; ep93 outage = +3s per proposal. Owner undecided: run extraction on flowers/planner/beauty PDFs for real prices (model spend).

**UPDATE 11-17 ~15:05 UTC - PUBLISH BLOCKER:** with component 0cfffed/9d7e274 active, Profile page "Save Changes" -> Discovery makes the floating Ask Tulle bar vanish in the WeWeb editor (reload fixes; 23d878c does not do it; published site fine). NOT reproduced offline (scratchpad bar_harness: real Edge, real chats in real/rec.json, suppress/remount/popup variants). Pushed 42dfd3c = 9d7e274 + bar self-check (console.warn '[tulle-bar] {...}', window.__tulleBar, track rec_bar_missing, auto re-attach/re-render). Owner to activate 42dfd3c, repeat the save, report bar back? + the [tulle-bar] console line. Owner will NOT publish until resolved; older version is the safe fallback. Also: Back to Planner button added in editor (rollback .claude/xano_backups/weweb_my_wedding_pre_back_to_planner_2026-11-17.json).

**UPDATE 11-17 16:47 UTC - LIVE FOR EVERY COUPLE** (owner "open it up to every single couple"): REC_WHOLE_WEDDING_PCT=100, WeWeb publish cacheVersion 1398 verified (ww-planner-chat 03d4ff5 incl. Tools "Plan my wedding" + bar self-check; ww-price-report 888aaf3 replaces PI card; pixel; /lp pages; Back to Planner; My wedding link sends vendor_idx). Server 3780d11 (locked sentence per reader) + d1483bc (photos from image_1 file record; live: 2 of 7 rows had photos). Registry EXP-2026-11-whole-wedding-planner, call 2026-12-01. Bar vanish root cause still unknown - watch Mixpanel rec_bar_missing.

**Open before real couples:** replace keeps couple-typed contact info on the swapped vendor (`whole_wedding.py` `flow_owned`); booking a vendor already "considering" leaves two rows; malformed swap / repeated identical request is charged; add-all lock is per process.
**Owner decisions pending:** proposal uses NO model call (code + regex reactions) - keep or add a model; cheaper/nicer blocked for free couples (builder's choice); one Planner pick per category in My wedding.
**First live check on test account:** does Xano table 79 drop or reject the unknown `ww_arm` field (rejected = proposals uncounted).

Related from the same session: Claude connector live for 14998 only at mcp.tulletogether.app/mcp, waiting on owner publishing `/connect-claude`; ep93 now requires sign-in (backup `.claude/xano_backups/ep93_pre_auth_user_2026-11-15.xs`), ep142/258/177 still anonymous; texting channel scoped only (carrier registration draft offered, unanswered). See [[project-touchbase-2026-11-14]].
