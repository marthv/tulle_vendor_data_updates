"""Opening chips are the likely answers to the closing question (owner 2026-10-31)."""
import app
import config


def test_area_question_gets_near_city_chips(monkeypatch):
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", True)
    user = {"id": 1, "Wedding_Location_Updated": ["Arizona"], "Wedding_Guest_Count": 80}
    chips = app._opening_chips("Top pick is The Willow. Which city or area of Arizona do you want to be close to?",
                               ["Show me cheaper options"], user, False)
    assert chips[:4] == ["Near Phoenix", "Near Scottsdale", "Near Tucson", "Near Sedona"]


def test_other_question_keeps_model_answers_first_then_starters(monkeypatch):
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", True)
    user = {"id": 1, "Wedding_Location_Updated": ["Texas"]}
    chips = app._opening_chips("Three picks. Should I keep to all-inclusive venues?", ["Yes, all-inclusive only", "No, show everything"], user, False)
    assert chips[:2] == ["Yes, all-inclusive only", "No, show everything"] and len(chips) == 4
    assert chips[2] in app._starter_chips(False)


def test_no_question_means_starters_unchanged(monkeypatch):
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", True)
    user = {"id": 1, "Wedding_Location_Updated": ["Texas"]}
    assert app._opening_chips("Three picks for you.", ["whatever"], user, True) == list(app._starter_chips(True))
