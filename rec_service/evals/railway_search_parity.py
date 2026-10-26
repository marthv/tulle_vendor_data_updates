"""Railway search vs ep119 parity for the queries the Tulle Assistant sends (2026-10-26 rollout check).

    python evals/railway_search_parity.py                 # all cases, table + summary, JSON to --out
    python evals/railway_search_parity.py --only tx_pop60 --gap 1.5

READ-ONLY: GETs only - ep119 (Xano wptp_updated_mappings_search) and the Railway search-api /search - with the
test account's token REC_TEST_TOKEN (read from the environment or ~/.tulle_rec.env, never printed). Calls are
strictly sequential, >= --gap seconds apart (default 1.0).

Each case is a search_venues-shaped call; the query sent is xano.venue_search_params(...) - the exact params
_search_one sends - with the page_size the agent really uses (60 = city / "more" path, 16 = plain search, a
multi-state search splits into ceil(page_size / n_states) per state, min 3; 20 = the retry).

Compared per case: itemsTotal, the first 24 ids in order, the overlap of all ids returned, both latencies
(client wall ms; search-api X-Search-Ms; ep119 X-Query-Cache hit flag). Verdicts:
    exact       same itemsTotal, same ids in the same order
    order_diff  same itemsTotal, same id set, different order
    set_diff    same itemsTotal, different ids returned
    count_diff  itemsTotal differs
    error       either side failed
popular_desc (the assistant's default) is click-driven: ep119's 24 h response cache vs the search-api's nightly
snapshot drift, so the tulle-search replay judges it by id SET. This script still reports the order, and says why.
"""
import argparse
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import requests  # noqa: E402

import config  # noqa: E402
import xano  # noqa: E402

EP119 = config.API_SEARCH_GROUP + "/wptp_updated_mappings_search"

# name, search_venues-shaped kwargs, page_size per state
CASES = [
    ("tx_pop60", dict(states=["Texas"]), 60),
    ("ny_g150_16", dict(states=["New York"], guests=150), 16),
    ("ca_fee10k_16", dict(states=["California"], max_venue_fee=10000), 16),
    ("fl_fb150_g120_16", dict(states=["Florida"], guests=120, max_food_per_person=150), 16),
    ("ga_barn_60", dict(states=["Georgia"], venue_types=["Barn / Ranch"]), 60),
    ("nc_waterfront_16", dict(states=["North Carolina"], vibes=["Waterfront"]), 16),
    ("il_allinc_16", dict(states=["Illinois"], pricing_models=["All-Inclusive"]), 16),
    ("tx_byo_g100_16", dict(states=["Texas"], guests=100, pricing_models=["Raw Space", "Semi-Inclusive"]), 16),
    ("co_outdoor_16", dict(states=["Colorado"], outdoor_ceremony=True), 16),
    ("nj_kw_princeton_16", dict(states=["New Jersey"], keyword="Princeton"), 16),
    ("ma_kw_boston_60", dict(states=["Massachusetts"], keyword="Boston"), 60),
    ("va_recent_16", dict(states=["Virginia"], sort_by="recent_desc"), 16),
    ("pa_capasc_g50_16", dict(states=["Pennsylvania"], guests=50, sort_by="capacity_asc"), 16),
    ("wa_capdesc_16", dict(states=["Washington"], sort_by="capacity_desc"), 16),
    ("intl_16", dict(states=["International"]), 16),
    ("tx_of2_8", dict(states=["Texas"], guests=120), 8),          # Texas + Oklahoma, 16 split per state
    ("ok_of2_8", dict(states=["Oklahoma"], guests=120), 8),
    ("tn_upscale_30", dict(states=["Tennessee"], guests=200, venue_types=["Hotel / Resort", "Country Club / Private Club"],
                           vibes=["Ballroom", "Historic Architecture"]), 30),   # 60 split over 2 states
    ("ca_elope_g6_16", dict(states=["California"], guests=6, max_venue_fee=3000), 16),
    ("ny_rooftop_full_60", dict(states=["New York"], guests=175, max_venue_fee=15000, max_food_per_person=200,
                                vibes=["Rooftop / Skyline Views"]), 60),
    ("az_kw_scottsdale_out_16", dict(states=["Arizona"], keyword="Scottsdale", outdoor_ceremony=True), 16),
    ("mi_retry20", dict(states=["Michigan"], guests=80), 20),
    ("ny_greenhouse_light_16", dict(states=["New York"], vibes=["Greenhouse", "Natural Light / Large Windows"]), 16),
]


def _token():
    t = os.environ.get("REC_TEST_TOKEN")
    if t:
        return t.strip()
    p = pathlib.Path.home() / ".tulle_rec.env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("REC_TEST_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("REC_TEST_TOKEN not set (env or ~/.tulle_rec.env)")


def _call(url, token, params, timeout):
    # One retry on a CONNECTION error only (2026-10-26 run: ~45 ms ConnectionErrors to BOTH hosts from the dev
    # machine, gone on the next call - a local network blip, not either server). Timeouts / HTTP errors are kept.
    for attempt in (1, 2):
        t = time.time()
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={"Authorization": "Bearer " + token})
            break
        except requests.exceptions.Timeout as e:
            return {"ok": False, "why": type(e).__name__, "ms": int((time.time() - t) * 1000)}
        except requests.exceptions.ConnectionError as e:
            if attempt == 2:
                return {"ok": False, "why": type(e).__name__, "ms": int((time.time() - t) * 1000)}
            time.sleep(3)
        except Exception as e:
            return {"ok": False, "why": type(e).__name__, "ms": int((time.time() - t) * 1000)}
    ms = int((time.time() - t) * 1000)
    out = {"ok": r.status_code == 200, "status": r.status_code, "ms": ms,
           "server_ms": r.headers.get("X-Search-Ms"), "cache": r.headers.get("X-Query-Cache")}
    if r.status_code != 200:
        out["why"] = "http_%d" % r.status_code
        return out
    try:
        d = r.json()
    except Exception:
        out.update(ok=False, why="not_json")
        return out
    items = d.get("items") if isinstance(d, dict) else None
    if not isinstance(items, list) or "itemsTotal" not in d:
        out.update(ok=False, why="bad_shape")
        return out
    out.update(total=d.get("itemsTotal"), ids=[it.get("id") for it in items],
               clicks={it.get("id"): it.get("Clicks_on_Venue") for it in items},
               names={it.get("id"): it.get("Name") for it in items}, is_fallback=d.get("is_fallback"))
    return out


def compare(a, b):
    if not (a.get("ok") and b.get("ok")):
        return "error", {}
    ia, ib = a["ids"], b["ids"]
    sa, sb = set(ia), set(ib)
    extra = {"overlap": len(sa & sb), "union": len(sa | sb), "only_ep119": [i for i in ia if i not in sb][:8],
             "only_railway": [i for i in ib if i not in sa][:8], "first24_same": ia[:24] == ib[:24],
             "first_diff_at": next((k for k, (x, y) in enumerate(zip(ia, ib)) if x != y), None)}
    # popular_desc ranks on Clicks_on_Venue: ep119 reads it live, the search-api from the nightly snapshot.
    extra["clicks_changed"] = sum(1 for i in sa & sb if a["clicks"].get(i) != b["clicks"].get(i))
    if a["total"] != b["total"]:
        return "count_diff", extra
    if ia == ib:
        return "exact", extra
    if sa == sb:
        return "order_diff", extra
    return "set_diff", extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=1.0)
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--url", default=config.RAILWAY_SEARCH_URL)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    gap = max(1.0, args.gap)
    token = _token()
    cases = [c for c in CASES if not args.only or c[0] in args.only.split(",")]
    rows, last = [], 0.0

    def paced(url, params):
        nonlocal last
        wait = gap - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        r = _call(url, token, params, args.timeout)
        last = time.time()
        return r

    print("%-26s %-13s %-10s %8s %8s %7s %5s %7s %7s %6s %7s" % ("case", "sort", "verdict", "ep119_n", "rail_n",
          "overlap", "1st24", "ep119ms", "railms", "srv_ms", "clk_chg"))
    for name, kw, page_size in cases:
        params = xano.venue_search_params(page_size=page_size, **kw)
        a = paced(EP119, params)
        b = paced(args.url, params)
        verdict, extra = compare(a, b)
        rows.append({"case": name, "sort": params.get("sort_by"), "page_size": params["page_size"], "params": params,
                     "verdict": verdict, "ep119": {k: v for k, v in a.items() if k not in ("names", "clicks")},
                     "railway": {k: v for k, v in b.items() if k not in ("names", "clicks")}, **extra,
                     "names_only_ep119": [a.get("names", {}).get(i) for i in extra.get("only_ep119", [])],
                     "names_only_railway": [b.get("names", {}).get(i) for i in extra.get("only_railway", [])]})
        print("%-26s %-13s %-10s %8s %8s %7s %5s %7s %7s %6s %7s" % (
            name, params.get("sort_by"), verdict, a.get("total", a.get("why")), b.get("total", b.get("why")),
            "%s/%s" % (extra.get("overlap", "-"), extra.get("union", "-")),
            "y" if extra.get("first24_same") else "n", a.get("ms"), b.get("ms"), b.get("server_ms") or "-",
            extra.get("clicks_changed", "-")),
            flush=True)

    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    def p(vals, q):
        vals = sorted(v for v in vals if v is not None)
        return vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))] if vals else None

    ma = [r["ep119"].get("ms") for r in rows if r["ep119"].get("ok")]
    mb = [r["railway"].get("ms") for r in rows if r["railway"].get("ok")]
    print("\nSUMMARY %s of %d cases | ep119 p50 %s ms p90 %s ms | railway p50 %s ms p90 %s ms"
          % (" ".join("%s=%d" % kv for kv in sorted(counts.items())), len(rows), p(ma, .5), p(ma, .9),
             p(mb, .5), p(mb, .9)))
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
        print("detail ->", args.out)


if __name__ == "__main__":
    main()
