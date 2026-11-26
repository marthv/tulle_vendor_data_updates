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
import queue
import threading

import appliers
import config
import pdf_dupes

log = logging.getLogger("slack_qa.merge_checks")

SECONDS = 35.0             # price_impact's own budget for one card
PATIENT_SECONDS = 90.0     # MERGE_CARD_FAST, card building only: room for two slow reads (price_impact.PATIENT_TIMEOUTS)
BREATH = 2.0               # seconds between queued jobs
WAIT = 240.0               # how long a check waits for the one before it
PREVIEW_TIMEOUT = 90.0     # measured 14-65 s live on 2026-11-22 (it counts every reference)
_ONE = threading.Semaphore(1)
_JOBS: queue.Queue = queue.Queue()
_STOP = threading.Event()
_WORKER: list = []
_WLOCK = threading.Lock()


def _work() -> None:
    while not _STOP.is_set():
        try:
            fn = _JOBS.get(timeout=1)
        except queue.Empty:
            continue
        if _STOP.is_set():
            return
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("queued price check failed")
        _STOP.wait(BREATH)                  # a breath between jobs, so a person's click gets in between


def submit(fn) -> None:
    """Background queue: one job at a time, on a daemon thread. stop() drops what is queued (those cards keep their
    "Check price again" button), so a shutdown never waits for it."""
    if _STOP.is_set():
        return
    with _WLOCK:
        if not _WORKER or not _WORKER[0].is_alive():
            _WORKER[:] = [threading.Thread(target=_work, name="price-queue", daemon=True)]
            _WORKER[0].start()
    _JOBS.put(fn)


def stop() -> None:
    _STOP.set()
    while True:
        try:
            _JOBS.get_nowait()
        except queue.Empty:
            return


def touching(store, pid: str, payload: dict) -> list[str]:
    return store.merges_touching([payload.get("survivor")] + list(payload.get("losers") or []), exclude=pid)


def finish(slack, store, pid: str, xano=None) -> None:
    """Compute a posted card's price (with ep578's preview) and redraw the card."""
    import cards
    if xano is None:
        from xano_ro import ReadOnlyXano
        xano = ReadOnlyXano(cache_ttl=0)
    p = store.get(pid)
    if not p or p.get("status") not in ("pending", "dry_run") or current(p["payload"]):
        return
    ans = price(xano, p["payload"])
    ans["touching"] = touching(store, pid, p["payload"])
    p, why = store.set_price(pid, ans, only_if=lambda now: current(dict(now, price=ans)))
    if p and not why and p.get("channel") and p.get("ts"):
        text, blocks = cards.render(p)
        slack.update_message(p["channel"], p["ts"], text, blocks)


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
    md5 = pdf_dupes.md5_map(payload)
    if md5:
        body["pdf_md5"] = md5
    res = appliers._post(config.merge_url(), body, timeout=PREVIEW_TIMEOUT)
    return res if res.get("ok") and res.get("status") == "preview" else None


def slim(pv: dict | None) -> dict | None:
    """The parts of a preview the card keeps: what the price check needs, the endpoint's refusals and the state it saw.
    No field values from the listings beyond current_state."""
    if not pv:
        return None
    pick = lambda rows, keys: [{k: r.get(k) for k in keys} for r in rows or [] if isinstance(r, dict)]  # noqa: E731
    return {"survivor": pv.get("survivor"), "losers": pv.get("losers"), "hide_pdf_ids": pv.get("hide_pdf_ids") or [],
            "t10_plan": pick(pv.get("t10_plan"), ("row_id", "column", "new")),
            "t11_plan": pick(pv.get("t11_plan"), ("column", "role")), "rebuild_plan": pv.get("rebuild_plan"),
            "primary_choice": pv.get("primary_choice"),
            "touches_pricing": pv.get("touches_pricing"), "current_state": pv.get("current_state"),
            "refusals": [str(x) for x in pv.get("refusals") or []]}


def _budget(building: bool = True) -> tuple[float, tuple]:
    """(seconds for one check, per-read timeouts). MERGE_CARD_FAST gives a slow Xano read more time, but only while a
    card is being built (pv == "fetch"); the Approve re-check keeps the old 35 s and 8/12 s. Off = as before."""
    import price_impact
    if building and config.merge_card_fast():
        return PATIENT_SECONDS, price_impact.PATIENT_TIMEOUTS
    return SECONDS, price_impact.TIMEOUTS


def _compute(xano, payload: dict, hide: list[str], pv="fetch") -> dict:
    import price_impact
    building = pv == "fetch"
    pv = slim(preview(payload, hide)) if building else pv
    secs, timeouts = _budget(building)
    kw = {"timeouts": timeouts} if timeouts != price_impact.TIMEOUTS else {}      # off: the very same call as before
    r = price_impact.compute(xano, payload.get("survivor"), list(payload.get("losers") or []), preview=pv,
                             hidden_pdfs=hide, deadline=secs, **kw)
    b = short(r, price_impact.blocks(r))
    extra = list(b["extra_blocks"])
    # refusals a person can answer on the card (hidden / gold / unhide) have their own buttons there
    refusals = [x for x in (pv or {}).get("refusals") or []
                if not x.startswith("stale card") and not appliers.ANSWERABLE.search(x)]
    if refusals:
        extra.insert(0, {"type": "section", "text": {"type": "mrkdwn", "text": (
            ":no_entry: *Xano would refuse this merge as it stands:* "
            + " ".join(appliers.plain_refusals("; ".join(refusals))))[:2900]}})
    return {"status": "ok", "alert": bool(r["alert"]), "cant": bool(r["cant"]), "stamp": str(r["stamp"]),
            "after_total": (r.get("after") or {}).get("total"), "hide": sorted(hide),
            "survivor": payload.get("survivor"), "refusals": refusals, "seconds": r.get("seconds"), "preview": pv,
            # belt and braces: an alert always has a confirm text
            "confirm_text": b["confirm_text"] or ("The price couples see changes. Merge anyway?" if r["alert"] else None),
            "extra_blocks": extra, "detail": b["detail"]}


STRONG = {"can't compute", "price shown or not", "opening price"}     # headline >= 10%, none <-> some, or not computable


def _clip(t: str, n: int) -> str:
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def short(r: dict, b: dict) -> dict:
    """price_impact's answer in the space a card has (its logic is untouched: what alerts, and why, is decided there).
    33 of 40 high-tier groups alert, so the loud header and the strongest confirm are kept for the headline price or
    "can't compute"; everything else is one plain line and a plain confirm. The full text goes to Details."""
    full = list(b["extra_blocks"])
    if not r.get("alert"):
        return {"extra_blocks": full, "confirm_text": b["confirm_text"], "detail": ""}
    kinds, why = list(r.get("kinds") or []), [str(x) for x in r.get("reasons") or []]
    strong = bool(STRONG & set(kinds)) or bool(r.get("cant")) or not kinds
    esc = pdf_dupes._esc
    texts = [x["text"]["text"] for x in full if x.get("type") == "section"]
    figures = next((t for t in texts if "predicted" in t.lower()), texts[-1] if texts else "")
    keep = [ln for ln in figures.split("\n") if ln.startswith("• `V") or ln.startswith("• Kept listing")]
    top = "; ".join(_clip(x, 140) for x in why[:2]) + (f"; and {len(why) - 2} more under Details" if len(why) > 2 else "")
    out = []
    if strong:
        out.append({"type": "header", "text": {"type": "plain_text", "text": _clip("PRICE ALERT: " + (why or ["see below"])[0], 148)}})
        if len(why) > 1:
            out.append({"type": "section", "text": {"type": "mrkdwn", "text": esc(_clip("Also: " + "; ".join(
                _clip(x, 140) for x in why[1:3]) + (f"; and {len(why) - 3} more under Details" if len(why) > 3 else ""), 700))}})
    else:
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": esc(_clip(
            "Price note (the headline price moves less than 10%): " + top + ".", 700))}})
    if keep:
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": _clip("\n".join(keep), 1800)}})
    import price_impact
    out.append({"type": "context", "elements": [{"type": "mrkdwn", "text": esc(getattr(
        price_impact, "PREDICTED", "Predicted from stored data")) + "."}]})
    first = esc(_clip((why or ["the price couples see changes"])[0], 180))
    confirm = f"Price alert: {first}. Merge anyway?" if strong else f"Note: {first}. Continue with the merge?"
    return {"extra_blocks": out, "confirm_text": confirm, "detail": "\n\n".join(texts)[:5500]}


def price(xano, payload: dict, seconds: float | None = None, wait: float = WAIT, pv="fetch") -> dict:
    """Never raises and never hangs. Always an alert when it can't be sure. pv: "fetch" asks ep578 for a preview (slow:
    card building only); the Approve click passes the one the card was built with and re-reads the price rows."""
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
            box["r"] = _compute(xano, payload, hide, pv)
        except Exception as e:  # noqa: BLE001
            log.warning("price check failed: %s", type(e).__name__)
            box["r"] = _failed("the check failed", payload, hide)
        finally:
            _ONE.release()                  # released by the worker: a timed-out check never overlaps the next one

    t = threading.Thread(target=run, name="price-check", daemon=True)
    t.start()
    t.join((seconds if seconds is not None else _budget(pv == "fetch")[0]) + PREVIEW_TIMEOUT + 10)
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
