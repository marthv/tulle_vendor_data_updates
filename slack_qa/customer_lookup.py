"""Customer lookup on @-mention (owner 2026-11-21: Tulle Bot takes over the retired Tulle Ops `/tulle view-as`).

  @Tulle Bot look up jane@example.com      @Tulle Bot what does user 34819 see?

Answers "what does this customer see right now?" from Xano's read-only `POST /admin/customer_snapshot` (api group 3):
plan and days left, whether Pricing Intelligence is unlocked, free PDF views left, what the upgrade banner offers,
and activity. No login as the customer, no session, nothing written.

Privacy (kept from Tulle Ops, on purpose):
* Only people on LOOKUP_USER_IDS can use it. Empty = nobody (deny on empty). This list is separate from QA_APPROVERS:
  someone trusted to fix a venue's website isn't automatically trusted to read a customer's payments.
* The answer is EPHEMERAL - only the person who asked sees it. Nothing is posted to the channel, stored or logged
  (the log line has the asker and the outcome, never the email or the answer).
* Its own secret, CUSTOMER_LOOKUP_SECRET (Tulle Ops' XANO_IMPERSONATE_KEY), separate from VENDOR_EDIT_SECRET.
No model call."""
from __future__ import annotations

import logging
import os
import re
import time

from cards import esc

log = logging.getLogger("slack_qa.lookup")

TARGET = r"(?P<email>[\w.+-]+@[\w-]+(?:\.[\w-]+)+)|(?:user|customer|couple|account)\s*(?:id\s*)?#?(?P<uid>\d{1,7})"
LOOKUP = re.compile(r"^\s*(?:(?:can|could)\s+you\s+)?(?:please\s+)?(?:look\s*up|check|find|pull up|show me)\s+"
                    r"(?:the\s+)?(?:(?:customer|user|couple|account)\s+(?=[\w.+-]+@))?(?:" + TARGET + r")\s*\??\s*$",
                    re.I)
WHAT_SEE = re.compile(r"^\s*what\s+(?:does|can|do)\s+(?:" + TARGET + r")\s+see\b.*$", re.I)
MAILTO = re.compile(r"<mailto:([^|>]+)(?:\|[^>]+)?>")


def parse(question: str) -> dict | None:
    q = MAILTO.sub(lambda m: m.group(1), question or "").strip()
    m = LOOKUP.match(q) or WHAT_SEE.match(q)
    if not m:
        return None
    if m.group("email"):
        return {"email": m.group("email").strip().lower()}
    return {"user_id": int(m.group("uid"))}


def allowed(user: str) -> bool:
    ids = [u.strip() for u in os.environ.get("LOOKUP_USER_IDS", "").split(",") if u.strip()]
    return bool(user) and user in ids


def secret() -> str:
    return os.environ.get("CUSTOMER_LOOKUP_SECRET", "").strip()


def fetch(target: dict, actor: str, post=None) -> dict:
    """Xano resolves by id when user_id > 0, else by email; send exactly one so a typo can't be reinterpreted."""
    import appliers
    import config
    post = post or appliers._post
    body = {"actor_slack_id": actor, "secret": secret()}
    body.update(target)
    return post(config.vendor_edit_base().rstrip("/") + "/admin/customer_snapshot", body)


def _ago(v) -> str:
    if v in (None, "", 0):
        return "last active: unknown"
    try:
        ms = float(v)
        days = int((time.time() * 1000 - ms) // 86_400_000)
        return "active today" if days <= 0 else f"last active {days} day{'s' if days != 1 else ''} ago"
    except (TypeError, ValueError):
        return f"last active {esc(str(v)[:10])}"


def render(snap: dict) -> str:
    u, up = snap.get("user") or {}, snap.get("upgrade") or {}
    tier = u.get("tier")
    days = u.get("days_left")
    access = ("*Forever*, never expires" if tier == "forever"
              else f"*Timed*, {days} day{'s' if days != 1 else ''} left (until {esc(u.get('access_until'))})"
              if tier == "timed"
              else f"*Expired* on {esc(u.get('access_until'))}" if tier == "expired" else "*Free*, never paid")
    pdf = u.get("pdf_views_left")
    pdf_line = ("Free PDF views: unknown" if pdf is None else f"Free PDF views left: {pdf}" if pdf > 0
                else "Free PDF views: *none left*")
    pi = ("Pricing Intelligence *unlocked*" if u.get("pricing_intelligence_unlocked")
          else f"Pricing Intelligence *locked* (paid ${u.get('total_paid', 0)} so far)")
    banner = ("Upgrade banner: hidden (already Forever)" if up.get("already_forever")
              else f"Upgrade banner *showing*: ${up.get('credit')} credit, Forever for *${up.get('final_price')}* "
                   f"(normally ${up.get('forever_price')})" if up.get("eligible")
              else "Upgrade banner: not showing")
    pays = u.get("payments") or 0
    where = ", ".join(u["location"]) if isinstance(u.get("location"), list) and u["location"] else "not set"
    last_vendor = f" · last on *{esc(u.get('last_vendor'))}*" if u.get("last_vendor") else ""
    lines = [
        f"*{esc(u.get('name') or '(no name)')}* · {esc(u.get('email'))} · id {esc(u.get('id'))}",
        f":key: {access}",
        f":bar_chart: {pi}",
        f":page_facing_up: {pdf_line}",
        f":arrow_up: {banner}",
        f":chart_with_upwards_trend: {u.get('pdfs_viewed', 0)} PDFs · {u.get('vendors_viewed', 0)} vendors · "
        f"{u.get('favourites', 0)} saved · {pays} payment{'s' if pays != 1 else ''} totalling ${u.get('total_paid', 0)}",
        f":stopwatch: {_ago(u.get('last_active_at'))}{last_vendor}",
        f":ring: {esc(where)} · {u.get('guests') or '?'} guests · budget "
        f"{'$' + str(u['budget']) if u.get('budget') else 'not set'}",
        "_Only you can see this. Read-only: nothing was changed, and the customer can't tell you looked._",
    ]
    return "\n".join(lines)
