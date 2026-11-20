"""What "Approve and apply" does, per proposal kind. These are the ONLY code paths in slack_qa that can write
outside Slack, and each one is a DRY RUN unless its own variable is explicitly 0:

  merge        -> POST XANO_MERGE_URL (endpoint NOT built yet; see XANO_MERGE_PROPOSAL.md)   DEDUPE_DRY_RUN
  vendor_edit  -> Xano ep280 /vendor/edit/stage then ep281 /vendor/edit/apply (existing, drift-guarded)
                                                                                             VENDOR_EDIT_DRY_RUN
  task         -> GitHub issue in TASK_REPO with TASK_GITHUB_TOKEN                           TASK_DRY_RUN

Each returns {"ok": bool, "dry_run": bool, "text": one plain-English line for the card, "result": {...}}.
Nothing here is called except from interactions.py after the approver check and the pending->applying CAS.
"""
from __future__ import annotations

import logging

import requests

import config

log = logging.getLogger("slack_qa.apply")
TIMEOUT = 30


def _post(url: str, body: dict, headers: dict | None = None) -> dict:
    try:
        r = requests.post(url, json=body, headers=headers or {}, timeout=TIMEOUT)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 300:
            msg = (data or {}).get("message") or (data or {}).get("error") or f"HTTP {r.status_code}"
            return {"ok": False, "error": str(msg)[:300]}
        return {"ok": True, **(data if isinstance(data, dict) else {"data": data})}
    except requests.RequestException as e:
        return {"ok": False, "error": type(e).__name__}


# --- merge ---------------------------------------------------------------------------------------------
def merge(payload: dict, user: str, proposal_id: str, mode: str = "apply") -> dict:
    """payload = {"survivor": "V12", "losers": ["V34"], "counts": {...}}. mode = apply | undo."""
    counts = payload.get("counts") or {}
    total = sum(v for v in counts.values() if isinstance(v, int))
    if config.merge_dry_run():
        verb = "would restore" if mode == "undo" else "would repoint"
        log.info("DRY RUN merge %s %s <- %s by %s (%s)", mode, payload.get("survivor"), payload.get("losers"), user,
                 proposal_id)
        return {"ok": True, "dry_run": True, "result": {"counts": counts},
                "text": f"Dry run: {verb} about {total} rows across {len([v for v in counts.values() if v])} tables "
                        f"into {payload.get('survivor')}. Nothing was written."}
    if not config.merge_url() or not config.merge_secret():
        return {"ok": False, "dry_run": False, "text": "The merge endpoint isn't configured (XANO_MERGE_URL / "
                                                        "XANO_MERGE_SECRET).", "result": {}}
    body = {"mode": mode, "survivor_vendor_id": payload.get("survivor"), "loser_vendor_ids": payload.get("losers"),
            "proposal_id": proposal_id, "approved_by": user, "expected_counts": counts,
            "secret": config.merge_secret()}
    res = _post(config.merge_url(), body)
    if not res.get("ok"):
        return {"ok": False, "dry_run": False, "text": res.get("error", "merge failed"), "result": res}
    moved = res.get("repointed_total", "?")
    return {"ok": True, "dry_run": False, "result": {k: v for k, v in res.items() if k != "secret"},
            "text": (f"Restored {moved} rows from backup {res.get('backup_id', '?')}." if mode == "undo" else
                     f"Repointed {moved} rows into {payload.get('survivor')}; "
                     f"{', '.join(payload.get('losers') or [])} retired (backup {res.get('backup_id', '?')}).")}


# --- vendor data fix (existing staged-edit flow, table 74) ---------------------------------------------
def vendor_edit(payload: dict, user: str, proposal_id: str) -> dict:
    """payload = {"vendor_id", "field", "old_value", "new_value", "note"}. Stage + apply; ep281 re-reads the live
    value and refuses if it drifted since staging, so the approver always applies the diff they saw."""
    vid, field, new = payload.get("vendor_id"), payload.get("field"), payload.get("new_value")
    if config.vendor_edit_dry_run():
        log.info("DRY RUN vendor_edit %s.%s -> %r by %s (%s)", vid, field, new, user, proposal_id)
        return {"ok": True, "dry_run": True, "result": {},
                "text": f"Dry run: would set {field} on {vid} to “{new}”. Nothing was written."}
    if not config.vendor_edit_secret():
        return {"ok": False, "dry_run": False, "text": "VENDOR_EDIT_SECRET isn't set.", "result": {}}
    base = config.vendor_edit_base().rstrip("/")
    st = _post(base + "/vendor/edit/stage", {"vendor_id": vid, "field": field, "new_value": new,
                                             "proposed_by": "tulle-bot", "note": f"{payload.get('note', '')} "
                                             f"(proposal {proposal_id})"[:500], "source": "tulle-bot",
                                             "secret": config.vendor_edit_secret()})
    if not st.get("ok"):
        return {"ok": False, "dry_run": False, "text": f"Staging failed: {st.get('error')}", "result": {}}
    edit_id = st.get("edit_id") or st.get("id") or (st.get("edit") or {}).get("id")
    staged_prev = st.get("previous_value")
    if payload.get("old_value") is not None and staged_prev is not None \
            and str(staged_prev).strip() != str(payload.get("old_value")).strip():
        requests_discard = _post(base + "/vendor/edit/discard", {"edit_id": edit_id, "discarded_by": user,
                                                                 "secret": config.vendor_edit_secret()})
        log.info("vendor_edit drift: discarded staged edit %s (%s)", edit_id, requests_discard.get("ok"))
        return {"ok": False, "dry_run": False, "result": {},
                "text": f"{field} on {vid} changed since this was proposed (now “{staged_prev}”), so I didn't apply it."}
    ap = _post(base + "/vendor/edit/apply", {"edit_id": edit_id, "applied_by": user,
                                             "secret": config.vendor_edit_secret()})
    if not ap.get("ok"):
        return {"ok": False, "dry_run": False, "text": f"Apply refused: {ap.get('error')}", "result": {}}
    return {"ok": True, "dry_run": False, "result": {"edit_id": edit_id},
            "text": f"Set {field} on {vid} to “{new}” (edit {edit_id}; the old value is kept for rollback)."}


# --- task -----------------------------------------------------------------------------------------------
def task(payload: dict, user: str, proposal_id: str) -> dict:
    title = (payload.get("title") or "Feedback follow-up")[:200]
    if config.task_dry_run():
        log.info("DRY RUN task %r by %s (%s)", title, user, proposal_id)
        return {"ok": True, "dry_run": True, "result": {},
                "text": f"Dry run: would open a GitHub issue “{title}” in {config.task_repo()}."}
    if not config.task_github_token():
        return {"ok": False, "dry_run": False, "text": "TASK_GITHUB_TOKEN isn't set.", "result": {}}
    body = (payload.get("body") or "") + f"\n\n_Filed by Tulle Bot from Slack; approved by Slack user {user} " \
                                         f"(proposal {proposal_id})._"
    res = _post(f"https://api.github.com/repos/{config.task_repo()}/issues",
                {"title": title, "body": body[:60000], "labels": ["from-feedback"]},
                headers={"Authorization": f"Bearer {config.task_github_token()}",
                         "Accept": "application/vnd.github+json"})
    if not res.get("ok"):
        return {"ok": False, "dry_run": False, "text": f"GitHub refused: {res.get('error')}", "result": {}}
    return {"ok": True, "dry_run": False, "result": {"url": res.get("html_url")},
            "text": f"Opened <{res.get('html_url')}|issue #{res.get('number')}>."}


APPLIERS = {"merge": merge, "vendor_edit": vendor_edit, "task": task}
