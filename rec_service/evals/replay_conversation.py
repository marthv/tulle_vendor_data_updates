"""Replay a multi-turn conversation (model answers fed back exactly as app.py stores them) and count
repetition. SPENDS MONEY (~$0.25 per run). Case: user 31797's 10-04 chat (1/5 "repetitive and not the
right stuff") - profile fields only, no personal data.  python evals/replay_conversation.py [runs]"""
import json
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_evals  # noqa: E402  (loads secrets + tokens the same way)
import agent  # noqa: E402
import guards  # noqa: E402
from app import _turn_for_model, OPENING_ASK  # noqa: E402

PROFILE = {"Wedding_Location_Updated": ["California", "Hawaii"], "Wedding_Budget": 10000,
           "Planning_Phase": "Engaged and just started planning", "Age_Range": "40+"}
ASKS = ["Elopement with only 6 people", "Somewhere beautiful", "Cheaper options", "Big Sur?",
        "Any elopement packages in that area"]


def one_run(token):
    stored, cost = [], 0.0
    for ask in [None] + ASKS:
        msgs = [{"role": "user", "content": OPENING_ASK}] + [_turn_for_model(t) for t in stored]
        if ask:
            msgs.append({"role": "user", "content": ask})
        res, usage = agent.run(token, {"id": 0}, True, msgs, profile_override=PROFILE)
        cost += guards.cost_usd(usage["model"], usage)
        if ask:
            stored.append({"role": "user", "text": ask, "cards": [], "chips": []})
        stored.append({"role": "assistant", "text": res["text"], "cards": res["cards"], "chips": res["chips"]})
    replies = [t for t in stored if t["role"] == "assistant"]
    seen, repeat_cards, chips_seen, repeat_chips = set(), 0, set(), 0
    for r in replies:
        ids = [c.get("vendor_id") for c in r["cards"]]
        repeat_cards += sum(1 for i in ids if i in seen)
        seen.update(ids)
        repeat_chips += sum(1 for c in r["chips"] if c.lower() in chips_seen)
        chips_seen.update(c.lower() for c in r["chips"])
    return {"cost": round(cost, 3),
            "opening_states": sorted({c.get("state") for c in replies[0]["cards"]}),
            "cards_total": sum(len(r["cards"]) for r in replies), "cards_repeated": repeat_cards,
            "replies_with_peak_caveat": sum(1 for r in replies if re.search(r"peak", r["text"], re.I)),
            "replies_saying_free": sum(1 for r in replies if re.search(r"\bfree\b", r["text"], re.I)),
            "chips_repeated": repeat_chips, "transcript": stored}


if __name__ == "__main__":
    import os
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    out = [one_run(os.environ["REC_TEST_TOKEN"]) for _ in range(runs)]
    for o in out:
        print({k: v for k, v in o.items() if k != "transcript"})
    (pathlib.Path(__file__).parent / "results_replay_31797.json").write_text(json.dumps(out, indent=1))
