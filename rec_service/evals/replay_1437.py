"""Replay user 1437's 10-13 conversation (3/5, "Want accurate costs") and check the numbers hold together.
SPENDS MONEY (~$0.35 per run: opening + 6 questions). Profile fields only, no personal data.
    python evals/replay_1437.py [runs]

What 1437 got (chats 60-62): Connecticut food and drink called "about $205 a head" and "about $146 a head" in
the same session (two venues' own capacity bands), no all-in number until they typed a question, and model-made
totals. Pass = per run:
  - every Connecticut per-head food figure stated is the SAME number,
  - every reply that names a venue's total uses the computed estimate (no figure bounced twice),
  - the opening shows an all-in estimate for 160 guests.
"""
import os
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_evals  # noqa: E402  (loads secrets + tokens the same way)
import agent  # noqa: E402
import guards  # noqa: E402
from app import _turn_for_model, OPENING_ASK  # noqa: E402

PROFILE = {"Wedding_Location_Updated": ["Connecticut", "Pennsylvania"], "Wedding_Guest_Count": 160,
           "Wedding_Budget": 60000, "Planning_Phase": "Engaged and just started planning"}
ASKS = ["Is a venue I like realistic?", "What cheaper venues would you recommend?",
        "How about in Rhode Island and Massachusetts",
        "How much should I budget for a 160 person wedding in the northeast ?",
        "What if we focus on off peak dates", "What about the Aldrich mansion"]
CT_FOOD = re.compile(r"Connecticut[^.]{0,80}?\$(\d{2,3}) (?:a|per) (?:head|guest)|"
                     r"\$(\d{2,3}) (?:a|per) (?:head|guest)[^.]{0,60}?Connecticut", re.I)


def one_run(token):
    stored, cost, flags = [], 0.0, []
    for ask in [None] + ASKS:
        msgs = [{"role": "user", "content": OPENING_ASK}] + [_turn_for_model(t) for t in stored]
        if ask:
            msgs.append({"role": "user", "content": ask})
        res, usage = agent.run(token, {"id": 0}, True, msgs, profile_override=PROFILE)
        cost += guards.cost_usd(usage["model"], usage)
        flags.append({"ask": ask or "(opening)", "bounced": usage.get("figure_bounced"),
                      "unsupported": usage.get("unsupported_figures"), "latency_s": usage["latency_ms"] / 1000})
        if ask:
            stored.append({"role": "user", "text": ask, "cards": [], "chips": []})
        stored.append({"role": "assistant", "text": res["text"], "cards": res["cards"], "chips": res["chips"]})
    replies = [t for t in stored if t["role"] == "assistant"]
    ct_food = sorted({int(a or b) for r in replies for a, b in CT_FOOD.findall(r["text"])})
    opening_has_estimate = bool(re.search(r"for 160 guests", replies[0]["text"])) or any(
        c.get("estimate_label") for c in replies[0]["cards"])
    return {"cost": round(cost, 3), "ct_food_figures": ct_food, "ct_food_consistent": len(ct_food) <= 1,
            "opening_has_estimate": opening_has_estimate,
            "unsupported_after_retry": [f for f in flags if f["unsupported"]], "turns": flags,
            "replies": [r["text"] for r in replies]}


if __name__ == "__main__":
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    token = os.environ.get("REC_TEST_TOKEN") or sys.exit("REC_TEST_TOKEN not set (auth token of a TEST account)")
    for i in range(runs):
        r = one_run(token)
        print("=== run %d  cost $%s" % (i + 1, r["cost"]))
        for k in ("ct_food_figures", "ct_food_consistent", "opening_has_estimate", "unsupported_after_retry"):
            print("%s: %s" % (k, r[k]))
        for t, text in zip(r["turns"], r["replies"]):
            print("\n--- %s  (bounced=%s, %.1fs)\n%s" % (t["ask"], t["bounced"], t["latency_s"], text))
