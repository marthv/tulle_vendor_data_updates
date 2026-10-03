"""Run the 30-case test set against the real tools and the real model. SPENDS MONEY (~$2-4 on Sonnet 5.5).

Needs: ANTHROPIC_API_KEY, XANO_METADATA_TOKEN, and REC_TEST_TOKEN = a Xano auth token for a TEST
account (the search endpoint requires sign-in). Profiles come from cases.json, never from real users.
Does NOT write rec_usage. Usage:  python evals/run_evals.py [--paid] [--only o01,r03] [--model claude-opus-5-5]
Writes evals/results_<model>_<free|paid>.json and prints a summary table."""
import json
import os
import pathlib
import statistics as st
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Secrets are read HERE, in Python, from a file outside every repo - never via a shell `source`
# (2026-10-03: sourcing it in bash echoed both secrets into the transcript). Values are never printed.
ENV_FILE = pathlib.Path(os.environ.get("REC_ENV_FILE", pathlib.Path.home() / ".tulle_rec.env"))
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
# Local runs: reuse the Xano metadata token the tulletogether repo's MCP config already holds.
_MCP = pathlib.Path(os.environ.get("TULLE_MCP_JSON", pathlib.Path.home() / "Documents/GitHub/tulletogether/.mcp.json"))
if "XANO_METADATA_TOKEN" not in os.environ and _MCP.exists():
    _auth = json.loads(_MCP.read_text())["mcpServers"]["xano-meta"]["headers"]["Authorization"]
    os.environ["XANO_METADATA_TOKEN"] = _auth.replace("Bearer ", "")

import config  # noqa: E402

if "--model" in sys.argv:
    config.MODEL = sys.argv[sys.argv.index("--model") + 1]
import agent  # noqa: E402
import guards  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
OPENING = ("Recommend 3 venues for us based on our profile, and tell us the single most useful next step "
           "for where we are in planning.")


def check(case, res):
    """Automatic checks. Quality of the writing is judged by a human reading results_*.json."""
    issues = []
    if len(res["cards"]) < 2 and case["id"] not in ("r04", "r11", "r13", "r14"):
        issues.append("fewer than 2 venue cards")
    if res["dropped_unverified_ids"]:
        issues.append("model referenced ids not from search: %s" % res["dropped_unverified_ids"])
    if not res["text"]:
        issues.append("no message (present_recommendations not called)")
    if not res["chips"]:
        issues.append("no chips")
    return issues


def main():
    token = os.environ.get("REC_TEST_TOKEN")
    if not token:
        sys.exit("REC_TEST_TOKEN not set (auth token of a TEST account)")
    paid = "--paid" in sys.argv
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    cases = [c for c in json.loads((HERE / "cases.json").read_text())["cases"] if not only or c["id"] in only]
    out = []
    for c in cases:
        msgs = [{"role": "user", "content": OPENING if c["kind"] == "opening" else c["ask"]}]
        t0 = time.time()
        try:
            res, usage = agent.run(token, {"id": 0}, paid, msgs, profile_override=c["profile"])
            cost = guards.cost_usd(usage["model"], usage)
            row = {"id": c["id"], "ok": True, "cost": cost, "secs": round(time.time() - t0, 1),
                   "tool_calls": usage["tool_calls"], "model": usage["model"], "issues": check(c, res), "result": res}
        except Exception as e:
            row = {"id": c["id"], "ok": False, "error": repr(e)[:300], "secs": round(time.time() - t0, 1)}
        out.append(row)
        print("%-4s %-5s $%-7s %5ss tools=%-2s %s" % (row["id"], "ok" if row["ok"] else "ERR", row.get("cost", "-"),
              row["secs"], row.get("tool_calls", "-"), "; ".join(row.get("issues", [])) or row.get("error", "")))
    ok = [r for r in out if r["ok"]]
    name = HERE / ("results_%s_%s.json" % (config.MODEL, "paid" if paid else "free"))
    name.write_text(json.dumps(out, indent=1))
    if ok:
        print("\n%d/%d ran | clean (no issues) %d | mean $%.4f | p50 %.1fs | max %.1fs | total $%.2f" % (
            len(ok), len(out), sum(1 for r in ok if not r["issues"]), st.mean(r["cost"] for r in ok),
            st.median(r["secs"] for r in ok), max(r["secs"] for r in ok), sum(r["cost"] for r in ok)))
    print("details:", name)


if __name__ == "__main__":
    main()
