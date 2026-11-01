import sys, pathlib, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)
import app  # noqa: E402

USER = {"Wedding_Location_Updated": ["Arizona", "Alabama"], "Wedding_Guest_Count": 80}


def test_opening_named_after_its_picks_and_area():
    out = {"cards": [{"name": "Clayton House"}, {"name": "The Willow"}, {"name": "Gainey Ranch Golf Club"}, {"name": "Fourth"}],
           "search_filters": {"city": "Scottsdale", "states": ["Arizona"]}}
    assert app._picks_title(out, USER) == "Scottsdale \u00b7 Clayton House, The Willow, Gainey Ranch Golf Club"


def test_opening_without_a_city_uses_the_state_and_long_names_are_trimmed():
    out = {"cards": [{"name": "The Phoenician Scottsdale Luxury Resort"}], "search_filters": {"states": ["Arizona"]}}
    title = app._picks_title(out, USER)
    assert title.startswith("Arizona \u00b7 The Phoenician") and title.endswith("\u2026")


def test_opening_with_no_cards_keeps_the_profile_line():
    assert app._picks_title({"cards": [], "search_filters": {}}, USER).startswith("Venue picks")


def test_ask_tulle_template_collapses_to_about_the_vendor():
    assert app._question_title("Tell me about Alila Marea Beach Resort Encinitas and whether it fits my wedding") == "About Alila Marea Beach Resort Encinitas"
    assert app._question_title("tell me about The Henry") == "About The Henry"


def test_plain_questions_are_tidied():
    assert app._question_title("  what do photographers cost in arizona?  ") == "What do photographers cost in arizona?"
    assert len(app._question_title("x" * 200)) == 80
