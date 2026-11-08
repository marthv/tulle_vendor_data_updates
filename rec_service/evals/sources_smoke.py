"""Live smoke for sourced answers (2026-11-07): a few non-price venue questions through agent.run at FULL detail with
SOURCED_ANSWERS + WEB_SEARCH on. Prints the answer, its sources, dropped URLs and web search count. SPENDS ~$0.05-0.15
per question.  python evals/sources_smoke.py"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import run_evals  # noqa: E402,F401
import agent  # noqa: E402
import config  # noqa: E402

config.SOURCED_ANSWERS = True
config.WEB_SEARCH = True
config.OPEN_ENDED = True
PROFILE = {"Wedding_Location_Updated": ["California"], "Wedding_Guest_Count": 120, "Wedding_Budget": 60000}
QS = sys.argv[1:] or ["Is Kohl Mansion romantic? Can we do the ceremony outside, and what happens if it rains?",
                      "Does Kohl Mansion let us bring our own alcohol, and is there parking for guests?"]
for q in QS:
    res, usage = agent.run(os.environ["REC_TEST_TOKEN"], {"id": 0}, True, [{"role": "user", "content": q}],
                           profile_override=PROFILE, detail="full")
    print("Q:", q)
    print("A:", res.get("text"))
    print("sources:", json.dumps(res.get("sources"), ensure_ascii=False))
    print("dropped:", usage.get("dropped_sources"), "| web searches:", usage.get("web_searches"),
          "| urls found:", usage.get("web_urls_found"), "| tools:", usage.get("tools_used"),
          "| latency s:", round(usage.get("latency_ms", 0) / 1000, 1))
    print()
