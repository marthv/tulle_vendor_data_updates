"""Replay the 'breakdown' ask from user 31762's 10-05 beta feedback (1/5, "i need more breakdown of full
costs"): opening, then "breakdown <first pick> in a realistic way". Before the COST BREAKDOWN rule the
reply was one sentence with a range. SPENDS MONEY (~$0.10 per run). Profile fields only, no personal data.
    python evals/replay_breakdown.py [runs]"""
import json
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_evals  # noqa: E402  (loads secrets + tokens the same way)
import agent  # noqa: E402
import guards  # noqa: E402
from app import _turn_for_model, OPENING_ASK  # noqa: E402

PROFILE = {"Wedding_Location_Updated": ["Florida", "Louisiana", "California"], "Wedding_Budget": 55000,
           "Wedding_Guest_Count": 100, "Planning_Phase": "Engaged and just started planning"}
LINES = ("rental", "ceremony", "food", "bar", "service", "tax", "total")


def one_run(token):
    cost, stored = 0.0, []
    msgs = [{"role": "user", "content": OPENING_ASK}]
    res, usage = agent.run(token, {"id": 0}, True, msgs, profile_override=PROFILE)
    cost += guards.cost_usd(usage["model"], usage)
    stored.append({"role": "assistant", "text": res["text"], "cards": res["cards"], "chips": res["chips"]})
    name = res["cards"][0]["name"] if res["cards"] else "your top pick"
    ask = "breakdown %s in a realistic way" % name
    msgs = [{"role": "user", "content": OPENING_ASK}] + [_turn_for_model(t) for t in stored] + \
           [{"role": "user", "content": ask}]
    res2, usage = agent.run(token, {"id": 0}, True, msgs, profile_override=PROFILE,
                             prior_vendor_ids=[c["vendor_id"] for c in res["cards"]])
    cost += guards.cost_usd(usage["model"], usage)
    text = res2["text"]
    return {"cost": round(cost, 3), "venue": name, "words": len(text.split()),
            "dollar_figures": len(re.findall(r"\$\s?\d", text)),
            "list_lines": sum(1 for l in text.splitlines() if l.strip().startswith("•")),
            "lines_named": [w for w in LINES if re.search(w, text, re.I)],
            "cards": [c["name"] for c in res2["cards"]], "chips": res2["chips"], "reply": text}


if __name__ == "__main__":
    import os
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = [one_run(os.environ["REC_TEST_TOKEN"]) for _ in range(runs)]
    for o in out:
        print({k: v for k, v in o.items() if k != "reply"})
        print(o["reply"], "\n")
    (pathlib.Path(__file__).parent / "results_replay_breakdown.json").write_text(json.dumps(out, indent=1))
