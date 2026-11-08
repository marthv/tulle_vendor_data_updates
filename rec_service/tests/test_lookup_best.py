"""One answer instead of "which one did you mean?" (owner 2026-11-07). Planner log 11-03..11-06: 22 "which one" answers,
11 couples left right away and none tapped a name; 9 of 21 "found" answers led to a PDF open. When one match is the
likely one (the only one in the couple's wedding state, clearly ahead on score, or the same venue listed twice) the
Planner answers with it and offers the others by name; a tapped name is looked up, not sent to the model."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
import lookup  # noqa: E402
import xano  # noqa: E402
from test_name_lookup import env, ask, row  # noqa: E402,F401


def m(vid, name, state):
    return {"vendor_id": vid, "name": name, "state": state}


Q = {"name": "x", "cue": False, "words": 2}


def test_same_venue_listed_twice_is_found():
    res = {"status": "choices", "matches": [m("A", "The Breakers on the Ocean", "New Jersey"),
                                            m("B", "Breakers on the Ocean", "New Jersey")], "scores": [1.0, 1.0]}
    out = lookup.resolve(Q, res)
    assert out["status"] == "found" and [x["vendor_id"] for x in out["matches"]] == ["A"]


def test_the_only_match_in_their_state_is_the_guess():
    res = {"status": "choices", "matches": [m("W", "Brooks Lake Lodge", "Wyoming"),
                                            m("T", "The Brooks at Weatherford", "Texas")], "scores": [0.80, 0.79]}
    out = lookup.resolve(Q, res, ["Texas"])
    assert out["status"] == "best" and [x["vendor_id"] for x in out["matches"]] == ["T", "W"]


def test_a_clear_leader_is_the_guess():
    res = {"status": "choices", "matches": [m("C", "Woman's Club of Fort Worth", "Texas"),
                                            m("Z", "Fort Worth Zoo", "Texas")], "scores": [0.853, 0.772]}
    assert lookup.resolve(Q, res, ["Texas"])["status"] == "best"


def test_a_tie_still_asks():
    res = {"status": "choices", "matches": [m("1", "Ritz A", "Georgia"), m("2", "Ritz B", "California")],
           "scores": [0.775, 0.773]}
    assert lookup.resolve(Q, res, ["Colorado"])["status"] == "choices"


def test_one_bare_word_with_a_tie_goes_to_the_model():
    res = {"status": "choices", "matches": [m("1", "The Aspen Room", "Louisiana"), m("2", "Aspen Ranch", "Oklahoma")],
           "scores": [0.921, 0.917]}
    assert lookup.resolve({"name": "aspen", "cue": False, "words": 1}, res)["status"] == "unsure"


def test_found_and_not_found_pass_through():
    for st in ("found", "not_found"):
        res = {"status": st, "matches": [], "scores": []}
        assert lookup.resolve(Q, res, ["Texas"]) is res


@pytest.mark.parametrize("text", ["Houston texas", "houston, tx", "Austin TX", "dallas texas"])
def test_a_place_plus_its_state_is_not_a_venue_name(text):
    assert lookup.detect(text) is None


TWO = [row("V1", "Ashton Gardens | Atlanta", "Georgia", "1 Main St, Sugar Hill, GA 30518"),
       row("V2", "Ashton Gardens | Houston", "Texas", "1 Main St, Houston, TX 77001"),
       row("V3", "Ashton Gardens | Dallas", "Texas", "1 Main St, Dallas, TX 75201")]


def test_best_answer_shows_the_guess_priced_and_offers_the_others(env, monkeypatch):
    env["search"] = lambda t: [dict(r) for r in TWO]
    monkeypatch.setattr(xano, "profile_of", lambda u: {"Wedding_Location_Updated": ["Georgia"]})
    j = ask("Ashton gardens")
    assert env["model"] == 0 and j["lookup"]["status"] == "best"
    assert [c["vendor_id"] for c in j["cards"]] == ["V1"] and j["cards"][0].get("venue_fee_range")
    assert "Not the one? We also have" in j["text"] and "Houston" in j["text"] and "Dallas" in j["text"]
    assert len(j["chips"]) == 2 and all("Ashton Gardens" in ch for ch in j["chips"])


def test_a_tapped_offered_name_is_looked_up_not_sent_to_the_model(env, monkeypatch):
    env["search"] = lambda t: [dict(r) for r in TWO if "dallas" in t or "ashton" in t]
    env["history"] = [{"role": "user", "text": "Ashton gardens", "cards": []},
                      {"role": "assistant", "text": "Here's Ashton Gardens | Atlanta. Not the one? We also have "
                       "Ashton Gardens | Houston (Houston, Texas) and Ashton Gardens | Dallas (Dallas, Texas).",
                       "chips": ["Ashton Gardens | Houston", "Ashton Gardens | Dallas"], "cards": []}]
    j = ask("Ashton Gardens | Dallas")
    assert env["model"] == 0 and j["lookup"]["status"] == "found" and j["cards"][0]["vendor_id"] == "V3"


def test_an_ordinary_chip_still_goes_to_the_model(env):
    env["history"] = [{"role": "user", "text": "barns", "cards": []},
                      {"role": "assistant", "text": "Here are three barns.", "chips": ["Keep it all-inclusive"], "cards": []}]
    ask("Keep it all-inclusive")
    assert env["model"] == 1
