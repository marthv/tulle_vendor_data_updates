"""REC_FREE_BUDGET_FIT (owner yes 2026-11-22): the profile sheet's refresh message names the OLD budget first.
Real question 6302 (2026-11-20, a free couple): "I updated my wedding details (budget: $10K → $2K)" was judged
against $10,000 - every venue between $2k and $9k would have read "in your budget"."""
import pathlib, sys, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))
import free_mode  # noqa: E402

R = "I updated my wedding details (%s). Refresh my picks with the new details."


def test_refresh_uses_the_new_budget():
    assert free_mode.budget_target(R % "budget: $10K → $2K", 2000) == 2000
    assert free_mode.budget_target(R % "budget: — → $50K", 50000) == 50000
    assert free_mode.budget_target(R % "guests: 150 → 130; budget: — → $50K; date: May 2026", 0) == 50000


def test_refresh_without_budget_or_removed_budget_is_not_a_budget_question():
    assert free_mode.budget_target(R % "guests: 75 → 70", 40000) == 0
    assert free_mode.budget_target(R % "budget: $30K → —", 0) == 0


def test_ordinary_questions_unchanged():
    assert free_mode.budget_target("Anything under $10,000?", 0) == 10000
    assert free_mode.budget_target("Less than 20000 want a wedding venue with different rooms", 0) == 20000
    assert free_mode.budget_target("which of these fit our budget?", 30000) == 30000


def test_verdict_sentence_with_any_number_is_dropped():
    """QA 2026-11-22: a verdict plus a bare or worded number must not pass the free filter."""
    for bad in ["The Grand is over your budget by roughly 12000.", "The Grand is over your budget by 3 thousand.",
                "Rosewood is about at your budget, give or take ten grand.", "Rosewood is over your budget by 5k."]:
        assert not free_mode._verdict_ok(bad), bad
    assert free_mode._verdict_ok("Rosewood looks over your budget.")
    assert free_mode._verdict_ok("Kirkwood is in your budget, and the ballroom seats your guest list.")


def test_venue_names_with_grand_still_pass():
    assert free_mode._verdict_ok("Grand Hall is over your budget.")
    assert free_mode._verdict_ok("The Grand Ballroom is in your budget.")
