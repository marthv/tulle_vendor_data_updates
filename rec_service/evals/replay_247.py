"""Replay chat 247 (2026-10-21, user 33663, 1/5 "Not great suggestions"): a Texas-only profile, then "Stay close to
flower mound tx" -> "We hold no venues in Flower Mound that seat 175". Profile fields + preference answers only, no
personal data. SPENDS MONEY (~$0.10 per run).  python evals/replay_247.py"""
import json
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_evals  # noqa: E402  (loads secrets + tokens the same way)
import agent  # noqa: E402
import guards  # noqa: E402
import prefs  # noqa: E402
from app import _turn_for_model, OPENING_ASK  # noqa: E402

PROFILE = {"Planning_Phase": "I'm engaged and just started wedding planning", "Wedding_Location_Updated": ["Texas"],
           "Wedding_Guest_Count": 175, "Wedding_Budget": 15000, "Major_City": True, "Age_Range": "26-29"}
NOTES = [{"kind": "pref", "key": "style", "values": ["Country Club / Private Club", "Barn / Ranch", "Hotel / Resort"]},
         {"kind": "pref", "key": "feel", "values": ["Classic / timeless", "Industrial chic"]},
         {"kind": "pref", "key": "setting", "values": ["Tall / Vaulted Ceilings", "Natural Light / Large Windows"]},
         {"kind": "pref", "key": "catering", "values": ["Raw Space"]},
         {"kind": "pref", "key": "ceremony", "values": ["elsewhere"]},
         {"kind": "pref", "key": "priority", "values": ["Room for our guest count"]}]
ASKS = ["Stay close to flower mound tx"]


def main():
    token = os.environ["REC_TEST_TOKEN"]
    hint = prefs.search_hint(prefs.from_notes(NOTES))
    stored, cost, active = [], 0.0, None
    for ask in [None] + ([] if "--opening-only" in sys.argv else ASKS):
        msgs = [{"role": "user", "content": OPENING_ASK}] + [_turn_for_model(t) for t in stored]
        if ask:
            msgs.append({"role": "user", "content": ask})
        res, usage = agent.run(token, {"id": 0}, True, msgs, profile_override=PROFILE, preferences=hint,
                               active_filters=active)
        cost += guards.cost_usd(usage["model"], usage)
        active = res.get("search_filters") or active
        if ask:
            stored.append({"role": "user", "text": ask, "cards": [], "chips": []})
        stored.append({"role": "assistant", "text": res["text"], "cards": res["cards"], "chips": res["chips"]})
        print("\n== %s" % (ask or "OPENING"))
        print(res["text"])
        for c in res["cards"]:
            print("  - %s | %s | %s mi | seats %s" % (c.get("name"), c.get("address"), c.get("distance_miles"),
                                                      c.get("max_capacity_seated")))
        print("  filters:", json.dumps(res.get("search_filters")), "| chips:", res["chips"])
    print("\ncost $%.3f" % cost)


if __name__ == "__main__":
    main()
