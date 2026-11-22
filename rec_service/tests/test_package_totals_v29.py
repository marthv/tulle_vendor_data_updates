"""REC_PACKAGE_TOTALS (owner yes 2026-11-22): the Planner's all-in (estimate.compute) equals the venue card's
all-in (ep230 space.all_in.at_default.total, computed by Xano fn56 v29) for the hand-reviewed PACKAGES spaces.

The answer key is NOT written by this code's author: it is the card's own number in the LIVE ep230 payloads saved
2026-11-22 (read-only GET with the internal test token, Forever entitlement). Flag off must reproduce the totals the
Planner shows today (origin/main 65cc09b on the same payloads) to the dollar."""
import json, pathlib, sys, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))
import config  # noqa: E402
import estimate  # noqa: E402
import pytest  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"

# fixture -> Planner total on origin/main (flag off) at the card's default guest count
TODAY = {
    "V586_default": 64130,                                       # Celestine (Kate #feedback 11-20); card 45,580
    "V586_eveningweddingsseatedevents": 56175,                   # food line source 'market' + extra-guest rate
    "V231_default": 38308,
    "V2355_default": 146410,                                     # guest floor 150
    "V707_coldcreekfarmessentialsallinclusivepackage": 29016,    # ceremony fee_type 'included' ($0)
    "V3724_default": 13603,
    "V4226_default": 49105,
    "V674_default": 61610,
    "V4258_treasuryontheplazaeatdrinkbemarried2027": 61000,
    "V134_default": 30240,                                       # NOT a package (FEE_IN_MIN): must not move
}


def panel(name):
    return json.loads((FIX / ("pi_v29_%s.json" % name)).read_text())


def planner_total(d):
    g = d["space"]["all_in"]["at_default"]["guests"]
    return estimate.compute(estimate.inputs_from_panel(d), g)["total_before_tax_and_gratuity"]


def card_total(d):
    return d["space"]["all_in"]["at_default"]["total"]


@pytest.mark.parametrize("name", sorted(TODAY))
def test_flag_on_planner_matches_card(monkeypatch, name):
    monkeypatch.setattr(config, "PACKAGE_TOTALS", True)
    d = panel(name)
    assert planner_total(d) == card_total(d)


@pytest.mark.parametrize("name", sorted(TODAY))
def test_flag_off_is_todays_total(monkeypatch, name):
    monkeypatch.setattr(config, "PACKAGE_TOTALS", False)
    assert planner_total(panel(name)) == TODAY[name]


def test_canary_package_payloads_disagree_with_card_when_off(monkeypatch):
    # Proves the match test can fail: with the flag off every package fixture is off the card.
    monkeypatch.setattr(config, "PACKAGE_TOTALS", False)
    for name in TODAY:
        d = panel(name)
        if name != "V134_default":
            assert planner_total(d) != card_total(d), name


def test_package_lines_say_where_the_food_is(monkeypatch):
    monkeypatch.setattr(config, "PACKAGE_TOTALS", True)
    e = estimate.compute(estimate.inputs_from_panel(panel("V586_default")), 100)
    food = next(l for l in e["lines"] if l["item"] == "Food and drink")
    assert food["included_in_rental"] and "in the package for 70 guests; 30 extra" in food["math"]
    assert "Food and drink" not in e["estimated_lines"]


def test_ceremony_included_is_zero_from_the_pdf(monkeypatch):
    monkeypatch.setattr(config, "PACKAGE_TOTALS", True)
    e = estimate.compute(estimate.inputs_from_panel(panel("V707_coldcreekfarmessentialsallinclusivepackage")), 100)
    cer = next(l for l in e["lines"] if l["item"] == "Ceremony on site")
    assert cer["amount"] == 0 and cer["source"] == "this venue's PDF"
