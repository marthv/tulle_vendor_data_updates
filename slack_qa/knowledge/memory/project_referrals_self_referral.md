---
name: referrals-self-referral
description: "2026-08-26 ROOT CAUSE + PARTIAL FIX — referrals were BROKEN, not abused: the Aug-13/16 hardening made ep145+ep66 ignore their user_id path param, so every referral credited the NEW SIGNUP. Xano ep270 POST /referral/credit is BUILT AND TESTED, Melissa (27479) retro-credited, row 444 corrected. THE WEWEB REWIRE IS STILL OUTSTANDING and must be done by hand"
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-27T02:15:15.847Z
  originSessionId: bee362a9-3340-4a1d-8dfd-92ba65d8f986
---

Slack 2026-08-26: TulleBot posted a referral where referrer and referred were the same person.
Des: *"is there a way to not let people refer themselves"*. Vivek: *"not sure how she did that
if we have unique users"*.

**She did not do anything. The referral system credits the wrong person.** Vivek's instinct was
right — there was nothing to explain on her side.

## Root cause

The crediting lives in the **WeWeb Sign Up page** (`4a9de110-…`), form container
`1b21a127-543b-4410-9475-a720e75138b7`, submit workflow **`Sign Up v2`
`f7e97a41-3803-4342-9679-35ea1769cbff`**. Sequence:

1. `9d9037fc` **get referrer id** — parses `?referralcode=` out of the URL into var
   `2cc720c2-65cc-4847-9c8d-a02194858703`. **The referral code IS the referrer's user id.**
2. `e6d2d4b2` **signUp** — creates the account AND authenticates the browser as the new user.
3. `70874ec9` **"Is there a referral present?"** — the true/false branch. Its only test is
   `referralcode != ""`. No identity check of any kind.
4. `a30ff038` **get referrer** — `GET /user/{2cc720c2}`.
5. `5f7fadc7` stores that response in `bcfa33f9-…` ("referring_user").
6. `d7900280` "Referrer has a Date" → either branch calls `PATCH /userfreeviews/{2cc720c2}`
   with `free_views = bcfa33f9.data.FreeViewsRemaining + 3` (`ed533ce3` / `6bc4118d`).
7. `b391b7c2` / `c4b46c33` write the referral row via **`POST /referrals_Clone`** with
   `user_id: bcfa33f9.data.id` and `referred_user_id: <the new auth user>`.

**Steps 4 and 6 no longer address the referrer.** Both endpoints were hardened this month and
both now IGNORE the path param:

- **ep66 `GET /user/{user_id}`** — secured **2026-08-16**: *"The path param is retained for URL
  compatibility but IGNORED - the row is keyed on `$auth.id`."*
- **ep145 `PATCH /userfreeviews/{user_id}`** — secured **2026-08-13**: *"the user_id path param
  is retained for URL compatibility but IGNORED"*, write targets `$auth.id`.

By step 4 the browser is authenticated **as the brand-new signup**, so `$auth.id` is the new
user. Therefore `bcfa33f9` = the new user, the +3 free views land on the new user, and the
referral row gets the same id on both sides.

The hardening was correct on its own terms. What was missed is that the signup referral flow
**depended on those two endpoints addressing a DIFFERENT user by id** — the exact capability
the lockdown removed.

## Proof, and the blast radius

Every referral row before this is normal (distinct ids). **Row 444 is the FIRST referral since
the hardening, and the only one where the ids match:**

```
443  2026-07-14  uid=6627   ruid=23267           byorswedding@gmail.com
444  2026-08-27  uid=27480  ruid=27480   SELF    lindseyypapa@gmail.con
```

Only **1** referral row exists since 2026-08-16, so exactly one referrer has been cheated so
far — but **every referral from now on is broken the same way**. The arithmetic confirms it:
signup default is `FreeViewsRemaining: 3`, and she holds **6** = 3 + 3, credited to herself.

The Slack alert's "Referring User Email / Name" also read back her own details, because they
bind to `bcfa33f9` too. `Referring User Source undefined` is a field that does not exist on the
response at all.

The `.con` typo (`lindseyypapa@gmail.con`) is real and stored in both tables — Xano's `email`
type validates shape, not TLD — but it is incidental to this bug.

## The fix — SERVER SIDE DONE, FRONTEND STILL OUTSTANDING

**DONE: Xano ep270 `POST /referral/credit`** (group 4 `WGW_G49d`, `auth = "user"`). The caller
supplies only `referrer_user_id`; the server does everything else. Guards, all four verified
live against throwaway accounts on 2026-08-27:

| case | result |
|---|---|
| B credits A | 200, **A 3 -> 6, B unchanged at 3** |
| A credits themselves | 400 "A referral has to come from someone else's account." |
| B replays the same credit | 400 "This account has already been credited to a referrer." |
| non-existent referrer id | 404 "That referral link does not match an account." |
| `referrer_user_id: 0` | 400 "No referrer was supplied." |
| no / bad token | 401 |

Test users 27492 / 27493 and referral row 445 were deleted afterwards. The response carries
`referrer_name` / `referrer_email` / `referrer_free_views` so the Slack alert can name the real
referrer instead of re-reading the broken lookup.

**DONE: retro-credit.** The real referrer was recoverable only from the API request history:
`PATCH .../userfreeviews/27479?user_id=27479` returning `{"id":27480,...}` — the write landing
on the wrong account, in one line. That is **user 27479, Melissa (melissaa.izzio5@gmail.com)**,
a genuinely different person (New England hub, TikTok, 33 vendor views) who had been left on
**0 free views**. Credited to **3**. Referrals row 444 corrected to name her as the referrer;
`referred_user_id` stays 27480.

Lindsey (27480) keeps the 3 free views she was wrongly given — she did nothing wrong and the
cost is 3 PDF views. Not clawed back.

## WeWeb rewire — DONE BY HAND 2026-08-27 (user), verified

`editFrontendWorkflow` CANNOT touch `Sign Up v2` — plugin actions (Xano request, the auth
plugin's `signUp`, the email `apiRequest`) plus a `submit` trigger on a
`ww-form-container#deprecated-2` all fail validation. See [[reference_weweb_mcp_limits]].
The user hand-edited it. Verified state:

- **New action `fd766258-d189-42b0-83ad-a323fd026c7c`** → `POST /referral/credit`,
  body `referrer_user_id = toNumber(referral_id)`. Wired as the TRUE branch of
  `70874ec9` "Is there a referral present?".
- **Disabled:** `a30ff038` get referrer · `5f7fadc7` Save referrer data · `ed533ce3` +
  `6bc4118d` Free Views · `b391b7c2` + `c4b46c33` add to table · `29411a47` · `ea4d848a` ·
  `446a03d7`. The whole old crediting path is inert.
- **`ddc138d7` Post to Slack** rebound to `context.workflow['fd766258-…'].result`.

- **`9d9037fc` "get referrer id" FIXED** — a Set Variable action, 3rd in the workflow,
  immediately before Sign up, writing the **`referral_id`** string variable. The original had
  two faults: the `&referralcode=` branch searched for `"?referralcode="`, got -1 and sliced
  from index 13; and it took everything to the END of the URL, so `?referralcode=27479&hub=ny`
  yielded `27479&hub=ny` → `toNumber` → NaN. Now finds the marker in either position and
  stops at the next `&`.

**The whole referral chain is verified correct in the editor.** Only cosmetic item left:
`fd766258` still has no display name.

## WHY ep270 NEVER RETURNS AN ERROR (changed 2026-08-27, after the rewire)

The first build used `precondition`, so a bad referral returned 400/404. **That would have
broken signups.** ep270 is called AFTER the account is created, and the workflow's
`firstErrorAction` (`12ffc183`) catches any failed action and aborts the chain — so a
rejected referral meant no redirect, no welcome email, and the new user stranded on the
signup form with an error. The `get referrer id` bug above makes that reachable with an
ordinary shared link carrying a second query param.

So every rejection now returns **200 with `credited:false` and a `reason`**
(`self_referral` / `no_referrer` / `referrer_not_found` / `already_credited` / `credited`),
which the Slack alert prints. Only genuine transport failures (no token, malformed body)
still error. **Do not reintroduce `precondition` here** — it couples a referral problem to
account creation.

Re-verified live 2026-08-27 on fresh throwaway accounts 27504 / 27505:

| case | result |
|---|---|
| B credits A | 200 `credited:true` — **A 3 -> 6, B unchanged at 3** |
| A refers themselves | 200 `credited:false, reason:self_referral` |
| `referrer_user_id: 0` | 200 `credited:false, reason:no_referrer` |
| non-existent referrer | 200 `credited:false, reason:referrer_not_found` |
| B replays | 200 `credited:false, reason:already_credited` |
| no token | 401 (still errors, correctly) |

Test users and referral row 446 deleted; table back to 82 rows, row 444 intact.

## Other referral defects found in passing

- **ep138 `POST /referrals` writes `Status: $input.Body_Template`** — the invite email BODY
  into the Status column. That is why **37 of 82 rows** carry marketing copy as their status.
  Historical only: the live workflow now posts to **`/referrals_Clone` (ep161)** and passes
  `Status: "Accepted - Free Views"` explicitly. The two `/referrals` actions in the workflow
  are disabled.
- **ep136 `PUT /referrals/{id}` is DEAD CODE** — its where clause is
  `$db.Referrals.Referring_User == $db.Referrals.user_id` (email column vs int column), never
  matches, so the `foreach` never runs and it returns null. It also writes fields absent from
  the current schema.
- **`auth/signup` (ep2) declares a `referral_id` input and never uses it** — the WeWeb workflow
  does pass it, and Xano silently drops it.
- **`9d9037fc`'s URL parse has a copy-paste bug**: the `&referralcode=` branch searches for
  `"?referralcode="`, gets -1, and takes `subText(url, 13)` — garbage. Only the `?` form works.
- **`GET /referrals` (ep137) is public and unauthenticated**, returning all 82 rows with names
  and emails. That is how the table was pulled for this analysis.

Related: [[reference_access_entitlement_model]] (`FreeViewsRemaining` is the PDF meter, NOT the
PI allowance), [[project_preauth_401_fix]] (the same Aug hardening pass).


## 2026-09-06 — PUBLISHED
User confirmed this is published and live in production. Do not re-raise it as pending.
