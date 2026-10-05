import sys, pathlib, itertools
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
import budget  # noqa: E402

# Xano table 19 as read 2026-10-05: typical, venue, food, flowers, planner, entertainment (each column = 100)
_T19 = {
    "Venue": (20, 25, 20, 20, 18, 18), "Food and Beverage": (20, 20, 25, 20, 18, 18),
    "Photography": (10, 8, 9, 10, 10, 10), "Wedding Planner": (10, 10, 10, 8, 15, 10),
    "Entertainment": (10, 9, 8, 8, 9, 14), "Flowers": (9, 8, 8, 15, 9, 9), "Attire": (7, 7, 7, 6, 7, 7),
    "Rentals": (5, 5, 5, 5, 5, 5), "Additional Items": (3, 2, 2, 2, 3, 3), "Beauty": (2, 2, 2, 2, 2, 2),
    "Transportation": (2, 2, 2, 2, 2, 2), "Day of Stationery": (1, 1, 1, 1, 1, 1), "Invites": (1, 1, 1, 1, 1, 1),
}
SHARES = [{"category": c, "shares": dict(zip(budget.FOCUS_COLUMNS, v))} for c, v in _T19.items()]
NO_RIPPLE = {}


def test_fixture_columns_sum_to_100():
    for f in budget.FOCUS_COLUMNS:
        assert sum(s["shares"][f] for s in SHARES) == 100


@pytest.mark.parametrize("focus", list(budget.FOCUS_COLUMNS))
def test_every_focus_allocates_the_whole_budget(focus):   # old page: Entertainment typo, 4 sums were arrays
    p = budget.plan(SHARES, 55000, focus, ripple=NO_RIPPLE)
    assert abs(p["projected_total"] - 55000) <= len(SHARES)   # per-row rounding only
    assert p["over_by"] == 0


def test_exclusions_redistribute_and_keep_total():
    cats = [s["category"] for s in SHARES]
    for k in (1, 2, 5):
        for ex in itertools.islice(itertools.combinations(cats, k), 40):
            p = budget.plan(SHARES, 40000, "typical", excluded=list(ex), ripple=NO_RIPPLE)
            assert abs(sum(r["projected"] for r in p["rows"]) - 40000) <= len(SHARES)
            assert all(r["projected"] == 0 for r in p["rows"] if r["excluded"])


def test_excluding_planner_scales_others_proportionally():
    a = budget.allocate(SHARES, 50000, "typical", ["Wedding Planner"])
    assert round(a["Venue"]) == round(50000 * 20 / 90)


def test_focus_shifts_money_to_that_category():
    t = budget.allocate(SHARES, 50000, "typical", [])
    v = budget.allocate(SHARES, 50000, "venue", [])
    assert v["Venue"] > t["Venue"]


def test_overspend_raises_total_dollar_for_dollar_without_ripple():
    base = budget.allocate(SHARES, 50000, "typical", [])
    p = budget.plan(SHARES, 50000, "typical", edits={"Venue": base["Venue"] + 3000}, ripple=NO_RIPPLE)
    assert abs(p["over_by"] - 3000) <= len(SHARES)


def test_ripple_adds_to_related_categories():
    base = budget.allocate(SHARES, 50000, "typical", [])
    p = budget.plan(SHARES, 50000, "typical", edits={"Venue": base["Venue"] + 1000},
                    ripple={"Venue": {"Rentals": 0.10}})
    rentals = next(r for r in p["rows"] if r["category"] == "Rentals")
    assert rentals["ripple"] == 100
    assert abs(p["over_by"] - 1100) <= len(SHARES)


def test_ripple_skips_categories_the_couple_priced_and_excluded_ones():
    base = budget.allocate(SHARES, 50000, "typical", [])
    rip = {"Venue": {"Rentals": 0.10, "Flowers": 0.10}}
    p = budget.plan(SHARES, 50000, "typical", excluded=["Flowers"],
                    edits={"Venue": base["Venue"] + 1000, "Rentals": 500}, ripple=rip)
    rows = {r["category"]: r for r in p["rows"]}
    assert rows["Rentals"]["ripple"] == 0   # priced by the couple: no drift
    assert rows["Rentals"]["entered"] == 500
    assert rows["Flowers"]["projected"] == 0


def test_underspend_does_not_lower_projection():
    base = budget.allocate(SHARES, 50000, "typical", [])
    p = budget.plan(SHARES, 50000, "typical", edits={"Venue": 100}, ripple=NO_RIPPLE)
    venue = next(r for r in p["rows"] if r["category"] == "Venue")
    assert venue["projected"] == round(base["Venue"])


def test_clean_request_rejects_bad_input():
    with pytest.raises(ValueError):
        budget.clean_request(SHARES, "luxury", [], {})
    with pytest.raises(ValueError):
        budget.clean_request(SHARES, "typical", [], {"Venue": -5})
    with pytest.raises(ValueError):
        budget.clean_request(SHARES, "typical", list(_T19), {})
    focus, ex, ed = budget.clean_request(SHARES, "Venue", ["Nope", "Beauty"], {"Beauty": 50, "Venue": "9000", "X": 1})
    assert (focus, ex, ed) == ("venue", ["Beauty"], {"Venue": 9000})


def test_donut_has_no_excluded_slices_and_hex_colours():
    p = budget.plan(SHARES, 30000, "typical", excluded=["Invites"], ripple=NO_RIPPLE)
    ds = p["chart"]["data"]["datasets"][0]
    assert "Invites" not in p["chart"]["data"]["labels"]
    assert len(ds["data"]) == len(p["chart"]["data"]["labels"]) == 12
    assert all(c.startswith("#") for c in ds["backgroundColor"])


def test_venue_line_for_the_assistant():
    p = budget.plan(SHARES, 50000, "typical", ripple=NO_RIPPLE)
    line = budget.venue_line(p)
    assert "venue share $10,000" in line and "projected total $50,000 of $50,000" in line
