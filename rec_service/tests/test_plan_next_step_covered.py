"""REC_PLAN_NEXT_STEP (owner yes 2026-11-22): 'Next for your wedding' never offers the slot the answer was about.
11-07 reason it was off: "I asked for a photographer, then it says find a photographer again"."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import plan  # noqa: E402


def test_kate_case_photographer_then_not_photographer_again():
    nxt = plan.next_step([{"slot": "Venue"}], 1, plan.covered_slots(["photography"], "Find me a photographer"))
    assert nxt and nxt["slot"] != "Photography"
    assert nxt["slot"] == "Food and Beverage"


def test_words_alone_cover_a_slot():
    assert plan.covered_slots([], "how much do DJs usually cost?") == {"Entertainment"}
    assert plan.covered_slots([], "any florists near Austin") == {"Flowers"}
    assert plan.covered_slots([], "hair and makeup ideas") == {"Beauty"}


def test_tool_category_covers_a_slot():
    assert plan.covered_slots(["venue", "food and beverage"], "") == {"Venue", "Food and Beverage"}


def test_unchanged_without_covered():
    # Same answer as before the change when nothing was covered (back-compat of the 2-arg call).
    assert plan.next_step([], 3) == {"slot": "Venue", "label": "Venue", "ask": "Find venues for our wedding"}
    assert plan.next_step([], 2) is None


def test_everything_filled_or_covered_gives_nothing():
    items = [{"slot": k} for k in plan.SLOT_KEYS[:-1]]
    assert plan.next_step(items, 5, {"Beauty"}) is None
