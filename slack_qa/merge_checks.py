"""Runs the price check for a merge card and keeps it honest (owner 2026-11-22, PASS_BAR Amendments 1-2).

price(xano, payload) -> the small record stored as payload["price"] and shown on the card:
  {status: ok | computing | failed, alert, cant, stamp, after_total, hide, survivor, extra_blocks, confirm_text}
It asks ep578 for its read-only preview when the endpoint is configured (its own plan is the best input), then runs
price_impact.compute. Any failure or time-out is the alert "couldn't compute" plus the confirm step, never a pass.

Gentle on Xano: one price check at a time in the whole process (a scan's cards queue behind each other and other checks
wait their turn), and price_impact reads a few things at a time.
"""
from __future__ import annotations

import logging
import threading

import appliers
import config
import pdf_dupes

log = logging.getLogger("slack_qa.merge_checks")

SECONDS = 35.0             # price_impact's own budget for one card
WAIT = 120.0               # how long a check waits for the one before it
PREVIEW_TIMEOUT = 45.0     # measured 18-25+ s on 2026-11-22 (it counts every reference)
_ONE = threading.Semaphore(1)


def _failed(why: str, payload: dict, hide) -> dict:
    text = "Couldn't compute what this merge does to the price couples see"
    return {"status": "failed", "alert": True, "cant": True, "stamp": "failed", "after_total": None,
            "hide": sorted(hide), "survivor": payload.get("survivor"), "refusals": [],
            "confirm_text": f"{text} ({why}). Merge anyway?",
            "extra_blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "PRICE ALERT: couldn't compute the price change"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": (
                    f"{text} ({pdf_dupes._esc(why)}). Press *Check price again*, or compare both pages yourself before "
                    "approving.")}}]}


def preview(payload: dict, hide: list[str]) -> dict | None:
    """ep578's read-only preview, or None when it isn't configured or didn't answer properly."""
    if not config.merge_url() or not config.merge_secret():
        return None
    body = {"mode": "preview", "survivor_vendor_id": payload.get("survivor"),
            "loser_vendor_ids": payload.get("losers"), "secret": config.merge_secret()}
    if hide:
        body["hide_pdf_ids"] = hide
    res = appliers._post(config.merge_url(), body, timeout=PREVIEW_TIMEOUT)
    return res if res.get("ok") and res.get("status") == "preview" else None


def _compute(xano, payload: dict, hide: list[str]) -> dict:
    import price_impact
    pv = preview(payload, hide)
    r = price_impact.compute(xano, payload.get("survivor"), list(payload.get("losers") or []), preview=pv,
                             hidden_pdfs=hide, deadline=SECONDS)
    b = price_impact.blocks(r)
    extra = list(b["extra_blocks"])
    refusals = [str(x).split("; pass allow_")[0] for x in (pv or {}).get("refusals") or [] if not str(x).startswith("stale card")]
    if refusals:
        extra.insert(0, {"type": "section", "text": {"type": "mrkdwn", "text": (
            ":no_entry: *Xano would refuse this merge as it stands:* "
            + "; ".join(pdf_dupes._esc(x) for x in refusals[:4]))[:2900]}})
    return {"status": "ok", "alert": bool(r["alert"]), "cant": bool(r["cant"]), "stamp": str(r["stamp"]),
            "after_total": (r.get("after") or {}).get("total"), "hide": sorted(hide),
            "survivor": payload.get("survivor"), "refusals": refusals, "seconds": r.get("seconds"),
            # belt and braces: an alert always has a confirm text
            "confirm_text": b["confirm_text"] or ("The price couples see changes. Merge anyway?" if r["alert"] else None),
            "extra_blocks": extra}


def price(xano, payload: dict, seconds: float = SECONDS, wait: float = WAIT) -> dict:
    """Never raises and never hangs. Always an alert when it can't be sure."""
    try:
        hide = pdf_dupes.hide_ids(payload)
    except (ValueError, KeyError) as e:
        return _failed(str(e), payload, [])
    if xano is None or not getattr(xano, "token", "") or not hasattr(xano, "http"):
        return _failed("I can't read prices from here", payload, hide)
    if not _ONE.acquire(timeout=wait):
        return _failed("other price checks were still running", payload, hide)
    box: dict = {}

    def run():
        try:
            box["r"] = _compute(xano, payload, hide)
        except Exception as e:  # noqa: BLE001
            log.warning("price check failed: %s", type(e).__name__)
            box["r"] = _failed("the check failed", payload, hide)
        finally:
            _ONE.release()                  # released by the worker: a timed-out check never overlaps the next one

    t = threading.Thread(target=run, name="price-check", daemon=True)
    t.start()
    t.join(seconds + PREVIEW_TIMEOUT + 10)
    return box.get("r") or _failed("the check took too long", payload, hide)


def computing(payload: dict) -> dict:
    return {"status": "computing", "alert": True, "cant": True, "stamp": "computing", "after_total": None, "hide": [],
            "survivor": payload.get("survivor"), "refusals": [], "confirm_text": None, "extra_blocks": [
                {"type": "context", "elements": [{"type": "mrkdwn", "text": (
                    ":hourglass_flowing_sand: Working out the price couples would see (up to a minute). Approve comes "
                    "back when it's done.")}]}]}


def current(payload: dict) -> bool:
    """True when payload["price"] is a finished answer for exactly this survivor and these hide choices."""
    p = payload.get("price") or {}
    try:
        hide = sorted(pdf_dupes.hide_ids(payload))
    except (ValueError, KeyError):
        return False
    return p.get("status") in ("ok", "failed") and p.get("survivor") == payload.get("survivor") and p.get("hide") == hide


def moved(old: dict, new: dict) -> bool:
    """Did the answer change since the approver saw it? Two "couldn't compute" answers with the same after-price count as
    the same (the approver confirmed exactly that); anything else must match its stamp."""
    if old.get("cant") and new.get("cant"):
        return old.get("after_total") != new.get("after_total")
    return old.get("stamp") != new.get("stamp")
