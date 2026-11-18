import venue_log

USER = {"id": 501, "email": "c@example.com"}
SKY = {"vendor_id": "V3551", "name": "The Skylands Manor at the New Jersey State Botanical Garden", "state": "New Jersey"}


def _row(hit, shown=None):
    return venue_log.from_model_search(USER, True, True, 0, "planner", "Skylands manor", hit, shown)


def test_search_with_no_results_is_found_when_the_answer_showed_a_matching_card():
    r = _row({"keyword": "Skylands manor", "state": "New York", "results": []}, [("V3551", SKY["name"])])
    assert (r["outcome"], r["answered_as"], r["best_vendor_id"]) == ("found", "model_card", "V3551")


def test_low_score_best_match_on_a_card_is_found():
    r = _row({"keyword": "skylands", "state": "New Jersey", "results": [SKY]}, [("V3551", SKY["name"])])
    assert r["outcome"] == "found" and r["answered_as"] == "model_card"


def test_not_found_stays_when_no_card_matches():
    r = _row({"keyword": "Park Savoy", "state": "New Jersey", "results": []}, [("V1", "The Grand Ballroom")])
    assert r["outcome"] == "not_found"


def test_without_shown_cards_behaviour_is_unchanged():
    r = _row({"keyword": "Skylands manor", "state": "New York", "results": []})
    assert r["outcome"] == "not_found"
