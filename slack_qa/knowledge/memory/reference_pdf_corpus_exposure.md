---
name: reference_pdf_corpus_exposure
description: "The PDF corpus is anonymously dumpable from Xano (14,173 rows / 13,907 Drive links in one unauthenticated GET) — VERIFIED 2026-08-14 and DELIBERATELY DEFERRED, not a bug to re-report"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 91271f22-af11-4573-8c2c-b96fe2eb1b6c
  modified: 2026-08-15T04:35:20.000Z
---

Found while scoping anonymous browsing, 2026-08-14. **All verified live. The user was told,
weighed it, and chose NOT to act on the endpoint side** — the product stays login-gated and
only the Drive download restriction is being fixed. Do not re-raise this as a discovery or
"fix" it unprompted; it is a known, accepted position.

## The finding

The WeWeb **pages** are auth-gated. The Xano **API is not**:

```
GET /api:GynP5T1B/wptp_pdfs      no token, no account, no referer
-> 200 | 7.5 MB | 5.5s | 14,173 rows | 13,907 drive.google.com links
```

- **ep146 `wptp_pdfs`** — public, no `output` list, no paging. The entire corpus in one GET.
- **ep93 `wptp_updated_mappings_pdf_specific`** — public, returns `PDF_Link` per vendor,
  ignores `Show_PDF`. The WeWeb collection `304b5da6-…` auto-fetches it **with no
  `Auth_Token` header**, so the Drive URL is in every visitor's browser regardless of what
  the UI draws. **The lock is CSS.** (`Pricing_Intelligence_Full` by contrast DOES send the
  token — that is the pattern to copy if this is ever fixed.)
- **ep199 `?section=pdfs`** — public AND response-cached with `auth:false` in the key.
- **ep172** — public unauthenticated PATCH (a write).
- **ep43 `wptp_pending_uploads`** — public dump including `User_Email`. **Zero retained
  request history**, no callers in the pipeline repo — the cheapest thing to close if
  anything is ever done.

**ep146 and ep172 are LOAD-BEARING** — `extract_core.py` reads ep146 via `XANO_GET_ENDPOINT`
(`_fetch_xano_pages`, line ~2092) and writes status via ep172 (~1602); `dashboard.py:762`
and `:2009` use ep146 and ep199's `PDF_Link`. **Deleting them breaks extraction.** Securing
them = add a `secret` precondition + put `?secret=` in the Railway env URL;
`_fetch_xano_pages` uses `requests(params=…)` which merges query strings cleanly, so no
pipeline code change is needed. Retained request history is SHORT — the near-empty log is
not evidence nobody has scraped it.

## The framing that decided it

Two of the three counter-arguments hold: **sharing is unpreventable** (a paying user can
pass a link on) and **individual freeloading is negligible** behind a login. What does not
hold is "only authenticated people can see PDFs" — true of the UI, false of the API.

So the real exposure is **bulk corpus theft, not paywall leakage**. The corpus is the moat,
not the $30 unlock.

**Link secrecy is unwinnable; file-level download restriction is the durable control.** With
every file restricted, someone holding all 13,907 links still cannot bulk-harvest — they
must render each in a browser. Without it, the link list IS the corpus. That is why the
Drive sweep was the piece taken forward.

## Drive restriction — the mechanism (this part IS being fixed)

`copyRequiresWriterPermission` ("viewers can't download, print, or copy") is **per-file and
NOT inherited from the folder**. A one-off sweep protects only the files that existed when
it ran; everything added since is unprotected. Measured on 9 files: **4 of the 5 newest
(table-10 ids 14222-14226, added 2026-08-06) downloaded anonymously as real `%PDF-` bytes**,
vs 1 of 4 older. Protected files return *"the owner hasn't given you permission to download
this file."*

**It blocks SAVING, not READING** — a restricted file is still fully readable in the Drive
preview by anyone with the link, and for a pricing product reading the numbers is the
product. Anti-scraping, not a paywall.

## MEASURED 2026-08-15 by `auditOnly()` — these supersede all earlier estimates

```
scanned      18,741 PDFs in the folder tree (full pass, no truncation)
UNPROTECTED  10,464  = 55.8% freely downloadable by anyone with the link
protected     8,277  = 44.2%
```

**The 9-file sample beforehand implied ~40% — it understated badly. Do not extrapolate
Drive coverage from samples; Google rate-limits external probing hard (it blocked 29 of 30
after a bulk pull). `auditOnly()` is the only reliable measurement.**

**THE FOLDER AND THE DATABASE HAVE DIVERGED.** 18,741 PDFs in Drive vs **13,813 distinct
Drive ids referenced by Xano** (table 10 has 14,173 rows, some sharing/missing links). So
**~4,928 files — 26% — are in Drive but not reachable from the product.** Unexplained:
superseded re-uploads, submissions never ingested, and duplicates all look identical from
Drive's side. Worth a separate look; it also means table 10 is not a complete inventory of
what is publicly linked.

Traversal starts in that untracked region — the first 490 files the sweep fixed had **zero**
matches in Xano. Verified live: 4 swept ids flipped to "protected"; Zumbro Falls Golf Club,
Zorba Villas Tulum, Zoetry Marigot Bay and Oasis Party Venue were still DOWNLOADABLE and
awaiting later runs. [name] (167 Events, protected beforehand) was unaffected.

**Throughput: ~490 files per 4.5-minute run (~0.57s/file), so ~20 hourly runs to finish.**
A likely cause of the slowness is `setRestricted_()` attempting the 4-arg
`Drive.Files.update` and falling back to the 2-arg form — probably TWO API calls per file.
If this is ever re-run at scale, cache which form works, or move the writes to
`UrlFetchApp.fetchAll()` for ~10-20x. **The user judged the extra hours immaterial given the
files had been exposed for months — do not re-litigate this.**

**Apps Script's Drive advanced service defaulted to v2 here, and the script needs v3**
(`files` vs `items`). A version probe must not itself use a v3-only field like
`fields:'files(id)'` — v2 rejects the call with "Invalid field selection files" before the
guard can report anything useful.

Script: **`tulle_vendor_data_updates/drive_download_restriction.gs`** — resumable
(ScriptProperties cursor, 4.5-min budget, because Apps Script dies at 6 min and a plain loop
over ~14k files leaves a silent tail), `auditOnly()` / `sweep()` / `installTrigger()`.
Needs **Drive API v3**: v2 returns `items` not `files`, which would silently scan zero files
and report success — `assertDriveV3_()` guards that. Only the folder owner can run it; the
Railway service account has read access only.

**Do not try to measure coverage by probing Drive from outside** — Google rate-limited and
then blocked 29 of 30 sampled downloads after a bulk pull. `auditOnly()` is the reliable
measurement.

## Built then deleted (do not look for these)

`ep240 venue/pdfs` and `fn59 utils/user_entitlement` were built as the authed twin + shared
entitlement rule, then **deleted** when the endpoint work was deferred. `ep230` still carries
its own inline copy of the entitlement rule. If this is ever revived: **WeWeb must be pointed
at the new endpoint and published BEFORE `PDF_Link` is stripped from ep93**, or every user
including payers gets a broken PDF list.

Also noticed, unrelated and still live: a **hardcoded Gmail app password and Slack webhook in
the client-side WeWeb signup workflow** (`e447fd21`, `ddc138d7`) — ships to every browser.

Related: [[project_fb_min_type_bug]], [[reference_access_entitlement_model]],
[[reference_extraction_pipeline]], [[reference_railway_admin_dashboard]].

## 2026-08-30 — BOTH PDF ROOT FOLDERS ARE LINK-PUBLIC (verified)

`get_file_permissions` on both roots returns `{"role":"reader","type":"anyone"}`:

    [token]
        "Pricing PDF's Tulle Together"   (2025-09-06 ->, still receiving files)
    1T9iWk5QKm5NP16kUQD_dawUGk-LQUTFx
        "Tulle PDFs - ACTIVE IN WPTP"    (2024-12-29 ->, in Shared Drive 0AKgvfwqJGti8Uk9PVA)

Anyone holding either FOLDER url can browse and read the whole corpus — no app, no Xano.
**`copyRequiresWriterPermission` cannot fix this.** It blocks saving a copy, not reading, so
Kate's sweep and the new dashboard panel are treating a symptom. ep146 was handing out 13,907
of the file links unauthenticated until it was gated on 2026-08-30, which is what made them
harvestable; ep93 still hands `PDF_Link` to any caller.

Properly locking the folders (removing "anyone with link") BREAKS PDF viewing for paying
customers, because the app hands users the raw Drive link. The real fix is an
entitlement-checked signed-URL path, not a Drive toggle. **The two roots have no common
ancestor**, so any sweep must be pointed at BOTH — one root silently covers half the corpus
and still reports a clean pass. Service account is
`[email]`; it needs Editor on both
(the Claude Drive connection CANNOT grant this — "caller does not have permission").
A service account needs no console scope change: it requests the Drive scope at token time,
so Drive-level sharing is the only gate. See [[project_endpoint_auth_audit]].

## 2026-11-09 re-check (anonymous curl)
- ep146 wptp_pdfs -> 403 "Invalid or missing secret" (gated since 08-30). I wrongly told the owner on 11-09 it was still open; corrected.
- ep93 wptp_updated_mappings_pdf_specific?vendor_id=V96 -> 200 with PDF_Link, NO login. Vendor ids are sequential, so a loop harvests every link and skips the free-view count. This is the remaining gap; owner asked "how to fix" 11-09.
- Fix plan: vendor-page PDF taps go through an entitlement-checked open (like Railway /rec/pdf: lock, count, return link), publish WeWeb FIRST, then strip PDF_Link from ep93.
- **11-09 owner DEFERRED the ep93 fix again after the full explanation ("no we're fine on this for now"). Do NOT re-raise unprompted.**
