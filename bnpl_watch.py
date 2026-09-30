"""Geo-pricing + BNPL experiment watch (Oct 2026) - one section of the daily health DM.

    python bnpl_watch.py              # print the section (needs XANO_METADATA_TOKEN)

The experiment self-activated 2026-10-01 00:00 America/New_York. Three arms, assigned by the
buyer's Wedding_Location_Updated with the SAME rule as the WeWeb Forever button binding
(contains, case-insensitive, comma-packed items split, raised beats Texas):

    raised   New York / California / International   $149, BNPL on
    control  Texas                                   $129, BNPL on
    baseline everyone else                           $129, card only

What this reports, and why each number:
  * New-Forever purchases ("forever weeks" in the payment log) per arm, since launch and in the
    last 24h, against the same arm's daily rate over the 28 days BEFORE launch.
  * RAISED ARM SHARE of new-Forever purchases, pre vs post. Raw daily counts swing with
    traffic; the baseline arm is untreated, so if the $149 price hurts, the raised arm's share
    of purchases falls while the baseline's does not. This is the headline number.
  * Price mismatches: a raised-arm buyer paying the $129 link, or a baseline buyer paying $149,
    means the routing is wrong somewhere. Amounts include sales tax (NY $129 = $140.45), and
    promo codes lower them, so the bands are heuristics - a mismatch is a thing to LOOK AT.
  * BNPL share: the payment log has no method column. ep30's Slack post in #tulle-users ends
    with "Payment Method: ...", so the method is read from there. Best-effort: if the bot
    cannot read the channel, the section says so instead of guessing.
  * ep30 (Stripe webhook) non-200s in the last 24h - a failed webhook = paid but no access.

Upgrades ("forever" type, priced by fn63 with a credit) are counted separately and not
arm-compared: their amounts depend on what the buyer already paid.
"""

import datetime as dt
import os
import re
import sys

import requests

META_BASE = os.environ.get("XANO_META_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta")
WORKSPACE_ID = int(os.environ.get("XANO_WORKSPACE_ID", "1"))

LAUNCH_MS = 1790827200000          # 2026-10-01 00:00 America/New_York - same gate as WeWeb/fn63
PAYMENT_LOG_TABLE = 16             # DONATION - Payment Log
USER_TABLE = 1
EP30 = 30                          # Stripe webhook
PRE_DAYS = 28
TULLE_USERS_CHANNEL = os.environ.get("BNPL_SLACK_CHANNEL_ID", "C07D9U0FZF0")
# $129 + max US sales tax (~10.25%) = ~$142.2. $149 with TULLE15 = $126.65, so a discounted
# raised buyer lands in the $129 band - hence mismatches are flagged "check", not "error".
PRICE_149_MIN = 145.0

ARMS = ("raised", "control", "baseline")


def arm_for(location):
    raw = location
    items = raw if isinstance(raw, list) else ([raw] if raw else [])
    tokens = set()
    for it in items:
        for part in str(it if it is not None else "").split(","):
            t = part.strip().lower()
            if t:
                tokens.add(t)
    if tokens & {"new york", "california", "international"}:
        return "raised"
    if "texas" in tokens:
        return "control"
    return "baseline"


# --------------------------------------------------------------------------- fetching

def _get(path, token, **params):
    r = requests.get(f"{META_BASE}/workspace/{WORKSPACE_ID}{path}",
                     headers={"Authorization": f"Bearer {token}"}, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_payments(token, since_ms):
    """Payment-log rows at or after since_ms. The metadata API IGNORES sort/order here
    (2026-09-30: page 1 came back as ids 875-995), so no early stop is safe - read every page
    and filter. ~1.3k rows, growing ~10/day, so this is ~14 small requests."""
    out, page = [], 1
    while page <= 60:
        body = _get(f"/table/{PAYMENT_LOG_TABLE}/content", token, page=page, per_page=100)
        items = body.get("items", [])
        if not items:
            break
        out += items
        if not body.get("nextPage"):
            break
        page += 1
    return [i for i in out if (i.get("Time_of_Payment") or 0) >= since_ms]


def fetch_locations(token, user_ids):
    locs = {}
    for uid in user_ids:
        try:
            u = _get(f"/table/{USER_TABLE}/content/{uid}", token)
            locs[uid] = u.get("Wedding_Location_Updated")
        except Exception:
            locs[uid] = None          # unknown -> baseline, and counted as unresolved
    return locs


def fetch_ep30_failures(token, hours=24):
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    r = requests.post(f"{META_BASE}/workspace/{WORKSPACE_ID}/request_history/search",
                      headers={"Authorization": f"Bearer {token}"}, timeout=120,
                      json={"page": 1, "per_page": 200, "query_id": EP30,
                            "sort": {"created_at": "desc"}})
    r.raise_for_status()
    items = r.json().get("items", [])
    recent = [i for i in items
              if dt.datetime.strptime(i["created_at"][:19], "%Y-%m-%d %H:%M:%S") >= cutoff]
    return len(recent), [i for i in recent if i.get("status") != 200]


def fetch_slack_methods(since_ms):
    """{email_lower: method} from ep30's posts in #tulle-users since since_ms, or an error
    string. Needs HEALTH_SLACK_BOT_TOKEN with groups:history AND the bot in the channel."""
    token = os.environ.get("HEALTH_SLACK_BOT_TOKEN", "")
    if not token:
        return "HEALTH_SLACK_BOT_TOKEN not set"
    methods, cursor = {}, None
    for _ in range(20):
        params = {"channel": TULLE_USERS_CHANNEL, "oldest": f"{since_ms / 1000:.0f}", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = requests.get("https://slack.com/api/conversations.history",
                         headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
        body = r.json()
        if not body.get("ok"):
            return body.get("error", f"HTTP {r.status_code}")
        for m in body.get("messages", []):
            text = m.get("text", "")
            if "Payment Method:" not in text:
                continue
            em = re.search(r"mailto:([^|>]+)", text) or re.search(r"\(([^()\s]+@[^()\s]+)\)", text)
            pm = re.search(r"Payment Method:\s*([^\n]+)", text)
            if em and pm:
                methods[em.group(1).strip().lower()] = pm.group(1).strip()
        cursor = (body.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    return methods


# ep30 writes "BNPL - <provider> (paid over time)"; provider names kept as a backstop.
BNPL_WORDS = ("bnpl", "affirm", "klarna", "afterpay", "clearpay")


def is_bnpl(method):
    m = (method or "").lower()
    return any(w in m for w in BNPL_WORDS)


# --------------------------------------------------------------------------- report

def build(token, now_ms=None):
    now_ms = now_ms or int(dt.datetime.utcnow().timestamp() * 1000)
    live = now_ms >= LAUNCH_MS
    pre_start = LAUNCH_MS - PRE_DAYS * 86400000
    rows = fetch_payments(token, pre_start)
    forever = [r for r in rows if (r.get("Type") or "").strip().lower() in ("forever weeks", "forever")]
    uids = {str(r.get("Client_Reference_ID") or "").strip() for r in forever}
    uids.discard("")
    locs = fetch_locations(token, sorted(uids))

    def arm(r):
        return arm_for(locs.get(str(r.get("Client_Reference_ID") or "").strip()))

    new = [r for r in forever if (r.get("Type") or "").strip().lower() == "forever weeks"]
    pre = [r for r in new if (r["Time_of_Payment"] or 0) < LAUNCH_MS]
    post = [r for r in new if (r["Time_of_Payment"] or 0) >= LAUNCH_MS]
    last24 = [r for r in post if r["Time_of_Payment"] >= now_ms - 86400000]
    upgrades_post = [r for r in forever if (r.get("Type") or "").strip().lower() == "forever"
                     and r["Time_of_Payment"] >= LAUNCH_MS]
    post_days = max((now_ms - LAUNCH_MS) / 86400000, 1e-9)

    def count(rs):
        c = {a: 0 for a in ARMS}
        for r in rs:
            c[arm(r)] += 1
        return c

    cpre, cpost, c24 = count(pre), count(post), count(last24)
    rev_post = {a: sum(float(r.get("Amount") or 0) for r in post if arm(r) == a) for a in ARMS}

    def share(c):
        tot = sum(c.values())
        return (100.0 * c["raised"] / tot) if tot else None

    lines = [f"*Geo pricing + BNPL experiment* "
             f"({'LIVE since 10-01 00:00 ET, day ' + format(post_days, '.1f') if live else 'not live yet - pre-launch baseline only'})"]
    lines.append("```")
    lines.append(f"{'arm':<9}{'pre/day':>8}{'post/day':>9}{'24h':>5}{'total':>6}{'revenue':>10}")
    for a in ARMS:
        lines.append(f"{a:<9}{cpre[a] / PRE_DAYS:>8.2f}{(cpost[a] / post_days if live else 0):>9.2f}"
                     f"{c24[a]:>5}{cpost[a]:>6}{rev_post[a]:>10.2f}")
    lines.append("```")
    sp, sq = share(cpre), share(cpost)
    lines.append(f"Raised-arm share of new-Forever buys: pre {sp:.0f}% (n={sum(cpre.values())})"
                 if sp is not None else "Raised-arm share pre-launch: no purchases")
    if live:
        lines[-1] += (f" -> post {sq:.0f}% (n={sum(cpost.values())})" if sq is not None
                      else " -> post: no purchases yet")
        if sum(cpost.values()) < 30:
            lines.append("_Too few post-launch buys to call a direction (need ~30+); watch the trend, don't act on it._")

    # Routing check: amount band vs arm.
    mism = []
    for r in post:
        a, amt = arm(r), float(r.get("Amount") or 0)
        if a == "raised" and amt < PRICE_149_MIN:
            mism.append(f"raised buyer paid ${amt:.2f} (row {r['id']}) - $129 link? or promo code")
        elif a != "raised" and amt >= PRICE_149_MIN:
            mism.append(f"{a} buyer paid ${amt:.2f} (row {r['id']}) - $149 leaked outside raised arm?")
    unresolved = sum(1 for u in uids if locs.get(u) is None)
    if live:
        lines.append(f"Routing check: {len(mism)} to look at" + (":" if mism else " :white_check_mark:"))
        lines += [f"  - {m}" for m in mism[:8]]
    if unresolved:
        lines.append(f"_{unresolved} buyer(s) had no readable location - counted as baseline._")
    if upgrades_post:
        lines.append(f"Upgrades to Forever since launch (credit-priced, not arm-compared): {len(upgrades_post)}")

    # BNPL share, from ep30's Slack posts.
    if live:
        methods = fetch_slack_methods(LAUNCH_MS)
        if isinstance(methods, str):
            lines.append(f"BNPL share: _unavailable - could not read #tulle-users ({methods})._")
        else:
            by_arm = {a: [0, 0, 0] for a in ARMS}          # bnpl, matched, unmatched
            for r in post:
                m = methods.get((r.get("User") or "").strip().lower())
                if m is None or m.lower().startswith("unknown"):
                    by_arm[arm(r)][2] += 1
                else:
                    by_arm[arm(r)][1] += 1
                    by_arm[arm(r)][0] += 1 if is_bnpl(m) else 0
            parts = [f"{a} {b}/{n}" for a, (b, n, _) in by_arm.items() if n]
            miss = sum(v[2] for v in by_arm.values())
            lines.append("BNPL share (BNPL/buys with a method): " + (", ".join(parts) or "none yet")
                         + (f" _({miss} buys had no matching Slack post)_" if miss else ""))
            leak = by_arm["baseline"][0]
            if leak:
                lines.append(f":warning: {leak} BASELINE buyer(s) paid with BNPL - BNPL is leaking onto "
                             "the card-only link; that collapses the experiment. Check Stripe's "
                             "default payment method configuration.")

    # Webhook health.
    try:
        n30, bad30 = fetch_ep30_failures(token)
        lines.append(f"Stripe webhook ep30, 24h: {n30} calls, {len(bad30)} non-200"
                     + (" :white_check_mark:" if not bad30 else
                        " :rotating_light: - " + ", ".join(sorted({str(b.get('status')) for b in bad30}))
                        + " (a failed webhook = paid but no access)"))
    except Exception as e:
        lines.append(f"Stripe webhook ep30: could not read history ({e})")

    lines.append("_Kill switch (you run it): `python .claude/xano_backups/revert_geo_pricing.py --off`_")
    return "\n".join(lines)


if __name__ == "__main__":
    tok = os.environ.get("XANO_METADATA_TOKEN", "")
    if not tok:
        sys.exit("XANO_METADATA_TOKEN is not set")
    at = None
    if "--as-of" in sys.argv:           # test the post-launch path: --as-of <epoch ms>
        at = int(sys.argv[sys.argv.index("--as-of") + 1])
    print(build(tok, now_ms=at))
