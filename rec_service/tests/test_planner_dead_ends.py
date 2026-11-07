"""Planner dead ends found in the team's activity log, 2026-11-03..06 (owner 2026-11-07, "do all 5")."""
import pytest

import agent
import lookup


@pytest.mark.parametrize("text", [
    "is that inclusive?", "It’s stupid", "Not new york", "nevermind - disregard", "what about hotel buyout?",
    "how much is that venue?", "South Lake Tahoe", "Northern Virginia", "upstate new york", "Orange County area",
])
def test_follow_ups_complaints_and_places_are_not_venue_lookups(text):
    assert lookup.detect(text) is None


@pytest.mark.parametrize("text,name", [
    ("Dinolfos", "Dinolfos"), ("Gather green", "Gather green"), ("Lamb hill", "Lamb hill"),
    ("Fort Worth club pricing", "Fort Worth club"), ("do you have pricing for oheka castle", "oheka castle"),
    ("Hotel Haya", "Hotel Haya"), ("Bear brook valley", "Bear brook valley"), ("Bear brook nj", "Bear brook nj"),
])
def test_real_names_still_look_up(text, name):
    assert (lookup.detect(text) or {}).get("name") == name


def _row(vid, name, google=""):
    return {"vendor_id": vid, "name": name, "google_name": google, "address": "", "state": "Illinois"}


def test_one_strong_close_match_is_the_venue_not_a_question():
    # "Dinolfos" vs DiNolfo's Banquets of Homer Glen: every typed word matches; the venue's extra words kept it < HIGH.
    q = lookup.detect("Dinolfos")
    res = lookup.find(q, lambda term: [_row("V9", "DiNolfo's Banquets of Homer Glen")])
    assert res["status"] == "found" and res["matches"][0]["vendor_id"] == "V9"


def test_a_weak_single_match_still_asks():
    q = lookup.detect("Rosewood manor estates")
    res = lookup.find(q, lambda term: [_row("V1", "Rosewood Mansion Inn")])
    assert res["status"] != "found"


def test_named_fully_needs_most_of_the_name():
    assert agent.named_fully("Tell me about Tower Club Dallas and whether it fits my wedding", "Tower Club Dallas")
    assert not agent.named_fully("venues in Dallas under 10k", "Tower Club Dallas")
    assert agent.named_fully("thoughts on the fonthill?", "Fonthill Castle")
