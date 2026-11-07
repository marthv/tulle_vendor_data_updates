"""Open-ended replies (owner 2026-11-07): no trailing question, no chips, no profile questions when REC_OPEN_ENDED."""
import agent
import app
import config


def test_strip_trailing_question_keeps_content():
    t = ("Zachary's Red Barn is my top pick: food and rental both sit below average for Illinois. "
         "Open Zachary's PDF first. Which area of Illinois do you want to be close to?")
    assert app.strip_trailing_question(t) == ("Zachary's Red Barn is my top pick: food and rental both sit below "
                                              "average for Illinois. Open Zachary's PDF first.")
    two = "Here are three barns near Austin with real pricing from couples. Want all-inclusive? Or BYO?"
    assert app.strip_trailing_question(two) == "Here are three barns near Austin with real pricing from couples."
    assert app.strip_trailing_question("Which state is the wedding in?") == "Which state is the wedding in?"
    assert app.strip_trailing_question("No question here.") == "No question here."


def test_open_ended_clears_chips_questions_and_tail(monkeypatch):
    monkeypatch.setattr(config, "OPEN_ENDED", True)
    out = {"text": "Three venues fit your 125 guests and budget in Dallas well. Should I keep it to Dallas?",
           "chips": ["Yes, Dallas only", "No"], "questions": [{"id": "style"}]}
    app._open_ended(out)
    assert out["chips"] == [] and out["questions"] == [] and not out["text"].endswith("?")


def test_lookup_choices_keep_their_name_chips(monkeypatch):
    monkeypatch.setattr(config, "OPEN_ENDED", True)
    out = {"text": "We have 2 venues that could be “Gather” - which one did you mean? Tap its name below.",
           "chips": ["Gather Greene", "Gather Venue"], "lookup": {"status": "choices", "query": "Gather"}}
    app._open_ended(out)
    assert out["chips"] == ["Gather Greene", "Gather Venue"] and out["text"].startswith("We have 2")


def test_flag_off_is_a_no_op_and_prompt_unchanged(monkeypatch):
    out = {"text": "Picks here for you in Texas today. Want more?", "chips": ["More"], "questions": []}
    assert app._open_ended(dict(out)) == out
    base = agent.system_prompt()
    assert "OPEN-ENDED REPLIES" not in base
    monkeypatch.setattr(config, "OPEN_ENDED", True)
    assert agent.system_prompt().startswith(base) and agent.system_prompt().endswith(agent.OPEN_ENDED_RULES)
    assert "no question" in agent._nearby_note("Missouri")
