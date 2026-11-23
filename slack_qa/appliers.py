"""What "Approve and apply" does, per proposal kind. These are the ONLY code paths in slack_qa that can write
outside Slack. Each is a DRY RUN when the proposal was created under dry run (fixed at creation) OR its variable
is not explicitly off (fails closed):

  merge        -> POST XANO_MERGE_URL (Xano ep578 /vendor/merge, built 2026-11-22; dry run until wired)   DEDUPE_DRY_RUN
  vendor_edit  -> Xano ep280 /vendor/edit/stage then ep281 /vendor/edit/apply (existing, drift-guarded)
                                                                                             VENDOR_EDIT_DRY_RUN
  task         -> GitHub issue in TASK_REPO with TASK_GITHUB_TOKEN                           TASK_DRY_RUN

Each returns {"ok", "dry_run", "unknown", "text", "result"}. `unknown` = the request may or may not have landed
(timeout, connection drop, 5xx, unreadable reply): the card then says so and offers Re-check, and NEVER claims
"nothing was changed". Re-check: merge_status() asks the endpoint by proposal_id; vendor_edit_status() reads the live
table-11 value; task_status() can't look an issue up without a write token, so it tells a human to check.
"""
from __future__ import annotations

import logging
import re

import requests

import config

log = logging.getLogger("slack_qa.apply")
TIMEOUT = 30


def esc(text) -> str:
    return str(text if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _post(url: str, body: dict, headers: dict | None = None, timeout: float = TIMEOUT) -> dict:
    """{"ok": True, ...} on a 2xx JSON reply; {"ok": False, "unknown": False, "error"} when the server clearly refused
    (4xx); {"ok": False, "unknown": True, "error"} when we can't tell whether it ran."""
    try:
        r = requests.post(url, json=body, headers=headers or {}, timeout=timeout)
    except (requests.Timeout, requests.ConnectionError) as e:
        return {"ok": False, "unknown": True, "error": f"no reply ({type(e).__name__})"}
    except requests.RequestException as e:
        return {"ok": False, "unknown": True, "error": type(e).__name__}
    try:
        data = r.json()
    except ValueError:
        data = None
    if r.status_code >= 500 or (r.status_code < 300 and not isinstance(data, dict)):
        return {"ok": False, "unknown": True, "error": f"HTTP {r.status_code}"}
    if r.status_code >= 300:
        msg = (data or {}).get("message") or (data or {}).get("error") or f"HTTP {r.status_code}"
        return {"ok": False, "unknown": False, "error": str(msg)[:300], "http": r.status_code,
                "code": str((data or {}).get("code") or "")[:80]}
    return {"ok": True, **data}


def _out(ok, text, dry=False, unknown=False, result=None):
    return {"ok": ok, "dry_run": dry, "unknown": unknown, "text": text, "result": result or {}}


# --- merge ---------------------------------------------------------------------------------------------
RUNNING_TEXT = "A merge or undo is running in Xano right now (one at a time). Wait a few minutes, then press Re-check."
# Every refusal ep578 v5 can give (live XanoScript, 2026-11-23 04:48:21 UTC) -> what the card says. test_ep578_contract.py
# holds one line per endpoint message. "Nothing was changed" is added by _refusal.
REFUSALS = (
    (r"no visible Primary PDF", "Hiding these PDFs would leave the listing with no visible Primary. Choose Keep both on a "
                                "fresh card."),
    (r"expected_state is missing", "The card has no record of what it showed. Ask for a fresh card."),
    (r"^Stale plan", "One of these listings, or its PDFs, changed while the merge was starting. Ask for a fresh card."),
    (r"Another merge already involves one of these records",
     "An earlier merge of one of these listings isn't finished (stopped part-way, being undone, or undone with problems). "
     "Finish or undo that one first."),
    (r"A later merge involves one of these records",
     "A later merge used one of these listings. Undo that one first (newest first)."),
    (r"stale card", "One of these listings, or its PDFs, changed after this card was posted. Ask for a fresh card."),
    (r"a hidden listing \(", "The duplicate is hidden from search. Find out why, then answer the question on a fresh card."),
    (r"the survivor is hidden", "The kept listing is hidden from search and the merge would make it visible. Find out why, "
                                "then answer the question on a fresh card."),
    (r"holds the gold chip and the survivor does not", "The duplicate has the gold chip and the kept listing doesn't, and a "
                                                        "merge never moves one. Keep the gold-chip listing instead."),
    (r"florists with", "Florists with more than one price quote can't be merged yet: their summary would use only one quote."),
    (r"hide_pdf_ids has a repeated", "The same PDF was listed twice to hide. Ask for a fresh card."),
    (r"does not belong to the survivor", "A PDF to hide isn't on these listings any more. Ask for a fresh card."),
    (r"on more than one row", "A PDF to hide shares its id with another file, so Xano can't tell which. Choose Keep both on "
                              "a fresh card."),
    (r"no visible PDF", "Hiding these PDFs would leave the listing with none. Choose Keep both on a fresh card."),
    (r"^Drift", "Many more rows point at the duplicate than the card showed. Ask for a fresh card."),
    (r"Every loser must have", "The listings are in different categories."),
    (r"Survivor vendor not found", "The kept listing doesn't exist any more."),
    (r"A loser vendor was not found", "The duplicate listing doesn't exist any more."),
    (r"survivor was itself merged", "The kept listing was already merged into another one. Merge into that one instead."),
    (r"A loser is already merged", "The duplicate was already merged into another listing."),
    (r"merge chains are refused", "Another listing was already merged into the duplicate, so it can't be merged again."),
    (r"do not match the merge recorded", "Xano has a different merge recorded for this card. Ask the owner before doing "
                                         "anything else."),
    (r"survivor has since been merged", "The kept listing has since been merged into another one. Undo that later merge "
                                        "first."),
    (r"Need survivor_vendor_id", "The card is missing a listing, or has the same one twice. Ask for a fresh card."),
    (r"proposal_id is required|mode must be", "The request was malformed. Ask the owner."),
    (r"Invalid or missing secret", "Xano didn't accept the bot's key. Ask the owner."),
)
REFUSALS = tuple((re.compile(rx, re.I), text) for rx, text in REFUSALS)
ANSWERABLE = re.compile(r"a hidden listing \(|the survivor is hidden", re.I)
# the endpoint knows more about this merge than the card does: never report a plain failure, ask mode=status (Re-check)
ASK_STATUS = re.compile(r"already applied|was undone \(or is mid-undo\)|already undone|No merge exists", re.I)


def plain_refusals(said: str) -> list[str]:
    said = re.sub(r"^Refused: ", "", said)
    return list(dict.fromkeys(text for rx, text in REFUSALS if rx.search(said)))


def _is_running(res: dict) -> bool:
    if res.get("running") is True or str(res.get("status") or "").lower() == "running":
        return True
    return not res.get("ok") and bool(re.search(r"\b(running|in progress|locked)\b",
                                                f"{res.get('code', '')} {res.get('error', '')}", re.I))


def _warnings(res: dict, undo: bool = False) -> str:
    """QA 2026-11-22: a rebuild problem or a straggler must not read as a clean "Applied"."""
    w = res.get("warnings") or []
    if not w:
        return ""
    said = "; ".join(str(x.get("detail") or x.get("code")) if isinstance(x, dict) else str(x) for x in w[:3])
    if undo:
        return (f" :warning: Not everything could be put back: {esc(said[:300])}. *Undo again* retries it; a value someone "
                "changed since stays as they left it.")
    return f" :warning: The rows moved, but Xano reported a problem: {esc(said[:300])}."


def _refusal(res: dict) -> str:
    plain = plain_refusals(str(res.get("error") or ""))
    if plain:
        return "Xano refused, so nothing was changed. " + " ".join(plain)
    return f"Xano refused: {esc(res.get('error'))}. Nothing was changed."


def merge(payload: dict, user: str, proposal_id: str, mode: str = "apply", dry_run: bool = True) -> dict:
    """payload = {"survivor": "V12", "losers": ["V34"], "counts": {...}}. mode = apply | undo."""
    import pdf_dupes
    try:
        hide = [] if mode == "undo" else pdf_dupes.hide_ids(payload)       # undo restores PDFs from the backup
    except ValueError as e:
        return _out(False, f"I stopped before sending anything: {esc(e)}. Nothing was changed.")
    n_hide = f"{len(hide)} duplicate PDF{'s' if len(hide) != 1 else ''}"
    counts = payload.get("counts") or {}
    total = sum(v for v in counts.values() if isinstance(v, int))
    if dry_run or config.merge_dry_run():
        verb = "would restore" if mode == "undo" else "would repoint"
        log.info("DRY RUN merge %s %s <- %s by %s (%s)", mode, payload.get("survivor"), payload.get("losers"), user,
                 proposal_id)
        return _out(True, f"Dry run: {verb} about {total} rows across {len([v for v in counts.values() if v])} "
                          f"tables into {payload.get('survivor')}."
                          + (f" Would hide {n_hide}." if hide else "") + " Nothing was written.", dry=True,
                    result={"counts": counts, **({"hide_pdf_ids": hide} if hide else {})})
    if not config.merge_url() or not config.merge_secret():
        return _out(False, "The merge endpoint isn't configured (XANO_MERGE_URL / XANO_MERGE_SECRET), so nothing "
                           "was sent.")
    body = {"mode": mode, "survivor_vendor_id": payload.get("survivor"), "loser_vendor_ids": payload.get("losers"),
            "proposal_id": proposal_id, "approved_by": user, "expected_counts": counts,
            # no snapshots/counters: the endpoint recomputes both from the LIVE rows at apply time; the bot's
            # scan-time values are display-only (QA round 3)
            "secret": config.merge_secret()}
    if mode == "apply":                        # what the approver saw; the endpoint refuses if it changed since
        import listing_compare
        state = listing_compare.expected_state(payload)
        if not listing_compare.preview_state(payload):
            return _out(False, "I don't have Xano's preview of this merge matching what the card shows, so nothing was sent. "
                               "Nothing was changed. Ask for a fresh card.")
        if state:
            body["expected_state"] = state
        allow = payload.get("allow") or {}    # only an approver's explicit OK on this card
        if allow.get("hidden"):
            body["allow_hidden_loser"] = True
        md5 = pdf_dupes.md5_map(payload)
        if md5:                               # lets ep578 see the same file under two Drive ids as one
            body["pdf_md5"] = md5
    if hide:
        body["hide_pdf_ids"] = hide           # t10 PDF_IDs; the endpoint backs Show_PDF up and undo restores it
    res = _post(config.merge_url(), body, timeout=config.merge_timeout())
    if res.get("unknown"):
        return _out(False, f"I sent it but got {esc(res['error'])}, so I can't tell whether the merge ran.",
                    unknown=True)
    if _is_running(res):                       # 'unknown' on the card = Re-check only: never Resume or Undo mid-run
        return _out(False, RUNNING_TEXT, unknown=True)
    if not res.get("ok") and ASK_STATUS.search(str(res.get("error") or "")):
        return _out(False, f"Xano says: {esc(res.get('error'))}", unknown=True)
    if not res.get("ok") and not plain_refusals(str(res.get("error") or "")):
        if res.get("http") == 429:
            return _out(False, "Xano is turning requests away right now (too many), so nothing was sent. Try again in a few "
                               "minutes.")
        # QA 2026-11-23: a reply we don't recognise proves nothing about what ran
        return _out(False, f"Xano answered something I don't recognise ({esc(res.get('error'))}), so I can't tell whether the "
                           "merge ran.", unknown=True)
    if not res.get("ok"):
        return _out(False, _refusal(res))
    if res.get("status") == "partial" and mode != "undo":
        out = _out(False, f"Xano says the merge stopped part-way ({esc(res.get('repointed_total', '?'))} rows moved, "
                          "all backed up).", result={k: v for k, v in res.items() if k != "secret"})
        out["partial"] = True
        return out
    if res.get("status") in ("partial", "partial_undo", "undoing"):
        return _out(False, "Xano says the undo stopped part-way.", unknown=True,
                    result={k: v for k, v in res.items() if k != "secret"})
    if res.get("status") == "queued":
        return _out(False, "Xano queued the merge and is still running it.", unknown=True)
    known = ("undone", "undone_with_warnings") if mode == "undo" else ("applied", "applied_with_warnings")
    if res.get("status") not in known:
        return _out(False, f"Xano replied with a status I don't know ({esc(res.get('status') or 'none')}), so I can't tell "
                           "whether it ran.", unknown=True,
                    result={k: v for k, v in res.items() if k != "secret"})
    moved = res.get("repointed_total", "?")
    result = {k: v for k, v in res.items() if k != "secret"}
    # ep578 (built 2026-11-22) reports conflicts (values changed by someone since, left as they are) - show the count
    conflicts = res.get("conflicts") or res.get("undo_conflicts") or []
    note = (f" {len(conflicts)} value{'s' if len(conflicts) != 1 else ''} had been changed since and "
            "{} left as they are.".format("was" if len(conflicts) == 1 else "were")) if conflicts else ""
    note += _warnings(res, undo=mode == "undo")
    kept = res.get("kept_newer") or []
    if mode == "undo" and kept:               # information, not a conflict: the couple moved on since
        note += (f" {len(kept)} newer last-viewed value{'s' if len(kept) != 1 else ''} "
                 f"{'were' if len(kept) != 1 else 'was'} left as {'they are' if len(kept) != 1 else 'it is'}.")
    if hide:                                  # never claim PDFs were hidden unless the endpoint says so
        done = res.get("hide_pdf_ids")
        stuck = any("Show_PDF" in str(c.get("column")) for c in conflicts if isinstance(c, dict))
        note += (f" Hid {n_hide}." if isinstance(done, list) and set(hide) <= {str(x) for x in done} and not stuck else
                 f" I asked Xano to hide {n_hide} but it didn't confirm that: check the listing.")
    if mode == "undo":
        restored = res.get("restored_total", moved)            # ep578 reports restored_total on undo
        return _out(True, f"Restored {esc(restored)} values from backup {esc(res.get('backup_id', '?'))}.{note}",
                    result=result)
    return _out(True, f"Repointed {esc(moved)} rows into {payload.get('survivor')}; "
                      f"{', '.join(payload.get('losers') or [])} retired (backup {esc(res.get('backup_id', '?'))})."
                      f"{note}", result=result)


def merge_status(payload: dict, proposal_id: str) -> dict:
    """Re-check after an unknown outcome: {"state": applied|undone|none|unknown, "text"} from mode=status."""
    if not config.merge_url() or not config.merge_secret():
        return {"state": "unknown", "text": "The merge endpoint isn't configured, so I can't check."}
    res = _post(config.merge_url(), {"mode": "status", "proposal_id": proposal_id,
                                     "survivor_vendor_id": payload.get("survivor"),
                                     "loser_vendor_ids": payload.get("losers"), "secret": config.merge_secret()})
    if _is_running(res):
        return {"state": "unknown", "text": RUNNING_TEXT}
    if not res.get("ok"):
        return {"state": "unknown", "text": f"The status check didn't answer ({esc(res.get('error'))})."}
    st = str(res.get("status") or "none")
    if st in ("applied", "applied_with_warnings"):
        return {"state": "applied", "text": f"Confirmed: the merge ran ({esc(res.get('repointed_total', '?'))} rows, "
                                            f"backup {esc(res.get('merge_id', '?'))}).{_warnings(res)}",
                "result": {"status": st, "warnings": res.get("warnings") or []}}
    if st == "partial":
        return {"state": "partial", "text": "The merge stopped part-way; everything that moved is backed up."}
    if st in ("partial_undo", "undoing"):          # 'undoing' while it really runs is caught by _is_running above
        return {"state": "applied", "text": "The undo stopped part-way. Press Undo merge again to finish it."}
    if st in ("undone", "undone_with_warnings"):
        return {"state": "undone", "text": f"Confirmed: the merge ran and was undone.{_warnings(res, undo=True)}",
                "result": {"status": st, "warnings": res.get("warnings") or []}}
    if st != "none":                               # a status this bot doesn't know: never read it as "no merge"
        return {"state": "unknown", "text": f"Xano reports a status I don't know ({esc(st)}). Ask the owner."}
    return {"state": "none", "text": "Confirmed: no merge exists for this card, so nothing changed."}


# --- vendor data fix (existing staged-edit flow, table 74) ---------------------------------------------
def vendor_edit(payload: dict, user: str, proposal_id: str, dry_run: bool = True) -> dict:
    """payload = {"vendor_id", "field", "old_value", "new_value", "note"}. Stage + apply. Staging (ep280) reads the live
    value at approve time and returns it as previous_value; if that differs from what the card showed, the staged row
    is discarded and nothing is applied. ep281 then re-reads again and refuses on drift. Fails closed if the stage
    reply lacks edit_id or previous_value."""
    vid, field, new = payload.get("vendor_id"), payload.get("field"), payload.get("new_value")
    if dry_run or config.vendor_edit_dry_run():
        log.info("DRY RUN vendor_edit %s.%s by %s (%s)", vid, field, user, proposal_id)
        return _out(True, f"Dry run: would set {field} on {vid} to “{esc(new)}”. Nothing was written.", dry=True)
    if not config.vendor_edit_secret():
        return _out(False, "VENDOR_EDIT_SECRET isn't set, so nothing was sent.")
    base = config.vendor_edit_base().rstrip("/")
    sec = config.vendor_edit_secret()
    st = _post(base + "/vendor/edit/stage", {"vendor_id": vid, "field": field, "new_value": new,
                                             "proposed_by": "tulle-bot",
                                             "note": f"{payload.get('note', '')} (proposal {proposal_id})"[:500],
                                             "source": "tulle-bot", "secret": sec})
    if st.get("unknown"):
        # staging writes only table 74 (a proposal, not live data), so an unknown stage changed no listing
        return _out(False, f"Staging got {esc(st['error'])}; the listing was not changed (a staged row may exist in "
                           "the pending-edit queue).")
    if not st.get("ok"):
        return _out(False, f"Staging refused: {esc(st.get('error'))}. Nothing was changed.")
    edit_id = st.get("edit_id") or st.get("id") or (st.get("edit") or {}).get("id")
    has_prev = "previous_value" in st or "previous_value" in (st.get("edit") or {})
    staged_prev = st.get("previous_value", (st.get("edit") or {}).get("previous_value"))

    def discard():
        if edit_id:
            d = _post(base + "/vendor/edit/discard", {"edit_id": edit_id, "discarded_by": user, "secret": sec})
            log.info("discarded staged edit %s (%s)", edit_id, d.get("ok"))

    if not edit_id or not has_prev:
        discard()
        return _out(False, "The staging reply was missing the edit id or the current value, so I stopped before "
                           "applying. Nothing was changed.")
    want_old = "" if payload.get("old_value") is None else str(payload.get("old_value")).strip()
    if ("" if staged_prev is None else str(staged_prev).strip()) != want_old:
        discard()
        return _out(False, f"{field} on {vid} changed since this was proposed (now “{esc(staged_prev)}”), so I "
                           "didn't apply it.")
    ap = _post(base + "/vendor/edit/apply", {"edit_id": edit_id, "applied_by": user, "secret": sec})
    if ap.get("unknown"):
        return _out(False, f"Apply got {esc(ap['error'])}, so I can't tell whether {field} changed.", unknown=True,
                    result={"edit_id": edit_id})
    if not ap.get("ok"):
        discard()
        return _out(False, f"Apply refused: {esc(ap.get('error'))}. Nothing was changed.")
    return _out(True, f"Set {field} on {vid} to “{esc(new)}” (edit {esc(edit_id)}; the old value is kept for "
                      "rollback).", result={"edit_id": edit_id})


def vendor_edit_status(payload: dict, xano=None) -> dict:
    """Re-check by reading the live table-11 row (fresh read, no cache)."""
    if xano is None:
        from xano_ro import ReadOnlyXano
        xano = ReadOnlyXano(cache_ttl=0)
    if not getattr(xano, "enabled", True):
        return {"state": "unknown", "text": "I can't read Xano from here, so I can't check."}
    try:
        rows, _ = xano.rows(11)
    except Exception as e:  # noqa: BLE001
        return {"state": "unknown", "text": f"Reading the listing failed ({type(e).__name__})."}
    row = next((r for r in rows if str(r.get("Vendor_ID")) == str(payload.get("vendor_id"))), None)
    if row is None:
        return {"state": "unknown", "text": "I can't find that vendor any more."}
    live = "" if row.get(payload.get("field")) is None else str(row.get(payload.get("field"))).strip()
    if live == str(payload.get("new_value") or "").strip():
        return {"state": "applied", "text": f"Confirmed: {payload.get('field')} is now “{esc(live)}”."}
    if live == str(payload.get("old_value") or "").strip():
        return {"state": "none", "text": f"Confirmed: {payload.get('field')} is still “{esc(live)}”, so nothing changed."}
    return {"state": "unknown", "text": f"{payload.get('field')} is now “{esc(live)}”, which is neither value on the card."}


# --- hide / unhide a vendor (Xano ep279, the endpoint the retired Tulle Ops 🫣 reaction used) --------------------
def visibility(payload: dict, user: str, proposal_id: str, dry_run: bool = True) -> dict:
    """payload = {"vendor_id", "action": "hide"|"unhide", "name", "reason"}. One call; ep279 flips table 11
    Validated_Data and returns before/after + changed. Same secret as the staged-edit endpoints (api group 3)."""
    vid, action = payload.get("vendor_id"), payload.get("action")
    if action not in ("hide", "unhide") or not vid:
        return _out(False, "This card is missing the vendor or the action, so nothing was sent.")
    verb = "hidden from search" if action == "hide" else "visible in search again"
    if dry_run or config.visibility_dry_run():
        log.info("DRY RUN visibility %s %s by %s (%s)", action, vid, user, proposal_id)
        return _out(True, f"Dry run: {vid} would be {verb}. Nothing was written.", dry=True)
    if not config.vendor_edit_secret():
        return _out(False, "VENDOR_EDIT_SECRET isn't set, so nothing was sent.")
    r = _post(config.vendor_edit_base().rstrip("/") + "/vendor/visibility",
              {"vendor_id": vid, "action": action, "secret": config.vendor_edit_secret(),
               "actor_slack_id": user, "source": "tulle-bot"})
    if r.get("unknown"):
        return _out(False, f"Xano gave {esc(r['error'])}, so I can't tell whether {vid} changed.", unknown=True)
    if not r.get("ok"):
        return _out(False, f"Xano refused: {esc(r.get('error'))}. Nothing was changed.")
    if r.get("changed") is False:
        return _out(True, f"{vid} was already {'hidden' if action == 'hide' else 'visible'}, so nothing changed.")
    # ep279 replies {vendor_id, vendor_name, previous_visibility, new_visibility, changed}
    return _out(True, f"{vid} is now {verb}. Search can keep showing the old result for up to about 2 hours.",
                result={"previous_visibility": r.get("previous_visibility"),
                        "new_visibility": r.get("new_visibility")})


def visibility_status(payload: dict, xano=None) -> dict:
    """Re-check by reading the live table-11 row (fresh read)."""
    if xano is None:
        from xano_ro import ReadOnlyXano
        xano = ReadOnlyXano(cache_ttl=0)
    if not getattr(xano, "enabled", True):
        return {"state": "unknown", "text": "I can't read Xano from here, so I can't check."}
    try:
        rows, _ = xano.rows(11)
    except Exception as e:  # noqa: BLE001
        return {"state": "unknown", "text": f"Reading the listing failed ({type(e).__name__})."}
    row = next((r for r in rows if str(r.get("Vendor_ID")) == str(payload.get("vendor_id"))), None)
    if row is None:
        return {"state": "unknown", "text": "I can't find that vendor any more."}
    hidden = str(row.get("Validated_Data") or "").strip() == "0"
    want_hidden = payload.get("action") == "hide"
    if hidden == want_hidden:
        return {"state": "applied", "text": f"Confirmed: it is {'hidden' if hidden else 'visible'}."}
    return {"state": "none", "text": f"Confirmed: it is still {'hidden' if hidden else 'visible'}, so nothing changed."}


# --- task -----------------------------------------------------------------------------------------------
MENTION = re.compile(r"(?<![\w`])@([A-Za-z0-9][\w-]*)")


def defang(text: str) -> str:
    """GitHub issue text: plain text (no HTML escaping), but @handles wrapped in backticks so nobody gets pinged."""
    return MENTION.sub(r"`@\1`", text or "")


def task(payload: dict, user: str, proposal_id: str, dry_run: bool = True) -> dict:
    title = defang((payload.get("title") or "Feedback follow-up")[:200])
    if dry_run or config.task_dry_run():
        log.info("DRY RUN task %r by %s (%s)", title, user, proposal_id)
        return _out(True, f"Dry run: would open a GitHub issue “{esc(title)}” in {config.task_repo()}.", dry=True)
    if not config.task_github_token():
        return _out(False, "TASK_GITHUB_TOKEN isn't set, so nothing was sent.")
    body = defang(payload.get("body") or "") + f"\n\n_Filed by Tulle Bot from Slack; approved by Slack user {user} " \
                                               f"(proposal {proposal_id})._"
    res = _post(f"https://api.github.com/repos/{config.task_repo()}/issues",
                {"title": title, "body": body[:60000], "labels": ["from-feedback"]},
                headers={"Authorization": f"Bearer {config.task_github_token()}",
                         "Accept": "application/vnd.github+json"})
    if res.get("unknown"):
        return _out(False, f"GitHub didn't answer ({esc(res['error'])}); check the repo before trying again.",
                    unknown=True)
    if not res.get("ok"):
        return _out(False, f"GitHub refused: {esc(res.get('error'))}. Nothing was filed.")
    return _out(True, f"Opened <{res.get('html_url')}|issue #{res.get('number')}>.", result={"url": res.get("html_url")})


def task_status(payload: dict, proposal_id: str) -> dict:
    return {"state": "unknown", "text": f"I can't look issues up from here: search {config.task_repo()} for "
                                        f"“proposal {proposal_id}”."}


APPLIERS = {"merge": merge, "vendor_edit": vendor_edit, "task": task, "visibility": visibility}
