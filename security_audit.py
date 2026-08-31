"""Drift detector for the Xano API surface. Run on a schedule; alerts only on NEW exposure.

THE PROBLEM THIS SOLVES
-----------------------
Xano creates every endpoint with `auth = false`. Security is opt-in, per endpoint, forever.
There is no group-level default-deny to switch on — `updateApiGroupSecurity` only rotates the
group's GUID/canonical, it does not set an auth policy for the endpoints inside. So the
surface drifts open every time anyone adds an endpoint, and nothing surfaces that.

That is how 2026-08-30 happened: an unauthenticated `/payment_log` returning every customer
email and amount, a `/venue_pricing_dashboard` returning 27,287 pricing rows across 10,777
vendors to anyone, an `/impersonate` that minted a 24h token for any account, and two public
Apify actors quietly scraping it all. None of it was a new bug. It was accumulated default.

WHY A BASELINE INSTEAD OF AN ABSOLUTE RULE
------------------------------------------
A plain "alert on every anonymous endpoint" report cries wolf: ~60 endpoints are legitimately
anonymous (the free market-rate panel, the logged-out type-ahead, the pipeline's category
map, health checks). An alert nobody can act on gets muted, and a muted alert is worse than
none. So this compares against a committed baseline and reports only the DIFFERENCE — an
endpoint that is newly anonymous, or newly appeared and is anonymous. Reviewing that diff is
a small, honest task; reviewing 60 lines a day is not.

Update the baseline deliberately, as a reviewed commit, and the file doubles as the record of
which exposures are intentional and why.

CLASSIFICATION
--------------
An endpoint counts as GATED if it requires a user token (`auth`) or takes a `secret` input
(the machine-caller convention used by ep199/205/206/213/214 and the vendor portal).
Everything else is reachable by anyone on the internet.

    machine callers (dashboard, pipeline) -> secret gate
    browser callers (WeWeb, logged-in)    -> auth = "user"

Mixing those up is its own outage: user auth on a machine endpoint silently breaks a
dashboard tab (see endpoint_health.py), and a secret on a browser endpoint ships the secret
to every visitor.

SETUP
-----
    XANO_METADATA_TOKEN  — Xano metadata API token (Account -> Tokens; needs read on the
                           workspace). Read-only usage here.
    SLACK_WEBHOOK_URL    — optional; posts the diff to #feedback when non-empty.
    XANO_WORKSPACE_ID    — defaults to 1.

    python security_audit.py            # report drift, exit 1 if any
    python security_audit.py --baseline # rewrite the baseline from live state (review the diff!)
"""

import json
import os
import sys

import requests

META_BASE = os.environ.get("XANO_META_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io/api:meta")
WORKSPACE_ID = int(os.environ.get("XANO_WORKSPACE_ID", "1"))
BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "security_baseline.json")
API_GROUPS = [4, 10]     # Weweb (WGW_G49d) and WeWeb Transparency Project (GynP5T1B)


def fetch_endpoints(token):
    """[(group, id, verb, name, gated)] for every endpoint in the audited groups."""
    out, headers = [], {"Authorization": f"Bearer {token}"}
    for g in API_GROUPS:
        page = 1
        while True:
            r = requests.get(
                f"{META_BASE}/workspace/{WORKSPACE_ID}/apigroup/{g}/api",
                headers=headers, params={"page": page, "per_page": 100}, timeout=60,
            )
            r.raise_for_status()
            body = r.json()
            items = body.get("items", body if isinstance(body, list) else [])
            if not items:
                break
            for a in items:
                inputs = [i.get("name", "") for i in (a.get("input") or [])]
                gated = bool(a.get("auth")) or any(n.lower() == "secret" for n in inputs)
                out.append((g, a["id"], a.get("verb", "?"), a.get("name", "?"), gated))
            if not body.get("nextPage"):
                break
            page += 1
    return out


def load_baseline():
    try:
        with open(BASELINE_PATH, encoding="utf-8") as f:
            return {tuple(k.split("|", 1)) for k in json.load(f)["known_anonymous"]}
    except Exception:
        return set()


def save_baseline(anon):
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "_comment": "Endpoints intentionally reachable without auth or a secret. "
                        "Adding a line here is a decision to expose that endpoint publicly - "
                        "make it in a reviewed commit, not to silence an alert.",
            "known_anonymous": sorted(f"{g}|{n}" for g, n in anon),
        }, f, indent=2)
        f.write("\n")


def audit(token):
    eps = fetch_endpoints(token)
    anon = {(str(g), name) for g, _i, _v, name, gated in eps if not gated}
    baseline = load_baseline()
    new = sorted(anon - baseline)
    healed = sorted(baseline - anon)
    return eps, anon, new, healed


def post_slack(new, healed, total, anon_count):
    url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not url or not new:
        return
    lines = [f":lock: *Xano exposure drift* — {len(new)} newly public endpoint(s)",
             f"_{anon_count} of {total} endpoints are reachable without auth or a secret._", ""]
    lines += [f"• `group {g}` *{n}*" for g, n in new]
    if healed:
        lines += ["", f"_Also closed since the baseline: {len(healed)}._"]
    lines += ["", "Gate it, or add it to `security_baseline.json` if it is meant to be public."]
    try:
        requests.post(url, json={"text": "\n".join(lines)}, timeout=20)
    except Exception as e:
        print(f"slack post failed: {e}", file=sys.stderr)


def main():
    token = os.environ.get("XANO_METADATA_TOKEN", "")
    if not token:
        print("XANO_METADATA_TOKEN is not set.", file=sys.stderr)
        return 2

    eps, anon, new, healed = audit(token)
    print(f"{len(eps)} endpoints · {len(anon)} anonymous · "
          f"{len(eps) - len(anon)} gated by auth or secret")

    if "--baseline" in sys.argv:
        save_baseline(anon)
        print(f"baseline rewritten with {len(anon)} entries -> {BASELINE_PATH}")
        return 0

    if healed:
        print(f"\nclosed since baseline ({len(healed)}):")
        for g, n in healed:
            print(f"  group {g}  {n}")

    if not new:
        print("\nno new exposure.")
        return 0

    print(f"\nNEW EXPOSURE ({len(new)}):")
    for g, n in new:
        print(f"  group {g}  {n}")
    post_slack(new, healed, len(eps), len(anon))
    return 1


if __name__ == "__main__":
    sys.exit(main())
