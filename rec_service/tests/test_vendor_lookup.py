"""Instant lookup for every vendor type (owner 2026-11-07: "if they want a vendor: just let them ask and we'll find
it"): photographers, entertainers, florists, caterers, beauty, planners by name - no model."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
import lookup  # noqa: E402
from test_name_lookup import env, ask, row  # noqa: E402,F401


@pytest.mark.parametrize("text,core,cat", [
    ("Jane Doe Photography", "Jane Doe", "Photography"),
    ("Bloom florist", "Bloom", "Flowers"),
    ("DJ Mike Rivera", "Mike Rivera", "Entertainment"),
    ("Petals and Stems Florist", "Petals and Stems", "Flowers"),
])
def test_vendor_word_gives_the_category(text, core, cat):
    assert lookup.vendor_hint(text) == (core, cat)
    assert lookup.detect(text)["category"] == cat


@pytest.mark.parametrize("text", ["wedding photographer", "DJs with a photo booth", "photographers for elopements",
                                  "florist near me", "Find a photographer"])
def test_vendor_searches_stay_with_the_model(text):
    assert lookup.detect(text) is None


def test_a_name_with_wedding_in_it_is_still_a_name():
    assert lookup.detect("The Post Wedding Photographer")["category"] == "Photography"


PHOTOGS = [dict(row("P1", "Jane Doe Photography", "Texas", "1 Main St, Austin, TX 78701"), category="Photography")]


def test_photographer_found_by_name_without_the_model(env):
    env["vendor_search"] = lambda t, c: [dict(r) for r in PHOTOGS] if c == "Photography" else []
    j = ask("Jane Doe Photography")
    assert env["model"] == 0 and j["lookup"]["status"] == "found"
    c = j["cards"][0]
    assert c["vendor_id"] == "P1" and c["category"] == "Photography" and "venue_fee_range" not in c
    assert "photographer" in j["text"] and "Austin" in j["text"]


def test_a_bare_name_that_is_not_a_venue_is_found_among_other_vendors(env):
    env["search"] = lambda t: []                                    # no venue by that name
    env["vendor_search"] = lambda t, c: [dict(r) for r in PHOTOGS] if c == "Photography" else []
    j = ask("Jane Doe Photography pricing")
    assert j["lookup"]["status"] == "found" and j["cards"][0]["vendor_id"] == "P1"


def test_a_photographer_we_dont_have_gets_no_venue_cards(env):
    env["vendor_search"] = lambda t, c: []
    j = ask("Zzyzx Unicorn Photography")
    assert j["lookup"]["status"] == "not_found" and j["cards"] == [] and "We don't have" in j["text"]
