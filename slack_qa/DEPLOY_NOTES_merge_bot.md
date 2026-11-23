# Tulle Bot merges: deploy notes (2026-11-23)

Branch `merge-bot-1122`, on top of origin/main 1ac2b0f. **Nothing is pushed, deployed or set.** Every step below needs the
owner's yes. Service: `tulle-slack-qa` in the Railway project "tulle admin dash". Xano: ep578 `POST /vendor/merge` v5 (live
2026-11-23 04:48:21 UTC) does the writes; this bot only calls it after an approver's click.

## ep578 version
Written against ep578 **v5** (live 2026-11-23 04:48:21 UTC): newer pricing year becomes Primary. The bot compares Xano's own
plan (`primary_choice`) with the owner's rule on every card; if they ever disagree the card says "Can't be merged as it stands"
and Approve sends nothing.

## What is and isn't proven
- Proven: 1,224 tests pass (QA's 237 included) locally; filtered reads, the price check and `mode=preview` were run against live Xano (read-only).
- NOT proven until deployed: anything in Slack itself; the quickjs-ng install on Railway; whether the service's
  `XANO_METADATA_TOKEN` may read function source (if not, every card says "couldn't compute"); Google Drive access.
- No apply or undo has been run by this code. The first one is the owner's click on the first pilot card.

## Environment variables on tulle-slack-qa
| Variable | Value | Note |
|---|---|---|
| `DEDUPE_DRY_RUN` | `1` | **Set / confirm this FIRST.** Unset, empty or a typo also means dry run; only 0/false/no/off turns it off |
| `XANO_MERGE_URL` | `https://xqtb-2ma7-ijfy.n7e.xano.io/api:aow91bcd/vendor/merge` | without it there is no preview, so nothing can be approved for real |
| `XANO_MERGE_SECRET` | ep578's secret, sealed | owner pastes it (local copy `~/.tulle_merge_secret`). QA asked for it to be rotated: rotate in Xano and here together |
| `DEDUPE_CHANNEL` | `C06UB2V74GG` (#growth-strategy) | already listed on the service; check the value |
| `QA_APPROVERS` | owner, Des, Kate (see DEPLOY_NOTES_v2.md) | already listed; check the value |
| `XANO_MERGE_TIMEOUT` | leave unset | default 300 s |
| `XANO_MERGE_RECHECK_GRACE` | leave unset | default 120 s |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | **already set** on the service (name seen 2026-11-23; value not read) | Drive md5 and text reading in the PDF check starts as soon as this code is deployed. The code asks for read-only Drive; whether the account can open the files is UNVERIFIED |
| `DEDUPE_SCHEDULED_SCAN` | leave unset (on) | owner 2026-11-23: the scheduled scan stays on, as today; `0` switches it off |
| `DEDUPE_MAX_CARDS` | leave unset (10) | cards per scan, as today; `0` = no scan cards at all. The pilot itself runs by name |

Already set and used as is: `XANO_METADATA_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `STATE_DIR=/data`, `QA_SCHEDULER`.

## Order
1. Confirm `DEDUPE_DRY_RUN=1`. Do not go on until it reads 1. `XANO_MERGE_URL` / `XANO_MERGE_SECRET` stay unset for the push.
   The scheduled scan stays on (owner): the weekly one posts up to 10 cards Monday 08:00 UTC. Cards the old version posts (or has
   posted) are voided when someone presses Approve on this version.
2. Push `merge-bot-1122` to main, away from 13:30-13:45 UTC (a push to main can also redeploy batch-ingest, batch-worker-cron
   and health-report-cron). Not while a merge is running.
3. Build log: `quickjs-ng`, `pypdf`, `google-auth` installed. `/health` answers. `@Tulle Bot PDFs for V105` answers.
4. Set `XANO_MERGE_URL`, then `XANO_MERGE_SECRET` (dry run is still on).
5. Merge cards made before this version can't be approved any more (the card says to ask for a fresh one) and don't block a new
   card. In #growth-strategy ask for one pilot pair: `@Tulle Bot merge V… into V…`. The card must gain Approve within about 2 minutes
   and show today's and predicted prices. If it says "couldn't compute", the token can't read function source, quickjs is
   missing, or Xano was slow: press Check price again, then look at the logs for "price".
6. Approve it once in dry run: it must say "dry run, nothing written". Check Xano table 72 has no new `vendor_merge` row.
7. Owner's yes to go live: set `DEDUPE_DRY_RUN=0` (the service restarts). A card approved in dry run gets "Apply for real".
8. First merge: one pair, owner clicks. Independent before/after check, then Undo, then the undo check (PASS_BAR section 1).
   Only then the rest of the pilot. One merge at a time.

## What the owner sees in Slack
- Card appears without Approve and says it is working out the price; Approve comes when it is done (about 1-2 minutes; a scan of
  20 cards fills in one by one, estimate 10-25 minutes).
- Top to bottom: headline; red banner on any mismatch; "Can't be merged" stops; which PDF becomes the main one, with both
  years; the listings side by side with links; why; what
  happens; "Needs your OK" questions (hidden duplicate, unhiding); PDF check with a choice per duplicate
  (exact copies pre-selected); price (loud only when the headline moves 10% or more, appears or disappears, or can't be computed).
- Buttons: Approve and apply, Reject, Keep the duplicate instead, Details, sometimes Run PDF check again / Check price again.
- On Approve: price re-checked; if anything moved the card is redrawn and nothing is sent. A merge takes up to a few minutes.
- Approve only runs exactly the card that was clicked: if anyone changed direction, PDF choices, OKs or the price meanwhile, the
  card is shown again and nothing is sent. Suggested pairs must still pass the scan's rules on live rows; pairs asked for by name
  must pass state, distance, Google listing, hidden (with an OK) and gold.
- After: Applied (Undo merge), "Merged, but something needs a look", part-way (Resume / Undo), or "couldn't confirm" (Re-check
  only). Undo that couldn't put everything back offers Undo again.
- The gold-chip listing is always the one kept when only one has it. "merge A into B" or Keep the duplicate instead that would
  retire it is refused in plain words.
- The main-PDF line uses our stored year labels; it says what years are printed in the PDF, or that it wasn't read.
- A later merge into the same listing works once the earlier one finished. Undo goes newest first: an older merge can't be
  undone while a later one shares a listing. Asking for two of a bigger group suggests merging them all in one request (max 3).
- Every message for every Xano reply: `merge_bot_2026-11-22/pdfdup/CARD_TEXTS.txt` (tulletogether/.claude).

## Roll back
- **Stop merges now:** `DEDUPE_DRY_RUN=1` (takes effect on the restart Railway does for a variable change). Undo is also a dry
  run then; to undo a merge, leave it at 0 and press Undo merge on its card first.
- **Undo one merge:** Undo merge on its card (exact, from Xano's backup in table 72). Conflicts are listed and left alone.
- **Cut the bot off from ep578:** remove `XANO_MERGE_SECRET`. `QA_ENABLED=0` stops the whole bot.
- **Code:** redeploy the previous Railway deployment (1ac2b0f). Cards made by this version stay in `/data/proposals.json`; the
  old code ignores the new fields, but its Approve would send no `expected_state`, which ep578 refuses.
- Don't deploy, restart or change variables while a merge is running. **Raise the service's draining time from 180 s to at least
  360 s** (a merge can take 300 s) before the first live merge. If a restart still catches one, the card sits on "Applying…" and
  becomes "Re-check" after 10 minutes on its own (checked at start-up and every minute, or on the next click): press Re-check,
  which asks Xano what really happened. Queued price checks are dropped at shutdown; those cards keep "Check price again".

## Known limits
- Without Drive access only same-link PDFs are found as duplicates; other types show as not checked. With it, recall on
  non-exact types is low by design (a false "duplicate" is worse). The PDF rules were tuned on QA's canaries: accuracy needs a
  fresh blind set.
- Hiding a PDF does not remove its prices (live fn21 / fn54 ignore Show_PDF); the page can still count it as a second PDF.
- `xano_search.py` adds one filtered-read POST to a bot whose Xano reader was GET-only: 9 tables, the content/search path only.
- Florists with more than one priced quote can't be merged yet (ep578 refuses). Cross-category groups are out.
- ep578 does not catch a couple's open page saving its old list after the merge's last sweep.
