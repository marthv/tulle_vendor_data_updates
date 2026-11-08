"""Replay the November real-chat set (nov_cases.json, 14 chats from the 11-03..11-06 planner log) through the assistant
the way production answers them, and record ACCURACY CHECKS + TIMING per answer. SPENDS MONEY (~$0.03-0.06 per typed
question on Sonnet 5.5; "Show 3 more" and venue-name lookups are free and skipped here: lookups never reach the model).

Production settings are mirrored: OPEN_ENDED on (app._open_ended applied), detail by plan ("full" for Forever,
"free" for free couples), profile fields only. Writes evals/nov_results_<label>.json; prints a summary.
  python evals/nov_run.py <label> [case ...]
REC_CODE_DIR=<other rec_service checkout> runs that code instead (before/after)."""
import json
import os
import pathlib
import re
import statistics as st
import sys
import time

CODE = pathlib.Path(os.environ.get("REC_CODE_DIR") or pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "evals"))
HERE = pathlib.Path(__file__).resolve().parent
import run_evals  # noqa: E402,F401  (secrets + tokens, never printed)
import agent  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402
import guards  # noqa: E402
import lookup  # noqa: E402
import replay_real  # noqa: E402

config.OPEN_ENDED = True
MORE = "Show 3 more"
TRAIL_Q = re.compile(r"\?\s*$")
FAIL = re.compile(r"couldn'?t find|no venues (?:match|found)|something went wrong|try again", re.I)


def _state(c):
    s = c.get("state")
    return (s[0] if isinstance(s, list) and s else s) or ""


def run_case(token, name, case):
    detail = "full" if (case.get("plan") or "").lower().startswith("forever") else "free"
    prof = case["profile"]
    states = set(prof.get("Wedding_Location_Updated") or [])
    guests = int(prof.get("Wedding_Guest_Count") or 0)
    history, out, cost = [], [], 0.0
    for i, ask in enumerate(case["asks"]):
        if ask == MORE:
            r = replay_real._more(history)
            if r:
                history += [replay_real._turn("user", MORE), replay_real._turn("assistant", r["text"], r)]
            continue
        if lookup.detect(ask, (history[-1].get("text") if history else "") or ""):
            out.append({"ask": ask, "skipped": "lookup (fast path, no model)"})
            continue
        msgs, kw = app._model_inputs(history, ask)
        t = time.time()
        try:
            res, usage = agent.run(token, {"id": 0}, detail == "full", msgs, profile_override=prof, detail=detail,
                                   route=True, **kw)
        except Exception as e:
            out.append({"ask": ask, "error": repr(e)[:300]})
            continue
        wall = time.time() - t
        res = app._open_ended(dict(res))
        cost += guards.cost_usd(usage["model"], usage)
        cards = res.get("cards") or []
        venue_cards = [c for c in cards if (c.get("category") or "Venue") == "Venue"]
        out.append({
            "ask": ask, "text": res.get("text"), "cards": [c.get("name") for c in cards],
            "checks": {
                "has_cards": bool(cards),
                "ends_with_question": bool(TRAIL_Q.search(res.get("text") or "")),
                "failure_words": bool(FAIL.search(res.get("text") or "")),
                "out_of_state": [c.get("name") for c in venue_cards if states and _state(c) and _state(c) not in states
                                 and "International" not in states],
                "under_capacity": [c.get("name") for c in venue_cards if guests and (c.get("max_capacity_seated") or 0)
                                   and c["max_capacity_seated"] < guests],
            },
            "timing": {"wall_s": round(wall, 2), "latency_ms": usage.get("latency_ms"),
                       "model_turns": len(usage.get("turn_ms") or []), "turn_ms": usage.get("turn_ms"),
                       "turn_tools": usage.get("turn_tools"), "search_ms": usage.get("search_ms"),
                       "pricing_ms": usage.get("pricing_ms"), "tools": usage.get("tool_breakdown"),
                       "tool_calls": usage.get("tool_calls"), "router_searched": usage.get("router_searched"),
                       "router_ms": usage.get("router_ms")},
            "cost_usd": round(guards.cost_usd(usage["model"], usage), 4)})
        history += [replay_real._turn("user", ask), replay_real._turn("assistant", res.get("text") or "", res)]
    return out, round(cost, 3)


def summary(results):
    rows = [r for c in results.values() for r in c["answers"] if "timing" in r]
    lat = [r["timing"]["latency_ms"] / 1000 for r in rows if r["timing"].get("latency_ms")]
    t1 = [r["timing"]["turn_ms"][0] / 1000 for r in rows if r["timing"].get("turn_ms")]
    tool = [sum((r["timing"].get("tools") or {}).values()) / 1000 for r in rows if r["timing"].get("tools")]
    n = len(rows)
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    return {
        "answers": n, "errors": sum(1 for c in results.values() for r in c["answers"] if "error" in r),
        "latency_p50_s": round(st.median(lat), 2) if lat else None, "latency_p90_s": q(lat, 0.9),
        "turn1_p50_s": round(st.median(t1), 2) if t1 else None,
        "tools_p50_s": round(st.median(tool), 2) if tool else None,
        "model_turns_p50": st.median([r["timing"]["model_turns"] for r in rows]) if rows else None,
        "no_cards": sum(1 for r in rows if not r["checks"]["has_cards"]),
        "ends_with_question": sum(1 for r in rows if r["checks"]["ends_with_question"]),
        "failure_words": sum(1 for r in rows if r["checks"]["failure_words"]),
        "answers_with_out_of_state_cards": sum(1 for r in rows if r["checks"]["out_of_state"]),
        "answers_with_under_capacity_cards": sum(1 for r in rows if r["checks"]["under_capacity"]),
        "cost_usd": round(sum(c["cost_usd"] for c in results.values()), 2)}


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    only = set(sys.argv[2:])
    data = json.loads((HERE / "nov_cases.json").read_text(encoding="utf-8"))["cases"]
    token = os.environ["REC_TEST_TOKEN"]
    results = {}
    for name, case in data.items():
        if only and name not in only:
            continue
        answers, cost = run_case(token, name, case)
        results[name] = {"answers": answers, "cost_usd": cost}
        print(name, "answers", sum(1 for a in answers if "timing" in a), "cost $%.3f" % cost, flush=True)
    s = summary(results)
    (HERE / ("nov_results_%s.json" % label)).write_text(json.dumps({"summary": s, "results": results}, indent=1,
                                                                   ensure_ascii=False), encoding="utf-8")
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
